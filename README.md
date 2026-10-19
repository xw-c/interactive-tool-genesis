# Genesis 软体工具

输入只有 scene.json 和 OBJ；运行 ./start.ps1，打开 http://127.0.0.1:3000。

当前直接复用 D:/xwchen/genesis-garment-demo/.venv/Scripts/python.exe：Genesis 1.4.1、torch 2.8.0+cu126、pyuipc 0.0.28，不需要重新安装。需要换已有环境时，用 start.ps1 -GenesisPython <解释器路径>。不要用本项目 .venv 中的旧 CPU PyTorch 环境启动。

## 配置

薄壳和体积软体均绑定 Genesis IPC。Genesis 使用 gs.cuda，IPC 使用独立 CUDA 引擎；要求 CUDA 版 PyTorch 和可用 NVIDIA GPU，无法使用 CUDA 时直接报错，不回退 CPU。OBJ/TetGen 预处理、Python 输入整理、网页通信和结果取回仍包含 CPU 工作。

- body_type：shell 或 volume。
- mesh：项目内 OBJ 路径，单位 m。直接使用 Genesis 内置读取函数，不自写 OBJ 解析。
- tet_max_volume_m3：-1 自动划分，正数指定最大四面体体积（m³），仅体积模式使用。正数会允许 TetGen 细分边界。
- dt：每次 step 对应的时间（s）；子步数使用 Genesis 默认值 1。
- gravity：三维重力（m/s²）。
- material：youngs_modulus_pa、poisson_ratio、density_kg_m3、self_friction；薄壳另有 thickness_m、bending_model（DiscreteShellBending 或 null）、bending_rigidity_nm。
- pins：表面固定点编号；forces：[{"vertex":0,"vector":[0,0,0.001]}]，单位 N。

薄壳材料绑定原生 Cloth / Baraff–Witkin；体积绑定原生 Elastic / IPC Stable Neo-Hookean。不再提供无实际作用的膜模型、体积模型或求解器选择。

保留数学/物理要求和数据格式校验：正的刚度、密度、厚度、dt，0<nu<0.5，非负弯曲刚度与摩擦，有限数、三维向量、合法表面编号。删除人为设置的最大刚度、密度、厚度、点力、数量、时间步和重力范围，以及旧 elastic_moduli 兼容代码。Pydantic 在读取文件、保存配置、configure、更新点力或固定点时校验；推进时不重复校验配置。失败通过 HTTP 400 或 WebSocket error 返回，网页显示错误。

## 表面与体积

volume 输入必须封闭并有一致的朝外法线，Genesis 自动调用 TetGen 生成内部四面体。shell 直接使用三角形。

所有公开编号均对应 configure / snapshot 返回的 positions（从 0 开始），不是原始 OBJ v 行号，也不是体积内部的全局顶点号。前端只收到 entity.surface_triangles 引用的表面顶点，包括划分新增的表面点；内部顶点不能选择、施力或固定。模型、划分参数或引擎版本变化后应重新选择点。

点力合并、表面编号映射在 CPU 上处理用户输入时完成；每步由 uipc 内置动画接口将外力数组送入 CUDA 求解器。不实现材料、能量、积分、碰撞或优化算法。

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

## 后端文件

| 文件 | 内容 |
| --- | --- |
| app/config.py | 参数模型，无自定义 valid 函数 |
| app/mesh.py | Genesis OBJ loader 的薄包装及必要几何检查 |
| app/engine.py | 创建原生场景、注册内置 IPC 外力、推进和读取表面坐标 |
| app/interface.py | 文件读写与会话创建/固定点接口，不实现求解 |
| app/server.py | HTTP / WebSocket 路由与串行工作线程，不实现求解 |
| app/__init__.py | 当前 Windows 环境兼容与独立缓存目录（含 Numba，避免继承其他项目的旧编译缓存） |

engine.py 不再检查版本必须等于 1.4.4；requirements.txt 记录所复用环境的依赖版本，供新机器部署参考。注册外力需要调用一处 Genesis 内部 IPC 构建接口，升级后该接口可能变化，不保证所有未来版本直接兼容。

FiniteElementExternalForce 是 from uipc.constitution 直接导入的类。创建实例、注册到 constitution_tabular、apply_to 物体、写入输入数据，是启用 uipc 内置外力功能；不是安装额外软件。

交付后端：app/、run.py、requirements.txt、scene.json、model.obj、README.md。演示网页附 static/ 与 start.ps1。排除 .venv/、runtime/、__pycache__/。接收方使用 Python 3.11 安装依赖，IPC 要求 NVIDIA CUDA GPU。当前 Quadro RTX 8000 已验证；当前依赖使用 CUDA 12.6 版 PyTorch。

以下安装命令仅供新机器部署；本机已配置好已有环境，直接运行 start.ps1：

    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --no-cache-dir setuptools wheel
    .\.venv\Scripts\python.exe -m pip install --no-cache-dir --no-build-isolation -r requirements.txt
    .\.venv\Scripts\python.exe run.py


IPC 接触激活距离通过 scene.json 顶层 `contact_d_hat_m` 设置，默认 `0.0005` m（0.5 mm），网页同名输入也可调整；直接传给 Genesis 的 `IPCCouplerOptions(contact_d_hat=...)`。旧 JSON 缺少此字段时同样采用 0.5 mm。
每次场景初始化使用实际仿真表面网格做一次 O(面数) 的局部尺度检查：最短边、最小三角形高与接触范围比较，薄壳自身接触计入实际厚度。结果在配置响应 `contact_check` 返回，网页显示预警；不拦截、不自动调参。这是保守尺度预警，不是完整邻域距离/自碰撞检测，也不保证变形后无虚假接触。1w 薄片在 0.5 mm 默认值下不触发此尺度预警；更细或瘦长的面片仍可能触发。预警会指出虚假自接触、异常排斥/膨胀、人工变硬与步进变慢风险，并输出到后端日志及网页。


网页配置生效规则：切换薄壳/体积只更新表单，点击「应用设置并重置」后重建场景、归零时间；当前 OBJ 和已填参数保留，不自动导入另一套默认参数。「导入 JSON」读取文件后立即调用 `SimulationAPI.configure(JSON.parse(text))` 重建当前会话，无需再次应用；不会自动保存 scene.json。JSON 的 mesh 是后端项目内已有 OBJ 的路径，不包含或上传 OBJ 数据。
前端 JSON 导入使用已有 configure 接口，无单独文件上传路由：`await api.configure(JSON.parse(jsonText))`。后端对应 `Simulation.configure(data)`；若要持久化配置，另调用 `api.saveProject(config)`（PUT /api/project）。

## 版本管理

Git 跟踪后端、网页及 Three.js 许可证、启动脚本、依赖说明、场景配置、OBJ 网格，以及 benchmarks/ 中的测试脚本和调研报告。OBJ 是仿真输入，不作为编译产物忽略。

.venv/、runtime/、dist/ 与 Python 缓存不纳入版本管理；这些目录不会被删除。runtime/ 中的日志、矩阵、仿真快照和图表需在本机运行相应实验生成，报告中的 runtime 链接依赖这些本地结果。dist/ 中的 ZIP 是交付产物，其核心程序、网页与测试网格均已作为源文件跟踪。包内 BACKEND.md 的接口内容已包含在根目录 README.md，旧 MANIFEST.sha256 仅适用于该次打包。
