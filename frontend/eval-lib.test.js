// Frontend unit tests: number formatting, delta direction, pill states.
// Zero dependencies — run with:  node --test frontend/eval-lib.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const Lib = require("./eval-lib.js");

const METRICS = {
  recall_at_k: { format: "score", direction: "higher" },
  latency_ms: { format: "seconds", direction: "lower" },
  cost_usd: { format: "usd", direction: "lower" },
};
const TARGETS = { recall_at_k: 0.85, latency_ms: 5000, cost_usd: null };

test("score formatting and missing values", () => {
  assert.equal(Lib.fmtMetric(METRICS, "recall_at_k", 0.856), "0.86");
  assert.equal(Lib.fmtMetric(METRICS, "latency_ms", 4200), "4.2 s");
  assert.equal(Lib.fmtMetric(METRICS, "cost_usd", 0.012), "$0.012");
  assert.equal(Lib.fmtMetric(METRICS, "recall_at_k", null), null);
  assert.equal(Lib.fmtMetric(METRICS, "recall_at_k", NaN), null);
  assert.equal(Lib.fmtMetric(METRICS, "unknown_metric", 0.5), "0.50");
});

test("delta direction respects higher/lower-is-better", () => {
  assert.deepEqual(Lib.deltaInfo(METRICS, "recall_at_k", 0.04), { cls: "up", text: "▲ 0.04" });
  assert.deepEqual(Lib.deltaInfo(METRICS, "recall_at_k", -0.03), { cls: "down", text: "▼ 0.03" });
  // Lower-is-better: arrows show the direction of change, colour shows good/bad.
  assert.deepEqual(Lib.deltaInfo(METRICS, "latency_ms", -300), { cls: "up", text: "▼ 0.3 s" });
  assert.deepEqual(Lib.deltaInfo(METRICS, "cost_usd", 0.002), { cls: "down", text: "▲ $0.002" });
  assert.deepEqual(Lib.deltaInfo(METRICS, "recall_at_k", 0), { cls: "flat", text: "– 0.00" });
  assert.deepEqual(Lib.deltaInfo(METRICS, "recall_at_k", null), { cls: "flat", text: "—" });
});

test("pill states incl. missing values and unset targets", () => {
  assert.deepEqual(Lib.pillState(METRICS, TARGETS, "recall_at_k", 0.86),
    { cls: "pill-ok", text: "✓ Meets target" });
  assert.deepEqual(Lib.pillState(METRICS, TARGETS, "recall_at_k", 0.80),
    { cls: "pill-bad", text: "✗ Below target" });
  assert.deepEqual(Lib.pillState(METRICS, TARGETS, "latency_ms", 4200),
    { cls: "pill-ok", text: "✓ Meets target" });
  assert.deepEqual(Lib.pillState(METRICS, TARGETS, "cost_usd", 0.012),
    { cls: "pill-none", text: "No target set" });
  assert.deepEqual(Lib.pillState(METRICS, TARGETS, "recall_at_k", null),
    { cls: "pill-none", text: "—" });
});

test("timestamp labels", () => {
  assert.equal(Lib.fmtTime(65), "1:05");
  assert.equal(Lib.fmtTime(3661), "1:01:01");
  assert.equal(Lib.fmtTime(0), "0:00");
});
