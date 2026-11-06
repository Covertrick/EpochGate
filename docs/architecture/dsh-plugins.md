# DSH 插件

锁定 `@deepseek-ai/dsh@0.2.2-rc.1` 和同一版本的插件依赖。研究运行使用 `sdk` profile。`sdk-minimal` 是另一套插件树，不使用。

启动时执行 `dsh --profile sdk --dump-config`，确认树上有 JSON-RPC Server、DeepSeek Provider，以及下面三个研究 bundle。缺依赖就拒绝启动。

每个 bundle 在 `package.json` 里声明 `dsh.bundle` 和兼容的 DSH 版本。只导出 `apply(ctx)` 不算已经加入 profile。

## 三个安装包

`eg-workspace` 包含 repo-tools、checkpoint、change-feed 和 container executor。文件、终端和测试都进指定 Linux 容器。

`eg-policy` 包含 evidence-tracker、dependency-policy、commit-gateway 和 tool-guard。四种策略是这个包的配置，不是四套 Agent Loop。

`eg-observability` 包含 session-export、事件关联、成本审计和 budget-guard。评分标签不从这里读。

任务挖掘、gold 标注、评分和统计留在 Python 进程。

## 生命周期

插件通过 Cordis `inject` 取服务，不互相导入内部实现。注册是可撤销的：卸载时去掉工具、监听器和 guard。策略替换保留已经写下的 Session 和证据日志。

工具策略放在 `tools/pre-execute`。不能被后续监听器撤销的拒绝放在 `ctx.tools.guard()`。操作系统隔离放在容器。

研究 profile 把 `dsh-session-log-deepseek` 的 `enabled` 设为 false。模型请求仍然发送当前任务上下文。

## 加载验收

- `--dump-config` 的插件树与 profile 声明一致。
- 装上再卸下之后，工具表回到装载前。
- 声明了不存在的服务时，进程以非零状态退出。
- 换成另一种 dependency policy 后，已有 `session_id` 和证据前缀还在。
