"""Regression checks for automatic MAS fallback and Genesis stepping."""
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
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

    def test_automatic_mas_and_clean_fallback(self):
        import numpy as np
        from app.engine import GenesisEngine
        class Vertices:
            def __init__(self):self.attrs={}
            def find(self,name):return self.attrs.get(name)
            def destroy(self,name):del self.attrs[name]
        for mode in ('supported','missing_partition','old_backend','all_fixed','partition_failure','missing_attribute'):
            with self.subTest(mode=mode):
                e=GenesisEngine.__new__(GenesisEngine)
                e.fem_preconditioner='mas';e.fem_preconditioner_fallback_reason=None
                e._positions=np.zeros((3,3));e.surface_ids=np.arange(3)
                e.config=SimpleNamespace(pins=[0,1,2] if mode=='all_fixed' else [0])
                attrs=[Vertices(),Vertices()]
                geometries=[SimpleNamespace(vertices=lambda v=v:v) for v in attrs]
                calls=[]
                def partition(g,n):
                    calls.append(g)
                    if mode=='missing_attribute':return
                    g.vertices().attrs['mesh_part']=np.zeros(3,dtype=int)
                    if mode=='partition_failure' and len(calls)==2:raise RuntimeError('test failure')
                fake=SimpleNamespace(geometry=SimpleNamespace(mesh_partition=None if mode=='missing_partition' else partition),view=lambda a:a)
                with patch.dict('sys.modules',{'uipc':fake}),patch('app.plasticity.mas_supports_cuda_graph',return_value=mode!='old_backend'),patch('app.engine.gs.logger',Mock()):
                    e._configure_mas(geometries)
                self.assertEqual(e.fem_preconditioner,'mas' if mode=='supported' else 'diag')
                if mode=='supported':
                    for v in attrs:np.testing.assert_array_equal(v.find('mesh_part'),[-1,0,0])
                    self.assertIsNone(e.fem_preconditioner_fallback_reason)
                else:
                    self.assertTrue(e.fem_preconditioner_fallback_reason)
                    self.assertTrue(all(v.find('mesh_part') is None for v in attrs))

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
        from app.plasticity import mas_supports_cuda_graph
        import uipc
        from genesis.engine.couplers.ipc_coupler import coupler
        self.assertTrue(mas_supports_cuda_graph(),'Test requires the MAS stream fix')
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
                    parts=np.asarray(uipc.view(self.geometry(engine).vertices().find('mesh_part'))).reshape(-1)
                    self.assertTrue(np.any(parts>=0))
                    np.testing.assert_array_equal(parts[engine.surface_ids[config.pins]],[-1,-1])
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

    @unittest.skipUnless(os.environ.get('IPC_CONTACT_GPU_TEST')=='1','Requires CUDA and the project native backend')
    def test_unsupported_mas_automatically_runs_diag(self):
        import numpy as np
        import uipc
        from app.config import Setup
        from app.engine import GenesisEngine
        original_partition=uipc.geometry.mesh_partition
        for body in ('shell','volume'):
            for failure in ('old_backend','missing_partition','partial_partition'):
                with self.subTest(body=body,failure=failure):
                    config=Setup.model_validate_json((Path(__file__).resolve().parents[1]/f'benchmarks/{body}_1k.json').read_text())
                    config.pins=[0,1];config.step_mode='genesis_world';calls=[]
                    def partial(geometry,n):
                        original_partition(geometry,n);calls.append(geometry)
                        if len(calls)==2:raise RuntimeError('Injected failure on rest geometry')
                    if failure=='old_backend':context=patch('app.plasticity.mas_supports_cuda_graph',return_value=False)
                    elif failure=='missing_partition':context=patch.object(uipc.geometry,'mesh_partition',None)
                    else:context=patch.object(uipc.geometry,'mesh_partition',partial)
                    with context:engine=GenesisEngine(config)
                    try:
                        self.assertEqual(engine.fem_preconditioner,'diag')
                        self.assertTrue(engine.fem_preconditioner_fallback_reason)
                        c=engine.scene.sim.coupler;name=('cloth' if body=='shell' else 'fem')+'_0_0'
                        obj=c._ipc_objects.find(name)[0]
                        for slot in c._ipc_scene.geometries().find(obj.geometries().ids()[0]):
                            self.assertIsNone(slot.geometry().vertices().find('mesh_part'))
                        initial=engine.x.copy()
                        for _ in range(3):engine.step()
                        np.testing.assert_array_equal(engine.x[config.pins],initial[config.pins])
                        np.testing.assert_array_equal(engine.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3),engine.get_simulation_positions())
                        self.assertTrue(np.isfinite(engine.x).all())
                    finally:engine.close()


if __name__=='__main__':unittest.main()
