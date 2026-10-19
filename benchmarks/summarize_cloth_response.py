"""Summarize native loaded coupons; these are finite-time responses, not pure shear tests."""
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'benchmarks'))
from cloth_response import pk1
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
out=ROOT/'runtime/cloth-response'
names=['stretch_legacy','stretch_shear8','shear_legacy','shear_shear8'];results=[];configs=[]
fig,axs=plt.subplots(1,3,figsize=(14,4),layout='constrained')
for name in names:
    d=json.loads((out/(name+'.json')).read_text());z=np.load(out/(name+'.npz'));x=z['initial'];p=z['final'];faces=z['faces'];a=d['attributes'];g=np.zeros_like(x)
    for tri in faces:
        X=x[tri];Y=p[tri];Dm=np.column_stack([(X[1]-X[0])[:2],(X[2]-X[0])[:2]]);IB=np.linalg.inv(Dm)
        area=np.linalg.norm(np.cross(X[1]-X[0],X[2]-X[0]))/2
        F=np.column_stack([Y[1]-Y[0],Y[2]-Y[0]])@IB
        G=area*pk1(F,a['lambda'],a['mu'],a['strain_rate'])@IB.T
        g[tri[1]]+=G[:,0];g[tri[2]]+=G[:,1];g[tri[0]]-=G.sum(axis=1)
    axis=0 if name.startswith('stretch') else 1
    low=np.flatnonzero(np.isclose(x[:,axis],x[:,axis].min(),atol=1e-9));high=np.flatnonzero(np.isclose(x[:,axis],x[:,axis].max(),atol=1e-9))
    ext=np.zeros_like(x);ext[high,0]=.01/len(high);r=g-ext;r[low]=0
    result=dict(case=name,final_edge_dx_mm=d['rows'][-1]['loaded_edge_dx_mm'],last20_edge_dx_range_mm=d['last20_edge_dx_range_mm'],max_pin_error_m=max(v['pin_error_m'] for v in d['rows']),max_z_change_m=float(np.max(abs(p[:,2]-x[:,2]))),free_membrane_static_residual_norm_n=float(np.linalg.norm(r)),external_force_vector_norm_n=float(np.linalg.norm(ext)),clamp_reaction_x_n=float(g[low,0].sum()),finite=d['finite'])
    assert d['finite'] and result['max_pin_error_m']<1e-9
    results.append(result);configs.append(json.loads(d['resolved_config']))
    k=0 if name.startswith('stretch') else 1
    axs[k].plot(np.arange(1,len(d['rows'])+1)*d['dt'],[v['loaded_edge_dx_mm'] for v in d['rows']],label=name)
    if k==1:
        # Decimate lines only for display, keep original measured geometry.
        from matplotlib.collections import LineCollection
        edges=np.unique(np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1),axis=0)
        points=p[:,:2]*1000
        color='tab:blue' if name.endswith('legacy') else 'tab:orange'
        axs[2].add_collection(LineCollection(points[edges[::5]],colors=color,linewidths=.45,alpha=.6,label=name))
assert all(c==configs[0] for c in configs),'Native scene config changed unexpectedly'
for i in [0,1]:
    axs[i].set(xlabel='Time (s)',ylabel='Loaded edge displacement (mm)',title=['Pull along the strip','Pull sideways on the top edge'][i]);axs[i].legend(fontsize=8);axs[i].grid(alpha=.25)
axs[2].autoscale();axs[2].set_aspect('equal');axs[2].set(xlabel='X (mm)',ylabel='Y (mm)',title='XY projection at 0.5 s');axs[2].legend(fontsize=8)
fig.savefig(out/'native_loaded.png',dpi=170);plt.close(fig)
analysis=json.loads((out/'analysis.json').read_text())
summary=dict(native_coupons=results,identical_resolved_scene_config=True,precision='float64',settings={'linear_tolerance':configs[0]['linear_system/tol_rate']['attribute']['values'],'newton_velocity_tolerance':configs[0]['newton/velocity_tol']['attribute']['values']},analytic_checks=analysis['checks'],native_directional_ratio_45_to_0=analysis['native_rest_directional_stiffness'][3]['force_per_width_per_strain']/analysis['native_rest_directional_stiffness'][0]['force_per_width_per_strain'])
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
