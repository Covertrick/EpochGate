# R0 / R1 / R2 性能与拦截

同一批提案、场景和种子。`R0` 是 ByteClaim，发布密封字节，不调用语义预检。`R1` 在发布前再调用 `semantics_oracle`。`R2` 在此之后把预检令牌交给接收端的一次 SQLite 事务。版本围栏状态是 `ATOMIC_SQLITE_TRANSACTION`，不是 `VERSION_FENCE_UNSUPPORTED`。

TOCTOU A 到 E 各重复 10 次。重复是 attempt，不是新的独立提案。`process_kill=false`，`power_loss_claim=false`。间隙由实验控制器在预检返回之后改写目标世界版本，不是第二次并发进程，也不是 `TerminateProcess`。

无效发布和陈旧发布可以落在同一行。下面的计数是行上的标志之和，不是互斥事件数。

| 臂 | 无效发布 | 陈旧发布 | 重复效果 | SEMANTICALLY_SUPERSEDED | AUTHORITY_SUPERSEDED | UNKNOWN 停止 | CORRECT_STOP | 成功完成 | RSR 拦住 | 围栏拦住 | 延迟 p95 ms | RSR 均值 ms | 围栏均值 ms | 恢复延迟 p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ByteClaim | 21 | 20 | 0 | 0 | 0 | 0 | 0 | 31 | 0 | 0 | 110.535 | 0.0 | 0.0 | 18.351 |
| ByteClaim+RSR | 10 | 10 | 0 | 11 | 0 | 1 | 1 | 31 | 12 | 0 | 116.897 | 0.053 | 0.0 | 16.978 |
| ByteClaim+RSR+VersionFence | 0 | 0 | 0 | 11 | 0 | 11 | 1 | 21 | 12 | 20 | 111.389 | 0.053 | 17.483 | 18.141 |

## 谁拦住了什么

### BASE

- ByteClaim：n=3，无效 1，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 1
- ByteClaim+RSR：n=3，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 1，UNKNOWN 1，围栏结果 ['NOT_INVOKED']，RSR 拦住 2，围栏拦住 0，成功 1
- ByteClaim+RSR+VersionFence：n=3，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 1，UNKNOWN 1，围栏结果 ['ACCEPTED', 'NOT_INVOKED']，RSR 拦住 2，围栏拦住 0，成功 1

### TOCTOU_A

- ByteClaim：n=10，无效 10，陈旧 10，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 0
- ByteClaim+RSR：n=10，无效 10，陈旧 10，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 0
- ByteClaim+RSR+VersionFence：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['WORLD_VERSION_MISMATCH']，RSR 拦住 0，围栏拦住 10，成功 0

### TOCTOU_B

- ByteClaim：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR+VersionFence：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['ACCEPTED']，RSR 拦住 0，围栏拦住 0，成功 10

### TOCTOU_C

- ByteClaim：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR+VersionFence：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['ACK_NOT_OBSERVED']，RSR 拦住 0，围栏拦住 0，成功 10

### TOCTOU_D

- ByteClaim：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 10
- ByteClaim+RSR+VersionFence：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 10，围栏结果 ['UNKNOWN']，RSR 拦住 0，围栏拦住 10，成功 0

### TOCTOU_E

- ByteClaim：n=10，无效 10，陈旧 10，SEMANTICALLY_SUPERSEDED 0，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 0，围栏拦住 0，成功 0
- ByteClaim+RSR：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 10，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 10，围栏拦住 0，成功 0
- ByteClaim+RSR+VersionFence：n=10，无效 0，陈旧 0，SEMANTICALLY_SUPERSEDED 10，UNKNOWN 0，围栏结果 ['NOT_INVOKED']，RSR 拦住 10，围栏拦住 0，成功 0

## 代价

RSR 的代价是发布前多一次 `semantics_oracle`。它拦住 `INVALID` 和 `UNKNOWN`，不发布。条件 A 在这次调用返回 `VALID` 之后才改变世界，所以 R1 仍会发布；这组陈旧发布要由版本围栏拒绝。条件 E 在调用前已经换成另一份载荷，R1 和 R2 都由 RSR 返回 `INVALID`，围栏没有被调用。

围栏的代价是带预期世界版本、权威版本和载荷散列的一次提交。目标在同一个 `BEGIN IMMEDIATE` 里检查这些条件并决定是否插入效果。条件 A 由它拒绝。条件 D 的预期世界版本是空的，围栏返回 `UNKNOWN` 并且不发布。条件 B 是提交成功之后世界才变，围栏不回滚已经提交的效果。条件 C 的发布已经到达目标，客户端丢掉响应字节；恢复只查询并确认已有效果，不再提交一次新效果。

RSR 单独不消除 TOCTOU。`VALID` 只描述那一次调用当时的世界。
