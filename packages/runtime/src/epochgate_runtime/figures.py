"""一次运行的效果图和数据表。页面和运行目录里的文件用同一份结果。"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from epochgate_runtime.api import EVENT_LABELS
from epochgate_runtime.demo import run_title
from epochgate_runtime.observations import observe_events

_NODES = (
    ("fencing", "资格"),
    ("idempotency", "幂等"),
    ("reconcile", "对账"),
    ("slot", "槽位"),
    ("container_publish", "容器发布"),
)

_PHASES = {
    "created": "已建立",
    "running": "运行中",
    "committed": "已提交",
    "cancel_requested": "已请求取消",
    "stopping": "正在停止",
    "stopped": "已停止",
    "unknown": "结果未知",
    "reconciled": "已对账",
    "revoked": "已撤销",
    "completed": "已完成",
    "started": "已开始",
    "rejected": "已拒绝",
}

_WORDS = {
    "conflict": "冲突",
    "replayed": "重放",
    "withheld": "拒绝",
    "confirmed_from_sink": "按接收器确认",
    "replay": "重放",
    "result_withheld": "拒绝发布",
    "commit_rejected": "拒绝提交",
}


def phase_label(phase: str) -> str:
    return _PHASES.get(phase, phase)


def observation_sentence(key: str, item: dict[str, Any] | None) -> str:
    if not item or item.get("status") == "absent":
        return "这一次还没有。"
    if key == "fencing":
        return f"旧令牌 {item.get('old_token')} 已作废，新令牌 {item.get('new_token')}。那次写入被拒绝。"
    if key == "idempotency":
        result = _WORDS.get(str(item.get("result")), str(item.get("result")))
        changed = "没有被换掉" if item.get("unchanged") else "已经被换掉"
        return f"第二次是{result}，文件{changed}。"
    if key == "reconcile":
        first = _WORDS.get(str(item.get("first")), str(item.get("first")))
        second = _WORDS.get(str(item.get("second")), str(item.get("second")))
        return f"先是{first}，再是{second}。"
    if key == "slot":
        if item.get("released_after_exit"):
            return "进程退出之后，槽位才释放。"
        return "已经请求取消，槽位还没有在退出之后释放。"
    if key == "container_publish":
        path = item.get("path") or ""
        if item.get("container"):
            return f"{item['container']} 里的 {path} 已核对回执。"
        return f"{path} 已核对回执。"
    return "这一次还没有。"


def observation_brief(key: str, item: dict[str, Any] | None) -> str:
    if not item or item.get("status") == "absent":
        return "这一次还没有"
    if key == "fencing":
        return "旧令牌已作废"
    if key == "idempotency":
        return "文件没有被换掉" if item.get("unchanged") else "文件已经被换掉"
    if key == "reconcile":
        return "已对上"
    if key == "slot":
        return "退出后释放" if item.get("released_after_exit") else "槽位还在"
    if key == "container_publish":
        return "已核对回执"
    return "这一次还没有"


def render_effect_svg(view: dict[str, Any]) -> str:
    observations = _observations(view)
    run_id = str(view.get("run_id") or "")
    phase = phase_label(str(view.get("phase") or ""))
    token = view.get("fencing_token", "")
    eligible = "资格还在" if view.get("eligible") else "资格已经撤销"
    title = f"{run_title(run_id)} · 阶段 {phase} · 令牌 {token} · {eligible}"
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 168" role="img">',
        f"<title>{_esc(title)}</title>",
        f'<text x="8" y="22" fill="#4a4a46" font-size="14" font-weight="300" font-family="Inter, PingFang SC, sans-serif">{_esc(title)}</text>',
    ]
    for index, (key, name) in enumerate(_NODES):
        item = observations.get(key) or {"status": "absent"}
        seen = item.get("status") == "seen"
        cx = 108 + index * 216
        if index:
            parts.append(f'<line x1="{cx - 168}" y1="78" x2="{cx - 48}" y2="78" stroke="#b7b7b0" stroke-width="2"/>')
        fill = "#98d8c8" if seen else "#ffffff"
        ink = "#4a4a46"
        sentence = observation_sentence(key, item)
        parts.append(
            f'<g class="node" tabindex="0" data-seen="{"1" if seen else "0"}" data-sentence="{_esc(sentence)}">'
        )
        parts.append(f'<circle cx="{cx}" cy="78" r="32" fill="{fill}" stroke="#b7b7b0" stroke-width="2"/>')
        parts.append(
            f'<text x="{cx}" y="83" text-anchor="middle" fill="{ink}" font-size="14" font-weight="300" font-family="Inter, PingFang SC, sans-serif">{_esc(name)}</text>'
        )
        parts.append(
            f'<text x="{cx}" y="132" text-anchor="middle" fill="#4a4a46" font-size="12" font-weight="300" font-family="Inter, PingFang SC, sans-serif">{_esc(observation_brief(key, item))}</text>'
            "</g>"
        )
    parts.append("</svg>")
    return "".join(parts)


def render_effect_summary(view: dict[str, Any]) -> str:
    effects = view.get("effects") or []
    if not effects:
        return "<p class=\"effect-line\">还没有效果。</p>"
    lines = []
    for item in effects:
        receipt = str(item.get("receipt") or "")
        path = _effect_path(receipt) or "一条效果"
        phase = phase_label(str(item.get("phase") or ""))
        lines.append(
            f"<p class=\"effect-line\" title=\"{_esc(receipt)}\">{_esc(path)} · {_esc(phase)}</p>"
        )
    return "".join(lines)


def render_table_html(view: dict[str, Any]) -> str:
    events = view.get("events") or []
    event_rows = []
    for step, event in enumerate(events, start=1):
        label = event.get("label") or EVENT_LABELS.get(event.get("event_type"), event.get("event_type"))
        seq = event.get("seq")
        seq_mark = f" <small>{_esc(seq)}</small>" if str(seq) != str(step) else ""
        event_rows.append(
            "<tr>"
            f"<td><span class=\"step\">{step}</span>{seq_mark}</td>"
            f"<td>{_esc(label)} <small>{_esc(event.get('event_type'))}</small></td>"
            f"<td>{_esc(_note(event))}</td>"
            "</tr>"
        )
    if not event_rows:
        event_rows.append('<tr><td colspan="3">还没有事件。</td></tr>')
    return (
        '<table><thead><tr><th>序号</th><th>记录</th><th>说明</th></tr></thead>'
        f"<tbody>{''.join(event_rows)}</tbody></table>"
    )


def write_run_figures(directory: Path, view: dict[str, Any]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    prepared = dict(view)
    prepared["observations"] = _observations(prepared)
    svg = render_effect_svg(prepared)
    fragment = render_table_html(prepared)
    summary = render_effect_summary(prepared)
    (directory / "effect.svg").write_text(svg, encoding="utf-8")
    page = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(prepared.get('run_id', ''))}</title>"
        "<style>body{font-family:Inter,PingFang SC,sans-serif;font-weight:300;color:#4a4a46;background:#fafaf8;margin:2rem}"
        "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid rgba(212,212,207,.3);text-align:left;padding:.6rem;vertical-align:top;font-weight:300}"
        "small{color:#b8d4e3}</style></head><body>"
        f"<h1>{_esc(prepared.get('run_id', ''))}</h1>{summary}{fragment}</body></html>\n"
    )
    (directory / "table.html").write_text(page, encoding="utf-8")
    return {"svg": svg, "html": fragment, "summary": summary}


def _observations(view: dict[str, Any]) -> dict[str, Any]:
    if view.get("observations"):
        return view["observations"]
    events = view.get("events") or []
    return observe_events(events, slot_released=_slot_released(events))


def _slot_released(events: list[dict[str, Any]]) -> bool:
    cancel = next((item for item in events if item.get("event_type") == "cancel_requested"), None)
    exited = next((item for item in events if item.get("event_type") == "process_exited"), None)
    if cancel is None or exited is None:
        return False
    return int(exited.get("seq") or 0) > int(cancel.get("seq") or 0)


def _effect_path(receipt: str) -> str:
    parts = receipt.split(":")
    if receipt.startswith("file:") and len(parts) >= 2:
        return parts[1]
    if receipt.startswith("container:") and len(parts) >= 3:
        return parts[2]
    return ""


def _note(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    parts: list[str] = []
    if payload.get("path"):
        parts.append(str(payload["path"]))
    if payload.get("status"):
        parts.append(_WORDS.get(str(payload["status"]), str(payload["status"])))
    if payload.get("decision"):
        parts.append(_WORDS.get(str(payload["decision"]), str(payload["decision"])))
    return " · ".join(parts) if parts else "—"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
