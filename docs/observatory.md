# EpochGate Execution Observatory

## 改造前的审计

- 控制台由 FastAPI 直出 HTML、CSS 和原生 JavaScript。原页面把演示入口、任务表单、运行列表和报告放在同一条阅读动线上。原样式的页面宽度上限为 960px，多个全宽 `hr`、表格行边和输入下边框共同强化了横线。全局样式还隐藏了滚动条，降低了长表和日志的可发现性。
- 原详情把服务端效果图和 HTML 表直接插入页面。图表可以复用作导出，但原交互没有将事件、Attempt、幂等键、目标记录和回执连成可追踪的证据链。
- 原接口已经提供运行、事件、效果、会话、样本、命令日志和任务状态。运行阶段和效果阶段来自不同记录。若只按是否存在回执统计，会把持有回执与核验回执混为一谈。
- 27 格 Effect History 是固定的研究归档，有独立的目标数据库、时序记录、协议、结果和校验清单。研究页从这些文件读取，不依赖运行账本或普通演示的结果。

## 使用

在仓库根目录启动本机控制面，终端会打印地址。如果已经运行旧版服务，重启该进程以加载更新。继续使用原来的 `--data-dir` 可以读取已有历史。

```powershell
uv run --locked --package epochgate-runtime egctl serve
```

| 页面 | 验证入口 |
| --- | --- |
| 总览 | 真实 Run 计数、已确认效果、拒绝事件、UNKNOWN 效果、五步机制和四个离线场景 |
| 运行工作台 | 本地仓库或样本、路径树、任务名称和描述、测试命令、单轮超时、实际依赖预检 |
| 运行记录 | 按 Run ID、任务、场景、阶段或日期筛选和导出 |
| 运行详情 | 按事件序列检查证据，联动 Attempt、Effect 和 Receipt，查看恢复、取消、会话操作、日志和产物 |
| 实验分析 | 查看 27 格冻结结果、调度对照、目标账本、时序、归档 SHA-256 以及独立重算 |
| 系统状态 | Runtime、Python、控制面、SQLite、Worker、Docker、模型配置、存储和访问日志 |

## 接口和数据口径

| 接口 | 证据来源和新增能力 |
| --- | --- |
| `GET /runs` | 从原 RunLedger 记录补充场景、账本事件时间、执行返回状态与观测计数 |
| `GET /runs/{id}?figures=false` | 查询 Attempt、Effect、原始事件、回执和概览。省略参数时兼容既有生成 SVG / HTML 报告的行为 |
| `POST /observatory/workspace`、`POST /observatory/preflight` | 读取本地仓库文件名和会话依赖。真实任务的执行器为 DSH，容器工作目录固定为 `/workspace` |
| `POST /actions/cancellation` | 运行真实的本地子进程取消演示，账本记录停止请求、进程退出和槽位释放的观测顺序 |
| `GET /observatory/system`、`GET /observatory/system/log` | 从运行中实例、实际数据库连接、Docker 探测和本地任务读取状态 |
| `/observatory/research` | 读取冻结归档、逐格目标账本并下载证据。独立重算使用已有 `effect_history.run audit` |

回执核验使用发布时读回证据或 SQLite 接收器的持久回执。旧记录缺少关联时，详情仅在同一 Run 的 Fencing Token 唯一匹配时补充 Attempt 链接，并标注来源。Effect 计数按账本中的幂等键计算，包括尚未落地的意图。重复发布计数覆盖观测到的文件发布事件。账本阶段、执行器返回和回执核验在详情中各自显示。

实际的提交前后值、历史日志、精确结束时间和模型连通性取决于该次运行是否采集了相应证据。缺失项显示 `NOT_OBSERVED` 或 `NOT_AVAILABLE`。实验归档的 Git SHA 是归档引入提交，实验运行源版本以 manifest 中的源文件 SHA-256 为准。

## 验收截图

- [总览](assets/observatory/overview.png) · [运行详情](assets/observatory/run-detail.png) · [运行记录](assets/observatory/runs.png)
- [工作台](assets/observatory/workspace.png) · [实验分析](assets/observatory/research.png) · [系统状态](assets/observatory/system.png)
- [390px 移动端总览](assets/observatory/overview-mobile.png)

## 验收与复跑

- Runtime 测试覆盖原有控制面和新增的证据关联、取消顺序及研究归档接口。运行 `uv run --locked pytest packages/runtime/tests -q`。
- Playwright 在 1920×1080 和 390×844 验证页面与响应式布局。桌面流程覆盖四个真实离线演示、事件详情、证据跳转、日志搜索、JSON 导出、历史筛选、样本预检、归档下载、独立重算和系统检查。浏览器控制台未记录错误。
- 本机真实 Agent 会话在预检阶段发现 Docker 引擎管道不可访问，且没有配置 DeepSeek 密钥。容器和在线依赖对应的集成测试由环境条件跳过。

用空的测试数据目录启动控制面，再在另一个终端运行浏览器验收。也可用 `--executable` 指定本机 Chromium 或 Edge。

```powershell
uv run --locked --package epochgate-runtime egctl serve --data-dir .epochgate/observatory-smoke
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/observatory_browser_smoke.py --screenshots docs/assets/observatory
```

## 变更清单

- 前端入口与视觉系统位于 `console/index.html`、`console/app.css` 和 `console/app.js`。
- HTTP 接入与只读模型位于 `httpapp.py` 和 `observatory.py`。运行观测、演示、会话、任务及导出复用 `observations.py`、`demo.py`、`actions.py`、`sessions.py`、`jobs.py`、`processes.py` 和 `figures.py`。
- 本地控制台依赖包括 `egctl.py`、`agent_loop.py`、`plugin_patch.py`、`api.py`、`ledger.py`、`sandbox.py`、`service.py`、`lib/control-client.js`、`runtime_coding_run.py`、样本和依赖锁。
- 验收代码位于 `test_console_api.py`、`test_observatory.py` 和 `scripts/observatory_browser_smoke.py`。完整路径清单可用 `git diff --name-status 541621b HEAD` 获取。
