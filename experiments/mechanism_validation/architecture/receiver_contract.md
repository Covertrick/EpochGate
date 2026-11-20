# 本地接收端合同

这是测试用的本地提供者，不是第三方服务。它跑在单独的进程里，数据库文件只由那个进程打开。被测路径不能打开这个库。

## 路径可以使用的接口

`PathReceiverClient` 只有两件事：

- `submit`：提交一份效果。参数是效果键、载荷、attempt id。需要版本围栏时另外带上预期世界版本、预期载荷散列和预期权威。
- `query`：查询一个效果键的状态。返回 `ABSENT`、`PRESENT` 或传输失败时的 `UNKNOWN`。返回里有载荷散列、逻辑效果计数和回执，没有整本账本，也没有其他键的行。

同一效果键、同一载荷散列：返回 `REPLAY`，`applied=false`，逻辑效果计数保持 1。

同一效果键、不同载荷散列：写入 `target_conflicts`，不插入第二份效果，返回 `CONFLICT`，逻辑效果计数保持 1。

`UNKNOWN` 不是失败判决。调用方不得因为 `UNKNOWN` 再自动提交一次。

## 路径不能使用的接口

`ControlReceiverClient` 可以改世界版本、改权威、清空效果表、取审计摘要、关闭进程。Claim 和 Outbox 的源码不引用它。

审计有两条路，都在被测路径之外：

- 控制端的 `audit` 只返回键、散列、世界版本、逻辑效果计数和回执。
- `week5/runtime/auditor.py` 的 `read_target_ledger` 由审计进程打开接收端数据库，读真实载荷字节。路径模块不导入它。

接口没有认证。隔离靠的是路径代码拿到的客户端种类，不是一条对抗恶意路径的安全边界。

## 版本围栏的原子边界

`fence=true` 时，`apply_submit` 在一个 `BEGIN IMMEDIATE` 事务里完成这些事：读当前世界版本和权威；如果预期世界版本为空，结果是 `UNKNOWN`；如果世界版本不一致，结果是 `WORLD_VERSION_MISMATCH`；如果权威不一致，结果是 `AUTHORITY_SUPERSEDED`；如果载荷散列和预检散列不一致，结果是 `PAYLOAD_HASH_MISMATCH`；否则按上面的幂等规则插入或拒绝。拒绝的那次事务不插入 `target_effects`。

这不是两次独立的 `SELECT` 再 `INSERT`。它也不是跨库原子提交，不是任意 HTTP exactly-once，也不是掉电持久性。`power_loss_claim` 在崩溃行上保持 false。本周的语义矩阵没有把进程杀掉。

不带围栏的 `submit` 只做幂等接纳。`R0` 和 `R1` 使用这条路，所以它们没有版本围栏。

## 逻辑效果计数

`target_effects.logical_effect_count` 在首次接纳时写成 1。重放不增加它。冲突不增加它。审计按这个计数判断重复效果。确认已经存在的效果和发布一份新效果是两次不同的操作。
