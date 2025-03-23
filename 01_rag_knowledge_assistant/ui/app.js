/* ==========================================================================
   RAG Knowledge Assistant - browser code
   Plain JavaScript, no framework. Every function does one thing.
   ========================================================================== */

/* ---------- small helpers ---------- */

function element(id) {
  return document.getElementById(id);
}

function currentApiKey() {
  return element("apiKeySelect").value;
}

function escapeHtml(text) {
  const holder = document.createElement("div");
  holder.textContent = text === null || text === undefined ? "" : String(text);
  return holder.innerHTML;
}

/* Turn the [1] [2] markers in an answer into coloured chips. */
function highlightCitationMarkers(text) {
  return escapeHtml(text).replace(/\[(\d+)\]/g, '<span class="marker">$1</span>');
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
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = { detail: text };
    }
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
      for (let i = 0; i < allTabs.length; i = i + 1) {
        allTabs[i].classList.remove("tab-active");
      }
      this.classList.add("tab-active");

      const allPanels = document.querySelectorAll(".panel");
      for (let i = 0; i < allPanels.length; i = i + 1) {
        allPanels[i].classList.remove("panel-active");
      }
      element(panelId).classList.add("panel-active");

      if (panelId === "panelDocuments") { loadDocuments(); }
      if (panelId === "panelMetrics") { loadMetrics(); }
    });
  }
}

/* ---------- header status ---------- */

async function loadStatus() {
  const strip = element("statusStrip");
  try {
    const config = await callApi("/api/config");
    const health = await callApi("/api/health");

    let modeClass = "pill-offline";
    let modeText = "OFFLINE mode - no API key, using the built-in fallback";
    if (config.llm_is_live) {
      modeClass = "pill-live";
      modeText = "LIVE - " + config.llm_model;
    }

    const pills = [
      '<span class="pill ' + modeClass + '">' + escapeHtml(modeText) + "</span>",
      '<span class="pill">embeddings: ' + escapeHtml(describeEmbeddings(config)) + "</span>",
      '<span class="pill">storage: ' + escapeHtml(config.storage_backend) + "</span>",
      '<span class="pill">cache: ' + escapeHtml(config.cache_backend) + "</span>",
      '<span class="pill">' + health.chunks_indexed + " chunks indexed</span>"
    ];
    strip.innerHTML = pills.join("");
  } catch (error) {
    strip.innerHTML = '<span class="pill pill-offline">server unreachable: ' + escapeHtml(error.message) + "</span>";
  }
}

/* ---------- asking ---------- */

function describeEmbeddings(config) {
  // Say what is actually running, not what the .env file asked for. A request
  // for OpenAI embeddings with no usable key falls back to the offline embedder,
  // and the header should show that rather than quietly implying otherwise.
  if (config.embedding_is_live) {
    return config.embedding_provider;
  }
  return "offline fallback";
}

function buildBadges(response) {
  const badges = [];

  if (response.answered) {
    badges.push('<span class="badge badge-good">answered</span>');
  } else {
    badges.push('<span class="badge badge-warn">refused</span>');
  }

  const confidencePercent = Math.round((response.confidence || 0) * 100);
  let confidenceClass = "badge-bad";
  if (confidencePercent >= 70) { confidenceClass = "badge-good"; }
  else if (confidencePercent >= 45) { confidenceClass = "badge-warn"; }
  badges.push('<span class="badge ' + confidenceClass + '">confidence ' + confidencePercent + "%</span>");

  if (response.groundedness !== null && response.groundedness !== undefined) {
    const groundedPercent = Math.round(response.groundedness * 100);
    let groundedClass = "badge-bad";
    if (groundedPercent >= 90) { groundedClass = "badge-good"; }
    else if (groundedPercent >= 60) { groundedClass = "badge-warn"; }
    badges.push('<span class="badge ' + groundedClass + '">grounded ' + groundedPercent + "%</span>");
  }

  const usage = response.usage || {};
  if (usage.cache_hit) {
    badges.push('<span class="badge badge-good">cache hit</span>');
  }
  badges.push('<span class="badge">' + (usage.latency_ms || 0) + " ms</span>");
  badges.push('<span class="badge">' + (usage.total_tokens || 0) + " tokens</span>");
  badges.push('<span class="badge">$' + (usage.estimated_cost_usd || 0).toFixed(6) + "</span>");

  return badges.join("");
}

