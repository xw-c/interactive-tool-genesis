// Frontend entry module.
import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {SimulationAPI} from './api.js';
const $=id=>document.getElementById(id);
const api=new SimulationAPI();
let projectSpec=null,fields=[];
function buildForm(data){
 projectSpec=data;fields=[];$('material-fields').replaceChildren();$('model-fields').replaceChildren();
 const label=document.createElement('label');label.textContent='弯曲模型';
 const select=document.createElement('select');select.id='bending_model';
 for(const entry of data.models.bending_model){const option=document.createElement('option');option.value=entry.value??'';option.textContent=entry.label;select.append(option);}
 select.onchange=updateMaterialFields;label.append(select);$('model-fields').append(label);
 for(const [id,property] of Object.entries(data.material_schema.properties)){
  const number=property.type==='number'?property:property.anyOf?.find(x=>x.type==='number');if(!number)continue;
  fields.push(id);const label=document.createElement('label');label.textContent=property.title;label.dataset.field=id;
  const input=document.createElement('input');Object.assign(input,{id,type:'number',step:'any'});
  if(number.minimum!==undefined)input.min=number.minimum;if(number.maximum!==undefined)input.max=number.maximum;
  input.oninput=()=>$('dirty').textContent='有未应用的参数修改。';label.append(input);$('material-fields').append(label);
 }
 $('mesh-info').textContent=data.mesh.file+' · '+data.mesh.vertices+' 顶点 / '+data.mesh.triangles+' 三角形';
 showContactCheck(data.contact_check);
 $('capability').textContent='Genesis '+data.genesis_version+' · IPC / CUDA';
}
function showContactCheck(check){
 if(!check)return;
 $('contact-note').textContent=check.warnings.length?'⚠ Warning：'+check.warnings.join(' '):'接触距离初始化尺度检查通过。';
 $('contact-note').title=check.scope;
}
function updateMaterialFields(){
 const volume=$('body_type').value==='volume';
 for(const id of fields)$(id).parentElement.hidden=volume&&['thickness_m','bending_rigidity_nm'].includes(id)||id==='bending_rigidity_nm'&&!$('bending_model').value;
 $('bending_model').parentElement.hidden=volume;
 $('tet-options').hidden=!volume;
 $('model-name').textContent=projectSpec.models[$('body_type').value];
}
let config=null,positions=[],faces=[],forces=[],selected=null,pins=[],playing=false,busy=false;
let generation=0;let activeConfig=null;let lastFrameAt=null;
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
function playLabel(){lastFrameAt=null;$('play').textContent=playing?'Ⅱ 暂停':'▶ 开始';}
function pause(){playing=false;playLabel();}
function setBusy(b){busy=b;for(const id of ['apply','step','reset','import','body_type','contact_d_hat_m','tet_max_volume_m3','upload-obj','save-project','reload-project'])$(id).disabled=b;for(const id of ['bending_model',...fields]){if($(id))$(id).disabled=b;}}
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));$('canvas').append(renderer.domElement);
const scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x0d1722,.65);
const camera=new THREE.PerspectiveCamera(40,1,.001,10);camera.up.set(0,0,1);camera.position.set(.36,-.46,.37);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,0,.1);controls.enableDamping=true;controls.minDistance=.06;controls.maxDistance=2;
scene.add(new THREE.HemisphereLight(0xd0f7ff,0x1d2d37,2));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(.3,-.2,.8);scene.add(light);
const grid=new THREE.GridHelper(2,80,0x345065,0x1b3042);grid.rotation.x=Math.PI/2;scene.add(grid);
const floor=new THREE.Mesh(new THREE.PlaneGeometry(2,2),new THREE.MeshBasicMaterial({color:0x101e2b,transparent:true,opacity:.6,side:THREE.DoubleSide}));floor.position.z=-.0002;scene.add(floor);
const axes=new THREE.AxesHelper(.055);axes.position.set(-.15,-.15,.0005);scene.add(axes);
let mesh,wire,dots;const markers=new THREE.Group();scene.add(markers);const arrows=new THREE.Group();scene.add(arrows);
const raycaster=new THREE.Raycaster();const pointer=new THREE.Vector2();const project=new THREE.Vector3();
function disposeObject(obj){if(!obj)return;obj.traverse?.(o=>{o.geometry?.dispose();if(Array.isArray(o.material))o.material.forEach(m=>m.dispose());else o.material?.dispose();});scene.remove(obj);}
function rebuild(){disposeObject(wire);disposeObject(dots);disposeObject(mesh);
 const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(positions.flat(),3));geo.setIndex(faces.flat());geo.computeVertexNormals();
 mesh=new THREE.Mesh(geo,new THREE.MeshStandardMaterial({color:0x399eae,side:THREE.DoubleSide,roughness:.62,metalness:.18}));scene.add(mesh);
 wire=new THREE.Mesh(geo.clone(),new THREE.MeshBasicMaterial({color:0x82e2e6,wireframe:true,transparent:true,opacity:.28}));scene.add(wire);
 dots=new THREE.Points(geo.clone(),new THREE.PointsMaterial({color:0x6fdbe0,size:.0018,sizeAttenuation:true}));scene.add(dots);updateMarks();
}
function updateGeometry(){if(!mesh)return;for(const obj of [mesh,wire,dots]){obj.geometry.attributes.position.array.set(positions.flat());obj.geometry.attributes.position.needsUpdate=true;obj.geometry.computeBoundingSphere();}mesh.geometry.computeVertexNormals();updateMarks();}
function clearGroup(group){for(const o of [...group.children]){group.remove(o);o.traverse(c=>{c.geometry?.dispose();c.material?.dispose();});}}
function updateMarks(){clearGroup(markers);clearGroup(arrows);for(const i of pins){if(!positions[i])continue;const ball=new THREE.Mesh(new THREE.SphereGeometry(.0022,12,8),new THREE.MeshBasicMaterial({color:0xe8bb6a}));ball.position.fromArray(positions[i]);markers.add(ball);}if(selected!==null&&positions[selected]){const ball=new THREE.Mesh(new THREE.SphereGeometry(.0027,12,8),new THREE.MeshBasicMaterial({color:0xffffff}));ball.position.fromArray(positions[selected]);markers.add(ball);}
 for(const f of forces){if(!positions[f.vertex])continue;const v=new THREE.Vector3(...f.vector);if(v.length()===0)continue;const length=.025+Math.min(.035,v.length()*2);const arrow=new THREE.ArrowHelper(v.normalize(),new THREE.Vector3(...positions[f.vertex]),length,0x60e7b9,.008,.004);arrows.add(arrow);}updateSelection();}
