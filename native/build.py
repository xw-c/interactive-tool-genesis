"""Build a project-local libuipc package with volume plasticity; no pip/site-packages writes.
Run in an x64 Native Tools terminal with CMake, Ninja, CUDA and vcpkg installed.
"""
import argparse,hashlib,json,os,shlex,subprocess,sys,zipfile
from pathlib import Path
import importlib.metadata,importlib.util
from apply_volume_plasticity import validate_overlay

ROOT=Path(__file__).resolve().parents[1]
SOURCE_REV='a7f93b33a5c7ddf98f19ec6954fca3c1ef151de9'

def run(*args,**kwargs):subprocess.run([str(x) for x in args],check=True,**kwargs)

def installed_version():
    try:return importlib.metadata.version('pyuipc')
    except importlib.metadata.PackageNotFoundError:return None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='libuipc Git repository containing the pinned revision')
    p.add_argument('--vcpkg-toolchain',type=Path,required=True)
    p.add_argument('--output',type=Path,default=ROOT/'runtime/native-plasticity')
    p.add_argument('--cuda-architectures',default='native',help='Use a semicolon-separated list for redistribution, e.g. 75;86;89')
    p.add_argument('--cuda-flags',default='')
    p.add_argument('--jobs',type=int,default=8)
    a=p.parse_args()
    missing=[name for name in ['numpy','genesis'] if importlib.util.find_spec(name) is None]
    if missing:raise RuntimeError('Use an environment with the app dependencies; missing '+', '.join(missing))
    validate_overlay(ROOT/'native')
    original_version=installed_version()
    if sys.platform=='win32':
        if '/utf-8' not in a.cuda_flags:a.cuda_flags+=' -Xcompiler=/utf-8'
        if 'FMT_UNICODE=' not in a.cuda_flags:a.cuda_flags+=' -DFMT_UNICODE=0'
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    source=out/'src'
    source_fingerprint=dict(source_revision=SOURCE_REV,overlay={
        file.name:hashlib.sha256(file.read_bytes()).hexdigest()
        for file in (ROOT/'native').iterdir()
        if file.suffix in {'.h','.inl'} or file.name=='apply_volume_plasticity.py'})
    manifest=out/'source-info.json'
    if source.exists():
        if not manifest.is_file() or json.loads(manifest.read_text())!=source_fingerprint:
            raise RuntimeError('Existing output has a different or incomplete source snapshot; choose another --output')
        print('Resuming the existing source/build',flush=True)
    else:
        with (out/'source.zip').open('wb') as f:run('git','-C',a.source,'archive','--format=zip',SOURCE_REV,stdout=f)
        with zipfile.ZipFile(out/'source.zip') as z:z.extractall(source)
        run(sys.executable,ROOT/'native/apply_volume_plasticity.py',source)
        manifest.write_text(json.dumps(source_fingerprint,indent=2),encoding='utf-8')
    for name in ('plasticity.h','plasticity_integrator.inl','snk1.h'):
        snapshot=source/'src/backends/cuda/finite_element/constitutions'/name
        if snapshot.read_bytes()!=(ROOT/'native'/name).read_bytes():
            raise RuntimeError(f'Build snapshot differs from native/{name}; choose a fresh --output')
    tools=out/'python-tools'
    if not all((tools/package/'__init__.py').is_file() for package in ['pybind11','pybind11_stubgen']):
        run(sys.executable,'-m','pip','install','--target',tools,
            'pybind11==2.13.6','pybind11-stubgen==2.5.5')
    env=os.environ.copy();env['PYTHONPATH']=str(tools)+os.pathsep+env.get('PYTHONPATH','')
    build=out/'build'
    run('cmake','-S',source,'-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_TOOLCHAIN_FILE='+str(a.vcpkg_toolchain.resolve()),
        '-DUIPC_BUILD_PYBIND=ON','-DUIPC_BUILD_PYTHON_WHEEL=ON','-DUIPC_BUILD_TESTS=OFF','-DUIPC_BUILD_EXAMPLES=OFF',
        '-DUIPC_BUILD_BENCHMARKS=OFF','-DUIPC_PYTHON_EXECUTABLE_PATH='+sys.executable,
        '-DUIPC_CUDA_ARCHITECTURES='+a.cuda_architectures,'-DCMAKE_CUDA_FLAGS='+a.cuda_flags,env=env)
    run('cmake','--build',build,'--target','pyuipc','--parallel',a.jobs,env=env)
    # POST_BUILD does not run when only the backend DLL changes; always sync.
    module=next((build/'Release/bin').glob('pyuipc*.pyd' if sys.platform=='win32' else 'pyuipc*.so'))
    run(sys.executable,source/'scripts/after_build_pyuipc.py',
        '--target='+str(module),'--project_dir='+str(source),'--binary_dir='+str(build),
        '--config=Release','--build_type=Release','--build_wheel=ON',env=env)
    test=build/('plasticity-test.exe' if sys.platform=='win32' else 'plasticity-test')
    cache=(build/'CMakeCache.txt').read_text(encoding='utf-8')
    compiler=next(line.split('=',1)[1] for line in cache.splitlines() if line.startswith('CMAKE_CUDA_COMPILER:'))
    run(compiler,ROOT/'native/test_plasticity.cu','-O3','-DNDEBUG','-std=c++20','--expt-relaxed-constexpr','-arch=native',
        '-I'+str(ROOT/'native'),'-I'+str(source/'include'),
        '-I'+str(source/'src/backends/cuda'),'-I'+str(source/'src/backends/cuda/cuda_tool'),
        '-I'+str(build/('vcpkg_installed/x64-windows/include/eigen3' if sys.platform=='win32' else 'vcpkg_installed/x64-linux/include/eigen3')),
        *shlex.split(a.cuda_flags),'-o',test,env=env)
    run(test,env=env)
    package=build/'python/src'
    env['PYTHONPATH']=str(package)+os.pathsep+env['PYTHONPATH']
    run(sys.executable,'-c','import genesis; from app.plasticity import require_volume_backend; require_volume_backend()',cwd=ROOT,env=env)
    if installed_version()!=original_version:raise RuntimeError('The installed pyuipc package unexpectedly changed during the build')
    (out/'build-info.json').write_text(json.dumps(dict(source_revision=SOURCE_REV,python=sys.version,installed_pyuipc=original_version,
        cuda_architectures=a.cuda_architectures,cuda_flags=a.cuda_flags,package=str(package)),indent=2),encoding='utf-8')
    print('Native package ready:',package)

if __name__=='__main__':main()
