# EpochGate

多会话 Agent Runtime 的**版本化准入与取消层**：分离「资源执行状态」与「结果资格」。

> 当前：v0.1 可运行骨架（Fake 环境 + Fake 后端）。研究收益仍是待验证假设，不是已测性能。

## Quickstart

```powershell
cd C:\Project\EpochGate
python -m uv sync --extra dev
python -m uv run pytest -q
python -m uv run epochgate demo
```

## 文档（按用途）

| 文档 | 用途 |
| --- | --- |
| [docs/工程实现说明.md](docs/工程实现说明.md) | **代码结构、架构约束、怎么跑** |
| [docs/项目开发文档.md](docs/项目开发文档.md) | 研究设计与实验规格 |
| [docs/Freshness_Agent_Runtime_项目文档.html](docs/Freshness_Agent_Runtime_项目文档.html) | 设计书 HTML |
| [docs/figures/](docs/figures/) | 架构 / 时序示意图 |

## 包布局

```text
EpochGate/                 ← 仓库根
├── src/epochgate/         ← Python 包（import epochgate）
├── tests/
├── examples/
└── docs/
```

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
