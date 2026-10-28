# Genesis 软体工具

输入为 scene.json 和 OBJ 或 USD 资产；运行 ./start.ps1，打开 http://127.0.0.1:3000。USD 导入与按物体调参见下文。

启动脚本默认使用项目目录中的 .venv/Scripts/python.exe，不依赖开发者机器的路径。首次使用请按下文创建环境并安装依赖。也可运行 ./start.ps1 -GenesisPython <解释器路径>，显式复用已安装所需依赖和 CUDA PyTorch 的环境；启动脚本不会自动安装依赖。已验证的依赖版本为 Genesis 1.4.1、torch 2.8.0+cu126、pyuipc 0.0.28。

## 配置

完整调参说明：[参数指南](static/parameter-guide.html)。启动后可点击网页右上角「调参说明」，或打开 http://127.0.0.1:3000/static/parameter-guide.html。覆盖材料、薄壳塑性、网格、接触、时间步、固定点和点力；体积塑性需项目原生 CUDA 扩展，尚未按真实材料标定。


网页「接触模式」提供四档，也可设置 JSON 顶层 `contact_mode`：

| 值 | 网页选项 | 接触行为 |
| --- | --- | --- |
| `full` | 完整接触 | 自碰撞、地面及其他物体接触 |
| `no_self` | 关闭自碰撞 | 同一软体可自穿透，保留地面及其他物体接触 |
| `ground_only`（默认） | 仅保留地面接触 | 禁用自身及其他物体接触，仅保留地面碰撞与摩擦 |
| `none` | 关闭全部接触 | 可穿过自身、地面及其他物体 |

各档均保留原有 libuipc FEM 求解、本构、拉伸/剪切/弯曲、塑性设置、固定点和点力，不切换到 Genesis 普通体积 FEM。旧 JSON 缺少字段时默认仅保留地面接触。点击「应用设置并重置」后生效并清空变形历史；导入 JSON、导出和保存配置均包含此字段。`contact_d_hat_m` 在 `none` 下不参与接触，数值保留便于切回；禁用自身接触后不再显示自身接触尺度预警。当前工具只有一个软体和地面，因此 `no_self` 与 `ground_only` 的实际碰撞对象相同；后者显式禁用所有非地面的物体间接触对。

`none` 使用原生 `IPCCouplerOptions(contact_enable=False, contact_friction_enable=False)`；`no_self` 在 Genesis 注册完接触对后、libuipc 初始化前仅将本软体的自身接触对设为禁用。该过滤适配依赖 Genesis 内部 `_register_contact_pairs`、`_ipc_cloth_contacts` / `_ipc_fem_contacts`，升级后需复验。性能和效果对照见 [接触模式测试报告](benchmarks/CONTACT_MODES.md)，复现：`python -m benchmarks.contact_modes --suite`。

`ground_only` 在接触对注册后禁用 cloth/FEM/ABD 之间的全部接触对（含自身），保留所有平面地面接触对及其摩擦/阻力参数；全局接触与摩擦仍开启。此模式同样依赖 Genesis 内部注册接口，升级后须复验。

薄壳和体积软体均绑定 Genesis IPC。Genesis 使用 gs.cuda，IPC 使用独立 CUDA 引擎；要求 CUDA 版 PyTorch 和可用 NVIDIA GPU，无法使用 CUDA 时直接报错，不回退 CPU。OBJ/TetGen 预处理、Python 输入整理、网页通信和结果取回仍包含 CPU 工作。

- body_type：shell 或 volume。
- mesh：项目内 OBJ 路径，单位 m。直接使用 Genesis 内置读取函数，不自写 OBJ 解析。
- tet_max_volume_m3：-1 自动划分，正数指定最大四面体体积（m³），仅体积模式使用。正数会允许 TetGen 细分边界。
- dt：每次 step 对应的时间（s），默认 1/60 s（约 0.0166667）；子步数使用 Genesis 默认值 1。达到实时需要每秒推进 60 步，不代表程序保证运行到 60 FPS；已有配置中显式保存的 dt 按原值读取。
- gravity：三维重力（m/s²）。
- material：youngs_modulus_pa、poisson_ratio、density_kg_m3、self_friction；薄壳另有 thickness_m、shear_stiffness_n_m、bending_model（DiscreteShellBending 或 null）、bending_rigidity_nm。
- pins：表面固定点编号；forces：[{"vertex":0,"vector":[0,0,0.001]}]，单位 N。

