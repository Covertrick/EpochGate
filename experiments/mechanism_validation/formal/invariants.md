# Week 5 不变量

检查对象是每一次决策，以及这次决策应用到事实之后的状态。发布动作还要再检查下一步。数字来自同一次穷举：3981312 个状态，3981312 次转移，其中 864 次发布又检查了第二步。

采用的决策函数上，I1 到 I9 都是 PASS，命中数都是 0，`unmet_invariants` 为空。未采纳读法的违例没有删，它们在 `counterexamples.md`，不把这些不变量改成 UNMET。

`UNKNOWN`、`CORRECT_STOP`、`NOT_APPLICABLE` 都不是失败。`RESTORED_BYTES` 不推出 `CONFIRMED_EFFECT`。`PUBLISH_SEALED` 不映射成 `TARGET_CONFIRMED`。`CONFLICT` 没有被放进顺序。这些解释在检查器里单独断言，本次没有失败。

## I1 不产生第二个逻辑效果

同一 effect key 上，已经确认的目标、已经核对的回执、同一 key 的冲突载荷，都算一个逻辑效果。回执摘要与密封一致、但目标文件不在，也算效果已经存在。

`PUBLISH_SEALED_BYTES` 只允许从逻辑效果数为 0 的 `SEALED_ONLY` 出发，写完之后逻辑效果数是 1。下一步不能再发布。`OPAQUE` 不能用本地缺失证明接收端没有效果，所以不发布。`RELAY_THEN_REPLAY` 的历史含义是一次接受加一次重放，不是两个逻辑效果。

## I2 不覆盖另一份载荷

目标已经在，并且载荷哈希是 `MISMATCH`，或者 effect key 是 `MISMATCH` 时，动作不能发布，转移后的目标必须和转移前相同。`HOLD_CONFLICT` 留下原来的字节。

## I3 UNKNOWN 不自动重发

权威、语义、证据三者里任意一个是 `UNKNOWN`，`publish_allowed` 必须为 false，动作不能是 `PUBLISH_SEALED_BYTES`。`successor_failure` 保持 false。其他维保持输入值，不把 `UNKNOWN` 收成失败，也不把它收成 `CONFLICT`。

本次有 2220032 次求值落在这个条件里，发布次数是 0。

## I4 INVALID 不发布

语义是 `INVALID` 时，`publish_allowed` 为 false。只有密封字节时，动作是 `WITHHOLD_SUPERSEDED`。已经确认的历史字节可以保持 `CONFIRM_HISTORICAL`，但不能为了修正语义再写一份新载荷。

## I5 新的恢复发布必须有许可

`publish_allowed` 为 true 当且仅当动作是 `PUBLISH_SEALED_BYTES`。这还要求证据是 `SEALED_ONLY`，权威是 `VALID`，语义是 `VALID`，契约不是 `OPAQUE`，并且 `dispatch_permitted` 为 true。

许可由参数计算，不默认沿用旧 claim。要求新派发许可时，只看 `new_dispatch_granted`。不要求时，旧授权只在没有被消耗、并且 `crashed_continuation` 为 `OLD_GRANT_ALLOWED` 时成立；否则仍然要新的派发位。

## I6 已确认的目标不改写

证据是 `TARGET_CONFIRMED` 或 `RECEIPT_CONFIRMED` 时，动作不是发布，转移前后的事实相同。发布之后的第二步如果是确认，也不改目标字节；如果写许可被参数拒绝，动作是 `WITHHOLD_CONFIRM_NO_WRITE_PERMISSION`，目标仍然不动。

## I7 版本栅栏在目标提交边界生效

契约是 `VERSION_FENCED` 时，发布表示这次提交。权威不是 `VALID`，就不能通过这次提交。权威是 `SUPERSEDED` 且目标还不在，发布被拒绝，原因是 `FENCE_AT_COMMIT_BOUNDARY` 或 `SENT_IMMUNE_DOES_NOT_COVER_UNCOMMITTED`。目标已经在，栅栏不回头改写它。

`SENT_IMMUNE` 不是“未提交的密封字节仍可发布”。那个读法的 4 条反例保留在 `counterexamples.md`。其他三种契约没有这道栅栏；它们仍因权威不是 `VALID` 而拒绝新发布。

## I8 不一致的证据组合不是成功

证据是 `INCONSISTENT`、`CONFLICT` 或 `UNKNOWN` 时，`treated_as_success` 为 false，`publish_allowed` 为 false，动作也不是确认或重放。回执和密封不一致，不能走 `REPLAY_STORED_RECEIPT`。只有回执没有目标，不能走 `RECEIPT_CONFIRMED`。只有目标没有密封，不能走 `TARGET_CONFIRMED`。

## I9 确认历史效果和产生新效果是两件事

`PUBLISH_SEALED_BYTES` 的 `operation` 只能是 `PRODUCE_NEW_EFFECT`，`recovery_history` 只能是 `RESTORED_BYTES`，物理标签是 `PRESENT_UNVERIFIED`，不是 `CONFIRMED_EFFECT`，也不算成功。

`CONFIRM_EXISTING_TARGET` 和 `REPLAY_STORED_RECEIPT` 的 `operation` 只能是 `CONFIRM_HISTORICAL`，不发布，恢复列是 `NOT_APPLICABLE`。同一次决策不能同时是这两种操作。发布之后的第二步不能再产生一个新效果。
