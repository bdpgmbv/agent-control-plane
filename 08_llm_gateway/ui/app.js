// =====================================================================
//  The interface for the gateway.
//
//  Plain JavaScript, no framework, no build step. Explicit for loops rather
//  than chained map/filter, matching the style the Python follows.
//
//  The one drawn thing is the confidence interval, because a range is the
//  answer an A/B test actually gives and a number is not.
// =====================================================================

const EXPERIMENT = "prompt-style";

function element(id) { return document.getElementById(id); }

function escapeHtml(value) {
  if (value === null || value === undefined) { return ""; }
  return String(value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function callApi(path, options) {
  const response = await fetch(path, options);
  let body = null;
  try { body = await response.json(); } catch (error) { body = null; }
  if (!response.ok) {
    let detail = "request failed with status " + response.status;
    if (body && body.detail) { detail = body.detail; }
    if (body && body.message) { detail = body.message; }
    const failure = new Error(detail);
    failure.body = body;
    failure.status = response.status;
    throw failure;
  }
  return body;
}

function money(value) {
  const number = Number(value) || 0;
  if (number === 0) { return "$0"; }
  if (number < 0.01) { return "$" + number.toFixed(6); }
  return "$" + number.toFixed(4);
}

// ---------------------------------------------------------------- tabs

const tabButtons = document.querySelectorAll(".tab");
for (let index = 0; index < tabButtons.length; index++) {
  tabButtons[index].addEventListener("click", function () {
    const name = this.getAttribute("data-tab");
    for (let other = 0; other < tabButtons.length; other++) {
      tabButtons[other].classList.remove("active");
    }
    this.classList.add("active");
    const panels = document.querySelectorAll(".panel");
    for (let panelIndex = 0; panelIndex < panels.length; panelIndex++) {
      panels[panelIndex].classList.add("hidden");
    }
    element("tab-" + name).classList.remove("hidden");
    if (name === "dashboard") { loadDashboard(); }
    if (name === "traces") { loadTraces(); }
    if (name === "experiments") { loadExperiment(); }
  });
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  try {
    const data = await callApi("/api/health");

    const mode = element("mode-badge");
    if (data.mode === "live") {
      mode.textContent = "live + offline models";
      mode.className = "badge badge-green";
    } else {
      mode.textContent = "offline models only";
      mode.className = "badge badge-blue";
    }

    const cache = element("cache-badge");
    cache.textContent = data.cache_entries + " cached";
    cache.className = "badge badge-grey";

    const settings = data.settings;
    element("settings-table").innerHTML =
      "<h3>What this instance is running with</h3><table>"
      + "<tr><td>models available</td><td class='mono'>"
      + escapeHtml(data.models.join(", ")) + "</td></tr>"
      + "<tr><td>semantic threshold</td><td class='number mono'>"
      + settings.semantic_threshold + "</td></tr>"
      + "<tr><td>rate limit</td><td class='number mono'>"
      + settings.requests_per_minute + "/min</td></tr>"
      + "<tr><td>daily budget per key</td><td class='number mono'>$"
      + settings.daily_budget_usd.toFixed(2) + "</td></tr>"
      + "<tr><td>confidence required</td><td class='number mono'>"
      + Math.round(settings.confidence_level * 100) + "%</td></tr>"
      + "<tr><td>minimum samples per arm</td><td class='number mono'>"
      + settings.minimum_samples_per_arm + "</td></tr></table>";
  } catch (error) {
    element("mode-badge").textContent = "cannot reach the server";
    element("mode-badge").className = "badge badge-red";
  }
}

// ---------------------------------------------------------------- dashboard

function stat(value, label, kind) {
  const cssClass = kind ? "stat " + kind : "stat";
  return "<div class='" + cssClass + "'><div class='stat-value'>" + escapeHtml(value)
    + "</div><div class='stat-label'>" + escapeHtml(label) + "</div></div>";
}

async function loadDashboard() {
  try {
    const data = await callApi("/api/dashboard");
    const totals = data.totals;
    const latency = data.latency;

    element("dashboard-stats").innerHTML =
      "<div class='stat-grid'>"
      + stat(totals.requests || 0, "requests")
      + stat(money(totals.cost), "spent")
      + stat(Math.round(data.cache_hit_rate * 100) + "%", "from cache",
             data.cache_hit_rate > 0 ? "good" : "")
      + stat(latency.p50 + "s", "p50 latency")
      + stat(latency.p95 + "s", "p95 latency", latency.p95 > 2 ? "warn" : "")
      + stat(totals.refused || 0, "refused", (totals.refused || 0) > 0 ? "warn" : "")
      + stat(totals.failed || 0, "failed", (totals.failed || 0) > 0 ? "warn" : "")
      + stat(((totals.input_tokens || 0) + (totals.output_tokens || 0)).toLocaleString(), "tokens")
      + "</div>";

    let modelRows = "";
    for (let index = 0; index < data.by_model.length; index++) {
      const row = data.by_model[index];
      modelRows += "<tr><td class='mono'>" + escapeHtml(row.model) + "</td>"
        + "<td class='number'>" + row.requests + "</td>"
        + "<td class='number'>" + money(row.cost) + "</td>"
        + "<td class='number'>" + row.mean_seconds.toFixed(3) + "s</td></tr>";
    }
    element("by-model").innerHTML = data.by_model.length === 0
      ? "<p class='empty'>Nothing yet.</p>"
      : "<table><thead><tr><th>model</th><th class='number'>calls</th>"
        + "<th class='number'>cost</th><th class='number'>mean</th></tr></thead><tbody>"
        + modelRows + "</tbody></table>";

    let ownerRows = "";
    for (let index = 0; index < data.by_owner.length; index++) {
      const row = data.by_owner[index];
      ownerRows += "<tr><td>" + escapeHtml(row.owner) + "</td>"
        + "<td class='number'>" + row.requests + "</td>"
        + "<td class='number'>" + money(row.cost) + "</td></tr>";
    }
    element("by-owner").innerHTML = data.by_owner.length === 0
      ? "<p class='empty'>Nothing yet.</p>"
      : "<table><thead><tr><th>key</th><th class='number'>requests</th>"
        + "<th class='number'>cost</th></tr></thead><tbody>" + ownerRows + "</tbody></table>";

    loadHealth();
  } catch (error) {
    element("dashboard-stats").innerHTML =
      "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
  }
}

element("refresh-dashboard").addEventListener("click", loadDashboard);
element("reset-all").addEventListener("click", async function () {
  await callApi("/api/reset", { method: "POST" });
  loadDashboard();
  element("send-result").innerHTML = "";
  element("experiment-results").innerHTML = "";
});

// ---------------------------------------------------------------- try it

async function sendOne() {
  const body = {
    prompt: element("prompt").value,
    task: element("task").value,
    no_cache: element("no-cache").checked,
  };
  return await callApi("/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-API-Key": element("api-key").value },
    body: JSON.stringify(body),
  });
}

