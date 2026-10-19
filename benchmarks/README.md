# 1k / 10k 输入表面顶点测试

| 配置 | OBJ 顶点 | 三角形 | 几何 |
| --- | ---: | ---: | --- |
| shell_1k.json | 1000 | 1872 | 20 cm 正方形薄片，25×40 网格 |
| shell_10k.json | 10000 | 19602 | 20 cm 正方形薄片，100×100 网格 |
| volume_1k.json | 1000 | 1996 | 半径 10 cm 的封闭球面 |
| volume_10k.json | 10000 | 19996 | 半径 10 cm 的封闭球面 |

OBJ 与对应 JSON 同名。薄片高度及球心高度为 0.20 m；单位 m。四个配置使用相同 E、nu、密度、dt、重力；默认没有固定点和外力，作为自由下落基线。可以在网页选点施力或固定，测试更复杂的负载。

顶点数按输入 OBJ 表面统计。体积模型由 Genesis/TetGen 进一步划分，真正求解顶点和四面体会更多，精确数值见场景统计或命令输出。球面不是预制四面体网格。

## 网页使用

启动根目录 start.ps1，在网页点击“导入 JSON”，选择此目录中的配置。无需覆盖 scene.json / model.obj。更换网格或划分密度后重新选择受力点和固定点。

网页每步耗时包含 scene.step 和坐标取回；动画帧率还受 JSON、WebSocket、浏览器渲染及请求节奏影响。

## 命令行测量

在项目根目录运行，每个命令是独立进程：

    & "D:\xwchen\genesis-garment-demo\.venv\Scripts\python.exe" benchmarks/run.py shell_1k
    & "D:\xwchen\genesis-garment-demo\.venv\Scripts\python.exe" benchmarks/run.py shell_10k
    & "D:\xwchen\genesis-garment-demo\.venv\Scripts\python.exe" benchmarks/run.py volume_1k
    & "D:\xwchen\genesis-garment-demo\.venv\Scripts\python.exe" benchmarks/run.py volume_10k

可设置 --warmup 5 --steps 100；10k 模型建议先 --warmup 0 --steps 1，确认耗时再增加步数。默认预热 1 步后测量 5 步；构建/划分/首次编译单独报告，不计入步耗时。首次运行可能很慢，不宜和缓存命中的构建时间直接比较。

输出两种时间：advance_readback_ms 包含推进和 GPU/CPU 坐标取回；full_step_json_ms 还包含状态列表构造与 JSON 编码，不含网络和网页渲染。两者都不是独立的 GPU 内核耗时。当前场景采用 Genesis CUDA 后端 + 独立 IPC CUDA 求解器。下面保留的 CPU 后端数据是切换前的历史记录，不能当作当前 GPU 版结果。

## 本机原生划分核验

Genesis 1.4.4 默认 TetGen 参数及后续表面四面体分割下：

| 模型 | 输入表面顶点 | 全部求解顶点 | 四面体 |
| --- | ---: | ---: | ---: |
| volume_1k | 1000 | 1639 | 6806 |
| volume_10k | 10000 | 16628 | 70319 |

更换 Genesis/TetGen 版本或划分参数后，数量可能变化。

薄壳简短核验（自由下落、预热 1 步、测量 3 步，切换前 CPU Genesis + IPC CUDA 组合）：1k 的推进及取回中位数约 328 ms；10k 约 20302 ms。样本很少，不能当作稳定性能结论；它已说明当前工具的 10k 薄壳运行很慢，且不是 JSON 编码主导。默认脚本会用更多步数复测。

四份配置均已在本机完成 IPC/CUDA 场景构建与推进检查。体积 1k/10k 仅各检查首步，约 771/3693 ms，包含首次执行开销，不能与预热后的薄壳样本当作严格性能对比。


## CUDA 实测（2026-10-16）

复用 genesis-garment-demo 的环境：Genesis 1.4.1、Torch 2.8.0+cu126、pyuipc 0.0.28，Quadro RTX 8000 48 GB。Genesis 后端和 IPC 求解均为 CUDA，float64。四个模型逐个进程串行运行；自由下落、无固定点/外力，dt=0.005 s，预热 1 步，正式测量 5 步。样本较少，仅为短测，不代表接触、自碰撞或持续交互负载。

