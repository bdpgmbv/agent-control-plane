/* ==========================================================================
   AI Customer Support Agent - browser code
   Plain JavaScript, no framework. Every function does one thing.
   ========================================================================== */

let conversationId = "";

/* ---------- helpers ---------- */

function element(id) {
  return document.getElementById(id);
}

function currentApiKey() {
  return element("identitySelect").value;
}

function isSupportStaff() {
  return currentApiKey() === "human-agent-key";
}

function escapeHtml(text) {
  const holder = document.createElement("div");
  holder.textContent = text === null || text === undefined ? "" : String(text);
  return holder.innerHTML;
}

async function callApi(path, options) {
  const settings = options || {};
  const headers = settings.headers || {};
  headers["X-API-Key"] = currentApiKey();

  const response = await fetch(path, {
    method: settings.method || "GET",
    headers: headers,
    body: settings.body
  });

  const text = await response.text();
  let payload = null;
  if (text.length > 0) {
    try { payload = JSON.parse(text); } catch (error) { payload = { detail: text }; }
  }

  if (!response.ok) {
    const message = payload && payload.detail ? payload.detail : "request failed";
    throw new Error(message);
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

      if (panelId === "panelApprovals") { loadApprovals(); }
      if (panelId === "panelTickets") { loadTickets(); }
      if (panelId === "panelAudit") { loadAudit(); }
      if (panelId === "panelTools") { loadTools(); }
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
    let modeText = "OFFLINE - rule-based planner, no API key";
    if (config.llm_is_live) {
      modeClass = "pill-live";
      modeText = "LIVE - " + config.llm_model;
    }

    const pills = [
      '<span class="pill ' + modeClass + '">' + escapeHtml(modeText) + "</span>",
      '<span class="pill">auto-refund limit $' + config.refund_auto_approve_limit.toFixed(2) + "</span>",
      '<span class="pill">max ' + config.max_tool_steps + " tool steps</span>",
      '<span class="pill">' + health.counts.orders + " orders &middot; " + health.counts.tickets + " tickets</span>"
    ];
    strip.innerHTML = pills.join("");
  } catch (error) {
    strip.innerHTML = '<span class="pill pill-offline">server unreachable: ' + escapeHtml(error.message) + "</span>";
  }
}

/* ---------- chat ---------- */

function addBubble(cssClass, text) {
  const window_ = element("chatWindow");
  const empty = window_.querySelector(".chat-empty");
  if (empty !== null) { empty.remove(); }

  const bubble = document.createElement("div");
  bubble.className = "bubble " + cssClass;
  bubble.textContent = text;
  window_.appendChild(bubble);
  window_.scrollTop = window_.scrollHeight;
  return bubble;
}

function toolChipClass(outcome, replayed) {
  if (replayed) { return "tool-replay"; }
  if (outcome === "ok") { return "tool-ok"; }
  if (outcome === "needs_approval") { return "tool-approval"; }
  if (outcome === "denied" || outcome === "not_allowed") { return "tool-denied"; }
  return "tool-failed";
}

function addTurnMeta(response) {
  const window_ = element("chatWindow");
  const holder = document.createElement("div");
  holder.className = "turn-meta";

  const parts = [];
  parts.push('<span class="badge badge-info">' + escapeHtml(response.intent) +
             " " + Math.round(response.intent_confidence * 100) + "%</span>");

  for (let index = 0; index < response.tool_calls.length; index = index + 1) {
    const trace = response.tool_calls[index];
    let label = trace.tool_name + " &rarr; " + trace.outcome;
    if (trace.idempotent_replay) { label = trace.tool_name + " &rarr; replayed"; }
    if (trace.attempts > 1) { label = label + " (" + trace.attempts + " attempts)"; }
    parts.push('<span class="tool-chip ' + toolChipClass(trace.outcome, trace.idempotent_replay) +
               '">' + label + "</span>");
  }

  if (response.pii_redacted.length > 0) {
    parts.push('<span class="badge badge-warn">redacted: ' +
               escapeHtml(response.pii_redacted.join(", ")) + "</span>");
  }
  if (response.resolved) {
    parts.push('<span class="badge badge-good">resolved</span>');
  }
  parts.push('<span class="badge">' + response.usage.latency_ms + " ms</span>");
  parts.push('<span class="badge">' + response.usage.total_tokens + " tokens</span>");
  parts.push('<span class="badge">$' + response.usage.estimated_cost_usd.toFixed(6) + "</span>");

  holder.innerHTML = parts.join("");
  window_.appendChild(holder);
  window_.scrollTop = window_.scrollHeight;
}

