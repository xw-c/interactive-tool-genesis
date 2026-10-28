"""Validated paper-bag setup and motion; all simulation uses app.engine."""
import argparse
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Setup
from app.engine import GenesisEngine
import genesis as gs

HANDLES = ('/root/Rope001', '/root/Rope002')


def grip_nodes(positions):
    return np.flatnonzero((positions[:, 2] > positions[:, 2].max()-.006)
        & (abs(positions[:, 0]-np.median(positions[:, 0])) < .012))


def configure_bag(engine):
    engine.place_on_ground()
    for handle in HANDLES:
        engine.set_attachment_parameters(handle+'/attachment', 3000., disable_contact=True)
        engine.add_position_drive(handle, grip_nodes, 1e5)
        for other in HANDLES:
            engine.disable_contact(handle, other)


def displacement(t):
    def smooth(value):
        value = np.clip(value, 0., 1.)
        return value*value*value*(10-15*value+6*value*value)
    return np.array([.05*smooth(t-6), 0., .25*smooth((t-1)/3)])


def create_engine(usd, config, output):
    if isinstance(config, Setup):
        setup = config
    else:
        setup = Setup.model_validate_json(Path(config).read_text(encoding='utf-8-sig'))
        setup = setup.model_copy(update=dict(body_type='volume',dt=.001,
            contact_d_hat_m=.0005,contact_mode='full',step_mode='libuipc'))
    setup = setup.model_copy(update={'mesh':str(Path(usd).resolve())})
    options = gs.options.IPCCouplerOptions(contact_d_hat=setup.contact_d_hat_m,
        contact_enable=setup.contact_mode!='none',contact_friction_enable=setup.contact_mode!='none',
        contact_resistance=1e8, contact_eps_velocity=.001,
        newton_tolerance=1e-4, newton_semi_implicit_enable=False, linear_system_tolerance=1e-6)
    engine = GenesisEngine(setup, configure=configure_bag, ipc_options=options, output=output)
    rest = {path: engine.get_simulation_positions(path)[ids] for path, ids in engine.drive_nodes.items()}
    def motion(engine, t):
        for path, positions in rest.items():
            engine.set_position_targets(path, positions+displacement(t))
    engine.set_motion(motion)
    return engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usd', type=Path, default=Path(__file__).parent/'paperbag/PaperBag.usd')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_suffix('.json'))
    parser.add_argument('--output', type=Path, default=Path('runtime/paperbag'))
    parser.add_argument('--duration', type=float, default=9.)
    args = parser.parse_args()
    if not np.isfinite(args.duration) or args.duration <= 0:
        parser.error('Expected positive duration')
    engine = create_engine(args.usd, args.config, args.output)
    try:
        for step in range(round(args.duration/engine.config.dt)):
            engine.step(snapshot=False)
            if (step+1) % 1000 == 0:
                print(f'{engine.time:.1f} s', flush=True)
    finally:
        engine.close()


if __name__ == '__main__':
    main()
