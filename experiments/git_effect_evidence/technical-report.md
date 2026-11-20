# When a Git Retry Reinstates a Withdrawn Update

*A technical report on effect attribution and external workflow validation*

## Abstract

After a response failure, a Git client may observe the same branch tip in two histories: the original update never reached the target, or the update succeeded and another writer subsequently reset the branch. We used isolated repositories and the Git command-line client to compare current references, reflog entries, operation identifiers, and a reference stored in the same update transaction. Across 24 controlled cases, comparing the current tip to the desired commit produced six false attributions and missed six earlier successful updates. A genuine retry changed the reference in both histories. Ordinary push also reinstated the update after a reset; a compensating commit instead prevented the same ordinary push. We screened two independently maintained clients for a retry path coupled to an obligation to preserve an independent rollback. Neither passed that gate. The results bound what a client can infer and explain why a Git-level counterexample does not establish a workflow defect.

## 1. The recovery decision

Let A denote the old tip and B the proposed replacement, with B a descendant of A. An update is *observed* if the client learns its target outcome. The experimental controller retains the actual target effects but withholds the original response from the recovery analysis. Two histories expose the same current ref:

| History | Target-side events | Current tip |
| --- | --- | --- |
| No dispatch | The proposal exists and no target update occurs. | A |
| Reset after effect | A→B succeeds. A different writer then resets B→A. | A |

A client that can only observe A cannot decide which history occurred. Sending B again may complete unfinished work in the first history and reinstate a withdrawn change in the second. The latter is a violation only when the actual workflow treats the reset as a withdrawal that subsequent automatic actions must respect. A desired-state reconciler can legitimately reapply B while B remains its configured desired state.

We separately test whether another writer's A→B can be mistaken for the original operation. In this pair, a target ref at B correctly describes the current state but says nothing about which writer produced it. A matching ref value and a historical delivery acknowledgment are distinct observations.

## 2. Experimental design

The unit is an isolated bare Git repository on a local machine. The command-line client creates commits, uses Git's conditional ref update with an expected old OID, and records the target result. The 24-case matrix crosses four histories, three reflog retention settings, and two marker settings. Retention covers available, disabled, and explicitly expired logs. The marker is an operation-specific ref written in the same reference transaction as the branch. It tests an existing Git facility with cooperative writers, not a new transaction protocol.

Recovery is evaluated against four local evidence sources: branch-tip equality, matching old/new reflog OIDs, a matching reflog operation identifier, and the operation-specific ref. The transport probe uses Git's local file transport and reads the branch and marker through the ref advertisement. The same repository is then queried with the marker namespace hidden. Missing positive evidence yields unknown for attribution-based rules. The design does not infer absence of execution from a hidden marker.

The matrix measures attribution of past effects. A second two-case experiment executes the actual conditional retry against repositories at tip A. A third three-case experiment sends an ordinary push from an isolated sender to the target. It distinguishes a branch reset from a compensating commit. The latter control creates a new commit R on top of B and attempts to push the old B. Commit topology is the manipulated factor; this does not emulate an application deployment.

[Matrix protocol](PROTOCOL.md) · [External screening protocol](WORKFLOW_PROTOCOL.md)

## 3. What the target observations establish

| Evidence or action | Result on the tested histories |
| --- | --- |
| Tip equals B | Six false attributions to the original operation and six missed prior original updates across the 24 fixture cases. |
| Old/new OIDs in a retained reflog | Sees the transition when retained, but attributes the other writer's same transition to the original operation. |
| Operation ID in a retained reflog | Confirms the four original operations under the retained-log settings, with zero false confirmations. |
| An exposed operation ref in enabled cases | Confirms six original operations in the twelve marker-enabled cases, with zero false confirmations. |
| The same ref hidden from transport | The client can still read the branch tip, while the operation ref is unavailable through this view. |
| Real conditional retry with the tip back at A | Succeeds in both pair members, changing the tip to B even after the independent reset. |
| Real ordinary push after a compensating commit | Rejects the tested push to old B and preserves R. The corresponding ordinary push succeeds after the reset to A. |

The first five rows use the same underlying matrix, so the counts do not represent independent observations or incidence estimates. The final two rows exercise commands, rather than infer behavior solely from a scoring rule. The ordinary push result follows from the ancestor relation, which is necessary to interpret its difference from a branch reset.

The marker experiment does not establish stability under crash, concurrent ref readers, metadata deletion, or an uncooperative writer. Retention and read privileges can change the inference a recovery client is justified in making even when target-side history is unchanged.

[24-case data](../../results/experiments/git-effect-evidence/pilot-01/evidence.json) · [Executed conditional retry](../../results/experiments/git-effect-evidence/pilot-01/retry-report.json) · [Ordinary push controls](../../results/experiments/git-effect-evidence/workflow-screen-01/transport-control.json)

## 4. External screening and negative result

We required a candidate to have an actual ambiguous-push recovery path and a documented workflow rule requiring an independent reset to remain effective. Both requirements matter: a replay mechanism without a relevant obligation establishes feasibility, while an obligation without a reachable retry path does not produce the hypothesized failure.

**Flux Image Automation.** At revision `81b6a10eeca53a9bad1de8b739ae130accc30475`, the optional retry loop refreshes the remote. It can recognize a prior push when the refreshed tip matches the attempted revision. Otherwise it reapplies image policies and recomputes a commit. Its documented operational controls include suspension and changing the desired image policy. In the reviewed scope, a ref reset alone is not specified as revoking an in-progress publish. Reapplying the configured desired state can therefore be correct. Our screening does not settle the behavior of cancellation during an active reconciliation.

**create-pull-request.** At revision `3ffccacd46053766916f087ce41d627c0b8cf2d1` (v8.1.3), the unsigned path performs one force-with-lease push and propagates a failure. Retry code for API requests does not make this a same-operation push recovery path. A separate run could have different semantics, but no externally maintained instance satisfying both criteria was identified.

Both candidates fail the predefined gate for different reasons. We stopped before external fault injection and hosted-platform experiments. The screening result applies to these versions and paths only.

[Pinned source audit](WORKFLOW_AUDIT.md)

## 5. Interpretation and limits

Git refs encode values. An operation-specific record can correlate a value transition with a request, but it cannot establish the user's rollback intent by itself. The application must supply that interpretation. A force-with-lease or compare-and-set can enforce a condition on the current ref without distinguishing an uninterrupted A from A restored after an intermediate committed action. Our local transport result also shows that writing a witness and making it usable by the recovering client are separate requirements.

The novelty claim is deliberately narrow. Git conditional updates, reflogs, ref hiding, and multi-ref atomic push are established capabilities. Research on agent recovery already studies target evidence and ambiguous actions. This report contributes a concrete tool-level contrast, an executable artifact, and a negative external validation under a defined workflow criterion. It provides no evidence about the frequency of these histories in production, hosted custom-ref support, agent behavior, or a new recovery protocol.

[Git push](https://git-scm.com/docs/git-push) · [Git reflog](https://git-scm.com/docs/git-reflog) · [Xu's recovery experiments](https://arxiv.org/html/2611.23353v1) · [Evidence-guided recovery](https://arxiv.org/abs/2610.41453)

## 6. Reproduction

The artifact provides a read-only audit of the exported matrix, execution scripts for isolated bare repositories, a source audit with pinned links, and a checksum manifest. The local reproduction records the Git version and distinguishes the fixture matrix from the actual conditional and ordinary push controls. Hosted repositories were not used. See the [artifact instructions](README.md).
