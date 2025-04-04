/* ==========================================================================
   Agentic Research System - browser code. Plain JavaScript, no framework.
   ========================================================================== */

let lastResponse = null;

function element(id) { return document.getElementById(id); }

function escapeHtml(text) {
  const holder = document.createElement("div");
  holder.textContent = text === null || text === undefined ? "" : String(text);
  return holder.innerHTML;
}

function highlightMarkers(text) {
  return escapeHtml(text).replace(/\[(\d+)\]/g, '<span class="marker">$1</span>');
}

async function callApi(path, options) {
  const settings = options || {};
  const response = await fetch(path, {
    method: settings.method || "GET",
    headers: settings.headers || {},
    body: settings.body
  });

  const text = await response.text();
  let payload = null;
  if (text.length > 0) {
    try { payload = JSON.parse(text); } catch (error) { payload = { detail: text }; }
  }
  if (!response.ok) {
    throw new Error(payload && payload.detail ? payload.detail : "request failed");
  }
  return payload;
}

async function postJson(path, body) {
  return callApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

/* ---------- tabs ---------- */

function setUpTabs() {
  const tabs = document.querySelectorAll(".tab");
  for (let index = 0; index < tabs.length; index = index + 1) {
    tabs[index].addEventListener("click", function () {
      const panelId = this.getAttribute("data-panel");

      const allTabs = document.querySelectorAll(".tab");
      for (let i = 0; i < allTabs.length; i = i + 1) { allTabs[i].classList.remove("tab-active"); }
      this.classList.add("tab-active");

      const allPanels = document.querySelectorAll(".panel");
      for (let i = 0; i < allPanels.length; i = i + 1) { allPanels[i].classList.remove("panel-active"); }
      element(panelId).classList.add("panel-active");

      if (panelId === "panelSources") { loadSources(); }
      if (panelId === "panelMetrics") { loadMetrics(); }
      if (panelId === "panelTrace") { renderTrace(); }
    });
  }
}

/* ---------- header ---------- */

async function loadStatus() {
  const strip = element("statusStrip");
  try {
    const config = await callApi("/api/config");
    const health = await callApi("/api/health");

    let modeClass = "pill-offline";
    let modeText = "OFFLINE - rules, no API key";
    if (config.llm_is_live) {
      modeClass = "pill-live";
      modeText = "LIVE - " + config.llm_model;
    }

    let documents = 0;
    for (let index = 0; index < health.sources.length; index = index + 1) {
      documents = documents + (health.sources[index].documents || 0);
    }

    strip.innerHTML =
      '<span class="pill ' + modeClass + '">' + escapeHtml(modeText) + "</span>" +
      '<span class="pill">similarity by ' + escapeHtml(config.similarity_measured_by) + "</span>" +
      '<span class="pill">' + documents + " documents</span>" +
      '<span class="pill">max ' + config.budgets.max_subquestions + " sub-questions</span>" +
      '<span class="pill">' + config.budgets.max_parallel_workers + " parallel workers</span>";
  } catch (error) {
    strip.innerHTML = '<span class="pill pill-offline">server unreachable</span>';
  }
}

/* ---------- running research ---------- */

async function runResearch() {
  const question = element("questionInput").value.trim();
  if (question === "") { return; }

  const button = element("researchButton");
  button.disabled = true;
  button.textContent = "Researching...";

  element("reportCard").hidden = false;
  element("reportSummary").innerHTML =
    '<span class="spinner">planning, then researching the sub-questions in parallel...</span>';
  element("sectionList").innerHTML = "";
  element("citationList").innerHTML = "";
  element("conflictsBlock").hidden = true;
  element("gapsBlock").hidden = true;
  element("partialBanner").hidden = true;
  element("reportBadges").innerHTML = "";

  try {
    const response = await postJson("/api/research", {
      question: question,
      max_seconds: Number(element("maxSeconds").value),
      max_tokens: Number(element("maxTokens").value),
      max_subquestions: Number(element("maxSubquestions").value)
    });

    lastResponse = response;
    renderReport(response);
    renderBudget(response);
    renderTrace();
    loadStatus();
  } catch (error) {
    element("reportSummary").innerHTML =
      '<span style="color:var(--bad)">Failed: ' + escapeHtml(error.message) + "</span>";
  } finally {
    button.disabled = false;
    button.textContent = "Research";
  }
}

function renderReport(response) {
  const report = response.report;

  /* --- badges --- */
  const confidencePercent = Math.round(report.confidence * 100);
  let confidenceClass = "badge-bad";
  if (confidencePercent >= 75) { confidenceClass = "badge-good"; }
  else if (confidencePercent >= 50) { confidenceClass = "badge-warn"; }

  const badges = [];
  if (report.partial) {
    badges.push('<span class="badge badge-warn">partial</span>');
  } else {
    badges.push('<span class="badge badge-good">complete</span>');
  }
  badges.push('<span class="badge ' + confidenceClass + '">confidence ' + confidencePercent + "%</span>");
  badges.push('<span class="badge">' + report.citations.length + " sources</span>");
  if (report.conflicts.length > 0) {
    badges.push('<span class="badge badge-warn">' + report.conflicts.length + " disagreement(s)</span>");
  }
  badges.push('<span class="badge">' + response.seconds.toFixed(1) + " s</span>");
  badges.push('<span class="badge">$' + response.cost_usd.toFixed(5) + "</span>");
  element("reportBadges").innerHTML = badges.join("");

  /* --- partial banner --- */
  if (report.partial) {
    element("partialBanner").hidden = false;
    element("partialBanner").innerHTML =
      "<b>This report is incomplete.</b> " + escapeHtml(report.confidence_reason);
  }

  /* --- summary --- */
  element("reportSummary").innerHTML = highlightMarkers(report.summary);

  /* --- conflicts --- */
  if (report.conflicts.length > 0) {
    element("conflictsBlock").hidden = false;
    const blocks = [];
    for (let index = 0; index < report.conflicts.length; index = index + 1) {
      const conflict = report.conflicts[index];
      blocks.push(
        '<div class="conflict-block">' +
          '<div class="conflict-side"><span class="conflict-source">' +
            escapeHtml(conflict.source_a) + ":</span> " + escapeHtml(conflict.claim_a) + "</div>" +
          '<div class="conflict-side"><span class="conflict-source">' +
            escapeHtml(conflict.source_b) + ":</span> " + escapeHtml(conflict.claim_b) + "</div>" +
          '<div class="conflict-why">' + escapeHtml(conflict.explanation) + "</div>" +
        "</div>"
      );
    }
    element("conflictList").innerHTML = blocks.join("");
  }

  /* --- sections --- */
  const sections = [];
  for (let index = 0; index < report.sections.length; index = index + 1) {
    const section = report.sections[index];

    let statusBadge = "";
    if (section.status === "skipped_no_budget") {
      statusBadge = ' <span class="badge badge-warn">not researched: out of budget</span>';
    } else if (section.status === "no_evidence") {
      statusBadge = ' <span class="badge">no evidence found</span>';
    }

    let body = section.findings;
    if (body.trim() === "") { body = section.note || "Not researched."; }

    sections.push(
      '<div class="section-block">' +
        '<div class="section-question">' + escapeHtml(section.sub_question) + statusBadge + "</div>" +
        '<div class="section-body">' + highlightMarkers(body) + "</div>" +
      "</div>"
    );
  }
  element("sectionList").innerHTML = sections.join("");

  /* --- gaps --- */
  if (report.gaps.length > 0) {
    element("gapsBlock").hidden = false;
    const items = [];
    for (let index = 0; index < report.gaps.length; index = index + 1) {
      items.push("<li>" + escapeHtml(report.gaps[index]) + "</li>");
    }
    element("gapList").innerHTML = items.join("");
  }

  /* --- citations --- */
  const citations = [];
  for (let index = 0; index < report.citations.length; index = index + 1) {
    const citation = report.citations[index];
    citations.push(
      '<div class="citation">' +
        '<div class="citation-head">' +
          '<span class="marker">' + citation.marker + "</span>" +
          '<span class="citation-title">' + escapeHtml(citation.title) + "</span>" +
          '<span class="badge type-' + escapeHtml(citation.source_type) + '">' +
            escapeHtml(citation.source_type) + "</span>" +
          '<span class="citation-meta">' + escapeHtml(citation.source_name) + " &middot; " +
            escapeHtml(citation.published_date) + " &middot; credibility " +
            citation.credibility.toFixed(2) + "</span>" +
        "</div>" +
        '<div class="citation-quote">&ldquo;' + escapeHtml(citation.quote) + "&rdquo;</div>" +
      "</div>"
    );
  }
  element("citationList").innerHTML = citations.join("");
}

function renderBudget(response) {
  element("budgetCard").hidden = false;
  const budget = response.trace.budget;

  const parts = [];
  parts.push(makeBar("tokens", budget.tokens.spent, budget.tokens.limit));
  parts.push(makeBar("tool calls", budget.tool_calls.made, budget.tool_calls.limit));
  parts.push(makeBar("seconds", budget.seconds.used, budget.seconds.limit));

  parts.push('<p class="hint" style="margin-top:10px">model calls: ' + budget.model_calls +
             " &middot; cost $" + budget.cost_usd.toFixed(5) + "</p>");

  if (budget.stopped_because !== "none") {
    parts.push('<div class="warn-box">Stopped by the <b>' +
               escapeHtml(budget.stopped_because) + "</b> limit.</div>");
  }

  element("budgetDetail").innerHTML = parts.join("");
}

function makeBar(label, used, limit) {
  let fraction = 0;
  if (limit > 0) { fraction = used / limit; }
  if (fraction > 1) { fraction = 1; }

  let fillClass = "bar-fill";
  if (fraction >= 0.99) { fillClass = "bar-fill full"; }
  else if (fraction >= 0.75) { fillClass = "bar-fill high"; }

  return '<div class="bar-label"><span>' + escapeHtml(label) + "</span><span>" +
         Math.round(used) + " / " + Math.round(limit) + "</span></div>" +
         '<div class="bar"><div class="' + fillClass + '" style="width:' +
         (fraction * 100).toFixed(1) + '%"></div></div>';
}

/* ---------- trace ---------- */

function renderTrace() {
  const holder = element("traceBody");
  if (lastResponse === null) {
    holder.innerHTML = '<p class="hint">Run a question first.</p>';
    return;
  }

  const trace = lastResponse.trace;

  const stageRows = [];
  for (let index = 0; index < trace.stages.length; index = index + 1) {
    const stage = trace.stages[index];
    stageRows.push(
      "<tr><td><b>" + escapeHtml(stage.name) + "</b></td>" +
      '<td class="mono">' + stage.seconds.toFixed(3) + "s</td>" +
      '<td class="mono">' + stage.model_calls + "</td>" +
      '<td class="mono">' + stage.tokens + "</td>" +
      '<td class="mono">$' + stage.cost_usd.toFixed(5) + "</td>" +
      "<td>" + escapeHtml(stage.detail) + "</td></tr>"
    );
  }

  const planRows = [];
  for (let index = 0; index < lastResponse.plan.sub_questions.length; index = index + 1) {
    const sub = lastResponse.plan.sub_questions[index];
    planRows.push(
      "<tr><td>" + escapeHtml(sub.text) + "</td>" +
      "<td>" + escapeHtml(sub.status) + "</td>" +
      '<td class="mono">' + sub.evidence_ids.length + "</td>" +
      '<td class="mono">' + sub.worker_seconds.toFixed(2) + "s</td>" +
      "<td>" + escapeHtml(sub.note) + "</td></tr>"
    );
  }

  holder.innerHTML =
    "<h3>Stages</h3>" +
    "<table><tr><th>stage</th><th>time</th><th>model calls</th><th>tokens</th><th>cost</th><th>detail</th></tr>" +
    stageRows.join("") + "</table>" +
    "<h3>Sub-questions</h3>" +
    "<table><tr><th>sub-question</th><th>status</th><th>evidence</th><th>worker time</th><th>note</th></tr>" +
    planRows.join("") + "</table>" +
    "<h3>Evidence funnel</h3>" +
    "<table>" +
      "<tr><td>documents read</td><td class='mono'>" + trace.documents_seen + "</td></tr>" +
      "<tr><td>evidence extracted</td><td class='mono'>" + trace.evidence_collected + "</td></tr>" +
      "<tr><td>duplicates removed</td><td class='mono'>" + trace.duplicates_removed + "</td></tr>" +
      "<tr><td>evidence kept</td><td class='mono'>" + trace.evidence_after_dedupe + "</td></tr>" +
      "<tr><td>conflicts found</td><td class='mono'>" + trace.conflicts_found + "</td></tr>" +
      "<tr><td>workers run / skipped</td><td class='mono'>" + trace.workers_run + " / " + trace.workers_skipped + "</td></tr>" +
    "</table>" +
    "<h3>Budget</h3><pre>" + escapeHtml(JSON.stringify(trace.budget, null, 2)) + "</pre>";
}

/* ---------- corpus ---------- */

async function loadSources() {
  const holder = element("sourceList");
  try {
    const payload = await callApi("/api/sources");
    const rows = [];
    for (let index = 0; index < payload.documents.length; index = index + 1) {
      const document_ = payload.documents[index];
      rows.push(
        '<div class="source-row">' +
          '<div class="source-head">' +
            '<span class="source-title">' + escapeHtml(document_.title) + "</span>" +
            '<span class="badge type-' + escapeHtml(document_.source_type) + '">' +
              escapeHtml(document_.source_type) + "</span>" +
            '<span class="citation-meta">' + escapeHtml(document_.source_name) + " &middot; " +
              escapeHtml(document_.published_date) + " &middot; credibility " +
              document_.credibility.toFixed(2) + "</span>" +
          "</div>" +
          '<div class="citation-quote">' + escapeHtml(document_.body.slice(0, 240)) + "...</div>" +
        "</div>"
      );
    }
    holder.innerHTML = rows.join("");
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

/* ---------- metrics ---------- */

function makeMetricCard(value, label) {
  return '<div class="metric"><div class="metric-value">' + value +
         '</div><div class="metric-label">' + label + "</div></div>";
}

async function loadMetrics() {
  const holder = element("metricCards");
  try {
    const snapshot = await callApi("/api/metrics");
    const counters = snapshot.counters || {};
    const rates = snapshot.rates || {};
    const distributions = snapshot.distributions || {};

    const seconds = distributions.run_seconds || { p50: 0, p95: 0 };
    const tokens = distributions.run_tokens || { avg: 0 };
    const cost = distributions.run_cost_usd || { avg: 0 };
    const confidence = distributions.run_confidence || { avg: 0 };

    const cards = [
      makeMetricCard(counters.runs_total || 0, "runs"),
      makeMetricCard(Math.round((rates.budget_exhausted_rate || 0) * 100) + "%", "hit a budget limit"),
      makeMetricCard(Math.round((rates.partial_report_rate || 0) * 100) + "%", "partial reports"),
      makeMetricCard(Math.round((rates.subquestion_skip_rate || 0) * 100) + "%", "sub-questions skipped"),
      makeMetricCard(counters.subquestions_planned_total || 0, "sub-questions planned"),
      makeMetricCard(counters.duplicates_removed_total || 0, "duplicates removed"),
      makeMetricCard(counters.conflicts_found_total || 0, "conflicts found"),
      makeMetricCard(counters.invented_quotes_total || 0, "invented quotes caught"),
      makeMetricCard(counters.invented_citations_total || 0, "invented citations caught"),
      makeMetricCard(counters.unsupported_numbers_total || 0, "unsupported figures caught"),
      makeMetricCard(seconds.p50.toFixed(1) + "s", "p50 run time"),
      makeMetricCard(seconds.p95.toFixed(1) + "s", "p95 run time"),
      makeMetricCard(Math.round(tokens.avg || 0), "tokens per run"),
      makeMetricCard("$" + Number(cost.avg || 0).toFixed(5), "cost per run"),
      makeMetricCard(Number(confidence.avg || 0).toFixed(2), "average confidence")
    ];
    holder.innerHTML = cards.join("");
    element("metricsRaw").textContent = JSON.stringify(snapshot, null, 2);
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

/* ---------- wiring ---------- */

function start() {
  setUpTabs();

  element("researchButton").addEventListener("click", runResearch);
  element("questionInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { runResearch(); }
  });
  element("refreshMetrics").addEventListener("click", loadMetrics);

  const chips = document.querySelectorAll(".chip");
  for (let index = 0; index < chips.length; index = index + 1) {
    chips[index].addEventListener("click", function () {
      element("questionInput").value = this.getAttribute("data-q");
      runResearch();
    });
  }

  loadStatus();
}

start();
