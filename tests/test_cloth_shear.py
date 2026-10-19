"""Regression checks for the independent BW shear input and its native mapping."""
import json,os,unittest
from pathlib import Path
import numpy as np
from pydantic import ValidationError
from app.config import Material,Setup
from app.engine import GenesisEngine
import uipc
from uipc.geometry import trimesh
from uipc.constitution import StrainLimitingBaraffWitkinShell,ElasticModuli2D

class ClothShearTests(unittest.TestCase):
    def test_old_json_gets_explicit_new_default_and_roundtrips(self):
        payload=json.loads((Path(__file__).resolve().parent.parent/'benchmarks/shell_1k.json').read_text())
        payload['material'].pop('shear_stiffness_n_m',None)
        config=Setup.model_validate(payload)
        self.assertEqual(config.material.shear_stiffness_n_m,8.)
        self.assertEqual(Setup.model_validate_json(config.model_dump_json()),config)

    def test_invalid_shear_values_are_rejected(self):
        for value in [0,-1,float('nan'),float('inf'),None]:
            with self.subTest(value=value),self.assertRaises(ValidationError):
                Material(shear_stiffness_n_m=value)

    def test_native_override_only_changes_shear(self):
        for E,t,shear in [(100000.,.00025,8.),(200000.,.0005,3.5)]:
            with self.subTest(E=E,t=t,shear=shear):
                m=Material(youngs_modulus_pa=E,thickness_m=t,shear_stiffness_n_m=shear)
                geo=trimesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),np.array([[0,1,2]],dtype=np.int32))
                StrainLimitingBaraffWitkinShell().apply_to(geo,moduli=ElasticModuli2D.youngs_poisson(E,.25),mass_density=m.density_kg_m3,thickness=t/2)
                before={k:np.array(uipc.view(geo.triangles().find(k)),copy=True) for k in ['lambda','strain_rate']}
                engine=GenesisEngine.__new__(GenesisEngine);engine.config=Setup(material=m)
                engine._configure_shell_material(geo)
                self.assertEqual(float(uipc.view(geo.triangles().find('mu')).reshape(-1)[0]),shear)
                for k,value in before.items():np.testing.assert_array_equal(uipc.view(geo.triangles().find(k)),value)
                self.assertAlmostEqual(float(uipc.view(geo.triangles().find('lambda')).reshape(-1)[0]),E*t/(1-.25**2))
                self.assertEqual(float(uipc.view(geo.triangles().find('strain_rate')).reshape(-1)[0]),100.)

    def test_changed_native_constitution_fails_clearly(self):
        geo=trimesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),np.array([[0,1,2]],dtype=np.int32))
        engine=GenesisEngine.__new__(GenesisEngine);engine.config=Setup()
        with self.assertRaisesRegex(RuntimeError,'原生薄壳缺少 mu 属性'):engine._configure_shell_material(geo)

    @unittest.skipUnless(os.environ.get('IPC_GPU_TEST')=='1','Set IPC_GPU_TEST=1 for the CUDA integration check')
    def test_real_engine_build_and_step(self):
        payload=json.loads((Path(__file__).resolve().parent.parent/'benchmarks/shell_1k.json').read_text())
        payload['gravity']=[0,0,0]
        for shear in [8.,80.]:
            with self.subTest(shear=shear):
                payload['material']['shear_stiffness_n_m']=shear
                engine=GenesisEngine(Setup.model_validate(payload))
                try:
                    c=engine.scene.sim.coupler;body=c._ipc_objects.find('cloth_0_0')[0]
                    for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0]):
                        triangles=slot.geometry().triangles()
                        np.testing.assert_array_equal(uipc.view(triangles.find('mu')),shear)
                        np.testing.assert_allclose(uipc.view(triangles.find('lambda')),100000*.00025/(1-.25**2))
                        np.testing.assert_array_equal(uipc.view(triangles.find('strain_rate')),100.)
                    state=engine.step()
                    self.assertEqual(state['frame'],1)
                    self.assertTrue(np.isfinite(state['positions']).all())
                finally:engine.close()

if __name__=='__main__':unittest.main()