function renderResponse(response) {
  const cacheBadge = response.cache === "miss"
    ? "<span class='badge badge-grey'>not cached</span>"
    : "<span class='badge badge-green'>" + escapeHtml(response.cache) + " hit</span>";

  return "<div class='card'><h2>" + escapeHtml(response.model || "no model")
    + " " + cacheBadge + "</h2>"
    + "<pre class='answer'>" + escapeHtml(response.text || response.message) + "</pre>"
    + "<div class='stat-grid'>"
    + stat(money(response.cost_usd), "cost")
    + stat(response.seconds.toFixed(4) + "s", "took")
    + stat(response.attempts, "attempts")
    + stat((response.input_tokens + response.output_tokens), "tokens")
    + "</div>"
    + "<p class='hint mono'>" + escapeHtml(response.request_id) + "</p></div>";
}

element("send").addEventListener("click", async function () {
  this.disabled = true;
  element("send-status").textContent = "sending…";
  try {
    const response = await sendOne();
    element("send-result").innerHTML = renderResponse(response);
    element("send-status").textContent = "";
  } catch (error) {
    const body = error.body;
    element("send-result").innerHTML = body
      ? renderResponse(body)
      : "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
    element("send-status").innerHTML =
      "<span class='error-text'>" + escapeHtml(error.message) + "</span>";
  }
  this.disabled = false;
  loadHealth();
});

element("send-ten").addEventListener("click", async function () {
  this.disabled = true;
  let allowed = 0;
  let refused = 0;
  for (let index = 0; index < 10; index++) {
    element("send-status").textContent = "sending " + (index + 1) + " of 10…";
    try {
      await sendOne();
      allowed = allowed + 1;
    } catch (error) {
      refused = refused + 1;
    }
  }
  element("send-status").textContent =
    allowed + " allowed, " + refused + " refused by the limits";
  this.disabled = false;
  loadHealth();
});

