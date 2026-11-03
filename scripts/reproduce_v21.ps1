# 从冻结 JSONL 重建 v2.1 主表，并重跑 4.0 审计。
# 不重新生成 results/experiments/v2_1/confirmatory/traces.jsonl。
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m uv run python scripts/analyze_v21.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m uv run python scripts/close_v21.py
exit $LASTEXITCODE
