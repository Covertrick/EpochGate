# PDF 与论文证据地图

## PDF

`EpochGate-conference.pdf` 不在工作区。全库没有其他 PDF。下面凡是会议论文页码上的 Figure 2、Table 1、S3/B3 或主张边界，都**没有 PDF 页级证据**。未读文本不记为已核对。

仓库里能读到的是 Markdown 稿和实验记录。它们不是该 PDF 的替身。

## Figure 2

会议 PDF 中的 Figure 2 无法定位，也没有附带的图数据文件给出该图的单元格。

仓库里唯一标题为“图 2”的图，在 `docs/档案/v1.6/设计原文.md` 第 6 节：图注是“系统边界。评估器只读实验事实，不向调度器回传答案”，图文件为 `docs/档案/图/02-架构.svg`。这是架构边界图，不是终态与效果历史的联合分布，不能当作本次 Figure 2。

`docs/现行/论文/稿件.md` 没有编号为 Figure 2 的图，也没有终态 × 过期接纳的联合计数表。`results/runtime/correctness/m6/summary.json` 只保存 H4 的一个比例 `0.4`、H5 的配对数 5、H6 的配对数 4，不是一张联合分布图。

因此：Figure 2 的已发表联合表不可获取。运行级联合表是从 `m4-confirm` 的记录重建的，比较对象是 `m6/summary.json` 的配对边缘，不是缺失的图。

## Table 1

会议 PDF 的 Table 1 没有页码可引。`docs/现行/论文/稿件.md` 和 `docs/现行/论文/数据/01-主实验/主数字.md` 里的表是开销、撤销场景和崩溃点，统计单位是另一次测量（例如公平开销每臂 20 次，崩溃点各 3 次）。这些表没有写成确认批 72 格的联合分布，本审计不把它们编号成 Table 1。

## S3 / B3

PDF 中没有 S3/B3 段落。定义在协议正文：

- `docs/开发文档/M4-M6独立确认协议.md` 臂与故障一节：B3 先提交 Claim，再把 Claim 字节交给同一接收端，崩溃点在两者之间，恢复只重放 Claim 字节。S3 是效果已获授权、接收端执行中断，中断打在同一接收端之前。
- 同页写明永久撤销另开专门测试，撤销没有合法后继，不计为后继失败。
- `scripts/reliability_contract.py` 的 `resume_execution` 只重放已经写入 Claim 的字节；`commit_effect` 在 `interrupt` 时返回 `crashed_before_accept`。这条返回字符串没有写入 `evidence.json`。归档记录里能看到的是布尔字段 `interrupted_recovered`。

`results/runtime/correctness/m4-confirm/` 中实际有运行记录的 S3/B3 是四格：`ful-order-c1`、`ful-config-c4`、`ful-config-c5`、`ful-config-c6`。`ful-order-c2` 与 `ful-order-c3` 的 S3 四臂都是阻塞行，没有 `evidence.json`。

## 实验方法与主张边界

方法文本在 `docs/开发文档/M4-M6独立确认协议.md` 与 `docs/开发文档/实验方法.md`，不在 PDF。

主张边界以已读文本为准：

- `docs/现行/论文/稿件.md` 第 9 节限制：崩溃窗口的 sink 接受次数可以复核；稿件写明若干路径未测或不能并样本。第 6 节的四个崩溃点各 3 次，退出码 86。这组数字来自另一批测量，不是 72 格。
- `docs/开发文档/实验方法.md` E6：`process.kill()` 与 `SystemExit(86)` 用来区分受控退出和操作系统杀掉进程。该节写明不包含断电、磁盘持久性和内核崩溃。
- `docs/开发文档/补实验结果.md` 写明原子替换、进程崩溃后恢复、掉电持久性分开记，掉电是未测。
- `docs/开发文档/M4-M6结果与审计.md` 把当时规则下的决定写成 GO。那是 H5 精度与 M5 迁移的历史决定。本周审计不改写该文件，也不把那个 GO 当成效果历史联合分布已经在全部 72 格上可识别。

进程杀死、受控中断和退出码 86 都不是掉电持久性。S3 的 `interrupted_recovered` 只表示 Claim 字节是否被重放进接收端。
