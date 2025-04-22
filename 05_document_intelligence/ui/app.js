// =====================================================================
//  The interface for the document pipeline.
//
//  Plain JavaScript, no framework and no build step - the same reasoning as
//  the Python side of this project: use the least machinery that does the job,
//  so what remains is readable.
//
//  Written with explicit for loops rather than chained map/filter, to match the
//  style rule the Python follows.
// =====================================================================

let currentReviewFields = {};

// ---------------------------------------------------------------- helpers

function element(id) {
  return document.getElementById(id);
}

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

function decisionLabel(decision) {
  if (decision === "auto_approve") { return "Processed automatically"; }
  if (decision === "needs_review") { return "Sent to a person"; }
  if (decision === "reject") { return "Rejected - nothing to review"; }
  return decision;
}

function decisionBadgeClass(decision) {
  if (decision === "auto_approve") { return "badge-green"; }
  if (decision === "reject") { return "badge-red"; }
  return "badge-amber";
}

function meterColour(value) {
  if (value >= 0.9) { return "var(--green)"; }
  if (value >= 0.7) { return "var(--amber)"; }
  return "var(--red)";
}

function meter(value) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return '<span class="meter">'
    + '<span class="meter-track"><span class="meter-fill" style="width:'
    + percent + '%;background:' + meterColour(value) + '"></span></span>'
    + '<span class="meter-value">' + value.toFixed(2) + "</span></span>";
}

function money(value) {
  const number = Number(value);
  if (!isFinite(number)) { return escapeHtml(value); }
  return number.toLocaleString(undefined, { minimumFractionDigits: 2,
                                            maximumFractionDigits: 2 });
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

    if (name === "review") { loadReviews(); }
    if (name === "documents") { loadDocuments(); }
  });
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  try {
    const data = await callApi("/api/health");
    const badge = element("mode-badge");
    if (data.mode === "live") {
      badge.textContent = "live - " + data.model;
      badge.className = "badge badge-green";
    } else {
      badge.textContent = "offline - no model calls";
      badge.className = "badge badge-blue";
    }

    const pending = element("pending-badge");
    pending.textContent = data.pending_reviews + " waiting for review";
    pending.className = data.pending_reviews > 0
      ? "badge badge-amber" : "badge badge-grey";

    const gates = data.gates;
    element("gate-values").innerHTML =
      '<h3>The values this instance is running with</h3><table>'
      + "<tr><td>auto-approve confidence</td><td class='number mono'>"
      + gates.auto_approve_confidence.toFixed(2) + "</td></tr>"
      + "<tr><td>minimum per required field</td><td class='number mono'>"
      + gates.min_required_field_confidence.toFixed(2) + "</td></tr>"
      + "<tr><td>always review above</td><td class='number mono'>"
      + money(gates.always_review_above_amount) + "</td></tr>"
      + "<tr><td>arithmetic tolerance</td><td class='number mono'>"
      + gates.arithmetic_tolerance.toFixed(2) + "</td></tr></table>";
  } catch (error) {
    element("mode-badge").textContent = "cannot reach the server";
    element("mode-badge").className = "badge badge-red";
  }
}

// ---------------------------------------------------------------- samples

async function loadSampleList() {
  try {
    const data = await callApi("/api/samples");
    const picker = element("sample-picker");
    for (let index = 0; index < data.samples.length; index++) {
      const option = document.createElement("option");
      option.value = data.samples[index];
      option.textContent = data.samples[index];
      picker.appendChild(option);
    }
  } catch (error) {
    /* the picker simply stays empty */
  }
}

element("sample-picker").addEventListener("change", async function () {
  if (this.value === "") { return; }
  try {
    const data = await callApi("/api/samples/" + encodeURIComponent(this.value));
    element("document-text").value = data.text;
    element("process-status").textContent = "loaded " + data.name;
  } catch (error) {
    element("process-status").textContent = error.message;
  }
});

