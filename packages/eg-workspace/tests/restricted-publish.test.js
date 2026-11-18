import assert from "node:assert/strict";
import test from "node:test";
import { publishRestricted } from "../lib/restricted-publish.js";

test("direct does not consult eligibility", async () => {
  let written = false;
  const result = await publishRestricted({
    arm: "direct",
    hold: async () => {},
    admit: async () => {
      throw new Error("direct must not admit");
    },
    write: () => {
      written = true;
    },
  });
  assert.equal(written, true);
  assert.equal(result.published, true);
  assert.equal(result.eligibilityChecked, false);
});

test("commit-only withholds when the gate-time admit is already revoked", async () => {
  let eligible = true;
  let written = false;
  const result = await publishRestricted({
    arm: "commit_only",
    hold: async () => {
      eligible = false;
    },
    admit: async () => ({ allowed: eligible }),
    write: () => {
      written = true;
    },
  });
  assert.equal(written, false);
  assert.equal(result.published, false);
  assert.equal(result.status, "withheld");
});

test("revoke after admit still writes", async () => {
  let eligible = true;
  let written = false;
  const result = await publishRestricted({
    arm: "commit_only",
    hold: async () => {},
    admit: async () => ({ allowed: eligible }),
    betweenAdmitAndWrite: async () => {
      eligible = false;
    },
    write: () => {
      written = true;
    },
  });
  assert.equal(eligible, false);
  assert.equal(written, true);
  assert.equal(result.published, true);
  assert.equal(result.rechecked, false);
});
