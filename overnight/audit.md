# 阶段 0 审计

审计时分支是 `main`，提交 `a7463d2`，与 `origin/main` 一致，没有领先或落后。工作区已有两处改动，本轮不覆盖、不删除、不重置：

- 已修改：`docs/项目开发文档2.0.md`
- 未跟踪：`.github/workflows/ci.yml`。上次推送因 OAuth 令牌没有 workflow 范围被拒绝，这个文件继续留在磁盘上，不放进本轮提交。

开始时完整 `pytest` 为 85 passed、0 failed（16.85 秒）。本轮新增测试后的最终计数写在文末。

## 凭据

仓库里没有 `.env`。进程环境和该文件都没有同时给出 `EPOCHGATE_BASE_URL`、`EPOCHGATE_API_KEY`、`EPOCHGATE_MODEL`。三个名字都缺。因此不发任何真实模型请求，也不写 `overnight/baseline.jsonl`。

## 现有后端和追踪

`HttpToolBackend` 的 `supports_streaming` 为 false。一次调用是一次非流式 `POST`。取消只在客户端做标记，回执 `reclaimed` 为 false，`resource_reclaim_observable` 为 false。关闭连接不能说明服务端停止计算。

用量只有在响应的 `usage` 里存在名为 `tokens` 的字段时，才会标 `usage_source=backend`。兼容接口常见的是 `prompt_tokens` 和 `completion_tokens`，所以即便调用成功，改记录方式之前也记不到供应商用量。本轮只改记录，不改调度：供应商 `usage` 原样进入事件；出现 `tokens`、`prompt_tokens` 或 `completion_tokens` 时标 `usage_source=backend`。不把这几项相加成账单或节省。

事件日志能记下 `task_id`、`attempt_id`，以及 `backend_request`、`cancel_requested`、`cancel_acknowledged`、`action_rejected`、`action_committed`。没有单独的 invalidation 事件。资格撤销是 `attempt_superseded`，迟到结果由网关拒绝。观测版本在刷新事件里。

没有一条不带密钥、又能证明真实模型订对库存的可复现命令。`http-smoke` 在缺配置时以状态码 2 退出。

## 本轮不动的部分

不改 B0、B1、B2 的决策理由，不改 `configs/frozen.yaml`，不改 `docs/实验协议.md`。不改现有 `FakeBackend` 在取消后仍交出 `completed` 的行为，现有不变量测试依赖迟到结果仍被送到网关再拒绝。不使用种子 11、12、13。不接第二环境、vLLM 或机器人。

阶段 3 不新写真实流式客户端。当前 HTTP 后端明确不支持流式。

## 最终测试

本轮代码和测试写完后，完整 `pytest` 为 91 passed、0 failed（17.77 秒）。开始时的 85 passed 是改动前的计数。
