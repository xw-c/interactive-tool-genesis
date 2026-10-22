"""Use Genesis's mesh loader; no custom OBJ parser."""
from pathlib import Path
import numpy as np
from genesis.utils.mesh import load_mesh as genesis_load_mesh

ROOT=Path(__file__).resolve().parent.parent

def mesh_path(name):
    path=(ROOT/name).resolve()
    if not path.is_relative_to(ROOT) or path.suffix.lower()!='.obj':
        raise ValueError('mesh 必须是项目内的 OBJ 路径')
    if not path.is_file():raise FileNotFoundError(path)
    return path

def load_mesh(config):
    path=mesh_path(config.mesh)
    try:mesh=genesis_load_mesh(str(path))
    except Exception as exc:raise ValueError(f'Genesis 无法读取 OBJ：{exc}') from exc
    if len(mesh.vertices)==0 or len(mesh.faces)==0 or not np.isfinite(mesh.vertices).all():
        raise ValueError('模型必须包含有效顶点和三角面')
    if config.body_type=='volume' and not mesh.is_volume:
        raise ValueError('体积软体需要封闭、法线朝外的 OBJ')
    return mesh


def contact_check(vertices,faces,config):
    """Linear-time local scale warning, not a full self-collision test."""
    if config.contact_mode!='full':
        scope=('已关闭全部接触：可穿过自身、地面和其他物体。' if config.contact_mode=='none' else
            '仅保留地面接触：可穿过自身和其他物体，保留地面碰撞与摩擦。' if config.contact_mode=='ground_only' else
            '已关闭同一软体的自碰撞；保留地面和其他物体接触，自身可穿透。')
        return dict(warnings=[],enabled=False,scope=scope+' FEM 材料与受力求解保持原样；自接触尺度检查不适用。')
    triangles=np.asarray(vertices)[np.asarray(faces)]
    edges=np.linalg.norm(triangles-np.roll(triangles,1,axis=1),axis=2)
    double_area=np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)
    altitude=np.divide(double_area,edges.max(axis=1),out=np.zeros(len(edges)),where=edges.max(axis=1)>0)
    edge_min=float(edges.min());altitude_min=float(altitude.min())
    reach=config.contact_d_hat_m+(config.material.thickness_m if config.body_type=='shell' else 0.)
    messages=[]
    if reach>=min(edge_min,altitude_min):
        messages.append(f'面片局部尺度小于或等于接触范围：d_hat={config.contact_d_hat_m*1000:.3g} mm，'
            f'计入厚度后的自身接触范围约 {reach*1000:.3g} mm；'
            f'网格最短边 {edge_min*1000:.3g} mm、最小三角形高 {altitude_min*1000:.3g} mm。'
            '可能出现局部虚假自接触、异常排斥/膨胀、人工变硬和步进变慢；建议减小 d_hat 或检查网格，并验证接触行为。')
    return dict(d_hat_m=config.contact_d_hat_m,activation_range_m=reach,min_edge_m=edge_min,
        min_triangle_altitude_m=altitude_min,warnings=messages,
        scope='初始化局部尺度预警；不是完整自碰撞检测，通过也不保证变形后无虚假接触。')
