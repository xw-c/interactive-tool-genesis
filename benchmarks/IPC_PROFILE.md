**1k 布料 IPC 性能剖析（2026-10-19）**

结论：当前瓶颈是薄壳线性系统的谱条件与默认块对角预条件器的组合。优先验证 MAS；同时校准材料接口的剪切/厚度单位约定。Newton 外层通常只做两次线性求解，单次 PCG 却可能需要数百次迭代。单纯放宽容差会损失精度，缩小 dt 也没有降低相同物理时间的成本。本次只新增诊断脚本、结果和本文，app/、场景 JSON、已安装依赖均保持原样。

模型与参数解释已于后续核查澄清：BW 是独立拉伸/剪切的各向异性模型，剪切 10 不是唯一正确值；当前问题集中在 Genesis 单组 Pa 模量与 libuipc 独立二维剪切约定的适配。见 [CLOTH_MODEL_AUDIT.md](CLOTH_MODEL_AUDIT.md)。原性能和矩阵测量保留。后续精度设置按用户要求保持不变。

**测量口径**

使用 benchmarks/shell_1k.json：1000 顶点、1872 三角形、3000 自由度，20 cm 正方形，E=100000 Pa、nu=0.25、rho=360 kg/m³、完整厚度 0.25 mm、弯曲刚度 5e-8 N·m、dt=0.005 s、d_hat=0.5 mm。最短边 5.128 mm、最小三角形高 4.367 mm；计入厚度的接触激活范围 0.75 mm，初始化尺度检查通过。

Quadro RTX 8000 / WDDM；Genesis 1.4.1 CUDA、Torch 2.8.0+cu126、pyuipc 0.0.28，uipc Release、FP64。本机原生默认实际为 fused_pcg、CUDA Graph=1、FEM preconditioner=diag、tol_rate=1e-3、semi_implicit=1、Newton min_iter=0，和 Genesis 参数说明中的部分默认值不同。以实际 resolved_config 和 environment.json 中的二进制 SHA256 为准；本机 D:/xwchen/libuipc 源码与安装包的接口也不完全一致。

各 GPU 测试按独立进程串行运行。主要对照预热 5 步、测量第 6–35 步，共覆盖到 0.175 s，自由下落尚未落地。full_ms 包含推进、坐标取回、状态列表与 JSON 编码，不含网络、浏览器和构建。另用 cProfile 分析一个额外步，故结果中的最终坐标通常对应第 36 步（0.18 s），不属于计时样本。

原生 Timer 在作用域边界同步 CUDA，详尽日志也有开销。因此分解表使用独立的 15 步插桩运行；正式速度优先使用 clean 无原生 Timer、无 verbose 日志结果。每组一次短测，P95 对样本和运行波动敏感，不视为稳定 FPS 或跨硬件结论。

**保持材料、dt 和接触设置的加速对照**

| Case (clean) | Median ms | P95 ms |
| --- | ---: | ---: |
| Free fall / diag | 45.79 | 110.56 |
| Free fall / MAS | 23.89 | 27.14 |
| MAS + linear tol 1e-4 | 27.42 | 32.92 |
| MAS + linear 1e-4 + Newton 0.005 | 95.20 | 99.38 |
| Pinned / diag | 126.40 | 190.42 |
| Pinned / MAS | 43.24 | 84.64 |
| Landing / diag | 16.82 | 100.50 |
| Landing / MAS | 18.69 | 57.58 |

自由下落的 clean 基线约 45.8 ms，MAS 约 23.9 ms；相同 30 步总成本约 1.59 s 和 0.72 s。固定点组选点 0、24；落地组将薄片最低点放到地面上方 1 mm。落地后的平铺静止阶段外层和 PCG 工作少，MAS 没有稳定的中位数收益。P95 改善和整体倍数仍需长时间接触场景复测。