薄壳材料绑定原生 Cloth / Baraff–Witkin；体积绑定原生 Elastic / IPC Stable Neo-Hookean，体积塑性在此弹性本构上扩展。

薄壳新增 `material.shear_stiffness_n_m`，默认 8 N/m，必须为正的有限数。它独立设置 BW 的二维剪切系数：越大越难斜切，不随 E、nu 或厚度自动联动，体积模式不使用。网页修改后点击「应用设置并重置」。拉伸、strain limiting、弯曲和求解精度保持原样。

旧 JSON 缺省时也采用 8，会改变旧版效果；复现旧映射需显式填写原 E/[2(1+nu)] 的数值，本例为 40000。8 是调参起点，不是通用的真实材料标定值。剪切回归检查：`python -m unittest discover -s tests -v`；设置环境变量 `IPC_GPU_TEST=1` 可额外检查真实 CUDA 构建和步进。

保留数学/物理要求和数据格式校验：正的刚度、密度、厚度、dt，0<nu<0.5，非负弯曲刚度与摩擦，有限数、三维向量、合法表面编号。删除人为设置的最大刚度、密度、厚度、点力、数量、时间步和重力范围，以及旧 elastic_moduli 兼容代码。Pydantic 在读取文件、保存配置、configure、更新点力或固定点时校验；推进时不重复校验配置。失败通过 HTTP 400 或 WebSocket error 返回，网页显示错误。

## 塑性（默认关闭）

薄壳点力塑性验证示例见 [benchmarks/shell_plasticity/README.md](benchmarks/shell_plasticity/README.md)，包含可导入的配置与自动实验 `python -m benchmarks.shell_plasticity`。

塑性表示松开后仍留下永久形状，不是增加阻尼，也不是让果冻慢慢回弹的黏弹性。弹性刚度仍控制当前抵抗变形的程度；屈服参数控制从多大变形开始留痕。两者分别调节。

| 模式 | material 字段 | 默认值 | 调参含义 |
| --- | --- | --- | --- |
| 薄壳 | bending_plasticity | false | 启用原生 StrainPlasticDiscreteShellBending，替换弹性弯曲，不叠加两份弯曲能量 |
| 薄壳 | bending_yield_angle_deg | 10 | 相邻三角形相对当前无应力折角还能弹性转动多少度；越小越容易留下折痕 |
| 薄壳 | bending_plastic_hardening | 0 | 每积累 1 度永久折角，屈服折角增加多少度；0 表示不硬化 |
| 体积 | volume_plasticity | false | 启用逐四面体的等体积塑性，需项目 CUDA 扩展 |
| 体积 | volume_yield_strain_percent | 10 | 参考屈服形变；输入 r% 换算为初始屈服应力 Y0=3μr/100，μ 为原生剪切系数；越小越易留形 |
| 体积 | volume_plastic_hardening | 0 | 每积累 1 个百分点等效塑性形变，屈服阈值增加多少个百分点 |

屈服值可设为 0，表示没有弹性屈服范围；0 硬化表示屈服阈值不随塑性积累增加。10 度、10% 是便于试验的起点，不是真实材料标定值。先保留原弹性刚度，开启塑性、硬化设为 0，再降低屈服值观察残余形变。需要越捏越难继续变形时再增加硬化。改变 E 不会自动改变这里的屈服形变阈值。

