# 01 — Production RAG Knowledge Assistant

A question-answering system over your own documents that **refuses to guess**.

Hybrid retrieval (meaning + words), reranking, enforced citations, honest
"I don't know", role-based access control, caching, structured logging, live
metrics, and an evaluation suite that measures retrieval **separately** from
answer quality.

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 8   EVALUATION      recall@k, groundedness, abstain rate   │
│  Layer 7   API + UI        HTTP, auth, streaming, metrics         │
│  Layer 6   GENERATION      prompt, citations, refusal, scoring    │
│  Layer 5   RETRIEVAL       rewrite, hybrid search, fuse, rerank   │
│  Layer 4   INGESTION       load, clean, chunk, embed, store       │
│  Layer 3   STORAGE         SQLite  or  Postgres + pgvector        │
│  Layer 2   DATA SHAPES     the objects every layer agrees on      │
│  Layer 1   CONFIGURATION   every knob, in one place               │
│  Layer 0   SHARED          embeddings, model client, cache, cost  │
└──────────────────────────────────────────────────────────────────┘
```

Read the folders in that order, bottom to top. Each one only uses the layers
below it, so you can understand any layer without holding the rest in your head.

---

## Run it in two minutes

```bash
make install        # creates .venv, installs everything, copies .env
make run            # http://localhost:8010
```

Open <http://localhost:8010>, press **Load the 4 sample documents**, and ask
something. It works immediately with **no API key** (see *Offline mode* below).

For real answers, paste your key into `.env`:

```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
EMBEDDING_PROVIDER=openai
```

Other commands:

```bash
make test           # 82 tests, all offline
make eval           # the evaluation report
make tune           # sweep the refusal threshold and show the tradeoff
make infra-up       # Postgres (pgvector) + Redis, for the production path
```

---

## What the UI shows you

| Tab | What it is for |
|---|---|
| **Ask** | Ask a question. Streams the answer, then shows citations, confidence, groundedness, cost, and the full retrieval trace. |
| **Documents** | Add text, upload a `.pdf` / `.md` / `.txt` / `.html`, see chunk counts and access tags, delete. |
| **Retrieval debug** | Retrieval with **no answer generated**. When an answer is wrong, this tells you whether the right passage was even found. |
| **Metrics** | Requests, refusal rate, cache hit rate, p50/p95 latency, tokens and cost per question, duplicate chunks rejected, invented citations caught. |
| **Evaluation** | Runs all 16 golden questions and shows the scorecard. |

The **Acting as** dropdown switches between an admin key and a user key. Ask
*"What does a level three engineer earn?"* as each one: the admin gets the answer
from the `secret` document, the user is told the knowledge base does not contain
it. Same question, same code, different permissions.

---

## Measured results

Two runs of `make eval` over the same 16 hand-written questions — once with a
real OpenAI key, once with no key at all.

| | **Live** (`gpt-4o-mini` + `text-embedding-3-small`) | **Offline** (no key) |
|---|---|---|
| recall@5 | **1.000** | 1.000 |
| precision@5 | **1.000** | 0.795 |
| MRR | **1.000** | 1.000 |
| nDCG@5 | **1.000** | 1.000 |
| answer accuracy | **1.000** | 1.000 |
| groundedness | **1.000** | 1.000 |
| citation precision | **1.000** | 1.000 |
| abstain accuracy (3 unanswerable) | **1.000** | 1.000 |
| hallucinated answers | **0** | 0 |
| access-control leaks | **0** | 0 |
| p50 / p95 latency | 3323 / 4177 ms | 2 / 3 ms |
| tokens per question | 1172 | 427 |
| cost per question | **$0.000200** | $0 |
| cost for the whole 16-case run | **$0.0037** | $0 |

A second model then graded all 16 answers independently: **grounded 1.000,
relevant 1.000**.

Before believing that, the suite checks the judge itself — it feeds it a
deliberately unsupported answer ("refunds can be requested within five years")
and requires a low score. The judge gave it **0.00**, so its perfect scores mean
something. A judge that rates everything 10/10 produces a beautiful report and
tells you nothing; one extra call per run proves the instrument works before you
read the measurement.

Those numbers are the point of the project. "I built a RAG app" is a claim.
"recall@5 is 1.00 over 16 cases, it refuses all 3 unanswerable questions, no
answer has ever cited a document the asker may not read, and it costs $0.0002 per
question" is engineering.

### What a question actually costs

One question is not one model call. In live mode it is four, and the report
breaks them down:

```
Q: How long do I have to ask for a refund?      TOTAL 1306 tokens  $0.000224
   query_rewrite     1 call    123 tokens  $0.000031
   query_embedding   1 call     33 tokens  $0.000001
   rerank            1 call    782 tokens  $0.000130   <- 58% of the cost
   answer            1 call    368 tokens  $0.000062
