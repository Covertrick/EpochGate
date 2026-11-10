# 同日测量

这些文件和主实验同一时期，但语义或范围不同。引用时必须带上本页的限制。

| 文件 | 能写的话 | 不能写的话 |
| --- | --- | --- |
| [fault_matrix.json](fault_matrix.json) | 四个崩溃点的单次对账，和主实验的 3 次重复方向一致 | 用它代替 [../01-主实验/主数字.md](../01-主实验/主数字.md) 里的重复次数 |
| [stale_world.json](stale_world.json) | 文件库存变化后，没有 supersede 旧发布仍在；对齐后旧 token 被拒 | 把它说成 DSH 多步会话 |
| [stale_world_model.json](stale_world_model.json) | 模型当时回复了 `SKIP`，旧 token 未发布，新 token 发布了 `inventory:0` | 把它并进后来的 `COMMIT` 三例 |
| [read_race.json](read_race.json) | 读入后、发布前内容被替换时，结果可以是 `OUTCOME_UNKNOWN` | 把它写成内核读文件的时间窗口证明 |
| [dsh_probe.json](dsh_probe.json) | Run `dsh-0dd9ee3abc53` 一次 `eg_write` 发布 `note.txt` | 把它写成撤销实验 |
| [swebench_12907.json](swebench_12907.json) | 一条官方 `FAIL_TO_PASS`：基线 2 failed，gold patch 后 2 passed。没有 Agent 补丁，不是官方 harness | 写成 SWE-bench 修复率 |
| [online_b0b1b2.json](online_b0b1b2.json)、[online_scale.json](online_scale.json) | 短任务 `bug.py` 的 batch 墙钟。B0 无账本，B1 停在意图，B2 有 receipt。n=3 | 把差距叫成 EpochGate 开销。开销主表是公平 B0/B1/B2 |
| [chaos.json](chaos.json) | 更早的一次睡眠进程取消，取消到退出约 0.828 秒 | 用这一次代替主实验的 8 次；不要把其中的 `gpu_reclaimed: false` 写成资源结论 |
| [coding_run.json](coding_run.json)、[code-de85cb5530a6.json](code-de85cb5530a6.json) | 一次容器编码。`eg_write` 发布后评分通过，三次 `eg_bash` 仍是 `INTENT_RECORDED` | 改写原始字段，或把它当成新的撤销轨迹 |
| [w0_scale.json](w0_scale.json) | 本机 0.05 秒睡眠进程的并发档 | 放进在线 Agent 表或开销主表 |
| [doctor.json](doctor.json) | 包 `epochgate-runtime` 0.1.0，runtime 线 3.0 | 把它写成实验结果 |

## 在线 batch 中位

由各文件里带 `arm`、`concurrency`、`batch_seconds` 的批次算出。`online_b0b1b2.json` 的 `concurrency_2_first_attempt.valid_measurement=false` 已排除。

### online_b0b1b2.json

这个文件只采用并发 1。文件里另有并发 2，第一次尝试已标 `valid_measurement=false`，其余并发 2 批次也不要写进正文。并发 2/4/8 只用下一张表。

| 并发 | 臂 | n | 中位秒 |
| --- | --- | --- | --- |
| 1 | B0 | 3 | 1.600 |
| 1 | B1 | 3 | 1.740 |
| 1 | B2 | 3 | 2.919 |

### online_scale.json

| 并发 | 臂 | n | 中位秒 |
| --- | --- | --- | --- |
| 2 | B0 | 3 | 3.829 |
| 2 | B1 | 3 | 4.701 |
| 2 | B2 | 3 | 5.278 |
| 4 | B0 | 3 | 3.961 |
| 4 | B1 | 3 | 5.180 |
| 4 | B2 | 3 | 6.676 |
| 8 | B0 | 3 | 5.538 |
| 8 | B1 | 3 | 7.598 |
| 8 | B2 | 3 | 9.940 |

