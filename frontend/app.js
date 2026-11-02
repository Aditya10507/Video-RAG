/*
 * Video RAG - browser client.
 *
 * The page uses four backend calls and nothing else:
 *
 *   GET  /health              is the API up, and which backends are active
 *   POST /ingest              start indexing a playlist, returns a job id
 *   GET  /jobs/<job_id>       poll that job until it finishes
 *   POST /ask                 ask one question about one indexed course
 *
 * Plain JavaScript on purpose: no framework, no build step and no packages,
 * so the whole client is three files that can be read in ten minutes.
 */

// --- Configuration ---------------------------------------------------------

// The page is normally served by the API itself, so requests go to the same
// origin and no CORS setup is needed. Opening the file straight from disk
// falls back to the local development server.
var API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";

var COURSES_STORAGE_KEY = "video-rag.courses";
var API_KEY_STORAGE_KEY = "video-rag.api-key";
var POLL_INTERVAL_MS = 1500;
var MAX_POLL_ATTEMPTS = 2400; // roughly one hour at the interval above

// One CSS class per answer status, so styling stays in the stylesheet.
var BADGE_CLASS = {};
BADGE_CLASS.answered = "badge badge-ok";
BADGE_CLASS.partial = "badge badge-warn";
BADGE_CLASS.not_covered = "badge badge-refuse";

// Same list as the answer-box picker from the translation feature
// (session 2026-10-03): the picker now lives on every answer bubble.
var TRANSLATE_LANGUAGES = ["English", "Hindi", "Spanish", "French", "German",
  "Portuguese", "Arabic", "Japanese", "Chinese"];
var TRANSLATE_SVG = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12.87 15.07 10.33 12.56l.03-.03a17.52 17.52 0 0 0 3.71-6.3H17V4h-7V2H8v2H1v2h11.17a15.62 15.62 0 0 1-3 4.92A15.7 15.7 0 0 1 7 7H5a17.7 17.7 0 0 0 2.83 5.33L2.5 17.58 3.92 19l5.25-5.25 3.27 3.27.43-1.95ZM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12Zm-2.62 7 1.62-4.33L19.12 17h-3.24Z"></path></svg>';
var SPEAKER_SVG = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3Zm13.5 3a4.5 4.5 0 0 0-2.5-4.03v8.05A4.5 4.5 0 0 0 16.5 12ZM14 3.23v2.06a7 7 0 0 1 0 13.42v2.06a9 9 0 0 0 0-17.54Z"></path></svg>';


// --- Small helpers ---------------------------------------------------------

function el(id) {
  return document.getElementById(id);
}

function sleep(milliseconds) {
  return new Promise(function (resolve) {
    window.setTimeout(resolve, milliseconds);
  });
}

function removeChildren(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

// Text is always written with textContent, never innerHTML, so a video title
// or transcript excerpt can never inject markup into the page.
function setStatus(node, message, kind) {
  node.hidden = false;
  node.textContent = message;
  node.className = kind ? "status status-" + kind : "status";
}


// --- API access ------------------------------------------------------------

// The API-key input was removed from the page. The key (if a protected
// deployment needs one) is still read from session storage when present, so
// authenticated setups keep working without any visible UI.
function getApiKey() {
  var input = el("api-key");
  if (input && input.value) {
    var fromInput = input.value.trim();
    if (fromInput) {
      return fromInput;
    }
  }
  try {
    return (window.sessionStorage.getItem(API_KEY_STORAGE_KEY) || "").trim();
  } catch (error) {
    return "";
  }
}

// Every request goes through here, so authentication, JSON encoding and error
// handling exist in exactly one place.
async function request(path, method, body) {
  var headers = {};
  headers["Content-Type"] = "application/json";

  var apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }

  var options = {};
  options.method = method || "GET";
  options.headers = headers;
  if (body) {
    options.body = JSON.stringify(body);
  }

  var response = await fetch(API_BASE + path, options);

  var payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }

  // The backend returns one error shape for every failure: code and message.
  if (!response.ok) {
    var detail = payload && payload.message ? payload.message : "HTTP " + response.status;
    throw new Error(detail);
  }
  return payload;
}


// --- Health badge ----------------------------------------------------------

async function refreshHealth() {
  var badge = el("health");
  if (!badge) {
    return;
  }
  try {
    var health = await request("/health");
    badge.textContent = "API ok - vectors: " + health.vector_backend;
    badge.className = "badge badge-ok";
  } catch (error) {
    badge.textContent = "API unreachable";
    badge.className = "badge badge-refuse";
  }
}


// --- Remembered course ids -------------------------------------------------
// Course ids are awkward to retype, so successful ingestions are kept in the
// browser and offered as suggestions.

