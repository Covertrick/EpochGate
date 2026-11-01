# EpochGate

多会话智能体运行时的**版本化准入与取消层**：把资源执行状态和结果资格分开。

> 当前：**1.0**。基线到联合策略共用同一份外生库存、到达流和槽位。研究收益仍是待验证假设，不是已测性能。1.0 之后的目标见 [docs/项目开发文档2.0.md](docs/项目开发文档2.0.md)。

## 快速开始

在仓库根目录，不需要接口密钥：

```powershell
python -m uv sync --extra dev
python -m uv run pytest -q
python -m uv run epochgate demo
python -m uv run epochgate explain t-explain --trace results/explain/demo.jsonl
```

本机没有把 `uv` 加进路径时，用 `python -m uv`。需要 Python 3.12 及以上。

`explain` 读一条不含密钥的轨迹。命令的真实输出是：

```text
task=t-explain
stale_observation source=inventory reason=exogenous
eligibility_revoked reason=dirty and premise failed; revoke eligibility
late_output_rejected reason=attempt not eligible
late_output_rejected reason=insufficient stock
```

上面四行是程序打印的标签，不是性能数字。

## 文档

| 文档 | 用途 |
| --- | --- |
| [docs/工程实现说明.md](docs/工程实现说明.md) | 当前代码怎么跑 |
| [docs/实验协议.md](docs/实验协议.md) | 指标口径。不在别处再写一遍 |
| [docs/项目开发文档2.0.md](docs/项目开发文档2.0.md) | 1.0 之后要做的研究和工程 |
| [docs/项目开发文档.md](docs/项目开发文档.md) | 研究设计原文 |
| [docs/论文初稿.md](docs/论文初稿.md) | 论文骨架，不填未测数字 |
| [docs/第二环境.md](docs/第二环境.md) | 第二套公开环境为什么还没接上 |
| [docs/问题记录/开发问题.md](docs/问题记录/开发问题.md) | 开发时遇到的问题 |
| [docs/工程实现说明/](docs/工程实现说明/) | 各版实现说明原文 |

## 共同开发者

- [Covertrick](https://github.com/Covertrick)
- [chenjj2326](https://github.com/chenjj2326)