function renderCitations(citations) {
  const holder = element("citationList");

  if (!citations || citations.length === 0) {
    holder.innerHTML = '<p class="hint">No sources were cited.</p>';
    return;
  }

  const parts = [];
  for (let index = 0; index < citations.length; index = index + 1) {
    const citation = citations[index];
    parts.push(
      '<div class="citation">' +
        '<div class="citation-head">' +
          '<span class="marker">' + citation.marker + "</span>" +
          '<span class="citation-title">' + escapeHtml(citation.document_title) + "</span>" +
          '<span class="citation-source">' + escapeHtml(citation.source) + "</span>" +
          '<span class="badge">score ' + citation.score.toFixed(3) + "</span>" +
        "</div>" +
        '<div class="citation-quote">' + escapeHtml(citation.quote) + "</div>" +
      "</div>"
    );
  }
  holder.innerHTML = parts.join("");
}

function renderTrace(trace) {
  const holder = element("traceBody");
  if (!trace) {
    holder.innerHTML = "";
    return;
  }

  const rewrites = trace.rewritten_queries || [];
  let rewriteHtml = '<p class="hint">No rewrites were needed.</p>';
  if (rewrites.length > 0) {
    const items = [];
    for (let index = 0; index < rewrites.length; index = index + 1) {
      items.push("<li>" + escapeHtml(rewrites[index]) + "</li>");
    }
    rewriteHtml = "<ul>" + items.join("") + "</ul>";
  }

  const timings = trace.stage_timings_ms || {};
  const timingRows = [];
  const timingNames = Object.keys(timings);
  for (let index = 0; index < timingNames.length; index = index + 1) {
    const name = timingNames[index];
    timingRows.push("<tr><td>" + escapeHtml(name) + '</td><td class="number">' + timings[name] + " ms</td></tr>");
  }

  holder.innerHTML =
    "<h3>Queries actually searched for</h3>" + rewriteHtml +
    "<h3>Funnel</h3>" +
    "<table>" +
      "<tr><th>stage</th><th>passages</th></tr>" +
      '<tr><td>vector search found</td><td class="number">' + trace.vector_hits + "</td></tr>" +
      '<tr><td>keyword search found</td><td class="number">' + trace.keyword_hits + "</td></tr>" +
      '<tr><td>after fusion</td><td class="number">' + trace.merged_hits + "</td></tr>" +
      '<tr><td>after reranking</td><td class="number">' + trace.reranked_hits + "</td></tr>" +
      '<tr><td>dropped below the relevance threshold</td><td class="number">' + trace.dropped_below_threshold + "</td></tr>" +
    "</table>" +
    "<h3>Stage timings</h3><table>" + timingRows.join("") + "</table>" +
    "<h3>Filters applied</h3><pre>" + escapeHtml(JSON.stringify(trace.filters_applied, null, 2)) + "</pre>";
}

function showAbstainReason(reason) {
  const box = element("abstainBox");
  if (reason && reason.length > 0) {
    box.hidden = false;
    box.innerHTML = "<b>Why it refused:</b> " + escapeHtml(reason);
  } else {
    box.hidden = true;
  }
}

async function askWithoutStreaming(question) {
  const response = await postJson("/api/ask", {
    question: question,
    use_cache: element("cacheToggle").checked
  });

  element("answerBody").innerHTML = highlightCitationMarkers(response.answer);
  element("answerBadges").innerHTML = buildBadges(response);
  showAbstainReason(response.abstain_reason);
  renderCitations(response.citations);
  renderTrace(response.trace);
}

