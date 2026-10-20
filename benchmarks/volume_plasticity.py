"""SNK1/J2 volume validation: balanced end forces, release, and time-step refinement.
No position resets, no pins, zero gravity; original global solver tolerances.
Requires the v2 native package on PYTHONPATH.
"""
from pathlib import Path
import argparse,json
import numpy as np
from app.config import Setup,Material
from app.engine import GenesisEngine

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runtime/volume-plasticity-v2'

def run_case(plastic=True,hardening=0.,dt=.005,yield_percent=2.5):
    import trimesh
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/'block.obj'
    if not path.exists():
        mesh=trimesh.creation.box(extents=(.05,.05,.05));mesh.apply_translation([0,0,.3]);mesh.export(path)
    config=Setup(mesh=path.relative_to(ROOT).as_posix(),body_type='volume',gravity=(0,0,0),
        tet_max_volume_m3=1e-6,contact_d_hat_m=1e-5,dt=dt,
        material=Material(youngs_modulus_pa=5000,poisson_ratio=.45,density_kg_m3=1050,
            volume_plasticity=plastic,volume_yield_strain_percent=yield_percent,volume_plastic_hardening=hardening))
    engine=GenesisEngine(config);rows=[];positions=[]
    try:
        surface=engine.x.copy();rest=engine.entity.init_positions.detach().cpu().numpy().reshape(-1,3)
        tets=np.asarray(engine.entity.elems);centered=rest-rest.mean(axis=0);fit=np.linalg.pinv(centered)
        left=np.flatnonzero(surface[:,0]<surface[:,0].min()+1e-8)
        right=np.flatnonzero(surface[:,0]>surface[:,0].max()-1e-8)
        weights=np.zeros(len(surface))
        for ids in engine.faces:
            if np.all(np.isin(ids,left)) or np.all(np.isin(ids,right)):
                a,b,c=surface[ids]
                weights[ids]+=np.linalg.norm(np.cross(b-a,c-a))/6
        left_weights=weights[left]/weights[left].sum()
        right_weights=weights[right]/weights[right].sum()
        def determinants(x):
            t=x[tets];return np.linalg.det(np.stack([t[:,1]-t[:,0],t[:,2]-t[:,0],t[:,3]-t[:,0]],axis=-1))
        reference=determinants(rest)
        count=round(.3/dt)
        for frame in range(count):
            time=(frame+.5)*dt
            force=.6*min(time/.01,1.) if time<.02 else 0.
            forces=[dict(vertex=int(i),vector=[-force*w,0,0]) for i,w in zip(left,left_weights)]
            forces += [dict(vertex=int(i),vector=[force*w,0,0]) for i,w in zip(right,right_weights)]
            engine.set_forces(forces if force else [])
            engine.step();x=engine.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3)
            stretches=np.linalg.svd(fit@(x-x.mean(axis=0)),compute_uv=False)
            rows.append(dict(time_s=engine.time,total_end_force_n=force,stretch=float(stretches[0]),
                min_j=float(np.min(determinants(x)/reference)),step_ms=engine.step_ms))
            positions.append(engine.x.copy())
            if frame<8 or (frame+1)%10==0:print("FRAME",plastic,hardening,dt,frame+1,rows[-1],flush=True)
        import uipc
        body=engine.scene.sim.coupler._ipc_objects.find('fem_0_0')[0]
        slots=engine.scene.sim.coupler._ipc_scene.geometries().find(body.geometries().ids()[0])
        geometry=slots[0].geometry()
        mu=np.asarray(uipc.view(geometry.tetrahedra().find('mu'))).reshape(-1)
        return dict(plastic=plastic,hardening=hardening,dt=dt,yield_reference_percent=yield_percent,
            native_mu_pa=float(mu[0]),yield_stress_pa=float(3*mu[0]*yield_percent/100),
            hardening_modulus_pa=float(3*mu[0]*hardening),
            vertices=len(rest),tetrahedra=len(tets),left_points=len(left),right_points=len(right),
            final_stretch=rows[-1]['stretch'],min_j=min(r['min_j'] for r in rows),
            finite=bool(np.isfinite(positions).all()),rows=rows),np.asarray(positions),engine.faces.copy()
    finally:engine.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refine',action='store_true');args=parser.parse_args()
    cases=[('elastic',False,0,.005),('plastic',True,0,.005),('hardening',True,1,.005)]
    if args.refine:cases += [('hardening_dt_2p5ms',True,1,.0025),('hardening_dt_1p25ms',True,1,.00125)]
    results=[];arrays={}
    for name,plastic,h,dt in cases:
        result,states,faces=run_case(plastic,h,dt)
        print(name,{k:v for k,v in result.items() if k!='rows'},flush=True)
        if not result['finite'] or result['min_j']<=0:raise RuntimeError('Invalid tetrahedra in '+name)
        results.append(dict(name=name,**result));arrays[name+'_positions']=states;arrays['faces']=faces
        (OUT/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        np.savez_compressed(OUT/'states.npz',**arrays)

if __name__=='__main__':main()
