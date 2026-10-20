"""Shell-only configuration, UI and native point-force plasticity regressions."""
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from pydantic import ValidationError
from app.config import Setup,Material

class ShellPlasticityTests(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        old=Setup.model_validate({'material':{'youngs_modulus_pa':5000}})
        self.assertFalse(old.material.bending_plasticity)
        new=Setup(material=Material(bending_plasticity=True,bending_yield_angle_deg=15,bending_plastic_hardening=.2))
        self.assertEqual(Setup.model_validate_json(new.model_dump_json()),new)

    def test_thresholds(self):
        self.assertEqual(Material(bending_yield_angle_deg=0).bending_yield_angle_deg,0)
        for field,values in {'bending_yield_angle_deg':[-1,181,float('nan')],
                'bending_plastic_hardening':[-1,float('inf')]}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValidationError):Material(**{field:value})

    def test_example_configs(self):
        folder=Path(__file__).resolve().parents[1]/'benchmarks/shell_plasticity'
        plastic=Setup.model_validate_json((folder/'plastic.json').read_text())
        elastic=Setup.model_validate_json((folder/'elastic.json').read_text())
        self.assertTrue(plastic.material.bending_plasticity)
        self.assertFalse(elastic.material.bending_plasticity)
        elastic.material.bending_plasticity=True
        self.assertEqual(plastic,elastic)
        self.assertEqual(plastic.gravity,(0,0,0))
        self.assertEqual(plastic.pins,[0,1,2])
        self.assertEqual(plastic.forces[0].vector,(0,0,.5))

    @unittest.skipUnless(shutil.which('node'),'Node.js needed for the form test')
    def test_form(self):
        spec=dict(material_schema=Material.model_json_schema(),config=Setup().model_dump(mode='json'),
            models=dict(shell='BW',volume='SNH',bending_model=[dict(value='DiscreteShellBending',label='DSB')]),
            mesh=dict(file='model.obj',vertices=4,triangles=2),genesis_version='1.4.1')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'schema.json';path.write_text(json.dumps(spec))
            subprocess.run([shutil.which('node'),'tests/test_shell_material_form.cjs',str(path)],
                cwd=Path(__file__).resolve().parents[1],check=True,capture_output=True,text=True)

    @unittest.skipUnless(os.environ.get('IPC_GPU_TEST')=='1','Set IPC_GPU_TEST=1 for native CUDA tests')
    def test_point_force_release(self):
        from benchmarks.shell_plasticity import run_case
        values={}
        for name,plastic,y,h in [('elastic',False,10,0),('plastic',True,10,0),('below',True,90,0),('hardening',True,10,1)]:
            result,_,_=run_case(plastic,y,h)
            print(name,result,flush=True)
            self.assertTrue(result['finite'])
            self.assertLess(result['max_fixed_error_m'],1e-10)
            self.assertLess(result['final_30_range_deg'],.1)
            values[name]=result['final_angle_deg']
        self.assertLess(abs(values['elastic']),.01)
        self.assertLess(abs(values['below']),.01)
        self.assertGreater(values['plastic'],15)
        self.assertGreater(values['hardening'],0)
        self.assertLess(values['hardening'],values['plastic'])

if __name__=='__main__':unittest.main()
