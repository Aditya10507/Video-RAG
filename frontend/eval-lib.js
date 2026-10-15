// Pure formatting rules for the eval dashboard. No DOM here, so this file is
// unit-tested with stdlib node:test (see eval-lib.test.js) and shared by eval.html.
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) { module.exports = factory(); }
  else { root.EvalLib = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // metrics: {key: {format: score|seconds|usd, direction: higher|lower}}
  function fmtMetric(metrics, metric, value) {
    if (value === null || value === undefined || isNaN(value)) { return null; }
    var f = (metrics[metric] || {}).format;
    if (f === "seconds") { return (value / 1000).toFixed(1) + " s"; }
    if (f === "usd") { return "$" + Number(value).toFixed(3); }
    return Number(value).toFixed(2);
  }

  function deltaInfo(metrics, metric, delta) {
    if (delta === null || delta === undefined || isNaN(delta)) {
      return { cls: "flat", text: "—" };
    }
    var dir = (metrics[metric] || {}).direction || "higher";
    var good = dir === "higher" ? delta >= 0 : delta <= 0;
    return {
      cls: delta === 0 ? "flat" : (good ? "up" : "down"),
      text: (delta > 0 ? "▲ " : delta < 0 ? "▼ " : "– ") +
        fmtMetric(metrics, metric, Math.abs(delta))
    };
  }

  function pillState(metrics, targets, metric, value) {
    if (value === null || value === undefined || isNaN(value)) {
      return { cls: "pill-none", text: "—" };
    }
    var t = (targets || {})[metric];
    if (t === null || t === undefined) {
      return { cls: "pill-none", text: "No target set" };
    }
    var dir = (metrics[metric] || {}).direction || "higher";
    var good = dir === "higher" ? value >= t : value <= t;
    return { cls: good ? "pill-ok" : "pill-bad", text: good ? "✓ Meets target" : "✗ Below target" };
  }

  function fmtTime(s) {
    s = Math.max(0, Math.floor(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var mm = (h > 0 && m < 10 ? "0" : "") + m, ss = (r < 10 ? "0" : "") + r;
    return h > 0 ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  }

  return { fmtMetric: fmtMetric, deltaInfo: deltaInfo, pillState: pillState, fmtTime: fmtTime };
}));
