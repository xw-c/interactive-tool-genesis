"""Native volume-plasticity capability check and per-tetrahedron material inputs."""
import ctypes
import sys
from pathlib import Path
import numpy as np

_backend=None

def require_volume_backend():
    global _backend
    if _backend is not None:return
    import uipc._native.pyuipc as native
    folder=Path(native.__file__).resolve().parent
    name='uipc_backend_cuda.dll' if sys.platform=='win32' else 'libuipc_backend_cuda.so'
    try:
        library=ctypes.CDLL(str(folder/name))
        version=library.interactive_ipc_plasticity_version
        version.restype=ctypes.c_int
        if version()!=2:raise RuntimeError('体积塑性需要 v2 增量势扩展，请构建新版后端；v1 仅支持旧的应变截断')
        commit_version=library.interactive_ipc_step_commit_version
        commit_version.restype=ctypes.c_int
        if commit_version()!=1:raise RuntimeError('体积塑性后端缺少失败步提交保护，请重新构建并重启')
    except (OSError,AttributeError) as exc:
        raise RuntimeError('当前 libuipc CUDA 后端不支持体积塑性或缺少失败步提交保护；请先构建并使用 native/ 中的塑性扩展。未启用任何近似或伪造的塑性。') from exc
    _backend=library

def configure_volume(geometry,material):
    import uipc
    # Artist-facing percentages are small-elastic-strain reference values.
    # The constitutive model itself yields in Mandel/Kirchhoff stress space.
    mu=geometry.tetrahedra().find('mu')
    if mu is None:raise RuntimeError('体积材料缺少 mu，无法换算塑性屈服应力')
    shear=np.asarray(uipc.view(mu)).reshape(-1)
    values={'plasticity_enabled':1,
            'plastic_yield_stress':3*shear*material.volume_yield_strain_percent/100,
            'plastic_hardening':3*shear*material.volume_plastic_hardening}
    for name,value in values.items():
        attr=geometry.tetrahedra().find(name)
        if attr is None:attr=geometry.tetrahedra().create(name,1 if name=='plasticity_enabled' else 0.0)
        uipc.view(attr).reshape(-1)[:]=value
