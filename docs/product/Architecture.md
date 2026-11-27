# Architecture

The browser calls FastAPI on the local control plane. The workspace preflight validates the local prerequisites. A launch request is claimed in a separate SQLite database before the session store starts DSH and the sandbox. Replays with the same key return the original Run pointer. A pending claim after an ambiguous crash stays pending, so the browser can inspect the ledger without repeating the launch.

RunLedger is authoritative for attempts, eligibility, state versions, events and effect state. The target sink is authoritative for its accepted row. File receipts use evidence from the recorded publish readback. A reconciliation decision is an event and retains its original status. An unavailable sink remains unavailable in the read model.

Research opens the frozen pilot archive and target SQLite files read-only. Re-Audit executes the same independent scorer used by the CLI. The application stores audit records in the active data directory. Demo Mode uses its own data directory for runs, the sink and audit records.

SQLite migrations are additive and run when opening the ledger. The launch database creates its request table on first use. Preserve the data directory across upgrades and back up both databases before upgrading.
