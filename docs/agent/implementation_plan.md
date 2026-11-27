# Implementation plan

1. Add a durable SQLite job store and an independent Worker for a controlled receiver. Validate takeover and receipt loss before exposing it to the console.
2. Build a DAG repair algorithm with preserved committed history and pluggable recovery policies. Persist decisions and budget use in the durable execution path.
3. Add authenticated task and evidence APIs, an HTTP controlled receiver boundary, and console inspection.
4. Freeze a pilot, execute baselines, audit results independently and report the live-model and workload gates separately.
