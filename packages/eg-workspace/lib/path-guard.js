const SECRET_MARKERS = [".env", "gold", "deepseek", "epochgate_api", "git push"];

function refusedPath(path) {
  if (!path || typeof path !== "string") return true;
  if (path.startsWith("/") || path.startsWith("\\") || /^[A-Za-z]:/.test(path)) return true;
  const parts = path.split(/[\\/]/);
  if (parts.some((part) => part === ".." || part === ".env")) return true;
  return path.toLowerCase().includes("gold");
}

function commandRefused(command) {
  const text = String(command || "").toLowerCase();
  return SECRET_MARKERS.some((marker) => text.includes(marker));
}

function containerExecArgs(container, argv) {
  return ["docker", "exec", "-i", "-u", "1000:1000", "-w", "/workspace", container, ...argv];
}

export { commandRefused, containerExecArgs, refusedPath };
