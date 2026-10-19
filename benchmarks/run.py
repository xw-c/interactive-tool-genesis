"""Run from project root. Measures the existing tool, not isolated GPU kernels."""
import argparse
import json
import sys
import time
from importlib.metadata import version
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.interface import Simulation
from app.mesh import load_mesh
from app.config import Setup
import genesis as gs

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('case',choices=['shell_1k','shell_10k','volume_1k','volume_10k'])
parser.add_argument('--warmup',type=int,default=1)
parser.add_argument('--steps',type=int,default=5)
args=parser.parse_args()
if args.steps<1 or args.warmup<0:parser.error('steps must be positive; warmup must be nonnegative')
config=json.loads((Path(__file__).parent/(args.case+'.json')).read_text(encoding='utf8'))
sim=Simulation()
try:
    print(f'{args.case}: building scene...',file=sys.stderr,flush=True)
    start=time.perf_counter();initial=sim.configure(config);build_s=time.perf_counter()-start
    print(f'Built in {build_s:.3f}s; {initial["volume_mesh"]}; warming up...',file=sys.stderr,flush=True)
    for _ in range(args.warmup):sim.engine.step()
    print(f'Measuring {args.steps} steps...',file=sys.stderr,flush=True)
    advance=[];full=[]
    for _ in range(args.steps):
        start=time.perf_counter();state=sim.engine.step();json.dumps(state);full.append((time.perf_counter()-start)*1000)
        advance.append(state['metrics']['step_ms'])
    print(json.dumps(dict(case=args.case,input_obj_vertices=len(load_mesh(Setup.model_validate(config)).vertices),
        **initial['volume_mesh'],genesis_version=version('genesis-world'),genesis_backend=gs.backend.name,soft_body_backend='uipc/cuda',
        contact_d_hat_m=sim.engine.config.contact_d_hat_m,contact_check=initial['contact_check'],
        build_seconds=round(build_s,3),warmup_steps=args.warmup,measured_steps=args.steps,
        advance_readback_ms_median=round(float(np.median(advance)),3),
        full_step_json_ms_median=round(float(np.median(full)),3),full_step_json_ms_p95=round(float(np.percentile(full,95)),3),
        full_step_json_ms_samples=[round(x,3) for x in full],
        simulated_seconds=sim.engine.time),ensure_ascii=False,indent=2),flush=True)
finally:sim.close()