// ---------------------------------------------------------------- batch run

element("run-samples").addEventListener("click", async function () {
  this.disabled = true;
  this.textContent = "processing…";
  element("batch-summary").innerHTML = "";
  element("batch-table").innerHTML = "";

  try {
    const data = await callApi("/api/process/samples", { method: "POST" });
    renderBatch(data);
    loadHealth();
  } catch (error) {
    element("batch-summary").innerHTML =
      '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }

  this.disabled = false;
  this.textContent = "Process all samples";
});

function renderBatch(data) {
  const summary = data.summary;
  const documents = data.documents;

  let calls = 0;
  let cost = 0;
  let seconds = 0;
  for (let index = 0; index < documents.length; index++) {
    calls = calls + documents[index].model_calls;
    cost = cost + documents[index].cost_usd;
    seconds = seconds + documents[index].seconds;
  }

  const perDocument = documents.length > 0 ? cost / documents.length : 0;

  element("batch-summary").innerHTML =
    '<div class="stat-grid">'
    + stat(summary.processed, "documents")
    + stat(summary.auto_approved, "automatic")
    + stat(summary.needs_review, "to a person")
    + stat(Math.round(summary.straight_through_rate * 100) + "%", "straight through")
    + stat(summary.total_errors, "errors caught")
    + stat(summary.total_warnings, "warnings")
    + stat(calls, "model calls")
    + stat("$" + perDocument.toFixed(6), "per document")
    + "</div>";

  let rows = "";
  for (let index = 0; index < documents.length; index++) {
    const row = documents[index];
    rows += '<tr class="clickable" data-document="' + escapeHtml(row.document_id) + '">'
      + "<td>" + escapeHtml(row.filename) + "</td>"
      + "<td>" + escapeHtml(row.document_type) + "</td>"
      + "<td>" + meter(row.confidence) + "</td>"
      + '<td><span class="badge ' + decisionBadgeClass(row.decision) + '">'
      + escapeHtml(decisionLabel(row.decision)) + "</span></td>"
      + '<td class="number">' + row.errors + "</td>"
      + '<td class="number">' + row.warnings + "</td>"
      + "<td>" + escapeHtml(row.reason) + "</td></tr>";
  }

  element("batch-table").innerHTML =
    "<table><thead><tr><th>file</th><th>type</th><th>confidence</th>"
    + "<th>outcome</th><th>err</th><th>warn</th><th>the deciding reason</th>"
    + "</tr></thead><tbody>" + rows + "</tbody></table>";

  attachDocumentClicks("batch-table");
}

function stat(value, label) {
  return '<div class="stat"><div class="stat-value">' + escapeHtml(value)
    + '</div><div class="stat-label">' + escapeHtml(label) + "</div></div>";
}

element("reset-store").addEventListener("click", async function () {
  try {
    await callApi("/api/reset", { method: "POST" });
    element("batch-summary").innerHTML = "";
    element("batch-table").innerHTML = "";
    element("result-area").innerHTML = "";
    element("process-status").textContent = "store cleared";
    loadHealth();
  } catch (error) {
    element("process-status").textContent = error.message;
  }
});

// ---------------------------------------------------------------- one document

element("process-text").addEventListener("click", async function () {
  const text = element("document-text").value;
  if (text.trim() === "") {
    element("process-status").textContent = "there is nothing to process";
    return;
  }

  this.disabled = true;
  element("process-status").textContent = "processing…";

  try {
    const filename = element("sample-picker").value || "pasted.txt";
    const data = await callApi("/api/process/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, filename: filename }),
    });
    element("process-status").textContent = "";
    element("result-area").innerHTML = renderResult(data.result, null);
    loadHealth();
  } catch (error) {
    element("process-status").innerHTML =
      '<span class="error-text">' + escapeHtml(error.message) + "</span>";
  }

  this.disabled = false;
});