function loadCourses() {
  try {
    var stored = window.localStorage.getItem(COURSES_STORAGE_KEY);
    var parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function rememberCourse(courseId) {
  if (!courseId) {
    return;
  }
  var courses = loadCourses().filter(function (item) {
    return item !== courseId;
  });
  courses.unshift(courseId);
  window.localStorage.setItem(COURSES_STORAGE_KEY, JSON.stringify(courses.slice(0, 10)));
  renderCourseOptions();
}

function renderCourseOptions() {
  var list = el("known-courses");
  removeChildren(list);
  loadCourses().forEach(function (courseId) {
    var option = document.createElement("option");
    option.value = courseId;
    list.appendChild(option);
  });
}


// --- Step 1: indexing a course ---------------------------------------------

async function handleIngest(event) {
  event.preventDefault();

  var button = el("ingest-button");
  var url = el("ingest-url").value.trim();

  button.disabled = true;
  el("progress").hidden = false;
  el("progress-bar").style.width = "0%";
  setStatus(el("ingest-status"), "Reading the playlist and starting the job...");

  try {
    var body = {};
    body.url = url;
    body.force = el("ingest-force").checked;

    var job = await request("/ingest", "POST", body);
    var finished = await waitForJob(job.job_id);

    if (finished.status === "failed") {
      setStatus(el("ingest-status"), "Indexing failed: " + (finished.error || "unknown error"), "error");
      return;
    }

    var report = finished.report || {};
    var courseId = finished.course_id || report.course_id || "";

    setStatus(
      el("ingest-status"),
      "Indexed " + (report.indexed || 0) + " of " + (report.requested || 0) + " videos, " +
      (report.chunk_count || 0) + " chunks stored" +
      (report.failed ? ", " + report.failed + " failed" : "") +
      ". Course id: " + courseId,
      "ok"
    );

    el("course-id").value = courseId;
    el("video-id").value = "";
    rememberCourse(courseId);
    loadLibrary();
    // Chunking finished: open the Ask column beside the index card.
    // Paste of an already-indexed link reopens its previous chat.
    revealAsk();
    showView("home");
    if (courseId) {
      loadChat(courseId);
    }
  } catch (error) {
    setStatus(el("ingest-status"), error.message, "error");
  } finally {
    button.disabled = false;
  }
}

// Progress is polled rather than streamed. The API also exposes a server sent
// events endpoint, but a browser EventSource cannot send an API key header,
// so polling keeps the client working on a protected deployment.
async function waitForJob(jobId) {
  var attempts = 0;

  while (attempts < MAX_POLL_ATTEMPTS) {
    var job = await request("/jobs/" + encodeURIComponent(jobId));
    updateProgress(job);

    if (job.status === "succeeded" || job.status === "failed") {
      return job;
    }

    attempts += 1;
    await sleep(POLL_INTERVAL_MS);
  }

  throw new Error("Indexing is taking longer than expected. Check the server logs.");
}

function updateProgress(job) {
  var total = job.total || 0;
  var completed = job.completed || 0;
  var percent;
  if (total > 0) {
    percent = Math.round((completed / total) * 100);
  } else if (job.status === "running" && completed > 0) {
    // Older backends never publish total; show activity instead of a stuck 5%.
    percent = 50;
  } else {
    percent = 5;
  }
  // A succeeded job is always a full bar, even if some videos failed partway.
  if (job.status === "succeeded") {
    percent = 100;
  }

  el("progress-bar").style.width = percent + "%";

  if (job.status === "running" && total > 0) {
    setStatus(el("ingest-status"), "Indexing video " + completed + " of " + total + "...");
  }
}


// --- Smooth answer streaming -------------------------------------------------
// Two modes, same UI:
//  1. True token streaming when the API offers POST /ask/stream (SSE).
//  2. Simulated smooth reveal when only POST /ask exists (current backend).
// Both are driven by requestAnimationFrame so the main thread never blocks
// and the stream can never "get stuck" on a slow timer.

var activeAskToken = 0;
var activeStreamRaf = 0;
var originalAnswerText = "";
var activeTranslationToken = 0;

function prefersReducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (error) {
    return false;
  }
}

function stopActiveStream() {
  activeAskToken += 1;
  if (activeStreamRaf) {
    cancelAnimationFrame(activeStreamRaf);
    activeStreamRaf = 0;
  }
}

function resetAnswerTranslation(answer) {
  activeTranslationToken += 1;
  originalAnswerText = answer || "";
  var tools = el("translation-tools");
  var language = el("translation-language");
  var button = el("translate-button");
  var status = el("translation-status");
  tools.hidden = !originalAnswerText;
  language.value = "";
  language.disabled = false;
  button.disabled = true;
  status.hidden = true;
  status.textContent = "";
}

async function translateAnswer() {
  var language = el("translation-language").value;
  if (!originalAnswerText || !language) {
    return;
  }

  var button = el("translate-button");
  var selector = el("translation-language");
  var status = el("translation-status");
  var token = ++activeTranslationToken;
  button.disabled = true;
  selector.disabled = true;
  el("answer-text").textContent = originalAnswerText;
  setStatus(status, "Translating answer to " + language + "...");

  try {
    var result = await request("/translate", "POST", {
      answer: originalAnswerText,
      target_language: language
    });
    if (token === activeTranslationToken) {
      el("answer-text").textContent = result.translated_answer;
      setStatus(status, "Translated to " + result.target_language, "ok");
    }
  } catch (error) {
    if (token === activeTranslationToken) {
      setStatus(status, error.message, "error");
    }
  } finally {
    if (token === activeTranslationToken) {
      selector.disabled = false;
      button.disabled = !selector.value;
    }
  }
}

function updateTranslationButton() {
  el("translate-button").disabled =
    !originalAnswerText || !el("translation-language").value;
}

function setStreamingUI(on) {
  // Graphics removed: streaming updates text only, no visual effects.
}

// Reveal fullText progressively. Duration adapts to length (capped) so short
// answers feel alive and long answers never crawl. Snaps to word boundaries
// to avoid mid-word flicker. Resolves when done or superseded.
function streamTextSmooth(node, fullText, token) {
  return new Promise(function (resolve) {
    if (!fullText) {
      node.textContent = "";
      resolve();
      return;
    }
    if (prefersReducedMotion()) {
      node.textContent = fullText;
      resolve();
      return;
    }
    var len = fullText.length;
    var targetMs = Math.min(4500, Math.max(900, len * 4));
    var start = performance.now();
    var shown = 0;

    function frame(now) {
      if (token !== activeAskToken) {
        resolve();
        return;
      }
      var elapsed = now - start;
      var progress = Math.min(1, elapsed / targetMs);
      // Ease-out: fast start, gentle landing. Pure math, no timers to clog.
      var eased = 1 - Math.pow(1 - progress, 2);
      var want = Math.floor(eased * len);
      if (want > shown) {
        // Snap to the previous space so words pop in whole.
        var cut = want;
        if (want < len && fullText[want] !== " " && fullText[want] !== "\n") {
          var nextSpace = fullText.indexOf(" ", want);
          var prevSpace = fullText.lastIndexOf(" ", want);
          if (prevSpace > shown) {
            cut = prevSpace;
          } else if (nextSpace !== -1 && nextSpace - shown < 40) {
            cut = nextSpace;
          }
        }
        shown = Math.max(shown + 1, cut);
        node.textContent = fullText.slice(0, shown);
        // Keep the tail visible while streaming without yanking the page.
        try {
          node.scrollIntoView({ block: "nearest" });
        } catch (error) {
          /* ignore */
        }
      }
      if (progress < 1 && shown < len) {
        activeStreamRaf = requestAnimationFrame(frame);
      } else {
        node.textContent = fullText;
        resolve();
      }
    }
    activeStreamRaf = requestAnimationFrame(frame);
  });
}

// Try true SSE streaming. Backend contract (when implemented):
//   POST /ask/stream  Accept: text/event-stream
//   event lines:  data: {"token": "..."}  ...  data: {"result": {...final /ask payload...}}
// Throws on any non-SSE response so the caller falls back to POST /ask.
async function tryTrueStream(body, token, onToken) {
  var headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
  var apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  var response = await fetch(API_BASE + "/ask/stream", {
    method: "POST",
    headers: headers,
    body: JSON.stringify(body)
  });
  var ctype = (response.headers.get("content-type") || "").toLowerCase();
  if (!response.ok || ctype.indexOf("text/event-stream") === -1) {
    throw new Error("no-stream");
  }
  var reader = response.body.getReader();
  var decoder = new TextDecoder();
  var buf = "";
  var finalResult = null;
  for (;;) {
    if (token !== activeAskToken) {
      try {
        reader.cancel();
      } catch (error) {
        /* ignore */
      }
      return null;
    }
    var read = await reader.read();
    if (read.done) {
      break;
    }
    buf += decoder.decode(read.value, { stream: true });
    var parts = buf.split("\n");
    buf = parts.pop();
    for (var i = 0; i < parts.length; i++) {
      var line = parts[i].trim();
      if (line.indexOf("data:") !== 0) {
        continue;
      }
      var data = line.slice(5).trim();
      if (data === "[DONE]") {
        break;
      }
      try {
        var evt = JSON.parse(data);
        if (evt.token && onToken) {
          onToken(evt.token);
        }
        if (evt.result) {
          finalResult = evt.result;
        }
      } catch (error) {
        /* keep-alive line, ignore */
      }
    }
    if (finalResult) {
      break;
    }
  }
  if (!finalResult) {
    throw new Error("no-stream-result");
  }
  return finalResult;
}


// --- Chat send: the input IS the ask box -------------------------------------

async function handleAsk(event) {
  event.preventDefault();

  stopActiveStream();
  var token = activeAskToken + 1;
  activeAskToken = token;

  var button = el("ask-button");
  button.disabled = true;

  var courseId = el("course-id").value.trim();
  var videoId = el("video-id").value.trim();
  if (!courseId) {
    setStatus(el("ask-status"), "Index a course first, then ask.", "error");
    button.disabled = false;
    return;
  }
  var questionText = el("question").value.trim();
  if (!questionText) {
    button.disabled = false;
    return;
  }

  try {
    var body = {};
    body.question = questionText;
    body.course_id = courseId;
    if (videoId) {
      body.video_id = videoId;
    }

    if (body.course_id !== currentChatCourse) {
      await loadChat(body.course_id);
    }
    // Optimistic user bubble + typing indicator; the assistant bubble
    // streams into place below.
    el("question").value = "";
    el("chat-thread").appendChild(userBubbleNode(questionText));
    scrollChat();
    setTyping(true);
    el("ask-status").hidden = true;

    // Hidden latest-answer nodes keep streaming/translation/player logic
    // reusable; the visible bubble mirrors them.
    el("answer-message").textContent = "";
    el("answer-text").textContent = "";
    el("answer-score").textContent = "";
    resetAnswerTranslation("");
    setStreamingUI(true);

    var holder = el("chat-thread");
    var pending = assistantBubbleNode({ answer: "" });
    pending.textNode.textContent = "";
    holder.appendChild(pending.row);
    scrollChat();

    var streamedText = "";
    var result = null;

    try {
      result = await tryTrueStream(body, token, function (tok) {
        if (token !== activeAskToken) {
          return;
        }
        streamedText += tok;
        pending.textNode.textContent = streamedText;
        scrollChat();
      });
      if (token !== activeAskToken) {
        return;
      }
      setTyping(false);
    } catch (streamError) {
      // Fallback to plain /ask with the smooth reveal into the bubble.
      result = await request("/ask", "POST", body);
      if (token !== activeAskToken) {
        return;
      }
      setTyping(false);
      streamedText = result.answer || "";
      if (streamedText && !prefersReducedMotion()) {
        await streamTextSmooth(pending.textNode, streamedText, token);
        if (token !== activeAskToken) {
          return;
        }
      } else {
        pending.textNode.textContent = streamedText;
      }
    }

    el("ask-status").hidden = true;
    rememberCourse(body.course_id);
    await renderAnswerStreamed(result, token, streamedText);
    // Swap the pending bubble for the final one (badge + citation link).
    result.question = questionText;
    holder.removeChild(pending.row);
    appendAssistantTurn(result);
  } catch (error) {
    if (token === activeAskToken) {
      stopActiveStream();
      setTyping(false);
      setStatus(el("ask-status"), error.message, "error");
    }
  } finally {
    if (token === activeAskToken) {
      button.disabled = false;
    }
  }
}


// --- Sidebar views -----------------------------------------------------------
// Home is the working page (index left, ask right). Library is the picker
// page (indexed courses as a list). The course-id and video-id fields are
// the single source of truth: the library writes them, Ask reads them.

function showView(name) {
  el("view-home").hidden = name !== "home";
  el("view-library").hidden = name !== "library";

  var home = el("nav-home");
  var library = el("nav-library");
  home.classList.toggle("is-active", name === "home");
  library.classList.toggle("is-active", name === "library");
  if (name === "home") {
    home.setAttribute("aria-current", "page");
    library.removeAttribute("aria-current");
  } else {
    library.setAttribute("aria-current", "page");
    home.removeAttribute("aria-current");
  }

  // Leaving the evaluation view restores the regular app chrome.
  el("view-eval").hidden = true;
  document.body.classList.remove("eval-active");
  clearEvalNavState();
  if (window.EvalDashboard) {
    window.EvalDashboard.pause();
  }
}

// --- Evals view ---------------------------------------------------------------
// The sidebar's Evals entry expands into a mini sidebar with one item per
// dashboard; each shows its own dashboard inside the main area. The eval
// console is height-constrained, so body gets the eval-active class while
// it is open (see eval.css).

function showEvalView(tab) {
  el("view-home").hidden = true;
  el("view-library").hidden = true;
  el("view-eval").hidden = false;
  document.body.classList.add("eval-active");

  el("nav-home").classList.remove("is-active");
  el("nav-home").removeAttribute("aria-current");
  el("nav-library").classList.remove("is-active");
  el("nav-library").removeAttribute("aria-current");
  var live = el("nav-eval-live");
  var golden = el("nav-eval-golden");
  live.classList.toggle("is-active", tab !== "golden");
  golden.classList.toggle("is-active", tab === "golden");
  expandEvalSubnav(true);

  if (window.EvalDashboard) {
    window.EvalDashboard.show(tab);
  }
}

function clearEvalNavState() {
  var live = el("nav-eval-live");
  var golden = el("nav-eval-golden");
  if (live) { live.classList.remove("is-active"); }
  if (golden) { golden.classList.remove("is-active"); }
}

function expandEvalSubnav(open) {
  var subnav = el("eval-subnav");
  var button = el("nav-evals");
  if (!subnav || !button) { return; }
  subnav.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
}

// The chat panel is always visible: "reveal" just refreshes the scope label.
function revealAsk() {
  updateChatScope();
}

function updateChatScope() {
  var courseId = (el("course-id") && el("course-id").value.trim()) || currentChatCourse || "";
  var videoId = (el("video-id") && el("video-id").value.trim()) || "";
  var label = el("chat-course-label");
  if (label) {
    label.textContent = courseId
      ? (videoId ? courseId + " · " + videoId : courseId)
      : "No course selected — index a link or pick from Library";
    label.title = label.textContent;
  }
  var title = el("chat-title");
  if (title && courseId) {
    title.textContent = "Assistant";
  }
  var x = el("chat-scope-clear");
  if (x) {
    x.hidden = !videoId;
  }
}

// Single source of truth for course selection: hidden state + scope label
// + thread load. Library and ingest both go through here.
function selectCourse(courseId, videoId) {
  if (el("course-id")) {
    el("course-id").value = courseId || "";
  }
  if (el("video-id")) {
    el("video-id").value = videoId || "";
  }
  if (courseId) {
    rememberCourse(courseId);
  }
  updateChatScope();
  showView("home");
  if (courseId) {
    loadChat(courseId);
  }
}

function goHomeWithSelection(courseId, videoId) {
  selectCourse(courseId, videoId);
}


// --- Chat thread: single thread per course, stored in the backend DB --------
// Bubbles follow the reference layout: user right/blue, assistant left/white
// with avatar. Each assistant bubble carries its status badge + timestamp
// link, which seeks the shared player on the left.

var currentChatCourse = "";

function scrollChat() {
  var holder = el("chat-thread");
  if (holder) {
    holder.scrollTop = holder.scrollHeight;
  }
}

function setTyping(on) {
  var t = el("chat-typing");
  if (t) {
    t.hidden = !on;
  }
  if (on) {
    scrollChat();
  }
}

function userBubbleNode(text) {
  var row = document.createElement("div");
  row.className = "bubble-row user";
  var b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  row.appendChild(b);
  return row;
}

function assistantBubbleNode(turn) {
  var row = document.createElement("div");
  row.className = "bubble-row assistant";

  var avatar = document.createElement("span");
  avatar.className = "assistant-avatar sm";
  avatar.setAttribute("aria-hidden", "true");
  row.appendChild(avatar);

  var b = document.createElement("div");
  b.className = "bubble";

  var text = document.createElement("div");
  text.className = "bubble-text";
  text.textContent = (turn && (turn.answer || turn.message)) || "";
  b.appendChild(text);

  if (turn && turn.status) {
    var meta = document.createElement("div");
    meta.className = "bubble-meta";
    var badge = document.createElement("span");
    badge.textContent = turn.status.replace("_", " ");
    badge.className = BADGE_CLASS[turn.status] || "badge";
    meta.appendChild(badge);
    var cite = turn.primary_source;
    if (cite) {
      var link = document.createElement("button");
      link.type = "button";
      link.className = "cite-link";
      link.textContent = (cite.video_title || cite.video_id) + " at " + cite.timestamp_label;
      link.addEventListener("click", function () {
        playCitation(cite);
      });
      meta.appendChild(link);
    }
    b.appendChild(meta);
    // Translate icon on every generated answer. Always translates from the
    // original text, so switching languages never chains translations.
    if (turn.answer) {
      var tbtn = document.createElement("button");
      tbtn.type = "button";
      tbtn.className = "translate-button bubble-translate";
      tbtn.title = "Translate answer";
      tbtn.setAttribute("aria-label", "Translate answer");
      tbtn.innerHTML = TRANSLATE_SVG;
      var trow = translateRowNode(turn.answer, text);
      tbtn.addEventListener("click", function () {
        trow.hidden = !trow.hidden;
      });
      meta.appendChild(tbtn);
      // Speaker button, next to translate: reads whatever the bubble shows
      // (original or translated) through Gnani TTS.
      var sbtn = document.createElement("button");
      sbtn.type = "button";
      sbtn.className = "translate-button bubble-translate voice-speaker";
      sbtn.title = "Read answer aloud";
      sbtn.setAttribute("aria-label", "Read answer aloud");
      sbtn.innerHTML = SPEAKER_SVG;
      sbtn.addEventListener("click", function () {
        speakAnswer(text, sbtn);
      });
      meta.appendChild(sbtn);
      b.appendChild(trow);
    }
  }
  row.appendChild(b);
  return { row: row, textNode: text, bubble: b };
}

// Inline language picker for one bubble. Stale responses (slow request
// overtaken by a newer pick) are dropped via the per-bubble token.
function translateRowNode(originalText, textNode) {
  var wrap = document.createElement("div");
  wrap.className = "translate-row";
  wrap.hidden = true;

  var select = document.createElement("select");
  select.setAttribute("aria-label", "Translation language");
  var placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Translate to...";
  select.appendChild(placeholder);
  TRANSLATE_LANGUAGES.forEach(function (lang) {
    var option = document.createElement("option");
    option.value = lang;
    option.textContent = lang;
    select.appendChild(option);
  });

  var status = document.createElement("span");
  status.className = "translate-status";

  var token = 0;
  select.addEventListener("change", async function () {
    var lang = select.value;
    if (!lang) {
      textNode.textContent = originalText;
      status.textContent = "";
      return;
    }
    var my = ++token;
    select.disabled = true;
    status.textContent = "Translating to " + lang + "...";
    try {
      var result = await request("/translate", "POST", {
        answer: originalText,
        target_language: lang
      });
      if (my === token) {
        textNode.textContent = result.translated_answer;
        status.textContent = "Translated to " + result.target_language;
      }
    } catch (error) {
      if (my === token) {
        status.textContent = error.message;
      }
    } finally {
      if (my === token) {
        select.disabled = false;
      }
    }
  });

  wrap.appendChild(select);
  wrap.appendChild(status);
  return wrap;
}

function renderChatThread(turns) {
  var holder = el("chat-thread");
  if (!holder) {
    return;
  }
  removeChildren(holder);
  if (!turns || turns.length === 0) {
    var greet = document.createElement("div");
    greet.className = "bubble-row assistant";
    var avatar = document.createElement("span");
    avatar.className = "assistant-avatar sm";
    avatar.setAttribute("aria-hidden", "true");
    greet.appendChild(avatar);
    var gb = document.createElement("div");
    gb.className = "bubble";
    gb.textContent = "Hi! I'm your assistant. How can I help today?";
    greet.appendChild(gb);
    holder.appendChild(greet);
    return;
  }
  turns.forEach(function (turn) {
    holder.appendChild(userBubbleNode(turn.question || ""));
    holder.appendChild(assistantBubbleNode(turn).row);
  });
  scrollChat();
}

// The user bubble is added optimistically at send time, so this appends
// the assistant side only. (Appending a full turn here duplicated the
// question: once optimistic, once with the answer.)
function appendAssistantTurn(turn) {
  var holder = el("chat-thread");
  if (!holder) {
    return;
  }
  holder.appendChild(assistantBubbleNode(turn).row);
  scrollChat();
}

async function loadChat(courseId) {
  currentChatCourse = courseId || "";
  updateChatScope();
  if (!courseId) {
    renderChatThread([]);
    return;
  }
  try {
    var payload = await request("/courses/" + encodeURIComponent(courseId) + "/messages?limit=100");
    renderChatThread((payload && payload.messages) || []);
  } catch (error) {
    renderChatThread([]);
  }
}

async function clearChat() {
  var courseId = (el("course-id") && el("course-id").value.trim()) || currentChatCourse;
  if (!courseId) {
    return;
  }
  try {
    await request("/courses/" + encodeURIComponent(courseId) + "/messages", "DELETE");
  } catch (error) {
    setStatus(el("ask-status"), error.message, "error");
    return;
  }
  renderChatThread([]);
}


// --- Chat resize: drag the panel's left border, like the sidebar resizer --
// Width persists across reloads. Double-click toggles default <-> widest.

var CHAT_WIDTH_KEY = "video-rag.chat-width";
var CHAT_DEFAULT_PX = 400;
var CHAT_MIN_PX = 340;

function chatMaxWidth() {
  var grid = document.querySelector(".home-grid");
  var total = grid ? grid.getBoundingClientRect().width : window.innerWidth;
  return Math.max(CHAT_MIN_PX + 40, total - 300);
}

function setChatWidth(pixels) {
  var max = chatMaxWidth();
  var clamped = Math.min(max, Math.max(CHAT_MIN_PX, pixels));
  document.documentElement.style.setProperty("--chat-width", clamped + "px");
  try {
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(clamped));
  } catch (error) {
    /* private mode: layout still works, it just won't persist */
  }
}

