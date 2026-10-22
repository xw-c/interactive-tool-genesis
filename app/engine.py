"""Native Genesis IPC/CUDA FEM with selectable contact; surface vertices are public."""
import time
from types import MethodType
import numpy as np
import genesis as gs
from .config import Force
from .mesh import mesh_path, contact_check

class GenesisEngine:
    def __init__(self,config):
        # Both Genesis kernels and the independent IPC engine use CUDA.
        if not gs._initialized:gs.init(backend=gs.cuda,precision='64',logging_level='warning')
        if gs.backend!=gs.cuda:raise RuntimeError('本工具要求 Genesis CUDA 后端，请在新进程中使用 gs.cuda 初始化')
        self.fem_preconditioner='mas'
        self.fem_preconditioner_fallback_reason=None
        self.config=config;self.time=0.;self.frame=0;self.step_ms=0.;self._failed=False
        m=config.material;shell=config.body_type=='shell'
        if not shell and m.volume_plasticity:
            from .plasticity import require_volume_backend
            require_volume_backend()
        self.scene=gs.Scene(sim_options=gs.options.SimOptions(dt=config.dt,gravity=config.gravity),
            coupler_options=gs.options.IPCCouplerOptions(contact_d_hat=config.contact_d_hat_m,
                contact_enable=config.contact_mode!='none',contact_friction_enable=config.contact_mode!='none'),show_viewer=False)
        try:
            self.scene.add_entity(gs.morphs.Plane(),material=gs.materials.Rigid(coup_friction=m.self_friction))
            common=dict(E=m.youngs_modulus_pa,nu=m.poisson_ratio,rho=m.density_kg_m3,friction_mu=m.self_friction)
            material=gs.materials.FEM.Cloth(**common,thickness=m.thickness_m/2,
                bending_stiffness=m.bending_rigidity_nm if m.bending_model and not m.bending_plasticity else None) if shell else gs.materials.FEM.Elastic(**common)
            self.entity=self.scene.add_entity(gs.morphs.Mesh(file=str(mesh_path(config.mesh)),decimate=False,
                maxvolume=config.tet_max_volume_m3 if not shell else -1,
                nobisect=shell or config.tet_max_volume_m3==-1),material=material)
            vertices=self.entity.init_positions.detach().cpu().numpy().reshape(-1,3)
            self._positions=vertices.copy()
            triangles=self.entity.surface_triangles
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
            plastic_bending=None
            m=self.config.material
            if self.config.body_type=='shell' and m.bending_plasticity and m.bending_model:
                from uipc.constitution import StrainPlasticDiscreteShellBending
                plastic_bending=StrainPlasticDiscreteShellBending()
                c._ipc_constitution_tabular.insert(plastic_bending)
            name=('cloth' if self.config.body_type=='shell' else 'fem')+'_0_0'
            body=c._ipc_objects.find(name)[0]
            geometries=[slot.geometry() for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0])]
            self._ipc_geometry=geometries[0]
            for geometry in geometries:
                if self.config.body_type=='shell':self._configure_shell_material(geometry)
                elif m.volume_plasticity:
                    from .plasticity import configure_volume
                    configure_volume(geometry,m)
                if plastic_bending is not None:
                    plastic_bending.apply_to(geometry,m.bending_rigidity_nm,
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

    def set_forces(self,forces):
        forces=[Force.model_validate(f) for f in forces]
        ids=[f.vertex for f in forces]
        if any(i>=len(self.surface_ids) for i in ids):raise ValueError('受力点编号超出表面顶点范围')
        force=np.zeros_like(self.force)
        if forces:np.add.at(force,self.surface_ids[ids],[f.vector for f in forces])
        if not np.isfinite(force).all():raise ValueError('合力必须是有限数')
        force[self.surface_ids[self.config.pins]]=0
        self.force=force;self.force_flags=np.any(force!=0,axis=1).astype(np.int32);self.config.forces=forces
        return self.state()

    def step(self):
        if self._failed:raise RuntimeError('上一步模拟失败，请重置后再运行')
        start=time.perf_counter()
        try:
            world=self.scene.sim.coupler._ipc_world
            if not world.is_valid():raise RuntimeError('IPC 状态无效，请重置后再运行')
            if self.config.step_mode=='libuipc':
                world.advance()
                world.retrieve()
            else:self.scene.step()
            if not world.is_valid():raise RuntimeError('IPC 求解失败，请重置后再运行；详见后端日志')
            all_positions=self._read_positions()
            if not np.isfinite(all_positions).all():raise RuntimeError('Genesis 返回非有限状态，请检查材料或时间步并重置')
            positions=all_positions[self.surface_ids]
        except Exception:
            self._failed=True
            raise
        self._positions=all_positions;self.x=positions
        self.time+=self.config.dt;self.frame+=1;self.step_ms=(time.perf_counter()-start)*1000
        return self.state()

    def _read_positions(self):
        if self.config.step_mode=='libuipc':
            import uipc
            transformed,=uipc.geometry.apply_transform(self._ipc_geometry)
            return np.array(transformed.positions().view(),copy=True).reshape(-1,3)
        return self.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3).copy()

    def get_simulation_positions(self):
        """Copy of the last accepted state, including internal vertices.

        Web/API consumers continue to use state()['positions'] (surface only).
        Only genesis_world keeps Genesis entity.get_state() synchronized every frame.
        """
        return self._positions.copy()

    def state(self,mesh=False):
        data=dict(positions=self.x.tolist(),time=self.time,frame=self.frame,pins=self.config.pins,
            backend='Genesis CUDA / IPC CUDA',contact_mode=self.config.contact_mode,metrics={'step_ms':self.step_ms})
        if mesh:data.update(contact_check=self.contact_check,faces=self.faces.tolist(),config=self.config.model_dump(mode='json'),volume_mesh=self.mesh_info,
            mesh=dict(file=self.config.mesh,vertices=len(self.surface_ids),triangles=len(self.faces),index_base=0,units='m',index_source='simulation_surface'))
        return data

    def close(self):
        if hasattr(self,'scene'):self.scene.destroy()
