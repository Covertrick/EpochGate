import assert from "node:assert/strict";
import test from "node:test";
import { stageArgv } from "../lib/container-stage.js";

test("container staging reads bytes from stdin", () => {
  const argv = stageArgv("/scratch/.eg-staging/effect");
  assert.deepEqual(argv.slice(0, 2), ["python", "-c"]);
  assert.equal(argv.includes("sh"), false);
  assert.equal(argv.some((item) => item.includes(">") || item.includes("$")), false);
  assert.equal(argv.length, 4);
  assert.equal(argv[3], "/scratch/.eg-staging/effect");
  assert.equal(argv[2].includes("sys.stdin.buffer.read"), true);
});

test("staging rejects paths outside the scratch directory", () => {
  assert.throws(() => stageArgv("../bug.py"), /path refused/);
  assert.throws(() => stageArgv("/workspace/bug.py"), /path refused/);
});
