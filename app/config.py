"""File/API input types. Only physical and structural constraints are enforced."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_serializer, field_validator

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Material(Input):
    bending_model: Literal['DiscreteShellBending'] | None='DiscreteShellBending'
    bending_plasticity: bool=Field(False,title='启用永久折痕',description='仅薄壳弯曲：超过屈服折角后留下折痕；不包含永久拉伸或剪切。重置会清除折痕。')
    bending_yield_angle_deg: float=Field(10.,ge=0,le=180,title='屈服折角 / 度',description='相邻三角形相对当前无应力折角还能弹性转动的角度。越小越容易留下折痕；该参数与网格细密程度有关。')
    bending_plastic_hardening: float=Field(0.,ge=0,title='折痕硬化',description='0：保持同样容易折；正值：每积累 1 度塑性折角，屈服折角增加这么多度。')
    volume_plasticity: bool=Field(False,title='启用永久变形',description='体积软体：超过屈服形变后留下永久变形，塑性变形保持体积。需要带本项目塑性扩展的 libuipc CUDA 后端。')
    volume_yield_strain_percent: float=Field(10.,ge=0,title='参考屈服形变 / %',description='越小越容易留下永久形状。换算为应力阈值 Y0=3×原生剪切模量×该值/100；仅为小弹性应变参考，不是永久形变上限或精确伸长百分比。')
    volume_plastic_hardening: float=Field(0.,ge=0,title='塑性硬化',description='0 不硬化；正值提高后续屈服应力，硬化模量 H=3×原生剪切模量×该值。1 表示累计塑性增加 1% 时，参考屈服形变增加 1 个百分点。重置清空历史。')
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

class MaterialRegion(Input):
    name: str=''
    elements: list[Annotated[int,Field(ge=0,strict=True)]]=Field(min_length=1)
    material: Material

    @field_validator('material')
    @classmethod
    def global_friction_only(cls,value):
        if 'self_friction' in value.model_fields_set:raise ValueError('摩擦仅支持全局 material.self_friction')
        return value

    @field_serializer('material')
    def serialize_material(self,value):
        return value.model_dump(exclude_unset=True)

class Setup(Input):
    step_mode: Literal['libuipc','genesis_world']=Field('libuipc',title='步进路径',description='libuipc（默认）：单软体与静态地面直接 IPC 步进，不同步 Genesis 实体状态、计数及回调；genesis_world：完整 Genesis 调度。')
    contact_mode: Literal['full','no_self','ground_only','none']=Field('ground_only',title='接触模式',description='ground_only（默认）：仅保留地面接触，关闭自身及其他物体接触；full：完整接触；no_self：只关闭同一软体的自碰撞；none：包括地面在内关闭全部接触。')
    mesh: str='model.obj'
    body_type: Literal['shell','volume']='shell'
    tet_max_volume_m3: Literal[-1] | PositiveFloat=-1
    material: Material=Field(default_factory=Material)
    material_regions: list[MaterialRegion]=Field(default_factory=list)
    bodies: dict[str,'BodyOverride']=Field(default_factory=dict)
    contact_d_hat_m: PositiveFloat=Field(.0005,title='IPC 接触激活距离 / m')
    dt: PositiveFloat=1/60
    gravity: tuple[float,float,float]=(0,0,-9.81)
    pins: list[int]=Field(default_factory=list)
    forces: list[Force]=Field(default_factory=list)

    @field_serializer('material')
    def serialize_material(self,value):
        # USD supplies the base material; serializing defaults would turn them
        # into explicit overrides on the next load/save cycle.
        return value.model_dump(exclude_unset=self.mesh.lower().endswith(('.usd','.usda','.usdc','.usdz')))

class BodyOverride(Input):
    material: Material=Field(default_factory=Material)
    material_regions: list[MaterialRegion]=Field(default_factory=list)
    mass_kg: PositiveFloat | None=None

    @field_serializer('material')
    def serialize_material(self,value):
        return value.model_dump(exclude_unset=True)

Setup.model_rebuild()
