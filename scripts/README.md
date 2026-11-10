# 脚本

命令仍从 `scripts/` 根目录运行。子目录会改掉各脚本里的仓库根路径，所以这里只按版本归类，不搬文件。

## 现行运行时

- `runtime_dsh_probe.py`：宿主机 DSH 探针
- `runtime_coding_run.py`：容器编码 Run
- `score_m0_linux.py`：Linux 评分
- `audit_gold_leakage.py`：容器泄漏审计

## v1 阶段

- `run_experiment_matrix.py`、`recompute_phase1_summary.py`
- `run_task_validity.py`、`run_concurrency_baseline.py`
- `run_stage_e.py`、`analyze_stage_e.py`、`audit_stage_e.py`
- `run_stage_f.py`、`analyze_stage_f.py`、`audit_stage_f_evidence.py`
- `check_deepseek_api.py`、`run_dsh_sdk_session.py`、`run_clean_loop.py`

## v2

- `run_v21_pilot.py`、`run_v21_api.py`、`analyze_v21.py`、`analyze_v21_api.py`
- `audit_v18_bystander.py`、`plot_v21_figures.py`、`close_v21.py`、`reproduce_v21.ps1`
- `run_v22.py`、`analyze_v22.py`、`audit_v40_p95.py`
- `run_followup_matrix.py`、`run_followup_runtime.py`

## v5

- `run_v50_l0.py`、`run_v50_ds.py`、`run_v50_vllm_compat.py`、`analyze_v50.py`
- `run_v51_ds.py`、`run_v51_phase_c.py`、`run_v51_vllm_groups.py`、`analyze_v51.py`、`audit_v51_ds.py`

## UCR 与 RRM

- `run_ucr_matrix.py`、`run_ucr_runtime.py`、`diagnose_ucr.py`
- `check_ucr2_generator.py`、`fit_ucr2.py`、`run_ucr2_matrix.py`
- `validate_rrm_protocol.py`、`run_rrm_path_check.py`

## EG2

- `eg2_check_env.py`、`eg2_screen_cases.py`、`screen_eg2_cases.py`
- `eg2_run_clean.py`、`eg2_run_fork.py`、`eg2_score.py`、`eg2_analyze.py`、`eg2_reproduce.py`
