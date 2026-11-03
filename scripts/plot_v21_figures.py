"""从冻结 JSON 画三张 SVG。不读取确认性原始行以外的新实验。"""

from __future__ import annotations

import json
from pathlib import Path

SUMMARY = Path("results/experiments/v2_1/reports/v21_summary.json")
TIMELINE = Path("results/experiments/v4_0/figures/timeline_seed5005.json")
OUT = Path("results/experiments/v4_0/figures")


def _esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _svg(width: int, height: int, body: list[str]) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            *body,
            "</svg>",
        ]
    )


def _bar_chart(rows: list[dict], title: str, ylabel: str, path: Path) -> None:
    width, height = 980, 460
    left, right, top, bottom = 70, 20, 48, 110
    plot_w = width - left - right
    plot_h = height - top - bottom
    lows = [row["ci_low"] for row in rows]
    highs = [row["ci_high"] for row in rows]
    y_min = min(0.0, min(lows))
    y_max = max(0.0, max(highs))
    span = (y_max - y_min) or 1.0
    pad = span * 0.12
    y_min -= pad
    y_max += pad

    def y_of(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_h

    parts = [
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="16">{_esc(title)}</text>',
        f'<text x="18" y="{top + plot_h / 2}" text-anchor="middle" font-family="sans-serif" font-size="12" transform="rotate(-90 18 {top + plot_h / 2})">{_esc(ylabel)}</text>',
        f'<line x1="{left}" y1="{y_of(0)}" x2="{left + plot_w}" y2="{y_of(0)}" stroke="#222" stroke-width="1"/>',
    ]
    for tick in range(5):
        value = y_min + (y_max - y_min) * tick / 4
        yy = y_of(value)
        parts.append(f'<line x1="{left - 4}" y1="{yy:.1f}" x2="{left}" y2="{yy:.1f}" stroke="#222"/>')
        parts.append(
            f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="11">{value:.1f}</text>'
        )
    slot = plot_w / len(rows)
    for index, row in enumerate(rows):
        cx = left + slot * (index + 0.5)
        color = "#1f4e79" if row["delta_mean"] >= 0 else "#9c2f2f"
        y_point = y_of(row["delta_mean"])
        y_lo = y_of(row["ci_low"])
        y_hi = y_of(row["ci_high"])
        parts.append(f'<line x1="{cx:.1f}" y1="{y_hi:.1f}" x2="{cx:.1f}" y2="{y_lo:.1f}" stroke="{color}" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx - 5:.1f}" y1="{y_hi:.1f}" x2="{cx + 5:.1f}" y2="{y_hi:.1f}" stroke="{color}"/>')
        parts.append(f'<line x1="{cx - 5:.1f}" y1="{y_lo:.1f}" x2="{cx + 5:.1f}" y2="{y_lo:.1f}" stroke="{color}"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{y_point:.1f}" r="3.5" fill="{color}"/>')
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 78}" text-anchor="end" font-family="sans-serif" font-size="11" transform="rotate(-55 {cx:.1f} {height - 78})">{_esc(row["label"])}</text>'
        )
    path.write_text(_svg(width, height, parts), encoding="utf-8")


def figure_harmful(summary: dict) -> None:
    rows = []
    for row in summary["harmful_w95"]:
        rows.append(
            {
                "label": f'{row["load"]} {row["scheduler"]} ρ{row["rho"]:g}',
                "delta_mean": row["delta_mean"],
                "ci_low": row["ci_low"],
                "ci_high": row["ci_high"],
            }
        )
    _bar_chart(
        rows,
        "有害路径：旁观者 P95 等待差 W(G0)−W(G2)",
        "秒",
        OUT / "fig1_harmful_w95.svg",
    )


