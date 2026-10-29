# EpochGate

多会话 Agent Runtime 的**版本化准入与取消层**：分离「资源执行状态」与「结果资格」。

> 当前：**v0.4**。同一 runtime 上可以切换 B0、B1、B2、临近准入和 EVDF。故障场景、SQLite 并发和单 cell 复现已经接上。研究收益仍是待验证假设，不是已测性能。

## Quickstart

在仓库根目录：

```powershell
python -m uv sync --extra dev
python -m uv run pytest -q
python -m uv run epochgate demo
python -m uv run epochgate experiment --seed 1 --out traces
python -m uv run epochgate reproduce --cell-id b0-seed-1-none --manifest traces/manifest.json --out traces
```

本机没有把 `uv` 加进 PATH 时，用 `python -m uv`。需要 Python 3.12+。

真实模型冒烟读仓库根的 `.env`，只进入当前进程。变量名是 `EPOCHGATE_BASE_URL`、`EPOCHGATE_API_KEY`、`EPOCHGATE_MODEL`。`--base-url` 覆盖模型地址；`--env-url` 才是库存服务。缺配置时退出码是 2，不会发请求。

```powershell
python -m uv run epochgate env-serve --host 127.0.0.1 --port 8080 --db traces/inventory.sqlite
python -m uv run epochgate http-smoke --env-url http://127.0.0.1:8080 --chaos scenarios/chaos.yaml --seed 42 --out results/v0.4
python -m uv run epochgate reproduce --cell-id checks-seed-42-none --manifest results/v0.4/manifest.json --out results/v0.4/reproduce
```

## 文档（按用途）

| 文档 | 用途 |
| --- | --- |
| [docs/工程实现说明.md](docs/工程实现说明.md) | 当前代码结构、架构约束、怎么跑 |
| [docs/测试记录/v0.4.md](docs/测试记录/v0.4.md) | v0.4 测了什么、结果在 `results/v0.4/` |
| [docs/问题记录/开发问题.md](docs/问题记录/开发问题.md) | 开发时遇到的问题、原因和处理 |
| [docs/工程实现说明/](docs/工程实现说明/) | v0.1 到 v0.4 的实现说明原文 |
| [docs/项目开发文档.md](docs/项目开发文档.md) | 研究设计与实验规格。第 3–11 节仍是待验证规格 |
| [docs/Freshness_Agent_Runtime_项目文档.html](docs/Freshness_Agent_Runtime_项目文档.html) | 设计书 HTML |
| [docs/figures/](docs/figures/) | 架构 / 时序示意图 |

## 包布局

```text
EpochGate/                 ← 仓库根
├── src/epochgate/         ← Python 包（import epochgate）
├── tests/
├── results/v0.4/          ← 这一版的冒烟记录，不含密钥
└── docs/
```

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
