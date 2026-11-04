"""One native precision per subprocess; the web server never imports Genesis."""
import os
import pickle
import subprocess
import sys
from pathlib import Path
from run import runtime_environment


class SimulationWorker:
    def __init__(self, is_acceleration_enabled):
        self.is_acceleration_enabled = is_acceleration_enabled
        self.process = subprocess.Popen(
            [sys.executable, '-u', '-m', 'app.worker'],
            cwd=Path(__file__).resolve().parent.parent,
            env=runtime_environment(is_acceleration_enabled),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )

    def request(self, command, *args):
        try:
            pickle.dump((command, args), self.process.stdin)
            self.process.stdin.flush()
            ok, result = pickle.load(self.process.stdout)
        except (EOFError, BrokenPipeError, OSError) as exc:
            raise RuntimeError('Simulation worker exited; apply settings to restart it') from exc
        if not ok:
            kind, message = result
            error = {'ValueError': ValueError, 'FileNotFoundError': FileNotFoundError,
                     'NotImplementedError': NotImplementedError}.get(kind, RuntimeError)
            raise error(message)
        return result

    def close(self):
        # EOF requests orderly destruction on the same thread that initialized CUDA.
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait()
        finally:self.process.stdout.close()


def call_once(is_acceleration_enabled, command, *args):
    worker = SimulationWorker(is_acceleration_enabled)
    try:return worker.request(command, *args)
    finally:worker.close()


def serve():
    # Native CUDA/C++ code also prints to stdout. Keep it off the response pipe.
    response = os.fdopen(os.dup(sys.stdout.fileno()), 'wb')
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        import genesis as gs
        from .interface import Simulation, project_info, save_project, replace_mesh, replace_asset
    except Exception as exc:
        pickle.dump((False, (type(exc).__name__, str(exc))), response);response.flush()
        raise
    sim = Simulation()
    try:
        while True:
            try:command, args = pickle.load(sys.stdin.buffer)
            except EOFError:break
            try:
                if command == 'project':result = project_info()
                elif command == 'save':result = save_project(*args)
                elif command == 'replace_mesh':result = replace_mesh(*args)
                elif command == 'replace_asset':result = replace_asset(*args)
                elif command == 'configure':result = sim.configure(*args)
                elif sim.engine is None:raise ValueError('请先 configure')
                elif command == 'step':result = sim.engine.step()
                elif command == 'forces':result = sim.engine.set_forces(*args)
                elif command == 'pins':result = sim.set_pins(*args)
                elif command == 'snapshot':result = sim.engine.state(True)
                else:raise ValueError('Unknown worker command')
                reply = (True, result)
            except Exception as exc:
                reply = (False, (type(exc).__name__, str(exc)))
            pickle.dump(reply, response);response.flush()
    finally:
        try:sim.close()
        finally:
            if gs._initialized:gs.destroy()
            response.close()


if __name__ == '__main__':serve()
