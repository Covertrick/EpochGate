# UCR-Agent 开发记录

协议在 [UCR-Agent实验方案.md](../论文/UCR-Agent实验方案.md)。结论在 [论文UCR.md](../论文/论文UCR.md)。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 冻结 \(J\)、失败硬约束、Cost Oracle、选择命题、族切分、Gate | 已完成 |
| 1 | 八个结构族；特征不含 `plan_epoch` 和真实成本 | 已完成：`ucr_workload.py` |
| 2 | \(f_S\)、\(f_C\)、影子价格、CQR | 已完成。\(C\) 的扩张量是 39.4 秒 |
| 3 | 区间决策、一次 probe、校准集信息价值 | 已完成。校准集上的价值没有超过 probe 代价 |
| 4 | F5–F8 评估 | 已完成。Full 与 Point 的后悔差为 0，probe 率为 0。Gate A、B 不通过 |
| 5 | vLLM 240 个 attempt | 已完成。旧提交 0，回执未知。不进入后悔主表。Gate C 通过 |
| 6 | Gate D | 不通过。诊断在 [UCR-F5F6诊断.md](../论文/UCR-F5F6诊断.md)：F5/F6 是外推失败，不是标签不可预测 |

复现：

```powershell
python -m uv run python scripts/run_ucr_matrix.py
python -m uv run python scripts/run_ucr_runtime.py --engine vllm
```