| 模型 | 全部求解顶点 | 四面体 | 构建 / s | 求解＋取回中位数 / ms | 加 JSON 中位数 / ms | 加 JSON P95 / ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| shell_1k | 1000 | 0 | 18.598 | 282.702 | 284.715 | 382.507 |
| shell_10k | 10000 | 0 | 41.645 | 22479.111 | 22500.055 | 23022.126 |
| volume_1k | 1639 | 6806 | 19.039 | 70.285 | 72.325 | 78.135 |
| volume_10k | 16628 | 70319 | 32.813 | 659.134 | 679.062 | 3933.088 |

构建时间包含场景准备、体积划分及可能的编译，不含在步耗时内；缓存状态会影响构建时间。步耗时不含网络和浏览器渲染，不能作为网页实际 FPS 或独立 GPU 内核时间。CPU 仍负责配置、网格准备/四面体划分、力参数整理、IPC 输入/结果交换及 JSON 通信，因此不是整条链路都在 GPU。

本次 10k 薄壳约 22.5 s/步，当前不能用于实时交互；10k 体积中位数约 679 ms，且有秒级慢步。JSON 编码占比小，尚未进一步定位求解、同步等各环节的具体瓶颈。旧 CPU 主场景测试使用 Genesis 1.4.4、不同 Torch，IPC 也已在 CUDA，不能把两次差异解释为纯 CPU/GPU 加速比。原始输出保存在 runtime/gpu-*.log。


## 10k 薄壳慢步定位（2026-10-16）

使用同一 GPU 环境、同一 shell_10k 网格和材料，逐进程串行对照。只覆盖原生 IPCCouplerOptions；没有修改 app 生产参数、材料本构或积分算法。每组预热 1 步，测量 2 步；第一测量步开启 cProfile，故这是定位用短测，不是稳定 FPS 测试。

| 原生 IPC 设置 | 测量步 1 / ms | 测量步 2 / ms |
| --- | ---: | ---: |
| 默认 contact_d_hat=0.01 m，接触开启 | 22833.885 | 21555.435 |
| contact_d_hat=0.0005 m，接触开启 | 68.042 | 99.726 |
| contact_enable=False，仅诊断 | 57.263 | 89.916 |

默认组的单步 22.835 s 中，uipc.World.advance 占 22.821 s（约 99.94%），Genesis 的 FEM 状态同步约 2 ms、get_state 约 2 ms。主要耗时已定位到原生 IPC 推进，非网页、JSON 或本工具的点力整理。

20 cm 薄片在 100×100 网格下的间距约 2.02 mm，默认接触距离却为 10 mm。仅调整此距离就消除了绝大部分耗时，关闭接触的结果与小距离接近。默认组 3 步后 XY 范围约 ±0.109315 m（初始 ±0.1 m）；小距离组约 ±0.09999997 m。证据指向接触范围与细密共面网格不匹配引起的接触处理/排斥负担；尚未对原生 CUDA 内核分别计时，不能进一步断言 BVH、接触组装、线性系统各自占比。

0.5 mm 是当前模型对照值，不是所有模型的通用最佳值。contact_d_hat 是接触屏障的作用距离，与几何厚度不同；缩小它会改变接触行为。本次未关闭生产碰撞，也未更改原有配置。短时自由下落的改善不代表落地、折叠或自碰撞时仍有相同速度。

复现：使用已有 GPU Python 运行 runtime/profile_shell.py baseline / small_contact / no_contact；完整调用剖析保存在 runtime/profile-*.log。

FEM 支持范围：本机 Genesis 1.4.1 的 FEM.Cloth 是有限元薄壳材料，由 IPCCoupler 中的 StrainLimitingBaraffWitkinShell 与可选 DiscreteShellBending 推进。原生 FEMSolver 对 cloth 只注册顶点/三角形用于状态跟踪和耦合，没有独立薄壳推进路径。因此不能把此 Cloth 切换到非 IPC 的内置 FEM 求解器；不等于有限元方法本身不支持薄壳。官方源码：https://github.com/Genesis-Embodied-AI/genesis-world/blob/main/genesis/engine/materials/FEM/cloth.py


