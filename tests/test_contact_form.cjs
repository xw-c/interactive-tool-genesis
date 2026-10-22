const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
 constructor(){this.children=[];this.dataset={};this.hidden=false;this.value='';this.checked=false;}
 set id(id){this._id=id;elements.set(id,this);}get id(){return this._id;}
 append(child){this.children.push(child);child.parentElement=this;}
 replaceChildren(){this.children=[];}
 checkValidity(){return true;}
}
const document={getElementById(id){if(!elements.has(id)){const e=new Element();e.id=id;}return elements.get(id);},
 createElement(){return new Element();},querySelectorAll(){return [];}};
const code=fs.readFileSync('static/app.js','utf8');
const prefix=code.slice(code.indexOf('const $='),code.indexOf('let config=null'));
const read=code.slice(code.indexOf('function readConfig()'),code.indexOf('async function configure(c)'));
const spec=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const ctx=vm.createContext({document,structuredClone,spec,SimulationAPI:class {}});
vm.runInContext(prefix+'\nlet config=null,pins=[],forces=[];\n'+read,ctx);
vm.runInContext('buildForm(spec);fillConfig(spec.config)',ctx);
const get=id=>document.getElementById(id);
for(const mode of ['full','no_self','ground_only','none']){
 get('contact_mode').value=mode;vm.runInContext('updateMaterialFields()',ctx);
 assert.equal(get('contact-options').hidden,mode==='none');
 const config=vm.runInContext('readConfig()',ctx);
 assert.equal(config.contact_mode,mode);assert.deepEqual(JSON.parse(JSON.stringify(config.material)),spec.config.material);
 ctx.roundtrip=JSON.parse(JSON.stringify(config));vm.runInContext('fillConfig(roundtrip)',ctx);
 assert.equal(get('contact_mode').value,mode);
}
ctx.legacy=structuredClone(spec.config);delete ctx.legacy.contact_mode;
vm.runInContext('fillConfig(legacy)',ctx);assert.equal(get('contact_mode').value,'ground_only');
console.log('PASS: contact mode visibility, material preservation and JSON round trip');