薄壳目前提供永久折痕，不包含膜内永久拉伸/剪切；选择「无弯曲能」时折痕参数不参与计算。折角阈值按相邻三角形定义，网格加密后需要重新标定。体积 v2 采用乘法分解 F=Fe·Fp、SNK1 弹性、应力驱动的关联 J2 屈服及线性各向同性硬化，保持 det(Fp)=1，不模拟永久体积压实。每次 Newton/线搜索求值都做局部变分返回，能量、力和切线来自同一增量势，接受步后才提交历史。Newton 超限或线搜索失败会在提交前报错，失败后必须重置，不提供整帧回滚或直接重试。原有 E/nu 到原生系数的映射保留。界面百分数仅作为小弹性应变参考，换算成屈服应力，并非大变形下的精确应变阈值。完整推导与论文链接见 [native/MODEL.txt](native/MODEL.txt)。

旧 JSON 缺少这些字段时保持纯弹性。参数在「应用设置并重置」后生效；重置、修改固定点或重新导入配置会清空塑性历史。导出 JSON 保存参数，导出 OBJ 保存表面位置，两者均不保存内部塑性状态。

薄壳折痕可直接使用现有依赖。体积塑性需要编译 native/ 扩展；普通 pyuipc 0.0.28 没有此功能，v1 扩展也不支持 v2 增量势，缺少扩展时会明确报错。扩展不会覆盖系统安装，也不默认绑定开发者本机路径。在配置好 C++20、CUDA、CMake、Ninja、vcpkg 的开发终端中，使用选定的 Python 环境运行（首次编译可能较久）：

```powershell
python native/build.py --source <libuipc仓库路径> --vcpkg-toolchain <vcpkg路径>/scripts/buildsystems/vcpkg.cmake
./start.ps1 -UipcPackageDir runtime/native-plasticity/build/python/src
```

构建脚本使用固定的 libuipc 源码提交 a7f93b33a5c7ddf98f19ec6954fca3c1ef151de9 的隔离副本；编译所需 Python 工具安装到输出目录，不写入现有环境。默认只为本机 GPU 编译，给其他 GPU 分发时用 --cuda-architectures 指定目标架构。-UipcPackageDir 只对本次服务进程生效。体积扩展部署须携带整套匹配的 uipc Python 包与原生库，不能只替换一个 DLL。

回归：python -m unittest discover -s tests -v；IPC_GPU_TEST=1 额外运行真实 CUDA 折页加载—松开对照。native/test_plasticity.cu 检查实际 CUDA 回映射和能量导数。加载—卸载实验：python -m benchmarks.plasticity --body shell 或 --body volume；体积实验需先在进程的 PYTHONPATH 中指定扩展包。结果在 runtime/plasticity/。

旧 v1 预编译包在 `runtime/plasticity-ready`，仅保留作对照，不能启用新版体积塑性。本机已验证的修复包为 `runtime/plasticity-only/package`，仅包含塑性及其失败步提交保护；旧服务需重启：

```powershell
./start.ps1 -UipcPackageDir runtime/plasticity-only/package
```

沿用已有 CUDA 环境时，再添加 `-GenesisPython <解释器路径>`。此包适用于本机已测试的 Windows / Python 3.11 / NVIDIA GPU；其他平台或 GPU 架构需要匹配的构建及目标机器验证。上述 native/build.py 用于从固定源码构建纯塑性扩展。构建脚本支持同一源码与补丁下的增量重试，会检查安装环境中的 pyuipc 版本未被改变；更新 CUDA DLL 后始终重新同步项目内的 Python 包。

固定源码与原 wheel 的约定有差异，扩展同时对齐了本工具所用薄壳模型的厚度半径、弯曲权重和 Newton 默认值。原剪切回归及加载—卸载对照均通过；关闭塑性的落地对照也与原 wheel 一致。完整 CUDA 回归可同时设置 `IPC_GPU_TEST=1` 和 `IPC_VOLUME_GPU_TEST=1`。

## 表面与体积

volume 输入必须封闭并有一致的朝外法线，Genesis 自动调用 TetGen 生成内部四面体。shell 直接使用三角形。

所有公开编号均对应 configure / snapshot 返回的 positions（从 0 开始），不是原始 OBJ v 行号，也不是体积内部的全局顶点号。前端只收到 entity.surface_triangles 引用的表面顶点，包括划分新增的表面点；内部顶点不能选择、施力或固定。模型、划分参数或引擎版本变化后应重新选择点。

