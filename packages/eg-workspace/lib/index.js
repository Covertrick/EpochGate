import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { stageInContainer } from "./container-stage.js";
import { admit, finishCommand, publishContainer, publishFile, reserve, startCommand } from "./control-client.js";
import { commandRefused, refusedPath } from "./path-guard.js";

const name = "eg-workspace";
const inject = ["tools"];

function containerName() {
  const value = process.env.EG_CONTAINER || "";
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value)) {
    throw new Error("container is not configured");
  }
  return value;
}

function execInContainer(argv, input) {
  const started = Date.now();
  const result = spawnSync(
    "wsl.exe",
    ["-d", "eg-alpine", "-u", "root", "--", "docker", "exec", "-i", "-u", "1000:1000", "-w", "/workspace", containerName(), ...argv],
    { input, encoding: "utf-8", timeout: 120000, windowsHide: true },
  );
  return {
    status: result.status,
    stdout: String(result.stdout || "").slice(0, 20000),
    stderr: String(result.stderr || "").slice(0, 8000),
    duration_ms: Date.now() - started,
    timed_out: result.error && result.error.code === "ETIMEDOUT",
  };
}

function assertInsideWorkspace(path) {
  if (refusedPath(path)) throw new Error("path refused");
  const probe = execInContainer(["realpath", "-m", "--", path]);
  const resolved = String(probe.stdout || "").trim();
  if (resolved !== "/workspace" && !resolved.startsWith("/workspace/")) {
    throw new Error("path refused");
  }
}

const output = {
  schema: { type: "object", additionalProperties: true },
  render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }],
};

function controlBase() {
  return process.env.EG_CONTROL_URL || "";
}

function requireControl(sideEffect) {
  if (controlBase()) return;
  if (process.env.EG_REQUIRE_CONTROL === "1" && sideEffect) {
    throw new Error("control required");
  }
}

async function holdPublish() {
  const gate = process.env.EG_PUBLISH_GATE || "";
  if (!gate) return;
  writeFileSync(`${gate}.ready`, "reserved\n");
  const release = `${gate}.release`;
  const deadline = Date.now() + 90000;
  while (!existsSync(release)) {
    if (Date.now() > deadline) throw new Error("publish gate timed out");
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function authorize(toolName, args) {
  const sideEffect = toolName !== "eg_read";
  requireControl(sideEffect);
  const base = controlBase();
  if (!base) return { allowed: true, controlled: false };
  const runId = process.env.EG_RUN_ID || "";
  if (!runId) throw new Error("control required");
  let admission;
  try {
    admission = await admit(base, runId);
  } catch (error) {
    throw new Error("control unreachable");
  }
  if (!admission.allowed) throw new Error("effect refused");
  if (!sideEffect) return { allowed: true, controlled: true, ...admission };
  const key = createHash("sha256").update(JSON.stringify({ toolName, args, token: admission.fencing_token })).digest("hex");
  const reserved = await reserve(base, {
    run_id: runId,
    idempotency_key: key,
    fencing_token: admission.fencing_token,
    state_version: admission.state_version,
    effect_id: key.slice(0, 16),
    arguments_hash: key,
    tool_name: toolName,
  });
  if (!reserved.allowed) throw new Error("effect refused");
  return { allowed: true, controlled: true, ...admission, idempotency_key: key };
}

function processWorkspace() {
  const root = process.env.EG_WORKSPACE || "";
  if (!root) throw new Error("workspace is not configured");
  return resolve(root);
}

function execInWorkspace(command) {
  const env = { ...process.env };
  delete env.DEEPSEEK_API_KEY;
  delete env.EPOCHGATE_API_KEY;
  delete env.DS_API_KEY;
  return spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", command], {
    cwd: processWorkspace(),
    env,
    encoding: "utf-8",
    timeout: 120000,
    windowsHide: true,
  });
}

