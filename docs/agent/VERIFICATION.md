# Evidence-aware Agent verification

This page documents the original pilot. See the [expanded evaluation](EVALUATION.md) for the follow-up coding and forty-instance results.

## Reproduce the runtime

Start an isolated local instance and a Worker in two terminals. Set both commands to the same data directory.

~~~powershell
uv run --locked --package epochgate-runtime egctl serve --port 8767 --data-dir .epochgate/decision-demo
uv run --locked --package epochgate-runtime python -m epochgate_runtime.durable_worker --data-dir .epochgate/decision-demo --receiver-url http://127.0.0.1:8767
~~~

In Workspace submit a plan such as:

~~~json
{"plan":[{"step_id":"publish","effect":{"release-note":"candidate"}}],"budget":{"tools":5,"replans":2}}
~~~

The model entry accepts prompt, a list of allowed resource names, and an optional budget. It calls DeepSeek in JSON mode and submits the validated proposal. The model does not attest to receipt existence. The Worker reads the current control token from server.json when using the local HTTP receiver. Keep API and Workers pointed at the same data root.

The durability smoke submitted one write, ran the Worker with --fault-after-commit, observed exit code 86, stopped the API, and started it on the same data root. A new Worker claimed epoch 2 and recorded QUERY_EFFECT and confirmation. The target table retained one receipt. Regression tests also exercise competing claims, stale fencing, replayed launch keys and non-queryable UNKNOWN.

## Pilot

Ten fixed task instances crossed eight arms. The fulfillment family was designated as held out in the protocol. The source and per-arm raw records are included. The offline scorer derives outcomes and checks commit predicates and receipt digests independently of the policy implementation.

| Arm | Valid completion | Unknown | Invalid effects | Duplicates | Replans |
| --- | ---: | ---: | ---: | ---: | ---: |
| Always Continue | 3/10 | 4 | 0 | 0 | 0 |
| Full + fixed | 6/10 | 4 | 0 | 0 | 6 |
| Version + fixed | 6/10 | 4 | 0 | 0 | 3 |
| Predicate + fixed | 3/10 | 4 | 3 | 0 | 3 |
| A1 Partial + fixed | 6/10 | 4 | 0 | 0 | 3 |
| A2 Full + evidence | 9/10 | 1 | 0 | 0 | 9 |
| A3 Partial + evidence | 9/10 | 1 | 0 | 0 | 6 |
| A4 Partial + evidence + cost | 9/10 | 1 | 0 | 0 | 6 |

A1 and A2 isolate different changes. A3 preserved A2's nine completions with three fewer recorded repairs. A4 tied A3. The predicate baseline checks a step's own predicate and does not propagate dependency invalidation. The receiver evaluates its own declared conditions, so it cannot infer unstated downstream semantics. The fixture design explicitly tests this distinction.

Three separate DeepSeek Flash proposals completed against the controlled receiver. Measured token totals were 196 for code content, 186 for file content, and 180 for fulfillment. The final raw ledger replayed the original model proposals with their measured usage, preserving one actual request per family. These runs do not enter the deterministic ablation table.

- [Protocol](../../experiments/agent_decision/protocol.md)
- [Frozen raw events](results/frozen-pilot.json) · [Independent frozen report](results/frozen-audit.json)
- [Live-proposal raw events](results/live-pilot.json) · [Independent live report](results/live-audit.json)
- [Evidence hashes](results/SHA256SUMS)
- [Decision Trace screenshot](decision-trace.png)

Reproduce the frozen comparison with:

~~~powershell
uv run --locked --package epochgate-runtime python experiments/agent_decision/run.py .epochgate/decision-pilot
uv run --locked --package epochgate-runtime python experiments/agent_decision/audit.py .epochgate/decision-pilot/frozen-pilot.json
~~~

## Gate scope

The durable queue and managed receiver share a SQLite WAL authority. The HTTP service exposes that receiver across processes. It does not model separate receiver and authority databases. Receiver epoch and proposal checks, versions, predicates, writes and receipts are in one local transaction. The design makes no power-loss or arbitrary HTTP exactly-once claim.

DAG repair uses predeclared typed replacements and preserves committed records. The model proposes new plans, while unsupported repairs stop. A WAIT decision seals an unresolved task for review. The token meter records completed provider usage, then enforces the execution budget. Token estimation, delayed scheduling and model-generated replacement of an existing DAG remain open.

The experiment gate remains open for sandboxed patch execution and tests, a separate real filesystem publisher, and a larger formal live-model ablation. Managed code and file content exercises only verified receiver writes. Existing DSH/Docker sessions use their existing path. The host session prerequisite check reports Docker unavailable. The evidence supports conclusions about these fixtures.