element("file-input").addEventListener("change", async function () {
  if (this.files.length === 0) { return; }
  const file = this.files[0];
  element("process-status").textContent = "uploading " + file.name + "…";

  const form = new FormData();
  form.append("file", file);

  try {
    const data = await callApi("/api/process/upload", { method: "POST", body: form });
    element("process-status").textContent = "";
    element("document-text").value = data.result.document_text || "";
    element("result-area").innerHTML = renderResult(data.result, null);
    loadHealth();
  } catch (error) {
    element("process-status").innerHTML =
      '<span class="error-text">' + escapeHtml(error.message) + "</span>";
  }
  this.value = "";
});

// ---------------------------------------------------------------- rendering

function renderStages(result) {
  const stages = [
    { name: "loaded", done: true },
    { name: result.notes && hasTranscription(result.notes) ? "transcribed (vision)" : "cleaned",
      done: true },
    { name: "classified: " + result.document_type,
      done: result.document_type !== "unknown" },
    { name: "extracted " + countPresent(result.fields) + " fields",
      done: countPresent(result.fields) > 0 },
    { name: "validated: " + result.issues.length + " issue(s)", done: true },
    { name: "scored " + result.confidence.toFixed(2), done: true },
    { name: "decided", done: true },
  ];

  let html = '<div class="stages">';
  for (let index = 0; index < stages.length; index++) {
    const cssClass = stages[index].done ? "stage stage-done" : "stage stage-skipped";
    html += '<span class="' + cssClass + '">' + escapeHtml(stages[index].name) + "</span>";
  }
  return html + "</div>";
}

function hasTranscription(notes) {
  for (let index = 0; index < notes.length; index++) {
    if (notes[index].indexOf("transcribed from an image") === 0) { return true; }
  }
  return false;
}

function countPresent(fields) {
  let count = 0;
  for (let index = 0; index < fields.length; index++) {
    if (fields[index].value && fields[index].value.trim() !== "") { count = count + 1; }
  }
  return count;
}

