"""Genesis USD import and missing native mappings; no scene/world lifecycle here."""
from pathlib import Path
from contextlib import nullcontext
import numpy as np
from pxr import Gf, Usd, UsdShade
from scipy.spatial import ConvexHull, cKDTree
import uipc
from uipc.constitution import (
    AffineBodyConstitution, ElasticModuli, FiniteElementExternalForce, StableNeoHookean,
)
from uipc.geometry import ground, label_surface, label_triangle_orient, tetmesh, trimesh

from .attachments import fixed_joint, stitch_points, locate_tets
from .config import Material
from .materials import resolve_materials, element_material_ids, configure_elements


def _value(prim, name, default=None):
    attribute = prim.GetAttribute(name)
    result = attribute.Get() if attribute else None
    return default if result is None else result


def _fem_geometry(vertices, cells, E, nu, rho, fixed=False):
    cells = np.asarray(cells)
    if cells.ndim != 2 or cells.shape[1] != 4 or cells.dtype.kind not in 'iu' or not len(cells):
        raise ValueError('Expected nonempty integer (N,4) tetrahedra')
    if cells.min() < 0 or cells.max() >= len(vertices):
        raise ValueError('Tetrahedron index out of range')
    cells = cells.astype(np.int32).copy()
    edges = np.stack([vertices[cells[:, i]]-vertices[cells[:, 0]] for i in (1, 2, 3)], axis=-1)
    determinants = np.linalg.det(edges)
    if not np.isfinite(vertices).all() or np.any(np.abs(determinants) < 1e-15):
        raise ValueError('Nonfinite or degenerate FEM geometry')
    cells[determinants < 0] = cells[determinants < 0][:, [1, 0, 2, 3]]
    geometry = tetmesh(vertices, cells)
    label_surface(geometry)
    label_triangle_orient(geometry)
    StableNeoHookean().apply_to(geometry, ElasticModuli.youngs_poisson(E, nu), mass_density=rho)
    if fixed:
        uipc.view(geometry.vertices().find('is_fixed')).reshape(-1)[:] = 1
    return geometry


def _abd_geometry(vertices, mass_kg, fixed=False):
    if not np.isfinite(mass_kg) or mass_kg <= 0:
        raise ValueError('Native ABD needs explicit positive physics:mass')
    center = (vertices.min(axis=0)+vertices.max(axis=0))/2
    local = vertices-center
    hull = ConvexHull(local)
    faces = hull.simplices.copy()
    normals = np.cross(local[faces[:, 1]]-local[faces[:, 0]], local[faces[:, 2]]-local[faces[:, 0]])
    reverse = np.einsum('ij,ij->i', normals, hull.equations[:, :3]) < 0
    faces[reverse] = faces[reverse][:, [0, 2, 1]]
    ids = np.unique(faces)
    faces = np.searchsorted(ids, faces).astype(np.int32)
    local = local[ids]
    volume = np.einsum('ij,ij->i', local[faces[:, 0]],
                       np.cross(local[faces[:, 1]], local[faces[:, 2]])).sum()/6
    geometry = trimesh(local, faces)
    label_surface(geometry)
    AffineBodyConstitution().apply_to(geometry, 1e9, mass_kg/volume)
    uipc.view(geometry.transforms())[0, :3, 3] = center
    if fixed:
        uipc.view(geometry.instances().find('is_fixed')).reshape(-1)[:] = 1
    return geometry


