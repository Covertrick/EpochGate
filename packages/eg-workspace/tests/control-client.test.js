import assert from "node:assert/strict";
import test from "node:test";
import { admit, postJson, reserve } from "../lib/control-client.js";

test("plugin client uses the python ledger", async () => {
  const base = process.env.EG_CONTROL_URL;
  const runId = process.env.EG_RUN_ID;
  const created = await postJson(`${base}/runs`, { run_id: runId, session_id: "session-node" });
  const reserved = await reserve(base, {
    run_id: runId,
    idempotency_key: "node-effect",
    fencing_token: created.fencing_token,
    state_version: created.state_version,
    effect_id: "node-effect",
    arguments_hash: "abc",
  });
  assert.equal(reserved.allowed, true);
  await postJson(`${base}/runs/${runId}/revoke`, {});
  const late = await admit(base, runId);
  assert.equal(late.allowed, false);
});