function updateSelection(){$('selected').textContent=selected===null?'—':'#'+selected;$('position').textContent=selected===null?'在网格上点击一个顶点':positions[selected]?.map(v=>v.toFixed(5)).join(', ')+' m';$('pin').disabled=selected===null||busy;$('add-force').disabled=selected===null||pins.includes(selected)||busy;$('pin').textContent=pins.includes(selected)?'取消固定':'固定顶点';}
let pointerStart;
renderer.domElement.addEventListener('pointerdown',e=>pointerStart=[e.clientX,e.clientY]);
renderer.domElement.addEventListener('pointerup',e=>{if(!mesh||!pointerStart||Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>4||e.button!==0)return;const r=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);const hits=raycaster.intersectObject(mesh);if(!hits.length)return;const ids=[hits[0].face.a,hits[0].face.b,hits[0].face.c];let best=null,distance=22;for(const i of ids){project.fromArray(positions[i]).project(camera);const d=Math.hypot((project.x+1)*r.width/2+r.left-e.clientX,(1-project.y)*r.height/2+r.top-e.clientY);if(d<distance){distance=d;best=i;}}if(best!==null){selected=best;updateMarks();}});
new ResizeObserver(()=>{const w=$('canvas').clientWidth,h=$('canvas').clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}).observe($('canvas'));
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera);});
function renderState(s){
 showContactCheck(s.contact_check);
 if(s.volume_mesh)$('tet-result').textContent=s.volume_mesh.tetrahedra?'Genesis / TetGen：'+s.volume_mesh.simulation_vertices+' 个求解顶点，'+s.volume_mesh.surface_vertices+' 个表面顶点，'+s.volume_mesh.tetrahedra+' 个四面体':'薄壳使用表面三角形';
 positions=s.positions;pins=s.pins;faces=s.faces??faces;
 if(s.faces){activeConfig=s.config;config=structuredClone(s.config);forces=structuredClone(s.config.forces);$('mesh-info').textContent=s.mesh.file+' · '+s.mesh.vertices+' 表面顶点 / '+s.mesh.triangles+' 三角形';rebuild();renderForces();}else updateGeometry();
 $('time').textContent=s.time.toFixed(3)+' s';$('frame').textContent=s.frame;$('backend').textContent=s.backend;if(s.frame===0)$('fps').textContent='—';
}
async function connect(){
 try{const data=await api.getProject();buildForm(data);fillConfig(data.config);
  await api.connect(renderState,on=>{$('led').classList.toggle('on',on);$('connection').textContent=on?'本地服务已连接':'连接已断开';if(!on){pause();status('服务已断开，请重启后刷新',true);}});
  await configure(data.config);
 }catch(e){status(e.message,true);}
}
function readConfig(){
 for(const input of document.querySelectorAll('.left input[type=number]')){if(input.parentElement.hidden)continue;if(!input.checkValidity()||input.value==='')throw Error('请填写有效参数：'+input.parentElement.textContent);}
 const material={...config?.material};
 material.bending_model=$('bending_model').value||null;
 for(const id of fields)if(!$(id).parentElement.hidden)material[id]=+$(id).value;
 return {...config,mesh:config.mesh,body_type:$('body_type').value,tet_max_volume_m3:+$('tet_max_volume_m3').value,material,contact_d_hat_m:+$('contact_d_hat_m').value,dt:+$('dt').value,gravity:[config.gravity[0],config.gravity[1],+$('gravity').value],pins:[...pins],forces:structuredClone(forces)};
}
function fillConfig(c){
 config=structuredClone(c);$('body_type').value=c.body_type;
 $('bending_model').value=c.material.bending_model??'';
 for(const id of fields)$(id).value=c.material[id]??projectSpec.material_schema.properties[id].default;
 $('tet_max_volume_m3').value=c.tet_max_volume_m3??-1;
 $('contact_d_hat_m').value=c.contact_d_hat_m??.0005;
 $('dt').value=c.dt;$('gravity').value=c.gravity[2];updateMaterialFields();
}