MAS 通过原生场景配置 linear_system/fem_preconditioner='mas' 启用。本机 Genesis IPCCouplerOptions 没有暴露此字段，脚本仅在本进程的 build_ipc_scene_config 返回值中注入。当前安装包没有 uipc.geometry.mesh_partition；不应直接照搬本机另一份 libuipc 源码的显式分区 API。尝试显式分区失败的日志保留，但不计入有效性能结果。

结果图：[profiling.png](D:/xwchen/interactive-ipc/runtime/ipc-profile/profiling.png)

**收敛实际发生了什么**

默认 verbose 基线 30 步共 66 次线性求解，即平均 2.2 次/步；单次 PCG 平均 270.9、最大 880。最慢第 21 步耗时 127.7 ms，三次求解累计 1885 次 PCG。单步耗时与该步累计 PCG 次数的 Pearson 相关系数为 0.9865。MAS 相同 verbose 对照平均两次求解/步，PCG 平均 13.7、最大 40。

原生日志的 “Converged with Iteration Count: 1” 是从 0 开始的最后迭代索引，对应两次求解；不能误读为仅求解一次。默认组 15 步插桩运行恰好 30 次求解、30 次线搜索迭代，通常一次线搜索即可接受。Newton 最大上限 1024、线搜索上限 8 没有成为这些步骤的瓶颈；压低上限不能缩短已提前收敛的步骤。30 步中少量步骤需要 3–4 次求解，形成慢帧的一部分。

Newton velocity_tol=0.05 对应 dt×tol=0.00025 m 的平移步长阈值，即 0.25 mm，精度设置本身已经不严格。线性 tol_rate 则是预条件残差二次量的比例，不能直接理解成位移误差 0.1%，也不等同于普通残差范数比例。

**为什么线性系统难解：导出矩阵验证**

在第 1 步、第 0 次 Newton 求解处导出 A、b、x，并在 CPU 上对完整对称矩阵求极端特征值。原生导出保存上三角 3×3 块；分析脚本按 A=upper+upper.T-blockdiag 重建，验证对称性和正定特征值，而非直接分析半存储矩阵。

| Initial matrix | cond(A) | cond(block-Jacobi whitened A) |
| --- | ---: | ---: |
| matrix-baseline | 1.720e+07 | 5.339e+06 |
| matrix-shear_scaled | 1.040e+04 | 2.938e+03 |

基线有 6616 个唯一上三角块，规模很小，却具有很大的高/低刚度模态差异。Hessian 最小特征值约 1.04e-6，最大约 17.87；局部块对角预条件只能做局部缩放，仍留下约 5.34e6 的条件数，难以消除跨顶点的剪切耦合与低频模态。条件数是这份静止初态矩阵的结果，后续变形/接触时会改变，不能把它当成整段轨迹的常数。

已经安装的 constitution 二进制实测生成 lambda=26.6667、mu=40000、strain_rate=100。这里的属性分别承担有效拉伸/剪切系数。以常规三维 Young 模量输入解释，拉伸系数符合 E*t/(1-nu²)=26.6667 N/m，而 G*t=E*t/[2(1+nu)]=10 N/m；当前剪切项仍是 40000，较单纯 G*t 换算对照高 4000 倍。该对照尚未处理能量前因子；按当前 BW 写法匹配线弹性剪切响应应取 kh=G*t/2=5，详见 [CLOTH_ARTIST_PARAMETERS.md](CLOTH_ARTIST_PARAMETERS.md)。但 libuipc 有意把它作为独立二维有效系数，因此不能直接判为剪切公式漏乘厚度。明确的问题是 Genesis 单组 E、nu 与该接口的语义适配；10 也不是 BW 模型唯一正确的剪切值，详见后续模型核查。

shear_scaled 诊断只将 mu 乘以完整厚度 0.00025，从 40000 变成 10，保持质量、拉伸、弯曲、dt、接触和求解容差相同。它使条件数显著下降，clean 单步约 23.0 ms。此操作改变本构行为，属于定位证据；是否采用该换算，应先明确产品参数的单位及目标布料的拉伸/剪切标定，不能作为保持原材料不变的优化。

