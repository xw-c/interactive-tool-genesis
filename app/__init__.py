"""Process-local compatibility for Windows runtimes with missing CPU environment."""
import os
import sys
import sysconfig
if sys.platform=='win32' and not os.environ.get('PROCESSOR_ARCHITECTURE'):
    # The interpreter's build tag identifies the architecture, no hardware guess.
    tag=sysconfig.get_platform()
    arch={'win-amd64':'AMD64','win-arm64':'ARM64','win32':'x86'}.get(tag)
    if arch:
        os.environ['PROCESSOR_ARCHITECTURE']=arch
        import platform
        platform._uname_cache=None

from pathlib import Path
_runtime=Path(__file__).resolve().parent.parent/'runtime'
_runtime.mkdir(exist_ok=True)
os.environ['GS_CACHE_FILE_PATH']=str(_runtime/'genesis')
os.environ['QD_OFFLINE_CACHE_FILE_PATH']=str(_runtime/'quadrants')
# Keep compiled helpers separate from the reused environment's stale caches.
os.environ.setdefault('NUMBA_CACHE_DIR',str(_runtime/'numba-existing-gpu'))

# Select the optional complete plasticity package before importing Genesis/uipc.
_selected=os.environ.get("INTERACTIVE_IPC_UIPC_PACKAGE")
_default_package=_runtime/"plasticity-only/package"
_optimized_package=_runtime/"mas-stream/package"
if (_optimized_package/"mas-stream-validated.json").is_file():
    _default_package=_optimized_package
_package=Path(_selected).resolve() if _selected else _default_package
if _selected or (_package/"plasticity-only-build-info.json").is_file():
    if not (_package/"uipc/__init__.py").is_file():
        raise RuntimeError("指定的原生包目录不完整："+str(_package))
    if "uipc" not in sys.modules:sys.path.insert(0,str(_package))
