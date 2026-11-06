# UCR-2.1 开发记录

协议在 [UCR-2.1实验方案.md](../论文/UCR-2.1实验方案.md)。结论在 [论文UCR-2.md](../论文/论文UCR-2.md)。UCR-1 的代码和 `results/experiments/ucr/` 没有改动。

| 阶段 | 交付 | 结果 |
| --- | --- | --- |
| 封存 | [ucr_v1_diagnosis.md](../论文/ucr_v1_diagnosis.md) | UCR-1 保持「生成器不能用来评估非 Oracle 策略」 |
| 规格 | `generator_spec_v2_1.json`、实验方案 | 新公式、12 格校准、F13–F16 插值、F17–F20 只作压力测试 |
| Gate A0 | `generator_audit.json`、`tests/test_ucr2.py` | 通过。未拟合之前停止条件没有触发 |
| 估计器 | `estimator_comparison.json`、`estimator_lock.json` | 锁定 hybrid-group。pooled 校准格欠覆盖 |
| 插值留出 | `summary.json` | Gate A 不通过：F16 的 \(S\) 覆盖率 0.79。Gate B 不通过：Full 后悔高于 Point |
| 外推与 vLLM | 未运行 | Gate B 失败后按协议跳过 |
| 收口 | 论文 UCR-2、README | 算法线结束 |

复现到收口为止：

```powershell
python -m uv run python scripts/check_ucr2_generator.py
python -m uv run python scripts/fit_ucr2.py
python -m uv run python scripts/run_ucr2_matrix.py
```

`run_ucr2_matrix.py` 在 Gate B 失败时不会读取 F17–F20。
