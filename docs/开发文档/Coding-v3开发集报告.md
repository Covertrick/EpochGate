# Coding v3 开发集报告

协议是 `docs/开发文档/Coding-v3协议.md`。开发集只用 v2 清单里的两个 pilot 任务，没有使用确认任务。历史 GO、v2 原始记录和隐藏测试没有改。

运行时 `git rev-parse HEAD` 是 `b6dd92b491f5235b89a649132e63a889df598316`。v3 运行器当时还不在这个提交里。冻结摘要 `results/runtime/correctness/v3-coding/freeze.json` 记录了协议 SHA-256 `48dac590485332ad629e406e50b17efb98fc136265709d20e493f2683549307e` 和运行器 SHA-256 `d5b109ddcfedcffee08469d50418bfe408d9c1ca7e1662e13a95c7872f2e6327`。镜像标识是空的：这台机器没有 Docker，`python:3.10-slim` 没有启动。

## 工具链里先发现的两处阻断

第一次开发集跑在 `results/runtime/correctness/v3-coding/dev-first-truncated/`。两个任务的最后一轮 `finish_reason` 都是 `length`，输出在 4000 token 处被截断，运行器把它当成模型主动结束，标成 `MODEL_DID_NOT_ACT`。同一轮里，`read_file` 拒绝读取工作区里本来就有的公开测试文件。

这两处会在模型还没编辑时把循环掐断。确认之前做了三处修改，系统提示没有改：截断后续写，`max_tokens` 提到 8000，公开测试文件允许读取但仍然禁止修改 `tests`。工具返回里没有 `instruction` 或提交命令。

接着用协议里最初的 12 轮再跑。两个任务都把轮数用完，标签是 `BUDGET_EXCEEDED`，仍然没有补丁。12 轮单次工具调用只够逐页阅读。确认之前把轮数改成 24，40 次工具调用和 420 秒没有变。这是最后一次预算调整。

## 24 轮开发集

原始记录在 `results/runtime/correctness/v3-coding/dev/`。模型是 `deepseek-flash`，温度 0。

| 任务 | 标签 | 读到相关源文件 | 改了源码 | 跑了公开测试 | 测试后继续 | 补丁可应用 | SHA-256 | 容器评分 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| code-click-2247b35e | ENVIRONMENT_BLOCKED | 是 | 是 | 是 | 是 | 是 | a8be9b423de5d969fcf1cdf150ef1a131ac6d81bdf7e208ce36f8915b69327fc | 没有 |
| code-click-8abb77a8 | ENVIRONMENT_BLOCKED | 是 | 是 | 是 | 是 | 是 | 87aa50d68edb1d6da3ee195cadd85d9d2328d251c7dd61aa6928e97e709a4c60 | 没有 |

两份补丁都是源码修改，不是注释。第一份在 `src/click/testing.py` 里把 `0x04` 和 `0x1a` 显示成 `^D`、`^Z` 并从返回值里去掉。第二份在 `src/click/_textwrap.py` 里增加可见宽度边界。`git apply --check` 通过。

标签是 `ENVIRONMENT_BLOCKED`，因为容器没有启动，不是因为隐藏测试失败。隐藏测试没有降级，也没有用宿主机 pytest 代替容器结论。问题描述来自历史提交说明，失败行由评分器摘出；黄金补丁和隐藏测试 diff 没有放进提示。

这一步证明工具链可以读、改、跑公开测试、在测试输出之后继续，并冻结补丁。它没有证明隐藏测试通过。

## 本机容器补测

补测没有调用模型，也没有改这两份 `patch.diff` 或 `trace.json`。记录在 `results/runtime/correctness/v3-coding/rescore/summary.json`。镜像是 `python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`，检查到的 image id 与这个摘要相同。隐藏测试来自 v2 清单里保存的 `test_patch`。

两份补丁的 SHA-256 都与对应 trace 的 `diff_sha256` 一致。初始树加隐藏测试都失败。黄金补丁加隐藏测试都通过。黄金通过只说明评分器能认出历史修复，不记成模型成功。

两份冻结补丁都没有通过隐藏测试。`code-click-2247b35e` 在 `tests/test_testing.py` 上 7 失败、45 通过。`code-click-8abb77a8` 在 `tests/test_formatting.py` 上 10 失败、205 通过。开发集隐藏测试通过数是 0/2。

第一次截断跑在 `dev-first-truncated/`，不进入这个分母。
