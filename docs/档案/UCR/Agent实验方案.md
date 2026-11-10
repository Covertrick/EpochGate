# UCR-Agent 实验方案

冻结日：2026-11-06。机器可读副本在 `results/experiments/ucr/protocol.json`。本文在拟合之前写定。后面的阶段只追加执行记录，不改下面的常数。

主张只允许写成：语义谓词变化之后，Runtime 同时处理提交资格、继续成本、重规划成本和信息成本；两个成本区间不可分时，用一次有预算的 probe 取信息。主结果是留出族上相对 Cost Oracle 的后悔。不写成“加入了 conformal”或“加入了按成本取消”。

## 代价

\(\lambda_s=1\)，\(\lambda_b=1\)，\(\eta=0\)，\(\alpha=0.1\)，\(m=0\)。

\[
J=\lambda_s U+\lambda_b W
\]

\(U\) 是所选动作占用的服务秒，包含继续时的剩余服务、中止后的重做，以及 probe。\(W\) 是这段占用引起的旁观者等待之和。失败不放进这个 \(J\)。

硬约束：策略的任务失败率不得高于同一批 trace 上 AlwaysContinue 的失败率加 \(\epsilon=0.02\)。超过的策略失去 Gate D。敏感性分析另报 \(J_M=J+M\cdot 1[\text{失败}]\)，\(M\) 取开发集最大服务秒的 10 倍，拟合时算一次后写入 `fit.json`，不再改。

\[
D_{\text{probe}}=\lambda_s\cdot t_{\text{probe}}+\lambda_b\cdot W_{\text{probe}}
\]

\[
V_{\text{info}}=\mathbb{E}[J_{\text{不 probe}}]-\mathbb{E}[J_{\text{probe 之后的动作}}]
\]

\(J_{\text{probe 之后的动作}}\) 不含 \(D_{\text{probe}}\)。只在 \(V_{\text{info}}>D_{\text{probe}}\) 时 probe。期望在校准集上按区间重叠宽度和队列长度分桶，测试集只查表。

影子价格 \(\hat\lambda_b\) 在校准集上按队列长度、槽占用和到达速率分桶。服务区间先变成 \(\tilde S=(\lambda_s+\lambda_b\hat\lambda_b)S\)，\(\tilde C\) 同理，再比较。

## Oracle

Cost Oracle 在决策时刻知道当前真实 \(C,S\) 和当前谓词，在继续和中止里取 \(J\) 更小的动作。它不读 probe 之后才会出现的谓词。真实成本已经知道，所以它不 probe。Full Clairvoyant 不进主表，也不进 Gate D。

后悔 \(R=J(\pi)-J(\text{Cost Oracle})\)。

## 选择命题

只在两个区间都盖住真实值时声称：

- \(U_C<L_S\) 时，统一代价下 abort 的 \(J\) 不高于 continue。\(U_S<L_C\) 时相反。
- 区间重叠时，策略不得凭点估计强行 abort。probe 最多 8 个 token 或 0.2 秒，取先到者，且只能一次。

覆盖失败的样本不拿来充当这条保证。

## 切分

| 族 | 集合 | 种子 | 条数 | 结构 |
| --- | --- | --- | --- | --- |
| F1 | 开发 | 1000–1399 | 400 | 参数字段 1–2，重规划尺度 0.6，谓词偏早 |
| F2 | 开发 | 1000–1399 | 400 | 字段 3–5，尺度 1.0，谓词居中 |
| F3 | 校准 | 2000–2199 | 200 | 字段 6–10，尺度 1.6，谓词偏晚 |
| F4 | 校准 | 2000–2199 | 200 | 字段 3–5，突发到达 |
| F5 | 留出 | 3000–3099 | 100 | 字段 12–20 |
| F6 | 留出 | 3000–3099 | 100 | 重规划尺度 2.5 |
| F7 | 留出 | 3000–3099 | 100 | 谓词很晚，生成速率 0.6，到达偏移 |
| F8 | 留出 | 3000–3099 | 100 | 训练没见过的 transfer 工具 |

同一族内的种子唯一。开发、校准、留出的种子区间不重叠。估计器看不到真实 \(S\)、真实 \(C\)、harmful/harmless、Oracle 动作、未来 token、事后完成时间，也不把 `plan_epoch` 当数值。版本信息只用 `epoch_changed_since_start`、`epoch_age`、`has_recent_revision`。

## 门

- Gate A：F5–F8 分族覆盖率都 ≥ 0.9，宽度随已生成 token 增加而下降。
- Gate B：覆盖且分离的子集上，动作是 continue/abort 里代价更低的一侧；重叠区不因点估计强行 abort；Full 的留出后悔低于 Point，bootstrap 下界大于 0；probe 只出现在重叠区。
- Gate C：旧提交、谓词失效后的提交、重复副作用为 0；回执保持未知。
- Gate D：非 Oracle 输入、分族校准、至少两个留出族上 Full 低于 Point 且下界大于 0、去掉 probe 后至少一个留出族变差且下界大于 0、安全违规为 0 且失败率硬约束成立。

四项观察决定是否写投稿句：留出覆盖率、probe 是否改变动作、Full 是否低于 Point、至少两个留出族是否同时成立。

## 执行记录

阶段 0 于 2026-11-06 冻结。阶段 1–6 已跑完。留出覆盖率、probe 率和 Full 对 Point 的后悔差没有达到 Gate D。判定写在 [论文UCR.md](论文.md)。
