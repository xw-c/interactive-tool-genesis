// The frontend only needs this client. Genesis details stay on the server.
export class SimulationAPI {
  constructor(){this.pending=new Map();this.sequence=0;}
  async http(path,method='GET',data){
    const response=await fetch(path,{method,headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
    const value=await response.json();
    if(!response.ok)throw Error(value.message??'请求失败');
    return value;
  }
  getProject(){return this.http('/api/project');}
  saveProject(config){return this.http('/api/project','PUT',config);}
  replaceMesh(obj){return this.http('/api/mesh','PUT',{obj});}
  async replaceAsset(file){
    const response=await fetch('/api/asset?name='+encodeURIComponent(file.name),{method:'PUT',body:file});
    const data=await response.json();if(!response.ok)throw Error(data.message??'资产导入失败');return data;
  }
  connect(onState=()=>{},onConnection=()=>{}){
    return new Promise((resolve,reject)=>{
      this.socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
      this.socket.onopen=()=>{onConnection(true);resolve();};
      this.socket.onerror=()=>reject(Error('无法连接本地仿真服务'));
      this.socket.onmessage=event=>{
        const data=JSON.parse(event.data),p=this.pending.get(data.request_id);this.pending.delete(data.request_id);
        if(data.type==='error')p?.reject(Error(data.message));
        else{onState(data);p?.resolve(data);}
      };
      this.socket.onclose=()=>{onConnection(false);for(const p of this.pending.values())p.reject(Error('仿真服务已断开'));this.pending.clear();};
    });
  }
  request(type,data={}){
    return new Promise((resolve,reject)=>{
      if(this.socket?.readyState!==1)return reject(Error('仿真服务尚未连接'));
      const request_id=++this.sequence;this.pending.set(request_id,{resolve,reject});
      this.socket.send(JSON.stringify({type,request_id,...data}));
    });
  }
  configure(config){return this.request('configure',config===undefined?{}:{config});}
  step(){return this.request('step');}
  setForces(forces){return this.request('forces',{forces});}
  setPins(pins){return this.request('pins',{pins});}
  snapshot(){return this.request('snapshot');}
}
