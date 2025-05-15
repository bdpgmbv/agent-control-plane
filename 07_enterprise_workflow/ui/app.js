// =====================================================================
//  The interface for the workflow engine.
//
//  Plain JavaScript, no framework, no build step. Explicit for loops rather
//  than chained map/filter, matching the style the Python follows.
//
//  It polls. A workflow that can be parked for three days and resumed by a
//  different process is not something a websocket helps with: the page asks the
//  database what is true, which is the same thing every worker does.
// =====================================================================

let watchedRunId = "";
let pollTimer = null;

const SAMPLE_REQUEST =
  "Hiring Ada Lovelace as a backend engineer.\n" +
  "Her email will be ada.lovelace@example.com and she starts 2026-11-03.\n" +
  "Salary is £95,000. She'll need a headset.\n" +
  "Thanks, Grace Hopper";

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
    if (name === "runs") { loadRuns(); }
    if (name === "approvals") { loadApprovals(); }
  });
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  try {
    const data = await callApi("/api/health");

    const mode = element("mode-badge");
    if (data.mode === "live") {
      mode.textContent = "live - " + data.model;
      mode.className = "badge badge-green";
    } else {
      mode.textContent = "offline - no model calls";
      mode.className = "badge badge-blue";
    }

    const worker = element("worker-badge");
    if (data.worker_running) {
      worker.textContent = "worker running";
      worker.className = "badge badge-green";
    } else {
      worker.textContent = "worker stopped";
      worker.className = "badge badge-red";
    }

    const settings = data.settings;
    element("settings-table").innerHTML =
      "<h3>What this instance is running with</h3><table>"
      + "<tr><td>lease</td><td class='number mono'>" + settings.lease_seconds + "s</td></tr>"
      + "<tr><td>attempts per step</td><td class='number mono'>" + settings.max_attempts + "</td></tr>"
      + "<tr><td>approval needed above</td><td class='number mono'>"
      + settings.approval_required_above.toFixed(2) + "</td></tr>"
      + "<tr><td>approval expires after</td><td class='number mono'>"
      + settings.approval_expiry_hours + "h</td></tr>"
      + "<tr><td>database</td><td class='mono'>" + escapeHtml(data.database) + "</td></tr>"
      + "</table>";
  } catch (error) {
    element("mode-badge").textContent = "cannot reach the server";
    element("mode-badge").className = "badge badge-red";
  }
}

// ---------------------------------------------------------------- starting

element("request-text").value = SAMPLE_REQUEST;

element("start-run").addEventListener("click", async function () {
  let text = element("request-text").value;

  if (element("expensive").checked) {
    text = text.replace(/She'll need .*$/m, "She'll need a laptop, a desk and a chair.");
    if (text.indexOf("desk") < 0) { text += "\nShe'll need a laptop, a desk and a chair."; }
  }
  if (element("bad-date").checked) {
    text = text.replace(/\d{4}-\d{2}-\d{2}/, "2020-01-06");
  }

  const input = { request_text: text, today_override: "2026-09-25" };
  if (element("fail-licences").checked) { input.fail_licences_times = 2; }
  if (element("fail-payroll").checked) { input.fail_payroll_times = 99; }

  this.disabled = true;
  element("start-status").textContent = "starting…";

  try {
    const data = await callApi("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow_name: "employee_onboarding", input: input }),
    });
    element("start-status").textContent = "";
    watchRun(data.run_id);
  } catch (error) {
    element("start-status").innerHTML =
      "<span class='error-text'>" + escapeHtml(error.message) + "</span>";
  }
  this.disabled = false;
});

element("reset-all").addEventListener("click", async function () {
  try {
    await callApi("/api/reset", { method: "POST" });
    stopWatching();
    element("detail-area").innerHTML = "";
    element("start-status").textContent = "cleared";
    loadHealth();
  } catch (error) {
    element("start-status").textContent = error.message;
  }
});

// ---------------------------------------------------------------- watching

function watchRun(runId) {
  watchedRunId = runId;
  stopWatching();
  refreshWatched();
  pollTimer = setInterval(refreshWatched, 700);
}

function stopWatching() {
  if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null; }
}

async function refreshWatched() {
  if (watchedRunId === "") { return; }
  try {
    const view = await callApi("/api/runs/" + encodeURIComponent(watchedRunId));
    element("detail-area").innerHTML = renderRun(view);
    attachRunButtons(view);
    loadHealth();

    if (view.run.state === "succeeded" || view.run.state === "failed"
        || view.run.state === "compensated" || view.run.state === "cancelled") {
      stopWatching();
    }
  } catch (error) {
    stopWatching();
  }
}

function stateBadge(state) {
  const map = {
    succeeded: "badge-green", running: "badge-blue", pending: "badge-grey",
    waiting_approval: "badge-amber", failed: "badge-red",
    compensating: "badge-purple", compensated: "badge-purple",
    cancelled: "badge-grey",
  };
  const label = state.replace(/_/g, " ");
  return "<span class='badge " + (map[state] || "badge-grey") + "'>"
    + escapeHtml(label) + "</span>";
}