function showTurnDetail(response) {
  element("turnDetailCard").hidden = false;

  const stageRows = [];
  const stages = response.usage.by_stage || {};
  const names = Object.keys(stages);
  for (let index = 0; index < names.length; index = index + 1) {
    const name = names[index];
    stageRows.push("<tr><td>" + escapeHtml(name) + "</td><td class='mono'>" +
                   stages[name].calls + "</td><td class='mono'>" + stages[name].tokens +
                   "</td><td class='mono'>$" + stages[name].cost_usd.toFixed(6) + "</td></tr>");
  }

  const toolRows = [];
  for (let index = 0; index < response.tool_calls.length; index = index + 1) {
    const trace = response.tool_calls[index];
    toolRows.push(
      "<tr><td>" + escapeHtml(trace.tool_name) + "</td>" +
      "<td class='risk-" + escapeHtml(trace.risk) + "'>" + escapeHtml(trace.risk) + "</td>" +
      "<td>" + escapeHtml(trace.outcome) + "</td>" +
      "<td class='mono'>" + trace.latency_ms + "ms</td></tr>"
    );
  }

  let escalationHtml = '<p class="hint">Not escalated.</p>';
  if (response.escalation.escalated) {
    escalationHtml =
      '<div class="badge badge-warn">' + escapeHtml(response.escalation.reason) + "</div>" +
      '<p class="hint" style="margin-top:8px">' + escapeHtml(response.escalation.explanation) +
      "<br>ticket " + escapeHtml(response.escalation.ticket_id) + "</p>";
  }

  element("turnDetail").innerHTML =
    "<h3>Escalation</h3>" + escalationHtml +
    "<h3>Tool calls</h3>" +
    (toolRows.length > 0
      ? "<table><tr><th>tool</th><th>risk</th><th>outcome</th><th>time</th></tr>" + toolRows.join("") + "</table>"
      : '<p class="hint">No tools were used.</p>') +
    "<h3>Where the tokens went</h3>" +
    (stageRows.length > 0
      ? "<table><tr><th>stage</th><th>calls</th><th>tokens</th><th>cost</th></tr>" + stageRows.join("") + "</table>"
      : '<p class="hint">No model calls.</p>');
}

async function sendMessage() {
  const input = element("messageInput");
  const text = input.value.trim();
  if (text === "") { return; }

  const button = element("sendButton");
  button.disabled = true;
  input.value = "";

  addBubble("bubble-user", text);
  const thinking = addBubble("bubble-agent", "...");

  try {
    const response = await postJson("/api/chat", {
      message: text,
      conversation_id: conversationId
    });

    conversationId = response.conversation_id;
    thinking.textContent = response.reply;

    addTurnMeta(response);
    showTurnDetail(response);
    loadStatus();
    refreshApprovalCount();
  } catch (error) {
    thinking.textContent = "Error: " + error.message;
    thinking.style.color = "var(--bad)";
  } finally {
    button.disabled = false;
    input.focus();
  }
}

function startNewConversation() {
  conversationId = "";
  element("chatWindow").innerHTML =
    '<div class="chat-empty">New conversation started. Ask about an order, a refund or a policy.</div>';
  element("turnDetailCard").hidden = true;
}

/* ---------- approvals ---------- */

async function refreshApprovalCount() {
  const badge = element("approvalCount");
  if (!isSupportStaff()) {
    badge.textContent = "0";
    badge.className = "badge-count zero";
    return;
  }
  try {
    const pending = await callApi("/api/approvals?status=pending");
    badge.textContent = String(pending.length);
    if (pending.length === 0) { badge.className = "badge-count zero"; }
    else { badge.className = "badge-count"; }
  } catch (error) {
    badge.textContent = "0";
    badge.className = "badge-count zero";
  }
}