点力合并、表面编号映射在 CPU 上处理用户输入时完成；每步由 uipc 内置动画接口将外力数组送入 CUDA 求解器。默认弹性和薄壳折痕使用原生模型；体积塑性由 native/ 的 CUDA 扩展维护每个四面体的塑性状态，并一致地修改能量、力与刚度矩阵。Python 不直接移动顶点来制造塑性。

## 最小接口

### 按单元赋材质

可直接在网页「导入 JSON」中加载 [shell_1k 左软右硬样例](benchmarks/shell_1k_material_regions.json)，再点击「开始」。它与原有样例放在一起，复用仓库中的 `benchmarks/shell_1k.obj`，固定后侧两排共 80 个顶点，右半 936 个面使用较硬材质，`dt=1/60`。

复用原有 `configure`，在配置中增加 `material_regions`。`elements` 是从 0 开始的三角面编号（薄壳）或 TetGen 四面体编号（体积）；未覆盖的单元和参数继承默认 `material`。

```javascript
const state = await api.snapshot();
state.config.material_regions = [
  {elements: [0, 1, 2], material: {youngs_modulus_pa: 300000, density_kg_m3: 600}}
];
await api.configure(state.config); // 重建、时间归零；设为空数组可清除局部材质
```

configure / snapshot 新增 `element_mesh`：`elements` 的行号就是单元编号，行内顶点号索引其全体顶点 `positions`（含体积内部点）；薄壳行号与原有 `faces` 一致。`material_ids` 为 0 时使用默认材质，1、2… 对应区域顺序；`surface_vertex_ids` 将表面点编号映射到全体顶点。普通 step 不重复返回拓扑。区域重叠、越界和非法参数会报错。更换网格、软体类型或四面体划分后须重新选择单元；替换 OBJ 自动清除区域。原有点力、固定点编号仍是表面编号。

弹性和塑性参数按单元写入原生属性，密度按单元体积装配质量，保留共享顶点。薄壳共享边的弯曲参数按相邻面的静止面积加权；一侧未启用折痕塑性时该边保持弹性。局部厚度用于膜刚度和质量；原生后端要求统一碰撞厚度，因此碰撞厚度和摩擦仍使用全局材质。局部指定摩擦会报错。配置可保存和导入/导出，网页默认材质表单保留局部配置。

测试：`python -m unittest discover -s tests -p test_element_materials.py -v`，设置 `IPC_MATERIAL_GPU_TEST=1` 运行真实 CUDA 回归。

前端使用 static/api.js 的 SimulationAPI。

    const api = new SimulationAPI();
    const project = await api.getProject();
    await api.connect(state => render(state));
    await api.configure();
    await api.setPins([0, 3]); // 替换固定点；从输入 OBJ 重建，时间归零
    await api.setForces([{vertex:5,vector:[0,0,0.001]}]);
    await api.step();

| 入口 | 用途 |
| --- | --- |
| GET /api/project / getProject() | 配置、表单 schema、材料说明 |
| PUT /api/project / saveProject(config) | 保存 scene.json |
| PUT /api/mesh / replaceMesh(objText) | 验证并保存 model.obj，清空点力与固定点 |
| /ws configure / configure(config?) | 创建场景；省略配置读取文件 |
| /ws pins / setPins(pins) | 替换固定点并重建；空数组解除全部固定 |
| /ws forces / setForces(forces) | 实时替换点力；空数组清除 |
| /ws step / step() | 推进一步 |
| /ws snapshot / snapshot() | 取回完整状态 |

原始 WS 请求示例：{"type":"pins","pins":[0,3],"request_id":1}。

状态包含 positions、pins、time、frame、backend、metrics。configure / pins / snapshot 还返回 faces、config、mesh、volume_mesh；volume_mesh 给出表面顶点数、全部求解顶点数与四面体数。positions / faces 均只包含表面。