首个导出系统还用 CPU 稀疏直接解作线性参考：默认 PCG 已满足预条件二次残差比约 6.31e-4，普通相对残差约 1.94%，相对解向量误差约 2.63%。这说明容差字段达标与位移解足够准确是两个判断。此首帧 z 向子空间在两种剪切设置下相同，不能用该首帧的解误差衡量后续材料对照的全部差异。

**时间花在哪里**

以下为第 6–20 步、原生计时开启的均值，不能与 30 步 clean 中位数直接相加比较。

| 环节 | ms/步 | 占原生 IPC Pipeline |
| --- | ---: | ---: |
| 线性求解 | 21.32 | 60.6% |
| 线性系统组装、BCOO 转换、预条件器组装 | 5.66 | 16.1% |
| 线搜索（含能量、轨迹候选、CCD） | 4.08 | 11.6% |
| 其他 DCD 候选检测 | 2.49 | 7.1% |
| Pipeline 其余工作 | 1.61 | 4.6% |
| IPC Pipeline 总计 | 35.17 | 100% |

同组完整 step+JSON 均值 45.28 ms，差额约 10.11 ms 为 Genesis 推进/耦合/取回、状态构造、JSON 等，其中 JSON 编码均值约 1.98 ms。cProfile 也显示主要时间位于 uipc.World.advance；坐标读取约 2 ms，另有 Genesis 刚体/状态同步等工作。零重力静止诊断仍约 16.6 ms/步，提示降低 PCG 后会受到管线固定成本限制。

**其他参数对照**

| Case (verbose diagnostic) | Median ms | Solves/step | PCG/solve mean | PCG max |
| --- | ---: | ---: | ---: | ---: |
| baseline | 49.85 | 2.20 | 270.9 | 880 |
| mas | 24.82 | 2.00 | 13.7 | 40 |
| no_contact | 44.18 | 2.27 | 243.9 | 635 |
| wide_contact | 145.07 | 2.00 | 178.0 | 465 |
| no_bending | 86.94 | 3.43 | 313.8 | 1655 |
| soft | 67.11 | 3.10 | 192.7 | 755 |
| small_dt | 26.38 | 2.67 | 37.3 | 180 |
| linear_loose | 35.95 | 2.47 | 114.7 | 445 |
| linear_tight | 91.12 | 2.03 | 525.2 | 1250 |
| full_newton | 48.75 | 2.10 | 254.4 | 1035 |
| plain_pcg | 75.00 | 2.20 | 257.1 | 962 |
| graph_off | 50.66 | 2.07 | 253.6 | 1030 |
| zero_gravity | 16.61 | 1.00 | 5.0 | 5 |

- 当前 0.5 mm 基线的接触 Hessian 数和地面候选均为 0；关闭接触只有有限收益。摩擦不会解释无接触时的数百次 PCG。
- d_hat=10 mm 时，接触法向 Hessian 贡献峰值约 413990 个 3×3 块（组装去重前），矩阵唯一上三角块从 6616 增到 18965；最终横向尺寸约 206.57 mm，初始为 200 mm。PCG 次数甚至低于部分基线步，耗时仍升至约 145 ms，说明此时接触候选、贡献组装和更密的系统也很重要。
- 关闭弯曲使平均求解次数和 PCG 次数增加，运行更慢；降低 E 十倍也没有得到稳定提速。改变材料会同时影响模态、迭代路径和误差，不能依据单个刚度数字预计速度。
- tol_rate 从 1e-3 放宽到 1e-2 可降到约 36 ms，但自由落体坐标 RMS 误差从约 3.42 mm 增到 14.42 mm；收紧到 1e-4 则约 91 ms。应对照误差选择容差，而非只看耗时。更换预条件器也改变残差度量，相同 tol_rate 不保证相同误差。
- dt=1 ms 的早期 30 步约 26 ms/步，仅覆盖很短运动。进一步对齐物理时间：基线预热 25 ms 后测 150 ms，共 30 步约 1.59 s；小 dt 预热同样 25 ms 后测同样 150 ms，共 150 步约 10.15 s。小 dt 后段变慢、误差也增长，不能用早期短测宣称缩 dt 提速。
- 当前已开启 fused_pcg 和五次迭代一组的 CUDA Graph。切回普通 linear_pcg 约 75 ms；关闭 Graph 的短测约 51 ms，迭代路径有波动，尚不能据此给出严格的纯 Graph 加速比。优化单次迭代的 CUDA 启动/同步仍有空间，但应先降低不必要的迭代数。

