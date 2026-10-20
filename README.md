# Genesis 软体工具

输入只有 scene.json 和 OBJ；运行 ./start.ps1，打开 http://127.0.0.1:3000。

启动脚本默认使用项目目录中的 .venv/Scripts/python.exe，不依赖开发者机器的路径。首次使用请按下文创建环境并安装依赖。也可运行 ./start.ps1 -GenesisPython <解释器路径>，显式复用已安装所需依赖和 CUDA PyTorch 的环境；启动脚本不会自动安装依赖。已验证的依赖版本为 Genesis 1.4.1、torch 2.8.0+cu126、pyuipc 0.0.28。

## 配置

完整调参说明：[参数指南](static/parameter-guide.html)。启动后可点击网页右上角「调参说明」，或打开 http://127.0.0.1:3000/static/parameter-guide.html。覆盖材料、薄壳塑性、网格、接触、时间步、固定点和点力；体积塑性需项目原生 CUDA 扩展，尚未按真实材料标定。


薄壳和体积软体均绑定 Genesis IPC。Genesis 使用 gs.cuda，IPC 使用独立 CUDA 引擎；要求 CUDA 版 PyTorch 和可用 NVIDIA GPU，无法使用 CUDA 时直接报错，不回退 CPU。OBJ/TetGen 预处理、Python 输入整理、网页通信和结果取回仍包含 CPU 工作。

- body_type：shell 或 volume。
- mesh：项目内 OBJ 路径，单位 m。直接使用 Genesis 内置读取函数，不自写 OBJ 解析。
- tet_max_volume_m3：-1 自动划分，正数指定最大四面体体积（m³），仅体积模式使用。正数会允许 TetGen 细分边界。
- dt：每次 step 对应的时间（s）；子步数使用 Genesis 默认值 1。
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

交付后端：app/、native/、run.py、requirements.txt、scene.json、model.obj、README.md；联调附 static/ 与 start.ps1。当前体积功能还须携带完整的 runtime/plasticity-only/package/（包含 uipc Python 包、原生库和包内元数据），不能排除整个 runtime。可排除 .venv/、__pycache__/、runtime 中其余构建中间文件、缓存和实验结果。原生包可能未被 Git 跟踪，需要单独作为交付附件分发。

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

## 版本管理

Git 跟踪后端、网页及 Three.js 许可证、启动脚本、依赖说明、场景配置、OBJ 网格，以及 benchmarks/ 中的测试脚本和调研报告。OBJ 是仿真输入，不作为编译产物忽略。

.venv/、runtime/、dist/ 与 Python 缓存不纳入版本管理；这些目录不会被删除。runtime/ 中的日志、矩阵、仿真快照和图表需在本机运行相应实验生成，报告中的 runtime 链接依赖这些本地结果。dist/ 中的 ZIP 是交付产物，其核心程序、网页与测试网格均已作为源文件跟踪。包内 BACKEND.md 的接口内容已包含在根目录 README.md，旧 MANIFEST.sha256 仅适用于该次打包。