配置 `step_mode` 默认 `libuipc`（旧 JSON 缺少该字段时也使用此默认值），绕过 Genesis 每步调度，直接 `World.advance/retrieve` 并读取 IPC 位置，适用于本工具的单软体与静态地面、单子步场景；FEM、塑性、外力动画和接触仍在 IPC 内求解。此模式不更新 Genesis 实体状态、内部计数、传感器或场景回调，不能用于需要这些功能的集成。需要完整调度时设置 `"step_mode":"genesis_world"`，使用 Genesis 的 `scene.step()`，完成软体状态同步、场景时间及回调调度。网页接口仍返回表面位置及应用自己的时间、帧数；`engine.get_simulation_positions()` 返回最近一次成功步的全部顶点副本。网页导入、编辑和导出 JSON 会保留该设置；显式指定的 `genesis_world` 不会被自动替换。

默认自动尝试 MAS，无需选择预条件器。缺少网格分区接口、缺少 MAS CUDA stream 修复、全部求解顶点固定或分区失败时，初始化阶段自动回退 diag，并输出原因；部分生成的分区属性会清理。没有 `INTERACTIVE_IPC_FEM_PRECONDITIONER` 开关或构造参数。MAS 修复包位于 `runtime/mas-stream/package`；HTTP/WS 字段保持不变。相同加权残差阈值不等于等精度，MAS 的实际速度与误差仍取决于场景；历史计时使用旧步进路径，不能代表当前性能。

前端塑性联调约定：材料参数见上方表格。求解失败返回 {"type":"error","message":"...","request_id":...}，前端停止自动 step 并提示重置。重置使用 configure(config)，没有单独的 reset 消息；重新配置会清空塑性历史。当前接口不返回逐单元塑性应变或塑性云图数据。交付验收至少覆盖 configure、step、错误提示和重新 configure 后恢复运行。

## 后端文件

| 文件 | 内容 |
| --- | --- |
| app/config.py | 参数模型，无自定义 valid 函数 |
| app/mesh.py | Genesis OBJ loader 的薄包装及必要几何检查 |
| app/engine.py | 创建原生场景、注册内置 IPC 外力、推进和读取表面坐标 |
| app/plasticity.py、native/ | 体积塑性原生能力检查、输入映射及可复现的 CUDA 扩展构建 |
| app/interface.py | 文件读写与会话创建/固定点接口，不实现求解 |
| app/server.py | HTTP / WebSocket 路由与串行工作线程，不实现求解 |
| app/__init__.py | 当前 Windows 环境兼容与独立缓存目录（含 Numba，避免继承其他项目的旧编译缓存） |

engine.py 不再检查版本必须等于 1.4.4；requirements.txt 记录所复用环境的依赖版本，供新机器部署参考。注册外力需要调用一处 Genesis 内部 IPC 构建接口，升级后该接口可能变化，不保证所有未来版本直接兼容。

FiniteElementExternalForce 是 from uipc.constitution 直接导入的类。创建实例、注册到 constitution_tabular、apply_to 物体、写入输入数据，是启用 uipc 内置外力功能；不是安装额外软件。

交付后端：app/、native/、run.py、requirements.txt、scene.json、model.obj、README.md；联调附 static/ 与 start.ps1。MAS 优化版须携带完整的 runtime/mas-stream/package/（包含 uipc Python 包、原生库、构建元数据及 mas-stream-validated.json）；保留 runtime/plasticity-only/package/ 可复现旧后端和对角路径。不能排除整个 runtime。可排除 .venv/、__pycache__/、runtime 中其余构建中间文件、缓存和实验结果。原生包可能未被 Git 跟踪，需要单独作为交付附件分发。

跨机器部署不要求相同项目路径，但要求运行环境与原生二进制兼容。当前包是 Windows x64 / CPython 3.11 构建，CUDA 编译目标包含 compute_75/sm_75；不能承诺任意系统、Python 版本或 GPU 都可直接使用，也不意味着只有同型号显卡可用。接收方需要可用的 NVIDIA 驱动及匹配的 CUDA/PyTorch 依赖；当前验证环境为 Quadro RTX 8000、torch 2.8.0+cu126。跨平台或不兼容环境需重建完整扩展包；全新机器部署尚未验证。

