# Week 5 恢复模型

本模型只描述恢复决策。它不改 Week 1 到 Week 4 的文件，不改写已经落盘的证据名，也不把 `independence_decision.json` 的 `PARTIALLY_SHARED` 改成另一句结论。

检查器是 `week5/formal/state_space_checker.py`。机器上没有 TLC，`tla_checked` 为 false。下面的通过与否只来自这次 Python 穷举。

## 四维

决策同时给出四个维度，不把它们压成一个分数。

| 维度 | 取值 |
| --- | --- |
| Evidence | `UNKNOWN`，`SEALED_ONLY`，`TARGET_CONFIRMED`，`RECEIPT_CONFIRMED`，`CONFLICT`，`INCONSISTENT` |
| Authority | `VALID`，`SUPERSEDED`，`UNKNOWN` |
| Semantics | `VALID`，`INVALID`，`UNKNOWN` |
| Sink contract | `VERSION_FENCED`，`IDEMPOTENT_ONLY`，`QUERYABLE`，`OPAQUE` |

证据只看字节事实。权威和语义原样进入输出，不因为另一维已经确定就被改写。`CONFLICT` 没有顺序值，`evidence_order` 是 null。它不是 `UNKNOWN` 和 `VALID` 中间的一档。

## Week 4 名称如何落在这四维上

下表是映射。历史行仍用原来的名字。

| 历史名 | Week 5 |
| --- | --- |
| `UNKNOWN` | 密封、目标、回执都缺失时，证据是 `UNKNOWN`，动作 `WITHHOLD_UNSEALED`。不是失败，不自动重发。Week 4 的 RSR 在预检既不是 `VALID` 也不是 `INVALID` 时也返回过这个名字；那种情况在本模型里证据仍是 `SEALED_ONLY`，语义是 `UNKNOWN`，动作是 `WITHHOLD_UNKNOWN`。`week4_collapsed_label` 记下那个历史折叠，证据列本身不改成 `UNKNOWN`。 |
| `PUBLISH_SEALED` | 证据是 `SEALED_ONLY`。它不是 `TARGET_CONFIRMED`。历史矩阵继续写 `PUBLISH_SEALED`。 |
| `CONFIRM_TARGET` | 证据是 `TARGET_CONFIRMED`。动作 `CONFIRM_EXISTING_TARGET` 不改目标字节。 |
| `REPLAY_RECEIPT` | 只有回执摘要、密封、目标三者一致时，证据才是 `RECEIPT_CONFIRMED`，动作是 `REPLAY_STORED_RECEIPT`。回执和密封不一致就不是这个状态，也不是成功。 |
| `CONFLICT` | 同一 effect key 上密封和目标都在，载荷哈希不同。动作 `HOLD_CONFLICT`，不覆盖。 |
| `SEMANTICALLY_SUPERSEDED` | Week 4 把它写在证据列。本模型把它拆开：证据仍是 `SEALED_ONLY`，语义是 `INVALID`，动作是 `WITHHOLD_SUPERSEDED`，`publish_allowed` 为 false。历史行不改名。 |
| `WITHHOLD_SUPERSEDED`，`WITHHOLD_UNSEALED`，`HOLD_CONFLICT`，`REPLAY_STORED_RECEIPT`，`CONFIRM_EXISTING_TARGET`，`PUBLISH_SEALED_BYTES` | 动作名保留。`PUBLISH_SEALED_BYTES` 只在安全条件和显式派发许可都成立时出现。 |
| `RELAY_THEN_REPLAY` | Week 4 中继检查点的历史动作。一次新效果，随后的第二次投递是重放，`logical_effect_count` 仍是 1。本模型把这次新效果记成一次 `PRODUCE_NEW_EFFECT`。历史行继续写 `RELAY_THEN_REPLAY`。 |
| `RESTORED_BYTES` | Week 3 的恢复列。新进程把密封字节写到目标上。它不是 `CONFIRMED_EFFECT`。物理列保持 `PRESENT_UNVERIFIED`。 |
| `CONFIRMED_EFFECT` | Week 3 的前向物理列，表示目标等于计分世界的合法载荷。本模型的恢复写不产生这个标签。 |
| `CORRECT_STOP` | Week 3 后继状态。不是后继失败。Week 1 对应的是 `NOT_APPLICABLE`，不是 `NOT_COMPLETED`。 |
| `NOT_APPLICABLE` | Week 3 的恢复列或后继列。不是失败。 |
| `STALE_ACCEPTED` | Week 2 权威 oracle 的观察：过期凭证被接纳过。它不是本模型的权威值 `SUPERSEDED`，也不是 `UNKNOWN` 与 `VALID` 之间的等级。 |
| `NO_STALE_ACCEPTANCE_RECORDED` | 没有记录到过期接纳。它不是 `VALID` 的证明，也不是失败。 |

