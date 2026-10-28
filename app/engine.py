"""Native Genesis IPC/CUDA FEM with selectable contact; surface vertices are public."""
import time
import os
from pathlib import Path
from types import MethodType
import numpy as np
import genesis as gs
from .config import Force, Setup
from .mesh import mesh_path, contact_check
from .materials import resolve_materials, element_material_ids, configure_elements

class GenesisEngine:
    def __init__(self,config,*,configure=None,ipc_options=None,output=None,preview=False):
        config=config if isinstance(config,Setup) else Setup.model_validate(config)
        self._usd=None
        self._output=Path(output).resolve() if output is not None else None
        self._motion=None
        self._preview=preview
        if output is not None:
            for name,suffix in (('GS_CACHE_FILE_PATH','genesis'),('QD_OFFLINE_CACHE_FILE_PATH','quadrants')):
                directory=Path(output).resolve()/'cache'/suffix
                directory.mkdir(parents=True,exist_ok=True)
                os.environ[name]=str(directory)
        self.materials=resolve_materials(config)
        # Both Genesis kernels and its IPC coupler use CUDA.
        if not gs._initialized:gs.init(backend=gs.cuda,precision='64',logging_level='warning')
        if gs.backend!=gs.cuda:raise RuntimeError('本工具要求 Genesis CUDA 后端，请在新进程中使用 gs.cuda 初始化')
        self.fem_preconditioner='mas'
        self.fem_preconditioner_fallback_reason=None
        self.config=config;self.time=0.;self.frame=0;self.step_ms=0.;self._failed=False
        if Path(config.mesh).suffix.lower() in ('.usd','.usda','.usdc','.usdz'):
            self._init_usd(config,configure,ipc_options)
            return
        m=config.material;shell=config.body_type=='shell'
        if not shell and any(material.volume_plasticity for material in self.materials):
            from .plasticity import require_volume_backend
            require_volume_backend()
        self.scene=gs.Scene(sim_options=gs.options.SimOptions(dt=config.dt,gravity=config.gravity),
            coupler_options=gs.options.IPCCouplerOptions(contact_d_hat=config.contact_d_hat_m,
                contact_enable=config.contact_mode!='none',contact_friction_enable=config.contact_mode!='none'),show_viewer=False)
        try:
            self.scene.add_entity(gs.morphs.Plane(),material=gs.materials.Rigid(coup_friction=m.self_friction))
            common=dict(E=m.youngs_modulus_pa,nu=m.poisson_ratio,rho=m.density_kg_m3,friction_mu=m.self_friction)
            material=gs.materials.FEM.Cloth(**common,thickness=m.thickness_m/2,
                bending_stiffness=m.bending_rigidity_nm if m.bending_model and not m.bending_plasticity and not config.material_regions else None) if shell else gs.materials.FEM.Elastic(**common)
            self.entity=self.scene.add_entity(gs.morphs.Mesh(file=str(mesh_path(config.mesh)),decimate=False,
                maxvolume=config.tet_max_volume_m3 if not shell else -1,
                nobisect=shell or config.tet_max_volume_m3==-1),material=material)
            vertices=self.entity.init_positions.detach().cpu().numpy().reshape(-1,3)
            self._positions=vertices.copy()
            triangles=self.entity.surface_triangles
            self.elements=np.asarray(triangles if shell else self.entity.elems,dtype=np.int64).copy()
            self.element_material_ids=element_material_ids(config,len(self.elements))
            self.surface_ids=np.unique(triangles)
            self.faces=np.searchsorted(self.surface_ids,triangles)
            self.x=vertices[self.surface_ids].copy()
            self.contact_check=contact_check(self.x,self.faces,config)
            for message in self.contact_check['warnings']:gs.logger.warning(message)
            if any(i<0 or i>=len(self.surface_ids) for i in config.pins):raise ValueError('固定点编号超出表面顶点范围')
            self.config.pins=sorted(set(config.pins))
            self.force=np.zeros((len(vertices),3),dtype=np.float64)
            self.set_forces(config.forces)
            self.mesh_info=dict(simulation_vertices=len(vertices),surface_vertices=len(self.surface_ids),
                tetrahedra=0 if shell else len(self.entity.elems),tetrahedralization='none' if shell else 'Genesis / TetGen')
            self._attach_ipc_inputs()
            if config.contact_mode=='no_self':self._disable_self_contact()
            elif config.contact_mode=='ground_only':self._ground_only_contacts()
            self.scene.build()
            if config.step_mode=='libuipc' and self.scene.sim.substeps!=1:
                raise RuntimeError('直接 IPC 步进仅支持一个子步')
        except Exception:
            self.close();raise

    def _init_usd(self,config,configure,ipc_options):
        """Run imported native geometries directly; Genesis is used only for parsing/options."""
        import uipc
        from uipc.core import Engine, Scene, World
        uipc.Logger.set_level(uipc.Logger.Level.Warn)
        from genesis.engine.couplers.ipc_coupler.utils import build_ipc_scene_config
        from inspect import signature
        from .usd import UsdImporter
        if config.step_mode != 'libuipc':
            raise ValueError('USD native assets use step_mode=libuipc; Genesis entities are not registered')
        if config.material_regions:raise ValueError('USD 材质分区请放在 bodies[物体路径].material_regions')
        options=ipc_options or gs.options.IPCCouplerOptions(contact_d_hat=config.contact_d_hat_m,
            contact_enable=config.contact_mode!='none',contact_friction_enable=config.contact_mode!='none')
        dt={'dt':config.dt} if 'dt' in signature(build_ipc_scene_config).parameters else {}
        native_config=build_ipc_scene_config(options,gs.options.SimOptions(dt=config.dt,gravity=config.gravity),**dt)
        if 'fem_preconditioner' in native_config['linear_system']:
            native_config['linear_system']['fem_preconditioner']='mas'
        native_scene=Scene(native_config)
        self._usd=UsdImporter((Path(__file__).resolve().parent.parent/config.mesh).resolve(),native_scene,config)
        self._drive_targets={}
        if configure is not None:configure(self)
        try:
            start=time.perf_counter()
            self._usd.build(options.contact_resistance)
            self._configure_usd_drives()
            self._layout_usd()
            if self._preview:
                self.build_seconds=time.perf_counter()-start
                return
            for public_id in config.pins:
                if public_id<0 or public_id>=len(self.surface_ids):raise ValueError('固定点编号超出表面顶点范围')
                vertex=self.surface_ids[public_id]
                for path,span in self._body_slices.items():
                    if span.start<=vertex<span.stop:
                        if path not in self._usd._forces:raise ValueError('ABD固定应在USD中设置')
                        uipc.view(self._usd.slots[path].geometry().vertices().find('is_fixed')).reshape(-1)[vertex-span.start]=1
            folder=self._output or Path(__file__).resolve().parent.parent/'runtime/usd'
            folder.mkdir(parents=True,exist_ok=True)
            self._native_engine=Engine('cuda',str(folder))
            self._world=World(self._native_engine)
            self._world.init(native_scene)
            if not self._world.is_valid():raise RuntimeError('Native asset initialization failed')
            self.build_seconds=time.perf_counter()-start
            self.set_forces(config.forces)
        except Exception:
            self.close();raise

    def _configure_usd_drives(self):
        from uipc.constitution import SoftPositionConstraint
        import uipc
        for path, settings in self._usd.drives.items():
            geometry = self._usd.slots[path].geometry()
            vertices = self._usd.positions(path)
            selection = settings['vertex_ids']
            ids = np.asarray(selection(vertices) if callable(selection) else selection, np.int32)
            if path not in self._usd._forces or not len(ids) or ids.min() < 0 or ids.max() >= len(vertices):
                raise ValueError('Position drive requires valid FEM node IDs')
            cells = np.asarray(uipc.view(geometry.tetrahedra().topo())).reshape(-1, 4)
            edges = np.stack([vertices[cells[:, i]]-vertices[cells[:, 3]] for i in range(3)], axis=-1)
            masses = np.zeros(len(vertices))
            np.add.at(masses, cells.ravel(), np.repeat(abs(np.linalg.det(edges))*self._usd._densities[path]/24, 4))
            authored_mass=geometry.vertices().find('mass')
            if authored_mass is not None:
                actual=np.asarray(uipc.view(authored_mass)).reshape(-1)
                if np.all(actual[ids]>0):masses=actual
            SoftPositionConstraint().apply_to(geometry)
            flags = uipc.view(geometry.vertices().find('is_constrained')).reshape(-1)
            flags[:] = 0
            flags[ids] = 1
            strength = uipc.view(geometry.vertices().find('strength_ratio')).reshape(-1)
            strength[:] = 0
            strength[ids] = settings['stiffness_n_m']/len(ids)*self.config.dt**2/masses[ids]
            self._usd.drive_nodes[path] = ids
            self._drive_targets[path] = vertices[ids].copy()
            def animate(info, path=path, ids=ids, targets=self._drive_targets):
                target = uipc.view(info.geo_slots()[0].geometry().vertices().find('aim_position')).reshape(-1, 3)
                target[ids] = targets[path]
            self._usd.scene.animator().insert(self._usd.scene.objects().find(path)[0], animate)

    def _layout_usd(self):
        """Keep the existing surface numbering/state API for a multi-body asset."""
        import uipc
        surfaces=[];faces=[];cells=[];positions=[];offset=0
        self.bodies={}
        self._body_slices={}
        for path,slot in self._usd.slots.items():
            geometry=slot.geometry();x=self._usd.positions(path)
            self._body_slices[path]=slice(offset,offset+len(x));positions.append(x)
            ids=np.flatnonzero(np.asarray(uipc.view(geometry.vertices().find('is_surf'))).reshape(-1))
            self.bodies[path]=self._usd.body_info[path] | dict(vertex_start=len(surfaces),
                vertex_count=len(ids),face_start=sum(len(f) for f in faces),
                point_force_supported=path in self._usd._forces and path not in self._usd.drives,
                pin_supported=path in self._usd._forces)
            surfaces.extend(ids+offset)
            face=np.asarray(uipc.view(geometry.triangles().topo())).reshape(-1,3).copy()
            mask=np.asarray(uipc.view(geometry.triangles().find('is_surf'))).reshape(-1)!=0
            orient=geometry.triangles().find('orient')
            if orient is not None:
                reverse=np.asarray(uipc.view(orient)).reshape(-1)<0
                face[reverse]=face[reverse][:,[0,2,1]]
            faces.append(face[mask]+offset)
            self.bodies[path]['face_count']=int(mask.sum())
            if path in self._usd._forces:
                cells.append(np.asarray(uipc.view(geometry.tetrahedra().topo())).reshape(-1,4)+offset)
            offset+=len(x)
        self._positions=np.vstack(positions)
        self.surface_ids=np.asarray(surfaces,np.int64)
        self.faces=np.searchsorted(self.surface_ids,np.vstack(faces))
        self.x=self._positions[self.surface_ids]
        self.elements=np.vstack(cells) if cells else np.empty((0,4),np.int64)
        self.element_material_ids=np.zeros(len(self.elements),np.int64)
        local_ids=[info['element_material_ids'] for info in self.bodies.values() if 'element_material_ids' in info]
        if local_ids:self.element_material_ids=np.concatenate(local_ids)
        self.force=np.zeros_like(self._positions)
        self.mesh_info=dict(simulation_vertices=offset,surface_vertices=len(self.surface_ids),
            tetrahedra=len(self.elements),tetrahedralization='authored USD',bodies=list(self._usd.slots))
        self.contact_check=contact_check(self.x,self.faces,self.config)
        try:
            from .plasticity import require_volume_backend
            require_volume_backend()
            plasticity_error=None
        except RuntimeError as exc:plasticity_error=str(exc)
        for body in self.bodies.values():
            body['unsupported_fields']={} if plasticity_error is None else {
                key:plasticity_error for key in body['editable_fields'] if key.startswith('volume_')}

    def set_attachment_parameters(self,attachment,stiffness_n_m,*,disable_contact=False):
        self._usd.attachment_options[attachment]=dict(stiffness_n_m=stiffness_n_m,
            disable_contact=disable_contact)

    def add_position_drive(self,body,vertex_ids,stiffness_n_m):
        """vertex_ids may be an array or a Python selector of initial world positions."""
        self._usd.drives[body]=dict(vertex_ids=vertex_ids,stiffness_n_m=stiffness_n_m)

    def place_on_ground(self,clearance=None):
        self._usd.ground_clearance=self.config.contact_d_hat_m if clearance is None else clearance

    def disable_contact(self,left,right):
        self._usd.disabled_pairs.append((left,right))

    @property
    def drive_nodes(self):
        return self._usd.drive_nodes

    def set_position_targets(self,body,positions):
        positions=np.asarray(positions,float)
        if positions.shape!=self._drive_targets[body].shape or not np.isfinite(positions).all():
            raise ValueError('Expected finite position targets for every drive node')
        self._drive_targets[body]=positions.copy()

    def set_motion(self,motion):
        """Python example callback; evaluated before each accepted step."""
        self._motion=motion

    def _attach_ipc_inputs(self):
        import uipc
        from uipc.constitution import FiniteElementExternalForce
        c=self.scene.sim.coupler
        if not hasattr(c,'_add_fem_entities_to_ipc'):raise RuntimeError('此 Genesis 的 IPC 输入接口已变化，需要更新适配')
        original=c._add_fem_entities_to_ipc
        def add_inputs(c):
            original()
            external_force=FiniteElementExternalForce()
            c._ipc_constitution_tabular.insert(external_force)
            bending=None
            m=self.config.material
            if self.config.body_type=='shell' and (self.config.material_regions or (m.bending_plasticity and m.bending_model)):
                from uipc.constitution import DiscreteShellBending, StrainPlasticDiscreteShellBending
                if any(mat.bending_model for mat in self.materials):
                    cls=StrainPlasticDiscreteShellBending if any(mat.bending_model and mat.bending_plasticity for mat in self.materials) else DiscreteShellBending
                    bending=cls()
                    c._ipc_constitution_tabular.insert(bending)
            name=('cloth' if self.config.body_type=='shell' else 'fem')+'_0_0'
            body=c._ipc_objects.find(name)[0]
            geometries=[slot.geometry() for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0])]
            self._ipc_geometry=geometries[0]
            for geometry in geometries:
                if self.config.body_type=='shell':self._configure_shell_material(geometry)
                elif m.volume_plasticity:
                    from .plasticity import configure_volume
                    configure_volume(geometry,m)
                if self.config.material_regions:
                    cells=geometry.triangles() if self.config.body_type=='shell' else geometry.tetrahedra()
                    if not np.array_equal(np.asarray(cells.topo().view()).reshape(self.elements.shape),self.elements):
                        raise RuntimeError('IPC 单元顺序与公开编号不一致，无法安全赋材质')
                    configure_elements(geometry,self.config.body_type,self.materials,self.element_material_ids,bending)
                elif bending is not None:
                    bending.apply_to(geometry,m.bending_rigidity_nm,
                        np.deg2rad(m.bending_yield_angle_deg),m.bending_plastic_hardening)
                external_force.apply_to(geometry,np.zeros(3,dtype=np.float64))
                fixed=geometry.vertices().find(uipc.builtin.is_fixed)
                if fixed is None:fixed=geometry.vertices().create(uipc.builtin.is_fixed,0)
                uipc.view(fixed).reshape(-1)[self.surface_ids[self.config.pins]]=1
            self._configure_mas(geometries)
            def animate(info):
                v=info.geo_slots()[0].geometry().vertices()
                force=uipc.view(v.find('external_force')).reshape(-1,3)
                force[:]=self.force
                uipc.view(v.find(uipc.builtin.is_constrained)).reshape(-1)[:]=self.force_flags
            c._ipc_scene.animator().insert(body,animate)
            self._animate=animate
        c._add_fem_entities_to_ipc=MethodType(add_inputs,c)

    def _configure_mas(self,geometries):
        import uipc
        from .plasticity import mas_supports_cuda_graph
        partition=getattr(uipc.geometry,'mesh_partition',None)
        reason=None
        if not callable(partition):reason='原生包缺少 MAS 网格分区接口'
        elif not mas_supports_cuda_graph():reason='原生包缺少 MAS CUDA stream 修复'
        elif len(self.config.pins)>=len(self._positions):reason='全部求解顶点均被固定'
        if reason is None:
            try:
                for geometry in geometries:
                    partition(geometry,16)
                    parts=geometry.vertices().find('mesh_part')
                    if parts is None:raise RuntimeError('未生成 mesh_part 属性')
                    uipc.view(parts).reshape(-1)[self.surface_ids[self.config.pins]]=-1
            except (RuntimeError,ValueError) as exc:
                reason='MAS 网格分区失败：'+str(exc)
        if reason is not None:
            # No mesh_part means diagonal fallback; remove partial partitioning.
            for geometry in geometries:
                if geometry.vertices().find('mesh_part') is not None:
                    geometry.vertices().destroy('mesh_part')
            self.fem_preconditioner='diag'
            self.fem_preconditioner_fallback_reason=reason
            gs.logger.warning(reason+'；自动使用 diag 预条件器')

    def _disable_self_contact(self):
        # Run after Genesis registers pairs, before libuipc World.init().
        c=self.scene.sim.coupler
        if not hasattr(c,'_register_contact_pairs'):
            raise RuntimeError('此 Genesis 缺少接触对注册接口，无法关闭自碰撞，需要更新适配')
        original=c._register_contact_pairs
        def register(c):
            original()
            contacts=c._ipc_cloth_contacts if self.config.body_type=='shell' else c._ipc_fem_contacts
            element=contacts[self.entity]
            m=self.config.material
            c._ipc_contact_tabular.insert(element,element,m.self_friction,c.options.contact_resistance,False)
        c._register_contact_pairs=MethodType(register,c)

    def _ground_only_contacts(self):
        c=self.scene.sim.coupler
        if not hasattr(c,'_register_contact_pairs'):
            raise RuntimeError('此 Genesis 缺少接触对注册接口，无法仅保留地面接触')
        original=c._register_contact_pairs
        def register(c):
            original()
            # Exclude plane elements: keep their registered friction/resistance.
            elements=[*c._ipc_cloth_contacts.values(),*c._ipc_fem_contacts.values(),*c._ipc_abd_contacts.values()]
            for i,a in enumerate(elements):
                for b in elements[i:]:c._ipc_contact_tabular.insert(a,b,0.0,c.options.contact_resistance,False)
        c._register_contact_pairs=MethodType(register,c)

    def _configure_shell_material(self,geometry):
        # BW expects an independent 2D shear coefficient, not Genesis's shared Pa modulus.
        import uipc
        shear=geometry.triangles().find('mu')
        if shear is None:raise RuntimeError('原生薄壳缺少 mu 属性，无法设置剪切刚度，需要更新适配')
        uipc.view(shear)[:]=self.config.material.shear_stiffness_n_m

    def set_forces(self,forces,body=None):
        forces=[Force.model_validate(f) for f in forces]
        if getattr(self,'_usd',None) is not None:
            if body is not None:
                self._usd.set_forces(body,np.asarray([f.vertex for f in forces],np.int64),
                    np.asarray([f.vector for f in forces],float).reshape(-1,3))
            else:
                ids=[f.vertex for f in forces]
                if any(i>=len(self.surface_ids) for i in ids):raise ValueError('受力点编号超出表面顶点范围')
                values=np.zeros_like(self._positions)
                if forces:np.add.at(values,self.surface_ids[ids],[f.vector for f in forces])
                values[self.surface_ids[self.config.pins]]=0
                for path,span in self._body_slices.items():
                    local=values[span]
                    if path in self._usd._forces:
                        if path in self.drive_nodes:
                            if np.any(local):raise ValueError('夹持物体请通过set_position_targets驱动')
                            continue
                        active=np.flatnonzero(np.any(local!=0,axis=1))
                        self._usd.set_forces(path,active,local[active])
                    elif np.any(local):raise NotImplementedError('当前点力接口适用于FEM，ABD点力需单独映射')
                self.config.forces=forces
            return self.state()
        ids=[f.vertex for f in forces]
        if any(i>=len(self.surface_ids) for i in ids):raise ValueError('受力点编号超出表面顶点范围')
        force=np.zeros_like(self.force)
        if forces:np.add.at(force,self.surface_ids[ids],[f.vector for f in forces])
        if not np.isfinite(force).all():raise ValueError('合力必须是有限数')
        force[self.surface_ids[self.config.pins]]=0
        self.force=force;self.force_flags=np.any(force!=0,axis=1).astype(np.int32);self.config.forces=forces
        return self.state()

    def step(self,*,snapshot=True):
        if self._failed:raise RuntimeError('上一步模拟失败，请重置后再运行')
        start=time.perf_counter()
        try:
            if getattr(self,'_motion',None) is not None:self._motion(self,self.time+self.config.dt)
            world=self._world if getattr(self,'_usd',None) is not None else self.scene.sim.coupler._ipc_world
            if not world.is_valid():raise RuntimeError('IPC 状态无效，请重置后再运行')
            physics_start=time.perf_counter()
            if self.config.step_mode=='libuipc':
                world.advance()
                world.retrieve()
            else:self.scene.step()
            self.last_step_seconds=time.perf_counter()-physics_start
            if not world.is_valid():raise RuntimeError('IPC 求解失败，请重置后再运行；详见后端日志')
            all_positions=self._read_positions()
            if not np.isfinite(all_positions).all():raise RuntimeError('Genesis 返回非有限状态，请检查材料或时间步并重置')
            positions=all_positions[self.surface_ids]
        except Exception:
            self._failed=True
            raise
        self._positions=all_positions;self.x=positions
        self.time+=self.config.dt;self.frame+=1;self.step_ms=(time.perf_counter()-start)*1000
        return self.state() if snapshot else None

    def _read_positions(self):
        if getattr(self,'_usd',None) is not None:
            return np.vstack([self._usd.positions(path) for path in self._usd.slots])
        if self.config.step_mode=='libuipc':
            import uipc
            transformed,=uipc.geometry.apply_transform(self._ipc_geometry)
            return np.array(transformed.positions().view(),copy=True).reshape(-1,3)
        return self.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3).copy()

    def get_simulation_positions(self,body=None):
        """Copy of the last accepted state, including internal vertices.

        Web/API consumers continue to use state()['positions'] (surface only).
        Only genesis_world keeps Genesis entity.get_state() synchronized every frame.
        """
        if body is not None:return self._usd.positions(body)
        return self._positions.copy()

    def state(self,mesh=False):
        data=dict(positions=self.x.tolist(),time=self.time,frame=self.frame,pins=self.config.pins,
            backend='libuipc CUDA (Genesis USD parser)' if getattr(self,'_usd',None) is not None else 'Genesis CUDA / IPC CUDA',
            contact_mode=self.config.contact_mode,metrics={'step_ms':self.step_ms})
        if mesh:data.update(contact_check=self.contact_check,faces=self.faces.tolist(),config=self.config.model_dump(mode='json'),volume_mesh=self.mesh_info,
            element_mesh=dict(kind='triangle' if getattr(self,'_usd',None) is None and self.config.body_type=='shell' else 'tetrahedron',
                index_base=0,positions=self._positions.tolist(),
                elements=self.elements.tolist(),material_ids=self.element_material_ids.tolist(),
                surface_vertex_ids=self.surface_ids.tolist()),
            mesh=dict(file=self.config.mesh,vertices=len(self.surface_ids),triangles=len(self.faces),index_base=0,units='m',index_source='simulation_surface'))
        if mesh and getattr(self,'_usd',None) is not None:
            data.update(bodies=self.bodies,attachments=[{k:v for k,v in r.items()
                if k not in ('vertex_ids','anchors_local')} for r in self._usd.attachment_regions],
                warnings=self._usd.warnings)
        return data

    def close(self):
        if getattr(self,'_usd',None) is not None:
            self._motion=None
            self._world=None
            self._native_engine=None
            self._usd=None
        if hasattr(self,'scene'):self.scene.destroy()
