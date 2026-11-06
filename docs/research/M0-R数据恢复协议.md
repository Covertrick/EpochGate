# M0-R 数据恢复协议

日期：2026-11-06。`results/semantic-state/m0_screen.json` 里的 4 条旧准入和 `data_gate: stop` 不改。

探索池来自 SWE-bench Verified 的固定 `test` split，目标 30 到 40 条，至少覆盖 4 个仓库。工程启动门是至少 5 条完整事件对，并且来自至少 2 个仓库。论文样本量另冻。

一条完整事件对要同时有：可追溯的历史提交、变更后仍然存在合法修复、以及能区分错误实现和正确实现的测试。四格是 Base、Base+Gold、Base+Change、Base+Change+Valid Fix。文件交集不是金标准。

`task_relevance`、`evidence_invalidation`、`refresh_requirement` 分开记录。`information: insufficient` 表示当前证据不够，不把它写成第三种世界真值。

访问失败记为 `AccessBlocked`。补丁套不上记为 `ApplyFailed`。没有可执行评分记为 `Unscorable`。附近没有可用提交记为 `Unsatisfiable`。不补造提交，不改 gold。

已有四条 Astropy 只追加 `research_suitability`。旧六项结论不追溯修改。