Week 4 散文的五条字节谓词，在可实现事实上的对应是：`CONFLICT` → `CONFLICT`，`REPLAY_RECEIPT` → `RECEIPT_CONFIRMED`，`CONFIRM_TARGET` → `TARGET_CONFIRMED`，`PUBLISH_SEALED` → `SEALED_ONLY`，三者都缺 → `UNKNOWN`。这次穷举里这五条没有错位，`prose_alignment_mismatches` 为 0。

`week3.kill_child.run_reconcile` 的 else 比散文宽。回执和密封不一致、有回执没有目标、有目标没有密封，代码都落到 `UNKNOWN`。本模型把这些可实现组合定为 `INCONSISTENT`。已经写进 Week 4 结果文件的 `UNKNOWN` 不改。

## 事实空间

九个因素做全笛卡尔积，没有留下“未定义”：

- 密封在或不在
- 目标在或不在
- 回执在或不在
- 回执摘要相对密封：`MATCH`，`MISMATCH`，`NOT_APPLICABLE`
- effect key：`MATCH`，`MISMATCH`，`NOT_APPLICABLE`
- 载荷哈希：`MATCH`，`MISMATCH`，`NOT_APPLICABLE`
- 权威：`VALID`，`SUPERSEDED`，`UNKNOWN`
- 语义 oracle：`VALID`，`INVALID`，`UNKNOWN`
- 接收契约：四种

事实数 7776。其中关系标志和在场情况互相矛盾的有 6984，全部落到证据 `INCONSISTENT`，原因以 `FACT_ENCODING:` 开头。可实现事实 792。每一个事实再乘 64 个授权参数和 8 个许可位，共 3981312 次决策。每一次都给出 `evidence_state`，`authority_state`，`semantic_state`，`recovery_action`，`publish_allowed`，`requires_reconciliation`，`rejection_reason`。`undefined_combination_count` 为 0，`unclassified_combination_count` 为 0。

关系何时可比：回执摘要只在回执和密封都在时取 `MATCH` 或 `MISMATCH`；载荷哈希只在密封和目标都在时比较；effect key 只在至少两件制品都在时比较。违反这三条的编码不是成功。

决策表 `recovery_decision_table.csv` 有 9703 行。同一输出合并成一行，`assignment_count` 是这一行覆盖的参数与许可组合数。这些计数之和是 3981312。

## 必须单独定义的组合

| 事实 | 证据 | 原因 | 发布 |
| --- | --- | --- | --- |
| 回执在，目标不在，回执摘要等于密封，key 相同 | `INCONSISTENT` | `RECEIPT_WITHOUT_TARGET` | 否 |
| 回执在，目标不在，回执摘要不等于密封 | `INCONSISTENT` | `RECEIPT_DISAGREES_WITH_SEALED` | 否 |
| 回执在，密封不在 | `INCONSISTENT` | `RECEIPT_WITHOUT_SEALED_OR_TARGET` | 否 |
| 目标在，密封不在 | `INCONSISTENT` | `TARGET_WITHOUT_SEALED` | 否 |
| 目标与密封哈希相同，回执摘要不同，key 相同 | `INCONSISTENT` | `RECEIPT_DISAGREES_WITH_SEALED` | 否。不是 `REPLAY_RECEIPT` |
| 同一 key，两边都在，载荷哈希不同 | `CONFLICT` | `SAME_KEY_DIFFERENT_PAYLOAD` | 否。动作 `HOLD_CONFLICT` |
| key 不同，哪怕哈希碰巧相同 | `INCONSISTENT` | `EFFECT_KEY_MISMATCH` | 否。不把外来 key 确认成本效果 |
| 三件都缺 | `UNKNOWN` | `NO_DURABLE_BYTES` | 否。动作 `WITHHOLD_UNSEALED` |
| 只有密封，语义 `INVALID` | `SEALED_ONLY` | `SEMANTIC_INVALID` | 否。动作 `WITHHOLD_SUPERSEDED` |
| 只有密封，语义或权威是 `UNKNOWN` | `SEALED_ONLY` | `SEMANTIC_UNKNOWN_NOT_FAILURE` 或 `AUTHORITY_UNKNOWN_NOT_FAILURE` | 否。动作 `WITHHOLD_UNKNOWN` |

