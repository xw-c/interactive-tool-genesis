"""File/API input types. Only physical and structural constraints are enforced."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Material(Input):
    bending_model: Literal['DiscreteShellBending'] | None='DiscreteShellBending'
    youngs_modulus_pa: PositiveFloat=Field(100000.,title='杨氏模量 / Pa')
    poisson_ratio: float=Field(.25,gt=0,lt=.5,title='泊松比')
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