```

Two things only become visible once you measure per stage:

1. **Reranking is the most expensive stage**, not answering.
2. **A refused question costs more than an answered one** ($0.000238 vs
   $0.000224) — it still pays for rewriting and reranking, then skips the cheap
   part.

The first version of this project reported only the answer call. A refused
question showed *"0 tokens, $0.000000"* after making two real API calls and
taking 3.6 seconds — a cost number that was wrong by 3.6x and trusted anyway.
`test_every_model_call_is_counted_not_just_the_answer` stops that returning.

### The measurement that changes a decision

Because reranking dominates the cost, the obvious question is whether it earns
it. `ENABLE_RERANK=false` answers it:

| | model reranking on | off |
|---|---|---|
| recall@5 | 1.000 | 1.000 |
| **precision@5** | **1.000** | **0.808** |
| answer accuracy | 1.000 | 1.000 |
| abstain accuracy | 1.000 | 1.000 |
| hallucinations | 0 | 0 |
| p50 latency | 3323 ms | **1989 ms** |
| cost per question | $0.000200 | **$0.000100** |

On this knowledge base, turning the model reranker off **halves the cost and cuts
latency by 40% with no loss of accuracy or honesty**. The only thing that drops is
precision@5 — more irrelevant passages reach the prompt, which matters more as
the corpus grows and as questions get harder than these sixteen.

So the honest answer is "it depends, and here is the table". That is what having
an evaluation suite buys you: the argument stops being about opinions.

## The nine things that make this more than a demo

### 1. Hybrid search, because each method fails differently

| | Good at | Bad at |
|---|---|---|
| **Vector search** | paraphrases — "money back" finds "refund policy" | exact strings — `ERR-4521`, `12.99`, product codes |
| **Keyword search (BM25)** | exact strings, names, numbers, rare terms | paraphrases — zero shared words means zero results |

Running both and combining them covers both failure modes. The scores are not
comparable (cosine is 0–1, BM25 is unbounded), so they are merged by **rank
position** using Reciprocal Rank Fusion — `1 / (60 + rank)` — which needs no
normalisation and no tuning. See `layer5_retrieval/step4_hybrid_merge.py`.

### 2. Reranking produces an *absolute* score, not a ranking

Search can only rank: something is always "best", even when everything is
useless. Reranking judges each passage on its own — and that is the *only*
reason honest refusal is possible. See the long note in
`layer5_retrieval/step5_rerank.py`.

### 3. Citations are enforced in code, not requested in the prompt

The prompt says "cite every claim". The code then checks it:

- a `[7]` when only 5 passages were provided is **dropped and counted** as an
  invented citation;
- an answer with **no** citations is **not shipped** — the system refuses instead.

A prompt is a request. This is the enforcement.

### 4. "I don't know" is a designed feature

Three independent gates:

1. every retrieved passage scored below the relevance threshold → refuse
   **without calling the model** (which also saves the money);
2. the model itself declines → refuse;
3. the answer cites nothing verifiable → refuse.

Refusals are **never cached** — otherwise the system keeps saying "I don't know"
after you finally add the missing document.

### 5. Access control is pushed into the SQL query

Every document has an access tag; every API key lists the tags it may read. That
list goes into the `WHERE` clause, so a forbidden document is never loaded, never
embedded into a prompt, and cannot leak into an answer. A requested filter can
only **narrow** permissions, never widen them — `resolve_allowed_tags()` in
`layer5_retrieval/step6_pipeline.py`.

The worst bug in a company knowledge assistant is an answer that is correct,
well-cited, and quotes a document the asker should never have seen.

### 6. Both caches are scoped to permissions

This was a **real bug caught by the test suite**. The exact-match cache key
included the caller's permissions, so it was safe. The *semantic* cache did not:
a "public only" caller asking the identical question matched the admin's stored
vector at similarity 1.0 and was served an answer built from secret documents.

Both are now scoped. `test_the_semantic_cache_never_crosses_permission_scopes`
fails if anyone removes it.

### 7. Chunking keeps its headings, and loses no words

- Split on **headings first**, so a refund section and a shipping section never
  share a chunk — a chunk about two topics matches neither question well.
- Then into overlapping word windows, so a sentence on a boundary survives.
- The heading is prepended to every chunk: `"30 days"` is unretrievable,
  `"Refund Policy: 30 days"` is not.
- The final short window is **absorbed into the previous one** rather than
  dropped. Dropping it silently loses the end of every section, and content that
  was never indexed can never be retrieved. `test_no_words_are_lost_when_chunking`
  guards this.

### 8. Every request is traceable

One request id, in every log line that request produces, returned in the
`X-Request-ID` header. Logs are one JSON object per line. When someone says
"the answer was wrong at 14:32", that id gives you the exact retrieval trace —
which queries were searched, what each stage found, what was dropped and why.

### 9. Retrieval is evaluated separately from answering

"The answer was wrong" has two causes with two different fixes:

- **retrieval failed** → fix chunking, embeddings, the reranker, the threshold;
- **generation failed** → fix the prompt, the model, the citation rules.

A single end-to-end score cannot tell them apart, so you end up changing the
prompt to fix a chunking problem. `layer8_evaluation/` scores them separately.

---

## Four bugs the evaluation suite found

Worth reading, because each one *looked* fine and produced plausible answers.

| Symptom | Cause | Fix |
|---|---|---|
| "What is the parental leave policy?" answered from the **bonus** policy | relevance counted every matched word equally, and it matched the word "policy" — which is worthless in a corpus of policies | weight each word by how **rare** it is (`layer5_retrieval/term_weights.py`) |
| "How long do I have to **ask** for a refund?" was refused | rarity weighting decided "ask" was highly informative, because no policy document contains it | strip **question-framing words** — ask, tell, happens, quickly (`layer0_shared/vocabulary.py`) |
| "What does a level three engineer **earn**?" matched a **shipping fee** passage | "pay" and "earn" were grouped as synonyms — but a customer *pays* a fee and an employee *earns* a salary. Opposite directions. | synonyms must preserve direction, not just topic |
| A shipping passage scored **0.60** on that same question | the query was expanded with "salary compensation band", and all three matched the single passage word "pay" | rewriting is for **finding** candidates; **judging** uses the user's real question |

The lesson in all four: they were invisible without a scored test set. The system
returned fluent, cited, confident answers the whole time.

---

## Offline mode — and its honest limits

With no API key the system runs completely, with two substitutes:

- **`OfflineEmbedder`** hashes stemmed words into buckets. Shared vocabulary
  means similar vectors. Deterministic, instant, free.
- **`OfflineChatClient`** picks the best sentences out of the retrieved passages
  and cites them.

Everything else is the real thing: the same hybrid search, the same fusion, the
same reranking, the same citation enforcement, the same refusal logic, the same
tests, the same evaluation suite.

**What offline mode cannot do:** it matches words, not meaning. It has no idea
that "terminate my subscription" and "cancel my account" are the same request
unless a synonym entry says so. Two consequences, both deliberate:

1. `OfflineEmbedder.semantic_trust = 0.5` — its cosine score is a weaker copy of
   what keyword search already reports, so counting it as independent evidence
   double-counts one signal. `OpenAiEmbedder.semantic_trust = 1.0`.
2. `layer0_shared/vocabulary.py` holds a hand-written synonym list. It exists so
   the system is useful with no key. **A synonym list can never cover a
   language** — that is what embeddings are for.

Paste a key in and `semantic_trust` goes to 1.0, the synonym list stops carrying
the load, and the reranker becomes a real model judging relevance.

---

## Tuning the refusal threshold

`MIN_RELEVANCE_SCORE` is the most consequential number in the system:

- too low → it answers questions it has no source for;
- too high → it refuses questions it could have answered.

So don't guess. `make tune` measures both failure kinds at nine values:

```
 threshold | recall@5 | answer acc | refused ok | hallucinated | wrongly refused
   0.25    |  1.000   |   1.000    |   0.667    |      1       |       0
   0.30    |  1.000   |   1.000    |   1.000    |      0       |       0   <- safe
   0.35    |  1.000   |   1.000    |   1.000    |      0       |       0   <- chosen
   0.40    |  1.000   |   0.923    |   1.000    |      0       |       0
   0.50    |  0.923   |   0.923    |   1.000    |      0       |       1
