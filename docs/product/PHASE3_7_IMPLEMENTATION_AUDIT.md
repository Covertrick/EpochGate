# P3–P7 implementation audit

Baseline 905baeb. The FastAPI console serves a hash-routed vanilla JS application with overview, workspace, run details, runs, research and system. Existing cards, tables, dialogs, badges, tabs and toast components are reusable.

| Area | Existing contract | Gap |
| --- | --- | --- |
| Runtime | /runs, /runs/{id}, /sessions, /jobs, /actions, /effects | Lookup and comparison, launch replay protection, live job recovery |
| Research | /observatory/research, /cells, /artifacts, /audit under the research prefix | Trace-to-violation provenance, audit history, exports, filters |
| System | /observatory/system and /log | Timed checks, storage size, scoped diagnostics, path disclosure |
| QA | Runtime pytest suite and scripts/observatory_browser_smoke.py | Repeat launch, research export, comparison and viewport coverage |

RunLedger owns runs, attempts, effects, leases, cancellations and ordered runtime events. The independent sink owns accepted effects. Sessions and job tracking are orchestration state. Proposal, Admission and Recovery are derived from typed events. Receipt verification uses recorded publish readback or sink lookup.

The pilot-01 manifest defines the frozen cells and source hashes. report.json contains frozen scoring and strategy counts. sha256.json inventories original files. Each cell has trace.json and target.sqlite. File cells also preserve snapshots and final bytes. The existing audit command independently reconstructs the report from those files. The study has two execution boundaries and three arms, including fenced SQLite. Strategy membership is derived from protocol schedules and can overlap.

Implement additive read models and routes in observatory.py and httpapp.py, enhance the existing console and browser smoke, and document release gates and deployment. Keep old API fields and frozen history intact. Deployment assets were already present as untracked work. Docker and live-model verification require working host dependencies.
