"""Visual evidence of independent shear response on the actual 1000-vertex triangle mesh.
Run cloth_response.py --case shear_legacy --shear {40000,80,8} first, each in a fresh process.
"""
import json,sys
from pathlib import Path
import numpy as np
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation,PillowWriter
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'runtime/cloth-response'
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
keys=['shear_legacy-kh40000','shear_legacy-kh80','shear_legacy-kh8']
arrays=[np.load(OUT/(k+'.npz')) for k in keys]
data=[json.loads((OUT/(k+'.json')).read_text()) for k in keys]
x=arrays[0]['initial'];faces=arrays[0]['faces'];center=x.mean(axis=0)
assert faces.shape==(1872,3) and x.shape==(1000,3)
for a,d in zip(arrays,data):
    assert np.array_equal(a['initial'],x) and np.array_equal(a['faces'],faces)
    assert json.loads(d['resolved_config'])==json.loads(data[0]['resolved_config'])
    assert d['attributes']['lambda']==data[0]['attributes']['lambda'] and d['attributes']['strain_rate']==100
    assert all(r['pin_error_m']==0 for r in d['rows'])
    assert d['total_force_n']==.01 and d['steps']==100 and d['dt']==.005 and d['pins']==40
    edge=np.flatnonzero(np.isclose(x[:,1],x[:,1].max(),atol=1e-9))
    assert np.isclose((a['final'][edge,0]-x[edge,0]).mean()*1000,d['rows'][-1]['loaded_edge_dx_mm'],atol=1e-10)

p0=x[faces];Dm=np.stack([(p0[:,1]-p0[:,0])[:,:2],(p0[:,2]-p0[:,0])[:,:2]],axis=-1)
IB=np.linalg.inv(Dm)
area=np.linalg.norm(np.cross(p0[:,1]-p0[:,0],p0[:,2]-p0[:,0]),axis=1)/2

def material_measures(p):
    q=p[faces];Ds=np.stack([q[:,1]-q[:,0],q[:,2]-q[:,0]],axis=-1);F=Ds@IB
    lens=np.linalg.norm(F,axis=1)
    cosine=np.sum(F[:,:,0]*F[:,:,1],axis=1)/(lens[:,0]*lens[:,1])
    angle=np.rad2deg(np.arcsin(np.clip(cosine,-1,1)))
    return angle,lens-1

rows=[]
for a,d in zip(arrays,data):
    angle,stretch=material_measures(a['final'])
    rows.append(dict(shear_stiffness=d['attributes']['mu'],edge_dx_mm=d['rows'][-1]['loaded_edge_dx_mm'],area_weighted_abs_fiber_angle_change_deg=float(np.sum(abs(angle)*area)/area.sum()),fiber_angle_change_p95_deg=float(np.percentile(abs(angle),95)),positive_fiber_extension_p95_pct=float(np.percentile(np.maximum(stretch,0),95)*100),last20_edge_dx_range_mm=d['last20_edge_dx_range_mm']))
summary=dict(vertices=len(x),triangles=len(faces),same_mesh_and_initial_state=True,same_native_scene_config=True,stretch_stiffness=data[0]['attributes']['lambda'],bending_stiffness_nm=5e-8,strain_limiting_alpha=100,total_force_n=.01,dt=.005,steps=100,fixed_vertices=40,results=rows,scope='Native CUDA transient 3D deformation, not static equilibrium or a pure-shear modulus measurement. Each image is an actual saved simulation state, without displacement amplification.')
(OUT/'triangle_shear_summary.json').write_text(json.dumps(summary,indent=2))

# Fixed camera/limits/colour scale for all cases and all saved times.
allp=np.concatenate([(np.concatenate([a['initial'][None],a['snapshots']],axis=0)-center)*1000 for a in arrays],axis=0)
lo=allp.min(axis=(0,1));hi=allp.max(axis=(0,1));pad=np.maximum((hi-lo)*.06,5);lo-=pad;hi+=pad
vmin,vmax=0,2
cmap=plt.get_cmap('YlOrRd');norm=plt.Normalize(vmin,vmax)
fixed=np.flatnonzero(np.isclose(x[:,1],x[:,1].min(),atol=1e-9))
loaded=np.flatnonzero(np.isclose(x[:,1],x[:,1].max(),atol=1e-9))
fig=plt.figure(figsize=(14,5.4),layout='constrained')
axs=[fig.add_subplot(1,3,i+1,projection='3d') for i in range(3)]