```

The safe window is **0.30 – 0.35**. Below it the system hallucinates; above it it
starts refusing real questions. We ship 0.35 — the conservative end. This is the
same method for every threshold in every LLM system you will build.

---

## The two storage backends

| | SQLite (default) | Postgres + pgvector |
|---|---|---|
| Setup | none, one file | `make infra-up` |
| Vector search | cosine computed in Python | `VECTOR` column, `<=>` operator, HNSW index |
| Keyword search | inverted index table + BM25 in Python | `TSVECTOR` + GIN index + `ts_rank_cd` |
| Good for | learning, tests, up to ~tens of thousands of chunks | production |

Both implement the same interface (`layer3_storage/base.py`), so nothing above
layer 3 changes. Read the two files side by side — the methods line up one for
one, which is the clearest way to see what actually changes when you go to
production.

Switch with one line in `.env`: `STORAGE_BACKEND=postgres`.

---

## API

| Method | Path | Needs | Purpose |
|---|---|---|---|
| `GET` | `/api/health` | — | liveness + indexed chunk count |
| `GET` | `/api/config` | — | what this deployment is configured to do (never secrets) |
| `POST` | `/api/ask` | any key | answer a question, verified |
| `POST` | `/api/ask/stream` | any key | same, streamed as Server-Sent Events |
| `POST` | `/api/retrieve` | any key | retrieval only, for debugging |
| `GET` | `/api/documents` | any key | list what this key may see |
| `POST` | `/api/documents/text` | admin | add pasted text |
| `POST` | `/api/documents/upload` | admin | upload a file |
| `POST` | `/api/documents/seed` | admin | load the samples |
| `DELETE` | `/api/documents/{id}` | admin | delete a document |
| `GET` | `/api/metrics` | — | live metrics |
| `POST` | `/api/cache/clear` | admin | empty the answer cache |
| `POST` | `/api/evaluation/run` | admin | run the golden set |

Interactive docs at <http://localhost:8010/docs>.

```bash
curl -s localhost:8010/api/ask \
  -H 'Content-Type: application/json' -H 'X-API-Key: demo-admin-key' \
  -d '{"question":"How long do I have to ask for a refund?"}' | python3 -m json.tool
