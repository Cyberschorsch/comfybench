// Run with: node --test web/tests/
import {test} from "node:test";
import assert from "node:assert/strict";
import {BASE_TITLE, applyTitle, runTitle} from "../js/title.js";

const status = (extra = {}) => ({state: "running", kind: "run", total: 10, done: 3, skipped: 0, failed: 0, idle: 0, ...extra});

test("idle, done and stopped runs leave the plain title", () => {
  for(const state of ["idle", "done", "stopped", "error", null, undefined])
    assert.equal(runTitle(state ? status({state}) : state), BASE_TITLE);
  assert.equal(runTitle(null), BASE_TITLE);
});

test("generating shows the finished count of the run", () => {
  assert.equal(runTitle(status()), "Generating 3/10 · ComfyBench");
  assert.equal(runTitle(status({state: "stopping"})), "Generating 3/10 · ComfyBench");
});

test("judging is named as such", () => {
  assert.equal(runTitle(status({kind: "judge"})), "Judging 3/10 · ComfyBench");
});

test("skipped, failed and idle jobs count as finished like in the status panel", () => {
  assert.equal(runTitle(status({done: 2, skipped: 1, failed: 3, idle: 4})), "Generating 10/10 · ComfyBench");
});

test("a status without totals or counters does not print NaN", () => {
  assert.equal(runTitle({state: "running", kind: "run"}), "Generating 0/0 · ComfyBench");
});

test("applyTitle writes the title only when it changes", () => {
  const doc = {title: BASE_TITLE};
  applyTitle(status({done: 4, total: 10}), doc);
  assert.equal(doc.title, "Generating 4/10 · ComfyBench");
  const writes = [];
  const spy = {get title(){ return this._t; }, set title(v){ writes.push(v); this._t = v; }, _t: doc.title};
  applyTitle(status({done: 4, total: 10}), spy);
  assert.deepEqual(writes, []);
  applyTitle(status({state: "done"}), spy);
  assert.deepEqual(writes, [BASE_TITLE]);
  assert.equal(applyTitle(status(), null), undefined);
});
