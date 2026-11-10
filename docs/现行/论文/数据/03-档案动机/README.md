# 档案动机

这些材料解释设计从哪里来。不要和 [../01-主实验/主数字.md](../01-主实验/主数字.md) 加在同一个样本里，也不要写成当前 Runtime 的在线性能。

| 文件 | 只能使用的句子 |
| --- | --- |
| [rrm_gate1_decision.json](rrm_gate1_decision.json) | 等级 C。客户端断流和显式 abort 进入同一条引擎中止路径。没有请求级调度停止证据。不把 ClientStopOnly 当作未中止对照 |
| [m0_feasibility.md](m0_feasibility.md) | 数据门是 stop。完整事件对不够，M0-R 不重开。文中 12907 跑的是整个 `test_separable.py` 且退出码 0，和同日官方节点评分不是同一次实验 |
| [docs/档案/v4.0/论文.md](../../../../档案/v4.0/论文.md) | 逻辑时钟回放：有害路径 G2 相对 G0 少 12.8 秒失效占用；旁观者 P95 少 1.55–2.58 秒；12/12 格。这不是这次在线 Agent 的加速或减速 |
| [docs/档案/v5.0/论文.md](../../../../档案/v5.0/论文.md) | 60 次中止的回执等级是 `UNKNOWN`，`reclaimed=false`。其中 59 次采样时 `num_requests_running` 仍是 1。不能写成 GPU 或 KV 已回收 |
| [docs/档案/UCR/论文.md](../../../../档案/UCR/论文.md) | 非 Oracle 的区间加 probe 没有通过研究门。本文不使用未验证的停止预测 |
| [docs/档案/UCR-2/论文.md](../../../../档案/UCR-2/论文.md) | 同上，UCR-2 的门是 `gate_a=false`、`gate_b=false`。原始汇总在 `results/experiments/ucr2/summary.json`，不要把那些覆盖率写进本文结果表 |

v4 的 2400 行回放、v5 的 607,500 行回放和 UCR 的估计器输出都留在 `results/experiments/`。本文不需要把它们复制进来。