async function askWithStreaming(question) {
  const body = element("answerBody");
  const badges = element("answerBadges");

  body.innerHTML = '<span class="cursor">|</span>';
  badges.innerHTML = '<span class="badge">streaming...</span>';

  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": currentApiKey() },
    body: JSON.stringify({ question: question, use_cache: false })
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerText = "";

  while (true) {
    const chunk = await reader.read();
    if (chunk.done) { break; }

    buffer = buffer + decoder.decode(chunk.value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop();

    for (let index = 0; index < lines.length; index = index + 1) {
      const line = lines[index].trim();
      if (!line.startsWith("data:")) { continue; }

      const message = JSON.parse(line.slice(5).trim());

      if (message.type === "token") {
        answerText = answerText + message.text;
        body.innerHTML = highlightCitationMarkers(answerText) + '<span class="cursor">|</span>';
      } else if (message.type === "error") {
        throw new Error(message.message);
      } else if (message.type === "done") {
        body.innerHTML = highlightCitationMarkers(answerText.trim());
        badges.innerHTML = buildBadges({
          answered: message.answered,
          confidence: message.confidence,
          groundedness: message.groundedness,
          usage: message.usage || { latency_ms: 0, total_tokens: 0, estimated_cost_usd: 0 }
        });
        renderCitations(message.citations);
        renderTrace(message.trace);

        if (!message.answered) {
          showAbstainReason("The streamed answer cited no usable source, or the model declined.");
        } else {
          showAbstainReason("");
        }
      }
    }
  }
}

async function onAsk() {
  const question = element("questionInput").value.trim();
  if (question === "") { return; }

  const button = element("askButton");
  button.disabled = true;
  element("answerCard").hidden = false;
  element("answerBody").innerHTML = '<span class="spinner">thinking...</span>';
  element("answerBadges").innerHTML = "";
  element("citationList").innerHTML = "";
  element("traceBody").innerHTML = "";
  showAbstainReason("");

  try {
    if (element("streamToggle").checked) {
      await askWithStreaming(question);
    } else {
      await askWithoutStreaming(question);
    }
  } catch (error) {
    element("answerBody").innerHTML = '<span style="color:var(--bad)">Error: ' + escapeHtml(error.message) + "</span>";
  } finally {
    button.disabled = false;
    loadStatus();
  }
}

/* ---------- documents ---------- */

async function loadDocuments() {
  const holder = element("documentList");
  holder.textContent = "loading...";

  try {
    const documents = await callApi("/api/documents");
    if (documents.length === 0) {
      holder.innerHTML = '<p class="hint">Nothing indexed yet. Press "Load the 4 sample documents".</p>';
      return;
    }

    const rows = [];
    for (let index = 0; index < documents.length; index = index + 1) {
      const item = documents[index];
      rows.push(
        '<div class="doc-row">' +
          '<div class="doc-main">' +
            '<span class="doc-title">' + escapeHtml(item.title) + "</span>" +
            '<span class="doc-meta">' + escapeHtml(item.source) + " &middot; " + item.chunk_count + " chunks</span>" +
          "</div>" +
          "<div>" +
            '<span class="tag tag-' + escapeHtml(item.access_tag) + '">' + escapeHtml(item.access_tag) + "</span> " +
            '<button class="danger" data-document="' + escapeHtml(item.document_id) + '">delete</button>' +
          "</div>" +
        "</div>"
      );
    }
    holder.innerHTML = rows.join("");

    const deleteButtons = holder.querySelectorAll("button[data-document]");
    for (let index = 0; index < deleteButtons.length; index = index + 1) {
      deleteButtons[index].addEventListener("click", async function () {
        const documentId = this.getAttribute("data-document");
        try {
          await callApi("/api/documents/" + documentId, { method: "DELETE" });
          loadDocuments();
          loadStatus();
        } catch (error) {
          alert("Could not delete: " + error.message);
        }
      });
    }
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load documents: ' + escapeHtml(error.message) + "</p>";
  }
}

async function onSeed() {
  const button = element("seedButton");
  button.disabled = true;
  button.textContent = "loading...";
  try {
    await postJson("/api/documents/seed", {});
    await loadDocuments();
    await loadStatus();
  } catch (error) {
    alert("Seeding failed: " + error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Load the 4 sample documents";
  }
}

async function onReset() {
  if (!confirm("Delete every document from the knowledge base?")) { return; }
  try {
    await postJson("/api/documents/reset", {});
    await callApi("/api/cache/clear", { method: "POST" });
    await loadDocuments();
    await loadStatus();
  } catch (error) {
    alert("Reset failed: " + error.message);
  }
}

async function onAddText() {
  const title = element("docTitle").value.trim();
  const text = element("docText").value.trim();
  if (title === "" || text === "") {
    alert("A title and some text are both needed.");
    return;
  }

  try {
    const result = await postJson("/api/documents/text", {
      title: title,
      text: text,
      access_tag: element("docTag").value,
      source: title.toLowerCase().replace(/[^a-z0-9]+/g, "-") + ".md"
    });
    alert("Added: " + result.chunks_created + " chunks stored, " + result.chunks_skipped_as_duplicate + " skipped as duplicates.");
    element("docTitle").value = "";
    element("docText").value = "";
    loadDocuments();
    loadStatus();
  } catch (error) {
    alert("Could not add the document: " + error.message);
  }
}

async function onUpload() {
  const input = element("fileInput");
  const box = element("uploadResult");

  if (input.files.length === 0) {
    alert("Choose a file first.");
    return;
  }

  const form = new FormData();
  form.append("file", input.files[0]);
  form.append("access_tag", element("fileTag").value);

  box.hidden = false;
  box.className = "result-box";
  box.textContent = "uploading...";

  try {
    const response = await fetch("/api/documents/upload", {
      method: "POST",
      headers: { "X-API-Key": currentApiKey() },
      body: form
    });
    const payload = await response.json();
    if (!response.ok) { throw new Error(payload.detail || "upload failed"); }

    box.className = "result-box result-good";
    box.innerHTML =
      "<b>" + escapeHtml(payload.title) + "</b> indexed &middot; " +
      payload.chunks_created + " chunks stored, " +
      payload.chunks_skipped_as_duplicate + " skipped as duplicates.";
    loadDocuments();
    loadStatus();
  } catch (error) {
    box.className = "result-box result-bad";
    box.textContent = "Failed: " + error.message;
  }
}

/* ---------- retrieval debug ---------- */

async function onDebug() {
  const question = element("debugQuestion").value.trim();
  if (question === "") { return; }

  const holder = element("debugResult");
  holder.innerHTML = '<p class="spinner">retrieving...</p>';

  try {
    const result = await postJson("/api/retrieve", { question: question });

    if (result.passages.length === 0) {
      holder.innerHTML =
        '<div class="abstain-box">Nothing scored above the relevance threshold, so the ' +
        "assistant would refuse to answer this question. That is the correct behaviour " +
        "when the knowledge base does not contain the answer.</div>";
    } else {
      const parts = [];
      for (let index = 0; index < result.passages.length; index = index + 1) {
        const passage = result.passages[index];
        parts.push(
          '<div class="passage">' +
            '<div class="passage-head">' +
              '<span class="marker">' + (index + 1) + "</span>" +
              "<b>" + escapeHtml(passage.document_title) + "</b>" +
              '<span class="tag tag-' + escapeHtml(passage.access_tag) + '">' + escapeHtml(passage.access_tag) + "</span>" +
              '<span class="badge">' + escapeHtml(passage.reason) + "</span>" +
            "</div>" +
            '<div class="passage-scores">' +
              "rerank " + formatScore(passage.rerank_score) +
              " | vector rank " + formatRank(passage.vector_rank) + " (cosine " + formatScore(passage.vector_score) + ")" +
              " | keyword rank " + formatRank(passage.keyword_rank) + " (bm25 " + formatScore(passage.keyword_score) + ")" +
              " | fused " + formatScore(passage.fused_score) +
            "</div>" +
            '<div class="passage-text">' + escapeHtml(passage.text) + "</div>" +
          "</div>"
        );
      }
      holder.innerHTML = parts.join("");
    }

    renderTrace(result.trace);
    holder.innerHTML = holder.innerHTML +
      '<details class="trace" open><summary>Retrieval trace</summary>' +
      element("traceBody").innerHTML + "</details>";
  } catch (error) {
    holder.innerHTML = '<p class="hint">Failed: ' + escapeHtml(error.message) + "</p>";
  }
}

function formatScore(value) {
  if (value === null || value === undefined) { return "-"; }
  return Number(value).toFixed(3);
}

function formatRank(value) {
  if (value === null || value === undefined) { return "-"; }
  return String(value);
}

/* ---------- metrics ---------- */

async function loadMetrics() {
  const holder = element("metricCards");
  try {
    const snapshot = await callApi("/api/metrics");
    const counters = snapshot.counters || {};
    const rates = snapshot.rates || {};
    const distributions = snapshot.distributions || {};

    const requestLatency = distributions.request_latency_ms || { p50: 0, p95: 0 };
    const groundedness = distributions.groundedness || { avg: 0 };
    const tokens = distributions.tokens_per_request || { avg: 0 };
    const cost = distributions.cost_usd_per_request || { avg: 0 };

    const cards = [
      makeMetricCard(counters.requests_total || 0, "questions asked"),
      makeMetricCard(counters.answers_total || 0, "answers given"),
      makeMetricCard(counters.abstain_total || 0, "times it refused"),
      makeMetricCard(Math.round((rates.abstain_rate || 0) * 100) + "%", "refusal rate"),
      makeMetricCard(Math.round((rates.cache_hit_rate || 0) * 100) + "%", "cache hit rate"),
      makeMetricCard(requestLatency.p50 + " ms", "p50 latency"),
      makeMetricCard(requestLatency.p95 + " ms", "p95 latency"),
      makeMetricCard(Math.round((groundedness.avg || 0) * 100) + "%", "average groundedness"),
      makeMetricCard(Math.round(tokens.avg || 0), "tokens per question"),
      makeMetricCard("$" + Number(cost.avg || 0).toFixed(6), "cost per question"),
      makeMetricCard(counters.chunks_stored_total || 0, "chunks indexed"),
      makeMetricCard(counters.chunks_deduplicated_total || 0, "duplicate chunks rejected"),
      makeMetricCard(counters.invented_citations_total || 0, "invented citations caught"),
      makeMetricCard(counters.uncited_answers_total || 0, "uncited answers blocked")
    ];
    holder.innerHTML = cards.join("");
    element("metricsRaw").textContent = JSON.stringify(snapshot, null, 2);
  } catch (error) {
    holder.innerHTML = '<p class="hint">Could not load metrics: ' + escapeHtml(error.message) + "</p>";
  }
}

function makeMetricCard(value, label) {
  return '<div class="metric"><div class="metric-value">' + value +
         '</div><div class="metric-label">' + label + "</div></div>";
}

/* ---------- evaluation ---------- */

async function onRunEvaluation() {
  const button = element("runEvalButton");
  const status = element("evalStatus");

  button.disabled = true;
  status.innerHTML = '<p class="spinner">running every case, retrieval and answering separately...</p>';
  element("evalSummary").innerHTML = "";
  element("evalCases").innerHTML = "";

  const useJudge = element("judgeToggle").checked;

  try {
    const report = await postJson("/api/evaluation/run?use_judge=" + useJudge, {});
    status.innerHTML = '<p class="hint">Finished ' + report.cases_run + " cases in " +
                       report.total_seconds + "s. Saved to " + escapeHtml(report.saved_to || "-") + "</p>";
    renderEvaluationSummary(report.summary);
    renderEvaluationCases(report.cases);
  } catch (error) {
    status.innerHTML = '<div class="result-box result-bad">Failed: ' + escapeHtml(error.message) + "</div>";
  } finally {
    button.disabled = false;
  }
}

function renderEvaluationSummary(summary) {
  const retrieval = summary.retrieval;
  const answers = summary.answers;
  const honesty = summary.honesty;
  const performance = summary.performance;

  const cards = [
    makeMetricCard(retrieval.recall_at_5.toFixed(3), "recall@5"),
    makeMetricCard(retrieval.precision_at_5.toFixed(3), "precision@5"),
    makeMetricCard(retrieval.mean_reciprocal_rank.toFixed(3), "MRR"),
    makeMetricCard(retrieval.ndcg_at_5.toFixed(3), "nDCG@5"),
    makeMetricCard(answers.answer_accuracy.toFixed(3), "answer accuracy"),
    makeMetricCard(answers.average_groundedness.toFixed(3), "groundedness"),
    makeMetricCard(answers.average_citation_precision.toFixed(3), "citation precision"),
    makeMetricCard(honesty.abstain_accuracy.toFixed(3), "refused correctly"),
    makeMetricCard(honesty.hallucinated_answers, "hallucinations (want 0)"),
    makeMetricCard(honesty.leak_count, "access leaks (want 0)"),
    makeMetricCard(performance.p50_latency_ms + " ms", "p50 latency"),
    makeMetricCard(performance.p95_latency_ms + " ms", "p95 latency"),
    makeMetricCard(Math.round(performance.average_tokens_per_question), "tokens per question"),
    makeMetricCard("$" + performance.average_cost_per_question_usd.toFixed(6), "cost per question")
  ];

  element("evalSummary").innerHTML =
    '<h3 class="eval-section-title">Summary</h3><div class="metric-grid">' + cards.join("") + "</div>";
}

function renderEvaluationCases(cases) {
  const rows = [];
  for (let index = 0; index < cases.length; index = index + 1) {
    const item = cases[index];
    const scores = item.answer_scores;
    const retrieval = item.retrieval;

    let verdict = '<span class="badge badge-good">pass</span>';
    let rowClass = "row-pass";
    if (!scores.correct) {
      verdict = '<span class="badge badge-bad">fail</span>';
      rowClass = "row-fail";
    }

    let expectation = "answer it";
    if (scores.should_abstain) { expectation = "refuse it"; }

    rows.push(
      '<tr class="' + rowClass + '">' +
        "<td>" + verdict + "</td>" +
        "<td>" + escapeHtml(item.question) + '<br><span class="doc-meta">' + escapeHtml(item.caller_tags.join(", ")) + "</span></td>" +
        "<td>" + expectation + "</td>" +
        '<td class="number">' + retrieval.recall_at_k.toFixed(2) + "</td>" +
        '<td class="number">' + retrieval.reciprocal_rank.toFixed(2) + "</td>" +
        '<td class="number">' + scores.groundedness.toFixed(2) + "</td>" +
        "<td>" + escapeHtml(item.answer.slice(0, 130)) + "</td>" +
      "</tr>"
    );
  }

  element("evalCases").innerHTML =
    '<h3 class="eval-section-title">Every case</h3>' +
    "<table><tr><th></th><th>question</th><th>expected</th><th>recall@5</th>" +
    "<th>MRR</th><th>grounded</th><th>answer</th></tr>" + rows.join("") + "</table>";
}

/* ---------- wiring ---------- */

function setUpSampleChips() {
  const chips = document.querySelectorAll(".chip");
  for (let index = 0; index < chips.length; index = index + 1) {
    chips[index].addEventListener("click", function () {
      element("questionInput").value = this.getAttribute("data-q");
      onAsk();
    });
  }
}

function start() {
  setUpTabs();
  setUpSampleChips();

  element("askButton").addEventListener("click", onAsk);
  element("questionInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { onAsk(); }
  });

  element("seedButton").addEventListener("click", onSeed);
  element("resetButton").addEventListener("click", onReset);
  element("addTextButton").addEventListener("click", onAddText);
  element("uploadButton").addEventListener("click", onUpload);
  element("debugButton").addEventListener("click", onDebug);
  element("refreshMetrics").addEventListener("click", loadMetrics);
  element("runEvalButton").addEventListener("click", onRunEvaluation);
  element("apiKeySelect").addEventListener("change", function () {
    loadStatus();
    loadDocuments();
  });

  loadStatus();
  loadDocuments();
}

start();
