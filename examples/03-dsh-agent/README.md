# 把 DSH Agent 接到控制服务

先在一个终端启动服务：

```powershell
uv run egctl init
uv run egctl serve
```

`egctl serve` 打印 `http://127.0.0.1:8765`。研究 profile 已经加载 TypeScript 插件 `packages/eg-workspace`。插件读 `EG_CONTROL_URL`，不自己打开 SQLite。

另一个终端：

```powershell
$env:EG_CONTROL_URL = "http://127.0.0.1:8765"
$env:EG_REQUIRE_CONTROL = "1"
uv run egctl demo --scenario coding --model deepseek-flash
```

这条命令调用 `scripts/runtime_coding_run.py`。脚本通过插件访问已经启动的控制服务。没有密钥时它会停下来，不会发请求。

`infrastructure/dsh-profiles/research/cordis.patch.yml` 里的插件地址是本机绝对路径 `file:///D:/Workspace/EpochGate/packages/eg-workspace/lib/index.js`。换一台机器时，把这一行改成那台机器上的绝对 file URL。
