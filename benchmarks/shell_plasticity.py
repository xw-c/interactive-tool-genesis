"""Native shell plasticity validation using the same point force as the web demo."""
import argparse,json
from pathlib import Path
import numpy as np
from app.config import Setup
from app.engine import GenesisEngine

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'benchmarks/shell_plasticity'

def run_case(plastic=True,yield_deg=10.,hardening=0.,load_steps=20,release_steps=160):
    if load_steps<1 or release_steps<1:raise ValueError('Step counts must be positive')
    config=Setup.model_validate_json((EXAMPLE/'plastic.json').read_text(encoding='utf-8'))
    config.material.bending_plasticity=plastic
    config.material.bending_yield_angle_deg=yield_deg
    config.material.bending_plastic_hardening=hardening
    config=Setup.model_validate(config.model_dump())
    engine=GenesisEngine(config)
    angles=[];positions=[];fixed_error=0.
    try:
        rest=np.asarray(engine.state()['positions'])
        # Public IDs are loader-dependent: fail clearly if the fixture is reordered.
        expected=np.array([[0,.05,.3],[0,0,.3],[.1,0,.3],[0,-.05,.3]])
        if not np.allclose(rest,expected):raise RuntimeError('Fixture vertex order changed; update pins and force IDs')
        for frame in range(load_steps+release_steps):
            if frame==load_steps:engine.set_forces([])
            x=np.asarray(engine.step()['positions'])
            positions.append(x)
            fixed_error=max(fixed_error,float(np.max(np.abs(x[:3]-rest[:3]))))
            angles.append(float(np.degrees(np.arctan2(x[3,2]-.3,-x[3,1]))))
        tail=angles[-min(30,release_steps):]
        result=dict(plastic=plastic,yield_deg=yield_deg,hardening=hardening,
            force_n=[0,0,.5],dt=config.dt,load_steps=load_steps,release_steps=release_steps,
            release_angle_deg=angles[load_steps-1],final_angle_deg=angles[-1],
            final_30_range_deg=float(np.ptp(tail)),max_fixed_error_m=fixed_error,
            finite=bool(np.isfinite(positions).all()))
        return result,np.asarray(angles),np.asarray(positions)
    finally:engine.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-steps',type=int,default=20)
    parser.add_argument('--release-steps',type=int,default=160)
    args=parser.parse_args()
    output=ROOT/'runtime/shell-plasticity';output.mkdir(parents=True,exist_ok=True)
    results=[];arrays={}
    for name,plastic,y,h in [('elastic',False,10,0),('plastic',True,10,0),('below_yield',True,90,0),('hardening',True,10,1)]:
        result,angles,positions=run_case(plastic,y,h,args.load_steps,args.release_steps)
        results.append(dict(name=name,**result))
        arrays[name+'_angles_deg']=angles;arrays[name+'_positions']=positions
        print(name,json.dumps(result),flush=True)
    (output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    np.savez_compressed(output/'states.npz',**arrays)

if __name__=='__main__':main()
