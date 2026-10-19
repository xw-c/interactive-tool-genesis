"""Compare native IPC and AL-IPC without changing the application or installed packages."""
import argparse,importlib,json,os,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
OUT=ROOT/'runtime'/'alipc-comparison'
CASES=['shell_1k','shell_10k','volume_1k','volume_10k']
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--method',choices=['ipc','al-ipc'])
parser.add_argument('--case',choices=CASES)
parser.add_argument('--scenario',choices=['fall','ground','pin_force','pin_only','force_check','force_check_control'],default='fall')
parser.add_argument('--suite',action='store_true')
args=parser.parse_args()
OUT.mkdir(parents=True,exist_ok=True)
if args.suite:
    jobs=[(c,s,m) for s in ['fall','ground'] for c in CASES for m in ['ipc','al-ipc']]
    jobs += [(c,s,m) for s in ['pin_force','pin_only'] for c in ['shell_1k','volume_1k'] for m in ['ipc','al-ipc']]
    jobs += [('shell_1k',s,'al-ipc') for s in ['force_check','force_check_control']]
    status=[]
    for case,scenario,method in jobs:
        key=f'{case}-{scenario}-{method}'
        print('RUN',key,flush=True)
        log=OUT/(key+'.log')
        start=time.perf_counter()
        with log.open('w',encoding='utf8') as f:
            p=subprocess.run([sys.executable,'-X','utf8','-u',__file__,'--case',case,'--scenario',scenario,'--method',method],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
        record=dict(case=case,scenario=scenario,method=method,exit_code=p.returncode,wall_s=round(time.perf_counter()-start,3))
        status.append(record)
        (OUT/'status.json').write_text(json.dumps(status,indent=2),encoding='utf8')
        print('DONE',json.dumps(record),flush=True)
    sys.exit(0 if all(x['exit_code']==0 for x in status) else 1)
if not args.method or not args.case:parser.error('--method and --case are required')
import app
import numpy as np
import genesis as gs
from importlib.metadata import version
from app.interface import Simulation
from typing import Literal
BaseIPCOptions=gs.options.IPCCouplerOptions
if args.method=='al-ipc':
    class ALIPCOptions(BaseIPCOptions):
        contact_constitution: Literal['ipc','isometric','al-ipc']='al-ipc'
    gs.options.IPCCouplerOptions=ALIPCOptions
from app.config import Setup
from app.mesh import load_mesh
# Genesis 1.4.1 rejects al-ipc in its base Literal. The test extends the option
# type above and verifies the native forwarding here; no installed code is edited.
module=importlib.import_module('genesis.engine.couplers.ipc_coupler.coupler')
original=module.build_ipc_scene_config
effective={}
def build_config(*a,**kw):
    config=original(*a,**kw)
    if args.method=='al-ipc':
        assert a[0].contact_constitution=='al-ipc'
        assert config['contact']['constitution']=='al-ipc'
    config['contact']['constitution']=args.method
    effective.update(config)
    return config
module.build_ipc_scene_config=build_config
config=json.loads((ROOT/'benchmarks'/(args.case+'.json')).read_text(encoding='utf8'))
assert config['contact_d_hat_m']==.0005
if args.scenario=='ground':
    mesh=load_mesh(Setup.model_validate(config))
    mesh.apply_translation([0,0,.001-float(mesh.vertices[:,2].min())])
    path=OUT/(args.case+'-ground.obj')
    mesh.export(path)
    config['mesh']=path.relative_to(ROOT).as_posix()
if args.scenario in ('pin_force','pin_only'):
    config['pins']=[0,1]
    config['forces']=[dict(vertex=2,vector=[0,0,.0001 if config['body_type']=='shell' else .01])] if args.scenario=='pin_force' else []
if args.scenario in ('force_check','force_check_control'):
    config['gravity']=[0,0,0]
    config['pins']=[0,1]
    config['forces']=[dict(vertex=2,vector=[0,0,.001])] if args.scenario=='force_check' else []
key=f'{args.case}-{args.scenario}-{args.method}'
warmup=5 if args.scenario=='ground' else 3
steps=20
result=dict(case=args.case,scenario=args.scenario,method=args.method,requested_steps=steps,warmup_steps=warmup,
    genesis_version=version('genesis-world'),pyuipc_version=version('pyuipc'),torch_version=version('torch'),
    config=config,complete=False)
sim=Simulation()
try:
    start=time.perf_counter()
    initial=sim.configure(config)
    result.update(build_s=time.perf_counter()-start,mesh=initial['volume_mesh'],effective_contact=effective['contact'],
        effective_newton=effective['newton'],backend=gs.backend.name,warnings=initial['contact_check']['warnings'])
    e=sim.engine
    x0=e.x.copy()
    floor_offset=config['material']['thickness_m']/2 if config['body_type']=='shell' else 0.
    min_clearance=float('inf')
    pin_error=0.
    samples=[];advance=[];trace=[]
    start_loop=time.perf_counter()
    for i in range(warmup+steps):
        start=time.perf_counter()
        state=e.step()
        json.dumps(state)
        full_ms=(time.perf_counter()-start)*1000
        min_clearance=min(min_clearance,float(e.x[:,2].min())-floor_offset)
        if config['pins']:pin_error=max(pin_error,float(np.linalg.norm(e.x[config['pins']]-x0[config['pins']],axis=1).max()))
        trace.append(dict(frame=e.frame,time=e.time,min_z=float(e.x[:,2].min()),mean_z=float(e.x[:,2].mean()),step_ms=state['metrics']['step_ms']))
        if i>=warmup:samples.append(full_ms);advance.append(state['metrics']['step_ms'])
        if (i+1)%5==0:print('PROGRESS',key,i+1,full_ms,flush=True)
        if time.perf_counter()-start_loop>120:
            result['stopped_reason']='Step loop exceeded 120 s; incomplete results are not a speed comparison.'
            break
    edges=np.unique(np.sort(np.concatenate([e.faces[:,[0,1]],e.faces[:,[1,2]],e.faces[:,[2,0]]]),axis=1),axis=0)
    rest=np.linalg.norm(x0[edges[:,0]]-x0[edges[:,1]],axis=1)
    strain=np.linalg.norm(e.x[edges[:,0]]-e.x[edges[:,1]],axis=1)/rest
    result.update(complete=e.frame==warmup+steps,frames=e.frame,measured_steps=len(samples),
        full_ms_median=float(np.median(samples)) if samples else None,
        full_ms_p95=float(np.percentile(samples,95)) if samples else None,
        advance_ms_median=float(np.median(advance)) if advance else None,
        full_ms_samples=samples,min_surface_clearance_m=min_clearance,max_pin_error_m=pin_error,
        final_mean_displacement_m=(e.x-x0).mean(axis=0).tolist(),
        final_max_displacement_m=float(np.linalg.norm(e.x-x0,axis=1).max()),
        surface_edge_ratio_min=float(strain.min()),surface_edge_ratio_max=float(strain.max()),trace=trace)
    if config['body_type']=='volume':
        current=e.entity.get_state().pos.detach().cpu().numpy().reshape(-1,3)
        reference=e.entity.init_positions.detach().cpu().numpy().reshape(-1,3)
        tets=np.asarray(e.entity.elems,dtype=np.int64)
        def det(points):
            p=points[tets]
            return np.linalg.det(np.stack([p[:,1]-p[:,0],p[:,2]-p[:,0],p[:,3]-p[:,0]],axis=2))
        ratio=det(current)/det(reference)
        result.update(final_min_tet_volume_ratio=float(ratio.min()),final_inverted_tets=int(np.count_nonzero(ratio<=0)))
    np.savez_compressed(OUT/(key+'.npz'),initial=x0,final=e.x,faces=e.faces)
except Exception as exc:
    result['error']=str(exc)
    traceback.print_exc()
finally:
    sim.close()
    (OUT/(key+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print('RESULT',json.dumps({k:v for k,v in result.items() if k not in ['trace','config','effective_contact','effective_newton','full_ms_samples']},ensure_ascii=False),flush=True)
sys.exit(0 if result['complete'] else 1)

