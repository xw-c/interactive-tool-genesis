import asyncio
import functools
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .config import Setup, read_config
from .worker import SimulationWorker, call_once

ROOT=Path(__file__).resolve().parent.parent

EXECUTOR=None
@asynccontextmanager
async def lifespan(app):
    global EXECUTOR
    EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='shell')
    try:yield
    finally:
        await asyncio.to_thread(EXECUTOR.shutdown,wait=True)
        EXECUTOR=None

app=FastAPI(title='Shell Lab',lifespan=lifespan)
# Override Windows registry MIME associations so browsers execute ES modules.
mimetypes.add_type('text/javascript','.js')
mimetypes.add_type('text/css','.css')
STATIC=ROOT/'static'
if STATIC.is_dir():app.mount('/static',StaticFiles(directory=STATIC),name='static')
async def call(fn,*args):
    return await asyncio.get_running_loop().run_in_executor(EXECUTOR,functools.partial(fn,*args))
@app.get('/')
def index():
    page=STATIC/'index.html'
    if page.is_file():return FileResponse(page)
    return {'service':'Shell Lab backend','project_endpoint':'/api/project','websocket_endpoint':'/ws'}
@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'message':str(exc)},status_code=400)
@app.exception_handler(NotImplementedError)
async def unsupported(request,exc):return JSONResponse({'message':str(exc)},status_code=400)
@app.exception_handler(RuntimeError)
async def unavailable(request,exc):return JSONResponse({'message':str(exc)},status_code=503)
@app.exception_handler(FileNotFoundError)
async def missing(request,exc):return JSONResponse({'message':'找不到配置或 OBJ 文件：'+str(exc)},status_code=400)
@app.get('/api/project')
async def project():return await call(call_once,read_config().is_acceleration_enabled,'project')
async def body(request):
    origin=request.headers.get('origin')
    if origin and origin not in ('http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')):
        raise ValueError('跨来源写入被拒绝')
    raw=await request.body()
    return json.loads(raw)
@app.put('/api/project')
async def save(request:Request):
    config=Setup.model_validate(await body(request))
    return await call(call_once,config.is_acceleration_enabled,'save',config.model_dump(mode='json'))
@app.put('/api/mesh')
async def mesh(request:Request):
    data=await body(request)
    if not isinstance(data,dict) or not isinstance(data.get('obj'),str):raise ValueError('请求应为 {"obj":"OBJ 文件文本"}')
    return await call(call_once,read_config().is_acceleration_enabled,'replace_mesh',data['obj'])
@app.put('/api/asset')
async def asset(request:Request):
    origin=request.headers.get('origin')
    if origin and origin not in ('http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')):
        raise ValueError('跨来源写入被拒绝')
    return await call(call_once,read_config().is_acceleration_enabled,'replace_asset',request.query_params.get('name',''),await request.body())
@app.websocket('/ws')
async def socket(ws:WebSocket):
    origin=ws.headers.get('origin')
    if origin and origin not in ('http://'+ws.headers.get('host',''),'https://'+ws.headers.get('host','')):
        await ws.close(code=1008);return
    await ws.accept();worker=None
    try:
        while True:
            raw=await ws.receive_text();msg={}
            try:
                msg=json.loads(raw)
                if not isinstance(msg,dict):raise ValueError('消息必须是 JSON 对象')
                cmd=msg.get('type')
                if cmd=='configure':
                    config=read_config() if msg.get('config') is None else Setup.model_validate(msg['config'])
                    if worker is not None and (worker.is_acceleration_enabled!=config.is_acceleration_enabled or worker.process.poll() is not None):
                        await call(worker.close);worker=None
                    if worker is None:worker=SimulationWorker(config.is_acceleration_enabled)
                    state=await call(worker.request,'configure',config.model_dump(mode='json'))
                elif worker is None:raise ValueError('请先 configure')
                elif cmd in ('step','snapshot'):state=await call(worker.request,cmd)
                elif cmd=='forces':state=await call(worker.request,cmd,msg.get('forces',[]))
                elif cmd=='pins':state=await call(worker.request,cmd,msg.get('pins',[]))
                else:raise ValueError('未知命令')
                await ws.send_json({'type':'state','request_id':msg.get('request_id'),**state})
            except Exception as exc:
                if isinstance(exc,RuntimeError) and worker is not None:
                    await call(worker.close);worker=None
                await ws.send_json({'type':'error','message':str(exc),'request_id':msg.get('request_id') if isinstance(msg,dict) else None})
    except WebSocketDisconnect:pass
    finally:
        if worker is not None:await call(worker.close)
