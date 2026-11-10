# epochgate-runtime

执行控制面。`RunLedger` 决定一次工具调用还有没有提交资格，副作用有没有落地，崩溃之后能不能对上。

安装名是 `epochgate-runtime`，版本 `0.1.0`。Python 导入名是 `epochgate_runtime`。它不导入研究包 `epochgate`。

```powershell
uv run pytest packages/runtime/tests -q
uv run epochgate-runtime doctor --out results/runtime/doctor.json
```

仓库里的边界、还没关闭的 `eg_bash`，以及研究包为什么还在，写在 [docs/现行/架构/仓库布局.md](../../docs/现行/架构/仓库布局.md)。