前端同事也可以仅在自己的机器运行浏览器，连接已部署的后端；这种方式不需要在前端机器安装 CUDA。当前 WebSocket 校验同源 Origin，联调应由后端提供页面，或使用同源反向代理同时转发页面、/api 和 /ws。

首次使用，在项目根目录创建环境并安装依赖。以下为部署参考步骤，尚未在全新机器上重新验证；已有满足要求的环境时，可通过 -GenesisPython 显式指定：

    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --no-cache-dir setuptools wheel
    .\.venv\Scripts\python.exe -m pip install --no-cache-dir --no-build-isolation -r requirements.txt
    .\start.ps1


IPC 接触激活距离通过 scene.json 顶层 `contact_d_hat_m` 设置，默认 `0.0005` m（0.5 mm），网页同名输入也可调整；直接传给 Genesis 的 `IPCCouplerOptions(contact_d_hat=...)`。旧 JSON 缺少此字段时同样采用 0.5 mm。
每次场景初始化使用实际仿真表面网格做一次 O(面数) 的局部尺度检查：最短边、最小三角形高与接触范围比较，薄壳自身接触计入实际厚度。结果在配置响应 `contact_check` 返回，网页显示预警；不拦截、不自动调参。这是保守尺度预警，不是完整邻域距离/自碰撞检测，也不保证变形后无虚假接触。1w 薄片在 0.5 mm 默认值下不触发此尺度预警；更细或瘦长的面片仍可能触发。预警会指出虚假自接触、异常排斥/膨胀、人工变硬与步进变慢风险，并输出到后端日志及网页。


网页配置生效规则：切换薄壳/体积只更新表单，点击「应用设置并重置」后重建场景、归零时间；当前 OBJ 和已填参数保留，不自动导入另一套默认参数。「导入 JSON」读取文件后立即调用 `SimulationAPI.configure(JSON.parse(text))` 重建当前会话，无需再次应用；不会自动保存 scene.json。JSON 的 mesh 是后端项目内已有 OBJ 的路径，不包含或上传 OBJ 数据。
前端 JSON 导入使用已有 configure 接口，无单独文件上传路由：`await api.configure(JSON.parse(jsonText))`。后端对应 `Simulation.configure(data)`；若要持久化配置，另调用 `api.saveProject(config)`（PUT /api/project）。

## USD 资产与原生连接

现有 OBJ 的 HTTP/WebSocket 接口保持兼容。USD 路径复用 Genesis 的 `UsdContext`、`parse_usd_stage`、`parse_usd_rigid_entity` 和 `parse_prim_geoms`，补充原始四面体 FEM、ABD、attachment 映射。USD 使用 engine 自己管理的一个 libuipc Scene/World，不创建空 Genesis Scene、不替换 coupler 方法，也不声称物体是完整 Genesis Entity。

网页新增「导入 USD / USDZ / ZIP」和物体选择框。选择物体后显示有效材质，选中的物体用黄色高亮；修改后沿用「应用设置并重置」。两个提手可独立调整 E/ν/密度，袋身可调整质量和摩擦。单元材质分区沿用已有结构，通过 JSON 导入；前端只改动明确修改的字段，未填写的材质继续从 USD 继承。可以清除单物体覆盖。

配置仍使用 `Setup/Material/MaterialRegion`，只增加物体路径作用域：

```json
{
  "mesh": "runtime/assets/example/PaperBag.usd",
  "body_type": "volume",
  "dt": 0.001,
  "contact_mode": "full",
  "material": {},
  "bodies": {
    "/root/Rope001": {
      "material": {"youngs_modulus_pa": 750000},
      "material_regions": [
        {"name": "region", "elements": [0], "material": {"density_kg_m3": 750}}
      ]
    },
    "/root/PaperBag": {"mass_kg": 0.12}
  }
}
```