`UNKNOWN` 可以和任意其他维同时出现。这样的求值有 2220032 次，其中 `publish_allowed` 为 true 的次数是 0。权威列和语义列仍是输入值。`CONFLICT` 加上权威 `UNKNOWN` 仍然是 `HOLD_CONFLICT`，不会被收成失败，也不会被重发。

`INCONSISTENT` 在 `QUERYABLE` 上可以做 `RECONCILE_READONLY`。那是读，不是成功，也不是发布。没有该许可时动作是 `WITHHOLD_RECONCILE_NO_PERMISSION`。`OPAQUE` 与 `VERSION_FENCED`、`IDEMPOTENT_ONLY` 对不一致证据保持 `HOLD_INCONSISTENT`。

## 接收契约

`VERSION_FENCED` 的栅栏发生在目标提交边界，也就是 `sink.accept` 核对 `expected_version` 的那一次。权威 `SUPERSEDED` 且目标还不在，发布被拒绝。目标已经在，不回写。运行时里 `revoke()` 在 `phase == committed` 时直接返回，不抬高 token；这和“已提交的目标不被改写”是同一方向。

`IDEMPOTENT_ONLY` 对同一 key、同一载荷重放，对同一 key、不同载荷返回冲突。模型只在本地没有该效果、且安全和许可都成立时发布一次。第二次恢复看到的是 `TARGET_CONFIRMED`，不再发布。

`QUERYABLE` 把“目标在或不在”当作已经问到的事实。问到不在，才可能进入发布；问到的结果互相矛盾，就只读核对。

`OPAQUE` 不能用“本地没看见目标”证明接收端没有效果。只有密封、权威和语义都允许发布时，动作仍是 `WITHHOLD_OPAQUE_UNCONFIRMED`，`publish_allowed` 为 false。Week 4 的 `effect.bin` 是看得见的文件，那些 `PUBLISH_SEALED` 行不属于这个契约，本模型不把它们改判成非法。

## 转移

`PUBLISH_SEALED_BYTES` 是 `PRODUCE_NEW_EFFECT`。它写下与密封相同的目标，不写下回执。`recovery_history` 是 `RESTORED_BYTES`，`physical_label` 是 `PRESENT_UNVERIFIED`，`treated_as_success` 为 false。这不是 `CONFIRMED_EFFECT`。

同一次参数下的第二步必须是 `TARGET_CONFIRMED`，动作是 `CONFIRM_EXISTING_TARGET` 或 `WITHHOLD_CONFIRM_NO_WRITE_PERMISSION`，不能再发布。这对应 Week 4 重复恢复里 `after_sealed_before_replace` 的第一遍 `PUBLISH_SEALED`、第二遍 `CONFIRM_TARGET`。

`CONFIRM_EXISTING_TARGET` 和 `REPLAY_STORED_RECEIPT` 是 `CONFIRM_HISTORICAL`。它们不写字节。确认一个已经存在的效果，和产生一个新效果，是两个操作。语义 `INVALID` 时可以确认历史字节，但不能另写一份“修正后”的载荷。

其余动作不改变事实。

## 授权是参数

下面六件事分开写。代码里能直接读到的行为记为观察。观察没有把协议收成唯一规则的，就做成参数，64 种组合都检查。没有选“旧授权还能发”作为唯一策略。`authorization_chosen_to_favor_claim` 为 false。被结束的进程不恢复执行，`dead_process_resumes` 为 false。

