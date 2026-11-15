"""USD-authored tuning reaches the existing native material/attachment inputs."""
import os
from pathlib import Path
import tempfile
import unittest
import numpy as np
import app
from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
from app.config import Material, Setup


def author_values(prim, values):
    for key, value in values.items():
        kind = Sdf.ValueTypeNames.Token if key=='bending_model' else Sdf.ValueTypeNames.Bool if isinstance(value,bool) else Sdf.ValueTypeNames.Double
        prim.CreateAttribute('interactiveIpc:'+key,kind).Set('' if value is None else value)


def make_asset(filename, plastic=False):
    stage=Usd.Stage.CreateNew(str(filename))
    UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
    volume=Material(youngs_modulus_pa=600000,poisson_ratio=.22,density_kg_m3=700,
        self_friction=.6,volume_plasticity=plastic,volume_yield_strain_percent=3.5,volume_plastic_hardening=.2)
    shell=Material(youngs_modulus_pa=200000,poisson_ratio=.3,density_kg_m3=800,
        thickness_m=.0015,shear_stiffness_n_m=12,bending_rigidity_nm=.0002,
        bending_plasticity=plastic,bending_yield_angle_deg=4,bending_plastic_hardening=.4,self_friction=.5)
    materials={}
    for name,values in [('volume',volume.model_dump()),('shell',shell.model_dump()),
            ('volumeRegion',dict(youngs_modulus_pa=1200000,density_kg_m3=1000,volume_plasticity=False)),
            ('shellRegion',dict(youngs_modulus_pa=100000,density_kg_m3=600,bending_plasticity=False,bending_rigidity_nm=.00001))]:
        materials[name]=UsdShade.Material.Define(stage,'/materials/'+name)
        author_values(materials[name].GetPrim(),values)
    rigid_material=UsdShade.Material.Define(stage,'/materials/rigid')
    UsdPhysics.MaterialAPI.Apply(rigid_material.GetPrim()).CreateDynamicFrictionAttr(.7)
    base=np.array([[0,0,1],[.05,0,1],[0,.05,1],[0,0,1.05]])
    for name in ('volume','shell','rigid'):
        mesh=UsdGeom.Mesh.Define(stage,'/'+name)
        vertices=np.vstack((base,base+[.2,0,0])) if name=='volume' else (
            np.array([[0,0,1],[.05,0,1],[0,.05,1],[.05,.05,1]]) if name=='shell' else base+[0,-.1,0])
        mesh.CreatePointsAttr(vertices.tolist())
        faces=[0,1,2,1,3,2] if name=='shell' else [0,2,1,0,1,3,0,3,2,1,2,3]
        if name=='volume':faces+=(np.array(faces)+4).tolist()
        mesh.CreateFaceVertexCountsAttr([3]*(len(faces)//3));mesh.CreateFaceVertexIndicesAttr(faces)
        prim=mesh.GetPrim()
        if name=='volume':
            prim.CreateAttribute('physxDeformable:simulationRestPoints',Sdf.ValueTypeNames.Point3fArray).Set(vertices.tolist())
            prim.CreateAttribute('physxDeformable:simulationIndices',Sdf.ValueTypeNames.IntArray).Set(list(range(8)))
            author_values(prim,dict(youngs_modulus_pa=700000))
        elif name=='shell':
            prim.SetMetadata('apiSchemas',Sdf.TokenListOp.Create(prependedItems=['PhysxDeformableSurfaceAPI']))
            author_values(prim,dict(shear_stiffness_n_m=18))
        else:
            UsdPhysics.RigidBodyAPI.Apply(prim);UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(.1)
            prim.CreateAttribute('interactiveIpc:mass_kg',Sdf.ValueTypeNames.Double).Set(.12)
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(rigid_material if name=='rigid' else materials[name],materialPurpose='physics')
    region=stage.DefinePrim('/volume/second','Scope')
    region.CreateAttribute('interactiveIpc:elements',Sdf.ValueTypeNames.IntArray).Set([1])
    UsdShade.MaterialBindingAPI.Apply(region).Bind(materials['volumeRegion'],materialPurpose='physics')
    subset=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/shell')).CreateMaterialBindSubset('second',[1])
    UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(materials['shellRegion'],materialPurpose='physics')
    attachment=stage.DefinePrim('/connection','PhysxPhysicsAttachment')
    for end,name in enumerate(('shell','rigid')):
        attachment.CreateRelationship(f'actor{end}').SetTargets(['/'+name])
        attachment.CreateAttribute(f'points{end}',Sdf.ValueTypeNames.Point3fArray).Set(base[:3].tolist())
    attachment.CreateAttribute('interactiveIpc:stiffness_n_m',Sdf.ValueTypeNames.Double).Set(4200.)
    attachment.CreateAttribute('interactiveIpc:disable_contact',Sdf.ValueTypeNames.Bool).Set(True)
    stage.GetRootLayer().Save()
    return stage


class UsdMaterialReadTests(unittest.TestCase):
    def test_every_material_field_and_disabled_bending(self):
        from app.usd import _material_values
        stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageMetersPerUnit(stage,1.)
        prim=UsdShade.Material.Define(stage,'/material').GetPrim()
        values=Material(volume_plasticity=True,bending_plasticity=True,bending_model=None,
            youngs_modulus_pa=900000,poisson_ratio=.3,density_kg_m3=950,shear_stiffness_n_m=14,
            thickness_m=.002,bending_rigidity_nm=.0004,self_friction=.8,
            volume_yield_strain_percent=5,volume_plastic_hardening=.2,
            bending_yield_angle_deg=6,bending_plastic_hardening=.3).model_dump()
        author_values(prim,values)
        self.assertEqual(_material_values(prim),values)
        self.assertEqual(_material_values(prim,True),values)

    def test_standard_density_units_and_custom_precedence(self):
        from app.usd import _material_values
        stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageMetersPerUnit(stage,.01)
        UsdPhysics.SetStageKilogramsPerUnit(stage,2.)
        prim=UsdShade.Material.Define(stage,'/material').GetPrim()
        api=UsdPhysics.MaterialAPI.Apply(prim);api.CreateDensityAttr(.0005);api.CreateDynamicFrictionAttr(.7)
        self.assertAlmostEqual(_material_values(prim)['density_kg_m3'],1000.,places=4)
        author_values(prim,dict(density_kg_m3=700,self_friction=.2))
        self.assertEqual(_material_values(prim)['density_kg_m3'],700)
        self.assertEqual(_material_values(prim)['self_friction'],.2)

    def test_json_round_trip_preserves_region_inheritance_and_explicit_clear(self):
        for fields in ({'material':{'youngs_modulus_pa':800000}}, {'material_regions':[]}):
            setup=Setup(mesh='asset.usd',bodies={'/volume':fields})
            restored=Setup.model_validate_json(setup.model_dump_json())
            self.assertEqual('material_regions' in restored.bodies['/volume'].model_fields_set,'material_regions' in fields)


@unittest.skipUnless(os.environ.get('IPC_GPU_TEST')=='1','Requires the validated native CUDA package')
class UsdNativeMaterialTests(unittest.TestCase):
    def create_engine(self,folder,plastic=False,**fields):
        from app.engine import GenesisEngine
        filename=Path(folder)/'materials.usda'
        make_asset(filename,plastic)
        return GenesisEngine(Setup(mesh=str(filename),dt=.001,contact_mode='none',**fields),output=Path(folder)/'engine')

    def check_native(self,engine,plastic):
        import uipc
        info=engine.state(True);slots=engine._usd.slots
        self.assertEqual(info['element_mesh']['kind'],'mixed')
        self.assertEqual(info['bodies']['/volume']['element_material_ids'],[0,1])
        self.assertEqual(info['bodies']['/shell']['element_material_ids'],[0,1])
        volume=slots['/volume'].geometry();shell=slots['/shell'].geometry()
        np.testing.assert_allclose(uipc.view(volume.tetrahedra().find('mu')).ravel(),4/3*np.array([700000,1200000])/(2*1.22))
        np.testing.assert_allclose(uipc.view(shell.triangles().find('lambda')).ravel(),np.array([200000,100000])*.0015/(1-.3**2))
        np.testing.assert_allclose(uipc.view(shell.triangles().find('mu')).ravel(),18.)
        self.assertAlmostEqual(np.asarray(uipc.view(volume.vertices().find('mass'))).sum(),(.05**3/6)*(700+1000),places=7)
        self.assertAlmostEqual(np.asarray(uipc.view(shell.vertices().find('mass'))).sum(),(.05**2/2)*.0015*(800+600),places=9)
        self.assertEqual(info['bodies']['/rigid']['mass_kg'],.12)
        self.assertAlmostEqual(info['bodies']['/rigid']['material']['self_friction'],.7,places=6)
        connection=info['attachments'][0]
        self.assertEqual(connection['total_stiffness_n_m'],4200.)
        self.assertEqual(connection['pair_count'],3)
        self.assertIn(('shell','rigid'),[(a.strip('/'),b.strip('/')) for a,b in engine._usd.disabled_pairs])
        if plastic:
            np.testing.assert_array_equal(uipc.view(volume.tetrahedra().find('plasticity_enabled')).ravel(),[1,0])
            mu=700000/(2*1.22)
            self.assertAlmostEqual(float(uipc.view(volume.tetrahedra().find('plastic_yield_stress')).ravel()[0]),4*mu*.035,places=5)
            self.assertAlmostEqual(float(uipc.view(volume.tetrahedra().find('plastic_hardening')).ravel()[0]),4*mu*.2,places=5)
            thresholds=np.asarray(uipc.view(shell.edges().find('bending_yield_threshold'))).ravel()
            self.assertAlmostEqual(thresholds.min(),np.deg2rad(4),places=7)
            self.assertAlmostEqual(thresholds.max(),2*np.pi,places=7)
            self.assertTrue(np.any(np.asarray(uipc.view(shell.edges().find('bending_hardening_modulus')))>0))
        for _ in range(20):engine.step(snapshot=False)
        self.assertTrue(np.isfinite(engine.get_simulation_positions()).all())

    def test_usd_only_material_regions_mass_and_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=self.create_engine(folder)
            try:self.check_native(engine,False)
            finally:engine.close()

    def test_force_radius_stays_on_selected_body(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=self.create_engine(folder)
            try:
                volume=engine.bodies['/volume']
                vector=[.05,-.025,0.]
                engine.set_forces([dict(vertex=volume['vertex_start'],vector=vector,radius_m=1.)])
                forces=engine._usd._forces['/volume']
                self.assertGreater(np.count_nonzero(np.linalg.norm(forces,axis=1)),1)
                np.testing.assert_allclose(forces.sum(axis=0),vector)
                np.testing.assert_array_equal(engine._usd._forces['/shell'],0)
                engine.set_forces([dict(vertex=0,vector=vector,radius_m=0.)],body='/volume')
                forces=engine._usd._forces['/volume']
                np.testing.assert_allclose(forces[0],vector)
                np.testing.assert_array_equal(forces[1:],0)
            finally:engine.close()

    @unittest.skipUnless(os.environ.get('IPC_VOLUME_GPU_TEST')=='1','Requires the existing plasticity backend')
    def test_usd_only_volume_and_shell_plasticity(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=self.create_engine(folder,plastic=True)
            try:self.check_native(engine,True)
            finally:engine.close()

    def test_explicit_json_override_and_region_clear(self):
        import uipc
        for clear in (False,True):
            with self.subTest(clear=clear),tempfile.TemporaryDirectory() as folder:
                body={'material':{'youngs_modulus_pa':900000}}
                if clear:body['material_regions']=[]
                config=Setup(mesh='asset.usd',material={'youngs_modulus_pa':800000},bodies={
                    '/volume':body,'/rigid':{'mass_kg':.2,'material':{'self_friction':.9}}})
                config=Setup.model_validate_json(config.model_dump_json())
                engine=self.create_engine(folder,material=config.material,bodies=config.bodies)
                try:
                    mu=uipc.view(engine._usd.slots['/volume'].geometry().tetrahedra().find('mu')).ravel()
                    np.testing.assert_allclose(mu,4/3*np.array([900000,900000 if clear else 1200000])/(2*1.22))
                    self.assertEqual(engine.bodies['/volume']['element_material_ids'],[0,0 if clear else 1])
                    self.assertEqual(engine.bodies['/rigid']['mass_kg'],.2)
                    self.assertEqual(engine.bodies['/rigid']['material']['self_friction'],.9)
                finally:engine.close()

    def test_json_can_disable_usd_plasticity_without_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=self.create_engine(folder,plastic=True,bodies={
                '/volume':{'material':{'volume_plasticity':False}},
                '/shell':{'material':{'bending_plasticity':False,'bending_model':None}}})
            try:
                self.assertFalse(engine.bodies['/volume']['material']['volume_plasticity'])
                self.assertFalse(engine.bodies['/shell']['material']['bending_plasticity'])
                self.assertIsNone(engine.bodies['/shell']['material']['bending_model'])
                engine.step(snapshot=False)
            finally:engine.close()

    def test_subset_only_materials_and_invalid_value_override(self):
        from app.engine import GenesisEngine
        with tempfile.TemporaryDirectory() as folder:
            filename=Path(folder)/'subsets.usda';stage=make_asset(filename)
            shell=stage.GetPrimAtPath('/shell')
            UsdShade.MaterialBindingAPI(shell).UnbindAllBindings()
            shell.RemoveProperty('interactiveIpc:shear_stiffness_n_m')
            # Cover every face with complete material bindings, without a base binding.
            for subset_name,face in [('first',0),('second',1)]:
                subset=UsdShade.MaterialBindingAPI(shell).CreateMaterialBindSubset(subset_name,[face])
                UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(
                    UsdShade.Material(stage.GetPrimAtPath('/materials/shell')),materialPurpose='physics')
            author_values(stage.GetPrimAtPath('/volume'),dict(youngs_modulus_pa=-1))
            binding=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/volume'));binding.UnbindAllBindings()
            binding.Bind(UsdShade.Material(stage.GetPrimAtPath('/materials/volume')))
            stage.GetRootLayer().Save()
            setup=Setup(mesh=str(filename),dt=.001,contact_mode='none',bodies={
                '/volume':{'material':{'youngs_modulus_pa':900000}}})
            engine=GenesisEngine(setup,output=Path(folder)/'engine')
            try:
                self.assertEqual(set(engine.bodies['/shell']['element_material_ids']),{1,2})
                self.assertEqual(engine.bodies['/shell']['material']['thickness_m'],.0015)
                engine.step(snapshot=False)
            finally:engine.close()


if __name__=='__main__':unittest.main()
