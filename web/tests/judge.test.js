// Run with: node --test web/tests/
import {test} from "node:test";
import assert from "node:assert/strict";
import {badge, aggregate, spearman, worstFirst} from "../js/judge.js";

const entry = (fid, qual, extra = {}) => ({
  fidelity: fid, quality: qual,
  fidelity_score: fid.length ? fid.filter(([, a]) => a === "yes").length / fid.length : null,
  quality_score: qual.length ? qual.filter(([, a]) => a === "yes").length / qual.length : null,
  stale: false, ...extra,
});

test("badge counts yes per list and lists the no answers", () => {
  const b = badge(entry([["a", "yes"], ["b", "no"]], [["c", "yes"]]));
  assert.equal(b.text, "1/2 · 1/1");
  assert.match(b.title, /✗ b/);
  assert.doesNotMatch(b.title, /✗ a/);
  assert.equal(b.stale, false);
});

test("badge without quality questions shows only fidelity", () => {
  assert.equal(badge(entry([["a", "yes"]], [])).text, "1/1");
});

test("badge passes stale through and handles a missing entry", () => {
  assert.equal(badge(entry([["a", "yes"]], [], {stale: true})).stale, true);
  assert.equal(badge(undefined), null);
});

test("aggregate ignores stale and missing entries", () => {
  const r = aggregate([entry([["a", "yes"]], [["q", "no"]]), entry([["a", "no"], ["b", "no"]], []),
    entry([["a", "no"]], [["q", "no"]], {stale: true}), undefined]);
  assert.deepEqual(r.fidelity, {mean: 0.5, n: 2});
  assert.deepEqual(r.quality, {mean: 0, n: 1});
});

test("aggregate of nothing has null means", () => {
  assert.deepEqual(aggregate([]), {fidelity: {mean: null, n: 0}, quality: {mean: null, n: 0}});
});

const ten = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

test("spearman: same and reversed order", () => {
  assert.equal(spearman(ten, ten.map(x => x * 3)), 1);
  assert.equal(spearman(ten, ten.map(x => -x)), -1);
});

test("spearman: ties get average ranks", () => {
  const r = spearman([1, 1, 2, 3, 4, 5, 6, 7, 8, 9], ten);
  assert.ok(Math.abs(r - 0.9969650916353059) < 1e-12, String(r));
});

test("spearman: too few pairs or no variance gives null", () => {
  assert.equal(spearman(ten.slice(1), ten.slice(1)), null);
  assert.equal(spearman(ten.map(() => 4), ten), null);
});

test("worstFirst: lowest fidelity first, stale and missing last", () => {
  const a = entry([["a", "yes"], ["b", "no"]], []);          // 0.5
  const b = entry([["a", "no"], ["b", "no"], ["c", "no"], ["d", "no"], ["e", "yes"]], []);   // 0.2
  const s = entry([["a", "no"]], [], {stale: true});            // 0 but stale
  assert.deepEqual([a, undefined, b, s].sort(worstFirst), [b, a, s, undefined]);
});