function renderResult(result, reviewId) {
  let html = '<div class="card">';

  html += "<h2>" + escapeHtml(result.filename) + "</h2>";
  html += renderStages(result);

  let reasons = "";
  for (let index = 0; index < result.decision_reasons.length; index++) {
    reasons += "<li>" + escapeHtml(result.decision_reasons[index]) + "</li>";
  }
  html += '<div class="decision-banner decision-' + escapeHtml(result.decision) + '">'
    + "<h3>" + escapeHtml(decisionLabel(result.decision))
    + "  —  confidence " + result.confidence.toFixed(2) + "</h3>"
    + "<ul>" + reasons + "</ul></div>";

  // ---- issues
  if (result.issues.length > 0) {
    html += "<h3>What the checks found</h3>";
    for (let index = 0; index < result.issues.length; index++) {
      const issue = result.issues[index];
      html += '<div class="issue issue-' + escapeHtml(issue.severity) + '">'
        + '<div class="issue-code">' + escapeHtml(issue.code) + "</div>"
        + escapeHtml(issue.message);
      if (issue.expected !== "" || issue.actual !== "") {
        html += '<div class="issue-detail">expected <span class="mono">'
          + escapeHtml(issue.expected) + '</span>, found <span class="mono">'
          + escapeHtml(issue.actual) + "</span></div>";
      }
      html += "</div>";
    }
  } else {
    html += "<h3>What the checks found</h3><p class='empty'>Every check passed.</p>";
  }

  html += '<div class="two-column">';

  // ---- fields
  html += "<div><h3>Fields</h3><table><thead><tr><th>field</th><th>value</th>"
    + "<th>confidence</th><th>from</th></tr></thead><tbody>";
  for (let index = 0; index < result.fields.length; index++) {
    const field = result.fields[index];
    const present = field.value && field.value.trim() !== "";
    const verbatim = field.found_verbatim ? "" : " ⚠";
    html += "<tr><td>" + escapeHtml(field.name) + "</td>"
      + '<td class="mono">'
      + (present ? escapeHtml(field.value) : '<span class="empty">not found</span>')
      + "</td>"
      + "<td>" + (present ? meter(field.confidence) : "") + "</td>"
      + "<td>" + escapeHtml(field.source) + escapeHtml(verbatim) + "</td></tr>";
  }
  html += "</tbody></table>";
  html += "<p class='hint' style='margin-top:8px'>⚠ marks a value that does "
    + "not appear verbatim in the document.</p></div>";

  // ---- line items
  html += "<div><h3>Line items</h3>";
  if (result.line_items.length > 0) {
    html += "<table><thead><tr><th>description</th><th class='number'>qty</th>"
      + "<th class='number'>unit</th><th class='number'>total</th></tr></thead><tbody>";
    let sum = 0;
    for (let index = 0; index < result.line_items.length; index++) {
      const item = result.line_items[index];
      sum = sum + item.line_total;
      html += "<tr><td>" + escapeHtml(item.description) + "</td>"
        + '<td class="number">' + item.quantity + "</td>"
        + '<td class="number">' + money(item.unit_price) + "</td>"
        + '<td class="number">' + money(item.line_total) + "</td></tr>";
    }
    html += "<tr><td><strong>added up by the pipeline</strong></td><td></td><td></td>"
      + '<td class="number"><strong>' + money(sum) + "</strong></td></tr>";
    html += "</tbody></table>";
  } else {
    html += "<p class='empty'>No line items for this document type.</p>";
  }
  html += "</div></div>";

  // ---- confidence working
  html += "<details><summary>How that confidence was arrived at</summary>";
  for (let index = 0; index < result.confidence_explanation.length; index++) {
    html += '<p class="explain">'
      + escapeHtml(result.confidence_explanation[index]) + "</p>";
  }
  html += "</details>";

  // ---- notes
  if (result.notes && result.notes.length > 0) {
    html += "<details><summary>Processing notes ("
      + result.notes.length + ")</summary>";
    for (let index = 0; index < result.notes.length; index++) {
      html += '<p class="explain">' + escapeHtml(result.notes[index]) + "</p>";
    }
    html += "</details>";
  }

  // ---- cost
  html += '<p class="hint" style="margin-top:14px">'
    + result.model_calls + " model call(s), $" + result.cost_usd.toFixed(6)
    + ", " + result.seconds.toFixed(3) + " s, "
    + result.characters + " characters.</p>";

  // ---- source text and json
  html += "<details><summary>The text the pipeline read</summary>"
    + '<pre class="source">' + escapeHtml(result.document_text) + "</pre></details>";
  html += "<details><summary>Structured JSON output</summary>"
    + '<pre class="json">' + escapeHtml(JSON.stringify(result.structured, null, 2))
    + "</pre></details>";

  html += "</div>";

  if (reviewId !== null) {
    html += renderCorrectionForm(result, reviewId);
  }

  return html;
}

// ---------------------------------------------------------------- reviews

async function loadReviews() {
  const container = element("review-list");
  container.innerHTML = "<p class='empty'>loading…</p>";
  try {
    const data = await callApi("/api/reviews");
    if (data.reviews.length === 0) {
      container.innerHTML = "<p class='empty'>Nothing is waiting. "
        + "Run the samples on the Process tab to fill the queue.</p>";
      return;
    }

    let rows = "";
    for (let index = 0; index < data.reviews.length; index++) {
      const review = data.reviews[index];
      let reasonText = "";
      if (review.reasons.length > 0) { reasonText = review.reasons[0]; }
      rows += '<tr class="clickable" data-review="' + escapeHtml(review.review_id)
        + '" data-document="' + escapeHtml(review.document_id) + '">'
        + "<td>" + escapeHtml(review.filename) + "</td>"
        + "<td>" + escapeHtml(review.document_type) + "</td>"
        + "<td>" + meter(review.confidence) + "</td>"
        + '<td class="number">' + review.issue_count + "</td>"
        + "<td>" + escapeHtml(reasonText) + "</td></tr>";
    }
    container.innerHTML = "<table><thead><tr><th>file</th><th>type</th>"
      + "<th>confidence</th><th>issues</th><th>why it is here</th>"
      + "</tr></thead><tbody>" + rows + "</tbody></table>";

    const rowElements = container.querySelectorAll("tr.clickable");
    for (let index = 0; index < rowElements.length; index++) {
      rowElements[index].addEventListener("click", function () {
        openReview(this.getAttribute("data-review"),
                   this.getAttribute("data-document"));
      });
    }
  } catch (error) {
    container.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }
}

