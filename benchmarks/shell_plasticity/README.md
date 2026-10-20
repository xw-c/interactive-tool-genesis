# 薄壳折痕塑性验证

使用原版 pyuipc 0.0.28 的 StrainPlasticDiscreteShellBending，无需 native/ 扩展。

## 网页试用

启动工具后，点击「导入 JSON」，选择本目录 plastic.json。模型、固定点、0.5 N 点力和零重力已经设置。无需替换 OBJ 或重新选择顶点。

1. 保持暂停，单步 20 次（仿真时间 0.100 s）。
2. 点击外力列表「全部清除」。不要重置或修改固定点，否则折痕历史会清空。
3. 点击开始，观察回弹并保留折痕。
4. 导入 elastic.json，重复操作，对照恢复平面的效果。

原版后端实测：塑性松开时约 33.4°，稳定后约 23.4°；弹性松开时约 11.9°，最终约 0°。相同外力下两者加载角度不同是材料响应差异。

10° 是每条共享边的屈服折角，硬化为 0。这是效果验证参数，不是真实材料标定。薄壳只提供永久弯曲，不含永久拉伸或剪切。

## 自动验证

在项目根目录，使用已安装 Genesis、CUDA PyTorch 和 pyuipc 的 Python：

```powershell
python -m benchmarks.shell_plasticity
```

自动比较弹性、塑性、高屈服阈值及硬化，结果写到 runtime/shell-plasticity/results.json 与 states.npz。使用真实点力加载与清除，不重设顶点位置。默认加载 20 帧、松开后运行 160 帧，dt=0.005 s。

GPU 回归测试：

```powershell
$env:IPC_GPU_TEST='1'
python -m unittest discover -s tests -p test_shell_plasticity.py -v
```
