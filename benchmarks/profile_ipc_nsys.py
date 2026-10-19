"""Nsight entry point: avoid Windows venv launcher; reuse its exact package paths."""
import sys,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),'D:/xwchen/genesis-garment-demo/.venv/Lib/site-packages','D:/xwchen/lw-runtime/Lib/site-packages']
sys.stdout=open(ROOT/'runtime/profile-ipc-nsys-target-v2.log','w',buffering=1)
sys.stderr=sys.stdout
print('SYS_PATH',sys.path,flush=True)
sys.argv=[str(ROOT/'benchmarks/profile_ipc.py'),'--case','baseline','--tag','nsys','--nsys','--warmup','20','--steps','5']
runpy.run_path(str(ROOT/'benchmarks/profile_ipc.py'),run_name='__main__')
