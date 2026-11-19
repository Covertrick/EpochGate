# CBC 创新性判决

日期 2026-11-18。判决对象是「要不要为 EpochGate 新做一套 Commit-Boundary Coordination」。证据只来自 `epochgate-runtime` 0.1.0 的源码，以及本轮 `packages/runtime/tests/test_cbc_runtime_probe.py` 的 8 次通过。归档包上的 `tests/test_cbc_probe.py` 不参加这个判决。

## 判决

CBC 不应该作为新协议开发。T1 到 T4 里测到的洞，都能落到已有的写事务、持久版本、幂等重放和恢复对账上。它们是 `epochgate-runtime` 的工程正确性修复。

下面八条是「只有全部成立才写 CBC 研发计划」的门槛。本轮没有一条成立，所以这里不给出 CBC 的实现计划。

| 门槛 | 本轮结论 |
| --- | --- |
| 1. 已有方法的不足，且不足不是漏调用 | 不足是实现没有把已有原语用完。`BEGIN IMMEDIATE`、SQLite 里的版本行、幂等键比较、`reconcile` 都已经在这个包里 |
| 2. 需要一条新的协议规则 | 不需要。D1 是把 token 检查留到 `os.replace` 之前并守住写锁。D2 是把 `_version` 写入 sink 数据库。D3 是用 SQL 自增代替先读后写。D4 是重放时比较 payload |
| 3. 新机制比既有方法多保证什么 | 没有多出来的保证。锁住替换，保证的是「撤销已经提交则这次替换不发生」。持久版本保证的是「重启后的 sink 版本等于 Run 版本」。二者都是 fencing token 和单调版本已经声明过的性质 |
| 4. 完整故障模型超出这些原语 | 没有。测到的故障是进程杀死、两进程交错、接纳后退出、同键换 payload。都在崩溃一致性和幂等的常用模型里 |
| 5. 需要新的安全或进展性质 | 没有。安全性质是旧 token 不能产生第二次效果，证据不足保持 `unknown`。进展性质是拿到当前 token 和当前版本的调用最终能提交。D2 现在违反进展，修持久版本就能恢复，不必换协议 |
| 6. 和已有论文不重叠 | 重叠。fencing token、条件写、幂等键、崩溃后对账都是已有系统在用的方法。稿件第 2 节也写了「这些是本系统的检查规则，不是新的授权理论」 |
| 7. 最小实现成本低到值得单独立项 | 改动落在 `sink.py`、`ledger.supersede`、`files.publish_file` 三处，外加探针。这是修复成本，不是新系统的成本 |
| 8. 反例说明旧原语会永久挡住合法后继 | 不成立。把 `os.replace` 放进同一把写锁，后继者会等待这次发布结束，然后用新 token 继续。等待不是永久封锁。D3 的丢更新用条件自增可以变成两次成功、token 为 3 |

## 每个缺陷为什么归工程修复

T1。`claim_publish` 已经在写事务里拒绝过期 token。窗口出现是因为事务在 `os.replace` 之前就提交了，`complete_claimed_publish` 不再读 Run。把核对和替换放回同一临界区即可。撤销在 claim 之前已经能挡住文件，本轮对照探针就是这个结果。所以不是「fencing 无法表达文件发布」。

T2 和 T3。Run 的 token 和 `state_version` 已经在 SQLite。失败点只是 `EffectSink._version` 在构造时写成 1，`bump` 不写数据库。新进程因此拒绝本应合法的后继，接受次数是 0。持久化这一列之后，旧 token 仍会被 Run 拒绝，后继者可以提交。并发 `supersede` 的 12 次最终 token 都是 2，是因为两次都把读到的 1 写成 2。`UPDATE ... SET fencing_token = fencing_token + 1` 是同一张表上的既有写法。

T4。`after_sink` 退出码 86 之后，现有 `reconcile` 给出 `confirmed_from_sink`，再次对账是 `replay`，同一键的接受次数保持 1。另一个键代表另一次操作，可以再接纳。没有 sink 行且状态不明时，结果保持 `unknown`，不会自动重试。同键不同 payload 目前当重放，这是幂等实现少了一次比较，不是需要新恢复协议。

## 不写进论文的话

不要把 D1 到 D4 的修复说成 2026-11-10 那次 campaign 或两条 DSH 会话已经具备的能力。那些 JSON 仍只证明当时脚本记录下来的行为。修复前后的探针输出要分开存放。