**精度与适用范围**

自由下落参考为 x_n=x_0+g*dt²*n*(n+1)/2。RMS 指所有顶点三坐标元素的均方根；另报告最大单顶点欧氏误差，避免 RMS 掩盖局部异常。参考仅用于无固定点、尚未落地的组；固定点和落地组不采用自由落体误差字段作质量评价。

| Case (clean, final snapshot) | Coordinate RMS mm | Max vertex error mm |
| --- | ---: | ---: |
| baseline-clean | 2.8852 | 8.7076 |
| mas-clean | 0.7029 | 10.7874 |
| mas_tight-clean | 0.5438 | 5.2505 |
| mas_accurate-clean | 0.1214 | 0.9828 |
| shear_scaled-clean | 0.1616 | 1.0521 |

MAS 默认设置的坐标 RMS 下降，但最大局部误差从基线约 8.71 mm 增到约 10.79 mm；它不是严格同误差条件下的两倍加速证明。仅收紧线性容差后最大误差约 5.25 mm，同时收紧 Newton 后约 0.98 mm、耗时约 95 ms。固定点最终误差：diag 为 0，MAS 约 0.505 µm；两组落地最终最低点均约 0.602 mm。实际采用方案前应按所需精度验收这些量。

所有成功组最终坐标均有限。固定点最终位置误差和落地最终高度见质量数据；这些最终快照检查不证明整个连续轨迹无穿透，也不构成严格同误差的求解器比较。测试未覆盖长时间折叠、多层自接触或交互点力扫参。

**加速优先级**

1. 在保持材料的前提下，先验证原生 MAS；后续按用户要求保持原线性/Newton 容差和精度设置，本文严格容差对照仅保留为诊断结果。当前短测支持约两倍的自由下落收益，固定点组也明显受益；静止落地不保证同样收益。
2. 明确 Young 模量、膜刚度、剪切刚度与半厚度/完整厚度的接口约定。根据目标物理模型修正转换，再做拉伸/剪切响应和接触回归。剪切尺度是当前最可疑的系统设置。
3. 保留按网格尺度选择 d_hat 的检查；密集接触时同时记录候选数、实际接触数、矩阵块数，而非仅记录总步耗时。
4. 当 PCG 降到十几次后，再优化剩下的组装/排序、每次 Newton 重建、无效候选检查、Genesis 静态地面的刚体推进与状态交换。改善预测初值、绝对加相对残差门限、减少主机同步是后续实现方向，需要单独验证数值行为。

**复现与文件**

在项目根目录用已有 GPU Python 运行：

```powershell
$python = 'D:/xwchen/genesis-garment-demo/.venv/Scripts/python.exe'
& $python benchmarks/profile_ipc.py --case baseline --tag clean --warmup 5 --steps 30
& $python benchmarks/profile_ipc.py --case mas --tag clean --warmup 5 --steps 30
& $python benchmarks/profile_ipc.py --case baseline --log --warmup 5 --steps 30
& $python benchmarks/profile_ipc.py --case baseline --timer --steps 15
& $python benchmarks/profile_ipc.py --case shear_scaled --tag clean --steps 30
& $python benchmarks/profile_ipc.py --case baseline --tag dump --dump --warmup 0 --steps 1
& $python benchmarks/profile_ipc.py --case shear_scaled --tag dump --dump --warmup 0 --steps 1
& $python benchmarks/summarize_ipc_profile.py --matrix
& $python benchmarks/plot_ipc_profile.py
```