function initChatResizer() {
  try {
    var saved = parseInt(window.localStorage.getItem(CHAT_WIDTH_KEY), 10);
    if (saved >= CHAT_MIN_PX && saved <= 1600) {
      document.documentElement.style.setProperty("--chat-width", saved + "px");
    }
  } catch (error) {
    /* ignore */
  }

  var resizer = el("chat-resizer");
  if (!resizer) {
    return;
  }
  var shell = document.getElementById("app-shell");

  resizer.addEventListener("dblclick", function () {
    var current = document.getElementById("chat-panel").getBoundingClientRect().width;
    if (current >= chatMaxWidth() - 2) {
      setChatWidth(CHAT_DEFAULT_PX);
    } else {
      setChatWidth(chatMaxWidth());
    }
  });

  resizer.addEventListener("pointerdown", function (startEvent) {
    startEvent.preventDefault();
    resizer.setPointerCapture(startEvent.pointerId);
    shell.classList.add("chat-resizing");

    var panel = document.getElementById("chat-panel");
    var startX = startEvent.clientX;
    var startWidth = panel.getBoundingClientRect().width;

    function onMove(moveEvent) {
      setChatWidth(startWidth + startX - moveEvent.clientX);
    }

    function onUp() {
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
      resizer.removeEventListener("pointercancel", onUp);
      shell.classList.remove("chat-resizing");
    }

    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
    resizer.addEventListener("pointercancel", onUp);
  });
}


