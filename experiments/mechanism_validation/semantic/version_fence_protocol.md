# 版本围栏协议

三臂使用同一批夹具、同一组种子。

- `R0`：`ByteClaim`。提交密封字节，发布时不调用语义 oracle，也不带围栏令牌。
- `R1`：`ByteClaim+RSR`。发布前再调用 `semantics_oracle`。
- `R2`：`ByteClaim+RSR+VersionFence`。RSR 之后把预检令牌交给接收端。

## 预检记录

预检记下密封载荷散列、世界版本、语义 oracle 结果、权威版本和效果键。条件 D 的世界版本记为空，因为发布方不知道版本。

## RSR

对将要发布的密封字节和当时的评分世界调用 `semantics_oracle`：

- `VALID`：可以进入围栏。`R1` 在这里就直接做无围栏发布。
- `INVALID`：`SEMANTICALLY_SUPERSEDED`，不发布。
- `UNKNOWN`：返回 `UNKNOWN`，不发布。

`UNKNOWN` 不自动重发。`INVALID` 不发布。`CORRECT_STOP` 来自后继 oracle：义务是 `NOT_REQUIRED`、停止条件是 `MUST_STOP`、并且没有物理效果。它不是后继失败。

## 围栏

`R2` 把预期世界版本、预期权威和预期载荷散列传给目标。目标在一个 SQLite 事务里检查当前世界版本、权威、载荷散列，并查看这个效果键是否已经存在。条件都成立才插入效果。版本不一致就拒绝。空的世界版本得到 `UNKNOWN`，不插入。

实现状态是 `ATOMIC_SQLITE_TRANSACTION`。不是 `VERSION_FENCE_UNSUPPORTED`。边界是接收端进程里的一次 `BEGIN IMMEDIATE` / `COMMIT`。它不消除掉电窗口，也不提供跨库原子性。

## TOCTOU

每项预先声明重复 10 次。

- A：oracle 先返回 `VALID`，然后实验控制器把世界版本加一并改写评分世界，然后才尝试发布。RSR 不再被调用。`R1` 仍按那次 `VALID` 发布。`R2` 携带旧版本，被事务拒绝。
- B：目标先提交成功，然后世界版本才变。围栏不回滚已提交的效果。
- C：发布到达目标，客户端读到响应字节后丢掉，不解析回执。恢复只 `query`。散列相同则 `CONFIRMED_EXISTING`，不再 `submit`。
- D：预检里的世界版本为空。`R2` 不发布，结果是 `UNKNOWN`。`R0` 和 `R1` 不看版本。
- E：预检之后、下一次 RSR 之前，同一效果键的载荷被换成另一份字节。`R1` 和 `R2` 对新字节得到 `INVALID`，不发布。`R0` 发布新字节。

A 说明 RSR 单独不消除 TOCTOU。E 说明 RSR 拦住的是发布前已经可见的语义失效。这两件事不是同一个窗口。

这次矩阵不是 `process_kill`。没有调用 `TerminateProcess`。`power_loss_claim=false`。