def draw(frame):
    t=frame*.1
    for i,(ax,a,d) in enumerate(zip(axs,arrays,data)):
        ax.clear();p=a['initial'] if frame==0 else a['snapshots'][frame-1];xyz=(p-center)*1000
        angle,_=material_measures(p)
        polys=Poly3DCollection(xyz[faces],facecolors=cmap(norm(abs(angle))),edgecolors=(.15,.15,.15,.65),linewidths=.22,alpha=.95)
        ax.add_collection3d(polys)
        ax.scatter(*xyz[fixed].T,c='#0066cc',s=7,depthshade=False)
        ax.scatter(*xyz[loaded].T,c='#009944',s=5,depthshade=False)
        force_origin=xyz[loaded].mean(axis=0)
        ax.quiver(*force_origin,35,0,0,color='#009944',arrow_length_ratio=.25,linewidth=2)
        ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),zlim=(lo[2],hi[2]),xlabel='X / mm',ylabel='Y / mm',zlabel='Z / mm')
        from matplotlib.ticker import MaxNLocator
        ax.xaxis.set_major_locator(MaxNLocator(4));ax.yaxis.set_major_locator(MaxNLocator(4));ax.zaxis.set_major_locator(MaxNLocator(3))
        ax.view_init(elev=52,azim=-78);ax.set_proj_type('ortho');ax.set_box_aspect(hi-lo)
        dx=0 if frame==0 else d['rows'][frame*20-1]['loaded_edge_dx_mm']
        ax.set_title(f"剪切 stiffness = {d['attributes']['mu']:g} N/m\n加载边 X 位移 {dx:.2f} mm",fontsize=12)
        ax.tick_params(labelsize=8);ax.set_zticks([]);ax.set_zlabel('')
    fig.suptitle(f'同一块三角形布：只改剪切 stiffness  |  t = {t:.1f} s\n蓝点固定，绿点侧拉 0.01 N；形变未放大，三图相同视角和比例',fontsize=13)
    return []
draw(5)
sm=plt.cm.ScalarMappable(norm=norm,cmap=cmap);fig.colorbar(sm,ax=axs,shrink=.55,pad=.01,label='材料夹角变化 / 度（≥2° 同色）')
fig.savefig(OUT/'triangle_shear_comparison.png',dpi=160)
anim=FuncAnimation(fig,draw,frames=[0,1,2,3,4,5,5,5],interval=600,blit=False)
anim.save(OUT/'triangle_shear_comparison.gif',writer=PillowWriter(fps=2),dpi=105)
plt.close(fig)

# A simple triangle shows why mesh-edge length is not the same as fiber stretch.
fig,axs=plt.subplots(1,2,figsize=(8,3.8),layout='constrained')
for ax,q,title in zip(axs,[0,.5],['初始三角形','材料两方向不伸长，但夹角改变']):
    p=np.array([[0,0],[1,0],[q,np.sqrt(1-q*q)]])
    ax.plot(*p[[0,1]].T,c='tab:blue',lw=3,label='材料方向 u，长度 1')
    ax.plot(*p[[0,2]].T,c='tab:orange',lw=3,label='材料方向 v，长度 1')
    ax.plot(*p[[1,2]].T,c='black',lw=1.5,ls='--',label='网格第三边，可以变长或缩短')
    ax.scatter(*p.T,c='black',s=20);ax.set_aspect('equal');ax.set(xlim=(-.1,1.1),ylim=(-.1,1.1),title=title)
    ax.text(.08,.12,f'夹角 {np.rad2deg(np.arccos(q)):.0f}°',fontsize=12)
    ax.axis('off')
axs[1].legend(loc='upper center',bbox_to_anchor=(.5,-.02),fontsize=9)
fig.suptitle('示意图：三角形并不等于三根不可伸长的杆（非仿真帧）',fontsize=12)
fig.savefig(OUT/'triangle_shear_explanation.png',dpi=160);plt.close(fig)
print(json.dumps(summary,indent=2))
