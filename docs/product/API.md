# API

The running app provides OpenAPI at /docs. Protected APIs expect the local bearer token. The browser obtains it from the loopback /session-token endpoint.

| Route | Contract |
| --- | --- |
| POST /sessions | Optional Idempotency-Key. Same key and body return the registered Run pointer. Conflicts and ambiguous pending claims return 409. |
| POST /agent/tasks, POST /agent/model-tasks | Submit a durable plan or request model planning. An optional Idempotency-Key reuses the original submission. An unconfirmed model call returns 409 on replay until a Run is available. |
| GET /agent/tasks/{id}, POST /agent/tasks/{id}/cancel | Read the plan, decisions and receipts, or revoke an active or queued task. Confirmed effects remain recorded. |
| GET /observatory/launch/{key} | Registration state and Run pointer when available. |
| GET /runs/{id}?figures=false | Run, event, attempt, effect and receipt snapshots. |
| GET /observatory/runs/export | CSV run summary, with optional comma-delimited ids. |
| GET /observatory/search | Identifier query q and optional UTC dates start and end. |
| GET /observatory/research/cells/{id} | Ordered trace with linked target rows and matching receipt responses. |
| POST /observatory/research/audit | Invoke the independent evaluator and persist the result. |
| GET /observatory/research/audits | Latest local audit records. |
| GET /observatory/research/export/{kind} | json, csv, md and zip. ZIP can select a cell_id. |
| GET /observatory/system | Runtime, SQLite, Worker and Docker diagnostics. Durable Agent counts show queued tasks, active leases and expired leases awaiting takeover. |

Use the Run phase, execution result, receipt verification and recovery events independently. Unavailable ledger reads return 503. A report with an unknown effect still carries the original unknown state.
