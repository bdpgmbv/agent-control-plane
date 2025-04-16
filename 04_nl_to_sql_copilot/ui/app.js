/* ==========================================================================
   NL to SQL Analytics Copilot - browser code. Plain JavaScript, no framework.
   ========================================================================== */

function element(id) { return document.getElementById(id); }

function escapeHtml(text) {
  const holder = document.createElement("div");
  holder.textContent = text === null || text === undefined ? "" : String(text);
  return holder.innerHTML;
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

      if (panelId === "panelSchema") { loadSchema(); }
      if (panelId === "panelMetrics") { loadMetrics(); }
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
    let modeText = "OFFLINE - SQL from templates";
    if (config.llm_is_live) {
      modeClass = "pill-live";
      modeText = "LIVE - " + config.llm_model;
    }

    strip.innerHTML =
      '<span class="pill ' + modeClass + '">' + escapeHtml(modeText) + "</span>" +
      '<span class="pill">database: read-only</span>' +
      '<span class="pill">' + health.tables_available + " of " + health.tables_in_file + " tables available</span>" +
      '<span class="pill">timeout ' + config.limits.query_timeout_seconds + "s</span>" +
      '<span class="pill">max ' + config.limits.max_rows + " rows</span>";
  } catch (error) {
    strip.innerHTML = '<span class="pill pill-offline">server unreachable</span>';
  }
}

/* ---------- asking ---------- */

function renderResultTable(result) {
  if (result === null || result === undefined || !result.ok) { return ""; }
  if (result.columns.length === 0) { return ""; }

  const header = [];
  for (let index = 0; index < result.columns.length; index = index + 1) {
    header.push("<th>" + escapeHtml(result.columns[index]) + "</th>");
  }

  const bodyRows = [];
  for (let index = 0; index < result.rows.length; index = index + 1) {
    const cells = [];
    const row = result.rows[index];

    for (let position = 0; position < row.length; position = position + 1) {
      const value = row[position];
      let cssClass = "";
      if (typeof value === "number") { cssClass = ' class="number"'; }

      let shown = value;
      if (value === null) { shown = ""; }
      cells.push("<td" + cssClass + ">" + escapeHtml(shown) + "</td>");
    }
    bodyRows.push("<tr>" + cells.join("") + "</tr>");
  }

  return "<table><tr>" + header.join("") + "</tr>" + bodyRows.join("") + "</table>";
}

function renderAttempts(attempts) {
  const blocks = [];

  for (let index = 0; index < attempts.length; index = index + 1) {
    const attempt = attempts[index];

    let verdict = '<span class="badge">not validated</span>';
    if (attempt.validation !== null && attempt.validation !== undefined) {
      if (attempt.validation.allowed) {
        verdict = '<span class="badge badge-good">validation passed</span>';
      } else {
        verdict = '<span class="badge badge-bad">refused: ' +
                  escapeHtml(attempt.validation.reason) + "</span>";
      }
    }

    let executed = '<span class="badge">not executed</span>';
    if (attempt.executed) {
      if (attempt.error === "") {
        executed = '<span class="badge badge-good">executed</span>';
      } else {
        executed = '<span class="badge badge-bad">failed</span>';
      }
    }

    let repaired = "";
    if (attempt.repaired_from !== "") {
      repaired = '<span class="badge badge-warn">a repair</span>';
    }

    let errorLine = "";
    if (attempt.error !== "") {
      errorLine = '<div class="hint" style="margin:6px 0 0">' + escapeHtml(attempt.error) + "</div>";
    }

    blocks.push(
      '<div class="attempt">' +
        '<div class="attempt-head"><b>attempt ' + attempt.attempt + "</b>" +
          verdict + executed + repaired +
          '<span class="hint" style="margin:0">' + attempt.seconds.toFixed(3) + "s</span>" +
        "</div>" +
        "<pre>" + escapeHtml(attempt.sql) + "</pre>" +
        errorLine +
      "</div>"
    );
  }

  return blocks.join("");
}

