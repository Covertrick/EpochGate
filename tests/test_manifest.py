"""manifest 能锁定一个 cell，并且不写入密钥。"""

from __future__ import annotations

import json
import os

import pytest

from epochgate.experiment import reproduce_cell, run_experiment, trace_signature
from epochgate.manifest import cell_id


@pytest.mark.asyncio
async def test_reproduce_one_policy_cell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EPOCHGATE_API_KEY", "should-not-be-stored")
    out = tmp_path / "run"
    await run_experiment(seed=4, out_dir=out, root=tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    blob = json.dumps(manifest)
    assert "should-not-be-stored" not in blob
    assert manifest["env"]["EPOCHGATE_API_KEY"] == "set"
    identifier = cell_id("b0", 4, None)
    assert any(cell["cell_id"] == identifier for cell in manifest["cells"])
    replay = tmp_path / "replay"
    await reproduce_cell(manifest_path=out / "manifest.json", cell=identifier, out_dir=replay)
    original = (out / "b0-seed-4.jsonl").read_text(encoding="utf-8")
    copied = (replay / f"{identifier}.jsonl").read_text(encoding="utf-8")

    assert trace_signature(out / "b0-seed-4.jsonl") == trace_signature(replay / f"{identifier}.jsonl")


@pytest.mark.asyncio
async def test_chaos_cell_uses_the_scenario_seed(tmp_path) -> None:
    scenario = tmp_path / "short.yaml"
    scenario.write_text(
        "faults:\n  - kind: crash\n    probability: 1\n    modes: [http_500]\n",
        encoding="utf-8",
    )
    out = tmp_path / "chaos"
    monkey_env = os.environ.pop("EPOCHGATE_API_KEY", None)
    try:
        await run_experiment(seed=9, out_dir=out, scenario=scenario, root=tmp_path)
    finally:
        if monkey_env is not None:
            os.environ["EPOCHGATE_API_KEY"] = monkey_env
    text = (out / "chaos-seed-9.jsonl").read_text(encoding="utf-8")
    assert "fault_injected" in text
    assert "crash" in text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["env"]["EPOCHGATE_API_KEY"] == "unset"