覆盖顺序为 USD → 全局明确填写的材质字段 → 物体字段 → 物体内部材质分区。E/ν/ρ 必须由 USD 或覆盖提供。分区单元编号属于所选物体，不是全局合并编号。USD 顶层 `material_regions` 不接受无作用域的单元编号；OBJ 继续保留原含义。ABD 不接受 FEM 参数。物体对摩擦取两端系数的几何平均；地面使用全局摩擦，单物体摩擦覆盖也映射到地面接触对。质量、材质、固定点和拓扑设置通过重建生效并清除历史。

接口增量：

- 原有 `GET/PUT /api/project`、`PUT /api/mesh` 和 `/ws` 的 `configure/step/snapshot/forces/pins` 保持原形式。
- 新增 `PUT /api/asset?name=PaperBag.usd`，请求体为文件字节；支持 .usd/.usda/.usdc/.usdz/.zip。ZIP 应只有一个位于最浅目录的主 USD，保留贴图相对路径；有外部引用的资产优先用 USDZ/ZIP 打包。压缩包越界路径会被拒绝。
- `GET /api/project` 和完整 `configure/snapshot` 状态新增 `bodies`、`attachments`、`warnings`。每个 body 包含有效材质、类型、可编辑字段、不支持字段及原因、表面顶点和三角面范围、是否支持点力/固定。
- 原 `positions/faces/element_mesh` 仍提供合并数组；公开固定点和点力编号继续对应表面数组。运行时不依赖这些合并数组建立连接。
- Python 仍使用 `GenesisEngine` 的 `step/state/set_forces/get_simulation_positions`；按物体点力可传 `body=USD路径`。脚本不需要序列化每步状态时可用 `step(snapshot=False)`。

连接在导入时自动计算，不读取纸袋节点列表：解析 actor 和点对，统一坐标，去除重复点对。一个 USD attachment 结构就是一个区域，不按距离或连接点簇数量拆分；每个结构连接 actor0/actor1 两个物体，但可以包含任意多个连接位置。FEM 端已有节点直接连接；内部点先定位支撑四面体，FEM/ABD 使用支撑顶点集合，在 ABD 上创建同位置参考锚点；ABD/FEM 对称处理。FEM/FEM 的内部点使用明确报告的最近节点对近似；ABD/ABD 使用局部参考点。默认每个 attachment 总刚度1500 N/m，均分到实际节点对，可通过 engine 的 `set_attachment_parameters` 覆盖。纸袋样例将每个 attachment 设为3000 N/m，保持原先每根提手两个1500 N/m 区域的总刚度。没有 attachment 的资产不会因物体靠近而自动粘接。

`attachments` 每个结构返回一条记录：实际节点对、总刚度、每对刚度、最近节点误差和近似标记。它不是对 PhysX 嵌入点约束的无损替代。重合但编号不同的四面体可能对应不同支撑节点，导入不自动焊接它们。USD 局部碰撞过滤目前近似成物体对过滤，并返回提示。当前映射旧 PhysxPhysicsAttachment、authored 四面体 FEM、PhysxDeformableSurfaceAPI 三角形 shell、凸包 ABD 和已装配固定关节；不声称覆盖全部 USD 物理语义。

volume、shell、ABD 共用同一套连接 API，支持全部九种有序类型组合。shell 使用 Mesh 的三角形拓扑及 `physxDeformable:restPoints`（未提供时使用 `points`）；材质读取 `physxDeformableSurfaceMaterial:` 的 E、ν、密度、厚度和摩擦，JSON 继续通过 `bodies[路径].material` 覆盖。厚度按 stage 长度单位转为米，JSON 厚度使用米。剪切、弯曲及折痕沿用现有薄壳参数与材质分区逻辑。shell 内部连接点先检查是否落在三角面上；连接 ABD 时展开为三角形支撑顶点，连接另一软体时使用明确报告的最近节点近似。volume 的四面体定位规则保持原样。

