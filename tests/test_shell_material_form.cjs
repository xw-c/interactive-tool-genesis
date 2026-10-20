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
get('bending_plasticity').checked=true;get('bending_yield_angle_deg').value='15';
vm.runInContext('updateMaterialFields()',ctx);
assert.equal(get('bending_yield_angle_deg').parentElement.hidden,false);
let config=vm.runInContext('readConfig()',ctx);
assert.equal(config.material.bending_plasticity,true);assert.equal(config.material.bending_yield_angle_deg,15);
get('bending_plasticity').checked=false;
config=vm.runInContext('readConfig()',ctx);assert.equal(config.material.bending_plasticity,false);
ctx.roundtrip=JSON.parse(JSON.stringify(config));vm.runInContext('fillConfig(roundtrip)',ctx);
assert.equal(get('bending_plasticity').checked,false);
assert.equal(get('bending_yield_angle_deg').value,15);
get('body_type').value='volume';vm.runInContext('updateMaterialFields()',ctx);
assert.equal(get('bending_plasticity').parentElement.hidden,true);
get('body_type').value='shell';get('bending_model').value='';vm.runInContext('updateMaterialFields()',ctx);
assert.equal(get('bending_plasticity').parentElement.hidden,true);
console.log('PASS: shell plasticity form visibility, booleans and JSON round trip');
