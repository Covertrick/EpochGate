import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";

const name = "eg-observability";
const inject = ["tools"];

function apply(ctx) {
  const file = process.env.EG_OBS_LOG || "";
  if (!file) return;
  const episode = process.env.EG_EPISODE_ID || "episode-unset";
  const session = process.env.EG_SESSION_ID || "session-unset";
  const artifactDir = process.env.EG_OBS_DIR || "";
  mkdirSync(dirname(file), { recursive: true });
  let seq = 0;
  ctx.on("tools/result", (exec, result) => {
    seq += 1;
    const stdout = String((result && result.stdout) || "");
    const payload = {
      tool_name: exec.name,
      duration_ms: result && result.duration_ms,
      exit_code: result && result.status,
    };
    if (artifactDir && stdout.length > 2000) {
      mkdirSync(artifactDir, { recursive: true });
      const digest = createHash("sha256").update(stdout).digest("hex");
      const ref = join(artifactDir, `${seq}-stdout-${digest.slice(0, 12)}.txt`);
      writeFileSync(ref, stdout);
      payload.stdout_ref = ref;
      payload.stdout_sha256 = digest;
    }
    const event = {
      schema_version: "eg2.events.v1",
      episode_id: episode,
      session_id: session,
      event_seq: seq,
      event_type: "tool.execution.finished",
      timestamp_utc: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      status: result && result.isError ? "error" : "success",
      payload,
    };
    appendFileSync(file, JSON.stringify(event) + "\n");
  });
}

export { apply, inject, name };