诊断脚本：benchmarks/profile_ipc.py；汇总与矩阵谱：benchmarks/summarize_ipc_profile.py；绘图：benchmarks/plot_ipc_profile.py。逐步 JSON、最终坐标、pstats、矩阵、环境指纹和图位于 runtime/ipc-profile/，原始日志位于 runtime/profile-ipc-*.log。

**GPU 内核与主机同步（Nsight 实际采集）**

使用 Nsight Systems 2023.4.4，在同一依赖环境中预热 20 步，捕获第 21–25 步；关闭原生 Timer 和 verbose 日志，开启 Graph 节点级跟踪。捕获本身有开销，这五步不进入上面的性能对照表。

5 步产生 40294 次 GPU 内核执行、946 次 cudaGraphLaunch、2107 次 cudaStreamSynchronize。SpMV/dot 内核执行 4730 次，与每五次 PCG 迭代一组、共 946 组 Graph 一致；薄壳梯度/Hessian 内核执行 13 次，对应这五步的 13 次线性系统组装。

| GPU 内核 | 单次均值 | 本次累计 | 占累计内核时间 |
| --- | ---: | ---: | ---: |
| SpMV + dot | 12.49 µs | 59.08 ms | 27.7% |
| 块对角预条件器 apply | 7.11 µs | 33.70 ms | 15.8% |
| 弯曲梯度/Hessian | 1711.76 µs | 22.25 ms | 10.4% |
| fused update x/r | 4.62 µs | 21.88 ms | 10.3% |
| fused update p | 3.86 µs | 18.27 ms | 8.6% |
| fused dot | 2.08 µs | 9.88 ms | 4.6% |

内核累计执行约 212.96 ms，首末内核跨度约 446.12 ms。内核时间累加与多流重叠、主机 API 时间的口径不同，不能直接相加还原步耗时，也不据此推断稳定 GPU 利用率。

全部 GPU 拷贝合计约 2.17 MB、设备执行累计仅 0.717 ms，其中有 977 次仅 8 字节的 D2H 回读。cudaMemcpyAsync 主机调用累计却为 94.16 ms，cudaStreamSynchronize 累计 216.14 ms，GraphLaunch 主机调用累计 66.97 ms。同步调用包含等待 GPU 的时间，与内核时间重叠；它们共同说明收敛标量回读和启动/等待链路很频繁。cudaProfilerStart 自身约 1.23 s 是采集器启动开销，发生在步计时循环之前，不能当成仿真瓶颈。

因此，改善预条件器后，值得检查 Graph 后的等待与标量回读能否合并、是否可适当增大 check_interval，或将收敛循环完全留在设备侧。增大间隔会多执行若干次迭代，需比较实际总成本和误差。本次没有扫描该间隔，也没有做 Nsight Compute 的吞吐/占用率分析，不能据此断言 FP64 算力或显存带宽已经饱和。弯曲 Hessian 是另一个实测可见的组装热点，值得在 PCG 大幅降低后重新评估其占比。

完整捕获：runtime/ipc-profile/baseline-nsys-v2.nsys-rep；SQLite 同名；精简统计 nsys-summary.json；文本统计 runtime/profile-ipc-nsys-stats-v2.log。入口 benchmarks/profile_ipc_nsys.py 绕过 Windows venv launcher，显式复用现有环境的两处 package 路径，无依赖安装或环境修改。复现：

```powershell
$nsys = 'C:/Program Files/NVIDIA Corporation/Nsight Systems 2023.4.4/target-windows-x64/nsys.exe'
$basePython = 'C:/Users/Admin/AppData/Local/Programs/Python/Python311/python.exe'
& $nsys profile --trace=cuda,nvtx --sample=none --cpuctxsw=none --cuda-graph-trace=node --capture-range=cudaProfilerApi --capture-range-end=stop --force-overwrite=true -o runtime/ipc-profile/baseline-nsys-v2 $basePython benchmarks/profile_ipc_nsys.py
& $nsys stats --report cuda_api_sum,cuda_gpu_kern_sum,cuda_gpu_mem_time_sum --format csv runtime/ipc-profile/baseline-nsys-v2.nsys-rep
```
