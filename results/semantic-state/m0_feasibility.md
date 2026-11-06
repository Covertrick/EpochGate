# M0 数据可行性

日期：2026-11-06。机器记录在 `m0_screen.json`。

数据门是 **stop**。15 条 SWE-bench Verified 候选里，4 条六项都通过。少于五个完整事件对，所以变更臂、M2 的 Clean/Changed 分叉、M4–M6 和确认性实验都不做。

四条已经在 Linux 容器里跑完变更后测试。容器是 `eg-score`，用户 `1000:1000`，镜像 `python:3.10-slim`，digest `sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`。源码在容器自己的文件系统上，编译时加了 `-Wno-incompatible-pointer-types -Wno-error`。

- `astropy__astropy-12907`：基线 `d16bfe05a744909de4b27f5875fe0d4ed41ce607`，附近提交 `0ace3f93af69b4186199751619294ded65930d0c` 只改 `tox.ini` 里的 asdf 版本针。`pytest astropy/modeling/tests/test_separable.py` 结果是 11 passed，退出码 0。
- `astropy__astropy-13033`：`pytest astropy/modeling/tests/test_functional_models.py` 结果是 40 passed, 14 skipped，退出码 0。
- `astropy__astropy-13398`：`pytest astropy/wcs/wcsapi/tests/test_fitswcs.py` 结果是 59 passed，退出码 0。
- `astropy__astropy-14309`：`pytest astropy/io/fits/_tiled_compression/tests` 结果是 240 passed, 1 skipped, 56 xfailed，退出码 0。

后三条检出没有生成的 `astropy/_version.py`，容器里补了 `version = "0.0.0"` 之后测试才开始。

其余 11 条没有评分：3 条 GitHub `403`，3 条 `IncompleteRead`，3 条连接被重置，1 条 `RemoteDisconnected`，1 条 `no_nearby_commit`（`astropy__astropy-13453`）。没有改用仓库尖端的大 diff。

M1 的一条 Clean 闭环记在 `m1_clean.json`，七步未通过。容器和一次 DSH sdk 会话记在 `m1_environment.json` 与 `sdk_session.json`。