// --- Library view: indexed courses as a list ---------------------------------

async function loadLibrary() {
  var holder = el("library-list");
  try {
    var payload = await request("/courses");
    removeChildren(holder);

    var courses = (payload && payload.courses) || [];
    if (courses.length === 0) {
      setStatus(el("library-status"), "Nothing indexed yet. Index a course from Home.");
      return;
    }
    el("library-status").hidden = true;

    courses.forEach(function (course) {
      holder.appendChild(courseRow(course));
    });

    // Returning users already have chunks: keep the Ask column open for them.
    if (courses.some(function (item) { return item.indexed > 0; })) {
      revealAsk();
    }
  } catch (error) {
    setStatus(el("library-status"), error.message, "error");
  }
}

function statusDot(status, indexed) {
  var dot = document.createElement("span");
  var good = typeof indexed === "number" ? indexed > 0 : status === "indexed";
  var bad = typeof indexed === "number" ? indexed === 0 : status === "failed";
  dot.className = "dot " + (good ? "dot-ok" : (bad ? "dot-bad" : "dot-warn"));
  return dot;
}

function courseRow(course) {
  var item = document.createElement("li");
  item.className = "lib-item";

  var row = document.createElement("button");
  row.type = "button";
  row.className = "lib-course";
  row.title = course.course_id;

  var title = document.createElement("span");
  title.className = "lib-title";
  title.textContent = course.title || course.course_id;

  var meta = document.createElement("span");
  meta.className = "lib-meta";
  meta.textContent =
    course.indexed + "/" + course.video_count + " videos - " +
    course.chunk_count + " chunks";

  var chev = document.createElement("span");
  chev.className = "lib-chev";
  chev.textContent = ">";
  chev.setAttribute("aria-hidden", "true");

  row.appendChild(statusDot(course.status, course.indexed));
  row.appendChild(title);
  row.appendChild(meta);
  row.appendChild(chev);

  var sub = document.createElement("ul");
  sub.className = "lib-videos";
  sub.hidden = true;

  row.addEventListener("click", function () {
    var opening = sub.hidden;
    sub.hidden = !opening;
    chev.textContent = opening ? "v" : ">";
    el("course-id").value = course.course_id;
    el("video-id").value = "";
    rememberCourse(course.course_id);
    updateChatScope();
    if (opening) {
      loadCourseVideos(course.course_id, sub);
    }
  });

  item.appendChild(row);
  item.appendChild(sub);
  return item;
}

