const STAGE_CODE = "import pathlib,sys; target=pathlib.Path(sys.argv[1]); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(sys.stdin.buffer.read())";

function stageArgv(relative) {
  const path = String(relative || "");
  const scratch = path.startsWith("/scratch/.eg-staging/");
  const relativeStaging = path.startsWith(".eg-staging/");
  if (!path || path.split("/").includes("..") || /\s/.test(path) || (!scratch && !relativeStaging)) {
    throw new Error("path refused");
  }
  return ["python", "-c", STAGE_CODE, path];
}

function stageInContainer(execInContainer, relative, content) {
  const result = execInContainer(stageArgv(relative), String(content ?? ""));
  if (result.status !== 0) throw new Error("stage failed");
  return result;
}

export { stageArgv, stageInContainer };
