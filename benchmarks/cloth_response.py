"""Cloth response research: analytic coupons + measured native rest matrix + GPU loaded coupons.
No production or solver tolerance changes. Run native cases in separate processes.
"""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import app
import numpy as np
OUT=ROOT/'runtime/cloth-response';OUT.mkdir(exist_ok=True)

def energy(F,ks,kh,alpha=100):
    e=np.linalg.norm(F,axis=0)-1
    return float(ks*np.sum(e*e+alpha*np.maximum(e,0)**3)+kh*np.dot(F[:,0],F[:,1])**2)

def pk1(F,ks,kh,alpha=100):
    lengths=np.linalg.norm(F,axis=0);e=lengths-1
    P=F*((ks*(2*e+3*alpha*np.maximum(e,0)**2))/lengths)[None,:]
    q=F[:,0]@F[:,1]
    P[:,0]+=2*kh*q*F[:,1];P[:,1]+=2*kh*q*F[:,0]
    return P

def artist_map(stretch_force_1pct_n,shear_resistance_n_m,hardening_10pct=16,width_m=.1):
    """Stretch force refers to a width=10cm coupon; shear resistance is its infinitesimal tangent in N/m.
    hardening_10pct: stretch force at 10% / force of infinitesimal linear tangent at 10%.
    """
    if not np.isfinite([stretch_force_1pct_n,shear_resistance_n_m,width_m,hardening_10pct]).all() or min(stretch_force_1pct_n,shear_resistance_n_m,width_m)<=0 or hardening_10pct<1:
        raise ValueError('Forces/width must be positive; hardening multiplier >= 1')
    alpha=(hardening_10pct-1)/.15
    ks=stretch_force_1pct_n/(width_m*(.02+3*alpha*.01**2))
    kh=shear_resistance_n_m/2
    return dict(ks=ks,kh=kh,alpha=alpha)