async function configure(c){pause();generation++;setBusy(true);status('构建仿真场景… IPC 首次启动需要编译内核');try{const s=await api.configure(c);selected=null;fillConfig(s.config);updateMarks();status('场景已就绪，可选中顶点添加力');$('dirty').textContent='设置已应用。';return true;}catch(e){if(activeConfig)fillConfig(activeConfig);status(e.message,true);return false;}finally{setBusy(false);updateSelection();}}
async function step(){
 const start=performance.now();await api.step();const now=performance.now();
 const elapsed=playing&&lastFrameAt!==null?now-lastFrameAt:now-start;
 lastFrameAt=playing?now:null;
 $('fps').textContent=elapsed>0?(1000/elapsed).toFixed(1)+' FPS':'—';
}
async function loop(token){if(!playing||token!==generation)return;try{await step();if(playing&&token===generation)setTimeout(()=>loop(token),0);}catch(e){pause();status(e.message,true);}}
$('play').onclick=()=>{if(busy)return;playing=!playing;playLabel();if(playing)loop(++generation);};
$('step').onclick=async()=>{pause();generation++;setBusy(true);try{await step();}catch(e){status(e.message,true);}finally{setBusy(false);updateSelection();}};
$('apply').onclick=()=>{try{configure(readConfig());}catch(e){status(e.message,true);}};
$('reset').onclick=()=>{if(config)configure({...config,forces:structuredClone(forces),pins:[...pins]});};
$('body_type').onchange=()=>{updateMaterialFields();$('dirty').textContent='类型尚未应用：请点击「应用设置并重置」。当前 OBJ 和参数保留；体积要求封闭网格，变更模型后请重新选择固定点与受力点。';};
$('pin').onclick=async()=>{
 if(selected===null||busy)return;pause();generation++;setBusy(true);
 const next=new Set(pins);if(next.has(selected))next.delete(selected);else next.add(selected);
 try{const s=await api.setPins([...next]);selected=null;fillConfig(s.config);updateMarks();status('固定点已更新，仿真已重置');}catch(e){status(e.message,true);}finally{setBusy(false);updateSelection();}
};
$('clear-selection').onclick=()=>{selected=null;updateMarks();};
async function syncForces(next){try{await api.setForces(next);forces=next;config.forces=structuredClone(next);renderForces();updateMarks();status('点力已更新');}catch(e){status(e.message,true);renderForces();}}
function renderForces(){$('force-count').textContent=forces.length;const list=$('forces');list.replaceChildren();if(!forces.length){list.innerHTML='<div class="empty">尚未添加外力</div>';return;}forces.forEach((f,index)=>{const card=document.createElement('div');card.className='force-card';const content=document.createElement('div');const title=document.createElement('strong');title.textContent='顶点 #'+f.vertex;title.style.cursor='pointer';title.onclick=()=>{selected=f.vertex;updateMarks();};const vec=document.createElement('div');vec.className='vector';for(let axis=0;axis<3;axis++){const l=document.createElement('label');l.textContent=['Fx','Fy','Fz'][axis];const input=document.createElement('input');Object.assign(input,{type:'number',value:f.vector[axis],step:'any'});input.onchange=()=>{if(!input.checkValidity()||input.value===''){status('请填写有限的力分量',true);return;}const next=structuredClone(forces);next[index].vector[axis]=+input.value;syncForces(next);};l.append(input);vec.append(l);}const magnitude=document.createElement('span');magnitude.textContent='|F| = '+Math.hypot(...f.vector).toExponential(2)+' N';content.append(title,vec,magnitude);const remove=document.createElement('button');remove.textContent='×';remove.title='删除力';remove.onclick=()=>syncForces(forces.filter((_,i)=>i!==index));card.append(content,remove);list.append(card);});}
$('add-force').onclick=()=>{if(busy||selected===null||pins.includes(selected))return;const inputs=['fx','fy','fz'].map($);if(inputs.some(i=>!i.checkValidity()||i.value===''))return status('请填写有效的力分量',true);const vector=inputs.map(i=>+i.value);if(vector.every(v=>v===0))return status('请输入非零力矢量',true);syncForces([...forces,{vertex:selected,vector}]);};
$('clear-forces').onclick=()=>syncForces([]);
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=async()=>{try{const s=await api.snapshot();download('shell-config.json',JSON.stringify(s.config,null,2),'application/json');status('已导出当前生效配置');}catch(e){status(e.message,true);}};
$('snapshot').onclick=async()=>{try{const s=await api.snapshot();download('shell-frame-'+s.frame+'.obj',s.positions.map(p=>'v '+p.join(' ')).join('\n')+'\n'+s.faces.map(f=>'f '+f.map(i=>i+1).join(' ')).join('\n')+'\n','text/plain');}catch(e){status(e.message,true);}};
$('import').onclick=()=>$('file').click();$('file').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;const c=JSON.parse(await file.text());await configure(c);}catch(e){status(e.message,true);}finally{e.target.value='';}};
for(const i of document.querySelectorAll('.left input[type=number]'))i.addEventListener('input',()=>$('dirty').textContent='有未应用的参数修改。');
$('help').onclick=()=>$('help-dialog').showModal();$('close-help').onclick=()=>$('help-dialog').close();
$('save-project').onclick=async()=>{
 pause();generation++;setBusy(true);try{const next=readConfig();await api.saveProject(next);const ok=await configure(next);if(ok)status('已写入 scene.json，并应用到当前会话');}catch(e){status(e.message,true);}finally{setBusy(false);updateSelection();}
};
$('reload-project').onclick=async()=>{pause();generation++;setBusy(true);try{const next=await api.getProject();buildForm(next);if(await configure(next.config))status('已重新读取 scene.json 和 OBJ');}catch(e){status(e.message,true);}finally{setBusy(false);}};
$('upload-obj').onclick=()=>$('obj-file').click();
$('obj-file').onchange=async e=>{const file=e.target.files[0];if(!file)return;pause();generation++;setBusy(true);try{await api.replaceMesh(await file.text());const next=await api.getProject();buildForm(next);if(await configure(next.config))status('OBJ 已写入 model.obj，固定点与点力已清空');}catch(error){status(error.message,true);}finally{e.target.value='';setBusy(false);updateSelection();}};
connect();