历史参数更新（本次已改为下文的 0.5 mm）：四份示例配置当时显式加入 `contact_d_hat_m: 0.002`（2 mm）。上面的历史性能表仍对应当时 IPC 默认 10 mm；不能与更新后的配置混淆。重新运行会按 JSON 内设置测量。1w 薄壳在 2 mm 下仍触发初始化尺度预警，复现此前小距离结果时请设为 0.0005 m。


## 最新默认 0.5 mm：四组复测

默认配置及四份测试 JSON 均设为 `contact_d_hat_m: 0.0005`。沿用 Genesis 1.4.1、Torch 2.8.0+cu126、IPC CUDA、RTX 8000，float64。自由下落，无外力和固定点，dt=0.005 s。各模型独立进程串行测量，预热 3 步、测量 20 步，共模拟 0.115 s；构建/划分/编译另计。

| 模型 | 构建 / s | 求解与取回中位数 / ms | 加 JSON 中位数 / ms | 加 JSON P95 / ms | 尺度预警 |
| --- | ---: | ---: | ---: | ---: | --- |
| shell_1k | 15.721 | 40.822 | 42.974 | 87.211 | 无 |
| shell_10k | 16.102 | 127.175 | 147.293 | 480.764 | 无 |
| volume_1k | 16.196 | 22.987 | 25.157 | 28.41 | 无 |
| volume_10k | 18.052 | 79.405 | 99.443 | 111.058 | 有 |

不含网络和前端渲染，不能作为网页 FPS；未覆盖落地、折叠和密集自碰撞。与此前默认 10 mm 的短测相比，接触参数、预热和样本数均变化，旧表只供参考。10k 体积求解仍为 16628 顶点、70319 四面体；其表面最短边 0.344 mm、最小三角形高 0.0763 mm，低于默认接触尺度，已在后端实际输出 Warning。预警仅提示局部虚假自接触、异常排斥/膨胀、人工变硬和慢步风险，不判定碰撞已发生，不拦截。

复现：使用上面已有 GPU Python 命令并加 `--warmup 3 --steps 20`。输出现包含实际 contact_d_hat_m、contact_check 与每步样本；完整日志位于 runtime/gpu-dhat05-*.log。


## IPC / AL-IPC 对照（2026-10-18）

结论：本工具四组模型的自由下落和简单落地测试中，AL-IPC 没有提速。当前网页和交付包保持 IPC，未修改已安装环境。

接口：Genesis 1.4.1 的 contact_constitution 只接受 ipc/isometric，直接填 al-ipc 会报校验错误；pyuipc 0.0.28 CUDA 后端支持 al-ipc，已创建并步进验证。小型参数类型扩展可让 Genesis 原样转发，无需改求解算法：

```python
from typing import Literal
import genesis as gs
class ALIPCOptions(gs.options.IPCCouplerOptions):
    contact_constitution: Literal['ipc','isometric','al-ipc']='al-ipc'
options = ALIPCOptions(contact_d_hat=0.0005)
# gs.Scene(..., coupler_options=options, ...)
```

RTX 8000、Genesis CUDA + libuipc CUDA、float64；Genesis 1.4.1 / Torch 2.8.0+cu126 / pyuipc 0.0.28。成对测试只切换接触算法，材料、摩擦、dt=0.005、d_hat=0.0005、原生容差相同；但相同参数不代表两种算法达到严格相同的误差水平。

自由下落预热 3 步、测 20 步（累计 0.115 s）；落地预热 5 步、测 20 步（累计 0.125 s）。落地场景通过原生网格变换将最低点放到地面上方 1 mm。独立进程串行运行。耗时包含求解、坐标取回和 JSON，不含网络及网页渲染；构建/划分/首次编译另计。

