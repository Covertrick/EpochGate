# 标注说明

标注员看的是冻结后的 issue、两个完整 SHA、变更 diff、Agent 在 checkpoint 之前的 `evidence_read_set`，以及变更后的测试或规则。标注员不改这些材料。

三个字段独立。一个提交可以和任务目标有关，但 Agent 已经读过的证据还没失效。一个提交也可以不改同一批文件，却改变了被依赖的 API。

## task_relevance

- `relevant`：忽略这次变更会让原本正确的续作不再满足当前要求。
- `irrelevant`：变更真实存在，但当前候选工作和当前要求都不依赖它。
- `uncertain`：现有材料不够判断。这时应要求补充证据，而不是猜一个二分标签。

只因为 diff 碰到了同一文件，不能标 `relevant`。

## evidence_invalidation

对 `evidence_read_set` 里的每一条记录：

- `stale`：该路径或该工具结果的内容哈希，在变更后不再代表当前仓库。
- `intact`：哈希所代表的事实仍然成立。
- `unknown`：没有对应的变更后内容，不能猜。

## required_refresh

- `required`：继续使用这条证据会违反变更后的要求或证据规则。
- `optional`：刷新可以做，但当前不变量不依赖它。
- `forbidden_as_restart_reason`：这条变更不能作为放弃仍然有效工作的理由。这个值用于 Irrelevant 组，避免把“重做了”自动记成正确。

## 谁看不见标注

`annotation_id` 可以出现在实验元数据里。标签值、gold patch 和 `reference_recovery_witness` 只在评分进程。Agent 可见的 manifest 只有仓库 URL、issue URL、`base_commit`、变更通知文本和 checkpoint 边界。变更通知由协议规定，不附带正确答案。
