# RSR 消融

比较的代码路径是 `ByteClaim` 与 `ByteClaim+RSR`。场景是 S14（`p20`，检查点名 `after_sealed_before_replace`）和 S16（`p22`，检查点名 `lost_receipt`）。两条路径都是：子进程执行 `ByteClaim.commit`，父进程在 `SharedReceiver.accept` 之前用 `TerminateProcess` 结束它，新进程再打开 SQLite。

`ByteClaim` 直接发布 claim 里的字节，发布之后用 `semantics_oracle` 给结果打分。`ByteClaim+RSR` 在发布前对同一份密封字节和该场景的 `world_after` 调用 `semantics_oracle`。`VALID` 才发布。`INVALID` 返回 `SEMANTICALLY_SUPERSEDED`，不发布。其他结果，包括 `UNKNOWN`，返回 `UNKNOWN`，不发布。

无效恢复：发布发生，且 `semantics_oracle` 的结果是 `INVALID`。S14 与 S16 的冻结世界已经漂移，所以这组运行的预检预期是 `INVALID`。`VALID` 和 `UNKNOWN` 两个分支在代码里存在；这组场景运行不走它们。

| 场景 | 路径 | 次数 | 无效恢复 | UNKNOWN | SEMANTICALLY_SUPERSEDED | CORRECT_STOP | 恢复耗时均值 ms | 预检均值 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S14 | ByteClaim | 10 | 10 | 0 | 0 | 0 | 590.023 | 0.000 |
| S14 | ByteClaim+RSR | 10 | 0 | 0 | 10 | 0 | 699.839 | 0.045 |
| S16 | ByteClaim | 10 | 10 | 0 | 0 | 0 | 572.696 | 0.000 |
| S16 | ByteClaim+RSR | 10 | 0 | 0 | 10 | 0 | 653.461 | 0.030 |

## 权衡

S14：`ByteClaim` 无效恢复 10 / 10，`ByteClaim+RSR` 无效恢复 0 / 10，减少 10。证据状态 `UNKNOWN` 从 0 到 0。`SEMANTICALLY_SUPERSEDED` 从 0 到 10。`CORRECT_STOP` 从 0 到 0。恢复耗时均值变化 +109.816 ms。RSR 预检均值 0.045 ms。
S16：`ByteClaim` 无效恢复 10 / 10，`ByteClaim+RSR` 无效恢复 0 / 10，减少 10。证据状态 `UNKNOWN` 从 0 到 0。`SEMANTICALLY_SUPERSEDED` 从 0 到 10。`CORRECT_STOP` 从 0 到 0。恢复耗时均值变化 +80.765 ms。RSR 预检均值 0.030 ms。

无效恢复从每场景 10 次降到 0 次，停止发布的次数升到 10 次，状态是 `SEMANTICALLY_SUPERSEDED`。本次 40 次预检全部是 `INVALID`，`UNKNOWN` 保持 0，后继意义上的 `CORRECT_STOP` 保持 0。耗时是恢复进程的墙钟。正数表示 RSR 这组更慢。预检调用本身不到 0.05 ms，墙钟均值差大于这次调用，并落在各次启动时间的波动里。

RSR 不消除 TOCTOU。预检与发布不是同一次原子提交。`VALID` 只覆盖调用 `semantics_oracle` 的那一刻；在那之后、写入效果文件之前，世界仍可能变化。本次 S14 与 S16 没有走进 `VALID` 分支，因此这组运行没有测量那个窗口里的世界变化。
