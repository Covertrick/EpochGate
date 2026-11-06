# 容器执行

论文评分只在 Linux 容器里做。Windows 工作区不跑 SWE-bench。

容器要求：

- 非 root 用户。
- 工作区可写，gold 目录和 `.env` 不挂载。
- 不继承 `DS_API_KEY` 或 `DEEPSEEK_API_KEY`。
- 不提供能 `git push` 的远程凭据。
- Git 对象停在 `base_commit`。未来变更由宿主机控制器单独持有。
- 脚本和检出文件使用 LF。

镜像 digest 写入每次运行的 manifest。没有 digest 的运行不能进入 R1。
