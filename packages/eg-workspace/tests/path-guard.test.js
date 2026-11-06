import assert from "node:assert/strict";
import test from "node:test";
import { commandRefused, containerExecArgs, refusedPath } from "../lib/path-guard.js";

test("lexical escapes and gold paths are refused", () => {
  assert.equal(refusedPath("../etc/passwd"), true);
  assert.equal(refusedPath("/etc/passwd"), true);
  assert.equal(refusedPath("C:/secret"), true);
  assert.equal(refusedPath("nested/.env"), true);
  assert.equal(refusedPath("gold_patch.diff"), true);
  assert.equal(refusedPath("astropy/modeling/separable.py"), false);
});

test("container exec does not receive secret names", () => {
  const args = containerExecArgs("eg-agent", ["cat", "--", "a.py"]);
  assert.equal(args.includes("-e"), false);
  assert.equal(args.some((item) => item.includes("DEEPSEEK_API_KEY")), false);
});

test("secret and push commands are refused", () => {
  assert.equal(commandRefused("printenv DEEPSEEK_API_KEY"), true);
  assert.equal(commandRefused("git push origin main"), true);
  assert.equal(commandRefused("python -m pytest astropy/modeling/tests/test_separable.py -q"), false);
});
