"""P1.1 + P1.2：独立定时 ScenarioDriver 与统一行结构确定性时序验收测试。

覆盖 8 项必须测试的不变量：
1. 外部变化发生在模型生成期间；
2. A/B 收到相同的环境事件历史（独立副本重放同一 ScenarioSpec）；
3. A/B 的资格撤销均在线性化点生效（eligibility_revoked_at）；
4. A/B 的旧 attempt 都不能提交；
5. 恢复事件不会因为某个 attempt 结束而提前触发（由固定 offset_s 决定）；
6. 新 attempt 必须在新版本观测基础上生成；
7. 可恢复任务最终只产生一次副作用（side_effect_once=True）；
8. 同一剧本多次重放，事件序列和结果一致；且 A/B 输出相同的 JSON 键集合。
"""

from __future__ import annotations

import pytest

from epochgate.scenario_driver import make_scenario_spec
from epochgate.stage_e_runner import run_timed_deterministic_arm


@pytest.mark.asyncio
async def test_timed_recoverable_ab_invariants_and_schema_parity() -> None:
    # first_shot: 8 tokens * 0.04s = ~0.32s total stream duration for Arm A
    # t_change = 0.08s (fires during generation, around token 2-3)
    # t_restore = 0.24s (fires AFTER Arm B stops at ~0.08s, while Arm A is still streaming)
    spec = make_scenario_spec(
        scenario_id="det-rec-timed",
        task_kind="recoverable",
        t_change_s=0.08,
        t_restore_s=0.24,
        initial_stock=1,
    )
    rows_a = await run_timed_deterministic_arm(
        spec=spec,
        arm="A",
        seed=951,
        first_shot_tokens=8,
        second_shot_tokens=3,
        token_interval_s=0.04,
    )
    rows_b = await run_timed_deterministic_arm(
        spec=spec,
        arm="B",
        seed=951,
        first_shot_tokens=8,
        second_shot_tokens=3,
        token_interval_s=0.04,
    )
    assert len(rows_a) == 1 and len(rows_b) == 1
    ra, rb = rows_a[0], rows_b[0]

    # Unified JSON schema parity between Arm A and Arm B
    assert set(ra.keys()) == set(rb.keys())

    # 1. Exogenous change happened during generation
    assert ra["trigger_outcome"] == "triggered"
    assert rb["trigger_outcome"] == "triggered"
    assert ra["pre_revocation_delta_events"] >= 1
    assert rb["pre_revocation_delta_events"] >= 1

    # 2. A and B received the exact same exogenous event history (labels, deltas, versions)
    hist_a = [(c["label"], c["delta"], c["source_version"], c["stock_after"]) for c in ra["exogenous_history"]]
    hist_b = [(c["label"], c["delta"], c["source_version"], c["stock_after"]) for c in rb["exogenous_history"]]
    assert hist_a == [("t_change", -1, "v2", 0), ("t_restore", 1, "v3", 1)]
    assert hist_b == hist_a

    # 3. Both arms linearized eligibility revocation
    assert ra["eligibility_revoked_at"] is not None
    assert rb["eligibility_revoked_at"] is not None
    assert ra["cancel_requested_at"] is not None
    assert rb["cancel_requested_at"] is not None

    # 4. Old attempt never committed in either arm; B has 0 post-revocation output while A > 0
    assert ra["old_attempt_committed"] is False
    assert rb["old_attempt_committed"] is False
    assert ra["post_revocation_observed_text_tokens"] > 0
    assert rb["post_revocation_observed_text_tokens"] == 0
    assert rb["post_revocation_delta_events"] == 0
    assert rb["post_revocation_tool_call_delta_events"] == 0

    # 5. Restore event did NOT fire early when B stopped its stream early
    # B released its old slot before t_restore, then waited for t_restore
    assert rb["old_attempt_slot_release_at"] < rb["stock_restored_at"]
    actual_restore_offset_b = rb["exogenous_history"][1]["actual_offset_s"]
    assert actual_restore_offset_b >= 0.22  # respected scheduled 0.24s offset

    # 6 & 7. New attempt committed exactly once on restored stock; final stock = 0
    assert ra["action_commits"] == 1
    assert rb["action_commits"] == 1
    assert ra["side_effect_once"] is True
    assert rb["side_effect_once"] is True
    assert ra["final_stock"] == 0
    assert rb["final_stock"] == 0
    assert ra["correctness_ok"] is True
    assert rb["correctness_ok"] is True
    assert ra["replan_started_at"] is not None and ra["replan_committed_at"] is not None
    assert rb["replan_started_at"] is not None and rb["replan_committed_at"] is not None

    # B released the old attempt slot earlier than A
    assert rb["slot_release_latency_s"] < ra["slot_release_latency_s"]


@pytest.mark.asyncio
async def test_timed_invalidate_and_replay_determinism() -> None:
    spec = make_scenario_spec(
        scenario_id="det-inv-timed",
        task_kind="invalidate",
        t_change_s=0.06,
        initial_stock=1,
    )
    r1 = (await run_timed_deterministic_arm(spec=spec, arm="B", seed=960, first_shot_tokens=6, token_interval_s=0.03))[0]
    r2 = (await run_timed_deterministic_arm(spec=spec, arm="B", seed=960, first_shot_tokens=6, token_interval_s=0.03))[0]

    # 8. Replay consistency
    for key in (
        "trigger_outcome",
        "action_commits",
        "old_attempt_committed",
        "final_stock",
        "post_revocation_observed_text_tokens",
        "correctness_ok",
        "attempt_ids",
    ):
        assert r1[key] == r2[key], key
    assert r1["correctness_ok"] is True
    assert r1["action_commits"] == 0
    assert r1["old_attempt_committed"] is False
