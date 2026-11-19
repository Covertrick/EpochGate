# Coding v3 四臂与确认

四臂只作用于已经冻结的补丁。同一任务的四条臂使用同一份补丁字节、同一份源文件末尾并发注释、同一条通知和同一个接收端。并发注释写在该任务历史修复触及的源文件末尾，不再改 README。容器评分在全部格子上都是 `docker_unavailable`。下面的接纳和恢复是接收端记录，不是隐藏测试结果。

## 开发集四臂

两个任务的模式相同。

| 故障 | B0 | B1 | B2 | B3 |
| --- | --- | --- | --- | --- |
| S1 通知送达 | 接纳，过期凭证为真 | 接纳，过期凭证为真 | 拒绝 | 拒绝 |
| S2 通知缺失 | 接纳，过期凭证为假 | 接纳，过期凭证为假 | 接纳，过期凭证为假 | 接纳，过期凭证为假 |
| S3 接收端中断 | 没有写入 | 没有写入 | 同一次决定里接纳，恢复为假 | Claim 之后重放，恢复为真 |

S1 上 B1 减 B2 的过期接纳差在 2 个任务上都是 1。Clopper–Pearson 95% 区间是 0.158 到 1，半宽 0.421。样本内没有相反配对。这不能写成跨任务精度。

S2 两个任务的 `s2_mode` 都是缺失。资格没有在接纳前推进，四条臂都接纳了同一份补丁，过期凭证为假。

黄金补丁单独跑过同一套臂，不记成模型结果。S1 上 B0 接纳且过期凭证为真，B2 拒绝。S3 上 B3 重放已授权字节，B2 的恢复字段为假。评分器自检没有执行，错误是 `docker_unavailable`。黄金补丁的 SHA-256 与 v2 清单里的 `source_patch_sha256` 一致。

模型后继是旧补丁被拒绝后的另一次观察，最多 4 轮。两个任务都是 `BUDGET_EXCEEDED`，没有新补丁。这列不并进四臂的过期接纳。

## 确认集

确认在冻结之后运行，没有再改提示、工具语义和通过标准。四个任务是清单里的 confirm 名单。记录在 `results/runtime/correctness/v3-coding/confirm/`。

| 任务 | 标签 | 补丁可应用 | 公开测试 | SHA-256 |
| --- | --- | --- | --- | --- |
| code-click-ac0e297e | BUDGET_EXCEEDED | 否 | 跑过，之后继续读 | 无 |
| code-click-dd149eb3 | ENVIRONMENT_BLOCKED | 是 | 没有跑 | b244ee166b85e5f1a550587daddabd17ab915d2ce0135d0a13025ba56ecdf407 |
| code-more-itertools-08dca284 | ENVIRONMENT_BLOCKED | 是 | 跑过，之后继续 | c8b6b720dcffcdf4839862e78b4146462ed134176e99b88fe1a3df000306ca16 |
| code-more-itertools-0fcdde9c | ENVIRONMENT_BLOCKED | 是 | 跑过，之后继续 | d06683401af3fbca36c2f13113b59a6080315792c0250520b399fbf50deb13f9 |

四格都读过源文件。三格交出可应用补丁。一格在 24 轮内没有补丁。四格都没有容器评分。`ENVIRONMENT_BLOCKED` 表示 Docker 不在，不表示隐藏测试失败或通过。

有补丁的三格进入四臂。S1 上同样是 B1 接纳过期补丁、B2 拒绝。3 对差都是 1。Clopper–Pearson 95% 区间是 0.292 到 1，半宽 0.354。`code-click-ac0e297e` 没有补丁，不进入这 3 对，保留为 `BUDGET_EXCEEDED`。确认配对少于 8，半宽大于 0.25，状态是 `PRECISION_NOT_MET`。没有追加任务。

这三格的模型后继也都是 `BUDGET_EXCEEDED`，没有新补丁。后继和四臂分开记。

## 本机容器补测

补测读的是已经冻结的补丁和 `evidence.json`，没有改它们，也没有再叫模型。镜像摘要是 `sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`。六个任务的初始树加隐藏测试都失败，六份黄金补丁加隐藏测试都通过。`code-click-ac0e297e` 在这两项里，确认分母仍是 4。

五份补丁的 SHA-256 都与 trace 一致。隐藏测试通过 2 份，都在 more-itertools：`code-more-itertools-08dca284`、`code-more-itertools-0fcdde9c`。未通过的是两个开发集补丁和 `code-click-dd149eb3`。后者 1 个失败，断言是 `DID NOT RAISE TypeError`。确认集 4 格里，2 格隐藏测试通过，1 格补丁未通过，1 格仍是 `BUDGET_EXCEEDED`。通过率按 4 格算是 2/4，不把没有补丁的那格从分母里拿掉。

S1 的最终工作区也跑了隐藏测试。B0 和 B1 接纳补丁，树摘要相同；B2 和 B3 拒绝，树里只有基线加并发注释，树摘要与接纳侧不同。拒绝侧 5 个任务的隐藏测试都失败。接纳侧只有两份 more-itertools 补丁通过。拒绝旧凭证不是修复成功。

S3 的 B3 上，五份 `claim.payload_sha256` 都等于冻结补丁 SHA，恢复字段为真。恢复字节的隐藏测试结果与该补丁相同：两份通过，三份未通过。把一份未通过的补丁重放出来，不算把仓库修好。