function apply(ctx) {
  ctx.tools.register(defineTool({
    name: "eg_read",
    description: "Read one file inside the task container. The path is relative to /workspace.",
    parameters: {
      path: { type: "string", required: true, description: "Relative path inside the container workspace." },
    },
    output,
    async execute(args) {
      const path = String(args.path || "");
      await authorize("eg_read", { path });
      if (process.env.EG_EXECUTOR === "process") {
        if (refusedPath(path)) throw new Error("path refused");
        const root = processWorkspace();
        const full = resolve(root, path);
        const prefix = root.endsWith("\\") || root.endsWith("/") ? root : root + "\\";
        if (full.toLowerCase() !== root.toLowerCase() && !full.toLowerCase().startsWith(prefix.toLowerCase())) {
          throw new Error("path refused");
        }
        return execInWorkspace(`type ${path}`);
      }
      assertInsideWorkspace(path);
      return execInContainer(["cat", "--", path]);
    },
  }));
  ctx.tools.register(defineTool({
    name: "eg_write",
    description: "Replace one source file inside the task container. The path is relative to /workspace.",
    parameters: {
      path: { type: "string", required: true, description: "Relative path inside the container workspace." },
      content: { type: "string", required: true, description: "Complete new file contents." },
    },
    output,
    async execute(args) {
      const path = String(args.path || "");
      const gate = await authorize("eg_write", { path });
      const content = String(args.content ?? "");
      if (process.env.EG_EXECUTOR === "process") {
        if (refusedPath(path)) throw new Error("path refused");
        const root = processWorkspace();
        const stagingDir = join(root, ".eg-staging");
        mkdirSync(stagingDir, { recursive: true });
        const staging = join(stagingDir, gate.idempotency_key || "write");
        writeFileSync(staging, content);
        if (!gate.controlled) throw new Error("control required");
        await holdPublish();
        const published = await publishFile(controlBase(), {
          run_id: process.env.EG_RUN_ID,
          idempotency_key: gate.idempotency_key,
          fencing_token: gate.fencing_token,
          relative_path: path,
          staging_path: staging,
        });
        if (!published.published) return { status: "withheld", published: false };
        return { status: 0, stdout: "", stderr: "", published: true, receipt: published.receipt };
      }
      if (refusedPath(path)) throw new Error("path refused");
      if (!gate.controlled) throw new Error("control required");
      await holdPublish();
      const staging = `/scratch/.eg-staging/${gate.idempotency_key}`;
      stageInContainer(execInContainer, staging, content);
      const published = await publishContainer(controlBase(), {
        container: containerName(),
        run_id: process.env.EG_RUN_ID,
        idempotency_key: gate.idempotency_key,
        fencing_token: gate.fencing_token,
        relative_path: path,
        staging_path: staging,
      });
      if (!published.published) return { status: "withheld", published: false };
      return { status: 0, stdout: "", stderr: "", published: true, receipt: published.receipt };
    },
  }));
  ctx.tools.register(defineTool({
    name: "eg_bash",
    description: "Run one command inside the task container. Host shells, secrets, gold files, and git push are unavailable.",
    parameters: {
      command: { type: "string", required: true, description: "Command to run with /workspace as the working directory." },
    },
    output,
    async execute(args) {
      const command = String(args.command || "");
      if (commandRefused(command)) throw new Error("command refused");
      const gate = await authorize("eg_bash", { command });
      if (gate.controlled) {
        const started = await startCommand(controlBase(), {
          run_id: process.env.EG_RUN_ID,
          idempotency_key: gate.idempotency_key,
          fencing_token: gate.fencing_token,
        });
        if (!started.allowed) throw new Error("effect refused");
      }
      let result;
      try {
        result = process.env.EG_EXECUTOR === "process" ? execInWorkspace(command) : execInContainer(["sh", "-c", command]);
      } catch (error) {
        if (gate.controlled) {
          await finishCommand(controlBase(), {
            run_id: process.env.EG_RUN_ID,
            idempotency_key: gate.idempotency_key,
            fencing_token: gate.fencing_token,
            exit_code: null,
          });
        }
        throw error;
      }
      const body = {
        status: result.status,
        stdout: String(result.stdout || "").slice(0, 20000),
        stderr: String(result.stderr || "").slice(0, 8000),
      };
      if (!gate.controlled) return body;
      const finished = await finishCommand(controlBase(), {
        run_id: process.env.EG_RUN_ID,
        idempotency_key: gate.idempotency_key,
        fencing_token: gate.fencing_token,
        exit_code: result.status,
      });
      return { ...body, effect_state: finished.effect_state, receipt: finished.receipt || null };
    },
  }));
}

export { apply, inject, name };