element("refresh-reviews").addEventListener("click", loadReviews);

async function openReview(reviewId, documentId) {
  const container = element("review-detail");
  container.innerHTML = "<p class='empty'>loading…</p>";
  try {
    const data = await callApi("/api/documents/" + encodeURIComponent(documentId));
    currentReviewFields = {};
    container.innerHTML = renderResult(data.result, reviewId);
    attachCorrectionHandlers(reviewId);
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    container.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }
}

function renderCorrectionForm(result, reviewId) {
  let html = '<div class="card"><h2>Resolve this</h2>';
  html += "<p class='hint'>Change any value and press <em>Apply corrections</em>. "
    + "Every check runs again on the new values, and you are told which problems "
    + "the change fixed and which are still there.</p>";

  html += '<div class="correction-grid">';
  for (let index = 0; index < result.fields.length; index++) {
    const field = result.fields[index];
    html += "<label for='fix-" + escapeHtml(field.name) + "'>"
      + escapeHtml(field.name) + "</label>"
      + "<input type='text' id='fix-" + escapeHtml(field.name)
      + "' data-field='" + escapeHtml(field.name)
      + "' value='" + escapeHtml(field.value) + "'>";
  }
  html += "</div>";

  html += '<div class="row">'
    + "<button class='primary' id='apply-corrections'>Apply corrections</button>"
    + "<button class='ghost' id='approve-anyway'>Approve as it is</button>"
    + "<button class='ghost' id='reject-document'>Reject</button>"
    + "</div>";
  html += "<div id='review-outcome'></div></div>";
  return html;
}

function attachCorrectionHandlers(reviewId) {
  const original = {};
  const inputs = document.querySelectorAll("#review-detail input[data-field]");
  for (let index = 0; index < inputs.length; index++) {
    original[inputs[index].getAttribute("data-field")] = inputs[index].value;
  }

  element("apply-corrections").addEventListener("click", function () {
    const corrections = {};
    let changed = 0;
    for (let index = 0; index < inputs.length; index++) {
      const name = inputs[index].getAttribute("data-field");
      if (inputs[index].value !== original[name]) {
        corrections[name] = inputs[index].value;
        changed = changed + 1;
      }
    }
    if (changed === 0) {
      element("review-outcome").innerHTML =
        "<p class='empty'>Nothing was changed.</p>";
      return;
    }
    sendDecision(reviewId, "correct", corrections, "");
  });

  element("approve-anyway").addEventListener("click", function () {
    sendDecision(reviewId, "approve", {}, "approved by a reviewer without changes");
  });
  element("reject-document").addEventListener("click", function () {
    sendDecision(reviewId, "reject", {}, "rejected by a reviewer");
  });
}

