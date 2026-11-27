# Expanded offline comparison protocol

Use forty task instances, four fixture families crossed with ten fixed schedules. The fulfillment family is held out by designation. All five policies receive the same proposal, budget and fault schedule within a task. A family/schedule pair contributes one observation per policy. These are designed simulation instances with a shared implementation, not a random sample of real Agent tasks.

The strong semantic baseline checks business predicates and propagates invalidation through the DAG. When a predicate fails, it revalidates the entire unfinished plan. Partial repair restricts this work to the affected subgraph. All policies use the same transactional receiver checks. Fixed recovery stops on ambiguous dispatch. The full policy may query the receiver if it has that capability.

Record task completion, terminal state, valid and invalid effects, duplicate target rows, dispatch and query attempts, elapsed wall time, recovery time, plan retention and replan counts. Offline arms make no model calls and report null for measured model tokens. The separately recorded live coding task measures provider-reported tokens and actual Docker test time. Neither experiment estimates population risk.
