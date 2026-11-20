# Week 2 仓库侦察

侦察只读现有代码。Week 2 的新代码、清单和运行都放在 `week2/`。没有修改 `docs/开发文档/week1-historical-audit/` 下的文件，也没有回填 Week 1 的缺失格。

## 1. 运行时语言

Python。入口包是 `src/epochgate`，执行控制层在 `packages/runtime/src/epochgate_runtime`。`pyproject.toml` 要求 `requires-python >= 3.12`。

## 2. Python 版本

本机解释器是 `.venv\Scripts\python.exe`，版本 3.12.2（conda-forge，MSC v.1937 64 bit）。平台是 Windows-11-10.0.26200-SP0。

## 3. 数据库引擎

SQLite，链接库版本 3.46.0。文件库的默认 `journal_mode` 是 `delete`，`synchronous` 是 `2`（FULL）。内存库的 `journal_mode` 会显示为 `memory`，Week 2 的运行使用文件库。

## 4. SQLite 连接方式

没有单独的公共连接函数。两处实现都是直接 `sqlite3.connect`，并设置 `PRAGMA busy_timeout = 5000`：

- `packages/runtime/src/epochgate_runtime/ledger.py` 的 `RunLedger.__init__`
- `packages/runtime/src/epochgate_runtime/sink.py` 的 `EffectSink.__init__`

`RunLedger._begin_immediate` 使用 `BEGIN IMMEDIATE`。`src/epochgate/eg2_events.py` 另有 `PRAGMA journal_mode=WAL`，那是另一条事件路径，Week 2 的 Outbox 不沿用 WAL。Week 2 的 `week2/db.py` 复用 busy_timeout 和显式 `BEGIN IMMEDIATE`，并把 `isolation_level` 设为 `None`，以便故障点能切开事务边界。

## 5. Direct 路径

协议原文在 `docs/开发文档/M4-M6独立确认协议.md`：B0 Direct 不查资格、不查版本，把提案载荷交给接收端。实现是 `scripts/reliability_contract.py` 的 `commit_effect`，`arm == "B0"` 分支。Week 2 的 `week2/paths.py` `DirectPath` 保持这个行为：`authorize` 恒为真，发送的是冻结字节。

## 6. Admission 路径

同一协议把 B1 写成 Admission-only：决定点是资格检查返回允许，从该点到接收端接纳之间的窗口仍然存在。实现是 `commit_effect` 的 `arm == "B1"` 分支：先记下 `eligible`，延迟通知可以在这之后移动权威，然后仍然发送。Week 2 的 `AdmissionPath` 在 `authority_gate` 读取 token、epoch 和 eligible 并锁存，`after_admit_before_send` 之后不再读取。

## 7. Claim 路径

协议的 B3 先提交 Claim，再把 Claim 中的字节交给同一个接收端；崩溃点在两者之间；恢复只重放 Claim 字节。实现是 `RunLedger.claim_publish` 和 `reliability_contract.resume_execution`。账本里存的是摘要。Week 2 的 `week2/baselines/byte_claim.py` 把同一份提案字节和摘要放进同一个 SQLite 事务，恢复时读出这些字节，不重新评分。这不是 Semantic Claim。

## 8. 接收端

`scripts/reliability_contract.py` 的 `SharedReceiver.accept`。同一 `effect_key` 的相同载荷返回 `replayed` 且不再写入；不同载荷返回 `conflict` 且不覆盖文件。Week 2 四条路径都通过 `week2/receiver_adapter.py` 调用这个类。包装器只附加序号、呈现的 token，以及故障注入的重复或冲突投递。

## 9. 权威账本

`RunLedger` 的 `runs` 表保存 `fencing_token`、`state_version` 和 `eligible`。`revoke` 把 token 加一、eligible 置假。`supersede` 在 `RunLedger.supersede`。Week 2 不把运行写进历史 `ledger.sqlite`。它在每次运行自己的 `state.sqlite` 里放一行权威状态和 `authority_log`，字段对应 token、epoch、eligible。这是同一类 fencing 状态，不是第二套语义协议。

## 10. 现有故障注入

`commit_effect` 的 `interrupt` 参数在接纳前返回 `crashed_before_accept`。`docs/开发文档/实验方法.md` 把 `process.kill()` 和断电分开。Week 1 审计写明掉电未测。Week 2 使用进程内钩子 `FaultCut`，不用 `process.kill`。

## 11. 测试框架

`pyproject.toml` 的 `[tool.pytest.ini_options]` 使用 pytest，`testpaths = ["tests"]`，`pythonpath = ["src"]`。已安装 pytest 9.1.1。Week 2 的测试在 `week2/oracle_test_suite/`，用显式路径运行，不改这个配置。

## 12. 提交与环境哈希

`git rev-parse HEAD` 是 `b6dd92b491f5235b89a649132e63a889df598316`。这个提交不包含 `week2/`。实现哈希是 `week2/**/*.py` 按相对路径排序后的 SHA256，由 `week2/hashing.py` 的 `implementation_hash` 计算。环境哈希是下面记录的规范 JSON 的 SHA256：Python 版本、`platform.platform()`、`sqlite3.sqlite_version`，以及 pytest、pydantic、epochgate-runtime、aiosqlite、fastapi、uvicorn、httpx、typer、tiktoken、scikit-learn、pytest-asyncio、hypothesis 的发行版本。缺包记为 `ABSENT`。

## Week 1 合同

`docs/开发文档/week1-historical-audit/outcome.schema.json` 没有 `version` 字段。`$id` 是 `https://epochgate.local/week1/outcome.schema.json`。用户把这份合同称为 v1.0.0；文件里没有可改的版本号，本周也不改文件。Week 2 的裁决沿用其中的枚举：`UNKNOWN` 不是失败，`RESTORED_BYTES` 不是 `CONFIRMED_EFFECT`，`CORRECT_STOP` 不是后继失败。证据 oracle 额外保留 `CONFLICT`，因为 Week 1 的 `evidence_state` 枚举里没有它；这些行只出现在 `week2/week2_replay_joint.csv`，不写回 `archived_joint.csv`。

Week 1 审计的数字是历史子集：联合观察 52/72，EFE 28/52，EFE@success 6/16。它们不是 Week 2 的结果。