function stepMark(state) {
  if (state === "succeeded") { return "✓"; }
  if (state === "failed") { return "✗"; }
  if (state === "running" || state === "claimed") { return "▶"; }
  if (state === "blocked") { return "⏸"; }
  if (state === "compensated") { return "↺"; }
  if (state === "skipped") { return "–"; }
  return "·";
}

function renderRun(view) {
  const run = view.run;
  let html = "<div class='card'>";

  html += "<h2>" + escapeHtml(run.run_id) + " " + stateBadge(run.state) + "</h2>";

  const fields = run.context.fields || {};
  if (fields.full_name) {
    html += "<p class='hint'>" + escapeHtml(fields.full_name)
      + " &middot; " + escapeHtml(fields.department || "")
      + " &middot; starts " + escapeHtml(fields.start_date || "") + "</p>";
  }
  if (run.error) {
    html += "<p class='error-text'>" + escapeHtml(run.error) + "</p>";
  }

  // ---- the timeline
  html += "<div class='timeline'>";
  for (let index = 0; index < view.steps.length; index++) {
    const step = view.steps[index];
    let meta = "";
    if (step.attempts > 1) { meta += "attempt " + step.attempts + " &middot; "; }
    if (step.claimed_by) { meta += escapeHtml(step.claimed_by) + " &middot; "; }
    if (step.error) { meta += escapeHtml(step.error.substring(0, 90)); }

    html += "<div class='step-row " + escapeHtml(step.state) + "'>"
      + "<div class='step-mark'>" + stepMark(step.state) + "</div>"
      + "<div><strong>" + escapeHtml(step.step_name) + "</strong>"
      + (meta ? "<div class='step-meta'>" + meta + "</div>" : "") + "</div>"
      + "<div class='step-meta'>" + escapeHtml(step.state) + "</div>"
      + "</div>";
  }
  html += "</div>";

  // ---- approvals needing an answer
  for (let index = 0; index < view.approvals.length; index++) {
    const approval = view.approvals[index];
    if (approval.state !== "pending") { continue; }
    html += "<div class='approval-card'>"
      + "<strong>Waiting for you</strong>"
      + "<p style='margin:8px 0'>" + escapeHtml(approval.question) + "</p>"
      + "<div class='row'>"
      + "<button class='primary small' data-approve='" + escapeHtml(approval.approval_id) + "'>Approve</button>"
      + "<button class='ghost small' data-reject='" + escapeHtml(approval.approval_id) + "'>Reject</button>"
      + "</div></div>";
  }

  // ---- irreversible things
  if (view.side_effects.length > 0) {
    html += "<h3>Things that happened in the outside world</h3>";
    for (let index = 0; index < view.side_effects.length; index++) {
      const effect = view.side_effects[index];
      html += "<div class='effect" + (effect.compensated ? " undone" : "") + "'>"
        + "<span><strong>" + escapeHtml(effect.kind) + "</strong> "
        + "<span class='mono'>" + escapeHtml(effect.idempotency_key) + "</span></span>"
        + "<span>" + (effect.compensated ? "undone" : "done") + "</span></div>";
    }
  }

  // ---- the welcome note, if it got that far
  const note = run.context.welcome_note;
  if (note) {
    html += "<details><summary>The welcome note</summary><pre class='note'>"
      + escapeHtml(note) + "</pre></details>";
  }

  // ---- history
  html += "<details><summary>Everything that happened (" + view.events.length + ")</summary>";
  for (let index = 0; index < view.events.length; index++) {
    const event = view.events[index];
    let detail = "";
    const keys = Object.keys(event.detail || {});
    for (let k = 0; k < keys.length; k++) {
      if (keys[k] === "traceback") { continue; }
      detail += " " + keys[k] + "=" + String(event.detail[keys[k]]).substring(0, 70);
    }
    html += "<div class='event'><b>" + escapeHtml(event.kind) + "</b> "
      + escapeHtml(event.step_name) + escapeHtml(detail) + "</div>";
  }
  html += "</details>";

  // ---- chaos
  html += "<div class='row'>"
    + "<button class='danger small' id='kill-worker'>Kill the worker mid-run</button>"
    + "<button class='ghost small' id='restart-worker'>Start a new worker</button>"
    + "<button class='ghost small' id='cancel-run'>Cancel this run</button>"
    + "</div>"
    + "<p class='hint' id='chaos-note'></p>";

  html += "</div>";
  return html;
}