同类型互连是两个独立物体的连接；同一软体内部也可以 stitch 不同节点，但不能把节点连接到自身或重复添加同一节点对。同一 ABD 内部没有独立的关节自由度，重合参考点之间的连接只是冗余约束。若两个重合软体节点只有坐标相同、编号不同，USD 的点坐标本身不足以指定是哪一个；Python `stitch_points` 可传显式 `vertex_ids0/1`。

混合 shell/volume 场景的完整状态使用 `element_mesh.kind="mixed"`、`element_mesh.blocks`；每个 block 包含 `body`、`kind`、`elements`（索引全局 `positions`）、`material_ids`。混合时旧的顶层 `elements/material_ids` 为空，避免混用三角形和四面体编号。只有一种软体单元时保留原来的平铺数组，并额外提供 blocks。表面 `positions/faces` 和原 HTTP/WebSocket 消息不变，前端按所选物体类型显示薄壳或体积参数。

`examples/paperbag.py` 仅保留文件入口、固定动作和驱动/接触初始化；没有 REGIONS 或连接节点编号，也不提供外部质量矩阵。袋身质量矩阵由几何和质量自动计算。动作是静置1秒、1～4秒上提25厘米、6～7秒横移5厘米，保持至9秒。从网页导入与随附纸袋 USD 内容相同的文件（包括 ZIP 内该文件），会自动绑定此固定动作；其他 USD 不附带动作。用户的 dt、接触和物体参数不会被例子覆盖。

不再硬性要求某个 libuipc 版本字符串。stitch 在 World 初始化前显式复制并读回节点对，兼容旧绑定临时数组问题；外力标记兼容新旧属性名。已验证本机 Genesis 1.3.1 配合修复版 pyuipc 0.0.29，以及现有带塑性扩展的 0.0.28 包。也使用已安装的 Genesis 1.4.1 源码，在相同 CPython 3.11/原生环境中验证导入和物理测试；适配其 Stage 上下文管理器及显式 dt 接口。Python 二进制包需要与解释器版本匹配；没有把 CPython 3.11 原生包冒充为 3.12 兼容包。requirements 增加实际验证使用的 usd-core。体积塑性仍需要 native/ 的 v2 扩展；后端没有时前端禁用对应物体的选项，API 明确拒绝，不能默默按弹性运行。现有 OBJ 薄壳及其塑性逻辑保留。

启动时可使用现有 `-UipcPackageDir` 选择兼容的完整原生包（目录直接包含 uipc/）；不修改 site-packages：

```powershell
./start.ps1 -GenesisPython <Python路径> -UipcPackageDir <原生包目录>
python examples/paperbag.py --output runtime/paperbag
$env:IPC_GPU_TEST='1'
python -m unittest discover -s tests -p test_usd.py -v
python -m unittest discover -s tests -p test_usd_interface.py -v
```

测试覆盖自动 USD 四种 FEM/ABD 组合、实际原生材质系数、固定关节、断开对照、错误回滚、纸袋自动导入、单位/坐标转换、力平衡、HTTP 上传、单物体覆盖与分区、保存重载和原有 WebSocket 消息。源码包含 USD 和原引用贴图；上传资产、缓存、截图、视频和报告留在 runtime/ 或工作区输出目录，不提交。

## 版本管理

Git 跟踪后端、网页及 Three.js 许可证、启动脚本、依赖说明、场景配置和回归测试。根目录 model.obj 是本地工作网格，取消跟踪但保留在磁盘；运行当前场景仍需要它，换机或新检出仓库时须自行提供模型或更改 mesh 路径。benchmarks/ 中的新实验、报告及结果只在本地保留，已有回归测试依赖的脚本和网格仍跟踪。本文中的实验报告链接仅供本地查看。

.venv/、runtime/、dist/ 与 Python 缓存不纳入版本管理；这些目录不会被删除。runtime/ 中的日志、矩阵、仿真快照和图表需在本机运行相应实验生成，报告中的 runtime 链接依赖这些本地结果。dist/ 中的 ZIP 是交付产物，其核心程序、网页与测试网格均已作为源文件跟踪。包内 BACKEND.md 的接口内容已包含在根目录 README.md，旧 MANIFEST.sha256 仅适用于该次打包。
