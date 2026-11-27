# Experiments

The only registered batch is pilot-01 in results/experiments/effect-history. The manifest defines the case matrix. Each case is linked to its own target SQLite file and trace. For file publication the archive additionally contains immediate post-publish byte snapshots.

Run the existing audit from the experiments directory using python -m effect_history.run audit ../results/experiments/effect-history/pilot-01. Compare its output to the frozen report. JSON, CSV, Markdown and exported ZIP metadata identify the source, protocol SHA-256 and archive commit.

The measured comparison is bounded to the frozen fixtures. Both SQLite and file publication reveal two historical violations in the paired schedule and two under the strong comparison schedule. The fenced arm is recorded separately.
