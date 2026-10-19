"""Diagnostic shell_1k profiling. Run in a fresh GPU Python process per case."""
import sys,json,time,cProfile,pstats,io,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import app
import numpy as np
import genesis as gs
import uipc
from app.interface import Simulation
p=argparse.ArgumentParser();p.add_argument('--case',default='baseline');p.add_argument('--steps',type=int,default=30);p.add_argument('--warmup',type=int,default=5);p.add_argument('--timer',action='store_true');p.add_argument('--log',action='store_true');p.add_argument('--nsys',action='store_true');p.add_argument('--dump',action='store_true');p.add_argument('--tag',default='');a=p.parse_args();label=a.case+('-'+a.tag if a.tag else '')
if a.steps<1 or a.warmup<0:p.error('steps must be positive and warmup nonnegative')
if a.case=='mas_partition':raise SystemExit('This installed pyuipc does not expose mesh_partition; use --case mas for its native MAS mode.')
out=ROOT/'runtime'/'ipc-profile';out.mkdir(exist_ok=True)
cfg=json.loads((ROOT/'benchmarks/shell_1k.json').read_text())
options={
'baseline':{},'no_contact':{'contact_enable':False},'wide_contact':{'contact_d_hat':0.01},
'linear_loose':{'linear_system_tolerance':1e-2},'linear_tight':{'linear_system_tolerance':1e-4},
'newton_tight':{'newton_tolerance':0.005},'direct':{'linear_system_solver':'direct'},
'no_bending':{},'soft':{},'small_dt':{},'pinned':{},'landing':{}}
options.update({'mas':{},'plain_pcg':{'linear_system_solver':'linear_pcg'},'graph_off':{},'full_newton':{'newton_semi_implicit_enable':False},'zero_gravity':{},'shear_scaled':{},'mas_partition':{},'mas_pinned':{},'mas_landing':{},'mas_tight':{'linear_system_tolerance':1e-4},'mas_accurate':{'linear_system_tolerance':1e-4,'newton_tolerance':0.005}})
kw=options[a.case]
raw={}
if a.case in ('mas','mas_partition','mas_pinned','mas_landing','mas_tight','mas_accurate'):raw={'fem_preconditioner':'mas'}
if a.case=='graph_off':raw={'use_cuda_graph':0}
if a.case=='zero_gravity':cfg['gravity']=[0,0,0]
import genesis.engine.couplers.ipc_coupler.coupler as native_coupler
original_config=native_coupler.build_ipc_scene_config
resolved_config={}
if a.log:
 old_init=native_coupler.IPCCoupler._init_ipc
 def verbose_init(self):
  old_init(self);uipc.Logger.set_level(uipc.Logger.Level.Info)
 native_coupler.IPCCoupler._init_ipc=verbose_init
def config_override(*args,**kwargs):
 c=original_config(*args,**kwargs);c['linear_system'].update(raw)
 if a.dump:c['extras']['debug']['dump_linear_system']=1
 resolved_config.update(c)
 return c
native_coupler.build_ipc_scene_config=config_override
if a.case=='no_bending':cfg['material']['bending_model']=None
if a.case=='soft':cfg['material']['youngs_modulus_pa']=10000
if a.case=='small_dt':cfg['dt']=0.001
if a.case in ('pinned','mas_pinned'):cfg['pins']=[0,24]
if a.case in ('landing','mas_landing'):
 import trimesh
 mesh=trimesh.load(ROOT/cfg['mesh'],process=False);mesh.vertices[:,2]-=0.199;mesh.export(out/'landing.obj');cfg['mesh']='runtime/ipc-profile/landing.obj'
original=gs.options.IPCCouplerOptions
def override(**kwargs):kwargs.update(kw);return original(**kwargs)
gs.options.IPCCouplerOptions=override
from app.engine import GenesisEngine
if a.case in ('shear_scaled','mas_partition'):
 old_attach=GenesisEngine._attach_ipc_inputs
 def attach(self):
  old_attach(self)
  c=self.scene.sim.coupler;old_add=c._add_fem_entities_to_ipc
  def add():
   old_add()
   body=c._ipc_objects.find('cloth_0_0')[0]
   for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0]):
    geo=slot.geometry()
    if a.case=='shear_scaled':uipc.view(geo.triangles().find('mu'))[:]*=self.config.material.thickness_m
    else:
     from uipc.geometry import mesh_partition
     mesh_partition(geo,16)
  c._add_fem_entities_to_ipc=add
 GenesisEngine._attach_ipc_inputs=attach
