# 显式撤回工具先导

**当前判为 No-Go。** 选择 PyPI/Twine 做动态资格验证，Cargo 和 npm 做源码及契约筛查。目标是检验同一次发布的自动重试会不会把明确撤回的对象重新发布。

| 候选 | 核对 | 判定 |
| --- | --- | --- |
| PyPI/Twine | Twine 7.0.0 会重试上传的 5xx。Warehouse 对相同现存文件返回成功并放弃本次事务，对曾用但已删除的文件名拒绝。Yank 与恢复上架是各自操作。 | 用真实客户端做了回环 HTTP 验证，受测的同文件重试没有解除 yank 或恢复删除。 |
| crates.io/Cargo | Yank 改变版本的解析资格；已经发布的同一版本不能覆盖。 | 同版本重新发布缺少预期的覆盖通道。本轮以公开契约筛选。 |
| npm | 同一包名和版本在 unpublish 后不能复用。 | 普通同版本重试缺少覆盖通道。本轮以公开契约筛选。 |

[Warehouse 上传规则](https://github.com/pypi/warehouse/blob/80e31954190164b73e48940fad1fcd050a7293ba/warehouse/forklift/legacy.py#L1193) · [Twine 上传实现](https://github.com/pypa/twine/blob/7.0.0/twine/repository.py) · [PyPI Yank](https://docs.pypi.org/project-management/yanking/) · [Cargo 发布与撤回](https://doc.rust-lang.org/stable/cargo/reference/publishing.html) · [npm Unpublish](https://docs.npmjs.com/cli/v11/commands/npm-unpublish/)

## 实验结果

| 首次请求后的状态 | 第一次响应 | 自动重试响应 | Twine 退出 | 最终状态 |
| --- | ---: | ---: | ---: | --- |
| 尚未上传 | 503 | 200 | 0 | 正常发布 |
| 已上传 | 503 | 200 | 0 | 正常发布，无重复写入 |
| 已上传且已 yank | 503 | 200 | 0 | 仍为 yanked，无重复写入 |
| 已上传且已删除 | 503 | 400 | 非零 | 仍为删除，无重新发布 |

四格均发送同一文件两次，各有一次目标写入。真实客户端在 yanked 格报告上传成功，受测目标状态仍为 yanked。上传确认和当前推荐安装状态有不同含义。这里没有证据表明这种区分违背 Twine 的上传合同。

动态实验使用真实 Twine，目标为绑定回环地址的最小 Warehouse 规则模拟器。源码核对与客户端行为分别提供证据。未部署 Warehouse，未向任何公共注册表上传，也未动态测真实 Cargo 或 npm 注册表。结果不能推广到添加另一分发文件、多文件发布或不同注册表实现。

[事前协议](PROTOCOL.md) · [逐格证据](../../results/experiments/package-withdrawal/pilot-01/report.json)

## 复现

准备隔离的 Twine 7.0.0 环境后，从仓库根目录运行。每次用新路径。

```powershell
uv venv .epochgate/twine-7-probe
uv pip install --python .epochgate/twine-7-probe/Scripts/python.exe twine==7.0.0
.venv/Scripts/python.exe -m experiments.package_withdrawal.run .epochgate/package-withdrawal-new --client-python .epochgate/twine-7-probe/Scripts/python.exe
```