1. 初次 claim 是否消耗授权。`ByteClaim.commit` 和 `Outbox.commit` 核对 `eligible`、token、epoch，然后插入行，不把 `eligible` 清掉，也不增加 epoch。运行时 `commit_authorized` 同样在成功时不增加 fencing token。观察是：这次提交不消耗授权。参数 `claim_consumes_authorization` 仍取 true 和 false。为 true 时，旧授权不能再支持这次恢复发布。
2. 崩溃进程能否沿用旧授权继续原操作。进程已经被 `TerminateProcess` 结束，不能继续。参数 `crashed_continuation` 问的是新进程：`OLD_GRANT_ALLOWED` 或 `OLD_GRANT_DENIED`。Week 4 的 `run_reconcile` 发布密封字节前不再调用 `authorize`。这是观察，不是本模型唯一采用的规则。
3. 撤销是否影响已经发出的操作。`revoke()` 对已经 `committed` 的运行直接返回。`sink.accept` 在接受时拒绝过期的 `expected_version`。`execute()` 若在效果函数执行期间发现 fence 已变，可以返回 `applied: true` 且 `reason: revoked_during_effect`。所以“已经发出”没有单一答案。参数 `revocation_of_sent` 取 `FENCE_AT_COMMIT_BOUNDARY` 或 `SENT_IMMUNE`。两者都不允许在目标尚未提交时用 `SUPERSEDED` 再发布。`SENT_IMMUNE` 只表示已经越过提交边界的效果不回写。把 `SENT_IMMUNE` 读成“未提交也可以发”是未采纳读法，反例留在 `counterexamples.md`。
4. 确认已有目标是否需要新的写许可。Week 4 的 `CONFIRM_EXISTING_TARGET` 不写文件，代码也没有另要一次写许可。参数 `confirm_requires_write_permission`。为 true 且 `write_permission_granted` 为 false 时，动作是 `WITHHOLD_CONFIRM_NO_WRITE_PERMISSION`，目标字节不动。
5. 再次发布是否需要新的派发许可。Week 4 无 RSR 的路径没有新的 `authorize`。参数 `republish_requires_new_dispatch`。为 true 时，只有 `new_dispatch_granted` 为 true 才发布。为 false 时，旧授权仅在未被消耗且 `crashed_continuation` 为 `OLD_GRANT_ALLOWED` 时覆盖这次发布；否则仍要显式的新派发位。
6. 只读核对是否自带许可。Week 2 和 Week 4 的恢复代码里没有单独的只读许可。参数 `reconcile_requires_permission`。为 true 时，`RECONCILE_READONLY` 需要 `reconcile_permission_granted`。没有许可就 `WITHHOLD_RECONCILE_NO_PERMISSION`，仍然不发布。

新发布的许可谓词，在权威为 `VALID`、语义为 `VALID`、契约不是 `OPAQUE` 时是：

- 要求新派发许可：仅当 `new_dispatch_granted`。
- 不要求新派发许可，且旧授权仍可用：允许。
- 否则：仅当 `new_dispatch_granted`。

一条干净的、非 `OPAQUE` 的只密封事实，在 512 个参数与许可组合里有 288 个允许发布。这样的接收契约有 `VERSION_FENCED`、`IDEMPOTENT_ONLY`、`QUERYABLE` 三个，所以 `publish_allowed` 为 true 的求值是 864。`OPAQUE` 的对应事实是 0。864 不是“选定旧授权总能发”，288 也覆盖了拒绝发布的那 224 个组合。

## 这次运行

```text
c:\Project\EpochGate\.venv\Scripts\python.exe c:\Project\EpochGate\week5\formal\state_space_checker.py
```

退出码 0。耗时 53.903 秒。`undefined_combination_count=0`。I1 到 I9 都是 PASS，命中数都是 0。采用的决策函数反例数 0。未采纳读法的反例 472 条，留在 `counterexamples.md`：无门闩的 Week 4 发布 108 条，把 `SENT_IMMUNE` 当成未提交也可发布的 4 条，Week 4 else 分支把不一致收成 `UNKNOWN` 的 360 条。那 4 条也出现在 108 条里面，按两个读法各留一份。
