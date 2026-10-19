"""Summarize profile_ipc outputs; optional CPU-only matrix spectrum inspection."""
import json,re,argparse
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'runtime/ipc-profile'
p=argparse.ArgumentParser();p.add_argument('--matrix',action='store_true');a=p.parse_args()
summary=[]
for path in sorted(OUT.glob('*.json')):
 if path.name.endswith('-timer.json') or path.name in ('summary.json','spectrum.json'):continue
 d=json.loads(path.read_text())
 if 'rows' not in d:continue
 label=path.stem;log=ROOT/'runtime'/('profile-ipc-'+label+'.log')
 if label=='baseline':log=ROOT/'runtime/profile-ipc-baseline-logged.log'
 frames=[]
 if log.exists():
  for block in log.read_text(errors='replace').split('PROFILE_FRAME ')[1:]:
   it=list(map(int,re.findall(r'Iterative linear solver iteration count: (\d+)',block)))
   frames.append(dict(frame=int(block.split()[0]),pcg=it))
 its=[n for f in frames for n in f['pcg']]
 summary.append(dict(label=path.stem,steps=len(d['rows']),median_ms=d['median_ms'],p95_ms=d['p95_ms'],rms_mm=d['freefall_rms_m']*1000,solves_per_step=len(its)/len(frames) if frames else None,pcg_mean=float(np.mean(its)) if its else None,pcg_max=max(its) if its else None,frames=frames))
for item in summary:
 label=item['label'];path=OUT/(label+'-positions.npy')
 if not path.exists():continue
 d=json.loads((OUT/(label+'.json')).read_text());x=np.load(path);x0=np.array(d['initial']['positions']);cfg=d['config'];n=d.get('frame',d['rows'][-1]['frame']+1)
 valid=d.get('freefall_reference_valid',not cfg['pins'] and d['case'] not in ('landing','mas_landing'))
 quality=dict(finite=bool(np.isfinite(x).all()),min_z_m=float(x[:,2].min()),reference_valid=valid)
 if cfg['pins']:quality['max_pin_error_m']=float(np.linalg.norm((x-x0)[cfg['pins']],axis=1).max())
 if valid:
  err=x-x0-np.array(cfg['gravity'])*cfg['dt']**2*n*(n+1)/2
  quality.update(coordinate_rms_mm=float(np.sqrt(np.mean(err**2))*1000),max_vertex_error_mm=float(np.linalg.norm(err,axis=1).max()*1000))
 item['quality']=quality
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
for d in summary:print({k:round(v,3) if isinstance(v,float) else v for k,v in d.items() if k not in ('frames','quality')})
if a.matrix:
 from scipy.io import mmread
 from scipy.sparse import block_diag
 from scipy.sparse.linalg import eigsh,spsolve
 spectra=[]
 for folder in sorted(OUT.glob('matrix-*')):
  path=next(folder.rglob('A.1.0.mtx'),None)
  if path is None:continue
  raw=mmread(path).tocsr()
  coo=raw.tocoo();assert np.all(coo.row//3<=coo.col//3), 'Expected upper block storage'
  diag=block_diag([raw[i:i+3,i:i+3].toarray() for i in range(0,raw.shape[0],3)],format='csr')
  A=raw+raw.T-diag
  asym=float(abs(A-A.T).max());assert asym<1e-10
  A=(A+A.T)*0.5
  blocks=[]
  for i in range(0,A.shape[0],3):blocks.append(np.linalg.inv(np.linalg.cholesky(A[i:i+3,i:i+3].toarray())))
  S=block_diag(blocks,format='csr');B=(S@A@S.T).tocsr()
  eig={}
  for name,C in [('A',A),('block_jacobi_A',B)]:
   small=float(eigsh(C,k=1,sigma=0,which='LM',return_eigenvectors=False)[0]);large=float(eigsh(C,k=1,which='LA',return_eigenvectors=False)[0]);eig[name]=dict(min=small,max=large,condition=large/small)
  # Constant translational modes should have only the inertial contribution.
  v=np.zeros(A.shape[0]);v[2::3]=1
  item=dict(case=folder.name,shape=A.shape,nnz=A.nnz,asymmetry=asym,eigen=eig,z_translation_rayleigh=float(v@(A@v)/(v@v)))
  b=np.asarray(mmread(path.with_name('b.1.0.mtx'))).reshape(-1);x=np.asarray(mmread(path.with_name('x.1.0.mtx'))).reshape(-1)
  r=b-A@x;sb=S@b;sr=S@r;xref=spsolve(A,b)
  item['linear_check']=dict(relative_residual=float(np.linalg.norm(r)/np.linalg.norm(b)),relative_preconditioned_rz=float((sr@sr)/(sb@sb)),relative_solution_error=float(np.linalg.norm(x-xref)/np.linalg.norm(xref)),direct_solution_z_mean=float(xref[2::3].mean()))
  spectra.append(item);print('SPECTRUM',item,flush=True)
 (OUT/'spectrum.json').write_text(json.dumps(spectra,indent=2))
