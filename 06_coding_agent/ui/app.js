// =====================================================================
//  The interface for the coding agent.
//
//  Plain JavaScript, no framework, no build step. Explicit for loops rather
//  than chained map/filter, to match the style the Python follows.
// =====================================================================

let pollTimer = null;

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
    throw new Error(detail);
  }
  return body;
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
    if (name === "tasks") { loadTasks(); }
  });
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  try {
    const data = await callApi("/api/health");
    const badge = element("mode-badge");
    if (data.mode === "live") {
      badge.textContent = "key present - " + data.model;
      badge.className = "badge badge-green";
    } else {
      badge.textContent = "no key - offline only";
      badge.className = "badge badge-blue";
      element("run-live").disabled = true;
      element("run-live").title = "no OPENAI_API_KEY in .env";
    }

    let protectedList = "";
    for (let index = 0; index < data.protected_globs.length; index++) {
      protectedList += "<code>" + escapeHtml(data.protected_globs[index]) + "</code> ";
    }

    element("gate-values").innerHTML =
      "<h3>What this instance is running with</h3><table>"
      + "<tr><td>attempts per task</td><td class='number mono'>"
      + data.budget.max_iterations + "</td></tr>"
      + "<tr><td>tokens per task</td><td class='number mono'>"
      + data.budget.max_tokens_per_task.toLocaleString() + "</td></tr>"
      + "<tr><td>seconds per task</td><td class='number mono'>"
      + data.budget.max_seconds_per_task + "</td></tr>"
      + "<tr><td>test run killed after</td><td class='number mono'>"
      + data.budget.test_timeout_seconds + "s</td></tr>"
      + "<tr><td>files the agent may see</td><td class='number mono'>"
      + data.context.max_files_in_context + "</td></tr>"
      + "</table><h3>Files the agent may never edit</h3><p class='hint'>"
      + protectedList + "</p>";
  } catch (error) {
    element("mode-badge").textContent = "cannot reach the server";
    element("mode-badge").className = "badge badge-red";
  }
}

// ---------------------------------------------------------------- tasks

async function loadTasks() {
  const container = element("task-list");
  container.innerHTML = "<p class='empty'>loading&hellip;</p>";
  try {
    const data = await callApi("/api/tasks");
    let html = "";
    for (let index = 0; index < data.tasks.length; index++) {
      const task = data.tasks[index];
      let tests = "";
      for (let t = 0; t < task.target_tests.length; t++) {
        tests += "<div class='mono'>" + escapeHtml(task.target_tests[t]) + "</div>";
      }
      html += "<details><summary><strong>" + escapeHtml(task.task_id)
        + "</strong> &mdash; " + escapeHtml(task.title) + "</summary>"
        + "<div class='issue-text'>" + escapeHtml(task.issue) + "</div>"
        + "<h3>Test that must go green</h3>" + tests
        + "<h3>File broken</h3><div class='mono'>"
        + escapeHtml(task.files_broken.join(", ")) + "</div></details>";
    }
    container.innerHTML = html;
  } catch (error) {
    container.innerHTML = "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
  }
}

// ---------------------------------------------------------------- running

element("run-offline").addEventListener("click", function () { startRun(false); });
element("run-live").addEventListener("click", function () { startRun(true); });

async function startRun(live) {
  element("run-offline").disabled = true;
  element("run-live").disabled = true;
  element("summary-area").innerHTML = "";
  element("results-area").innerHTML = "";
  element("run-status").textContent = live ? "asking the model…" : "replaying…";

  try {
    const data = await callApi("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_ids: [], live: live }),
    });
    pollRun(data.run_id);
  } catch (error) {
    element("run-status").innerHTML =
      "<span class='error-text'>" + escapeHtml(error.message) + "</span>";
    finishRunButtons();
  }
}

function finishRunButtons() {
  element("run-offline").disabled = false;
  const liveButton = element("run-live");
  if (liveButton.title === "") { liveButton.disabled = false; }
}

function pollRun(runId) {
  if (pollTimer !== null) { clearInterval(pollTimer); }
  pollTimer = setInterval(async function () {
    try {
      const data = await callApi("/api/runs/" + encodeURIComponent(runId));
      renderProgress(data);

      if (data.status !== "running") {
        clearInterval(pollTimer);
        pollTimer = null;
        element("run-status").textContent = "";
        finishRunButtons();
        if (data.status === "failed") {
          element("summary-area").innerHTML =
            "<div class='card'><p class='error-text'>" + escapeHtml(data.error) + "</p></div>";
        } else {
          renderSummary(data);
          renderResults(data.results);
        }
      }
    } catch (error) {
      clearInterval(pollTimer);
      pollTimer = null;
      finishRunButtons();
    }
  }, 700);
}

function renderProgress(data) {
  const percent = data.total > 0 ? Math.round(100 * data.done / data.total) : 0;
  let rows = "";
  for (let index = 0; index < data.results.length; index++) {
    const result = data.results[index];
    rows += "<div class='verdict-line'>"
      + (result.outcome === "solved" ? "<span class='verdict-ok'>solved  </span>"
                                     : "<span class='verdict-fail'>" + escapeHtml(result.outcome) + "</span>")
      + "  " + escapeHtml(result.task_id)
      + "  (" + result.iterations + " attempt(s))</div>";
  }
  element("progress").innerHTML =
    "<div class='progress-bar'><div class='progress-fill' style='width:" + percent + "%'></div></div>"
    + "<p class='hint'>" + data.done + " of " + data.total + " task(s)"
    + (data.live ? " &mdash; live" : " &mdash; offline") + "</p>" + rows;
}