class UsdImporter:
    """Reuse Genesis parsing and register data in the engine-owned native scene."""
    def __init__(self, filename, scene, config):
        self.scene, self.config, self.material = scene, config, config.material
        self.dt = float(np.asarray(uipc.view(scene.config().find('dt'))).reshape(-1)[0])
        self.slots, self._forces = {}, {}
        self._contacts, self._densities = {}, {}
        self.drive_nodes = {}
        self.attachment_regions, self.warnings = [], []
        self.attachment_options, self.drives = {}, {}
        self.disabled_pairs = []
        self.body_info = {}
        self.ground_clearance = None
        self._floor_contact = None
        self._offset = np.zeros(3)
        self._import_genesis(filename)

    def build(self, contact_resistance):
        self.scene.contact_tabular().default_model(self.material.self_friction, contact_resistance)
        for links, joints, geometries, _ in self.genesis_rigid:
            for link, joint_group, geom_group in zip(links, joints, geometries):
                self._load_body(self.stage.GetPrimAtPath(link['name']),
                                link, joint_group, geom_group)
        if self.ground_clearance is not None:
            floor = ground(0., np.array([0., 0., 1.]))
            self._floor_contact = self.scene.contact_tabular().create('__floor__')
            self._floor_contact.apply_to(floor)
            self.scene.objects().create('__floor__').geometries().create(floor)
        for prim in self.stage.Traverse():
            if any(name in str(prim.GetMetadata('apiSchemas')) for name in (
                    'DeformableBodyAPI', 'DeformableSurfaceAPI', 'DeformableVolumeAPI')) and not prim.GetAttribute(
                    'physxDeformable:simulationRestPoints'):
                raise NotImplementedError(f'Unsupported deformable schema: {prim.GetPath()}')
            if prim.GetAttribute('physxDeformable:simulationRestPoints'):
                self._load_body(prim)
        unknown = self.config.bodies.keys()-self.slots.keys()
        if unknown:
            raise ValueError(f'Unknown USD body overrides: {sorted(unknown)}')
        if self.ground_clearance is not None and self.slots:
            self._offset[2] = self.ground_clearance-min(self.positions(p)[:, 2].min() for p in self.slots)
            for path, slot in self.slots.items():
                geometry = slot.geometry()
                if path in self._forces:
                    uipc.view(geometry.positions()).reshape(-1, 3)[:] += self._offset
                else:
                    uipc.view(geometry.transforms())[0, :3, 3] += self._offset
        self._load_fixed_joints()
        for prim in self.stage.Traverse():
            self._load_attachment(prim)
        for left, right in self.disabled_pairs:
            self.scene.contact_tabular().insert(self._contacts[left], self._contacts[right], 0., 0., False)
        for left in self.slots:
            override=self.config.bodies.get(left)
            if self._floor_contact is not None and override is not None and 'self_friction' in override.material.model_fields_set:
                friction=float(np.sqrt(self.body_info[left]['material']['self_friction']*self.material.self_friction))
                self.scene.contact_tabular().insert(self._contacts[left],self._floor_contact,
                    friction,contact_resistance,self.config.contact_mode!='none')
                self.body_info[left]['ground_friction']=friction
            for right in self.slots:
                friction = np.sqrt(self.body_info[left]['material']['self_friction']*
                                   self.body_info[right]['material']['self_friction'])
                enabled = not (self.config.contact_mode == 'none' or
                    self.config.contact_mode == 'ground_only' or
                    self.config.contact_mode == 'no_self' and left == right or
                    (left, right) in self.disabled_pairs or (right, left) in self.disabled_pairs)
                self.scene.contact_tabular().insert(self._contacts[left], self._contacts[right],
                    friction, contact_resistance, enabled)

    def _import_genesis(self, filename):
        import genesis as gs
        from genesis.utils.usd import UsdContext, parse_usd_stage, parse_usd_rigid_entity
        from genesis.utils.usd.usd_context import find_rigid_bodies_in_range
        self._gs = gs
        self.usd_context = UsdContext(str(Path(filename).resolve()))
        self.usd_context.find_all_materials()
        self.stage = self.usd_context.stage
        context = {'usd_ctx': self.usd_context} if 'usd_ctx' in gs.morphs.USD.model_fields else {}
        morph = gs.morphs.USD(file=self.usd_context.stage_file, **context,
                                    decimate=False, convexify=False, align=False)
        # Keep Genesis's return values unchanged, including meshes, UVs and surfaces.
        self.genesis_rigid = []
        if find_rigid_bodies_in_range(Usd.PrimRange(self.stage.GetPseudoRoot())):
            parsed = parse_usd_stage(morph)
            with parsed if hasattr(parsed, '__enter__') else nullcontext(parsed) as morphs:
                self.genesis_rigid = [parse_usd_rigid_entity(morph, gs.surfaces.Default()) for morph in morphs]

    def _world_points(self, prim, coordinates):
        transform = self.usd_context.compute_transform(prim)
        return np.asarray(coordinates, float) @ transform[:3, :3].T + transform[:3, 3] + self._offset

    def _mesh_vertices(self, actor, geometries):
        """Convert Genesis collision meshes from link-local to native world space."""
        from genesis.utils import geom as gu
        pieces = []
        transform, _ = self.usd_context.compute_gs_transform(actor)
        selected = [g for g in geometries if 'mesh' in g] or geometries
        for info in selected:
            mesh = info.get('mesh', info.get('vmesh'))
            if mesh is None:
                continue
            local = np.asarray(mesh.verts) @ gu.quat_to_R(info['quat']).T + info['pos']
            pieces.append(local @ transform[:3, :3].T + transform[:3, 3] + self._offset)
        if not pieces:
            raise ValueError(f'Genesis produced no geometry for {actor.GetPath()}')
        return np.vstack(pieces)

    def _load_body(self, prim, link=None, joints=None, geometries=None):
        path = str(prim.GetPath())
        override = self.config.bodies.get(path)
        unmapped = [str(a.GetName()) for a in prim.GetAuthoredAttributes() if
                    (str(a.GetName()).startswith(('physxRigidBody:', 'physxDeformable:'))
                     and str(a.GetName()) not in ('physxDeformable:simulationRestPoints', 'physxDeformable:simulationIndices'))
                    or str(a.GetName()) in ('physics:diagonalInertia', 'physics:centerOfMass',
                                             'physics:principalAxes', 'physics:density')]
        if unmapped:
            self.warnings.append(f'{path}: native model replaces {unmapped}')
        if prim.GetAttribute('physxDeformable:simulationRestPoints'):
            if override is not None:
                allowed={'youngs_modulus_pa','poisson_ratio','density_kg_m3','self_friction',
                    'volume_plasticity','volume_yield_strain_percent','volume_plastic_hardening'}
                if override.mass_kg is not None or override.material.model_fields_set-allowed:
                    raise ValueError(f'{path}: volume FEM uses E/nu/density/plasticity; mass_kg is ABD-only')
            material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial('physics')
            properties = {key: (_value(material.GetPrim(), 'physxDeformableBodyMaterial:'+name) if material else None)
                          for key, name in [('youngs_modulus_pa', 'youngsModulus'),
                                            ('poisson_ratio', 'poissonsRatio'), ('density_kg_m3', 'density')]}
            overrides = self.material.model_dump(exclude_unset=True)
            properties.update({key: overrides[key] for key in properties if key in overrides})
            if override is not None:
                properties.update({key: value for key, value in override.material.model_dump(exclude_unset=True).items()
                                   if key in properties})
            if any(v is None for v in properties.values()):
                raise ValueError(f'{path}: USD or JSON overrides must provide E/nu/rho')
            vertices = self._world_points(prim, _value(prim, 'physxDeformable:simulationRestPoints'))
            cells = np.asarray(_value(prim, 'physxDeformable:simulationIndices'), np.int32).reshape(-1, 4)
            geometry = _fem_geometry(vertices, cells, *properties.values())
            values = self.material.model_dump() | properties
            if override is not None:
                values.update(override.material.model_dump(exclude_unset=True))
            effective = Material.model_validate(values)
            regions = override.material_regions if override is not None else []
            local = self.config.model_copy(update=dict(material=effective, material_regions=regions))
            materials = resolve_materials(local)
            if any(m.volume_plasticity for m in materials):
                from .plasticity import require_volume_backend
                require_volume_backend()
            if regions or effective.volume_plasticity:
                configure_elements(geometry, 'volume', materials, element_material_ids(local, len(cells)))
            self.body_info[path] = dict(kind='fem', body_type='volume', material=effective.model_dump(),
                editable_fields=['youngs_modulus_pa','poisson_ratio','density_kg_m3','self_friction',
                    'volume_plasticity','volume_yield_strain_percent','volume_plastic_hardening'],
                element_material_ids=element_material_ids(local, len(cells)).tolist())
            self._densities[path] = properties['density_kg_m3']
            uipc.view(geometry.meta().find('self_collision'))[:] = 1
            if path not in self.drives:
                FiniteElementExternalForce().apply_to(geometry, np.zeros(3))
            self._forces[path] = np.zeros_like(vertices)
        else:
            fixed = link['parent_idx'] < 0 and all(j['type'] == self._gs.JOINT_TYPE.FIXED for j in joints)
            mass = link.get('inertial_mass')
            if override is not None and override.mass_kg is not None:
                mass = override.mass_kg
            if mass is None:
                raise ValueError(f'{path}: explicit physics:mass required for native ABD')
            geometry = _abd_geometry(self._mesh_vertices(prim, geometries), mass, fixed)
            effective = self.material.model_dump()
            if override is not None:
                unsupported = override.material.model_fields_set-{'self_friction'}
                if unsupported or override.material_regions:
                    raise ValueError(f'{path}: ABD supports mass_kg and self_friction, not FEM material fields')
                effective.update(override.material.model_dump(exclude_unset=True))
            self.body_info[path] = dict(kind='abd', body_type='rigid', mass_kg=float(mass),
                material=effective, editable_fields=['self_friction'])
        contact = self.scene.contact_tabular().create(path)
        contact.apply_to(geometry)
        self._contacts[path] = contact
        obj = self.scene.objects().create(path)
        self.slots[path] = obj.geometries().create(geometry)[0]
        if path in self._forces and path not in self.drives:
            force_flag = ('external_force/is_constrained' if geometry.vertices().find('external_force/is_constrained') is not None
                          else 'is_constrained')
            def animate(info, buffers=self._forces, path=path, force_flag=force_flag):
                attributes = info.geo_slots()[0].geometry().vertices()
                forces = buffers[path]
                uipc.view(attributes.find('external_force')).reshape(-1, 3)[:] = forces
                uipc.view(attributes.find(force_flag)).reshape(-1)[:] = np.any(forces != 0, axis=1)
            self.scene.animator().insert(obj, animate)

    def _load_fixed_joints(self):
        """Map Genesis joint results; never implement another joint parser."""
        for links, joint_groups, _, _ in self.genesis_rigid:
            for link, group in zip(links, joint_groups):
                for joint in group:
                    kind, parent = joint['type'], link['parent_idx']
                    if kind not in (self._gs.JOINT_TYPE.FREE, self._gs.JOINT_TYPE.FIXED):
                        raise NotImplementedError(f"Genesis parsed {joint['name']}; its native joint mapping is unsupported")
                    if kind == self._gs.JOINT_TYPE.FIXED:
                        prim = self.stage.GetPrimAtPath(joint['name'])
                        if prim:
                            # Native fixed joints preserve the initial pose: validate this
                            # mapping assumption against the already-parsed USD source.
                            frames = []
                            for i in (0, 1):
                                targets = prim.GetRelationship(f'physics:body{i}').GetTargets()
                                actor = self.stage.GetPrimAtPath(targets[0]) if targets else self.stage.GetPseudoRoot()
                                position = self._world_points(actor, [_value(prim, f'physics:localPos{i}', (0., 0., 0.))])[0]
                                rotation = np.asarray(Gf.Matrix3d(_value(prim, f'physics:localRot{i}', Gf.Quatf(1.))))
                                linear = self.usd_context.compute_transform(actor)[:3, :3]
                                U, _, V = np.linalg.svd(rotation @ linear.T)
                                frames.append((position, U @ V))
                            if not all(np.allclose(a, b, atol=1e-7, rtol=0) for a, b in zip(*frames)):
                                raise NotImplementedError('Native fixed joints require an assembled initial pose')
                            if any(np.isfinite(_value(prim, key, np.inf)) for key in ('physics:breakForce', 'physics:breakTorque')):
                                raise NotImplementedError('Breakable fixed joints are unsupported')
                    if parent < 0:
                        continue  # Genesis root FREE/FIXED was applied in _load_body.
                    if kind != self._gs.JOINT_TYPE.FIXED:
                        raise NotImplementedError('Free joints between native bodies need an explicit mapping')
                    left, right = links[parent]['name'], link['name']
                    fixed_joint(self.scene, joint['name'], self.slots[left], self.slots[right])

    def _load_attachment(self, prim):
        kind = str(prim.GetTypeName())
        if 'Attachment' not in kind:
            return
        if kind != 'PhysxPhysicsAttachment':
            raise NotImplementedError(f'Unsupported attachment schema: {prim.GetPath()} ({kind})')
        if not _value(prim, 'attachmentEnabled', True):
            return
        targets = [prim.GetRelationship(f'actor{i}').GetTargets() for i in (0, 1)]
        if any(len(t) != 1 for t in targets):
            raise ValueError('Attachment requires exactly two body targets')
        paths = [str(t[0]) for t in targets]
        left, right = [self.slots[path] for path in paths]
        options = self.attachment_options.get(str(prim.GetPath()), {})
        total = float(options.get('stiffness_n_m', 1500.))
        points = [self._world_points(self.stage.GetPrimAtPath(paths[i]), _value(prim, f'points{i}'))
                  for i in (0, 1)]
        if points[0].shape != points[1].shape or points[0].ndim != 2 or points[0].shape[1] != 3 or not len(points[0]):
            raise ValueError(f'{prim.GetPath()}: expected equal nonempty attachment point arrays')
        _, indices = np.unique(np.round(np.column_stack(points), 8), axis=0, return_index=True)
        indices = np.sort(indices)
        points = [p[indices] for p in points]
        fem = [p in self._forces for p in paths]
        source = 0 if fem[0] else 1 if fem[1] else 0
        # One authored attachment is one region, regardless of sample layout.
        vertices = [self.positions(path) for path in paths]
        nearest = [cKDTree(vertices[i]).query(points[i]) if fem[i] else None for i in (0, 1)]
        errors = [float(item[0].max()) if item is not None else 0. for item in nearest]
        explicit = {}
        if sum(fem) == 1:
            end = source
            ids = nearest[end][1]
            if errors[end] > 1e-8:
                cells = np.asarray(uipc.view(self.slots[paths[end]].geometry().tetrahedra().topo())).reshape(-1, 4)
                ids = cells[locate_tets(points[end], vertices[end], cells)].ravel()
            ids = np.unique(ids).astype(np.int32)
            world = vertices[end][ids]
            offset = np.mean(points[1-end]-points[end], axis=0)
            points[end], points[1-end] = world, world+offset
            explicit[f'vertex_ids{end}'] = ids
        elif all(fem):
            # Keep the existing, reported nearest-node approximation for FEM/FEM.
            # Validate embedded samples before projection; never extrapolate.
            for end in (0, 1):
                if errors[end] > 1e-8:
                    cells = np.asarray(uipc.view(self.slots[paths[end]].geometry().tetrahedra().topo())).reshape(-1, 4)
                    locate_tets(points[end], vertices[end], cells)
            pairs = np.unique(np.column_stack([item[1] for item in nearest]), axis=0)
            points = [vertices[i][pairs[:, i]] for i in (0, 1)]
            explicit = dict(vertex_ids0=pairs[:, 0], vertex_ids1=pairs[:, 1])
        slot, _ = stitch_points(self.scene, str(prim.GetPath()), left, right,
            *points, total/len(points[0]), **explicit)
        pairs = np.asarray(uipc.view(slot.geometry().instances().find('topo'))).reshape(-1, 2)
        record = dict(attachment=str(prim.GetPath()), body0=paths[0], body1=paths[1],
            node_pairs=pairs.tolist(), pair_count=len(pairs), total_stiffness_n_m=total,
            nearest_node_error_m=errors, approximation=bool(max(errors)>1e-8),
            stiffness_n_m=total/len(pairs))
        if sum(fem) == 1:
            record.update(vertex_ids=pairs[:, source],
                anchors_local=np.asarray(uipc.view(self.slots[paths[1-source]].geometry().positions())).reshape(-1, 3)[pairs[:, 1-source]].copy())
        self.attachment_regions.append(record)
        filtered = any(len(_value(prim, f'collisionFilterIndices{i}', [])) for i in (0, 1))
        if options.get('disable_contact', False) or filtered:
            self.disabled_pairs.append(tuple(paths))
            if filtered:
                self.warnings.append(f'{prim.GetPath()}: local USD contact filter approximated by whole-body exclusion')


    @property
    def deformable_paths(self):
        return tuple(self._forces)

    def positions(self, path):
        """Copy current solver vertices in world meters, indexed by native node ID."""
        geometry = self.slots[path].geometry()
        positions = np.array(uipc.view(geometry.positions())).reshape(-1, 3)
        if path not in self._forces:
            transform = np.asarray(uipc.view(geometry.transforms()))[0]
            positions = positions @ transform[:3, :3].T + transform[:3, 3]
        return positions

    def set_forces(self, path, vertex_ids, forces_n):
        """Replace one FEM body's point forces in world N; repeated IDs add."""
        if path in self.drive_nodes:
            raise ValueError('This body uses an implicit position drive; set its targets instead')
        current = self._forces[path]
        ids = np.asarray(vertex_ids)
        if ids.ndim != 1 or (len(ids) and (ids.dtype.kind not in 'iu' or ids.min() < 0 or ids.max() >= len(current))):
            raise ValueError('Invalid FEM force vertex IDs')
        ids = ids.astype(np.int64)
        forces = np.broadcast_to(np.asarray(forces_n, float), (len(ids), 3))
        if not np.isfinite(forces).all():
            raise ValueError('Point forces must be finite')
        updated = np.zeros_like(current)
        np.add.at(updated, ids, forces)
        self._forces[path] = updated
