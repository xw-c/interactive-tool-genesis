"""Regression checks for native MAS selection and Genesis stepping."""
import os
from pathlib import Path
import unittest
from unittest.mock import Mock,patch


class SolverOptimizationTests(unittest.TestCase):
    def test_direct_step_accepts_only_valid_finite_states(self):
        import numpy as np
        from app.config import Setup
        from app.engine import GenesisEngine
        for failure in ('none','invalid_before','invalid_after','exception','nonfinite'):
            with self.subTest(failure=failure):
                e=GenesisEngine.__new__(GenesisEngine)
                e.config=Setup(step_mode='libuipc');e._failed=False;e.frame=0;e.time=0.;e.step_ms=0.
                e.surface_ids=np.array([0]);e.x=np.zeros((1,3));e._positions=e.x.copy()
                world=Mock();world.is_valid.side_effect=[failure!='invalid_before',failure!='invalid_after']
                e.scene=Mock();e.scene.sim.coupler._ipc_world=world
                if failure=='exception':world.advance.side_effect=RuntimeError('failure')
                e._read_positions=Mock(return_value=np.full((1,3),float('nan') if failure=='nonfinite' else 1.))
                if failure=='none':
                    e.step();self.assertEqual(e.frame,1);self.assertAlmostEqual(e.time,e.config.dt)
                    world.advance.assert_called_once();world.retrieve.assert_called_once()
                    np.testing.assert_array_equal(e.get_simulation_positions(),np.ones((1,3)))
                else:
                    with self.assertRaises(RuntimeError):e.step()
                    self.assertEqual(e.frame,0);self.assertEqual(e.time,0.)
                    np.testing.assert_array_equal(e.get_simulation_positions(),np.zeros((1,3)))
                    calls=world.advance.call_count
                    with self.assertRaisesRegex(RuntimeError,'重置'):e.step()
                    self.assertEqual(world.advance.call_count,calls)
                e.scene.step.assert_not_called()

    @unittest.skipUnless(os.environ.get('IPC_CONTACT_GPU_TEST')=='1','Requires CUDA and the project native backend')
    def test_explicit_ipc_options_reach_native_scene(self):
        import numpy as np
        import genesis as gs
        import uipc
        from app.config import Setup
        from app.engine import GenesisEngine
        config=Setup(mesh='benchmarks/shell_1k.obj',contact_mode='none',step_mode='genesis_world',
            dt=.005,gravity=(0,0,-2))
        for variant in ('default','baseline','hessian','newton5','combined'):
            with self.subTest(variant=variant):
                accelerated=variant in ('hessian','combined')
                limited=variant in ('newton5','combined')
                options=None if variant=='default' else gs.options.IPCCouplerOptions(
                    is_acceleration_enabled=accelerated,newton_max_iterations=5 if limited else None,
                    is_strict=False,contact_enable=False,contact_friction_enable=False,contact_d_hat=.0007)
                engine=GenesisEngine(config,ipc_options=options)
                try:
                    native=engine.scene.sim.coupler._ipc_scene.config()
                    expected={'fem/bending/pseudo':int(accelerated),'contact/friction/pseudo':int(accelerated),
                        'newton/max_iter':5 if limited else 1024,'contact/enable':0,
                        'contact/d_hat':config.contact_d_hat_m if options is None else options.contact_d_hat}
                    for key,value in expected.items():
                        self.assertAlmostEqual(float(uipc.view(native.find(key))[0]),value,msg=key)
                    initial=engine.get_simulation_positions()
                    engine.step(snapshot=False)
                    positions=engine.get_simulation_positions()
                    self.assertTrue(np.isfinite(positions).all())
                    self.assertTrue(np.all(positions[:,2]<initial[:,2]))
                    self.assertEqual(engine.frame,1)
                finally:engine.close()

    @staticmethod
    def geometry(engine):
        c=engine.scene.sim.coupler
        name=('cloth' if engine.config.body_type=='shell' else 'fem')+'_0_0'
        body=c._ipc_objects.find(name)[0]
        return list(c._ipc_scene.geometries().find(body.geometries().ids()[0]))[0].geometry()

    def test_full_state_is_returned_as_an_independent_copy(self):
        import numpy as np
        from app.engine import GenesisEngine
        engine=GenesisEngine.__new__(GenesisEngine)
        engine._positions=np.array([[1.,2.,3.],[4.,5.,6.]])
        result=engine.get_simulation_positions()
        result[:]=0
        np.testing.assert_array_equal(engine._positions,[[1.,2.,3.],[4.,5.,6.]])

    @unittest.skipUnless(os.environ.get('IPC_CONTACT_GPU_TEST')=='1','Requires CUDA and the project native backend')
    def test_mas_pins_and_graph_match_uncaptured_execution(self):
        import numpy as np
        from app.config import Setup
        from app.engine import GenesisEngine
        import uipc
        from genesis.engine.couplers.ipc_coupler import coupler
        for body in ('shell','volume'):
            with self.subTest(body=body):
                config=Setup.model_validate_json((Path(__file__).resolve().parents[1]/f'benchmarks/{body}_1k.json').read_text())
                config.pins=[0,1];config.step_mode='genesis_world'
                engine=GenesisEngine(config)
                try:
                    self.assertEqual(engine.fem_preconditioner,'mas')
                    graph=engine.scene.sim.coupler._ipc_scene.config().find('linear_system/use_cuda_graph')
                    self.assertEqual(int(uipc.view(graph)[0]),1)
                    initial=engine.x.copy()
                    native=engine.scene.sim.coupler._ipc_scene.config()
                    self.assertEqual(uipc.view(native.find('linear_system/fem_preconditioner'))[0],'mas')
                    self.assertIsNone(self.geometry(engine).vertices().find('mesh_part'))
                    for _ in range(5):engine.step()
                    graph_result=engine.get_simulation_positions()
                    np.testing.assert_array_equal(engine.x[config.pins],initial[config.pins])
                    self.assertTrue(np.isfinite(graph_result).all())
                    self.assertGreater(np.linalg.norm(engine.x-initial),0)
                    np.testing.assert_array_equal(engine.x,graph_result[engine.surface_ids])
                    self.assertEqual(engine.state()['positions'],engine.x.tolist())
                    np.testing.assert_array_equal(engine.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3),graph_result)
                    self.assertEqual(engine.scene.sim.cur_step_global,engine.frame)
                finally:engine.close()
                original=coupler.IPCCoupler._init_ipc
                def no_graph(c):
                    original(c)
                    uipc.view(c._ipc_scene.config().find('linear_system/use_cuda_graph'))[:]=0
                with patch.object(coupler.IPCCoupler,'_init_ipc',no_graph):
                    engine=GenesisEngine(config)
                try:
                    for _ in range(5):engine.step()
                    np.testing.assert_allclose(engine.get_simulation_positions(),graph_result,rtol=0,atol=5e-6)
                    np.testing.assert_array_equal(engine.x[config.pins],initial[config.pins])
                finally:engine.close()


if __name__=='__main__':unittest.main()