| 场景 | 模型 | IPC 中位数 / ms | AL-IPC 中位数 / ms | AL 耗时 / IPC 耗时 |
| --- | --- | ---: | ---: | ---: |
| 自由下落 | shell_1k | 42.4 | 103.9 | 2.45 |
| 自由下落 | shell_10k | 146.3 | 357.1 | 2.44 |
| 自由下落 | volume_1k | 26.6 | 51.0 | 1.92 |
| 自由下落 | volume_10k | 102.8 | 211.3 | 2.05 |
| 落地接触 | shell_1k | 31.7 | 53.1 | 1.67 |
| 落地接触 | shell_10k | 48.2 | 179.5 | 3.72 |
| 落地接触 | volume_1k | 25.6 | 42.5 | 1.66 |
| 落地接触 | volume_10k | 87.5 | 153.4 | 1.75 |

效果：
- 各组均完成；成对初始表面位置及面拓扑一致。采样坐标为有限数，落地采样表面未低于地面/薄壳半厚度边界；检查到的最终体积网格没有翻转四面体。这不是完整连续轨迹的无穿透证明。
- 1w 体积网格含瘦长面片，会触发尺度预警，结果不能直接推广到优质网格。
- 1w 体积自由下落 23 步，隐式 Euler 解析位移 -67.689 mm；IPC 表面位置 RMS 误差 11.016 mm，AL-IPC 为 3.55e-08 mm。AL-IPC 更接近无接触自由落体，IPC 出现局部非物理形变。
- IPC 接触前有屏障排斥，AL-IPC 使用增广拉格朗日活跃集；接触间隙、瞬态形变不保证一致。

固定点/点力：1k 薄壳和体积各跑有力/无力控制组，固定表面点 0/1，在点 2 加 Z 向力。

| 模型 | 方法 | 力 / N | 固定点误差 / m | 相对无力组的点 2 位移差 / mm |
| --- | --- | ---: | ---: | ---: |
| shell_1k | ipc | 0.0001 | 0 | 0.101732 |
| shell_1k | al-ipc | 0.0001 | 0 | 0.059392 |
| volume_1k | ipc | 0.01 | 0 | 0.0540451 |
| volume_1k | al-ipc | 0.01 | 0 | 0.0568689 |

注意：薄壳 0.0001 N 小力测试中，AL-IPC 相对无力组的点 2 Z 位移为 -0.0483885 mm，方向与 IPC 不同，不能据此认定精细小力响应已验证。补充零重力 AL-IPC 对照：在同一点加 0.001 N，点 2 Z 向位移差 3.03648 mm，固定点误差 0 m。此测试用于判断力确实起作用，不替代收敛性验证。

限制：短时单体/地面测试，未覆盖多层折叠、密集自碰撞、多物体挤压和长时间漂移；未做严格参考解的全面收敛测试。当前不建议为了提速直接切换默认算法。
参考：[libuipc 接口](https://spirimirror.github.io/libuipc-doc/specification/scene_configs/contact/#ipc-versus-al-ipc)、[AL-IPC 论文与案例](https://simulation-intelligence.github.io/barrier-free/)。论文高密度接触场景的最大加速比不能套用到本工具。
复现：GPU Python 运行 benchmarks/compare_contact.py --suite。机器可读汇总：benchmarks/alipc_comparison.json；逐步数据及状态：runtime/alipc-comparison/。


1k 布料的矩阵条件数、PCG/Newton 迭代、参数对照及 CUDA 内核 profiling：见 [IPC_PROFILE.md](IPC_PROFILE.md)。复现脚本为 profile_ipc.py、summarize_ipc_profile.py、plot_ipc_profile.py；仅诊断，不修改生产参数。


薄壳论文、LibShell 对照、参数含义与原生膜 Hessian 核查：见 [CLOTH_MODEL_AUDIT.md](CLOTH_MODEL_AUDIT.md)。

面向建模师的参数接口建议、拉伸硬化曲线、原生方向刚度与四组加载测试：见 [CLOTH_ARTIST_PARAMETERS.md](CLOTH_ARTIST_PARAMETERS.md)。脚本为 cloth_response.py、summarize_cloth_response.py；保持原求解精度，未修改生产界面或默认材质。
