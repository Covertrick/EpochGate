"""命令行：演示、HTTP 冒烟、同种子对照实验。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from epochgate.experiment import reproduce_cell, run_experiment, run_freshness_cell
from epochgate.smoke import run_http_smoke

app = typer.Typer(add_completion=False, no_args_is_help=True, help="EpochGate 研究运行时命令行")


def load_project_env(path: Path | None = None) -> None:
    """把仓库根的 ``.env`` 读进当前进程。

    输入：可选文件路径。默认是仓库根的 ``.env``。
    输出：无。
    副作用：只填充当前进程里还没有的变量，不改用户环境，也不覆盖已经设置的变量。
    失败：文件不存在时直接返回。
    """
    env_path = path or Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def http_settings() -> tuple[str, str, str] | None:
    """读取真实 HTTP 后端需要的三个环境变量。

    输入：无。先读仓库 ``.env``，再读进程环境。
    输出：base_url、api_key、model；缺任一项时为 None。
    副作用：可能把 ``.env`` 中尚未设置的变量填进当前进程。
    失败：无。
    """
    load_project_env()
    base = os.environ.get("EPOCHGATE_BASE_URL")
    key = os.environ.get("EPOCHGATE_API_KEY")
    model = os.environ.get("EPOCHGATE_MODEL")
    if not base or not key or not model:
        return None
    return base, key, model


@app.callback()
def _root() -> None:
    """EpochGate 研究运行时命令行。启动时读取仓库根的 ``.env``。"""
    load_project_env()


@app.command("demo")
def demo(seed: int = typer.Option(7, help="外生库存变化的种子")) -> None:
    """在同一份外生库存上比较 B0、B3 和 J0 的刷新与吞吐。"""

    async def _run() -> None:
        for policy in ("b0", "b3", "j0"):
            metrics = await run_freshness_cell(
                seed=seed,
                policy=policy,
                jsonl_path=Path("traces") / f"demo-{policy}-seed-{seed}.jsonl",
            )
            typer.echo(
                f"{policy} on_time={metrics['on_time_success']} "
                f"refresh_count={metrics['refresh_count']} "
                f"refresh_skipped={metrics['refresh_skipped']} "
                f"throughput={metrics['effective_throughput']:.4f} "
                f"observe_calls={metrics['observe_calls']}"
            )

    Path("traces").mkdir(parents=True, exist_ok=True)
    asyncio.run(_run())


@app.command("http-smoke")
def http_smoke(
    base_url: str | None = typer.Option(None, "--base-url", help="覆盖模型地址。默认读 EPOCHGATE_BASE_URL"),
    env_url: str | None = typer.Option(None, "--env-url", help="已启动的库存服务。不传则 ASGI 直连"),
    chaos: str = typer.Option("scenarios/chaos.yaml", "--chaos", help="故障场景"),
    seed: int = typer.Option(42, "--seed", help="故障随机序列的种子"),
    out: str = typer.Option("results/v0.4", "--out", help="trace 和 manifest 目录"),
    requests: int = typer.Option(6, "--requests", help="发往模型的请求数"),
    service_prior: float | None = typer.Option(None, "--service-prior", help="用这个秒数检查不可达拒绝"),
) -> None:
    """走真实模型 transport，并跑库存、租约和准入检查。"""
    settings = http_settings()
    if settings is None and base_url is None:
        typer.echo("缺少 EPOCHGATE_BASE_URL、EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
        raise typer.Exit(code=2)
    if settings is None:
        typer.echo("缺少 EPOCHGATE_API_KEY 或 EPOCHGATE_MODEL")
        raise typer.Exit(code=2)
    configured_base, api_key, model = settings
    model_url = base_url or configured_base

    async def _run() -> None:
        report = await run_http_smoke(
            base_url=model_url,
            api_key=api_key,
            model=model,
            scenario=chaos,
            seed=seed,
            out_dir=Path(out),
            env_url=env_url,
            requests=requests,
            service_prior_s=service_prior,
        )
        typer.echo(
            f"cell_valid={report['cell_valid']} checks_passed={report['checks_passed']} "
            f"faults={report['fault_count']} service_p50={report['service_p50']:.3f}"
        )
        for item in report["checks"]:
            typer.echo(f"  {item['name']}: {'pass' if item['passed'] else 'fail'} {item['detail']}")
        if not report["ok"]:
            raise typer.Exit(code=1)

    asyncio.run(_run())


@app.command("experiment")
def experiment(
    seed: int = typer.Option(1, help="外生事件与 id 的种子"),
    out: str = typer.Option("traces", help="JSONL 输出目录"),
    scenario: str | None = typer.Option(None, help="可选故障场景 YAML。默认不注入"),
) -> None:
    """同一种子跑 B0 到临近准入，再跑 B3 和 J0，写出 manifest 并打印 trace 指标。"""

    async def _run() -> None:
        summaries = await run_experiment(seed=seed, out_dir=Path(out), scenario=scenario)
        for policy, metrics in summaries.items():
            typer.echo(
                f"{policy} tasks={metrics['tasks']} on_time={metrics['on_time_success']} "
                f"cohort={metrics['cohort_success_rate']:.3f} "
                f"stale_rejects={metrics['stale_rejects']} waits={metrics['waits']} "
                f"reject_rate={metrics['reject_rate']:.3f} "
                f"fault_rate={metrics['fault_injection_rate']:.3f} "
                f"wait_p99={metrics['wait_p99']:.3f} "
                f"refresh={metrics['refresh_count']} skipped={metrics['refresh_skipped']} "
                f"throughput={metrics['effective_throughput']:.4f}"
            )

    asyncio.run(_run())


@app.command("reproduce")
def reproduce(
    cell_id: str = typer.Option(..., "--cell-id", help="manifest 里的 cell id"),
    manifest: str = typer.Option("traces/manifest.json", help="manifest 路径"),
    out: str = typer.Option("traces", help="重跑输出目录"),
) -> None:
    """按 manifest 精确重跑一个 cell。"""

    async def _run() -> None:
        metrics = await reproduce_cell(manifest_path=Path(manifest), cell=cell_id, out_dir=Path(out))
        typer.echo(
            f"{cell_id} tasks={metrics['tasks']} on_time={metrics['on_time_success']} "
            f"fault_rate={metrics['fault_injection_rate']:.3f}"
        )

    asyncio.run(_run())


@app.command("env-serve")
def env_serve(
    db: str = typer.Option("traces/inventory.sqlite", help="SQLite 文件"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    stock: int = typer.Option(5),
) -> None:
    """启动 FastAPI 库存服务。"""
    import uvicorn

    from epochgate.env_app import create_app
    from epochgate.sqlite_inventory import SqliteInventory

    store = SqliteInventory(db, stock=stock)

    async def _setup() -> None:
        await store.setup()

    asyncio.run(_setup())
    uvicorn.run(create_app(store), host=host, port=port)


if __name__ == "__main__":
    app()
