# Runtime 正确性缺陷清单

日期 2026-11-18。对象是 `epochgate-runtime` 0.1.0，提交 `7ac72ddc7b562d411ce9e6febb1d426174fb4c36`。证据来自 `packages/runtime/tests/test_cbc_runtime_probe.py` 的本轮运行，以及对应源码。这些条目不是论文 `tables.json` 里的旧样本，也不要把以后的修复写回 2026-11-10 的冻结结果。

归档包 `epochgate` 5.0.0 的原探针另记。它的 6 次通过不关闭下面任何一条。

| 编号 | 缺陷 | 本轮观察 | 已知可用来修的机制 | 还没做 |
| --- | --- | --- | --- | --- |
| D1 | claim 提交之后，`os.replace` 不再看 token | 替换前撤销，文件仍写成 `order 3`，回执仍写入，事件以 `file_published` 结束。撤销若发生在 claim 之前，文件不会出现 | 在同一次 `BEGIN IMMEDIATE` 里重新核对 token，核对失败就不替换。`revoke` / `supersede` 使用同一把写锁，所以后继者会等待这次发布结束，而不是永远失去提交权 | 把临界区扩到物理替换。修复后的轨迹要单独存，不能并进旧 DSH JSON |
| D2 | EffectSink 版本只在进程内存 | 新进程和重启后的 `_version` 都是 1，Run 的 `state_version` 是 2。合法后继提交被 `reason=version` 拒绝，接受次数 0 | 把 sink 版本写进它自己的 SQLite，并在和 Run 版本同一次事务里加一。启动时读出该行 | 持久版本。旧论文没有重启后 sink 版本仍对齐的样本 |
| D3 | 跨进程 `supersede` 丢失加一 | 12 次里两次调用都成功，最终 token 全是 2 | `BEGIN IMMEDIATE` 里执行 `fencing_token = fencing_token + 1`，不要先读到 Python 再写回绝对值 | 这个条件更新。12 次记录留在探针输出里，不改论文竞争表 |
| D4 | 回执丢失已经能对账；同键不同 payload 不能 | `after_sink` 退出码 86 后，对账一次即 `confirmed_from_sink`，再来是 `replay`，接受次数保持 1。另一键可以再接纳。同键换 payload 仍返回原 receipt | 现有 `recovery.reconcile` 处理回执丢失。同键冲突是幂等键上比较 payload，不一致就拒绝 | payload 比较。论文写的是「返回原来的 receipt」，没有写冲突检测 |
| D5 | `UNKNOWN` 已有，但是调用方要自己停 | 效果行是 `OUTCOME_UNKNOWN` 且 sink 无行时，对账和再次提交都是 `unknown`，没有自动再执行 | 保持这个分支。不要在证据不足时合成成功 | 产品路径上的回归测试可以加，不需要新协议 |
| D6 | 论文表和 DSH 轨迹能对上冻结 JSON，生成命令本轮没有重跑 | `tables.json` 与稿件数字一致，见 `论文-代码对应关系.md`。稿件事件表省略了每条会话开头的 `attempt_opened` | 在复现说明里把命令、输出文件和提交钉在一起 | 不重写 2026-11-10 的 JSON。新测量用新目录 |

优先顺序就是 D2、D3、D1、D4。D5 已经有行为。D6 是记录，不是代码缺陷。

取消回执仍然不是 GPU 回收。`processes.py` 把槽位释放和 GPU 回收分开。这条不要和文件发布窗口混成一个协议问题。