def figure_harmless(summary: dict) -> None:
    order = {"fcfs": 0, "runtime-plas": 1, "srpt": 2}
    rows = sorted(summary["harmless_w95"], key=lambda row: (order[row["scheduler"]], row["rho"]))
    labeled = [
        {
            "label": f'{row["scheduler"]} ρ{row["rho"]:g}',
            "delta_mean": row["delta_mean"],
            "ci_low": row["ci_low"],
            "ci_high": row["ci_high"],
        }
        for row in rows
    ]
    _bar_chart(
        labeled,
        "无害路径：旁观者 P95 等待差 W(G1)−W(G2)",
        "秒",
        OUT / "fig2_harmless_rho.svg",
    )


def figure_timeline(payload: dict) -> None:
    width, height = 980, 520
    left, top = 150, 46
    plot_w = 790
    end = 32.0
    lanes = ["target", "successor", "wf-b00", "wf-b01", "wf-b02"]
    lane_name = {"target": "焦点任务", "successor": "successor", "wf-b00": "旁观者 b00", "wf-b01": "旁观者 b01", "wf-b02": "旁观者 b02"}
    colors = {"target": "#1f4e79", "successor": "#c47b2b", "bystander": "#5b8f6a"}
    policies = ["G0", "G2", "G2-delayed-release"]
    block_h = 14
    gap = 6
    group_h = len(lanes) * (block_h + gap) + 28
    parts = [
        '<text x="490" y="26" text-anchor="middle" font-family="sans-serif" font-size="16">种子 5005：G0、G2 与延迟释放的占槽时间线</text>',
        '<text x="490" y="500" text-anchor="middle" font-family="sans-serif" font-size="12">时间（秒）。竖线是 3.2 秒的外部事件。这是一条示例 trace，不是 50 条的均值。</text>',
    ]
    for index, policy in enumerate(policies):
        origin = top + index * group_h
        parts.append(f'<text x="16" y="{origin + 12}" font-family="sans-serif" font-size="13">{_esc(policy)}</text>')
        for lane_index, lane in enumerate(lanes):
            yy = origin + 22 + lane_index * (block_h + gap)
            parts.append(
                f'<text x="{left - 8}" y="{yy + 11}" text-anchor="end" font-family="sans-serif" font-size="11">{_esc(lane_name[lane])}</text>'
            )
            parts.append(f'<line x1="{left}" y1="{yy + block_h / 2}" x2="{left + plot_w}" y2="{yy + block_h / 2}" stroke="#eeeeee"/>')
        event_x = left + 3.2 / end * plot_w
        parts.append(f'<line x1="{event_x:.1f}" y1="{origin + 18}" x2="{event_x:.1f}" y2="{origin + group_h - 24}" stroke="#888" stroke-dasharray="3 3"/>')
        for item in payload["arms"][policy]["attempts"]:
            start = item["service_started_at"]
            stop = item["slot_released_at"]
            if start is None or stop is None:
                continue
            lane = item["workflow_id"] if item["role"] == "bystander" else item["role"]
            if lane not in lanes:
                continue
            lane_index = lanes.index(lane)
            yy = origin + 22 + lane_index * (block_h + gap)
            x1 = left + max(0.0, start) / end * plot_w
            x2 = left + min(end, stop) / end * plot_w
            parts.append(
                f'<rect x="{x1:.1f}" y="{yy:.1f}" width="{max(1.0, x2 - x1):.1f}" height="{block_h}" fill="{colors[item["role"]]}" opacity="0.9"/>'
            )
    for tick in range(0, 33, 4):
        xx = left + tick / end * plot_w
        parts.append(f'<text x="{xx:.1f}" y="484" text-anchor="middle" font-family="sans-serif" font-size="11">{tick}</text>')
    OUT.joinpath("fig3_timeline_seed5005.svg").write_text(_svg(width, height, parts), encoding="utf-8")


def main() -> None:
    """写出三张论文图。

    输入：无。读取汇总 JSON 和时间线 JSON。
    输出：三份 SVG。
    副作用：覆盖图文件。
    失败：源文件缺失时抛出 ``FileNotFoundError``。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    timeline = json.loads(TIMELINE.read_text(encoding="utf-8"))
    figure_harmful(summary)
    figure_harmless(summary)
    figure_timeline(timeline)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
