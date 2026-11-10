# 本地运行

EpochGate 的控制面是本机进程加一本 SQLite。这里没有 Kubernetes 清单。

```powershell
copy deploy\epochgate.example.yaml epochgate.yaml
uv run egctl doctor
uv run egctl serve --config epochgate.yaml
```

`doctor` 检查 Python、账本能否初始化、数据目录能否写入、Node、DSH、插件语法，以及 WSL 里的 Docker 是否是 Linux。Docker 或 DSH 缺失时状态是 `degraded`，离线 Demo 仍可运行。Python 或账本失败时是 `blocked`。

`doctor` 不启动容器，所以它不探测一次真实的挂载。默认沙箱不挂载宿主机目录，也不挂载 docker socket。

服务重启就是再执行 `egctl serve`。数据留在 `data_dir`。已经提交的 Run 还在。`server.json` 里的 pid 如果还活着，第二次启动会停下来，避免两个进程写同一本账。