function stat(value, label, kind) {
  const cssClass = kind ? "stat " + kind : "stat";
  return "<div class='" + cssClass + "'><div class='stat-value'>" + escapeHtml(value)
    + "</div><div class='stat-label'>" + escapeHtml(label) + "</div></div>";
}

function renderSummary(data) {
  const summary = data.summary;
  if (!summary) { return; }

  const cheated = summary.accepted_with_tampered_tests + summary.accepted_with_regressions;
  const resolvePercent = summary.tasks > 0
    ? Math.round(100 * summary.solved / summary.tasks) : 0;

  element("summary-area").innerHTML =
    "<div class='card'><h2>" + (data.live ? "Live run &mdash; this measures the agent"
                                          : "Offline run &mdash; this measures the harness")
    + "</h2><div class='stat-grid'>"
    + stat(cheated, "accepted while cheating", cheated === 0 ? "good" : "bad")
    + stat(summary.solved + " / " + summary.tasks, "resolved")
    + stat(resolvePercent + "%", "resolve rate")
    + stat(summary.accepted_but_flagged, "flagged for review",
           summary.accepted_but_flagged > 0 ? "" : "good")
    + stat(summary.rejected, "patch rejected")
    + stat(summary.total_model_calls, "model calls")
    + stat(summary.total_tokens.toLocaleString(), "tokens")
    + stat("$" + summary.total_cost_usd.toFixed(4), "cost")
    + "</div>"
    + (data.report ? "<details><summary>The full report</summary><pre class='report'>"
        + escapeHtml(data.report) + "</pre></details>" : "")
    + "</div>";
}

function renderResults(results) {
  let html = "";
  for (let index = 0; index < results.length; index++) {
    html += renderOneResult(results[index]);
  }
  element("results-area").innerHTML = html;
}

function outcomeBadge(result) {
  if (result.outcome === "solved" && result.flagged) {
    return "<span class='badge badge-amber'>solved, flagged</span>";
  }
  if (result.outcome === "solved") { return "<span class='badge badge-green'>solved</span>"; }
  if (result.outcome === "rejected") { return "<span class='badge badge-red'>patch rejected</span>"; }
  return "<span class='badge badge-amber'>" + escapeHtml(result.outcome) + "</span>";
}

function renderOneResult(result) {
  let html = "<div class='card'><h2>" + escapeHtml(result.task_id) + " &mdash; "
    + escapeHtml(result.title) + " " + outcomeBadge(result) + "</h2>";

  html += "<p class='hint'>" + escapeHtml(result.summary) + "</p>";

  if (result.baseline_run && result.final_run) {
    html += "<p class='hint'>before: <strong>" + escapeHtml(runSummary(result.baseline_run))
      + "</strong> &rarr; after: <strong>" + escapeHtml(runSummary(result.final_run))
      + "</strong></p>";
  }

  // ---- the verdict
  if (result.verdict) {
    html += "<h3>The verifier</h3>";
    for (let index = 0; index < result.verdict.reasons.length; index++) {
      const reason = result.verdict.reasons[index];
      html += "<div class='verdict-line " + (reason.passed ? "verdict-ok" : "verdict-fail") + "'>"
        + (reason.passed ? "ok   " : "FAIL ") + escapeHtml(reason.code) + ": "
        + escapeHtml(reason.detail) + "</div>";
    }
  }

  // ---- the steps
  if (result.steps.length > 0) {
    html += "<details open><summary>What the agent did ("
      + result.steps.length + " attempt(s))</summary>";
    for (let index = 0; index < result.steps.length; index++) {
      html += renderStep(result.steps[index]);
    }
    html += "</details>";
  }

  // ---- the diff
  if (result.diff && result.diff.length > 0) {
    html += "<details><summary>The patch</summary><pre class='diff'>"
      + escapeHtml(result.diff) + "</pre></details>";
  }

  html += "<p class='hint'>" + result.model_calls + " model call(s), "
    + result.tokens.toLocaleString() + " tokens, $" + result.cost_usd.toFixed(6)
    + ", " + result.seconds.toFixed(1) + "s</p>";

  html += "</div>";
  return html;
}

function runSummary(run) {
  return run.passed.length + " passed, " + run.failed.length + " failed, "
    + run.errors.length + " errors";
}

function renderStep(step) {
  let html = "<div class='step step-" + escapeHtml(step.action) + "'>";
  html += "<div class='step-head'>attempt " + step.number + " &mdash; "
    + escapeHtml(step.action) + "</div>";

  if (step.thinking) {
    html += "<div class='step-thinking'>" + escapeHtml(step.thinking) + "</div>";
  }

  for (let index = 0; index < step.edits.length; index++) {
    const editResult = step.edits[index];
    if (editResult.status === "applied") {
      html += "<div class='applied'>applied to " + escapeHtml(editResult.edit.path)
        + (editResult.edit.reason ? " &mdash; " + escapeHtml(editResult.edit.reason) : "")
        + "</div>";
    } else {
      html += "<div class='refused'>REFUSED (" + escapeHtml(editResult.status) + ") "
        + escapeHtml(editResult.edit.path) + "</div>";
    }
  }

  if (step.files_read.length > 0) {
    html += "<div class='hint'>read " + escapeHtml(step.files_read.join(", ")) + "</div>";
  }
  if (step.test_run) {
    html += "<div class='hint'>tests: " + escapeHtml(runSummary(step.test_run)) + "</div>";
  }
  if (step.note && step.edits.length === 0 && step.files_read.length === 0) {
    html += "<div class='hint'>" + escapeHtml(step.note.substring(0, 300)) + "</div>";
  }

  html += "</div>";
  return html;
}

// ---------------------------------------------------------------- start
loadHealth();
