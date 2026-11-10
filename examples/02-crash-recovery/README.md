# 崩溃恢复

Worker 在接收器已经接受、receipt 还没写入时以退出码 86 结束。父进程重新打开同一本 SQLite，按接收器补上同一次效果。再对账一次是重放，接受次数保持 1。

```powershell
uv run egctl demo --scenario crash-recovery --offline
```

不需要事先删除 `.epochgate/*.sqlite`。
