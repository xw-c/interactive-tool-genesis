"""Create a static figure from measured IPC diagnostic results."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'runtime/ipc-profile'
def load(n):return json.loads((OUT/(n+'.json')).read_text())
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(13,4.8),layout='constrained')
keys=['baseline-clean','mas-clean','shear_scaled-clean'];labels=['Current settings','MAS preconditioner','Shear scale diagnostic'];colors=['#3b5b92','#249983','#d79535']
ds=[load(k) for k in keys];med=[d['median_ms'] for d in ds]
axes[0].barh(labels,med,color=colors,height=.55)
for i,d in enumerate(ds):
 axes[0].plot([d['median_ms'],d['p95_ms']],[i,i],color=colors[i],linewidth=2)
 axes[0].plot(d['p95_ms'],i,'|',color=colors[i],markersize=12)
 axes[0].text(d['median_ms']+1,i+.18,f"{d['median_ms']:.1f} ms",fontsize=10)
axes[0].invert_yaxis();axes[0].set_xlabel('Step + readback + JSON (ms)');axes[0].set_title('No native timer / verbose log\nBars: median; whiskers: P95',loc='left',fontweight='bold');axes[0].grid(axis='x',alpha=.15);axes[0].set_axisbelow(True)
summs=load('summary');by={d['label']:d for d in summs}
for key,color,label in [('baseline','#3b5b92','Current settings'),('mas','#249983','MAS')]:
 d=load(key);f=by[key]['frames'];xs=[sum(x['pcg']) for x in f];ys=[r['full_ms'] for r in d['rows']]
 axes[1].scatter(xs,ys,s=22,alpha=.8,color=color,label=label)
axes[1].set_xlabel('Total PCG iterations per step');axes[1].set_ylabel('Step time (ms)');axes[1].set_title('Slow frames track linear iterations\n30 measured steps, verbose diagnostic runs',loc='left',fontweight='bold');axes[1].legend(frameon=False);axes[1].grid(alpha=.15)
fig.suptitle('shell_1k | 1,000 vertices / 3,000 DoFs | RTX 8000 | dt = 5 ms',fontsize=14,fontweight='bold')
fig.savefig(OUT/'profiling.png',dpi=170);plt.close(fig)
