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
