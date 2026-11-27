# Release readiness

The v1.0.0 tag is gated by G1 Function, G2 Data, G3 Reliability and G4 Demonstration. Candidate artifacts are tracked on the product-completion branch.

| Gate | Required evidence |
| --- | --- |
| G1 | Offline demonstrations, live agent session, run comparison, diagnostics and deployment smoke |
| G2 | Archive audit, integrity hashes, case-to-target links and export verification |
| G3 | Duplicate launch and reload protection, receipt mismatch and unavailable data, reconnection and crash recovery |
| G4 | Browser screenshots, reproducible demo script and seven-minute presentation |

Candidate notes include Research Lab evidence drill-downs and exports, run management and comparison, richer diagnostic checks, a local identifier search shortcut, and a separate Demo Mode ledger.

## Candidate verification

The Runtime suite recorded 105 passed and five environment-dependent skips. The original process-kill test now waits for the child to report the intended injection point before terminating it. This removes dependence on Windows process startup time. Browser checks use five viewport widths, cover keyboard search, reload and reconnection, and run against a fresh isolated ledger.

- G1 remains open. The local offline workflow and the Docker container smoke ran successfully. The host Python process received access denied from the Docker Desktop engine, so the live Agent session stopped at preflight.
- G2 passed for pilot-01. The host and the offline container independently recomputed the frozen report. API and browser checks cover linked target evidence and downloads.
- G3 coverage includes repeated launch keys, refresh, UNKNOWN, unavailable and mismatched receipts, and the four publish fault points. Pending launches require operator review after an ambiguous service interruption.
- G4 includes the seven-minute script, automated browser walkthrough, and captured [Overview](../assets/product/overview.png), [Runs](../assets/product/runs.png), [Research](../assets/product/research.png), [Audit](../assets/product/audit.png) and [mobile](../assets/product/overview-mobile.png) views.

The candidate has no formal tag. Per-component historical error timelines, virtualized long logs, and an independently measured active Attempt count are open items.
