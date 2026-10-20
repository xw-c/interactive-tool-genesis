const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.hidden=false;this.type='';this.value='';this.checked=false;}
 set id(id){this._id=id;elements.set(id,this);}get id(){return this._id;}
 append(child){this.children.push(child);child.parentElement=this;if(child.id)elements.set(child.id,child);}
 replaceChildren(){this.children=[];}
 checkValidity(){return true;}
}
const document={getElementById(id){if(!elements.has(id)){const e=new Element('div');e.id=id;elements.set(id,e);}return elements.get(id);},
 createElement(tag){return new Element(tag);},querySelectorAll(){return [...elements.values()].filter(e=>e.type==='number');}};
const code=fs.readFileSync('static/app.js','utf8');
const prefix=code.slice(code.indexOf('const $='),code.indexOf('let config=null'));
const read=code.slice(code.indexOf('function readConfig()'),code.indexOf('async function configure(c)'));
const spec=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const ctx=vm.createContext({document,structuredClone,console,spec,SimulationAPI:class {}});
vm.runInContext(prefix+'\nlet config=null,pins=[],forces=[];\n'+read,ctx);
vm.runInContext('buildForm(spec);fillConfig(spec.config)',ctx);
const get=id=>document.getElementById(id);
assert.equal(get('bending_plasticity').checked,false);
assert.equal(get('bending_yield_angle_deg').parentElement.hidden,true);
assert.equal(get('volume_plasticity').parentElement.hidden,true);
get('bending_plasticity').checked=true;get('bending_yield_angle_deg').value='15';
vm.runInContext('updateMaterialFields()',ctx);
assert.equal(get('bending_yield_angle_deg').parentElement.hidden,false);
let config=vm.runInContext('readConfig()',ctx);
assert.equal(config.material.bending_plasticity,true);assert.equal(config.material.bending_yield_angle_deg,15);
get('bending_plasticity').checked=false;
config=vm.runInContext('readConfig()',ctx);assert.equal(config.material.bending_plasticity,false);
get('body_type').value='volume';get('volume_plasticity').checked=true;
vm.runInContext('updateMaterialFields()',ctx);
assert.equal(get('bending_plasticity').parentElement.hidden,true);
assert.equal(get('volume_yield_strain_percent').parentElement.hidden,false);
get('volume_yield_strain_percent').value='12';
config=vm.runInContext('readConfig()',ctx);assert.equal(config.material.volume_plasticity,true);
assert.equal(config.material.volume_yield_strain_percent,12);
ctx.roundtrip=JSON.parse(JSON.stringify(config));vm.runInContext('fillConfig(roundtrip)',ctx);
assert.equal(get('volume_plasticity').checked,true);assert.equal(get('volume_yield_strain_percent').value,12);
console.log('PASS: plasticity material form visibility, booleans, numeric units and JSON round trip');
