# 体积塑性扩展

模型、推导、参数换算和论文链接见 [MODEL.txt](MODEL.txt)。

当前 v2 保留 SNK1 弹性，使用等体积关联 J2 塑性、线性各向同性硬化、逐试探局部变分返回及一致切线。需要整套匹配的原生包，不能与旧 v1 后端混用。

项目根目录构建：

```powershell
python native/build.py --source <libuipc仓库> --vcpkg-toolchain <vcpkg>/scripts/buildsystems/vcpkg.cmake
```

真实点力加载—松开及时间步对照：

```powershell
python -m benchmarks.volume_plasticity --refine
```

使用带 v2 扩展的 Python 环境。结果写入 `runtime/volume-plasticity-v2/`；参考阈值 2.5%，两端按三角形面积分配、反向施加最高 0.6 N 拉力，10 ms 爬升，20 ms 撤力，观察至 300 ms。不同时间步保留相同物理载荷时程。较小时间步会保留更多振动，不应把单个时刻的形变当作完全静止的残余形状。

无硬化材料超过承载能力后可持续流动和颈缩；大恒力可能使固定四面体网格及全局求解困难。本实现不包含重网格或断裂。

## MAS 与 CUDA Graph

此版本 libuipc 的 MAS 通过 `mesh_partition(sc,16)` 产生的 `mesh_part` 属性启用，不使用新版 `fem_preconditioner` 配置项。应用将固定顶点标为 `-1`，由后端对角预条件回退处理。

旧 MAS 的 apply 内核、残差缓冲区清零和对角回退没有传递 PCG 的 CUDA stream，会漏出 Graph 捕获。`mas_stream.py` 为这些操作补上 stream 参数，并导出 `interactive_ipc_mas_stream_version()=1`。应用默认尝试 MAS；缺少该能力、分区接口不可用、全部顶点固定或分区失败时自动使用 diag，清理部分分区属性并输出原因。应用默认使用 `step_mode="libuipc"` 直接 IPC 路径，不更新 Genesis 实体状态及回调；需要完整 Genesis 调度时设置 `step_mode="genesis_world"`。不提供手动预条件器选项。

在本项目已有的 Windows 构建树上增量构建：`python native/mas_stream.py`。它复用 `runtime/plasticity-build` 的头文件、依赖和目标文件，以及 `runtime/plasticity-only` 的塑性目标文件，输出完整独立包 `runtime/mas-stream/package`，不修改原包或 site-packages。它不是从干净源码开始的一键构建器。源补丁及构建参数记录在脚本和输出目录中。

构建后设置 `INTERACTIVE_IPC_UIPC_PACKAGE` 为完整修复包的绝对路径，使用项目 CUDA Python 环境设置 `IPC_CONTACT_GPU_TEST=1` 并运行 `python -m unittest discover -s tests -p test_solver_optimizations.py -v`，验证固定点及 Graph 开关一致性；性能和误差还需按目标场景对照。显式指定原生包优先于默认选择。本机已验证包带有 `mas-stream-validated.json`，应用可自动选择该包；这只选择原生库，预条件器由应用自动尝试 MAS 并按能力回退 diag。分发须携带完整修复包；只有单独 Python 源文件不会获得 CUDA stream 修复。