async function loadApprovals() {
  const holder = element("approvalList");

  if (!isSupportStaff()) {
    holder.innerHTML = '<p class="hint">Sign in as <b>Support staff</b> on the Chat tab to see the approval queue.</p>';
    return;
  }

  holder.textContent = "loading...";
  try {
    const approvals = await callApi("/api/approvals");
    if (approvals.length === 0) {
      holder.innerHTML = '<p class="hint">Nothing waiting. Ask for a refund above the automatic limit to create one.</p>';
      return;
    }

    const rows = [];
    for (let index = 0; index < approvals.length; index = index + 1) {
      const approval = approvals[index];

      let statusBadge = '<span class="badge badge-warn">pending</span>';
      if (approval.status === "approved") { statusBadge = '<span class="badge badge-good">approved</span>'; }
      if (approval.status === "rejected") { statusBadge = '<span class="badge badge-bad">rejected</span>'; }

      let actions = "";
      if (approval.status === "pending") {
        actions =
          '<div class="row-actions">' +
            '<input type="text" placeholder="note (optional)" id="note-' + approval.approval_id + '">' +
            '<button class="primary small" data-approve="' + approval.approval_id + '">Approve</button>' +
            '<button class="danger small" data-reject="' + approval.approval_id + '">Reject</button>' +
          "</div>";
      }

      rows.push(
        '<div class="row">' +
          '<div class="row-head">' +
            statusBadge +
            '<span class="row-title">' + escapeHtml(approval.tool_name) + "</span>" +
            '<span class="row-meta">' + escapeHtml(approval.approval_id) + " &middot; " +
              escapeHtml(approval.customer_id) + "</span>" +
          "</div>" +
          '<div class="row-meta">' + escapeHtml(JSON.stringify(approval.arguments)) + "</div>" +
          '<p class="hint" style="margin:8px 0 0">' + escapeHtml(approval.reason) + "</p>" +
          actions +
        "</div>"
      );
    }
    holder.innerHTML = rows.join("");

    wireApprovalButtons(holder, "data-approve", true);
    wireApprovalButtons(holder, "data-reject", false);
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

function wireApprovalButtons(holder, attribute, approve) {
  const buttons = holder.querySelectorAll("button[" + attribute + "]");
  for (let index = 0; index < buttons.length; index = index + 1) {
    buttons[index].addEventListener("click", async function () {
      const approvalId = this.getAttribute(attribute);
      const noteField = element("note-" + approvalId);
      let note = "";
      if (noteField !== null) { note = noteField.value; }

      this.disabled = true;
      try {
        const result = await postJson("/api/approvals/decide", {
          approval_id: approvalId,
          approve: approve,
          note: note
        });
        alert(result.message);
        loadApprovals();
        refreshApprovalCount();
      } catch (error) {
        alert("Failed: " + error.message);
        this.disabled = false;
      }
    });
  }
}

/* ---------- tickets ---------- */

async function loadTickets() {
  const holder = element("ticketList");
  holder.textContent = "loading...";
  try {
    const tickets = await callApi("/api/tickets");
    if (tickets.length === 0) {
      holder.innerHTML = '<p class="hint">No tickets yet.</p>';
      return;
    }

    const rows = [];
    for (let index = 0; index < tickets.length; index = index + 1) {
      const ticket = tickets[index];
      rows.push(
        "<tr>" +
          "<td class='mono'>" + escapeHtml(ticket.ticket_id) + "</td>" +
          "<td>" + escapeHtml(ticket.category) + "</td>" +
          "<td>" + escapeHtml(ticket.priority) + "</td>" +
          "<td>" + escapeHtml(ticket.summary) + "</td>" +
          "<td class='mono'>" + escapeHtml(ticket.customer_id) + "</td>" +
          "<td class='mono'>" + escapeHtml(ticket.created_at.slice(0, 16).replace("T", " ")) + "</td>" +
        "</tr>"
      );
    }
    holder.innerHTML =
      "<table><tr><th>ticket</th><th>category</th><th>priority</th><th>summary</th>" +
      "<th>customer</th><th>created</th></tr>" + rows.join("") + "</table>";
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

/* ---------- audit ---------- */

async function loadAudit() {
  const holder = element("auditList");

  if (!isSupportStaff()) {
    holder.innerHTML = '<p class="hint">Sign in as <b>Support staff</b> on the Chat tab to read the audit log.</p>';
    return;
  }

  holder.textContent = "loading...";
  try {
    const rows = await callApi("/api/audit?limit=120");
    if (rows.length === 0) {
      holder.innerHTML = '<p class="hint">Nothing recorded yet.</p>';
      return;
    }

    const lines = [];
    for (let index = 0; index < rows.length; index = index + 1) {
      const record = rows[index];
      let rowClass = "";
      if (!record.allowed) { rowClass = ' class="row-denied"'; }

      let allowedText = "allowed";
      if (!record.allowed) { allowedText = "DENIED"; }

      lines.push(
        "<tr" + rowClass + ">" +
          "<td class='mono'>" + escapeHtml(record.created_at.slice(11, 19)) + "</td>" +
          "<td>" + escapeHtml(record.action) + "</td>" +
          "<td class='mono'>" + escapeHtml(record.tool_name) + "</td>" +
          "<td class='risk-" + escapeHtml(record.risk) + "'>" + escapeHtml(record.risk) + "</td>" +
          "<td>" + allowedText + "</td>" +
          "<td>" + escapeHtml(record.outcome) + "</td>" +
          "<td class='mono'>" + escapeHtml(record.actor) + "</td>" +
          "<td class='mono'>" + escapeHtml(record.detail) + "</td>" +
        "</tr>"
      );
    }
    holder.innerHTML =
      "<table><tr><th>time</th><th>action</th><th>tool</th><th>risk</th><th></th>" +
      "<th>outcome</th><th>actor</th><th>detail</th></tr>" + lines.join("") + "</table>";
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

/* ---------- tools ---------- */

async function loadTools() {
  const holder = element("toolList");
  try {
    const tools = await callApi("/api/tools");
    const rows = [];
    for (let index = 0; index < tools.length; index = index + 1) {
      const tool = tools[index];
      let extra = "";
      if (tool.needs_idempotency_key) {
        extra = '<span class="badge badge-info">idempotent</span>';
      }
      rows.push(
        '<div class="row">' +
          '<div class="row-head">' +
            '<span class="row-title">' + escapeHtml(tool.name) + "</span>" +
            '<span class="badge risk-' + escapeHtml(tool.risk) + '">' + escapeHtml(tool.risk) + "</span>" +
            extra +
            '<span class="row-meta">(' + escapeHtml(tool.parameters.join(", ")) + ")</span>" +
          "</div>" +
          '<p class="hint" style="margin:0">' + escapeHtml(tool.description) + "</p>" +
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

    const turnLatency = distributions.turn_latency_ms || { p50: 0, p95: 0 };
    const tokens = distributions.tokens_per_turn || { avg: 0 };
    const cost = distributions.cost_usd_per_turn || { avg: 0 };

    const cards = [
      makeMetricCard(counters.conversations_total || 0, "conversations"),
      makeMetricCard(counters.turns_total || 0, "turns"),
      makeMetricCard(Math.round((rates.resolution_rate || 0) * 100) + "%", "resolution rate"),
      makeMetricCard(Math.round((rates.escalation_rate || 0) * 100) + "%", "escalation rate"),
      makeMetricCard(counters.tool_calls_total || 0, "tool calls"),
      makeMetricCard(Math.round((rates.tool_success_rate || 0) * 100) + "%", "tool success rate"),
      makeMetricCard(counters.tool_calls_denied_total || 0, "tool calls denied"),
      makeMetricCard(counters.tool_calls_replayed_total || 0, "idempotent replays"),
      makeMetricCard(counters.tool_retries_total || 0, "retries"),
      makeMetricCard(counters.cross_customer_attempts_total || 0, "cross-customer attempts"),
      makeMetricCard(counters.pii_redactions_total || 0, "PII items redacted"),
      makeMetricCard(counters.step_limit_reached_total || 0, "step limit hit"),
      makeMetricCard(turnLatency.p50 + " ms", "p50 turn latency"),
      makeMetricCard(turnLatency.p95 + " ms", "p95 turn latency"),
      makeMetricCard(Math.round(tokens.avg || 0), "tokens per turn"),
      makeMetricCard("$" + Number(cost.avg || 0).toFixed(6), "cost per turn")
    ];
    holder.innerHTML = cards.join("");
    element("metricsRaw").textContent = JSON.stringify(snapshot, null, 2);
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load: ' + escapeHtml(error.message) + "</p>";
  }
}

async function resetDemo() {
  if (!isSupportStaff()) {
    alert("Sign in as Support staff to reset the demo.");
    return;
  }
  if (!confirm("Reset all conversations, tickets, refunds and metrics?")) { return; }

  try {
    await postJson("/api/demo/reset", {});
    startNewConversation();
    loadMetrics();
    loadStatus();
    refreshApprovalCount();
    alert("Demo reset.");
  } catch (error) {
    alert("Failed: " + error.message);
  }
}

/* ---------- wiring ---------- */

function setUpSampleChips() {
  const chips = document.querySelectorAll(".chip");
  for (let index = 0; index < chips.length; index = index + 1) {
    chips[index].addEventListener("click", function () {
      element("messageInput").value = this.getAttribute("data-q");
      sendMessage();
    });
  }
}

function start() {
  setUpTabs();
  setUpSampleChips();

  element("sendButton").addEventListener("click", sendMessage);
  element("messageInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { sendMessage(); }
  });
  element("newConversationButton").addEventListener("click", startNewConversation);
  element("identitySelect").addEventListener("change", function () {
    startNewConversation();
    loadStatus();
    refreshApprovalCount();
  });
  element("refreshApprovals").addEventListener("click", loadApprovals);
  element("refreshTickets").addEventListener("click", loadTickets);
  element("refreshAudit").addEventListener("click", loadAudit);
  element("refreshMetrics").addEventListener("click", loadMetrics);
  element("resetDemoButton").addEventListener("click", resetDemo);

  loadStatus();
  refreshApprovalCount();
}

start();
