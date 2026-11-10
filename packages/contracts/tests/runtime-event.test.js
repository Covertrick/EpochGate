import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const schema = JSON.parse(readFileSync(new URL("../schemas/runtime-event.schema.json", import.meta.url), "utf8"));

function validate(event) {
  for (const key of schema.required) {
    if (!Object.prototype.hasOwnProperty.call(event, key)) {
      throw new Error(`missing ${key}`);
    }
  }
  if (event.schema_version !== schema.properties.schema_version.const) {
    throw new Error("schema_version");
  }
  for (const key of Object.keys(event)) {
    if (!Object.prototype.hasOwnProperty.call(schema.properties, key)) {
      throw new Error(`unknown ${key}`);
    }
  }
}

const valid = {
  schema_version: "runtime.v1",
  event_id: "run:1",
  run_id: "run",
  attempt_id: null,
  effect_id: null,
  epoch: 1,
  event_type: "file_published",
  timestamp: "2026-11-10T00:00:00Z",
  ledger_sequence: 1,
};

test("a runtime.v1 event with every required field is accepted", () => {
  validate(valid);
});

test("dropping a required field fails the same schema", () => {
  const broken = { ...valid };
  delete broken.event_id;
  assert.throws(() => validate(broken), /missing event_id/);
});