sim=Simulation()
try:
 t=time.perf_counter();initial=sim.configure(cfg);build=time.perf_counter()-t
 c=sim.engine.scene.sim.coupler
 print('DEFAULT_CONFIG',json.dumps(uipc.Scene.default_config()),flush=True)
 print('ENGINE_CONFIG',uipc.Engine.default_config(),flush=True)
 if a.dump:
  import tempfile,shutil
  workspace=Path(tempfile.gettempdir())/('genesis_ipc_'+sim.engine.scene.uid.full())
  print('DUMP_WORKSPACE',workspace,flush=True)
 print('BUILD',build,'OPTIONS',c.options,flush=True)
 body=c._ipc_objects.find('cloth_0_0')[0]
 for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0]):
  geo=slot.geometry()
  for key in ('lambda','mu','strain_rate'):
   attr=geo.triangles().find(key)
   if attr is not None:print('MATERIAL_ATTRIBUTE',key,uipc.view(attr).reshape(-1)[:4],flush=True)
 x0=sim.engine.x.copy()
 for i in range(a.warmup):sim.engine.step()
 if a.timer:uipc.Timer.enable_all()
 if a.timer or a.log:uipc.Logger.set_level(uipc.Logger.Level.Info)
 if a.nsys:
  import torch
  torch.cuda.profiler.start()
 rows=[]
 for i in range(a.steps):
  print('PROFILE_FRAME',sim.engine.frame+1,flush=True)
  t=time.perf_counter();s=sim.engine.step();t1=time.perf_counter();json.dumps(s);t2=time.perf_counter()
  rows.append({'frame':sim.engine.frame,'step_ms':sim.engine.step_ms,'full_ms':(t2-t)*1000,'state_json_ms':(t2-t1)*1000})
 if a.nsys:torch.cuda.profiler.stop()
 if a.dump:
  for item in workspace.rglob('*.mtx'):
   target=out/('matrix-'+a.case)/item.relative_to(workspace);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(item,target)
 if a.timer:
  report=uipc.Timer.report_as_json();(out/(label+'-timer.json')).write_text(json.dumps(report,indent=2));print('TIMER',report,flush=True)
 uipc.Timer.disable_all();uipc.Logger.set_level(uipc.Logger.Level.Error)
 pr=cProfile.Profile();pr.enable();sim.engine.step();pr.disable();pr.dump_stats(str(out/(label+'.pstats')))
 stream=io.StringIO();pstats.Stats(pr,stream=stream).sort_stats('cumulative').print_stats(30);print(stream.getvalue(),flush=True)
 x=sim.engine.x;n=sim.engine.frame
 expected=x0+np.array(cfg['gravity'])*cfg['dt']**2*n*(n+1)/2
 result=dict(case=a.case,label=label,timer=a.timer,config=cfg,overrides=kw,raw_linear=raw,resolved_config=resolved_config,material_override={'mu_scale':cfg['material']['thickness_m']} if a.case=='shear_scaled' else {},build_s=build,rows=rows,median_ms=float(np.median([r['full_ms'] for r in rows])),p95_ms=float(np.percentile([r['full_ms'] for r in rows],95)),freefall_reference_valid=not cfg['pins'] and a.case not in ('landing','mas_landing'),freefall_rms_m=float(np.sqrt(np.mean((x-expected)**2))),min_z=float(x[:,2].min()),max_xy_extent=float(np.ptp(x[:,:2],axis=0).max()),finite=bool(np.isfinite(x).all()),frame=n,centroid_error_m=(x-expected).mean(axis=0).tolist(),shape_rms_m=float(np.sqrt(np.mean(((x-x.mean(axis=0))-(x0-x0.mean(axis=0)))**2))),initial=initial)
 np.save(out/(label+'-positions.npy'),x)
 (out/(label+('-instrumented' if a.timer else '')+'.json')).write_text(json.dumps(result,indent=2))
 print('RESULT',json.dumps({k:v for k,v in result.items() if k not in ('initial','rows')}),flush=True)
finally:sim.close()