async function loadCourseVideos(courseId, sub) {
  removeChildren(sub);

  var loading = document.createElement("li");
  loading.className = "lib-loading";
  loading.textContent = "Loading videos...";
  sub.appendChild(loading);

  try {
    var payload = await request("/courses/" + encodeURIComponent(courseId) + "/videos");
    var videos = (payload && payload.videos) || [];
    removeChildren(sub);
    refreshVideoOptions(videos);

    if (videos.length === 0) {
      var empty = document.createElement("li");
      empty.className = "lib-loading";
      empty.textContent = "No videos found.";
      sub.appendChild(empty);
      return;
    }

    videos.forEach(function (video) {
      sub.appendChild(videoRow(courseId, video));
    });
  } catch (error) {
    removeChildren(sub);
    var failed = document.createElement("li");
    failed.className = "lib-loading";
    failed.textContent = error.message;
    sub.appendChild(failed);
  }
}

function refreshVideoOptions(videos) {
  var list = el("known-videos");
  removeChildren(list);
  videos.forEach(function (video) {
    var option = document.createElement("option");
    option.value = video.video_id;
    list.appendChild(option);
  });
}

function videoRow(courseId, video) {
  var item = document.createElement("li");
  item.className = "lib-video";

  var title = document.createElement("span");
  title.className = "lib-title";
  title.textContent = video.title || video.video_id;
  title.title = video.video_id;

  var meta = document.createElement("span");
  meta.className = "lib-meta";
  meta.textContent = video.status + " - " + video.chunk_count + " chunks";

  var ask = document.createElement("button");
  ask.type = "button";
  ask.className = "lib-ask";
  ask.textContent = "Ask >";
  ask.addEventListener("click", function (event) {
    event.stopPropagation();
    goHomeWithSelection(courseId, video.video_id);
  });

  item.appendChild(statusDot(video.status));
  item.appendChild(title);
  item.appendChild(meta);
  item.appendChild(ask);
  return item;
}


// --- Step 3: rendering the answer ------------------------------------------

function renderAnswer(result) {
  stopActiveStream();

  var badge = el("answer-badge");
  badge.textContent = result.status.replace("_", " ");
  badge.className = BADGE_CLASS[result.status] || "badge";

  el("answer-score").textContent = "top score " + result.top_score;
  el("answer-message").textContent = result.message;
  el("answer-text").textContent = result.answer || "";
  resetAnswerTranslation(result.answer);

  renderSources(result, true);
}

// Streaming variant: badge + message appear instantly, answer text reveals
// smoothly. If preStreamedText is given (true SSE path), the text is already
// visible and we only finish the metadata.
async function renderAnswerStreamed(result, token, preStreamedText) {
  var badge = el("answer-badge");
  badge.textContent = result.status.replace("_", " ");
  badge.className = BADGE_CLASS[result.status] || "badge";

  el("answer-score").textContent = "top score " + result.top_score;
  el("answer-message").textContent = result.message;

  var node = el("answer-text");
  var fullText = result.answer || "";
  resetAnswerTranslation(fullText);

  if (preStreamedText !== undefined && preStreamedText !== null) {
    // True-stream path: ensure the final text is exact.
    node.textContent = fullText || preStreamedText;
  } else if (fullText) {
    node.textContent = "";
    await streamTextSmooth(node, fullText, token);
    if (token !== activeAskToken) {
      return;
    }
  } else {
    node.textContent = "";
  }

  renderSources(result, false);
}

function renderSources(result, instant) {
  // The citation row was removed from the UI: the embedded player IS the
  // citation view now, and the small caption under it names the video.
  if (result.primary_source) {
    playCitation(result.primary_source);
  } else {
    el("player").hidden = true;
    el("player-caption").textContent = "";
  }

  // On a refusal, showing the syllabus proves the refusal was a decision
  // rather than a broken search.
  var covers = result.course_covers || [];
  el("covers").hidden = covers.length === 0;

  var coversList = el("covers-list");
  removeChildren(coversList);
  covers.forEach(function (title) {
    var item = document.createElement("li");
    item.textContent = title;
    coversList.appendChild(item);
  });

  // The watch card lives below the index card: it appears only when there
  // is something to watch or a coverage list to show.
  el("watch-card").hidden = !(result.primary_source || covers.length);
}

// The timestamp is the whole point of the project: the embedded player starts
// at the second the topic is explained. The embed stays in-app, so the player
// URL must carry the page origin as Referer — served without one YouTube
// rejects the embed with "Video player configuration error" (Error 153),
// whose error card is what sends people to youtube.com.
function playCitation(citation) {
  if (!citation || !citation.video_id) {
    el("player").hidden = true;
    return;
  }

  var startSeconds = Number(citation.start_seconds || 0);
  if (!Number.isFinite(startSeconds)) {
    startSeconds = 0;
  }

  var source = "https://www.youtube.com/embed/" + encodeURIComponent(citation.video_id) +
    "?start=" + Math.max(0, Math.floor(startSeconds)) +
    "&autoplay=1&rel=0&playsinline=1";

  el("player-frame").src = source;
  el("player-caption").textContent =
    citation.video_title + " starting at " + citation.timestamp_label;
  el("player").hidden = false;
  el("player-frame").focus();
}

// Entry point for the eval dashboards: every video citation in the app stays
// in the app. It takes you to the Home watch card and plays the cited moment
// in the embedded player.
function openInAppPlayer(citation) {
  showView("home");
  if (!citation || !citation.video_id) {
    return;
  }

  el("covers").hidden = true;
  removeChildren(el("covers-list"));
  el("watch-card").hidden = false;
  playCitation(citation);
}


// --- Enterprise sidebar: collapse on one click, drag to resize -------------

