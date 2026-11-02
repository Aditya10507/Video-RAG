/*
 * Evaluation dashboards embedded in the main app (index.html).
 *
 * Ported from the inline script of eval.html, which is still the standalone
 * page at http://127.0.0.1:8000/eval. Differences here:
 *
 *   - there is no eval sidebar: the app sidebar's "Evals" submenu calls
 *     EvalDashboard.show("live") / EvalDashboard.show("offline");
 *   - the System block lives in the eval header strip (eval-system-*);
 *   - boot is lazy: nothing loads or streams until the user first opens
 *     "Evals", and the polling fallback pauses while another view is open.
 *
 * Live dashboard: per-query traces over /api/live/* (stream + 3s polling
 * fallback) and the offline golden-set panel over /api/runs*.
 * textContent only — external titles and excerpts never become markup.
 */
(function () {
  "use strict";
  var API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
  var API_KEY = "";
  try { API_KEY = (window.sessionStorage.getItem("video-rag.api-key") || "").trim(); }
  catch (e) { API_KEY = ""; }

  var state = {
    active: true,     // polling allowed while the eval view is open
    booting: false,
    currentTab: "live",
    queries: [],
    selectedQueryId: null,
    details: {},      // id -> detail object
    queryFilter: "",
    metrics: null,    // metrics registry from /api/config/dashboard
    targets: null     // offline targets from the same config
  };
  var $ = function (id) { return document.getElementById(id); };

  // The system strip is decorative: a missing node must never abort the
  // dashboard render (that is exactly the bug one absent id once caused).
  function setSystemQueue(text) {
    var node = $("eval-system-queue");
    if (node) { node.textContent = text; }
  }

  function headers(extra) {
    var h = extra || {};
    if (API_KEY) { h["X-API-Key"] = API_KEY; }
    return h;
  }
  async function get(path) {
    var r = await fetch(API_BASE + path, { headers: headers() });
    if (!r.ok) { throw new Error("HTTP " + r.status + " " + path); }
    return r.json();
  }
  function fmtNum(v, dp) {
    if (v === null || v === undefined || isNaN(v)) { return "—"; }
    return Number(v).toFixed(dp === undefined ? 2 : dp);
  }
  function mfmt(name, v) {
    if (!state.metrics) { return fmtNum(v, 2); }
    return window.EvalLib.fmtMetric(state.metrics, name, v) || "—";
  }
  function badge(txt, cls) {
    var s = document.createElement("span");
    s.textContent = txt;
    s.className = "badge " + cls;
    return s;
  }
  var STATUS_BADGE = {
    answered: ["answered", "badge-ok"], partial: ["partial", "badge-warn"],
    not_covered: ["not covered", "badge-refuse"], error: ["error", "badge-refuse"],
    running: ["running…", "badge-idle"]
  };
  function relTime(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) { return ""; }
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) { return "just now"; }
    if (s < 3600) { return Math.floor(s / 60) + "m ago"; }
    return Math.floor(s / 3600) + "h ago";
  }
  function scorePill(id, m) {
    var v = m.value;
    var s = document.createElement("span");
    s.className = "pill score-pill";
    if (m.status === "error") {
      s.classList.add("pill-bad");
      s.textContent = LIVE_SHORT_LABELS[id] + " !";
      s.title = "Judge metric failed: " + (m.error || "unknown error");
    } else if (m.status === "not_applicable") {
      s.classList.add("pill-none");
      s.textContent = LIVE_SHORT_LABELS[id] + " N/A";
      s.title = "Not applicable to this query.";
    } else if (v === null || v === undefined) {
      s.classList.add("pill-none");
      s.textContent = LIVE_SHORT_LABELS[id] + " —";
      s.title = "Not computed (judge failed or not applicable).";
    } else {
      s.textContent = LIVE_SHORT_LABELS[id] + " " + mfmt(id, v);
      s.title = LIVE_LABELS[id] + "; automated estimate, not a correctness label.";
    }
    return s;
  }
  var LIVE_METRICS = ["faithfulness", "answer_relevancy", "retrieval_relevance"];
  var LIVE_LABELS = {
    faithfulness: "Faithfulness estimate",
    answer_relevancy: "Answer relevancy estimate",
    retrieval_relevance: "Retrieved relevance (precision-like)"
  };
  var LIVE_SHORT_LABELS = {
    faithfulness: "Faith",
    answer_relevancy: "Answer",
    retrieval_relevance: "Retrieved"
  };

  // =================== LIVE TAB ===================
  async function refreshLive() {
    if (!state.active) { return; }
    var queries;
    try {
      queries = (await get("/api/live/recent?limit=30")).queries || [];
    } catch (e) {
      setConn("is-poll", "API unavailable");
      $("eval-system-dot").className = "eval-system-dot warn";
      $("eval-system-status").textContent = "API unavailable";
      setSystemQueue(e.message);
      return;
    }
    state.queries = queries;
    if (!queries.some(function (q) { return q.id === state.selectedQueryId; })) {
      state.selectedQueryId = queries.length ? queries[0].id : null;
    }
    renderLiveSummary();
    renderQueryList();
    if (state.selectedQueryId !== null) {
      loadDetail(state.selectedQueryId);
    } else {
      var empty = document.createElement("div");
      empty.className = "selected-placeholder";
      empty.textContent = "No queries yet. Ask a question in the app to see its retrieval evidence and answer here.";
      $("selected-query-detail").replaceChildren(empty);
    }
  }
  function removeChildren(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  function renderLiveSummary() {
    var queries = state.queries;
    var answered = queries.filter(function (q) { return q.status === "answered"; }).length;
    var review = queries.filter(function (q) {
      return ["partial", "not_covered", "error"].indexOf(q.status) !== -1;
    }).length;
    var queued = queries.filter(function (q) {
      return ["pending", "queued", "running"].indexOf(q.scoring_status) !== -1;
    }).length;
    $("summary-recent").textContent = String(queries.length);
    $("summary-answered").textContent = String(answered);
    $("summary-review").textContent = String(review);
    $("summary-queue").textContent = String(queued);
    $("live-count").textContent = queries.length
      ? queries.length + " of latest " + (queries.length === 30 ? "30" : queries.length)
      : "No queries captured yet";
    setSystemQueue(queued + " scoring in recent queries");
  }

  function renderQueryList() {
    var holder = $("live-feed");
    removeChildren(holder);
    var needle = state.queryFilter.trim().toLowerCase();
    var queries = state.queries.filter(function (q) {
      return !needle || (q.question + " " + (q.course_id || "")).toLowerCase().indexOf(needle) !== -1;
    });
    $("live-empty").hidden = queries.length !== 0;
    if (!queries.length) {
      $("live-empty").querySelector("h2").textContent =
        state.queries.length ? "No matching queries" : "No queries yet";
      $("live-empty").querySelector("p").textContent = state.queries.length
        ? "Try a different search."
        : "Ask a question on the Home page and its trace will appear here.";
    }
    $("live-search").setAttribute("aria-label", "Search recent questions or courses");
    queries.forEach(function (q) { holder.appendChild(liveCard(q)); });
  }

  function liveCard(q) {
    var card = document.createElement("button");
    card.type = "button";
    card.className = "query-item";
    card.setAttribute("aria-pressed", String(q.id === state.selectedQueryId));
    card.setAttribute("aria-label", "Inspect query: " + q.question);
    var qq = document.createElement("span");
    qq.className = "query-item-title";
    qq.textContent = q.question;
    var sub = document.createElement("span");
    sub.className = "query-item-sub";
    var course = document.createElement("span");
    course.className = "query-item-course";
    course.textContent = String(q.course_id || "Course unavailable").replace(/^video:/, "");
    var when = document.createElement("span");
    when.textContent = relTime(q.created_at);
    when.title = q.created_at;
    sub.appendChild(course);
    sub.appendChild(when);
    var meta = document.createElement("div");
    meta.className = "query-item-meta";
    var b = STATUS_BADGE[(q.status || "running")] || STATUS_BADGE.running;
    meta.appendChild(badge(b[1] === "running" ? "running…" : (STATUS_BADGE[q.status] || [q.status])[0], b[1]));
    if (q.scoring_status === "queued") { meta.appendChild(badge("judge queued", "badge-scored")); }
    else if (q.scoring_status === "running" || q.scoring_status === "pending") {
      meta.appendChild(badge("scoring…", "badge-scored"));
    } else if (q.scoring_status === "partial") {
      meta.appendChild(badge("partial scores", "badge-warn"));
    } else if (q.scoring_status === "error") {
      meta.appendChild(badge("scoring failed", "badge-refuse"));
    } else if (q.scoring_status === "overloaded") {
      meta.appendChild(badge("scores skipped: busy", "badge-warn"));
    }
    LIVE_METRICS.forEach(function (m) {
      if (q.scores && q.scores[m]) { meta.appendChild(scorePill(m, q.scores[m])); }
    });
    card.appendChild(qq);
    card.appendChild(sub);
    card.appendChild(meta);
    card.addEventListener("click", function () {
      state.selectedQueryId = q.id;
      renderQueryList();
      loadDetail(q.id);
    });
    return card;
  }

  async function loadDetail(qid) {
    if (state.details[qid]) { renderDetail(qid); }
    var d;
    try {
      d = await get("/api/live/query/" + qid);
    } catch (e) {
      if (state.selectedQueryId === qid) {
        $("selected-query-detail").textContent = "Unable to load query details: " + e.message;
      }
      return;
    }
    state.details[qid] = d;
    renderDetail(qid);
  }

  function renderDetail(qid) {
    if (state.selectedQueryId !== qid) { return; }
    var body = $("selected-query-detail");
    var d = state.details[qid];
    if (!d) { return; }
    removeChildren(body);

    var heading = document.createElement("div");
    heading.className = "detail-heading";
    var titleBlock = document.createElement("div");
    var eyebrow = document.createElement("div");
    eyebrow.className = "eval-eyebrow";
    eyebrow.textContent = "Query detail";
    var title = document.createElement("h2");
    title.textContent = d.question || "Untitled query";
    var subline = document.createElement("p");
    subline.className = "detail-heading-sub";
    var selectedQuery = state.queries.filter(function (q) { return q.id === qid; })[0];
    subline.textContent = (d.course_id || "Course unavailable").replace(/^video:/, "") +
      " · " + (selectedQuery ? relTime(selectedQuery.created_at) : "");
    titleBlock.appendChild(eyebrow);
    titleBlock.appendChild(title);
    titleBlock.appendChild(subline);
    var status = STATUS_BADGE[d.status] || ["running…", "badge-idle"];
    var statusBadge = badge(status[0], status[1]);
    statusBadge.classList.add("detail-status");
    heading.appendChild(titleBlock);
    heading.appendChild(statusBadge);
    body.appendChild(heading);

    var meta = document.createElement("div");
    meta.className = "detail-meta";
    var parts = [];
    if (d.effective_question && d.effective_question !== d.question) {
      parts.push("Searched as: " + d.effective_question);
    }
    if (d.gate) {
      parts.push("gate " + d.gate + (d.verify_verdict ? " → " + d.verify_verdict : ""));
    }
    if (d.lang && d.lang !== "english") { parts.push("reply in " + d.lang); }
    if (d.course_id) { parts.push(String(d.course_id).replace(/^video:/, "video ")); }
    var t = d.timings_ms || {};
    if (t.total_ms !== undefined) {
      parts.push("retrieve " + fmtNum((t.retrieve_ms || 0) / 1000, 1) + "s, answer " +
        fmtNum(((t.verify_ms || 0) + (t.generate_ms || 0)) / 1000, 1) + "s, total " +
        fmtNum(t.total_ms / 1000, 1) + "s");
    }
    parts.push("top score " + fmtNum(d.top_score, 2));
    meta.textContent = parts.join(" · ");
    body.appendChild(meta);

    var metricGrid = document.createElement("div");
    metricGrid.className = "detail-metrics";
    var metricInfo = [
      ["retrieval_relevance", "Retrieved relevance", "Judge-estimated relevant share · precision-like, not recall"],
      ["faithfulness", "Faithfulness estimate", "Claims supported by the excerpts sent to the model"],
      ["answer_relevancy", "Answer relevancy", "How directly the answer addresses the question"]
    ];
    metricInfo.forEach(function (info) {
      var metric = info[0], score = (d.scores || {})[metric];
      var item = document.createElement("section");
      item.className = "detail-metric";
      var label = document.createElement("div");
      label.className = "detail-metric-label";
      label.textContent = info[1];
      var value = document.createElement("div");
      value.className = "detail-metric-value";
      var note = document.createElement("div");
      note.className = "detail-metric-note";
      note.textContent = info[2];
      if (score && score.status === "done" && score.value !== null) {
        value.textContent = fmtNum(score.value, 2);
        var source = [score.judge_model, score.prompt_version ? "prompt " + score.prompt_version : ""]
          .filter(Boolean).join(" · ");
        if (source) { note.textContent += " · " + source; }
      } else if (score && score.status === "error") {
        value.textContent = "Failed";
        value.classList.add("metric-error");
        note.textContent = score.error || "Judge returned an invalid result.";
      } else if (score && score.status === "not_applicable") {
        value.textContent = "N/A";
        note.textContent = "Not applicable to this query.";
      } else if (d.scoring_status === "pending" || d.scoring_status === "queued" ||
                 d.scoring_status === "running") {
        value.textContent = "…";
        note.textContent = "Waiting for asynchronous judge scoring.";
      } else {
        value.textContent = "—";
        note.textContent = "No metric result was recorded.";
      }
      item.appendChild(label);
      item.appendChild(value);
      item.appendChild(note);
      metricGrid.appendChild(item);
    });
    body.appendChild(metricGrid);

    var chunkHeading = document.createElement("div");
    chunkHeading.className = "section-heading";
    var chunkTitle = document.createElement("h3");
    chunkTitle.textContent = "Retrieval evidence";
    var chunks = d.chunks || [];
    var nSent = chunks.filter(function (c) { return c.sent_to_llm; }).length;
    var chunkSummary = document.createElement("span");
    chunkSummary.textContent = chunks.length + " candidates · top " + nSent + " sent to the answer model";
    chunkHeading.appendChild(chunkTitle);
    chunkHeading.appendChild(chunkSummary);
    body.appendChild(chunkHeading);

    // --- chunks: before/after reranking ---
    var tableWrap = document.createElement("div");
    tableWrap.className = "score-table-wrap";
    var tbl = document.createElement("table");
    tbl.className = "score-table";
    var head = document.createElement("tr");
    ["#", "Chunk / timestamp", "Dense", "Keyword", "Rerank", "Rank", "Prompt", "Judge"].forEach(function (h) {
      var th = document.createElement("th"); th.textContent = h; head.appendChild(th);
    });
    tbl.appendChild(head);
    var showAll = Boolean(state.showAllChunks && state.showAllChunks[qid]);
    var shownChunks = showAll ? chunks : chunks.slice(0, 10);
    shownChunks.forEach(function (c) {
      var tr = document.createElement("tr");
      if (c.sent_to_llm) { tr.className = "senttd"; }
      var chunkName = document.createElement("td");
      chunkName.textContent = (c.title || c.video_id) + " @ " +
        window.EvalLib.fmtTime(c.start_s || 0);
      chunkName.title = (c.text || "").slice(0, 500);
      var cells = [
        String(c.idx),
        fmtNum(c.dense_score, 3),
        fmtNum(c.keyword_score, 3),
        fmtNum(c.rerank_score, 3),
        (c.union_rank || "—") + " → " + (c.idx || "—"),
        c.sent_to_llm ? "Sent" : "—",
        c.relevance === null || c.relevance === undefined
          ? "Pending" : (c.relevance > 0 ? "Relevant" : "Not relevant")
      ];
      var indexCell = document.createElement("td");
      indexCell.textContent = cells[0];
      tr.appendChild(indexCell);
      tr.appendChild(chunkName);
      cells.slice(1).forEach(function (txt) {
        var td = document.createElement("td");
        td.textContent = txt;
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    });
    tableWrap.appendChild(tbl);
    body.appendChild(tableWrap);
    if (chunks.length > 10) {
      var more = document.createElement("button");
      more.type = "button";
      more.className = "text-button";
      more.textContent = showAll ? "Show top 10 only" : "Show all " + chunks.length + " candidates";
      more.addEventListener("click", function () {
        if (!state.showAllChunks) { state.showAllChunks = {}; }
        state.showAllChunks[qid] = !showAll;
        renderDetail(qid);
      });
      body.appendChild(more);
    }
    var sentNote = document.createElement("div");
    sentNote.className = "detail-meta";
    sentNote.textContent = chunks.length
      ? "Shaded rows were sent to the answer model. Score columns are distinct ranking signals; a dash means that retrieval source returned no score."
      : "No chunks were retrieved for this query.";
    body.appendChild(sentNote);

    // --- the excerpts the LLM actually saw ---
    var sentChunks = chunks.filter(function (c) { return c.sent_to_llm; });
    if (sentChunks.length && d.answer) {
      var context = document.createElement("details");
      context.className = "excerpt";
      var contextSummary = document.createElement("summary");
      contextSummary.textContent = "Inspect the " + sentChunks.length + " excerpts sent to the answer model";
      context.appendChild(contextSummary);
      sentChunks.forEach(function (c) {
        var e = document.createElement("p");
        e.className = "eval-snippet";
        e.textContent = (c.title || c.video_id) + " · " +
          window.EvalLib.fmtTime(c.start_s || 0) + " — " + (c.text || "").slice(0, 600);
        context.appendChild(e);
      });
      body.appendChild(context);
    }

    // --- generated answer and citations ---
    var answerHeading = document.createElement("div");
    answerHeading.className = "section-heading";
    var answerTitle = document.createElement("h3");
    answerTitle.textContent = "Generated answer";
    var timing = document.createElement("span");
    timing.textContent = t.generate_ms !== undefined
      ? fmtNum(t.generate_ms / 1000, 1) + "s generation · " + sentChunks.length + " excerpts used"
      : sentChunks.length + " excerpts used";
    answerHeading.appendChild(answerTitle);
    answerHeading.appendChild(timing);
    body.appendChild(answerHeading);
    if (d.answer) {
      var ans = document.createElement("div");
      ans.className = "live-answer";
      ans.textContent = d.answer;
      body.appendChild(ans);
      var cites = document.createElement("div");
      cites.className = "cite-line";
      var sources = [d.primary_source].concat(d.also_mentioned || []);
      sources.forEach(function (s) {
        if (!s || !s.video_id) { return; }
        if (typeof window.openInAppPlayer === "function") {
          // In-app playback: switch to the Home view and play in the
          // embedded player at the cited timestamp.
          var open = document.createElement("button");
          open.type = "button";
          open.className = "hint cite-link";
          open.textContent = (s.video_title || s.video_id) + " at " + (s.timestamp_label || "");
          open.addEventListener("click", function () { window.openInAppPlayer(s); });
          cites.appendChild(open);
        } else {
          var a = document.createElement("a");
          a.href = s.url;
          a.target = "_blank"; a.rel = "noopener noreferrer";
          a.className = "hint";
          a.textContent = (s.video_title || s.video_id) + " at " + (s.timestamp_label || "");
          cites.appendChild(a);
        }
      });
      if (d.message) {
        var msg = document.createElement("span");
        msg.className = "hint";
        msg.textContent = d.message;
        cites.appendChild(msg);
      }
      body.appendChild(cites);
    } else {
      var noAnswer = document.createElement("div");
      noAnswer.className = "live-answer";
      noAnswer.textContent = d.message || "No answer was generated for this query.";
      body.appendChild(noAnswer);
      if ((d.covers || []).length) {
        var cov = document.createElement("div");
        cov.className = "detail-meta";
        cov.textContent = "Course covers: " + d.covers.slice(0, 4).join(" · ");
        body.appendChild(cov);
      }
    }

    // --- metric-level scoring status and judge rationale ---
    var judgeHeading = document.createElement("div");
    judgeHeading.className = "section-heading";
    var judgeTitle = document.createElement("h3");
    judgeTitle.textContent = "Judge diagnostics";
    var scoringLabels = {
      pending: "Waiting", queued: "Queued", running: "Running", partial: "Partial",
      done: "Complete", error: "Failed", overloaded: "Skipped · queue full"
    };
    var judgeState = document.createElement("span");
    judgeState.textContent = scoringLabels[d.scoring_status] || d.scoring_status || "Status unavailable";
    judgeHeading.appendChild(judgeTitle);
    judgeHeading.appendChild(judgeState);
    body.appendChild(judgeHeading);
    LIVE_METRICS.forEach(function (m) {
      var row = document.createElement("p");
      row.className = "judge-line";
      var sc = (d.scores || {})[m];
      var label = LIVE_LABELS[m] || m;
      if (!sc) {
        row.classList.add("metric-na");
        row.textContent = label + ": " +
          (d.scoring_status === "pending" || d.scoring_status === "queued" ||
            d.scoring_status === "running" ? "waiting for judge" : "not recorded");
      } else if (sc.status === "error") {
        row.classList.add("metric-error");
        row.textContent = label + ": failed — " + (sc.error || "no error detail");
      } else if (sc.status === "not_applicable") {
        row.classList.add("metric-na");
        row.textContent = label + ": not applicable";
      } else {
        row.textContent = label + ": " + fmtNum(sc.value, 2) +
          (sc.detail && sc.detail.reasoning ? " — " + sc.detail.reasoning :
            sc.detail && sc.detail.claims ? " — " + sc.detail.claims.length + " claims graded" :
              sc.detail && sc.detail.relevant !== undefined
                ? " — " + sc.detail.relevant + " of " + sc.detail.of + " chunks relevant" : "");
        if (sc.judge_model) {
          row.appendChild(document.createTextNode(" · " + sc.judge_model));
        }
        if (sc.prompt_version) {
          row.appendChild(document.createTextNode(" · prompt " + sc.prompt_version));
        }
      }
      body.appendChild(row);
      if (m === "faithfulness" && sc && sc.status === "done" &&
          sc.detail && Array.isArray(sc.detail.claims) && sc.detail.claims.length) {
        var claims = document.createElement("details");
        var claimSummary = document.createElement("summary");
        claimSummary.textContent = "Review " + sc.detail.claims.length + " claim checks";
        claims.appendChild(claimSummary);
        var claimList = document.createElement("ul");
        claimList.className = "claim-checks";
        sc.detail.claims.forEach(function (claim) {
          var item = document.createElement("li");
          item.textContent = (claim.supported ? "Supported · " : "Unsupported · ") +
            (claim.text || "");
          claimList.appendChild(item);
        });
        claims.appendChild(claimList);
        body.appendChild(claims);
      }
    });
    if (d.scoring_error && d.scoring_status !== "error" && d.scoring_status !== "overloaded") {
      var partialError = document.createElement("p");
      partialError.className = "judge-line metric-error";
      partialError.textContent = "Scoring warning: " + d.scoring_error;
      body.appendChild(partialError);
    }

  }

  // --- live SSE + polling fallback ---
  var sseDown = false, pollTimer = null;
  function setConn(stateName, txt) {
    var live = stateName === "is-live";
    var connection = $("eval-connection");
    connection.className = "status " + (live ? "status-ok" : "status-error");
    connection.textContent = live ? "Live · connected" : txt;
    $("eval-system-dot").className = "eval-system-dot " + (live ? "ok" : "warn");
    $("eval-system-status").textContent = live ? "API operational" : txt;
  }
  async function subscribe() {
    try {
      var r = await fetch(API_BASE + "/api/live/events", { headers: headers() });
      if (!r.ok || !r.body) { throw new Error("no stream"); }
      var reader = r.body.getReader();
      var dec = new TextDecoder();
      var buf = "";
      sseDown = false;
      if (pollTimer !== null) { window.clearTimeout(pollTimer); pollTimer = null; }
      setConn("is-live", "Live");
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) { throw new Error("closed"); }
        buf += dec.decode(chunk.value, { stream: true });
        var parts = buf.split("\n\n");
        buf = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var lines = parts[i].split("\n").filter(function (l) { return l.indexOf("data:") === 0; });
          if (!lines.length) { continue; }
          try {
            var evt = JSON.parse(lines.map(function (l) { return l.slice(5); }).join(""));
            onLiveEvent(evt);
          } catch (e) { /* ignore malformed */ }
        }
      }
    } catch (e) {
      sseDown = true;
      setConn("is-poll", "Polling every 3s (stream unavailable)");
      pollLiveLoop();
      // The page stays open for the app lifetime: rely on the SSE reconnect
      // backoff instead of killing and re-subscribing when the view returns.
      window.setTimeout(subscribe, Math.min(30000, 15000));
    }
  }
  function onLiveEvent(evt) {
    if (!state.active || !evt || !evt.query_id) { return; }
    refreshLive();
  }
  function pollLiveLoop() {
    if (!sseDown || !state.active) { return; }
    refreshLive();
    pollTimer = window.setTimeout(pollLiveLoop, 3000);
  }

  // =================== OFFLINE TAB ===================
  var offState = { runId: null, compareId: null, runs: [] };
  function params() { return new URLSearchParams(window.location.search); }
  function syncUrl(runId, compareId) {
    var q = new URLSearchParams(window.location.search);
    if (runId) { q.set("run", runId); } else { q.delete("run"); }
    if (compareId) { q.set("compare", compareId); } else { q.delete("compare"); }
    window.history.replaceState(null, "", window.location.pathname + (q.toString() ? "?" + q.toString() : ""));
  }
  function fillSelect(sel, runs, current, allowNone) {
    while (sel.firstChild) { sel.removeChild(sel.firstChild); }
    if (allowNone) {
      var none = document.createElement("option");
      none.value = ""; none.textContent = "—";
      sel.appendChild(none);
    }
    runs.forEach(function (r) {
      var o = document.createElement("option");
      o.value = r.id;
      o.textContent = (r.label || r.id.slice(0, 8)) + " · " + r.status;
      sel.appendChild(o);
    });
    sel.value = current || "";
  }
  async function loadRuns() {
    var q = params();
    offState.runs = (await get("/api/runs?limit=50")).runs || [];
    offState.runId = q.get("run") || (offState.runs[0] && offState.runs[0].id) || null;
    var done = offState.runs.filter(function (r) { return r.status === "completed"; });
    var prev = done.filter(function (r) { return r.id !== offState.runId; })[0];
    offState.compareId = q.get("compare") || (prev && prev.id) || null;
    fillSelect($("eval-run-sel"), offState.runs, offState.runId);
    fillSelect($("eval-cmp-sel"), offState.runs, offState.compareId, true);
    $("eval-csv").href = offState.runId ? API_BASE + "/api/runs/" + offState.runId + "/export.csv" : "#";
    var run = offState.runs.filter(function (r) { return r.id === offState.runId; })[0];
    if (!run && offState.runId) {
      offState.runId = (offState.runs[0] && offState.runs[0].id) || null;
      syncUrl(offState.runId, offState.compareId);
      run = offState.runs.filter(function (r) { return r.id === offState.runId; })[0];
    }
    if (!offState.runs.length) {
      $("eval-empty").hidden = false;
      $("eval-run-label").textContent = "No runs yet";
      $("eval-metrics-table").textContent = "";
      $("eval-lowest").textContent = "";
      $("eval-failures").textContent = "";
      return null;
    }
    $("eval-empty").hidden = true;
    var cfg = (run && run.config) || {};
    $("eval-run-label").textContent =
      "Run " + (run.label || run.id.slice(0, 8)) + " · " + (run.n_total || "?") + " questions";
    $("eval-config-chips").textContent =
      "top-k " + cfg.rerank_top_k + " · judge " + (cfg.judge_model || "off") +
      (cfg.judge_warning ? " (" + cfg.judge_warning + ")" : "");
    var strip = $("eval-status-strip");
    var st = run.display_status || run.status;
    if (st === "completed") { strip.hidden = true; }
    else {
      strip.hidden = false;
      strip.textContent = "Status: " + st + " · answered " + (run.n_answered || 0) + "/" +
        (run.n_total || "?") + ", scored " + (run.n_scored || 0) + "/" + (run.n_total || "?") +
        ". Numbers are partial until the run completes." + (run.error ? " Error: " + run.error : "");
    }
    var active = offState.runs.some(function (r) {
      return r.status === "running" || r.status === "queued";
    });
    $("eval-run-btn").disabled = active;
    var sel = offState.runs.filter(function (r) { return r.id === offState.runId; })[0];
    $("eval-cancel-btn").hidden = !(sel && (sel.status === "running" || sel.status === "queued"));
    return run;
  }
  function fmt(metric, value) {
    return window.EvalLib.fmtMetric(state.metrics, metric, value);
  }
  function showText(el, text) { el.textContent = text; }
  function deltaEl(metric, delta) {
    var info = window.EvalLib.deltaInfo(state.metrics, metric, delta);
    var s = document.createElement("span");
    s.className = "eval-delta " + info.cls;
    s.textContent = info.text;
    return s;
  }
  async function renderTable(detail, cmp) {
    var host = $("eval-metrics-table");
    while (host.firstChild) { host.removeChild(host.firstChild); }
    var table = document.createElement("table");
    table.className = "eval-table";
    var head = document.createElement("tr");
    ["Metric", "Run", "Compare", "Change", "Status"].forEach(function (t) {
      var th = document.createElement("th"); th.textContent = t; head.appendChild(th);
    });
    table.appendChild(head);
    var groups = {};
    Object.keys(state.metrics || {}).forEach(function (m) {
      var g = (state.metrics[m] || {}).group || "other";
      (groups[g] = groups[g] || []).push(m);
    });
    ["retrieval", "generation", "video", "system", "other"].forEach(function (g) {
      (groups[g] || []).forEach(function (m, i) {
        if (i === 0) {
          var gr = document.createElement("tr");
          var gd = document.createElement("td");
          gd.colSpan = 5; gd.className = "eval-group"; gd.textContent = g;
          gr.appendChild(gd); table.appendChild(gr);
        }
        var agg = (detail.aggregates || {})[m] || { value: null, n: 0 };
        var c = cmp && cmp.metrics[m] ? cmp.metrics[m] : null;
        var tr = document.createElement("tr");
        var name = document.createElement("td");
        name.textContent = (state.metrics || {})[m] && state.metrics[m].name ? state.metrics[m].name : m;
        var cur = document.createElement("td");
        showText(cur, fmt(m, agg.value) === null ? "—" : fmt(m, agg.value) + "  (n=" + agg.n + ")");
        var oth = document.createElement("td");
        var cv = c ? c.comparison.value : null;
        showText(oth, !c || fmt(m, cv) === null ? "—" : fmt(m, cv));
        var chg = document.createElement("td");
        chg.appendChild(deltaEl(m, c ? c.delta : null));
        if (c && c.delta !== null && c.within_noise) {
          var noise = document.createElement("span");
          noise.className = "hint";
          noise.textContent = " · within noise";
          chg.appendChild(noise);
        }
        var st = document.createElement("td");
        st.appendChild(pillEl(m, agg.value));
        [name, cur, oth, chg, st].forEach(function (td) { tr.appendChild(td); });
        table.appendChild(tr);
      });
    });
    host.appendChild(table);
  }
  function pillEl(metric, value) {
    var st = window.EvalLib.pillState(state.metrics, state.targets || {}, metric, value);
    var span = document.createElement("span");
    span.className = "pill " + st.cls;
    span.textContent = st.text;
    return span;
  }
  function barRow(label, value, text) {
    var row = document.createElement("div");
    row.className = "bar-row";
    var lab = document.createElement("span"); lab.textContent = label;
    var track = document.createElement("div"); track.className = "bar-track";
    var fill = document.createElement("div"); fill.className = "bar-fill";
    fill.style.width = (value === null ? 0 : Math.max(0, Math.min(1, value)) * 100) + "%";
    track.appendChild(fill);
    var val = document.createElement("span"); val.className = "bar-val";
    val.textContent = text !== undefined ? text : (value === null ? "—" : Number(value).toFixed(2));
    [lab, track, val].forEach(function (e) { row.appendChild(e); });
    return row;
  }
  function note(text) {
    var p = document.createElement("p");
    p.className = "hint"; p.textContent = text;
    return p;
  }
  async function renderSide(run) {
    if (!run) { return; }
    var lh = $("eval-lowest");
    while (lh.firstChild) { lh.removeChild(lh.firstChild); }
    var low = (await get("/api/runs/" + run.id +
      "/results?sort=answer_correctness&order=asc&limit=5")).results || [];
    if (!low.results && !low.length) { lh.appendChild(note("No results yet.")); }
    var causes;
    try { causes = (await get("/api/runs/" + run.id + "/failures")); }
    catch (e) { causes = { by_cause: {} }; }
    var fh = $("eval-failures");
    while (fh.firstChild) { fh.removeChild(fh.firstChild); }
    var entries = Object.keys(causes.by_cause || {});
    var sub = document.createElement("p");
    sub.className = "hint";
    sub.textContent = causes.total_failed
      ? "Questions below the bar (" + causes.total_failed + " of " + causes.total + ")"
      : "No failures — every scored question is above the bar.";
    fh.appendChild(sub);
    var max = Math.max.apply(null, entries.map(function (k) { return causes.by_cause[k]; }).concat([1]));
    entries.forEach(function (k) {
      fh.appendChild(barRow(k, causes.by_cause[k] / max, String(causes.by_cause[k])));
    });
    (low.results || low).forEach(function (r) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "qrow";
      var q = document.createElement("span"); q.textContent = r.question;
      var ty = document.createElement("span");
      ty.className = "hint"; ty.textContent = r.question_type || "";
      var cause = document.createElement("span");
      cause.className = "cause-tag";
      cause.textContent = r.failure_cause || "—";
      var sc = document.createElement("span");
      var v = (r.scores || {}).answer_correctness;
      sc.textContent = (v === null || v === undefined) ? "—" : Number(v).toFixed(2);
      [q, ty, cause, sc].forEach(function (e) { b.appendChild(e); });
      b.addEventListener("click", function () { openTrace(r.id); });
      lh.appendChild(b);
    });
  }
  async function openTrace(resultId) {
    var body = $("eval-drawer-body");
    while (body.firstChild) { body.removeChild(body.firstChild); }
    $("eval-drawer").hidden = false;
    var t = await get("/api/results/" + resultId).catch(function (e) { return { error: e.message }; });
    if (t.error) { body.appendChild(note("Trace failed: " + t.error)); return; }
    var h = document.createElement("h2");
    h.textContent = t.question || t.question_id;
    body.appendChild(h);
    var meta = document.createElement("p");
    meta.className = "hint";
    meta.textContent = (t.question_type || "") + " · status " + t.status +
      (t.failure_cause ? " · likely cause: " + t.failure_cause : "");
    body.appendChild(meta);
    if (t.reference_answer) {
      var ref = document.createElement("p");
      ref.textContent = "Reference: " + t.reference_answer;
      body.appendChild(ref);
    }
    var ch = document.createElement("h3");
    ch.textContent = "Retrieved chunks (" + t.chunks.length + ")";
    body.appendChild(ch);
    t.chunks.forEach(function (c, i) {
      var box = document.createElement("div");
      box.className = "eval-chunk" + (c.relevant ? " rel" : "");
      var head = document.createElement("div");
      head.textContent = "#" + (i + 1) + " " + c.video_id + " · " +
        window.EvalLib.fmtTime(c.start_s) + "–" + window.EvalLib.fmtTime(c.end_s) +
        " · score " + (c.score === null ? "—" : c.score) + (c.relevant ? " · relevant" : "");
      var sn = document.createElement("p");
      sn.className = "eval-snippet";
      sn.textContent = c.text || "";
      box.appendChild(head); box.appendChild(sn);
      body.appendChild(box);
    });
    if (t.answer) {
      var ah = document.createElement("h3");
      ah.textContent = "Answer";
      var ap = document.createElement("p");
      ap.className = "eval-snippet";
      ap.textContent = t.answer;
      body.appendChild(ah); body.appendChild(ap);
    }
    var shh = document.createElement("h3");
    shh.textContent = "Scores";
    body.appendChild(shh);
    t.scores.forEach(function (s) {
      var p = document.createElement("p");
      p.className = "hint";
      var d = s.detail || {};
      p.textContent = s.metric + ": " +
        (s.value === null ? "— (not computed)" : String(Number(s.value).toFixed(2))) +
        (d.reasoning ? " — " + d.reasoning : "") +
        (s.judge_model ? " [" + s.judge_model + "]" : "");
      body.appendChild(p);
    });
  }
  $("eval-drawer-close").addEventListener("click", function () {
    $("eval-drawer").hidden = true;
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { $("eval-drawer").hidden = true; }
  });
  $("eval-cancel-btn").addEventListener("click", async function () {
    if (!offState.runId) { return; }
    try {
      var r = await fetch(API_BASE + "/api/runs/" + offState.runId + "/cancel", {
        method: "POST", headers: headers()
      });
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      await refreshOffline();
    } catch (err) {
      window.alert("Cancel failed: " + err.message);
    }
  });
  $("eval-run-btn").addEventListener("click", async function () {
    var path = window.prompt("Golden set file path:", "backend\\eval\\golden_set.jsonl");
    if (path === null) { return; }
    var label = window.prompt("Run label:", "dashboard run");
    if (label === null) { return; }
    try {
      var r = await fetch(API_BASE + "/api/runs", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ dataset_path: path, label: label })
      });
      if (!r.ok) {
        var e = await r.json().catch(function () { return {}; });
        throw new Error("HTTP " + r.status + " " + (e.message || ""));
      }
      await waitForRun((await r.json()).run_id);
      await refreshOffline();
    } catch (err) {
      window.alert("Run failed to start: " + err.message);
    }
  });
  async function waitForRun(runId) {
    for (var i = 0; i < 10; i++) {
      try {
        var r = await fetch(API_BASE + "/api/runs/" + runId, { headers: headers() });
        if (r.ok) { return; }
      } catch (e) { /* retry */ }
      await new Promise(function (res) { window.setTimeout(res, 500); });
    }
  }
  $("eval-run-sel").addEventListener("change", function () {
    offState.runId = this.value || null;
    syncUrl(offState.runId, offState.compareId);
    refreshOffline();
  });
  $("eval-cmp-sel").addEventListener("change", function () {
    offState.compareId = this.value || null;
    syncUrl(offState.runId, offState.compareId);
    refreshOffline();
  });
  async function refreshOffline() {
    if (!state.active) { return; }
    try {
      var run = await loadRuns();
      if (!run && !offState.runs.length) { return; }
      var detail = await get("/api/runs/" + offState.runId);
      var cmp = offState.compareId
        ? await get("/api/runs/" + offState.runId + "/compare/" + offState.compareId).catch(function () { return null; })
        : null;
      $("eval-metrics-table").textContent = "";
      await renderTable(detail, cmp);
      await renderSide(run);
    } catch (e) {
      $("eval-metrics-table").textContent = "Load failed: " + e.message;
    }
  }

  // =================== tab control ===================
  // The app sidebar's "Evals" submenu calls EvalDashboard.show(tab). Both
  // dashboards live inside #view-eval; only one shows at a time.
  function applyTab() {
    var live = state.currentTab !== "offline";
    $("page-live").hidden = !live;
    $("page-offline").hidden = live;
    $("eval-page-title").textContent = live ? "Live evaluation" : "Golden dataset evaluation";
    // The subtitle was removed to widen the dashboard; guard the write so a
    // missing node never aborts tab switching.
    var subtitle = $("eval-page-subtitle");
    if (subtitle) {
      subtitle.textContent = live
        ? "Inspect retrieval and answer quality for real user queries."
        : "Validate answer quality against reviewed reference answers.";
    }
    if (!live) { refreshOffline(); }
  }

  async function boot() {
    try {
      await get("/api/health");
      var conn = $("eval-connection");
      conn.textContent = "API operational";
      conn.className = "status status-ok";
      $("eval-system-dot").className = "eval-system-dot ok";
      $("eval-system-status").textContent = "API operational";
    } catch (e) {
      var conn2 = $("eval-connection");
      conn2.textContent = "Backend: unreachable (" + e.message + ")";
      conn2.className = "status status-error";
      $("eval-system-dot").className = "eval-system-dot warn";
      $("eval-system-status").textContent = "API unavailable";
      setSystemQueue(e.message);
    }
    try {
      var cfg = await get("/api/config/dashboard");
      state.metrics = cfg.metrics;
      state.targets = cfg.targets;
    } catch (e) { state.metrics = null; state.targets = null; }
    await refreshLive();
    subscribe();
    applyTab();
  }

  window.EvalDashboard = {
    // Called by app.js: show("live") or show("golden") when the main view
    // becomes visible. Boots once, on first use.
    show: function (tab) {
      // The app sidebar passes "golden"; the golden-set dashboard's internal
      // state uses "offline" (as on the standalone /eval page).
      state.currentTab = (tab === "offline" || tab === "golden") ? "offline" : "live";
      state.active = true;
      applyTab();
      if (!state.booting) {
        // First open: load health/config, the live feed and the stream.
        state.booting = true;
        boot().catch(function () { /* status pill already reports it */ });
      } else if (sseDown) {
        // Returning to a booted dashboard: resume the polling fallback.
        pollLiveLoop();
      }
    },
    // Called by app.js when the user navigates to another view: stop the
    // polling fallback (the stream stays connected but events are ignored
    // while the dashboard is hidden).
    pause: function () {
      state.active = false;
      if (pollTimer !== null) { window.clearTimeout(pollTimer); pollTimer = null; }
    }
  };
})();