async function ask() {
  const question = element("questionInput").value.trim();
  if (question === "") { return; }

  const button = element("askButton");
  button.disabled = true;
  button.textContent = "Working...";

  element("answerCard").hidden = false;
  element("answerText").innerHTML = '<span class="spinner">retrieving schema, writing SQL, validating...</span>';
  element("refusalBox").hidden = true;
  element("sqlBlock").hidden = true;
  element("resultBlock").hidden = true;
  element("attemptsBlock").hidden = true;
  element("answerBadges").innerHTML = "";

  try {
    const response = await postJson("/api/ask", {
      question: question,
      max_rows: Number(element("maxRows").value)
    });

    const badges = [];
    if (response.answered) {
      badges.push('<span class="badge badge-good">answered</span>');
    } else if (response.refused) {
      badges.push('<span class="badge badge-bad">refused</span>');
    } else {
      badges.push('<span class="badge badge-warn">no answer</span>');
    }

    if (response.repaired) {
      badges.push('<span class="badge badge-warn">repaired</span>');
    }
    if (response.result !== null && response.result !== undefined && response.result.ok) {
      badges.push('<span class="badge">' + response.result.row_count + " rows</span>");
      if (response.result.truncated) {
        badges.push('<span class="badge badge-warn">truncated</span>');
      }
      badges.push('<span class="badge">' + response.result.seconds.toFixed(3) + "s query</span>");
    }
    badges.push('<span class="badge">' + response.usage.latency_ms + " ms</span>");
    badges.push('<span class="badge">' + response.usage.total_tokens + " tokens</span>");
    badges.push('<span class="badge">$' + response.usage.estimated_cost_usd.toFixed(6) + "</span>");
    element("answerBadges").innerHTML = badges.join("");

    if (response.refused) {
      element("refusalBox").hidden = false;
      element("refusalBox").innerHTML =
        "<b>Refused by rule: " + escapeHtml(response.refusal_reason) + "</b><br>" +
        escapeHtml(response.refusal_message) +
        "<br><br>The query was never sent to the database.";
      element("answerText").innerHTML = "";
    } else {
      element("answerText").innerHTML = escapeHtml(response.answer);
    }

    if (response.sql !== "") {
      element("sqlBlock").hidden = false;
      element("sqlText").textContent = response.sql;

      const notes = [];
      if (response.tables_used.length > 0) {
        notes.push("tables: " + response.tables_used.join(", "));
      }
      if (response.schema_tables_offered.length > 0) {
        notes.push("schema offered: " + response.schema_tables_offered.join(", "));
      }
      element("sqlNotes").textContent = notes.join("  |  ");
    }

    const tableHtml = renderResultTable(response.result);
    if (tableHtml !== "") {
      element("resultBlock").hidden = false;
      element("resultTable").innerHTML = tableHtml;
    }

    if (response.attempts.length > 0) {
      element("attemptsBlock").hidden = false;
      element("attemptsBody").innerHTML = renderAttempts(response.attempts);
    }

    loadStatus();
  } catch (error) {
    element("answerText").innerHTML =
      '<span style="color:var(--bad)">Failed: ' + escapeHtml(error.message) + "</span>";
  } finally {
    button.disabled = false;
    button.textContent = "Ask";
  }
}

/* ---------- schema ---------- */

async function loadSchema() {
  const holder = element("schemaList");
  try {
    const payload = await callApi("/api/schema");

    if (payload.not_available.length > 0) {
      element("hiddenTables").innerHTML =
        '<div class="warn-box"><b>In the database but NOT available to the copilot:</b> ' +
        escapeHtml(payload.not_available.join(", ")) +
        ". Ask about it and the query is refused by rule 7.</div>";
    }

    const blocks = [];
    for (let index = 0; index < payload.tables.length; index = index + 1) {
      const table = payload.tables[index];

      const columns = [];
      for (let position = 0; position < table.columns.length; position = position + 1) {
        const column = table.columns[position];

        let extra = "";
        if (column.is_primary_key) { extra = extra + "  PRIMARY KEY"; }
        if (column.references !== "") { extra = extra + "  -> " + column.references; }
        if (column.description !== "") { extra = extra + "   -- " + column.description; }

        columns.push(
          '<div class="column-row"><span class="column-name">' + escapeHtml(column.name) +
          "</span> " + escapeHtml(column.data_type) + escapeHtml(extra) + "</div>"
        );
      }

      blocks.push(
        '<div class="schema-table">' +
          '<div class="schema-head">' +
            '<span class="schema-name">' + escapeHtml(table.name) + "</span>" +
            '<span class="hint" style="margin:0">' + table.row_count + " rows</span>" +
          "</div>" +
          '<div class="hint" style="margin:0 0 6px">' + escapeHtml(table.description) + "</div>" +
          columns.join("") +
        "</div>"
      );
    }
    holder.innerHTML = blocks.join("");
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

    const latency = distributions.question_latency_ms || { p50: 0, p95: 0 };
    const querySeconds = distributions.query_seconds || { p50: 0, p95: 0 };
    const cost = distributions.cost_usd_per_question || { avg: 0 };

    const cards = [
      makeMetricCard(counters.questions_total || 0, "questions"),
      makeMetricCard(Math.round((rates.answer_rate || 0) * 100) + "%", "answered"),
      makeMetricCard(counters.queries_refused_total || 0, "queries refused"),
      makeMetricCard(counters.refused_unknown_table_total || 0, "refused: unknown table"),
      makeMetricCard(counters.refused_not_a_select_total || 0, "refused: not a SELECT"),
      makeMetricCard(counters.refused_multiple_statements_total || 0, "refused: stacked statement"),
      makeMetricCard(counters.refused_forbidden_keyword_total || 0, "refused: forbidden keyword"),
      makeMetricCard(counters.queries_failed_total || 0, "queries that errored"),
      makeMetricCard(counters.queries_timed_out_total || 0, "queries timed out"),
      makeMetricCard(counters.repairs_attempted_total || 0, "repairs attempted"),
      makeMetricCard(Math.round((rates.repair_success_rate || 0) * 100) + "%", "repairs that worked"),
      makeMetricCard(counters.injection_signals_in_results_total || 0, "injection text in results"),
      makeMetricCard(latency.p50 + " ms", "p50 answer time"),
      makeMetricCard(querySeconds.p95.toFixed(3) + "s", "p95 query time"),
      makeMetricCard("$" + Number(cost.avg || 0).toFixed(6), "cost per question")
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

  element("askButton").addEventListener("click", ask);
  element("questionInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask();
    }
  });
  element("refreshMetrics").addEventListener("click", loadMetrics);

  const chips = document.querySelectorAll(".chip");
  for (let index = 0; index < chips.length; index = index + 1) {
    chips[index].addEventListener("click", function () {
      element("questionInput").value = this.getAttribute("data-q");
      ask();
    });
  }

  loadStatus();
}

start();
