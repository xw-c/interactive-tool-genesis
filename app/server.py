import asyncio
import functools
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .interface import ROOT, Simulation, project_info, save_project, replace_mesh

EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='shell')
app=FastAPI(title='Shell Lab')
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
@app.exception_handler(FileNotFoundError)
async def missing(request,exc):return JSONResponse({'message':'找不到配置或 OBJ 文件：'+str(exc)},status_code=400)
@app.get('/api/project')
async def project():return await call(project_info)
async def body(request):
    origin=request.headers.get('origin')
    if origin and origin not in ('http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')):
        raise ValueError('跨来源写入被拒绝')
    raw=await request.body()
    return json.loads(raw)
@app.put('/api/project')
async def save(request:Request):return await call(save_project,await body(request))
@app.put('/api/mesh')
async def mesh(request:Request):
    data=await body(request)
    if not isinstance(data,dict) or not isinstance(data.get('obj'),str):raise ValueError('请求应为 {"obj":"OBJ 文件文本"}')
    return await call(replace_mesh,data['obj'])
@app.websocket('/ws')
async def socket(ws:WebSocket):
    origin=ws.headers.get('origin')
    if origin and origin not in ('http://'+ws.headers.get('host',''),'https://'+ws.headers.get('host','')):
        await ws.close(code=1008);return
    await ws.accept();sim=Simulation()
    try:
        while True:
            raw=await ws.receive_text();msg={}
            try:
                msg=json.loads(raw)
                if not isinstance(msg,dict):raise ValueError('消息必须是 JSON 对象')
                cmd=msg.get('type')
                if cmd!='configure' and sim.engine is None:raise ValueError('请先 configure')
                if cmd=='configure':state=await call(sim.configure,msg.get('config'))
                elif cmd=='step':state=await call(sim.engine.step)
                elif cmd=='forces':state=await call(sim.engine.set_forces,msg.get('forces',[]))
                elif cmd=='pins':state=await call(sim.set_pins,msg.get('pins',[]))
                elif cmd=='snapshot':state=await call(sim.engine.state,True)
                else:raise ValueError('未知命令')
                await ws.send_json({'type':'state','request_id':msg.get('request_id'),**state})
            except Exception as exc:
                await ws.send_json({'type':'error','message':str(exc),'request_id':msg.get('request_id') if isinstance(msg,dict) else None})
    except WebSocketDisconnect:pass
    finally:await call(sim.close)
