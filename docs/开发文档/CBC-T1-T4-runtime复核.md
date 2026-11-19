# CBC T1–T4：epochgate-runtime 复核

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-11-18 |
| 代码提交 | `7ac72ddc7b562d411ce9e6febb1d426174fb4c36` |
| 新探针 | `packages/runtime/tests/test_cbc_runtime_probe.py`。未提交，不在上面的 SHA 里 |
| 原探针 | `tests/test_cbc_probe.py`。只覆盖 `src/epochgate`。未提交 |
| 命令 | `python -m uv run pytest -q packages/runtime/tests/test_cbc_runtime_probe.py --tb=short -s` |
| 结果 | `8 passed in 3.96s`。退出码 0。工作目录 `D:\Checkout\EpochGate` |
| 原探针命令 | `python -m uv run pytest -q tests/test_cbc_probe.py --tb=line` |
| 原探针结果 | `6 passed in 1.12s`。退出码 0。只描述归档包 |

没有改 `packages/runtime/src`，没有改论文数据目录。

## T1 文件发布边界

路径是 `publish_file`：路径检查，对暂存文件做 SHA-256，`claim_publish` 在 `BEGIN IMMEDIATE` 里核对资格和 fencing token 并提交 `DISPATCHED`，然后 `os.replace`，再核对目标摘要，最后 `complete_claimed_publish`。

注入点在真实的 `os.replace`。替换函数先调用 `ledger.revoke`，再做原来的替换。这是检查和 claim 都已经成功之后、物理发布之前。

`test_t1_revoke_between_claim_and_replace_still_publishes` 的结果：

| 观察 | 值 |
| --- | --- |
| 发布结果 | `published=True` |
| 文件字节 | `order 3` |
| 账本回执 | `file:order.txt:` 加上该文件的 SHA-256 |
| Run | `eligible=False`，fencing token 从 1 变成 2 |
| 效果行上的 fencing token | 仍是 claim 时的 1。`complete_claimed_publish` 把 0 传进插入语句，但冲突更新不改这一列，也不再读 Run |
| 事件顺序 | `intent_recorded`，然后 `file_published` |

对照：`test_t1_revoke_before_claim_withholds_file`。撤销发生在 `publish_file` 之前时，结果是 `withheld`，`order.txt` 不存在。窗口只在 claim 提交之后。

## T2 跨进程提交与撤销

两个进程打开同一对 SQLite 文件。

| 探针 | 结果 |
| --- | --- |
| `supersede(run)` 之后，另一进程用旧 token 1 / 版本 1 提交 | `rejected`，没有 `reason`。接受次数 0 |
| 同一进程关系下的合法后继 token 2 / 版本 2，由新进程提交 | `rejected`，`reason=version`。新进程里的 EffectSink 版本是 1，Run 的版本是 2。接受次数 0。没有效果行 |
| 两个进程用同一键提交，payload 分别是 `alpha` 和 `beta` | 状态一个 `completed`、一个 `replayed`。接受次数 1。落盘 payload 是二者之一 |
| 第三个进程用同一键提交 `gamma` | `replayed`。接受次数仍是 1。payload 不变 |
| 提交与 `revoke_with_sink` 并发，4 次 | 接受次数 `[1, 1, 0, 1]`。没有 2。Run 最终都不可提交 |
| 两个进程同时 `supersede(run)`，12 次 | 最终 fencing token 全是 `[2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]`。两次调用都返回成功，token 只增加了 1 |

同一效果键没有被接纳两次。后继者在新进程里提交失败，是因为接收端版本只活在内存里。并发 `supersede` 的读改写不在 `BEGIN IMMEDIATE` 里，12 次都丢掉一次加一。

## T3 持久版本与重启

一个进程启动 `ControlServer`，HTTP 创建 Run，再 `POST /runs/run/supersede`。然后杀掉这个进程，另一个进程打开同一数据库。

| 观察 | 值 |
| --- | --- |
| 杀掉之前 | token 1、版本 1，supersede 后 token 2、版本 2 |
| 重启后的 Run | token 2，`state_version` 2，`eligible=True` |
| 重启后的 EffectSink | `_version` 回到 1 |
| 旧 token 1 / 版本 1 | `rejected` |
| 账本上的最新后继 token 2 / 版本 2 | `rejected`，`reason=version` |
| 接受次数 | 0 |

Run 的版本跨重启还在。接收端版本没有保持一致，所以上一任后继者也不能提交。

## T4 接纳之后、回执之前

`epochgate_runtime.worker.run_fault(..., "after_sink")` 在 `sink.accept` 成功后以退出码 86 结束，不调用 `store_receipt`。

| 观察 | 值 |
| --- | --- |
| 接收端 | 键 `k` 已接纳 1 次，payload 是 `payload` |
| 第一次 `reconcile` | `confirmed_from_sink`，账本变成 `completed` |
| 第二次 `reconcile` | `replay`。接受次数仍是 1 |
| 另一个键 `other` | `accepted`。这是另一次逻辑操作。总接受次数变为 2 |
| 同一键、payload 改成 `different` | `replayed`。原 payload 仍是 `payload`。接受次数仍是 1。没有冲突状态 |
| 效果行是 `OUTCOME_UNKNOWN` 且 sink 没有行 | `reconcile` 返回 `unknown`，`retried=False`。随后 `commit_authorized` 也返回 `unknown`。sink 没有该键 |

回执丢失可以由现有对账补上，不会把同一键再接纳一次。证据不足时保持 `unknown`。同一键的不同 payload 不会被标成冲突。
