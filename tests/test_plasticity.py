"""Regression tests for plasticity configuration and native shell loading/unloading."""
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from pydantic import ValidationError
from app.config import Setup,Material

class PlasticityTests(unittest.TestCase):
    def test_opt_in_and_round_trip(self):
        old=Setup.model_validate({'material':{'youngs_modulus_pa':5000}})
        self.assertFalse(old.material.bending_plasticity)
        self.assertFalse(old.material.volume_plasticity)
        new=Setup(material=Material(bending_plasticity=True,bending_yield_angle_deg=15,
            volume_plasticity=True,volume_yield_strain_percent=8,volume_plastic_hardening=.2))
        self.assertEqual(Setup.model_validate_json(new.model_dump_json()),new)

    def test_zero_yield_is_allowed(self):
        material=Material(bending_yield_angle_deg=0,volume_yield_strain_percent=0)
        self.assertEqual(material.bending_yield_angle_deg,0)
        self.assertEqual(material.volume_yield_strain_percent,0)

    @unittest.skipUnless(shutil.which('node'),'Node.js is needed for the material form test')
    def test_material_form(self):
        spec=dict(material_schema=Material.model_json_schema(),config=Setup().model_dump(mode='json'),
            models=dict(shell='BW',volume='SNH',bending_model=[dict(value='DiscreteShellBending',label='DSB')]),
            mesh=dict(file='model.obj',vertices=4,triangles=2),genesis_version='1.4.1')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'schema.json';path.write_text(json.dumps(spec),encoding='utf-8')
            subprocess.run([shutil.which('node'),'tests/test_material_form.cjs',str(path)],
                cwd=Path(__file__).resolve().parents[1],check=True,capture_output=True,text=True)

    def test_invalid_thresholds_and_hardening(self):
        for field,values in {'bending_yield_angle_deg':[-1,181,float('nan')],
                'volume_yield_strain_percent':[-1,float('inf')],
                'bending_plastic_hardening':[-1,float('nan')],
                'volume_plastic_hardening':[-1,float('inf')]}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValidationError):Material(**{field:value})

    def test_volume_reference_parameters_convert_to_native_stress(self):
        import numpy as np
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.plasticity import configure_volume
        attrs={'mu':np.array([2000.,3000.])}
        class Geometry:
            def tetrahedra(self):return self
            def find(self,name):return attrs.get(name)
            def create(self,name,value):
                attrs[name]=np.full(2,value);return attrs[name]
        with patch.dict('sys.modules',{'uipc':SimpleNamespace(view=lambda attr:attr)}):
            configure_volume(Geometry(),Material(volume_yield_strain_percent=10,volume_plastic_hardening=.2))
        np.testing.assert_allclose(attrs['plastic_yield_stress'],[600,900])
        np.testing.assert_allclose(attrs['plastic_hardening'],[1200,1800])
        self.assertEqual(attrs['plasticity_enabled'].dtype.kind,'i')

    def test_failed_steps_stop_without_advancing_public_state(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import numpy as np
        from app.engine import GenesisEngine
        for mode in ('invalid_before','invalid_after','exception','nonfinite','success'):
            with self.subTest(mode=mode):
                engine=GenesisEngine.__new__(GenesisEngine)
                engine._failed=False;engine.frame=4;engine.time=.04;engine.step_ms=1.
                engine.config=SimpleNamespace(dt=.01);engine.surface_ids=np.array([0])
                engine.x=np.array([[1.,2.,3.]])
                world=Mock();world.is_valid.side_effect=[mode!='invalid_before',mode!='invalid_after']
                scene=Mock();scene.sim.coupler._ipc_world=world;engine.scene=scene
                if mode=='exception':scene.step.side_effect=RuntimeError('solver failure')
                engine.entity=Mock()
                engine.entity.get_state.return_value.pos.detach.return_value.cpu.return_value.numpy.return_value=np.array([[float('nan') if mode=='nonfinite' else 2.,3.,4.]])
                engine.state=Mock(return_value='accepted')
                if mode=='success':
                    self.assertEqual(engine.step(),'accepted');self.assertEqual(engine.frame,5)
                    self.assertAlmostEqual(engine.time,.05);self.assertFalse(engine._failed)
                else:
                    with self.assertRaises(RuntimeError):engine.step()
                    self.assertEqual(engine.frame,4);self.assertEqual(engine.time,.04)
                    np.testing.assert_array_equal(engine.x,[[1.,2.,3.]])
                    calls=scene.step.call_count
                    with self.assertRaisesRegex(RuntimeError,'重置'):engine.step()
                    self.assertEqual(scene.step.call_count,calls)

    @unittest.skipUnless(os.environ.get('IPC_VOLUME_GPU_TEST')=='1','Requires the project volume-plasticity backend')
    def test_native_failure_requires_reset(self):
        from unittest.mock import patch
        import benchmarks.plasticity as experiment
        from genesis.engine.couplers.ipc_coupler import coupler
        from app.engine import GenesisEngine
        for key in ('newton/max_iter','line_search/max_iter'):
            engines=[]
            def failing_engine(config):
                original=coupler.build_ipc_scene_config
                def limited(*args,**kwargs):
                    cfg=original(*args,**kwargs)
                    cfg['extras']['strict_mode']['enable']=0
                    cfg[key.split('/')[0]]['max_iter']=0
                    return cfg
                with patch.object(coupler,'build_ipc_scene_config',limited):
                    engine=GenesisEngine(config)
                engines.append(engine)
                return engine
            with self.subTest(limit=key),patch.object(experiment,'GenesisEngine',failing_engine):
                with self.assertRaisesRegex(RuntimeError,'IPC .*重置'):
                    experiment.run_volume(True,steps=1)
                engine=engines[0]
                self.assertEqual(engine.frame,0);self.assertEqual(engine.time,0.)
                self.assertTrue(engine._failed)
                with self.assertRaisesRegex(RuntimeError,'重置'):engine.step()

    @unittest.skipUnless(os.environ.get('IPC_GPU_TEST')=='1','Set IPC_GPU_TEST=1 for CUDA load/unload experiments')
    def test_shell_residual_and_elastic_control(self):
        from benchmarks.plasticity import run_hinge
        values={}
        for name,plastic,y,h in [('elastic',False,10,0),('plastic',True,10,0),('below',True,90,0),('hardening',True,10,1)]:
            r,_,_=run_hinge(plastic,y,h,steps=160)
            self.assertTrue(r['finite']);self.assertLess(r['final_30_range_deg'],.1)
            values[name]=r['final_angle_deg']
        self.assertLess(abs(values['elastic']),.01)
        self.assertLess(abs(values['below']),.01)
        self.assertGreater(values['plastic'],30)
        self.assertGreater(values['hardening'],5)
        self.assertLess(values['hardening'],values['plastic'])
        # Rebuilding an elastic scene after yielding must not inherit plastic state.
        r,_,_=run_hinge(False,steps=160)
        self.assertLess(abs(r['final_angle_deg']),.01)

    @unittest.skipUnless(os.environ.get('IPC_VOLUME_GPU_TEST')=='1','Requires the project volume-plasticity backend')
    def test_volume_residual_and_elastic_control(self):
        from benchmarks.plasticity import run_volume
        values={}
        for name,plastic,y,h in [('elastic',False,10,0),('plastic',True,10,0),('below',True,90,0),('hardening',True,10,1)]:
            r,_,_=run_volume(plastic,y,h,steps=160)
            self.assertTrue(r['finite']);self.assertGreater(r['min_j'],0)
            self.assertLess(r['final_30_range'],.002)
            values[name]=r['final_stretches'][0]
        self.assertAlmostEqual(values['elastic'],1.,delta=.002)
        self.assertAlmostEqual(values['below'],1.,delta=.002)
        self.assertGreater(values['plastic'],1.05)
        self.assertGreater(abs(values['hardening']-values['plastic']),.01)

if __name__=='__main__':unittest.main()