def analysis():
    from scipy.io import mmread
    from scipy.sparse import block_diag,diags
    base=json.loads((ROOT/'runtime/ipc-profile/baseline-clean.json').read_text())
    x=np.array(base['initial']['positions']);tri=np.array(base['initial']['faces']);n=len(x);dt=base['config']['dt']
    mass=np.zeros(n)
    for f in tri:
        p=x[f];area=np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))/2
        mass[f]+=.09*area/3
    raw=mmread(next((ROOT/'runtime/ipc-profile/matrix-baseline').rglob('A.1.0.mtx'))).tocsr()
    diag=block_diag([raw[i:i+3,i:i+3].toarray() for i in range(0,3*n,3)],format='csr')
    H=(raw+raw.T-diag-diags(np.repeat(mass,3)))/dt**2
    ks=100000*.00025/(1-.25**2);kh=40000;area_total=.04
    native=[]
    for deg in [0,15,30,45,60,90]:
        v=np.array([np.cos(np.deg2rad(deg)),np.sin(np.deg2rad(deg)),0])
        dx=np.outer((x-x.mean(axis=0))@v,v).ravel()
        measured=float(dx@H@dx/area_total)
        c,s=v[:2];expected=2*ks*(c**4+s**4)+8*kh*c*c*s*s
        assert np.isclose(measured,expected,rtol=1e-8),(measured,expected)
        native.append(dict(angle_deg=deg,force_per_width_per_strain=measured,formula=expected))
    settings={'legacy':(ks,40000,100),'shear8_only':(ks,8,100),'isotropic_partial':(13.333333333333,5,100),'no_hardening':(ks,8,0)}
    rows=[];max_fd=0;max_rigid=0
    for name,(s,h,alpha) in settings.items():
        for strain in [.001,.01,.05,.1]:
            F=np.array([[1+strain,0],[0,1],[0,0.]])
            rows.append(dict(case=name,test='axial',deformation=strain,force_10cm_n=float(.1*pk1(F,s,h,alpha)[0,0]),energy_per_area=energy(F,s,h,alpha)))
        for q in [.01,.1,.3]:
            # Fibers maintain their lengths, change angle; shear term isolated exactly.
            F=np.array([[1.,q],[0,np.sqrt(1-q*q)],[0,0.]])
            rows.append(dict(case=name,test='angle_change',deformation=q,angle_change_deg=float(np.rad2deg(np.arcsin(q))),generalized_force_10cm_n=.1*2*h*q,energy_per_area=energy(F,s,h,alpha)))
        for g in [.01,.1]:
            F=np.array([[1.,g],[0,1.],[0,0.]])
            rows.append(dict(case=name,test='simple_shear',deformation=g,force_10cm_n=float(.1*pk1(F,s,h,alpha)[0,1]),energy_per_area=energy(F,s,h,alpha)))
        # Non-rest force formula checked against numerical energy derivative.
        F=np.array([[1.03,.04],[.02,.97],[.01,.02]])
        P=pk1(F,s,h,alpha);Pfd=np.zeros((3,2));eps=1e-6
        for i in range(3):
            for j in range(2):
                d=np.zeros((3,2));d[i,j]=eps
                Pfd[i,j]=(energy(F+d,s,h,alpha)-energy(F-d,s,h,alpha))/(2*eps)
        err=float(np.linalg.norm(P-Pfd)/max(np.linalg.norm(P),1e-12));max_fd=max(max_fd,err);assert err<1e-7
        angle=.7;R=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        rigid=energy(R@np.eye(3)[:,:2],s,h,alpha);max_rigid=max(max_rigid,rigid);assert rigid<1e-20
    mapped=artist_map(.13333333333333333,16,16)
    assert np.isclose(mapped['ks'],ks) and np.isclose(mapped['kh'],8) and np.isclose(mapped['alpha'],100)
    for h in [1,4,16]:
        m=artist_map(.13333333333333333,16,h)
        F=np.array([[1.01,0],[0,1],[0,0.]])
        assert np.isclose(.1*pk1(F,**m)[0,0],.13333333333333333)
    for args in [(0,16,1),(1,0,1),(1,16,.5),(float('nan'),16,1)]:
        try:artist_map(*args)
        except ValueError:pass
        else:raise AssertionError('Invalid artist parameter accepted')
    import uipc
    from uipc.geometry import trimesh
    from uipc.constitution import StrainLimitingBaraffWitkinShell as BW,ElasticModuli2D as M
    geo=trimesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),np.array([[0,1,2]],dtype=np.int32))
    BW().apply_to(geo,stretch_moduli=M.youngs_poisson(100000.,.25),shear_moduli=M.youngs_poisson(20.,.25),mass_density=360.,thickness=.000125,strain_rate=100.)
    native_api={k:float(uipc.view(geo.triangles().find(k)).reshape(-1)[0]) for k in ['lambda','mu','strain_rate']}
    assert np.isclose(native_api['lambda'],ks) and np.isclose(native_api['mu'],8)

    result=dict(scope='Analytic finite-deformation coupons; native binary validation limited to rest-state directional stiffness. Not physical sample calibration.',native_rest_directional_stiffness=native,analytic_coupons=rows,checks=dict(force_finite_difference_max_relative_error=max_fd,rigid_rotation_energy_max=max_rigid),artist_mapping_example=mapped,native_dual_api=native_api)
    (OUT/'analysis.json').write_text(json.dumps(result,indent=2))
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif']=['Microsoft YaHei']
    plt.rcParams['axes.unicode_minus']=False
    labels={'legacy':'当前参数','shear8_only':'仅降低剪切','isotropic_partial':'小变形局部匹配参考','no_hardening':'关闭额外硬化'}
    fig,axs=plt.subplots(1,3,figsize=(14,4.1),layout='constrained')
    es=np.linspace(0,.1,201)
    for name,(s,h,alpha) in settings.items():
        axs[0].plot(es*100,.1*s*(2*es+3*alpha*es**2),label=labels[name])
    axs[0].set(xlabel='伸长量 (%)',ylabel='10 cm 宽布条的拉力 (N)',title='正向拉伸：降低剪切不改变这条曲线')
    axs[0].legend(fontsize=8)
    angles=np.linspace(0,90,181);r=np.deg2rad(angles)
    for name in ['legacy','shear8_only']:
        s,h,_=settings[name];K=2*s*(np.cos(r)**4+np.sin(r)**4)+8*h*np.cos(r)**2*np.sin(r)**2
        axs[1].semilogy(angles,K,label=labels[name])
    axs[1].scatter([i['angle_deg'] for i in native],[i['force_per_width_per_strain'] for i in native],s=15,c='black',label='原生矩阵核对')
    axs[1].set(xlabel='拉伸方向 (度)',ylabel='微小变形下的刚度 (N/m)',title='同一块布，换个方向就可能很硬')
    axs[1].legend(fontsize=8)
    for name in ['legacy','shear8_only']:
        s,h,alpha=settings[name];qs=np.linspace(.001,.1,100)
        axs[2].semilogy(np.rad2deg(np.arcsin(qs)),h*qs**2,label=labels[name])
    axs[2].set(xlabel='方格夹角变化 (度)',ylabel='单位面积能量 (J/m²)',title='只改变夹角：当前剪切系数高出 5000 倍')
    axs[2].legend(fontsize=8)
    for ax in axs:ax.grid(alpha=.25)
    fig.savefig(OUT/'response.png',dpi=170);plt.close(fig)
    print(json.dumps(result,indent=2))

