import { spawnSync } from "node:child_process";
import { defineTool } from "@deepseek-ai/dsh-tools";
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
      assertInsideWorkspace(path);
      return execInContainer(["sh", "-c", "cat > \"$1\"", "sh", path], String(args.content ?? ""));
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
      return execInContainer(["sh", "-c", command]);
    },
  }));
}

export { apply, inject, name };
