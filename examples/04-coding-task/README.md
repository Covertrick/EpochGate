# 容器编码任务

评分断言是 `bug.value() == 1`。这不是官方 SWE-bench。

控制服务先开着，然后：

```powershell
uv run egctl demo --scenario coding --model deepseek-flash
```

不设置 `EG_USE_EXISTING_CONTROL` 时，`scripts/runtime_coding_run.py` 仍会自己启动一个控制服务，输出留在 `results/runtime/coding_run.json`。已归档的 Run `code-de85cb5530a6` 在 `results/runtime/runs/`，不要覆盖那份原始记录。
