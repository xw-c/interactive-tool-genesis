"""Read-only native parameter and rest-state membrane Hessian audit. No solver tolerances changed."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import app
import numpy as np
import uipc
from scipy.io import mmread
from scipy.sparse import block_diag,coo_matrix,diags
from scipy.sparse.linalg import norm
from uipc.geometry import trimesh
from uipc.constitution import StrainLimitingBaraffWitkinShell,ElasticModuli2D
out=ROOT/'runtime/cloth-model-audit';out.mkdir(exist_ok=True)
results=[]
for E,t in [(100000.,0.00025),(100000.,0.0005),(200000.,0.00025)]:
 m=trimesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),np.array([[0,1,2]],dtype=np.int32))
 StrainLimitingBaraffWitkinShell().apply_to(m,moduli=ElasticModuli2D.youngs_poisson(E,.25),mass_density=360.,thickness=t/2)
 ks=float(uipc.view(m.triangles().find('lambda'))[0]);kh=float(uipc.view(m.triangles().find('mu'))[0]);results.append(dict(E_input=E,nu_input=.25,full_thickness_m=t,k_stretch=ks,k_shear=kh,ratio=kh/ks))
base=json.loads((ROOT/'runtime/ipc-profile/baseline-clean.json').read_text());x=np.array(base['initial']['positions']);faces=np.array(base['initial']['faces']);cfg=base['config'];n=len(x);rows=[];cols=[];values=[];mass=np.zeros(n)
ks=results[0]['k_stretch'];kh=results[0]['k_shear'];dt=cfg['dt']
for tri in faces:
 p=x[tri];d1=p[1]-p[0];d2=p[2]-p[0];a=np.linalg.norm(d1);u=d1/a;b=float(u@d2);perp=d2-b*u;c=np.linalg.norm(perp);v=perp/c;area=.5*a*c
 # Native Dm rotates the normal to +Z and retains the projected XY material axes.
 ib=np.linalg.inv(np.column_stack([d1[:2],d2[:2]]));f=np.column_stack([d1,d2])@ib;u=f[:,0];v=f[:,1];du=np.array([-ib[:,0].sum(),ib[0,0],ib[1,0]]);dv=np.array([-ib[:,1].sum(),ib[0,1],ib[1,1]])
 gu=np.outer(du,u).ravel();gv=np.outer(dv,v).ravel();gg=(np.outer(du,v)+np.outer(dv,u)).ravel()
 h=2*area*(ks*(np.outer(gu,gu)+np.outer(gv,gv))+kh*np.outer(gg,gg))
 idx=(tri[:,None]*3+np.arange(3)).ravel();rows.extend(np.repeat(idx,9));cols.extend(np.tile(idx,9));values.extend(h.ravel())
 mass[tri]+=cfg['material']['density_kg_m3']*cfg['material']['thickness_m']*area/3
href=coo_matrix((values,(rows,cols)),shape=(3*n,3*n)).tocsr();aref=diags(np.repeat(mass,3))+dt**2*href
path=next((ROOT/'runtime/ipc-profile/matrix-baseline').rglob('A.1.0.mtx'));raw=mmread(path).tocsr();diag=block_diag([raw[i:i+3,i:i+3].toarray() for i in range(0,3*n,3)],format='csr');actual=raw+raw.T-diag
# Flat-rest bending affects normal directions; compare the x/y membrane block only.
xy=np.arange(3*n).reshape(-1,3)[:,:2].ravel();a=actual[xy][:,xy];b=aref[xy][:,xy];diff=a-b
checks=dict(matrix_snapshot=str(path),vertices=n,triangles=len(faces),total_mass_kg=float(mass.sum()),inplane_relative_frobenius_error=float(norm(diff)/norm(a)),inplane_max_abs_error=float(abs(diff).max()),inplane_max_native_entry=float(abs(a).max()),comparison='M + dt^2 * Hessian of area-weighted BW stretch/shear at F_rest; bending excluded by x/y restriction; cubic extension term has zero Hessian at rest')
assert checks['inplane_relative_frobenius_error']<1e-6,checks
result=dict(parameter_probes=results,rest_hessian_check=checks,scope='No runtime dependencies or simulation/solver settings modified; rest-state membrane audit does not validate every nonlinear state or PSD projection.')
(out/'audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
