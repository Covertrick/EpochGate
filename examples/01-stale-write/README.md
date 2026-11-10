# 过期写入

这个目录不另放一套账本。演示由控制面命令完成，每次生成新的 Run ID。

```powershell
uv run egctl demo --scenario stale-write --offline
```

期望看到 Attempt #1 为 `REVOKED`，Attempt #2 为 `COMMITTED`，receipt 为 `VERIFIED`，`Duplicate accepts` 为 0。报告写在 `.epochgate/runs/<run-id>/`。

这里的 REVOKED 表示第一个 fencing token 不能再发布。Run 仍接受第二个 token。把整次 Run 取消要走 revoke，取消之后没有可提交的新 token。
