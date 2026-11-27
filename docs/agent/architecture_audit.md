# Architecture audit

The console uses an in-process JobRunner for DSH sessions. Launch keys protect creation replays but do not persist executable jobs. ProcessScheduler records leases for local processes and does not claim jobs after an API restart. The existing Worker CLI injects faults into the controlled SQLite sink path. RunLedger and EffectSink independently own runtime events and durable receiver receipts. The Docker-backed DSH harness still owns its current session lifecycle.

Run Details reads the legacy RunLedger and projects target receipts. Formal effect-history artifacts and their scorer are independent of ordinary console runs.