// ---------------------------------------------------------------- A/B

element("make-experiment").addEventListener("click", async function () {
  element("experiment-status").textContent = "creating…";
  try {
    await callApi("/api/experiments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: EXPERIMENT,
        question: "Does asking for working-out improve accuracy?",
        variants: [
          { name: "direct", system: "Answer with just the number.", weight: 0.5 },
          { name: "stepwise", system: "Work through it step by step.", weight: 0.5 },
        ],
      }),
    });
    element("experiment-status").textContent = "created";
    loadExperiment();
  } catch (error) {
    element("experiment-status").innerHTML =
      "<span class='error-text'>" + escapeHtml(error.message) + "</span>";
  }
});

function wireSimulate(buttonId, count) {
  element(buttonId).addEventListener("click", async function () {
    this.disabled = true;
    element("experiment-status").textContent = "sending " + count + " requests…";
    try {
      const data = await callApi(
        "/api/experiments/" + EXPERIMENT + "/simulate",
        { method: "POST",
          headers: { "Content-Type": "application/json", "X-API-Key": "team-key" },
          body: JSON.stringify({ requests: count }) });
      let note = "graded " + data.graded;
      if (data.refused > 0) { note += ", " + data.refused + " refused by the limits"; }
      if (data.failed > 0) { note += ", " + data.failed + " failed"; }
      element("experiment-status").textContent = note;
      renderVerdict(data.results);
    } catch (error) {
      element("experiment-status").innerHTML =
        "<span class='error-text'>" + escapeHtml(error.message)
        + " &mdash; create the experiment first</span>";
    }
    this.disabled = false;
  });
}
wireSimulate("simulate-40", 40);
wireSimulate("simulate-200", 200);
wireSimulate("simulate-600", 600);

async function loadExperiment() {
  try {
    const results = await callApi("/api/experiments/" + EXPERIMENT + "/results");
    renderVerdict(results);
  } catch (error) {
    element("experiment-results").innerHTML =
      "<p class='empty'>No experiment yet. Create it above.</p>";
  }
}

function renderInterval(low, high, difference) {
  // A range the reader can see. The red line is zero: while the bar crosses
  // it, "no difference" is still one of the possibilities.
  const span = Math.max(Math.abs(low), Math.abs(high), 0.05) * 1.25;
  const toPercent = function (value) { return ((value + span) / (2 * span)) * 100; };

  const left = toPercent(low);
  const right = toPercent(high);
  const point = toPercent(difference);
  const zero = toPercent(0);

  return "<div class='interval'>"
    + "<div class='interval-bar' style='left:" + left + "%;width:" + (right - left) + "%'></div>"
    + "<div class='interval-zero' style='left:" + zero + "%'></div>"
    + "<div class='interval-point' style='left:" + point + "%'></div>"
    + "</div><div class='interval-labels'><span>"
    + (low * 100).toFixed(1) + "%</span><span>no difference</span><span>"
    + (high * 100).toFixed(1) + "%</span></div>";
}

function renderVerdict(results) {
  if (!results || !results.arms || results.arms.length === 0) {
    element("experiment-results").innerHTML =
      "<p class='empty'>Nothing sent through it yet.</p>";
    return;
  }

  let rows = "";
  for (let index = 0; index < results.arms.length; index++) {
    const arm = results.arms[index];
    const isWinner = results.winner === arm.variant;
    rows += "<tr>"
      + "<td>" + escapeHtml(arm.variant)
      + (isWinner ? " <span class='badge badge-green'>winner</span>" : "") + "</td>"
      + "<td class='number'>" + arm.samples + "</td>"
      + "<td class='number'>" + arm.scored + "</td>"
      + "<td class='number'>" + Math.round(arm.success_rate * 100) + "%</td>"
      + "<td class='number'>" + money(arm.mean_cost_usd) + "</td>"
      + "<td class='number'>" + arm.p95_seconds.toFixed(4) + "s</td></tr>";
  }

  let html = "<div class='card'><h2>" + escapeHtml(results.experiment) + "</h2>"
    + "<table><thead><tr><th>variant</th><th class='number'>sent</th>"
    + "<th class='number'>graded</th><th class='number'>correct</th>"
    + "<th class='number'>mean cost</th><th class='number'>p95</th>"
    + "</tr></thead><tbody>" + rows + "</tbody></table>";

  html += "<div class='verdict " + (results.confident ? "confident" : "unsure") + "'>"
    + "<h3>" + (results.confident ? "There is a difference"
                                  : "Not confident yet") + "</h3>"
    + "<p style='margin:6px 0 0'>" + escapeHtml(results.verdict) + "</p>";

  if (results.confidence_interval && results.confidence_interval.length === 2) {
    html += renderInterval(results.confidence_interval[0],
                           results.confidence_interval[1], results.difference);
  }
  html += "</div></div>";

  element("experiment-results").innerHTML = html;
}

