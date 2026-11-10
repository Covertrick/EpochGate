> 副本。原文 `docs/现行/基准/官方SWE-bench.md`。文内相对链接仍指向原文所在目录。

# 官方 SWE-bench FAIL_TO_PASS

实验设计仍是下面的标准。2026-11-10 只有 1 条节点样本，没有多任务成功率。

## 要通过的标准

一条实例算作修复，需要同时满足：

1. 使用 SWE-bench 官方 `FAIL_TO_PASS` 节点，而不是整文件 pytest。
2. 基线提交上这些节点失败。
3. 官方 gold patch 在干净评分环境里让这些节点通过。
4. Agent 补丁单独评分。补丁必须改到任务源码。只增加无关文件不算修复。
5. 每次候选使用自己的 `run_id`。评分缓存不能把上一次结果复用到下一次。
6. 留下工具事件、补丁、评分命令、退出码和节点结果。失败要归到一类：没有补丁、补丁无法应用、节点仍失败、评分环境错误。

多任务报告要给出实例数、修复数、上面四类失败的计数，以及每条实例的轨迹路径。

## 当前状态

2026-11-10 评了 1 条，样本数就是 1。实例 `astropy__astropy-12907`，基线提交 `d16bfe05a744909de4b27f5875fe0d4ed41ce607`，和本机检出一致。节点来自 `princeton-nlp/SWE-bench_Verified` 的 `FAIL_TO_PASS`：

- `astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]`
- `astropy/modeling/tests/test_separable.py::test_separable[compound_model9-result9]`

镜像是 `epochgate/astropy-task:m1r`，不是 SWE-bench 官方 harness。先在干净的 `git archive HEAD` 上应用数据集 `test_patch`，再 `pip install -e`。安装需要网络。基线 pytest 退出码 1，输出是 `2 failed`。再应用数据集 gold patch 并重新安装后，退出码 0，输出是 `2 passed`。`PASS_TO_PASS` 有 13 条，这次没跑。没有生成或评分 Agent 补丁，`repair_demonstrated=false`。没有多任务成功率，也没有 500 条排行榜。

结果在 `results/runtime/swebench_12907.json`。更早一次把节点括号交给 `sh -c`，以及一次 `git apply` 在仓库里把补丁跳过并返回 0，那两次都没有真正跑到这两个节点。

同一次记录里的 `results/runtime/swebench_attempt.json` 是另一条本地断言 `bug.value() == 1`。宿主和 `python:3.10-slim` 容器上都是基线失败、参考修复通过、`probe.txt` 不算修复。那不是官方 SWE-bench。

已经有的相关记录仍然不能写成多任务成功率：

- `results/semantic-state/m0_feasibility.md`：四条 Astropy 候选在基线上跑的是整文件或整目录 pytest，退出码都是 0。`astropy__astropy-12907` 的 `test_separable.py` 是 11 passed。整文件通过不能证明官方节点在基线上失败。
- `results/runtime/f2p_local.json`：本机断言 `bug.value() == 1`。基线失败、参考修复通过、`probe.txt` 不算修复。
- 会话 `eg-code-8c90621aea59`、Run `code-de85cb5530a6`：容器里的 `bug.py` 经账本发布后，独立评分通过。断言同样是 `bug.value() == 1`。

`epochgate_runtime.f2p.recorded_12907` 对 12907 的结论保持 `repair_demonstrated=false`。
