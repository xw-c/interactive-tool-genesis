"""File/API input types. Only physical and structural constraints are enforced."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Material(Input):
    bending_model: Literal['DiscreteShellBending'] | None='DiscreteShellBending'
    bending_plasticity: bool=Field(False,title='启用永久折痕',description='仅薄壳弯曲：超过屈服折角后留下折痕；不包含永久拉伸或剪切。重置会清除折痕。')
    bending_yield_angle_deg: float=Field(10.,ge=0,le=180,title='屈服折角 / 度',description='相邻三角形相对当前无应力折角还能弹性转动的角度。越小越容易留下折痕；该参数与网格细密程度有关。')
    bending_plastic_hardening: float=Field(0.,ge=0,title='折痕硬化',description='0：保持同样容易折；正值：每积累 1 度塑性折角，屈服折角增加这么多度。')
    youngs_modulus_pa: PositiveFloat=Field(100000.,title='杨氏模量 / Pa')
    poisson_ratio: float=Field(.25,gt=0,lt=.5,title='泊松比')
    shear_stiffness_n_m: PositiveFloat=Field(8.,title='剪切刚度 / N·m⁻¹',description='仅薄壳：控制材料方向的夹角变化。独立二维系数；越大越难斜切，不随 E、泊松比或厚度自动变化。经验参考：可取拉伸系数的约 0.3 倍作为调参起点，不是固定物理关系。')
    density_kg_m3: PositiveFloat=Field(360.,title='密度 / kg·m⁻³')
    thickness_m: PositiveFloat=Field(.00025,title='实际厚度 / m')
    bending_rigidity_nm: float=Field(5e-8,ge=0,title='弯曲刚度 / N·m')
    self_friction: float=Field(.4,ge=0,title='摩擦系数')

class Force(Input):
    vertex: int=Field(ge=0)
    vector: tuple[float,float,float]

class Setup(Input):
    mesh: str='model.obj'
    body_type: Literal['shell','volume']='shell'
    tet_max_volume_m3: Literal[-1] | PositiveFloat=-1
    material: Material=Field(default_factory=Material)
    contact_d_hat_m: PositiveFloat=Field(.0005,title='IPC 接触激活距离 / m')
    dt: PositiveFloat=.005
    gravity: tuple[float,float,float]=(0,0,-9.81)
    pins: list[int]=Field(default_factory=list)
    forces: list[Force]=Field(default_factory=list)