async function sendDecision(reviewId, action, corrections, note) {
  const outcome = element("review-outcome");
  outcome.innerHTML = "<p class='empty'>working…</p>";
  try {
    const data = await callApi(
      "/api/reviews/" + encodeURIComponent(reviewId) + "/decide",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_id: reviewId, action: action,
                               corrections: corrections, note: note }),
      });

    let html = "<div class='decision-banner decision-"
      + (data.remaining_issues.length === 0 ? "auto_approve" : "needs_review")
      + "'><h3>" + escapeHtml(data.message) + "</h3>";

    if (data.fixed_issues.length > 0) {
      html += "<ul>";
      for (let index = 0; index < data.fixed_issues.length; index++) {
        html += "<li>fixed: " + escapeHtml(data.fixed_issues[index]) + "</li>";
      }
      html += "</ul>";
    }
    if (data.remaining_issues.length > 0) {
      html += "<ul>";
      for (let index = 0; index < data.remaining_issues.length; index++) {
        html += "<li>still outstanding: "
          + escapeHtml(data.remaining_issues[index]) + "</li>";
      }
      html += "</ul>";
    }
    html += "</div>";
    outcome.innerHTML = html;

    loadReviews();
    loadHealth();
  } catch (error) {
    outcome.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }
}

// ---------------------------------------------------------------- documents

async function loadDocuments() {
  const container = element("documents-list");
  container.innerHTML = "<p class='empty'>loading…</p>";
  try {
    const data = await callApi("/api/documents");
    if (data.documents.length === 0) {
      container.innerHTML = "<p class='empty'>Nothing processed yet.</p>";
      return;
    }
    let rows = "";
    for (let index = 0; index < data.documents.length; index++) {
      const row = data.documents[index];
      rows += '<tr class="clickable" data-document="'
        + escapeHtml(row.document_id) + '">'
        + "<td>" + escapeHtml(row.filename) + "</td>"
        + "<td>" + escapeHtml(row.document_type) + "</td>"
        + '<td class="mono">' + escapeHtml(row.reference) + "</td>"
        + "<td>" + escapeHtml(row.supplier) + "</td>"
        + '<td class="number mono">' + escapeHtml(row.total) + "</td>"
        + "<td>" + meter(row.confidence) + "</td>"
        + '<td><span class="badge ' + decisionBadgeClass(row.decision) + '">'
        + escapeHtml(decisionLabel(row.decision)) + "</span></td></tr>";
    }
    container.innerHTML = "<table><thead><tr><th>file</th><th>type</th>"
      + "<th>reference</th><th>party</th><th class='number'>total</th>"
      + "<th>confidence</th><th>outcome</th></tr></thead><tbody>"
      + rows + "</tbody></table>";
    attachDocumentClicks("documents-list");
  } catch (error) {
    container.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }
}

element("refresh-documents").addEventListener("click", loadDocuments);

function attachDocumentClicks(containerId) {
  const rows = element(containerId).querySelectorAll("tr.clickable");
  for (let index = 0; index < rows.length; index++) {
    rows[index].addEventListener("click", function () {
      showDocument(this.getAttribute("data-document"));
    });
  }
}

async function showDocument(documentId) {
  const isDocumentsTab = !element("tab-documents").classList.contains("hidden");
  const container = isDocumentsTab ? element("document-detail") : element("result-area");
  container.innerHTML = "<p class='empty'>loading…</p>";
  try {
    const data = await callApi("/api/documents/" + encodeURIComponent(documentId));
    let html = renderResult(data.result, null);
    if (data.audit.length > 0) {
      html += "<div class='card'><h2>Audit trail</h2><table><thead><tr>"
        + "<th>when</th><th>who</th><th>what</th></tr></thead><tbody>";
      for (let index = 0; index < data.audit.length; index++) {
        const entry = data.audit[index];
        html += "<tr><td class='mono'>" + escapeHtml(entry.happened_at) + "</td>"
          + "<td>" + escapeHtml(entry.actor) + "</td>"
          + "<td>" + escapeHtml(entry.action) + "</td></tr>";
      }
      html += "</tbody></table></div>";
    }
    container.innerHTML = html;
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    container.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
  }
}

// ---------------------------------------------------------------- start

loadHealth();
loadSampleList();