def native(case,steps,shear_override=None):
    label=case if shear_override is None else f"{case}-kh{shear_override:g}"
    from app.engine import GenesisEngine
    from app.interface import Simulation
    import uipc,trimesh
    cfg=json.loads((ROOT/'benchmarks/shell_1k.json').read_text());cfg['gravity']=[0,0,0]
    mesh=trimesh.load(ROOT/cfg['mesh'],process=False);x=np.asarray(mesh.vertices)
    # mesh loader used by app preserves vertex order for this benchmark; verify after build.
    axis=0 if case.startswith('stretch') else 1
    low=np.flatnonzero(np.isclose(x[:,axis],x[:,axis].min(),atol=1e-9))
    high=np.flatnonzero(np.isclose(x[:,axis],x[:,axis].max(),atol=1e-9))
    cfg['pins']=low.tolist();force=.01
    cfg['forces']=[dict(vertex=int(i),vector=[force/len(high),0,0]) for i in high]
    old_attach=GenesisEngine._attach_ipc_inputs
    def attach(self):
        old_attach(self);c=self.scene.sim.coupler;old=c._add_fem_entities_to_ipc
        def add():
            old();body=c._ipc_objects.find('cloth_0_0')[0]
            for slot in c._ipc_scene.geometries().find(body.geometries().ids()[0]):
                geo=slot.geometry()
                if shear_override is not None:uipc.view(geo.triangles().find('mu'))[:]=shear_override
                elif case.endswith('shear8'):uipc.view(geo.triangles().find('mu'))[:]=8.
        c._add_fem_entities_to_ipc=add
    GenesisEngine._attach_ipc_inputs=attach
    sim=Simulation()
    try:
        initial=sim.configure(cfg);x0=sim.engine.x.copy();assert np.allclose(x,x0)
        c=sim.engine.scene.sim.coupler;rows=[];snaps=[]
        for i in range(steps):
            sim.engine.step();p=sim.engine.x
            rows.append(dict(frame=i+1,loaded_edge_dx_mm=float((p[high,0]-x0[high,0]).mean()*1000),pin_error_m=float(np.linalg.norm(p[low]-x0[low],axis=1).max()),step_ms=sim.engine.step_ms))
            if (i+1)%20==0:snaps.append(p.copy())
        for slot in c._ipc_scene.geometries().find(c._ipc_objects.find('cloth_0_0')[0].geometries().ids()[0]):
            geo=slot.geometry();attrs={k:float(uipc.view(geo.triangles().find(k)).reshape(-1)[0]) for k in ['lambda','mu','strain_rate']}
        result=dict(case=case,label=label,steps=steps,dt=cfg['dt'],total_force_n=force,pins=len(low),loaded_vertices=len(high),attributes=attrs,resolved_config=c._ipc_scene.config(),rows=rows,finite=bool(np.isfinite(p).all()),last20_edge_dx_range_mm=float(np.ptp([r['loaded_edge_dx_mm'] for r in rows[-20:]])),scope='Transient force-loaded 1k coupon, not static equilibrium or material calibration. Original solver tolerances, contact, bending, precision retained.')
        (OUT/(label+'.json')).write_text(json.dumps(result,indent=2,default=str));np.savez(OUT/(label+'.npz'),initial=x0,final=p,faces=np.asarray(initial['faces']),snapshots=np.array(snaps))
        print('RESULT',json.dumps({k:v for k,v in result.items() if k not in ['rows','resolved_config']},indent=2))
    finally:sim.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',default='analysis',choices=['analysis','stretch_legacy','stretch_shear8','shear_legacy','shear_shear8']);p.add_argument('--steps',type=int,default=100);p.add_argument('--shear',type=float);args=p.parse_args()
    if args.shear is not None and (not np.isfinite(args.shear) or args.shear<=0):p.error('--shear must be positive and finite')
    if args.case=='analysis':analysis()
    else:native(args.case,args.steps,args.shear)