function attachRunButtons(view) {
  const approveButtons = document.querySelectorAll("[data-approve]");
  for (let index = 0; index < approveButtons.length; index++) {
    approveButtons[index].addEventListener("click", function () {
      decide(this.getAttribute("data-approve"), true);
    });
  }
  const rejectButtons = document.querySelectorAll("[data-reject]");
  for (let index = 0; index < rejectButtons.length; index++) {
    rejectButtons[index].addEventListener("click", function () {
      decide(this.getAttribute("data-reject"), false);
    });
  }

  const kill = element("kill-worker");
  if (kill) {
    kill.addEventListener("click", async function () {
      try {
        const data = await callApi(
          "/api/runs/" + encodeURIComponent(view.run.run_id) + "/simulate-crash",
          { method: "POST" });
        element("chaos-note").textContent = data.message;
      } catch (error) {
        element("chaos-note").textContent = error.message;
      }
    });
  }

  const restart = element("restart-worker");
  if (restart) {
    restart.addEventListener("click", async function () {
      await callApi("/api/worker/start", { method: "POST" });
      element("chaos-note").textContent =
        "a new worker started. It will pick up whatever the old one was holding "
        + "once that lease expires.";
      watchRun(view.run.run_id);
    });
  }

  const cancel = element("cancel-run");
  if (cancel) {
    cancel.addEventListener("click", async function () {
      try {
        const data = await callApi(
          "/api/runs/" + encodeURIComponent(view.run.run_id) + "/cancel",
          { method: "POST" });
        element("chaos-note").textContent = data.message;
      } catch (error) {
        element("chaos-note").textContent = error.message;
      }
    });
  }
}

async function decide(approvalId, approved) {
  try {
    const data = await callApi("/api/approvals/" + encodeURIComponent(approvalId) + "/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: approved, decided_by: "you", note: "" }),
    });
    if (watchedRunId !== "") { watchRun(watchedRunId); }
    loadApprovals();
    return data;
  } catch (error) {
    return null;
  }
}

// ---------------------------------------------------------------- runs list

async function loadRuns() {
  const container = element("runs-list");
  container.innerHTML = "<p class='empty'>loading&hellip;</p>";
  try {
    const data = await callApi("/api/runs");
    if (data.runs.length === 0) {
      container.innerHTML = "<p class='empty'>Nothing has been started yet.</p>";
      return;
    }
    let rows = "";
    for (let index = 0; index < data.runs.length; index++) {
      const run = data.runs[index];
      rows += "<tr class='clickable' data-run='" + escapeHtml(run.run_id) + "'>"
        + "<td class='mono'>" + escapeHtml(run.run_id) + "</td>"
        + "<td>" + escapeHtml(run.person) + "</td>"
        + "<td>" + stateBadge(run.state) + "</td>"
        + "<td class='number'>" + run.steps_done + " / " + run.steps_total + "</td>"
        + "<td>" + escapeHtml((run.error || "").substring(0, 70)) + "</td></tr>";
    }
    container.innerHTML = "<table><thead><tr><th>run</th><th>person</th><th>state</th>"
      + "<th class='number'>steps</th><th>error</th></tr></thead><tbody>"
      + rows + "</tbody></table>";

    const rowElements = container.querySelectorAll("tr.clickable");
    for (let index = 0; index < rowElements.length; index++) {
      rowElements[index].addEventListener("click", async function () {
        const view = await callApi("/api/runs/"
          + encodeURIComponent(this.getAttribute("data-run")));
        element("runs-detail").innerHTML = renderRun(view);
        attachRunButtons(view);
      });
    }
  } catch (error) {
    container.innerHTML = "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
  }
}

element("refresh-runs").addEventListener("click", loadRuns);

// ---------------------------------------------------------------- approvals

async function loadApprovals() {
  const container = element("approvals-list");
  container.innerHTML = "<p class='empty'>loading&hellip;</p>";
  try {
    const data = await callApi("/api/approvals");
    if (data.approvals.length === 0) {
      container.innerHTML = "<p class='empty'>Nothing is waiting.</p>";
      return;
    }
    let html = "";
    for (let index = 0; index < data.approvals.length; index++) {
      const card = data.approvals[index];
      html += "<div class='approval-card'>"
        + "<strong>" + escapeHtml(card.person || card.run_id) + "</strong>"
        + "<p style='margin:8px 0'>" + escapeHtml(card.question) + "</p>"
        + "<p class='muted'>waiting " + card.waiting_hours.toFixed(1)
        + "h &middot; expires in " + card.expires_in_hours.toFixed(1) + "h</p>"
        + "<div class='row'>"
        + "<button class='primary small' data-approve='" + escapeHtml(card.approval_id) + "'>Approve</button>"
        + "<button class='ghost small' data-reject='" + escapeHtml(card.approval_id) + "'>Reject</button>"
        + "</div></div>";
    }
    container.innerHTML = html;

    const approveButtons = container.querySelectorAll("[data-approve]");
    for (let index = 0; index < approveButtons.length; index++) {
      approveButtons[index].addEventListener("click", function () {
        decide(this.getAttribute("data-approve"), true);
      });
    }
    const rejectButtons = container.querySelectorAll("[data-reject]");
    for (let index = 0; index < rejectButtons.length; index++) {
      rejectButtons[index].addEventListener("click", function () {
        decide(this.getAttribute("data-reject"), false);
      });
    }
  } catch (error) {
    container.innerHTML = "<p class='error-text'>" + escapeHtml(error.message) + "</p>";
  }
}

element("refresh-approvals").addEventListener("click", loadApprovals);

// ---------------------------------------------------------------- start
loadHealth();
