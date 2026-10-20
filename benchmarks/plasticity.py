"""CUDA load/unload experiment: prescribed hinge pose, then free elastic/plastic recovery."""
from pathlib import Path
import argparse,json
import numpy as np
from app.config import Setup,Material
from app.engine import GenesisEngine

OUT=Path(__file__).resolve().parents[1]/'runtime/plasticity'

def run_hinge(plastic=False,yield_deg=10.,hardening=0.,angle_deg=65.,steps=240):
    import uipc
    from uipc.core import FiniteElementStateAccessorFeature
    OUT.mkdir(parents=True,exist_ok=True)
    mesh=OUT/'hinge.obj'
    rest=np.array([[0,.05,.3],[0,0,.3],[.1,0,.3],[0,-.05,.3]])
    mesh.write_text(''.join('v '+' '.join(map(str,p))+'\n' for p in rest)+'f 1 2 3\nf 2 4 3\n')
    cfg=Setup(mesh=str(mesh.relative_to(OUT.parents[1])),gravity=(0,0,0),pins=[0,1,2],
        material=Material(youngs_modulus_pa=10000,density_kg_m3=1000,thickness_m=.001,
            bending_rigidity_nm=.01,bending_plasticity=plastic,bending_yield_angle_deg=yield_deg,
            bending_plastic_hardening=hardening),contact_d_hat_m=1e-5)
    engine=GenesisEngine(cfg)
    try:
        c=engine.scene.sim.coupler
        accessor=c._ipc_world.features().find(FiniteElementStateAccessorFeature)
        state=accessor.create_geometry()
        if state.positions() is None:state.vertices().create('position',np.zeros(3))
        if state.vertices().find('velocity') is None:state.vertices().create('velocity',np.zeros(3))
        driven=rest.copy();theta=np.deg2rad(angle_deg)
        driven[3]=[0,-.05*np.cos(theta),.3+.05*np.sin(theta)]
        angles=[];positions=[]
        for frame in range(steps+8):
            if frame<8:
                uipc.view(state.positions()).reshape(-1,3)[:]=driven
                uipc.view(state.vertices().find('velocity'))[:]=0.
                accessor.copy_from(state)
            engine.step();x=np.array(engine.state()['positions']);positions.append(x)
            angles.append(float(np.rad2deg(np.arctan2(x[3,2]-.3,-x[3,1]))))
        result=dict(loading='Reset the driven pose before each of 8 solves, then release; not an exact displacement clamp',plastic=plastic,yield_deg=yield_deg,hardening=hardening,
            imposed_angle_deg=angle_deg,release_angle_deg=angles[7],final_angle_deg=angles[-1],
            final_30_mean_deg=float(np.mean(angles[-30:])),final_30_range_deg=float(np.ptp(angles[-30:])),
            finite=bool(np.isfinite(positions).all()))
        return result,np.array(angles),np.array(positions)
    finally:engine.close()

def run_volume(plastic=False,yield_percent=10.,hardening=0.,steps=240):
    import uipc,trimesh
    from uipc.core import FiniteElementStateAccessorFeature
    OUT.mkdir(parents=True,exist_ok=True)
    mesh=OUT/'block.obj';box=trimesh.creation.box(extents=(.05,.05,.05))
    box.apply_translation([0,0,.3]);box.export(mesh)
    cfg=Setup(mesh=str(mesh.relative_to(OUT.parents[1])),body_type='volume',gravity=(0,0,0),
        tet_max_volume_m3=1e-6,contact_d_hat_m=1e-5,
        material=Material(youngs_modulus_pa=5000,poisson_ratio=.45,density_kg_m3=1050,
            volume_plasticity=plastic,volume_yield_strain_percent=yield_percent,
            volume_plastic_hardening=hardening))
    engine=GenesisEngine(cfg)
    try:
        rest=engine.entity.init_positions.detach().cpu().numpy().reshape(-1,3).copy()
        tets=np.asarray(engine.entity.elems)
        np.save(OUT/'volume-faces.npy',engine.faces)
        np.save(OUT/'volume-rest.npy',rest[engine.surface_ids])
        centered=rest-rest.mean(axis=0);reference=np.linalg.pinv(centered)
        def determinants(x):
            v=x[tets];return np.linalg.det(np.stack([v[:,1]-v[:,0],v[:,2]-v[:,0],v[:,3]-v[:,0]],axis=-1))
        rest_det=determinants(rest)
        accessor=engine.scene.sim.coupler._ipc_world.features().find(FiniteElementStateAccessorFeature)
        state=accessor.create_geometry()
        for name in ['position','velocity']:
            if state.vertices().find(name) is None:state.vertices().create(name,np.zeros(3))
        driven=centered*np.array([1.6,1/np.sqrt(1.6),1/np.sqrt(1.6)])+rest.mean(axis=0)
        stretches=[];surface=[];min_j=1.
        for frame in range(steps+8):
            if frame<8:
                uipc.view(state.positions()).reshape(-1,3)[:]=driven
                uipc.view(state.vertices().find('velocity'))[:]=0.
                accessor.copy_from(state)
            engine.step()
            x=engine.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3)
            stretches.append(np.linalg.svd(reference@(x-x.mean(axis=0)),compute_uv=False))
            surface.append(x[engine.surface_ids].copy())
            min_j=min(min_j,float(np.min(determinants(x)/rest_det)))
        result=dict(loading='Reset the driven pose before each of 8 solves, then release; not an exact displacement clamp',plastic=plastic,yield_percent=yield_percent,hardening=hardening,
            simulation_vertices=len(rest),tetrahedra=len(tets),release_stretches=stretches[7].tolist(),
            final_stretches=stretches[-1].tolist(),
            final_30_mean_stretch=np.mean(stretches[-30:],axis=0).tolist(),
            final_30_range=float(np.ptp(np.array(stretches[-30:]),axis=0).max()),
            min_j=min_j,finite=bool(np.isfinite(surface).all()))
        return result,np.array(stretches),np.array(surface)
    finally:engine.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--steps',type=int,default=240)
    parser.add_argument('--body',choices=['shell','volume'],default='shell')
    args=parser.parse_args();results=[];arrays={}
    for name,plastic,y,h in [('elastic',False,10,0),('plastic',True,10,0),('below_yield',True,90,0),('hardening',True,10,1)]:
        result,a,x=(run_hinge if args.body=='shell' else run_volume)(plastic,y,h,steps=args.steps)
        print(name,result,flush=True)
        results.append(dict(name=name,**result));arrays[name+('_angle' if args.body=='shell' else '_stretch')]=a;arrays[name+'_positions']=x
    (OUT/(args.body+'-results.json')).write_text(json.dumps(results,indent=2),encoding='utf-8')
    np.savez_compressed(OUT/(args.body+'-states.npz'),**arrays)
    print(json.dumps(results,indent=2))