var SIDEBAR_WIDTH_KEY = "video-rag.sidebar-width";
var SIDEBAR_COLLAPSED_KEY = "video-rag.sidebar-collapsed";
var SIDEBAR_MIN_PX = 200;
var SIDEBAR_MAX_PX = 440;

function setSidebarCollapsed(collapsed) {
  var shell = document.getElementById("app-shell");
  shell.classList.toggle("sidebar-collapsed", collapsed);

  var collapse = el("sidebar-collapse");
  collapse.setAttribute("aria-expanded", String(!collapsed));
  el("sidebar-expand").hidden = !collapsed;

  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (error) {
    /* private mode: layout still works, it just won't persist */
  }
}

function setSidebarWidth(pixels) {
  var clamped = Math.min(SIDEBAR_MAX_PX, Math.max(SIDEBAR_MIN_PX, pixels));
  document.documentElement.style.setProperty("--sidebar-width", clamped + "px");
  try {
    window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
  } catch (error) {
    /* ignore */
  }
}

function initSidebar() {
  var shell = document.getElementById("app-shell");

  try {
    var savedWidth = parseInt(window.localStorage.getItem(SIDEBAR_WIDTH_KEY), 10);
    if (savedWidth >= SIDEBAR_MIN_PX && savedWidth <= SIDEBAR_MAX_PX) {
      document.documentElement.style.setProperty("--sidebar-width", savedWidth + "px");
    }
  } catch (error) {
    /* ignore */
  }

  var collapsed = false;
  try {
    collapsed = window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch (error) {
    /* ignore */
  }
  setSidebarCollapsed(collapsed);

  // Single click collapse / expand.
  el("sidebar-collapse").addEventListener("click", function () {
    setSidebarCollapsed(true);
  });
  el("sidebar-expand").addEventListener("click", function () {
    setSidebarCollapsed(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !shell.classList.contains("sidebar-collapsed")) {
      setSidebarCollapsed(true);
    }
  });

  // Drag the edge to resize. Pointer events cover mouse, touch and pen.
  var resizer = el("sidebar-resizer");
  resizer.addEventListener("pointerdown", function (startEvent) {
    startEvent.preventDefault();
    resizer.setPointerCapture(startEvent.pointerId);
    shell.classList.add("sidebar-resizing");

    var startX = startEvent.clientX;
    var startWidth = document.getElementById("sidebar").getBoundingClientRect().width;

    function onMove(moveEvent) {
      setSidebarWidth(startWidth + moveEvent.clientX - startX);
    }

    function onUp() {
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
      resizer.removeEventListener("pointercancel", onUp);
      shell.classList.remove("sidebar-resizing");
    }

    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
    resizer.addEventListener("pointercancel", onUp);
  });
}


// --- Voice: spoken questions (mic -> Gnani STT) and spoken answers (TTS) ----
//
// Questions are streamed live: the browser pipes raw PCM frames through a
// backend WebSocket proxy to Gnani's realtime STT (Prisma v3.0 streaming).
// Transcripts come back per spoken phrase, so the question appears word by
// word in the chat bar, in the language it is spoken (Hindi in Hindi).
// A client-side voice-activity check stops the mic after ~3.5 s of silence
// (or ~4.5 s if nothing was ever spoken) and sends the question. If the
// stream can't be used, it falls back to record-then-upload over the REST
// endpoint. STT runs only while the mic is on; TTS only on a speaker click,
// so the free credit pool is spent one request at a time.

var voice = {
  recording: false, finalizing: false, stream: null, recorder: null, chunks: [],
  ws: null, connected: false, audioCtx: null, source: null, sink: null,
  processor: null, segments: [], partial: "", sampleRate: 48000,
  playing: null, button: null
};

// Silence detection knobs. speechRms is generous: laptop mics peak far
// above it, background hiss/keyboard taps stay below.
var VAD = {
  sampleRate: null,
  hadSpeech: false, calmMs: 0, elapsedMs: 0, loudFrames: 0,
  speechRms: 0.02, silenceRms: 0.012,
  silenceSendMs: 3500,   // stop reading and send once spoken words run out
  noSpeechStopMs: 4500   // stop and report "nothing heard" if still silent
};

// Decode + resample (properly, via OfflineAudioContext) to 16 kHz mono, then
// wrap the PCM frames in a RIFF/WAVE container.
async function recordingToWav(blob) {
  var bytes = await blob.arrayBuffer();
  var ctx = new (window.AudioContext || window.webkitAudioContext)();
  var decoded;
  try {
    decoded = await ctx.decodeAudioData(bytes);
  } finally {
    ctx.close();
  }

  if (decoded.sampleRate === 16000 && decoded.numberOfChannels === 1) {
    return pcm16ToWav(decoded.getChannelData(0), 16000);
  }
  var frames = Math.max(1, Math.ceil(decoded.duration * 16000));
  var offline = new window.OfflineAudioContext(1, frames, 16000);
  var source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  var rendered = await offline.startRendering();
  return pcm16ToWav(rendered.getChannelData(0), 16000);
}

function pcm16ToWav(samples, sampleRate) {
  var pcm = new window.Int16Array(samples.length);
  for (var i = 0; i < samples.length; i++) {
    var s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  var bytes = new Uint8Array(44 + pcm.byteLength);
  var view = new window.DataView(bytes.buffer);

  var writeAscii = function (offset, text) {
    for (var i = 0; i < text.length; i++) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);          // PCM fmt chunk size
  view.setUint16(20, 1, true);           // PCM format
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // byte rate (16-bit mono)
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, pcm.byteLength, true);
  bytes.set(new Uint8Array(pcm.buffer), 44);
  return new Blob([bytes], { type: "audio/wav" });
}

async function voiceRequest(path, init) {
  var headers = init.headers || {};
  var apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  var response = await fetch(API_BASE + path, Object.assign({}, init, { headers: headers }));
  if (!response.ok) {
    var payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    throw new Error(payload && payload.message ? payload.message : "HTTP " + response.status);
  }
  return response;
}

function micButtonBusy(busy) {
  var button = el("mic-button");
  if (!button) {
    return;
  }
  button.classList.toggle("busy", busy);
  button.disabled = busy;
}

function stopMicRecording() {
  if (voice.recorder && voice.recorder.state !== "inactive") {
    voice.recorder.stop();  // onstop below sends it
    return;
  }
  if (voice.stream) {
    voice.stream.getTracks().forEach(function (track) { track.stop(); });
    voice.stream = null;
  }
}

function voiceWsUrl() {
  var base = API_BASE || window.location.origin;
  return base.replace(/^http/, "ws") + "/voice/stream?sample_rate=" + voice.sampleRate;
}

// Silence energy analysis on the frames that are already being sent to the
// stream. Speech needs 3 loud frames (~32 ms) so a click or door slam does
// not count; then keeping quiet for VAD.silenceSendMs ends the capture.
function trackVoiceActivity(samples) {
  var v = VAD;
  var sum = 0;
  for (var i = 0; i < samples.length; i++) {
    sum += samples[i] * samples[i];
  }
  var rms = Math.sqrt(sum / samples.length);
  v.elapsedMs += (samples.length / v.sampleRate) * 1000;

  if (rms >= v.speechRms) {
    v.loudFrames += 1;
    if (v.loudFrames >= 3) {
      v.hadSpeech = true;
      v.calmMs = 0;
    }
  } else if (rms < v.silenceRms) {
    v.loudFrames = 0;
    v.calmMs += (samples.length / v.sampleRate) * 1000;
  } else {
    v.loudFrames = 0;  // between the two thresholds: keep current state
  }

  if ((v.hadSpeech && v.calmMs >= v.silenceSendMs) ||
      (!v.hadSpeech && v.elapsedMs >= v.noSpeechStopMs)) {
    finalizeVoiceCapture();
  }
}

function floatToPcm16(samples) {
  var pcm = new window.Int16Array(samples.length);
  for (var i = 0; i < samples.length; i++) {
    var s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm.buffer;
}

// Insert the newest transcript phrase where the user currently is typed/
// spoken: the settled segments become the question shown in the chat bar,
// with the still-unconfirmed partial tail appended live.
function heardSoFarText() {
  var parts = voice.segments.slice();
  if (voice.partial) {
    parts.push(voice.partial);
  }
  var text = parts.join(" ").replace(/\s+/g, " ").trim();
  var max = parseInt(el("question").maxLength, 10) || 500;
  return text.slice(0, max);
}

function openWebSocket(url) {
  return new window.Promise(function (resolve, reject) {
    var ws = new window.WebSocket(url);
    voice.ws = ws;
    voice.connected = false;
    ws.addEventListener("open", function () { resolve(ws); });
    ws.addEventListener("error", function () { reject(new Error("voice stream unavailable")); });
    ws.addEventListener("close", function () {
      if (!voice.connected) {
        reject(new Error("voice stream closed"));
      }
      voice.ws = null;
      voice.connected = false;
    });
    ws.addEventListener("message", function (event) {
      var data = null;
      try {
        data = window.JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (!data || !data.type) {
        return;
      }
      if (data.type === "connected") {
        voice.connected = true;
      } else if (data.type === "transcript" || data.type === "final_transcript") {
        // Completed utterance: settle it, drop the running partial.
        if (data.text) {
          voice.segments.push(data.text);
          voice.partial = "";
          el("question").value = heardSoFarText();
        }
      } else if (data.type === "partial_transcript") {
        // Word-by-word updates while the user is still speaking.
        voice.partial = data.text || "";
        if (voice.partial) {
          el("question").value = heardSoFarText();
        }
      } else if (data.type === "error" && !voice.segments.length) {
        setStatus(el("ask-status"), data.message || "STT error.", "error");
      }
    });
  });
}

// Live capture: PCM frames go out as they are recorded; every transcript
// segment Gnani sends lands in the chat bar immediately.
async function startVoiceStreaming(stream) {
  // AudioContext first: the real sample rate must be declared when the
  // stream handshake happens (query parameter on voiceWsUrl()).
  var ctx = new (window.AudioContext || window.webkitAudioContext)();
  voice.audioCtx = ctx;
  voice.sampleRate = ctx.sampleRate;
  VAD.sampleRate = ctx.sampleRate;
  if (ctx.state === "suspended") {
    ctx.resume();  // already inside a click handler, so this always runs
  }

  await openWebSocket(voiceWsUrl());
  if (!voice.recording || voice.finalizing) {
    throw new Error("capture already ended");  // silence timer won the race
  }

  var processor = ctx.createScriptProcessor ? ctx.createScriptProcessor(512, 1, 1) : null;
  if (!processor) {
    throw new Error("voice streaming not supported here");
  }
  voice.processor = processor;
  voice.source = ctx.createMediaStreamSource(stream);
  voice.sink = ctx.createGain();
  voice.sink.gain.value = 0;  // processor must reach destination to run; mute it

  processor.addEventListener("audioprocess", function (event) {
    var samples = event.inputBuffer.getChannelData(0);
    if (voice.connected) {
      trackVoiceActivity(samples);
      if (voice.ws && voice.ws.readyState === 1 && !voice.finalizing) {
        voice.ws.send(floatToPcm16(samples));  // 512 samples = 1024 bytes
      }
    }
  });

  voice.source.connect(processor);
  processor.connect(voice.sink);
  voice.sink.connect(ctx.destination);
}

// Tear down whatever capture state exists (WS, audio nodes, mic tracks).
function closeVoiceCapture() {
  if (voice.ws) {
    try {
      voice.ws.close();
    } catch (error) {
      /* already closed */
    }
  }
  if (voice.processor) {
    try {
      voice.processor.disconnect();
      voice.source.disconnect();
      voice.sink.disconnect();
    } catch (error) {
      /* ignore */
    }
    voice.processor = null;
    voice.source = null;
    voice.sink = null;
  }
  if (voice.audioCtx) {
    voice.audioCtx.close();
    voice.audioCtx = null;
  }
  if (voice.stream) {
    voice.stream.getTracks().forEach(function (track) { track.stop(); });
    voice.stream = null;
  }
}

// Silence path (or another mic click): commit the pending phrase, collect
// the last transcript for a moment, then stop and send the question.
async function finalizeVoiceCapture() {
  if (voice.finalizing) {
    return;
  }
  voice.finalizing = true;
  el("mic-button").classList.remove("recording");
  micButtonBusy(true);
  var status = el("ask-status");

  try {
    if (voice.ws && voice.ws.readyState === 1) {
      voice.ws.send('{"type":"commit"}');  // flush the phrase still open
    }
    await sleep(1200);  // leave Gnani time to return the final segment
  } finally {
    closeVoiceCapture();
  }

  var text = heardSoFarText();
  try {
    if (!text) {
      setStatus(status, "Nothing heard - try again closer to the mic.", "error");
      return;
    }
    el("question").value = text;
    setStatus(status, "Heard: " + text, "info");
    // Question is complete: send it without waiting for the user.
    if (el("ask-form").requestSubmit) {
      el("ask-form").requestSubmit();
    } else {
      handleAsk(new window.Event("submit", { cancelable: true }));
    }
  } finally {
    micButtonBusy(false);
    voice.recording = false;
    voice.finalizing = false;
  }
}

// One press starts, the next stops. A stream failure degrades safely to the
// record-then-upload flow against the REST endpoint.
function initVoiceInput() {
  var button = el("mic-button");
  if (!button) {
    return;
  }
  if (!window.WebSocket || !window.AudioContext) {
    button.disabled = true;
    button.title = "Voice input needs browser media support";
    return;
  }

  button.addEventListener("click", function () {
    if (voice.recording) {
      finalizeVoiceCapture();  // manual stop == silence stop
      return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      stopSpeaking();  // never talk over a new question being asked
      voice.recording = true;
      voice.finalizing = false;
      voice.stream = stream;
      voice.segments = [];
      voice.partial = "";
      // Fresh silence-tracking state per capture.
      VAD.hadSpeech = false;
      VAD.calmMs = 0;
      VAD.elapsedMs = 0;
      VAD.loudFrames = 0;
      button.classList.add("recording");
      setStatus(el("ask-status"), "Listening... pause 3 s and the question sends itself.", "info");

      startVoiceStreaming(stream).catch(function () {
        // Streaming failed (or ended before wiring): drop any half-built
        // audio state, then fall back to record-then-upload.
        closeVoiceCapture();
        if (!voice.recording) {
          return;  // silence timer already ended the capture; nothing to do
        }
        startRecorderFallback(button, stream);
      });
    }).catch(function () {
      setStatus(el("ask-status"), "Microphone access was denied.", "error");
    });
  });
}

// Legacy path: MediaRecorder (webm) -> decode -> 16 kHz WAV -> REST upload.
function startRecorderFallback(button, stream) {
  VAD.sampleRate = VAD.sampleRate || 48000;
  voice.recorder = new window.MediaRecorder(stream);
  voice.chunks = [];
  setStatus(el("ask-status"), "Listening (recording mode)... tap the mic to stop.", "info");

  voice.recorder.addEventListener("dataavailable", function (event) {
    if (event.data && event.data.size > 0) {
      voice.chunks.push(event.data);
    }
  });
  voice.recorder.addEventListener("stop", async function () {
    voice.recording = false;
    voice.finalizing = false;
    button.classList.remove("recording");
    stopMicRecording();

    var raw = new window.Blob(voice.chunks);
    var status = el("ask-status");
    try {
      micButtonBusy(true);
      var wav = await recordingToWav(raw);
      setStatus(status, "Transcribing...", "info");
      var response = await voiceRequest("/voice/transcribe", {
        method: "POST",
        body: (function () {
          var form = new window.FormData();
          form.append("audio_file", wav, "recording.wav");
          return form;
        })()
      });
      var payload = await response.json();
      var text = (payload && payload.text) || "";
      if (!text) {
        setStatus(status, "Nothing heard - try again closer to the mic.", "error");
        return;
      }
      el("question").value = text.slice(0, parseInt(el("question").maxLength, 10) || 500);
      setStatus(status, "Heard: " + text, "info");
      // Demo flow: the transcribed question is sent immediately.
      if (el("ask-form").requestSubmit) {
        el("ask-form").requestSubmit();
      } else {
        handleAsk(new window.Event("submit", { cancelable: true }));
      }
    } catch (error) {
      setStatus(status, error.message, "error");
    } finally {
      micButtonBusy(false);
    }
  });
  voice.recorder.start();
}

// Speak (or stop) the text currently displayed in one answer bubble. Reading
// textNode at click time means translated answers are spoken in that language
// (TTS language "auto" follows the text).
function stopSpeaking() {
  if (voice.playing) {
    voice.playing.pause();
    voice.playing = null;
  }
  if (voice.button) {
    voice.button.classList.remove("speaking");
    voice.button.disabled = false;
    voice.button.title = "Read answer aloud";
    voice.button = null;
  }
}

async function speakAnswer(textNode, button) {
  if (voice.button === button) {
    stopSpeaking();
    return;
  }
  stopSpeaking();

  var text = textNode.textContent.trim();
  if (!text) {
    return;
  }

  var status = el("ask-status");
  button.disabled = true;
  try {
    var response = await voiceRequest("/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: window.JSON.stringify({ text: text, language: "auto" })
    });
    var blob = await response.blob();
    var previous = voice.playing;  // racing clicks: keep only the newest audio
    var audio = new window.Audio(window.URL.createObjectURL(blob));
    voice.playing = audio;
    voice.button = button;
    button.classList.add("speaking");
    button.disabled = false;
    button.title = "Stop reading";
    audio.addEventListener("ended", function () {
      if (voice.playing === audio) {
        stopSpeaking();
      }
    });
    audio.addEventListener("error", function () {
      if (voice.playing === audio) {
        stopSpeaking();
        setStatus(status, "Could not play the spoken answer.", "error");
      }
    });
    if (previous) {
      previous.pause();
    }
    await audio.play();
  } catch (error) {
    stopSpeaking();
    setStatus(status, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

// --- Player resize: drag the handle under the player for a taller player ---
// Height persists across reloads; double-click returns to auto-fit. The
// player's width follows the left column, so dragging the chat panel wider
// widens the player too.

var PLAYER_HEIGHT_KEY = "video-rag.player-height";
var PLAYER_MIN_PX = 240;

function setPlayerHeight(pixels) {
  var frame = el("player-frame");
  if (!frame) {
    return;
  }
  // Keep the player + caption + resizer on screen: leave room for the index
  // card above and a little padding below.
  var max = Math.max(PLAYER_MIN_PX, window.innerHeight - 240);
  var height = Math.round(Math.min(Math.max(pixels, PLAYER_MIN_PX), max));
  document.documentElement.style.setProperty("--player-height", height + "px");
  frame.classList.add("player-height-custom");
  try {
    window.localStorage.setItem(PLAYER_HEIGHT_KEY, String(height));
  } catch (error) {
    /* private mode: layout still works, it just won't persist */
  }
}

function resetPlayerHeight() {
  var frame = el("player-frame");
  document.documentElement.style.removeProperty("--player-height");
  if (frame) {
    frame.classList.remove("player-height-custom");
  }
  try {
    window.localStorage.removeItem(PLAYER_HEIGHT_KEY);
  } catch (error) {
    /* ignore */
  }
}

function initPlayerResizer() {
  var resizer = el("player-resizer");
  if (!resizer) {
    return;
  }

  // A saved custom height beats the auto-fit cap from the previous session.
  try {
    var saved = parseInt(window.localStorage.getItem(PLAYER_HEIGHT_KEY), 10);
    if (saved >= PLAYER_MIN_PX && saved <= 4000) {
      setPlayerHeight(saved);
    }
  } catch (error) {
    /* ignore */
  }

  resizer.addEventListener("dblclick", resetPlayerHeight);

  resizer.addEventListener("pointerdown", function (startEvent) {
    startEvent.preventDefault();
    resizer.setPointerCapture(startEvent.pointerId);
    document.getElementById("app-shell").classList.add("player-resizing");

    var frame = el("player-frame");
    var startY = startEvent.clientY;
    var startHeight = frame.getBoundingClientRect().height;

    var onMove = function (event) {
      setPlayerHeight(startHeight + (event.clientY - startY));
    };
    var onUp = function () {
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
      resizer.removeEventListener("pointercancel", onUp);
      document.getElementById("app-shell").classList.remove("player-resizing");
    };

    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
    resizer.addEventListener("pointercancel", onUp);
  });
}

// --- Start up --------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {
  var keyInput = el("api-key");
  if (keyInput) {
    // The key stays in session storage: it survives a page reload but not a
    // closed browser, and it is never written to disk with the project files.
    try {
      keyInput.value = window.sessionStorage.getItem(API_KEY_STORAGE_KEY) || "";
    } catch (error) {
      /* private mode: input stays empty */
    }
    keyInput.addEventListener("change", function () {
      try {
        window.sessionStorage.setItem(API_KEY_STORAGE_KEY, keyInput.value.trim());
      } catch (error) {
        /* ignore */
      }
      refreshHealth();
    });
  }

  el("ingest-form").addEventListener("submit", handleIngest);
  el("ask-form").addEventListener("submit", handleAsk);
  el("clear-chat").addEventListener("click", clearChat);
  el("chat-scope-clear").addEventListener("click", function () {
    if (el("video-id")) {
      el("video-id").value = "";
    }
    updateChatScope();
  });
  el("translation-language").addEventListener("change", updateTranslationButton);
  el("translate-button").addEventListener("click", translateAnswer);
  el("library-refresh").addEventListener("click", loadLibrary);
  el("nav-home").addEventListener("click", function () {
    showView("home");
  });
  el("nav-library").addEventListener("click", function () {
    showView("library");
    loadLibrary();
  });
  el("nav-evals").addEventListener("click", function () {
    // Toggle the mini sidebar; the dashboards themselves open from the items.
    expandEvalSubnav(el("eval-subnav").hidden);
  });
  el("nav-eval-live").addEventListener("click", function () {
    showEvalView("live");
  });
  el("nav-eval-golden").addEventListener("click", function () {
    showEvalView("golden");
  });

  renderCourseOptions();
  initSidebar();
  initChatResizer();
  initPlayerResizer();
  initVoiceInput();
  showView("home");
  loadLibrary();

  var courses = loadCourses();
  if (courses.length > 0) {
    el("course-id").value = courses[0];
    revealAsk();
    loadChat(courses[0]);
  } else {
    updateChatScope();
    renderChatThread([]);
  }

  refreshHealth();
});
