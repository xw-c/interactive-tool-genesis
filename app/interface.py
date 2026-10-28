"""Configuration files and public session operations; no simulation algorithms."""
import json
import os
import tempfile
import hashlib
import io
import uuid
import zipfile
from importlib.metadata import version
from pathlib import Path
from .config import Setup, Material
from .mesh import ROOT, load_mesh, contact_check, asset_path
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

def validate_asset(config):
    path=asset_path(config.mesh)
    if path.suffix.lower()=='.obj':
        load_mesh(config)
    else:
        engine=GenesisEngine(config,preview=True)
        try:return engine.state(True)
        finally:engine.close()

def project_info():
    config=read_config()
    if asset_path(config.mesh).suffix.lower()=='.obj':
        mesh=load_mesh(config)
        details=dict(mesh=dict(file=config.mesh,vertices=len(mesh.vertices),triangles=len(mesh.faces)),
            contact_check=contact_check(mesh.vertices,mesh.faces,config))
    else:
        details=validate_asset(config)
    return dict(details,config=config.model_dump(mode='json'),
        material_schema=Material.model_json_schema(),
        genesis_version=version('genesis-world'),solver='ipc',device='cuda',
        models=dict(shell='StrainLimitingBaraffWitkinShell',volume='StableNeoHookean',
            bending_model=[dict(value='DiscreteShellBending',label='DiscreteShellBending'),dict(value=None,label='无弯曲能')]))

def save_project(data):
    config=Setup.model_validate(data);validate_asset(config)
    atomic_write(PROJECT,json.dumps(config.model_dump(mode='json'),ensure_ascii=False,indent=2)+'\n')
    return dict(config=config.model_dump(mode='json'))

def replace_asset(filename,content):
    """Import USD/USDZ or a ZIP with one root USD; keep legacy OBJ upload unchanged."""
    name=Path(filename).name
    if name!=filename or not name:raise ValueError('资产文件名不能包含路径')
    suffix=Path(name).suffix.lower()
    if suffix not in ('.usd','.usda','.usdc','.usdz','.zip'):raise ValueError('请选择 USD、USDZ 或资产 ZIP')
    folder=ROOT/'runtime/assets'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    target=folder/name
    if suffix in ('.zip','.usdz'):
        try:archive=zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:raise ValueError('无效的 USDZ / ZIP 压缩包') from exc
        with archive:
            for entry in archive.infolist():
                path=folder/entry.filename
                if not path.resolve().is_relative_to(folder.resolve()) or '\\' in entry.filename or ':' in entry.filename:
                    raise ValueError('压缩包包含越界路径')
            if suffix=='.zip':
                archive.extractall(folder)
                candidates=[p for p in folder.rglob('*') if p.suffix.lower() in ('.usd','.usda','.usdc')
                            and not p.name.startswith('._') and '__MACOSX' not in p.parts]
                roots=[p for p in candidates if len(p.relative_to(folder).parts)==min(
                    (len(q.relative_to(folder).parts) for q in candidates),default=0)]
                if len(roots)!=1:raise ValueError('ZIP 需要一个明确的主 USD 文件')
                target=roots[0]
            else:target.write_bytes(content)
    else:target.write_bytes(content)
    config=Setup(mesh=str(target.relative_to(ROOT)),body_type='volume',material={},contact_mode='full',dt=.001)
    # The bundled example owns its scripted motion; generic USD imports have none.
    example=ROOT/'examples/paperbag/PaperBag.usd'
    if hashlib.sha256(target.read_bytes()).digest()==hashlib.sha256(example.read_bytes()).digest():
        config.material.self_friction=1.
    return save_project(config.model_dump(mode='json'))

def create_engine(config):
    path=asset_path(config.mesh)
    example=ROOT/'examples/paperbag/PaperBag.usd'
    if path.suffix.lower()!='.obj' and hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(example.read_bytes()).digest():
        from examples.paperbag import create_engine as paperbag
        return paperbag(path,config,ROOT/'runtime/paperbag')
    return GenesisEngine(config)

def replace_mesh(text):
    config=read_config().model_copy(update=dict(mesh='model.obj',pins=[],forces=[],material_regions=[],bodies={}))
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
        asset_path(config.mesh)
        new=create_engine(config)
        if self.engine is not None:self.engine.close()
        self.engine=new
        return new.state(True)
    def set_pins(self,pins):
        data=self.engine.config.model_dump(mode='json');data['pins']=pins
        # Fixed flags are installed at build time; changing them rebuilds from the input mesh.
        return self.configure(data)
    def close(self):
        if self.engine is not None:self.engine.close();self.engine=None
