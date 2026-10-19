**薄壳模型与参数映射核查（2026-10-19）**

结论：本机使用的是 FEM Baraff–Witkin 膜能量加三次拉伸应变限制，再叠加独立弯曲能量。模型有明确论文与作者源码依据。当前证据支持参数语义适配问题，而不能将它简化为“libuipc 的剪切公式漏乘厚度”。此前把 10 N/m 当成唯一正确剪切值的说法需要纠正：它只完成厚度换算，尚未处理当前能量前因子的差异；小变形剪切匹配应为 5 N/m，详见后续响应测试。此次保持原精度设置、材料与仿真配置，不做生产参数修改。

**论文和参考实现**

| 参考 | 与当前模型的关系 |
| --- | --- |
| [Baraff & Witkin, Large Steps in Cloth Simulation, SIGGRAPH 1998](https://www.cs.cmu.edu/~baraff/papers/sig98.pdf) | 经典拉伸、面内剪切与弯曲条件能量。第 4 节的刚度按变形模式给定，不能把所有系数自动视为同一套三维 Lamé 参数。 |
| [Kim, A Finite Element Formulation of Baraff-Witkin Cloth, SCA 2020](https://www.tkim.graphics/FEMBW/) | 明确说明 BW 是耦合各向异性能量，分析其伸长和跨纤维剪切及 Hessian；它并非普通各向同性 StVK 薄片。 |
| [StiffGIPC, 2025，第 5 节](https://arxiv.org/html/2411.06224v4#S5) | 在 FEM BW 的二次拉伸项上增加仅伸长时激活的三次惩罚，正是本机 strain_rate=100 对应的形式。论文把剪切刚度设为拉伸刚度的 30%，这是实验选择，不是通用物理定律。 |
| [Stiff-GIPC 作者仓库](https://github.com/KemengHuang/Stiff-GIPC) | femEnergy.cu 中的 __cal_BaraffWitkinStretch_energy 同时包含二次拉伸、三次伸长和剪切平方项。核查固定提交 bb2849a7b292099581907937860d96ecfdf42588。 |
| [LibShell](https://github.com/evouga/libshell) | 当前公开材料模型是 StVK、Neo-Hookean 及其相关变体，未找到 BW 加上述三次应变限制模型。核查 master 的目录树提交 6072d9e4b3c9f99c8cfe538b8b5560954d87dfdf。它适合做标准薄壳参考，不能直接以同一 E、nu 要求两种不同本构给出相同响应。 |

Libuipc 自己的 [#819 模型规范](https://raw.githubusercontent.com/spiriMirror/libuipc/main/docs/specification/constitutions/strain_limiting_baraff_witkin_shell.md) 也引用 StiffGIPC。但该数学文档中的体积权重及“Lamé 参数”措辞，与当前接口的二维有效系数约定存在不一致，应以实际实现及二进制探针核对，不能仅靠属性名判断单位。

**实际能量和系数含义**

令 F 的两列分别为 f_u、f_v，l_u=|f_u|，l_v=|f_v|，q=f_u·f_v。当前面积加权形式可写为：

```
W = A0 * [ ks*((lu-1)^2 + (lv-1)^2)
         + kh*q^2
         + 100*ks*(max(lu-1,0)^3 + max(lv-1,0)^3) ]
```

ks、kh 是两个有效的面内能量系数；三次项在静止态的 Hessian 为零。该式的普通拉伸二次项没有一般各向同性模型的经纬正常应变交叉项。因此，输入名 nu 在这里参与系数换算，并不自动保证标准各向同性材料的泊松响应。使用 E、nu 的工厂函数只是软件参数化选择，不能据此把 BW 等同于 StVK。

论文第 5 节以三角形面积乘材料厚度为共同体积权重。实现也可把厚度预先吸收到两个系数里，改成面积权重；只要系数与权重一致，两种写法可等价。独立标定的二维剪切系数本身已经是有效量，就不应再无条件乘一次厚度。

**本机调用链**

1. 已安装 Genesis 的 Cloth 把 E 声明为 Pa；IPCCoupler 从同一份 E、nu 构造 ElasticModuli2D，然后调用共享 moduli 的兼容重载。
2. 当前 libuipc 的 [双参数接口与兼容重载](https://raw.githubusercontent.com/spiriMirror/libuipc/main/include/uipc/constitution/strain_limiting_baraff_witkin.h) 明确把拉伸与剪切分开；兼容重载会将同一份 moduli 同时交给二者。
3. 当前 libuipc 的 [初始化实现](https://raw.githubusercontent.com/spiriMirror/libuipc/main/src/constitution/strain_limiting_baraff_witkin.cpp) 将拉伸按完整厚度换算，剪切则按独立二维有效系数使用；[CUDA 实现](https://raw.githubusercontent.com/spiriMirror/libuipc/main/src/backends/cuda/finite_element/constitutions/strain_limiting_baraff_witkin_shell_2d.cu) 再统一乘面积。
4. 因此，当前组合把同一个“Pa 单位的 E”用于两个不同的参数约定，却没有独立转换或标定剪切。

已安装二进制的直接探针：

| E 输入 | 完整厚度 | ks | kh | kh/ks |
| ---: | ---: | ---: | ---: | ---: |
| 100000 | 0.25 mm | 26.6667 | 40000 | 1500 |
| 100000 | 0.50 mm | 53.3333 | 40000 | 750 |
| 200000 | 0.25 mm | 53.3333 | 80000 | 1500 |

这确认了参数的实际流向。当前“剪切比拉伸硬 1500 倍”不是 StiffGIPC 论文采用的那组配置。若采用论文的经验比值 0.3，对应约 8。此前 G*t=10 的说法只完成了厚度积分；按当前能量没有 1/2 前因子的约定，匹配线弹性剪切小变形响应应为 kh=G*t/2=5。8 是论文比例对照，5 是特定物理响应匹配，二者不能混作唯一真实布料参数。完整的响应测试与接口建议见 [CLOTH_ARTIST_PARAMETERS.md](CLOTH_ARTIST_PARAMETERS.md)。

本机源码 D:/xwchen/libuipc 的初始化文件仍按单个 thickness 乘拉伸，而安装二进制及当前公开 main 使用完整厚度 2r；本机旧源码不能完整代表实际加载的 DLL。前面探针直接调用已安装 constitution，因此表中数值不受这一源码版本差异影响。

**独立矩阵核对**

使用此前保存的 1k 布料第 1 步、第 0 次 Newton 系统。按上述能量在静止态独立组装膜 Hessian；使用原生实际的经纬坐标系，加入逐顶点质量及 dt² 缩放。平面静止态弯曲项只影响法向块，因此取 x/y 子矩阵与原生导出系统比较：

- 顶点 1000，三角形 1872；总质量 0.0036 kg。
- 相对 Frobenius 误差：2.6257e-16。
- 最大绝对条目差：3.5527e-15；原生最大条目约 6.5016。

这验证了本例静止态膜刚度组装与 BW 能量形式一致，支持“系数映射/参数化有问题”的定位。它不验证所有大变形状态、非线性梯度、PSD 投影或弯曲实现，不能泛化为整个求解器已全面验证。

**归因与下一步**

- 我的理解：此前直接用各向同性薄片换算解释 BW，并把 10 说得过于确定，需要修正。性能数据、条件数与剪切对照实验仍然成立；其物理解释应以本核查为准。
- Genesis 与 libuipc 的集成：本机组合存在参数语义不匹配，单组 Pa 模量没有正确表达“经厚度积分的拉伸”和“独立二维剪切”。这是最明确、可复现的适配问题。应在接口边界修复或明确参数含义。
- Libuipc：当前源码明确有意采用独立有效剪切约定，证据不支持把它直接判为忘乘厚度。兼容重载的默认行为与文档仍容易导致上述误用。
- 能量公式：有论文和作者参考代码支持；本例静止态矩阵核对通过，尚未发现该部分计算错误。

后续保持现有容差与精度要求。先确定要保留独立 BW 布料参数，还是用统一三维材料模量参数化该模型，再调整接口映射；此次不自动采用 8 或 10，不切换材料模型。

复现独立探针与矩阵核对：

```powershell
& 'D:/xwchen/genesis-garment-demo/.venv/Scripts/python.exe' benchmarks/audit_cloth_model.py
```

机器可读结果：runtime/cloth-model-audit/audit.json。作者参考源码快照及来源：runtime/cloth-model-audit/references/。