```

---

## Where every file lives

```
src/rag_assistant/
  layer0_shared/       cross-cutting foundations
    text_tools.py        stemming, stop words, sentence splitting
    vocabulary.py        question-framing words + synonym groups (and why)
    embeddings.py        OpenAI + offline embedders, calibration, semantic trust
    llm_client.py        OpenAI chat + offline extractive answerer
    cache.py             exact + semantic cache, scoped by permissions
    context_format.py    the exact prompt format, defined once
    metrics.py           counters, percentiles, rates
    cost.py              token prices, dollars per request
    logging_setup.py     one JSON object per log line, with a request id
  layer1_config/       settings.py  — every knob, read from .env
  layer2_models/       schemas.py   — the objects every layer agrees on
  layer3_storage/      base.py, sqlite_store.py, postgres_store.py, bm25.py
  layer4_ingestion/    step1_load → step2_clean → step3_chunk → step4_pipeline
  layer5_retrieval/    step1_rewrite → step2_vector → step3_keyword →
                       step4_fuse → step5_rerank → step6_pipeline, term_weights
  layer6_generation/   prompts.py, answer_builder.py, groundedness.py
  layer7_api/          main.py, routes_*.py, security.py, assistant_service.py
  layer8_evaluation/   golden_dataset.json, retrieval_metrics.py,
                       answer_metrics.py, llm_judge.py, run_eval.py
ui/                    index.html, style.css, app.js  (no build step)
tests/                 82 tests, all offline
scripts/               seed_demo.py, tune_threshold.py
samples/               4 documents, tagged public / internal / secret
```

---

## Extending the golden set

This is the habit that matters most. When the system gets a real question wrong,
add it to `src/rag_assistant/layer8_evaluation/golden_dataset.json`:

```json
{
  "id": "short-name",
  "question": "the question exactly as someone asked it",
  "relevant_sources": ["the_file_that_answers_it.md"],
  "must_contain": ["the fact|an acceptable alternative wording"],
  "should_abstain": false,
  "caller_tags": ["public", "internal"]
}
```

A golden set grows out of your bug reports, not your imagination. `make eval`
then tells you whether your fix worked — and whether it broke something else.

## Project layout

This is an installable package, not a directory that happens to be importable.
`pip install -e ".[dev]"` puts `rag_assistant` on the path, so every import
resolves the same way whether you are running tests, the server, a script, or
the container.

```
pyproject.toml         dependencies, ruff, mypy and pytest configuration
src/rag_assistant/
  layerN_*/            the numbered layers, in build order
tests/                 the test suite
scripts/               things you run by hand
ui/                    the web interface, no build step
Dockerfile             installs the package; runs as a non-root user
```

The numbers are the reading order. `layer0_shared` is what everything builds on,
and each layer only imports from the ones below it - so reading them in order is
reading the system in the order it was built. The number sits after a word
because Python cannot import a module whose name starts with a digit.

```bash
make install     # python3.12 venv, dependencies, editable install
make lint        # ruff
make typecheck   # mypy
make test        # pytest, offline, no key needed
```
