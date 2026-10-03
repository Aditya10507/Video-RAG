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
    revealAsk();
    showView("home");
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


// --- Step 2: asking a question ---------------------------------------------

async function handleAsk(event) {
  event.preventDefault();

  stopActiveStream();
  var token = activeAskToken + 1;
  activeAskToken = token;

  var button = el("ask-button");
  button.disabled = true;

  var videoId = el("video-id").value.trim();
  setStatus(
    el("ask-status"),
    videoId ? "Searching that one video..." : "Searching the course..."
  );

  try {
    var body = {};
    body.question = el("question").value.trim();
    body.course_id = el("course-id").value.trim();
    if (videoId) {
      body.video_id = videoId;
    }

    // Show the card instantly with a live shimmer so the user sees motion
    // within one frame, then fill the text as tokens arrive.
    el("answer-card").hidden = false;
    el("answer-message").textContent = "";
    el("answer-text").textContent = "";
    el("answer-score").textContent = "";
    resetAnswerTranslation("");
    setStreamingUI(true);

    var streamedText = "";
    var answerNode = el("answer-text");
    var result = null;

    try {
      result = await tryTrueStream(body, token, function (tok) {
        if (token !== activeAskToken) {
          return;
        }
        streamedText += tok;
        // Direct append for true tokens: no re-layout of the whole string,
        // just the delta. rAF-throttle via textContent is already cheap.
        answerNode.textContent = streamedText;
      });
      // True stream delivered tokens live; mark UI done below.
      if (token !== activeAskToken) {
        return;
      }
    } catch (streamError) {
      // No /ask/stream on this backend yet: fall back to normal /ask.
      result = await request("/ask", "POST", body);
      if (token !== activeAskToken) {
        return;
      }
      el("ask-status").hidden = true;
      rememberCourse(body.course_id);
      // Hand over to the smooth renderer; it clears the shimmer when done.
      await renderAnswerStreamed(result, token);
      return;
    }

    el("ask-status").hidden = true;
    rememberCourse(body.course_id);
    // True-stream path: text is already on screen; finish metadata + sources
    // with the reveal animation, then drop the streaming glow.
    await renderAnswerStreamed(result, token, streamedText);
  } catch (error) {
    if (token === activeAskToken) {
      stopActiveStream();
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
}

// The Ask column stays hidden until the first chunks land, so a new user
// meets Index first and Ask second.
function revealAsk() {
  el("ask-col").hidden = false;
}

function goHomeWithSelection(courseId, videoId) {
  el("course-id").value = courseId || "";
  el("video-id").value = videoId || "";
  if (courseId) {
    rememberCourse(courseId);
  }
  revealAsk();
  showView("home");
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
  el("answer-card").hidden = false;

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
  el("answer-card").hidden = false;

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
  // Sources: the primary citation is the one the answer was written from.
  var primaryHolder = el("primary-source");
  removeChildren(primaryHolder);

  var secondaryHolder = el("secondary-sources");
  removeChildren(secondaryHolder);

  if (result.primary_source) {
    primaryHolder.appendChild(citationButton(result.primary_source));

    var link = document.createElement("a");
    link.className = "external";
    link.href = result.primary_source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open on YouTube at " + result.primary_source.timestamp_label;
    primaryHolder.appendChild(document.createElement("br"));
    primaryHolder.appendChild(link);

    el("sources").hidden = false;
    playCitation(result.primary_source);
  } else {
    el("sources").hidden = true;
    el("player").hidden = true;
  }

  var others = result.also_mentioned_in || [];
  el("secondary-title").hidden = others.length === 0;
  others.forEach(function (citation) {
    secondaryHolder.appendChild(citationButton(citation));
  });

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

function citationButton(citation) {
  var button = document.createElement("button");
  button.type = "button";
  button.className = "citation";

  var title = document.createElement("span");
  title.textContent = citation.video_title;

  var time = document.createElement("span");
  time.className = "time";
  time.textContent = citation.timestamp_label;

  button.appendChild(title);
  button.appendChild(time);
  button.addEventListener("click", function () {
    playCitation(citation);
  });
  return button;
}

// The timestamp is the whole point of the project: the embedded player starts
// at the second the topic is explained.
function playCitation(citation) {
  var source = "https://www.youtube.com/embed/" + encodeURIComponent(citation.video_id) +
    "?start=" + citation.start_seconds + "&rel=0";

  el("player-frame").src = source;
  el("player-caption").textContent =
    citation.video_title + " starting at " + citation.timestamp_label;
  el("player").hidden = false;
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

  renderCourseOptions();
  initSidebar();
  showView("home");
  loadLibrary();

  var courses = loadCourses();
  if (courses.length > 0) {
    el("course-id").value = courses[0];
  }

  refreshHealth();
});
