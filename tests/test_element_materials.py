"""Element numbering, material inheritance, mass assembly and live assignment."""
import os
from pathlib import Path
import unittest
import numpy as np
from pydantic import ValidationError
from app.config import Material, Setup
from app.materials import resolve_materials, element_material_ids, configure_elements


class ElementMaterialTests(unittest.TestCase):
    def test_sparse_overrides_survive_json_and_reinherit_defaults(self):
        c=Setup(material=Material(poisson_ratio=.4,density_kg_m3=800),material_regions=[
            dict(elements=[0,2],material=dict(youngs_modulus_pa=300000,bending_model=None))])
        self.assertEqual(Setup.model_validate_json(c.model_dump_json()),c)
        self.assertEqual(c.model_dump()['material_regions'][0]['material'],dict(youngs_modulus_pa=300000.,bending_model=None))
        m=resolve_materials(c)[1]
        self.assertEqual((m.youngs_modulus_pa,m.poisson_ratio,m.density_kg_m3,m.bending_model),(300000,.4,800,None))
        c.material.density_kg_m3=900
        self.assertEqual(resolve_materials(c)[1].density_kg_m3,900)

    def test_invalid_regions_fail_instead_of_silently_overwriting(self):
        for material in ({'youngs_modulus_pa':-1},{'youngs_modulus_pa':None},{'self_friction':.2},{'unknown':1}):
            with self.subTest(material=material),self.assertRaises(ValidationError):
                Setup(material_regions=[dict(elements=[0],material=material)])
        for elements in ([],[-1],[True],[.5]):
            with self.subTest(elements=elements),self.assertRaises(ValidationError):
                Setup(material_regions=[dict(elements=elements,material={})])
        for groups in ([[0,0]],[[3]],[[0,1],[1,2]]):
            c=Setup(material_regions=[dict(elements=ids,material={}) for ids in groups])
            with self.subTest(groups=groups),self.assertRaises(ValueError):element_material_ids(c,3)

    def test_shell_coefficients_mass_and_shared_edge_material(self):
        import uipc
        from uipc.geometry import trimesh
        from uipc.constitution import StrainLimitingBaraffWitkinShell,ElasticModuli2D,StrainPlasticDiscreteShellBending
        x=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[1.,1.,0.]])
        faces=np.array([[0,1,2],[1,3,2]],dtype=np.int32)
        geo=trimesh(x,faces)
        base=Material(youngs_modulus_pa=100,poisson_ratio=.2,density_kg_m3=10,thickness_m=.1,bending_rigidity_nm=2)
        c=Setup(material=base,material_regions=[dict(elements=[1],material=dict(youngs_modulus_pa=400,
            density_kg_m3=20,thickness_m=.2,shear_stiffness_n_m=30,bending_rigidity_nm=6,bending_plasticity=True))])
        StrainLimitingBaraffWitkinShell().apply_to(geo,ElasticModuli2D.youngs_poisson(100,.2),mass_density=10,thickness=.05)
        configure_elements(geo,'shell',resolve_materials(c),element_material_ids(c,2),StrainPlasticDiscreteShellBending())
        read=lambda coll,key:np.asarray(uipc.view(coll.find(key))).reshape(-1)
        np.testing.assert_allclose(read(geo.triangles(),'lambda'),[100*.1/.96,400*.2/.96])
        np.testing.assert_allclose(read(geo.triangles(),'mu'),[8,30])
        np.testing.assert_allclose(read(geo.vertices(),'mass'),[.5/3,2.5/3,2.5/3,2/3])
        np.testing.assert_array_equal(read(geo.vertices(),'thickness'),np.full(4,.05))
        edges=np.asarray(geo.edges().topo().view()).reshape(-1,2)
        shared=np.flatnonzero(np.all(np.sort(edges,axis=1)==[1,2],axis=1))[0]
        self.assertAlmostEqual(read(geo.edges(),'bending_stiffness')[shared],4.)
        self.assertAlmostEqual(read(geo.edges(),'bending_yield_threshold')[shared],2*np.pi)

    def test_volume_mass_and_independent_elastic_plastic_tets(self):
        import uipc
        from uipc.geometry import tetmesh
        from uipc.constitution import StableNeoHookean,ElasticModuli
        x=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.],[0.,0.,-1.]])
        tets=np.array([[0,1,2,3],[0,2,1,4]],dtype=np.int32)
        geo=tetmesh(x,tets)
        c=Setup(body_type='volume',material=Material(youngs_modulus_pa=120,poisson_ratio=.25,density_kg_m3=12),
                material_regions=[dict(elements=[1],material=dict(youngs_modulus_pa=240,density_kg_m3=24,
                    volume_plasticity=True,volume_yield_strain_percent=5,volume_plastic_hardening=.2))])
        StableNeoHookean().apply_to(geo,ElasticModuli.youngs_poisson(120,.25),mass_density=12)
        configure_elements(geo,'volume',resolve_materials(c),element_material_ids(c,2))
        read=lambda coll,key:np.asarray(uipc.view(coll.find(key))).reshape(-1)
        np.testing.assert_allclose(read(geo.tetrahedra(),'mu'),[64,128])
        np.testing.assert_allclose(read(geo.tetrahedra(),'lambda'),[88,176])
        np.testing.assert_allclose(read(geo.vertices(),'mass'),[1.5,1.5,1.5,.5,1])
        np.testing.assert_array_equal(read(geo.tetrahedra(),'plasticity_enabled'),[0,1])
        np.testing.assert_allclose(read(geo.tetrahedra(),'plastic_yield_stress'),[19.2,19.2])
        np.testing.assert_allclose(read(geo.tetrahedra(),'plastic_hardening'),[0,76.8])

    @unittest.skipUnless(os.environ.get('IPC_MATERIAL_GPU_TEST')=='1','Requires CUDA and native plasticity backend')
    def test_density_override_changes_actual_inertial_response(self):
        from app.engine import GenesisEngine
        root=Path(__file__).resolve().parents[1]
        c=Setup.model_validate_json((root/'benchmarks/shell_1k.json').read_text())
        c.gravity=(0,0,0);c.contact_mode='none';c.material.bending_model=None
        e=GenesisEngine(c)
        try:
            x=e.x.copy();faces=e.faces.copy()
            area=np.linalg.norm(np.cross(x[faces[:,1]]-x[faces[:,0]],x[faces[:,2]]-x[faces[:,0]]),axis=1)/2
            mass=np.zeros(len(x))
            for j in range(3):np.add.at(mass,faces[:,j],area*c.material.thickness_m*c.material.density_kg_m3/3)
            forces=[dict(vertex=i,vector=[0,0,-100*m]) for i,m in enumerate(mass)]
            e.set_forces(forces);e.step()
            baseline=float(np.mean(x[:,2]-e.x[:,2]))
            self.assertAlmostEqual(baseline,100*c.dt**2,delta=1e-6)
        finally:e.close()
        data=c.model_dump(mode='json')
        data['material_regions']=[dict(elements=list(range(len(faces))),material=dict(density_kg_m3=2*c.material.density_kg_m3))]
        e=GenesisEngine(Setup.model_validate(data))
        try:
            e.step()
            displacement=float(np.mean(x[:,2]-e.x[:,2]))
            self.assertAlmostEqual(displacement,baseline/2,delta=1e-6)
        finally:e.close()

    @unittest.skipUnless(os.environ.get('IPC_MATERIAL_GPU_TEST')=='1','Requires CUDA and native plasticity backend')
    def test_configure_assigns_steps_and_clears_regions(self):
        import uipc
        from app.interface import Simulation
        root=Path(__file__).resolve().parents[1]
        for body in ('shell','volume'):
            with self.subTest(body=body):
                sim=Simulation()
                c=Setup.model_validate_json((root/f'benchmarks/{body}_1k.json').read_text())
                c.pins=[0,1]
                try:
                    initial=sim.configure(c.model_dump(mode='json'))
                    mesh=initial['element_mesh'];count=len(mesh['elements'])
                    selected=list(range(count//2))
                    region=dict(elements=selected,material=dict(youngs_modulus_pa=300000,density_kg_m3=600,
                        bending_plasticity=body=='shell',volume_plasticity=body=='volume'))
                    data=initial['config'];data['material_regions']=[region]
                    state=sim.configure(data)
                    e=sim.engine
                    self.assertEqual(state['frame'],0)
                    np.testing.assert_array_equal(state['element_mesh']['material_ids'],[1]*len(selected)+[0]*(count-len(selected)))
                    coupling=e.scene.sim.coupler;name=('cloth' if body=='shell' else 'fem')+'_0_0'
                    obj=coupling._ipc_objects.find(name)[0]
                    for slot in coupling._ipc_scene.geometries().find(obj.geometries().ids()[0]):
                        g=slot.geometry();cells=g.triangles() if body=='shell' else g.tetrahedra()
                        expected=np.full(count,8.) if body=='shell' else np.r_[np.full(len(selected),160000.),np.full(count-len(selected),100000/2.5*4/3)]
                        np.testing.assert_allclose(uipc.view(cells.find('mu')).reshape(-1),expected)
                        self.assertIsNotNone(g.vertices().find('mass'))
                    bad=data | {'material_regions':[dict(elements=[count],material={})]}
                    with self.assertRaisesRegex(ValueError,'超出范围'):sim.configure(bad)
                    self.assertIs(sim.engine,e)
                    e.set_forces([dict(vertex=len(e.x)//2,vector=[0,0,-.001])])
                    for _ in range(5):e.step()
                    self.assertTrue(np.isfinite(e.x).all())
                    np.testing.assert_array_equal(e.x[c.pins],np.array(initial['positions'])[c.pins])
                    self.assertEqual(e.frame,5)
                    self.assertEqual(e.state(True)['config']['material_regions'][0]['material'],region['material'])
                    data=e.config.model_dump(mode='json');data['material_regions']=[]
                    cleared=sim.configure(data)
                    self.assertEqual(cleared['config']['material_regions'],[])
                    self.assertEqual(cleared['frame'],0)
                    self.assertTrue(all(i==0 for i in cleared['element_mesh']['material_ids']))
                finally:sim.close()


if __name__=='__main__':unittest.main()
