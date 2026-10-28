"""Exercise Genesis USD reuse, native connections, and point-force input."""
import os
from pathlib import Path
import tempfile
import unittest
import numpy as np
import app  # Select the configured native package before any uipc import.

ROOT = Path(__file__).resolve().parents[1]
VERTICES = np.array([[0., 0., 0.], [.05, 0., 0.], [0., .05, 0.], [0., 0., .05]])


class MaterialOverrideTests(unittest.TestCase):
    def test_usd_save_load_preserves_only_explicit_overrides(self):
        from app.config import Setup
        config = Setup(mesh='asset.usd', material={'density_kg_m3': 1000.})
        restored = Setup.model_validate_json(config.model_dump_json())
        self.assertEqual(restored.material.model_fields_set, {'density_kg_m3'})
        self.assertEqual(restored.model_dump()['material'], {'density_kg_m3': 1000.})

    def test_obj_material_serialization_retains_existing_defaults(self):
        from app.config import Setup
        material = Setup(mesh='model.obj').model_dump()['material']
        self.assertIn('youngs_modulus_pa', material)
        self.assertIn('poisson_ratio', material)


@unittest.skipUnless(os.environ.get('IPC_GPU_TEST') == '1', 'Use fixed pyuipc 0.0.29 and IPC_GPU_TEST=1')
class NativeAssetTests(unittest.TestCase):
    def test_automatic_usd_connections_for_all_body_pairs(self):
        from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
        from app.config import Setup
        from app.engine import GenesisEngine
        import genesis as gs
        for kinds in (('fem','fem'),('fem','abd'),('abd','fem'),('abd','abd')):
            with self.subTest(kinds=kinds), tempfile.TemporaryDirectory() as folder:
                file=Path(folder)/'automatic.usda'
                stage=Usd.Stage.CreateNew(str(file))
                UsdGeom.SetStageMetersPerUnit(stage,1.)
                UsdGeom.SetStageUpAxis(stage,'Z')
                material=UsdShade.Material.Define(stage,'/material')
                for key,value in [('youngsModulus',5e5),('poissonsRatio',.2),('density',500.)]:
                    material.GetPrim().CreateAttribute('physxDeformableBodyMaterial:'+key,Sdf.ValueTypeNames.Float).Set(value)
                for path,kind in zip(('/arbitrary/left','/arbitrary/right'),kinds):
                    mesh=UsdGeom.Mesh.Define(stage,path)
                    # Three distant attachment sites in one authored structure.
                    x=np.vstack([VERTICES+[shift,0,1] for shift in (0,.5,1.)])
                    mesh.CreatePointsAttr(x.tolist())
                    mesh.CreateFaceVertexCountsAttr([3]*12)
                    faces=np.array([0,2,1,0,1,3,0,3,2,1,2,3])
                    mesh.CreateFaceVertexIndicesAttr(np.concatenate([faces+4*i for i in range(3)]).tolist())
                    prim=mesh.GetPrim()
                    if kind=='fem':
                        prim.CreateAttribute('physxDeformable:simulationRestPoints',Sdf.ValueTypeNames.Point3fArray).Set(x.tolist())
                        prim.CreateAttribute('physxDeformable:simulationIndices',Sdf.ValueTypeNames.IntArray).Set(list(range(12)))
                        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material,materialPurpose='physics')
                    else:
                        UsdPhysics.RigidBodyAPI.Apply(prim)
                        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(.1)
                attachment=stage.DefinePrim('/connection','PhysxPhysicsAttachment')
                for i,path in enumerate(('/arbitrary/left','/arbitrary/right')):
                    attachment.CreateRelationship(f'actor{i}').SetTargets([path])
                    samples=[(shift+delta,.01,1.01) for shift in (0,.5,1.) for delta in (.01,.011)]
                    attachment.CreateAttribute(f'points{i}',Sdf.ValueTypeNames.Point3fArray).Set(samples)
                stage.GetRootLayer().Save()
                left_material={'youngs_modulus_pa':1e6}
                if os.environ.get('IPC_VOLUME_GPU_TEST')=='1':left_material['volume_plasticity']=True
                overrides={'/arbitrary/left':({'material':left_material} if kinds[0]=='fem' else {'mass_kg':.2})}
                engine=GenesisEngine(Setup(mesh=str(file),body_type='volume',dt=.001,gravity=(0,0,0),contact_mode='none',bodies=overrides),
                    ipc_options=gs.options.IPCCouplerOptions(contact_enable=False,newton_semi_implicit_enable=False),output=Path(folder)/'run')
                try:
                    state=engine.state(True)
                    self.assertEqual(len(state['attachments']),1)
                    self.assertGreater(state['attachments'][0]['pair_count'],0)
                    self.assertAlmostEqual(state['attachments'][0]['total_stiffness_n_m'],1500.)
                    if kinds[0]=='fem':
                        import uipc
                        cells=engine._usd.slots['/arbitrary/left'].geometry().tetrahedra()
                        np.testing.assert_allclose(uipc.view(cells.find('mu')),4/3*1e6/(2*1.2))
                        if left_material.get('volume_plasticity'):
                            self.assertEqual(int(np.asarray(uipc.view(cells.find('plasticity_enabled'))).ravel()[0]),1)
                    for _ in range(3):engine.step(snapshot=False)
                    self.assertTrue(np.isfinite(engine.get_simulation_positions()).all())
                finally:engine.close()

    def make_scene(self, left_kind, right_kind):
        from uipc.core import Scene
        from app.usd import _abd_geometry, _fem_geometry
        config = Scene.default_config()
        config['dt'] = .001
        config['gravity'] = [[0.], [0.], [-9.81]]
        config['contact']['enable'] = 0
        config['newton']['semi_implicit']['enable'] = 0
        scene = Scene(config)
        slots = []
        for index, kind in enumerate((left_kind, right_kind)):
            vertices = VERTICES.copy()
            if index:
                vertices[3, 2] *= -1
            geometry = (_fem_geometry(vertices, np.array([[0, 1, 2, 3]]), 5e5, .2, 500., fixed=index == 0)
                        if kind == 'fem' else _abd_geometry(vertices, .1, fixed=index == 0))
            slots.append(scene.objects().create(str(index)).geometries().create(geometry)[0])
        return scene, slots

    def test_rejections_do_not_append_anchors(self):
        from app.attachments import fixed_joint, stitch_points, stitch_vertices
        scene, (left, right) = self.make_scene('fem', 'abd')
        count = right.geometry().vertices().size()
        invalid = [
            lambda: stitch_vertices(scene, 'bad', left, right, [[999, 0]], 1e3),
            lambda: stitch_points(scene, 'bad', left, right, [[.01, .01, .01]], [[0, 0, 0]], 1e3),
            lambda: stitch_points(scene, 'bad', left, right, [[0, 0, 0]], [[0, 0, 0]], -1),
            lambda: fixed_joint(scene, 'bad', left, right),
        ]
        for operation in invalid:
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(right.geometry().vertices().size(), count)
        _, errors = stitch_points(scene, 'approx', left, right, [[.001, 0, 0]], [[.001, 0, 0]],
                                   1e3, projection_tolerance_m=.002)
        self.assertAlmostEqual(errors[0][0], .001)
        self.assertEqual(right.geometry().vertices().size(), count+1)
        from app.usd import _fem_geometry
        geometry = _fem_geometry(np.vstack([VERTICES, VERTICES]),
            np.array([[0, 1, 2, 3], [4, 5, 6, 7]]), 5e5, .2, 500.)
        duplicate = scene.objects().create('coincident_nodes').geometries().create(geometry)[0]
        slot, _ = stitch_points(scene, 'explicit_node', duplicate, right,
                                 VERTICES[:1], VERTICES[:1], 1e3, vertex_ids0=[4])
        import uipc
        pairs = np.asarray(uipc.view(slot.geometry().instances().find('topo'))).reshape(-1, 2)
        self.assertEqual(pairs[0, 0], 4)

    def test_connected_and_detached_controls(self):
        import uipc
        from uipc.core import Engine, World
        from app.attachments import fixed_joint, stitch_points
        uipc.Logger.set_level(uipc.Logger.Level.Warn)
        cases = [(a, b, 'stitch') for a in ('fem', 'abd') for b in ('fem', 'abd')]
        cases += [('abd', 'abd', 'joint'), ('fem', 'fem', 'detached'), ('abd', 'abd', 'detached')]
        for left_kind, right_kind, mode in cases:
            with self.subTest(left=left_kind, right=right_kind, mode=mode), tempfile.TemporaryDirectory() as temporary:
                scene, (left, right) = self.make_scene(left_kind, right_kind)
                if mode == 'joint':
                    fixed_joint(scene, 'seam', left, right, strength_ratio=1e8)
                elif mode == 'stitch':
                    stitch_points(scene, 'seam', left, right, VERTICES[:3], VERTICES[:3], 1e5)
                engine = Engine('cuda', temporary)
                world = World(engine)
                world.init(scene)
                self.assertTrue(world.is_valid())
                for _ in range(100):
                    world.advance()
                    world.retrieve()
                    self.assertTrue(world.is_valid())
                geometry = right.geometry()
                if right_kind == 'fem':
                    final = np.asarray(uipc.view(geometry.positions())).reshape(-1, 3)[:3]
                else:
                    transform = np.asarray(uipc.view(geometry.transforms()))[0]
                    local = np.asarray(uipc.view(geometry.positions())).reshape(-1, 3)[:3]
                    final = local @ transform[:3, :3].T + transform[:3, 3]
                error = np.max(np.linalg.norm(final-VERTICES[:3], axis=1))
                self.assertTrue(np.isfinite(error))
                self.assertGreater(error, .03) if mode == 'detached' else self.assertLess(error, .002)
                del world, engine

    def test_usd_units_axis_and_force_balance(self):
        from pxr import Sdf, Usd, UsdGeom, UsdShade
        from app.engine import GenesisEngine
        from app.config import Setup
        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary)/'fixture.usda'
            stage = Usd.Stage.CreateNew(str(filename))
            UsdGeom.SetStageMetersPerUnit(stage, .01)
            UsdGeom.SetStageUpAxis(stage, 'Y')
            mesh = UsdGeom.Mesh.Define(stage, '/renamed/soft')
            # Centimeter Y-up -> meter Z-up; starts one meter above ground.
            points = [(0, 100, 0), (5, 100, 0), (0, 100, -5), (0, 105, 0)]
            mesh.CreatePointsAttr(points)
            mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
            mesh.CreateFaceVertexIndicesAttr([0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3])
            prim = mesh.GetPrim()
            prim.CreateAttribute('physxDeformable:simulationRestPoints', Sdf.ValueTypeNames.Point3fArray).Set(points)
            prim.CreateAttribute('physxDeformable:simulationIndices', Sdf.ValueTypeNames.IntArray).Set([0, 1, 2, 3])
            material = UsdShade.Material.Define(stage, '/material').GetPrim()
            for name, value in [('youngsModulus', 5e5), ('poissonsRatio', .2), ('density', 500.)]:
                material.CreateAttribute('physxDeformableBodyMaterial:'+name, Sdf.ValueTypeNames.Float).Set(value)
            prim.CreateRelationship('material:binding:physics').SetTargets(['/material'])
            UsdShade.MaterialBindingAPI.Apply(prim)
            stage.GetRootLayer().Save()
            import genesis as gs
            setup = Setup(mesh=str(filename), dt=.001, body_type='volume', contact_mode='none',
                          material={'density_kg_m3': 1000.})
            self.assertEqual(setup.model_dump()['material'], {'density_kg_m3': 1000.})
            sim = GenesisEngine(setup, output=Path(temporary)/'engine',
                ipc_options=gs.options.IPCCouplerOptions(contact_enable=False,
                    newton_semi_implicit_enable=False))
            self.assertFalse(hasattr(sim, 'scene'))
            self.assertTrue(sim._world.is_valid())
            self.assertEqual(sim._usd.stage.GetPrimAtPath('/material').GetAttribute(
                'physxDeformableBodyMaterial:density').Get(), 500.)
            initial = sim.get_simulation_positions('/renamed/soft')
            np.testing.assert_allclose(initial, VERTICES+[0, 0, 1], atol=1e-8)
            # Equal upward nodal forces balance the tetrahedron's gravity.
            weight = (.05**3/6)*1000*9.81
            sim.set_forces([{'vertex': i, 'vector': [0, 0, weight/4]} for i in range(4)], body='/renamed/soft')
            with self.assertRaises(ValueError):
                sim.set_forces([{'vertex': 999, 'vector': [0, 0, 1]}], body='/renamed/soft')
            for _ in range(100):
                sim.step()
            np.testing.assert_allclose(sim.get_simulation_positions('/renamed/soft'), initial, atol=2e-5)
            sim.set_forces([], body='/renamed/soft')
            for _ in range(100):
                sim.step()
            self.assertLess((sim.get_simulation_positions('/renamed/soft')-initial)[:, 2].mean(), -.03)
            sim.close()
            del sim

    def test_paperbag_stage_and_connections(self):
        from app.engine import GenesisEngine
        from app.config import Setup
        with tempfile.TemporaryDirectory() as temporary:
            from examples.paperbag import create_engine
            sim = create_engine(ROOT/'examples/paperbag/PaperBag.usd', ROOT/'examples/paperbag.json', temporary)
            self.assertFalse(hasattr(sim, 'scene'))
            self.assertTrue(sim._world.is_valid())
            self.assertEqual(len(sim._usd.slots), 3)
            self.assertEqual(len(sim._usd.deformable_paths), 2)
            self.assertEqual(len(sim._usd.genesis_rigid), 1)
            self.assertTrue(any('vmesh' in g for g in sim._usd.genesis_rigid[0][2][0]))
            self.assertEqual(len(sim._usd.attachment_regions), 2)
            self.assertTrue(all(r['pair_count']>0 for r in sim._usd.attachment_regions))
            self.assertTrue(all(abs(len(r['vertex_ids'])*r['stiffness_n_m']-3000) < 1e-8
                                for r in sim._usd.attachment_regions))
            self.assertEqual([len(ids) for ids in sim.drive_nodes.values()], [82, 81])
            for path in sim._usd.slots:
                self.assertTrue(sim._usd.stage.GetPrimAtPath(path).IsValid())
            sim.step()
            sim.close()
            del sim

    def test_genesis_primitive_material_and_fixed_joint(self):
        from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
        from app.engine import GenesisEngine
        from app.config import Setup
        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary)/'cubes.usda'
            stage = Usd.Stage.CreateNew(str(filename))
            UsdGeom.SetStageMetersPerUnit(stage, 1.)
            UsdGeom.SetStageUpAxis(stage, 'Z')
            material = UsdShade.Material.Define(stage, '/red')
            shader = UsdShade.Shader.Define(stage, '/red/surface')
            shader.CreateIdAttr('UsdPreviewSurface')
            shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set((.8, .1, .2))
            shader.CreateOutput('surface', Sdf.ValueTypeNames.Token)
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
            for i, path in enumerate(('/a', '/b')):
                cube = UsdGeom.Cube.Define(stage, path)
                cube.CreateSizeAttr(.05)
                cube.AddTranslateOp().Set((.1*i, 0., 1.))
                body = UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
                if i == 0:
                    body.CreateKinematicEnabledAttr(True)
                UsdPhysics.MassAPI.Apply(cube.GetPrim()).CreateMassAttr(.1)
                UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)
            joint = UsdPhysics.FixedJoint.Define(stage, '/seam')
            joint.CreateBody0Rel().SetTargets(['/a'])
            joint.CreateBody1Rel().SetTargets(['/b'])
            joint.CreateLocalPos0Attr((.05, 0., 0.))
            joint.CreateLocalPos1Attr((-.05, 0., 0.))
            stage.GetRootLayer().Save()
            sim = GenesisEngine(Setup(mesh=str(filename), dt=.001, body_type='volume', contact_mode='none'),
                                output=Path(temporary)/'engine')
            self.assertEqual(len(sim._usd.slots), 2)
            visual = next(g['vmesh'] for g in sim._usd.genesis_rigid[0][2][0] if 'vmesh' in g)
            np.testing.assert_allclose(visual.surface.texture.color[:3], [.8, .1, .2], atol=1e-6)
            initial = sim.get_simulation_positions('/b')
            for _ in range(100):
                sim.step()
            np.testing.assert_allclose(sim.get_simulation_positions('/b'), initial, atol=2e-5)
            sim.close()
            del sim


if __name__ == '__main__':
    unittest.main()
