"""Configuration files and public session operations; no simulation algorithms."""
import json
import os
import tempfile
from importlib.metadata import version
from pathlib import Path
from .config import Setup, Material
from .mesh import ROOT, load_mesh, contact_check
from .engine import GenesisEngine

PROJECT=ROOT/'scene.json'

def read_config():
    return Setup.model_validate_json(PROJECT.read_text(encoding='utf-8-sig'))

def atomic_write(path,text):
    f=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False)
    try:
        with f:f.write(text)
        os.replace(f.name,path)
    finally:
        if os.path.exists(f.name):os.unlink(f.name)

def project_info():
    config=read_config();mesh=load_mesh(config)
    return dict(config=config.model_dump(mode='json'),
        mesh=dict(file=config.mesh,vertices=len(mesh.vertices),triangles=len(mesh.faces)),
        contact_check=contact_check(mesh.vertices,mesh.faces,config),
        material_schema=Material.model_json_schema(),
        genesis_version=version('genesis-world'),solver='ipc',device='cuda',
        models=dict(shell='StrainLimitingBaraffWitkinShell',volume='StableNeoHookean',
            bending_model=[dict(value='DiscreteShellBending',label='DiscreteShellBending'),dict(value=None,label='无弯曲能')]))

def save_project(data):
    config=Setup.model_validate(data);load_mesh(config)
    atomic_write(PROJECT,json.dumps(config.model_dump(mode='json'),ensure_ascii=False,indent=2)+'\n')
    return dict(config=config.model_dump(mode='json'))

def replace_mesh(text):
    config=read_config().model_copy(update=dict(mesh='model.obj',pins=[],forces=[]))
    # Validate with the native loader before replacing the user's file.
    with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
        path=Path(folder)/'upload.obj'
        path.write_text(text,encoding='utf-8')
        load_mesh(config.model_copy(update={'mesh':str(path.relative_to(ROOT))}))
    target=ROOT/'model.obj';old=target.read_text(encoding='utf-8-sig') if target.exists() else None
    try:
        atomic_write(target,text)
        return save_project(config.model_dump(mode='json'))
    except Exception:
        if old is None:target.unlink(missing_ok=True)
        else:atomic_write(target,old)
        raise

class Simulation:
    def __init__(self):self.engine=None
    def configure(self,data=None):
        config=read_config() if data is None else Setup.model_validate(data)
        load_mesh(config)
        new=GenesisEngine(config)
        if self.engine is not None:self.engine.close()
        self.engine=new
        return new.state(True)
    def set_pins(self,pins):
        data=self.engine.config.model_dump(mode='json');data['pins']=pins
        # Fixed flags are installed at build time; changing them rebuilds from the input mesh.
        return self.configure(data)
    def close(self):
        if self.engine is not None:self.engine.close();self.engine=None
