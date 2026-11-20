# 语义复验和版本围栏的限制

接收端是本机测试进程里的 SQLite，不是第三方服务。路径能提交和查询，是因为测试代码把 `PathReceiverClient` 交给它。控制接口没有认证。

版本围栏的原子性是一次 SQLite 事务。它不是跨数据库原子提交，不是任意 HTTP exactly-once，也不是掉电持久性。每一行 `power_loss_claim=false`。这组运行没有进程杀死；`process_kill=false`。条件 A 的世界变化是实验控制器在同一次试验里、在 oracle 返回之后调用 `set_world_version`。这是排好的间隙，不是另一个未同步的写入进程。

RSR 使用 Week 2 的 `semantics_oracle`。`VALID` 只覆盖那一次调用。条件 A 故意把世界变化放在这次调用之后，所以不能把 RSR 写成已经消除 TOCTOU。

`UNKNOWN` 不发布，也不当成失败后再自动发布。`INVALID` 不发布。从 Claim 行读回字节是 `RESTORED_BYTES`，审计进程在目标账本里看到合法效果才是 `CONFIRMED_EFFECT`。确认已有效果不会再插入一行。

`CORRECT_STOP` 只在后继义务为 `NOT_REQUIRED` 且停止条件为 `MUST_STOP`、并且没有物理效果时出现。它不是后继失败。本矩阵的 `AUTHORITY_SUPERSEDED` 计数可以是 0：条件 A 先比较世界版本，权威不一致的拒绝留在 `week5/tests/test_receiver_ledger.py`，没有被算进这五组 TOCTOU 的发布计数。

夹具是确定性的 `week5_semantic_fixture_v1`，不是语言模型。Week 4 在 S14 和 S16 上只观察到 `INVALID`。这里的 `VALID`、`INVALID` 和 `UNKNOWN` 是新跑出来的行，不是把 Week 4 的行拿来充数。

样本是每个 TOCTOU 条件 10 次尝试。尝试不是新的独立提案。延迟是进程内墙钟，含一次本地套接字往返，不能外推到网络上的第三方接收端。
