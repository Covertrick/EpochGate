"""给调用方使用的控制面接口。

这里不暴露 SQLite 事务、内部锁或容器里的发布用户。
文件发布仍走 ``publish_file``，资格判断仍走 RunLedger。
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from epochgate_runtime.files import publish_file
from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sink import EffectSink


@dataclass(frozen=True)
class Attempt:
    run_id: str
    attempt_id: str
    resource: str
    fencing_token: int
    state_version: int
    idempotency_key: str


class Runtime:
    """打开一本本地账本，申请资格，并查询已经发生的副作用。"""

    def __init__(self, ledger: RunLedger, sink: EffectSink, workspace: Path, data_dir: Path) -> None:
        self.ledger = ledger
        self.sink = sink
        self.workspace = workspace
        self.data_dir = data_dir

    @classmethod
    def open(
        cls,
        database: str | Path,
        *,
        sink: str | Path | None = None,
        workspace: str | Path | None = None,
        data_dir: str | Path | None = None,
    ) -> Runtime:
        database_path = Path(database)
        root = Path(data_dir) if data_dir is not None else database_path.parent
        root.mkdir(parents=True, exist_ok=True)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        sink_path = Path(sink) if sink is not None else root / "sink.sqlite"
        sink_path.parent.mkdir(parents=True, exist_ok=True)
        work = Path(workspace) if workspace is not None else root / "workspace"
        work.mkdir(parents=True, exist_ok=True)
        return cls(RunLedger(database_path), EffectSink(sink_path), work, root)

    def close(self) -> None:
        self.ledger.close()
        self.sink.close()

    def reserve(self, run_id: str, resource: str) -> Attempt:
        """申请当前资源的执行资格。同一 Run 上的下一次申请会作废旧 token。"""
        try:
            state = self.ledger.get_run(run_id)
        except KeyError:
            self.ledger.create_run(run_id)
            self.ledger.append_event(run_id, "run_created", {"resource": resource})
            state = self.ledger.advance(run_id, "running")
        else:
            if state.phase == "created":
                state = self.ledger.advance(run_id, "running")
            elif state.phase == "running" and state.eligible:
                state = self.ledger.supersede(run_id)
            else:
                raise RuntimeError(f"run {run_id} is {state.phase} and cannot take a new attempt")
        attempt_id = f"{run_id}-attempt-{state.fencing_token}"
        self.ledger.open_attempt(run_id, attempt_id, "sdk")
        key = f"{run_id}:{attempt_id}"
        reserved = self.ledger.record_intent(
            run_id,
            key,
            state.fencing_token,
            state.state_version,
            effect_id=key,
            arguments_hash=resource,
            tool_name="reserve",
        )
        if reserved.get("status") != "reserved":
            raise RuntimeError(f"reserve failed: {reserved}")
        return Attempt(run_id, attempt_id, resource, state.fencing_token, state.state_version, key)

    def publish(self, attempt: Attempt, payload: bytes) -> dict[str, Any]:
        """把字节交给受控发布。过期 token 不会写入目标文件。"""
        run_workspace = self.workspace / attempt.run_id
        staging = run_workspace / ".eg-staging" / attempt.attempt_id
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(payload)
        relative = Path(attempt.resource).name
        return publish_file(
            self.ledger,
            workspace=run_workspace,
            run_id=attempt.run_id,
            idempotency_key=attempt.idempotency_key,
            fencing_token=attempt.fencing_token,
            relative_path=relative,
            staging_path=staging,
        )

    def inspect(self, run_id: str) -> dict[str, Any]:
        state = self.ledger.get_run(run_id)
        return {
            "run_id": run_id,
            "phase": state.phase,
            "eligible": state.eligible,
            "fencing_token": state.fencing_token,
            "state_version": state.state_version,
            "events": self.ledger.events(run_id),
            "effects": self.ledger.effects_for_run(run_id),
        }

    def list_runs(self) -> list[dict[str, Any]]:
        return self.ledger.list_runs()

    def verify(self, run_id: str) -> dict[str, Any]:
        effects = self.ledger.effects_for_run(run_id)
        verified = [item for item in effects if item["phase"] == "completed" and item["receipt"]]
        return {
            "run_id": run_id,
            "verified": bool(verified),
            "receipts": [item["receipt"] for item in verified],
            "effects": effects,
        }


_LABELS = {
    "run_created": "建立 Run 与执行上下文",
    "attempt_opened": "取得当前 fencing token 的执行资格",
    "intent_recorded": "记录这次副作用的意图",
    "eligibility_superseded": "旧 token 作废，epoch 前进，Run 仍可接受新 token",
    "result_withheld": "旧 token 不能发布副作用",
    "commit_rejected": "旧 token 不能提交",
    "file_published": "新 token 通过摘要检查并发布",
    "effect_committed": "副作用已写入接收器",
    "reconciled_from_sink": "控制面重新打开后，按接收器补上 receipt",
    "eligibility_revoked": "资格撤销",
    "not_applied": "可控接收器没有这一行，效果未生效",
    "outcome_unknown": "无法证明效果是否生效",
}


def timeline_lines(snapshot: dict[str, Any]) -> list[str]:
    lines = [
        f"Run {snapshot['run_id']}",
        f"phase {snapshot['phase']}  token {snapshot['fencing_token']}  eligible {str(snapshot['eligible']).lower()}",
        "",
    ]
    for index, event in enumerate(snapshot["events"], start=1):
        label = _LABELS.get(event["event_type"], event["event_type"])
        lines.append(f"{index}. {event['event_type']}")
        lines.append(f"   {label}")
    return lines


def write_report(directory: Path, payload: dict[str, Any]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    html_path = directory / "report.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    events = payload.get("events") or []
    rows = []
    for index, event in enumerate(events, start=1):
        label = _LABELS.get(event["event_type"], event["event_type"])
        rows.append(
            "<li><strong>"
            + html.escape(f"{index}. {event['event_type']}")
            + "</strong><br>"
            + html.escape(label)
            + "</li>"
        )
    document = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>Run {html.escape(str(payload.get('run_id', '')))}</title>"
        "<style>body{font-family:Segoe UI,sans-serif;max-width:42rem;margin:2rem auto;line-height:1.5}"
        "code{background:#f4f4f4;padding:0 .2rem}</style></head><body>"
        f"<h1>Run {html.escape(str(payload.get('run_id', '')))}</h1>"
        f"<p>phase <code>{html.escape(str(payload.get('phase', '')))}</code> "
        f"token <code>{html.escape(str(payload.get('fencing_token', '')))}</code></p>"
        "<ol>"
        + "".join(rows)
        + "</ol></body></html>\n"
    )
    html_path.write_text(document, encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}
