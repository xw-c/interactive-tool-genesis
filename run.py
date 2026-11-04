from pathlib import Path
import os
import argparse
if __name__=='__main__':
    os.chdir(Path(__file__).resolve().parent)
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=3000)
    parser.add_argument('--scene',type=Path,help='Path to the experiment scene configuration')
    args=parser.parse_args()
    if args.scene:os.environ['INTERACTIVE_IPC_PROJECT']=str(args.scene.resolve())
    import uvicorn
    uvicorn.run('app.server:app',host='127.0.0.1',port=args.port)