// ---------------------------------------------------------------- traces

async function loadTraces() {
  const container = element("traces-list");
  container.innerHTML = "<p class='empty'>loading&hellip;</p>";
  try {
    const data = await callApi("/v1/traces?limit=60");
    if (data.traces.length === 0) {
      container.innerHTML = "<p class='empty'>Nothing yet.</p>";
      return;
    }
    let rows = "";
    for (let index = 0; index < data.traces.length; index++) {
      const trace = data.traces[index];
      const badge = trace.outcome === "ok" ? "badge-green"
        : (trace.outcome === "refused" ? "badge-amber" : "badge-red");
      rows += "<tr class='clickable' data-trace='" + escapeHtml(trace.request_id) + "'>"
        + "<td><span class='badge " + badge + "'>" + escapeHtml(trace.outcome) + "</span></td>"
        + "<td class='mono'>" + escapeHtml(trace.model || "-") + "</td>"
        + "<td>" + escapeHtml(trace.cache) + "</td>"
        + "<td>" + escapeHtml(trace.variant || "-") + "</td>"
        + "<td class='number'>" + trace.attempt_list.length + "</td>"
        + "<td class='number'>" + money(trace.cost_usd) + "</td>"
        + "<td class='number'>" + trace.seconds.toFixed(4) + "s</td>"
        + "<td>" + escapeHtml((trace.prompt_preview || "").substring(0, 44)) + "</td></tr>";
    }
    container.innerHTML = "<table><thead><tr><th>outcome</th><th>model</th>"
      + "<th>cache</th><th>variant</th><th class='number'>tries</th>"
      + "<th class='number'>cost</th><th class='number'>took</th><th>prompt</th>"
      + "</tr></thead><tbody>" + rows + "</tbody></table>";

    const rowElements = container.querySelectorAll("tr.clickable");
    for (let index = 0; index < rowElements.length; index++) {
      rowElements[index].addEventListener("click", async function () {
        const trace = await callApi("/v1/traces/"
          + encodeURIComponent(this.getAttribute("data-trace")));
        element("trace-detail").innerHTML = renderTrace(trace);
      });
    }
  } catch (error) {
    container.innerHTML = "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
  }
}

function renderTrace(trace) {
  let html = "<div class='card'><h2 class='mono'>" + escapeHtml(trace.request_id) + "</h2>";
  html += "<p class='hint'>" + escapeHtml(trace.prompt_preview) + "</p>";

  if (trace.message) {
    html += "<p class='hint'>" + escapeHtml(trace.message) + "</p>";
  }

  html += "<h3>Every attempt, including the ones that failed</h3>";
  if (trace.attempt_list.length === 0) {
    html += "<p class='empty'>No provider was called "
      + (trace.cache !== "miss" ? "(the cache answered it)." : "(it was refused).") + "</p>";
  }
  for (let index = 0; index < trace.attempt_list.length; index++) {
    const attempt = trace.attempt_list[index];
    html += "<div class='attempt " + (attempt.ok ? "ok" : "failed") + "'>"
      + "<span><strong>" + escapeHtml(attempt.model) + "</strong> via "
      + escapeHtml(attempt.provider)
      + (attempt.ok ? "" : " &mdash; " + escapeHtml(attempt.failure_kind)) + "</span>"
      + "<span>" + attempt.seconds.toFixed(4) + "s</span></div>";
    if (!attempt.ok && attempt.error) {
      html += "<p class='hint' style='margin:0 0 8px 10px'>"
        + escapeHtml(attempt.error.substring(0, 140)) + "</p>";
    }
  }

  html += "<div class='stat-grid'>"
    + stat(money(trace.cost_usd), "cost")
    + stat(trace.seconds.toFixed(4) + "s", "took")
    + stat(trace.route || "-", "route")
    + stat(trace.score === null ? "not graded" : trace.score, "score")
    + "</div></div>";
  return html;
}

element("refresh-traces").addEventListener("click", loadTraces);

// ---------------------------------------------------------------- start
loadHealth();
loadDashboard();
