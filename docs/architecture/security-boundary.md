# 信任边界

宿主机运行 DSH Provider、实验控制器和插件服务。Agent 能碰到的文件、终端和测试在 Linux 容器里。DeepSeek 密钥只留在宿主机进程环境，由 `.env` 的 `DS_API_KEY` 映射为 `DEEPSEEK_API_KEY`。

## 谁可以看见什么

| 对象 | 宿主机 DSH | 容器内工具 | 评分进程 |
| --- | --- | --- | --- |
| API 密钥 | 可以 | 不可以 | 不可以 |
| 当前工作区 | 不直接改 | 可以改 | 只读评分副本 |
| gold patch、witness、标注 | 不可以 | 不可以 | 可以 |
| 未来 Git 对象 | 控制器单独持有 | 不可以 | 评分时按需读取 |
| `.env` | 可以 | 不可以 | 不可以 |

容器以非 root 运行。工作区可写。网络只开放任务声明需要的依赖安装；不能 `git push`。容器退出后删除该次进程和工作目录。

## 三层拦截

1. `tools/pre-execute` 决定允许、拒绝或审批。
2. `ctx.tools.guard()` 单调拒绝：宿主机路径、`.env`、gold 目录、`git push`、未登记工具。
3. 容器的用户、挂载和网络限制。命令字符串匹配不是安全边界。

默认 DSH profile 里如果还有能在宿主机执行任意命令的 Bash，研究 profile 必须去掉或把它指到容器 executor。研究插件自己的 repo-tools 是安全的，也不能留下另一条宿主机通道。

## 验收

未授权工具调用失败。Agent 读不到宿主机 `.env`。`git push` 失败。gold 路径打开失败。容器环境没有 `DS_API_KEY` 和 `DEEPSEEK_API_KEY`。容器内 `git log` 和对象库不含未来的参考修复。执行环境是 Linux，脚本和仓库文件保持 LF。

泄漏审计同时扫描 prompt、挂载、工具目录、环境变量、检索索引和容器能读到的 Git 对象。扫描范围写成 [audit_gold_leakage.py](../../scripts/audit_gold_leakage.py)。
