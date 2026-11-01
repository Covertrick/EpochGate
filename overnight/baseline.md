# 阶段 1 静态真实模型

基线被凭据挡住，没有调用模型。

检查了仓库根目录的 `.env` 和进程环境。`EPOCHGATE_BASE_URL`、`EPOCHGATE_API_KEY`、`EPOCHGATE_MODEL` 三个都缺。缺任何一个都不发请求。因此没有真实的读工具、预约参数、库存减少或库存不足拒绝可以记录。

没有写 `overnight/baseline.jsonl`。这里的空白不是失败样本，也不是补出来的成功样本。

供应商用量的记录方式已经改成原样保存，并在出现 `tokens`、`prompt_tokens` 或 `completion_tokens` 时标 `usage_source=backend`。这项由本地假响应测试覆盖，没有打到外部接口。不把这几项相加，也不当成账单。

阶段 1 没有通过，所以不做真实动态实验，也不做真实配对。阻塞原因是缺配置，不是提示、工具模式、模型或解析失败。
