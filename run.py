from pathlib import Path
import os
import argparse

def runtime_environment(is_acceleration_enabled):
    """Select the package before starting a Python process that imports Genesis."""
    env = os.environ.copy()
    precision = '32' if is_acceleration_enabled else '64'
    package = env.get('INTERACTIVE_IPC_FP'+precision+'_PACKAGE') or env.get('INTERACTIVE_IPC_UIPC_PACKAGE')
    if package:
        package = Path(package).resolve()
        if not (package/'uipc/__init__.py').is_file():
            raise RuntimeError('Incomplete FP'+precision+' package: '+str(package))
        env['INTERACTIVE_IPC_UIPC_PACKAGE'] = str(package)
        # Dynamic-library selection must also precede process startup.
        env['LD_LIBRARY_PATH'] = str(package/'uipc/_native')+os.pathsep+env.get('LD_LIBRARY_PATH','')
    env['INTERACTIVE_IPC_ACCELERATION_ENABLED'] = '1' if is_acceleration_enabled else '0'
    return env

def configure_runtime(is_acceleration_enabled):
    os.environ.update(runtime_environment(is_acceleration_enabled))

if __name__=='__main__':
    os.chdir(Path(__file__).resolve().parent)
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=3000)
    parser.add_argument('--scene',type=Path,help='Path to the experiment scene configuration')
    parser.add_argument('--enable-acceleration',action='store_true',help='Use FP32 and enable both IPC pseudo optimizations; requires an FP32 package')
    args=parser.parse_args()
    configure_runtime(args.enable_acceleration)
    if args.scene:os.environ['INTERACTIVE_IPC_PROJECT']=str(args.scene.resolve())
    import uvicorn
    uvicorn.run('app.server:app',host='127.0.0.1',port=args.port)
