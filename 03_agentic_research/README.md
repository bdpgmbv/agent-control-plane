# 03 — Agentic Research System

Ask a complex question. A planner splits it up, workers research the parts **in
parallel against one shared budget**, evidence is deduplicated, disagreements
between sources are surfaced rather than averaged away, and a synthesis step
writes a cited report that says what it did **not** manage to cover.

```
┌────────────────────────────────────────────────────────────────────────┐
│  Layer 9  EVALUATION   golden questions, safety checks, calibration    │
│  Layer 8  API + UI     HTTP, the run trace, the budget meter           │
│  Layer 7  SYNTHESIS    cited sections, then the summary, then verify   │
│  Layer 6  EVIDENCE     deduplicate, corroborate honestly, find conflicts│
│  Layer 5  WORKERS      search, extract with verified quotes, in parallel│
│  Layer 4  PLANNER      decompose, then validate the plan               │
│  Layer 3  SOURCES      the corpus behind one swappable interface       │
│  Layer 2  DATA SHAPES  Evidence, Conflict, Report, RunTrace            │
│  Layer 1  CONFIG       every knob, including all four budgets          │
│  Layer 0  SHARED       the BUDGET, similarity, model client, metrics   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Run it

```bash
make install        # venv + dependencies + .env
make run            # http://localhost:8030
```

Works with **no API key** — a rule-based model drives the real pipeline. For real
planning and synthesis, put your key in `.env`.

```bash
make test           # 62 tests, all offline
make eval           # the 8 golden questions
make budget-sweep   # what shrinking the budget actually costs you
make calibrate      # measure where the retrieval threshold should sit
```

**The thing to try:** set the token budget to `3000` in the UI, or run
`make budget-sweep`. Watch the report come back partial, with the sub-questions
it could not reach listed under *Gaps*, and confidence scored down accordingly.

---

## Measured results

`make eval` — 8 research questions, 56 checks, against a corpus with planted
duplicates, planted disagreements and questions it deliberately cannot answer:

| | **Live** (`gpt-4o-mini`) | **Offline** (no key) |
|---|---|---|
| questions fully passed | **8 / 8** | 8 / 8 |
| checks passed | **56 / 56 (100%)** | 56 / 56 (100%) |
| **critical safety failures** | **0** | 0 |
| every quote is real | 8/8 | 8/8 |
| every figure is in the evidence | 8/8 | 8/8 |
| budget never exceeded | 8/8 | 8/8 |
| partial reports declared themselves partial | 8/8 | 8/8 |
| disagreements surfaced | 2/2 | 2/2 |
| irrelevant sources avoided | 6/6 | 6/6 |
| p50 / p95 run time | 15.7 / 24.9 s | <0.1 s |
| cost per question | **$0.00130** | $0 |
| cost of the whole 8-question run | **$0.0105** | $0 |

Quality is scored as a percentage. **Safety is counted, and the target is zero** —
a research system that occasionally invents a quote is not a research system.

---

## The budget is the point of this project

A research agent decides for itself how much work to do, which means it decides
how much to spend. There is no natural stopping point, because "I have researched
enough" is a judgement it is not well placed to make about your money.

So there are four budgets, covering four different ways a run gets away from you:

| | limits | why it is separate |
|---|---|---|
| **tokens** | the bill | |
| **tool calls** | load on whatever you are searching | a run can stay inside its token budget and still make 400 searches — that is how you get an API key revoked |
| **seconds** | the user's patience | a run that takes four minutes has already lost them |
| **depth** | how far the planner may decompose | without it, "research X" becomes a tree, billed by the node |

### One budget, shared by everyone

The obvious design gives each worker its own budget. It is also wrong, and wrong
in a way that looks fine in testing:

```
4 workers × 30,000 tokens each = 120,000 tokens
8 workers × 30,000 tokens each = 240,000 tokens
```

The limit silently scales with the number of workers — the exact thing you were
trying to control. So there is **one** `Budget` per run and every agent charges
against it. Workers run in parallel threads, so every counter is behind a lock;
`test_one_budget_shared_by_many_threads_counts_correctly` fails if that lock is
removed.

The consequence is worth understanding: workers do not get an equal share, they
race. Three cheap sub-questions may finish on very little, leaving plenty for a
hard fourth — or an expensive first worker may leave the others with nothing, and
they say so. That is correct. Dividing the budget evenly up front wastes what the
easy sub-questions do not use and starves the hard one that needed it.

### Something is held back for the answer

Research will consume every token you give it. If it consumes all of them there is
nothing left to write the report with, and you have paid full price for a pile of
evidence and no answer. 15% is reserved: ordinary work is refused once it is
reached, and only synthesis may spend it.

### Running out is a normal outcome, not an error

`make budget-sweep`, offline:

```
 searches | sub-qs done | citations | conflicts | partial | gaps | confidence
   1      |     0 of 3  |     0     |     0     |  True   |  6   |    0.25
   2      |     1 of 3  |     2     |     0     |  True   |  6   |    0.74
   3      |     1 of 3  |     4     |     0     |  True   |  4   |    0.79
   5      |     2 of 3  |     6     |     1     |  False  |  1   |    0.88
   40     |     2 of 3  |     6     |     1     |  False  |  1   |    0.88
```

Every row produced a usable report. None crashed, and every partial one said so.

Look at the **conflicts** column. The disagreement between the two studies only
appears at five searches. Below that you get a report that is cheap, fast,
confident and **one-sided** — and the only thing that tells you so is the
`partial` flag and the gaps list. That is why they exist.

---

## Disagreement is the most valuable output, and the easiest to lose

Two studies of the four-day week report **+12%** and **−3%**. A system that
quietly picks one has not answered the question; it has hidden the most
important thing it found.

The trap is in deduplication. Measured by wording, these two are 85% identical:

```
"The trial found productivity rose by 12 percent."
"The trial found productivity fell by 3 percent."
```

Any ordinary near-duplicate detector merges them and keeps whichever arrived
first. So the check is in two parts:

| | | |
|---|---|---|
| similar wording | **and compatible numbers** | → duplicate, merge |
| similar wording | **and different numbers** | → **conflict**, keep both |

And two further rules, each added because of a specific false positive:

- **A document cannot conflict with itself.** One paper saying "2,900 employees
  took part" and "56 of 61 organisations continued" was flagged as a study
  disagreeing with itself.
- **A conflict must be about the same thing.** "Burnout fell 71%" and "turnover
  fell 57%" have the same shape and different numbers. They are two results, not
  a contradiction. Stripping the *measuring* vocabulary (percent, fell, rose,
  relative, study…) leaves what the claim is actually about; if nothing is shared,
  it is not a disagreement.

Conflicts are never resolved. The report says which side carries more weight, by
source credibility, and then shows both.

---

## Corroboration is not automatic, and most of it is not independent

The obvious move is to raise a finding's score for each source that repeats it.
It is wrong most of the time:

> A journal publishes a trial finding 12%.
> A newspaper reports the journal's finding of 12%.

That is **one** study and one write-up, not two studies agreeing. Counting the
newspaper as corroboration makes a single result look twice as well supported —
precisely the error that makes a report more confident than the research is.

So the bonus applies only between sources that could plausibly have found the
same thing independently: two peer-reviewed studies, or a study and a government
evaluation, from different organisations. A news article or blog repeating a
finding is recorded as "also reported by" and adds nothing.

---

## Every quote is checked against the source

Asked to quote a document, a model will sometimes produce a sentence that is
*almost* there — tidied up, or two sentences welded together, or simply invented.
The claim then looks perfectly sourced and is not.

So after extraction, every quote is looked up in the document it claims to come
from. A quote that is not there means the evidence is dropped and counted. The
evaluation suite then re-checks the finished report's citations the same way:
`every_quote_is_real`, 8/8, and it is a **critical** check.

Hyphens, curly quotes and dashes are normalised first. A correct citation
rejected over a hyphen pushes the system towards having no evidence rather than
towards being careful.

---

## Seven bugs found while building this

Every one produced a plausible, confident, wrong report.

| Symptom | Cause | Fix |
|---|---|---|
| The plan collapsed to **one sub-question — the original question** | "is this just a restatement?" used a containment measure, and a *good* sub-question is supposed to contain the original plus an angle | compare symmetrically (Jaccard); a restatement scores 1.0, an angle scores 0.45 |
| With embeddings on, the plan collapsed to **one sub-question again** | every sub-question of one question is about the same topic, so they all score ~0.85 by meaning; the evidence threshold of 0.82 merged them | sub-questions get their own, much higher bar (0.93) |
| A study was reported as **disagreeing with itself** | two facts from one paper, similar wording, different numbers | a document cannot conflict with itself |
| **"Burnout fell 71%" vs "turnover fell 57%"** reported as a disagreement | same sentence shape, different quantities | a conflict must share a *subject* term after the measuring vocabulary is stripped |
| A question with **nothing** in the corpus produced a **10-source report at 0.99 confidence** | relevance is rescaled against the best hit, so the best hit is always 1.00 however bad it is. The "match" was the word *deep*, from "deep concentration" | keep the raw score too, and require the document to cover enough of the query's *information* — rare words weighted heavily |
| The verifier reported **two invented figures** in a perfectly sourced report | they were the citation markers `[10][11]`; markers ≤10 were hidden by the small-integer rule, so the bug only appeared past ten sources | strip markers before checking figures — a noisy alarm gets switched off, and then the real ones go unnoticed |
| A report citing **nothing** scored **0.50 confidence** | cleanliness and agreement both give full marks for having no problems, and a report with no evidence has no problems | having nothing to say is not the same as saying it well — cap at 0.25 |

A process note worth the same attention: one of my patches matched nothing and
printed "fixed" anyway. Every patch script in this project now asserts that its
replacement actually applied.

---

## Thresholds are measured, not guessed

`make calibrate` prints the distribution of query coverage for questions the
corpus can and cannot answer, and suggests a threshold in the gap:

```
  questions the corpus CAN answer      lowest 0.235   highest 0.782
  questions the corpus CANNOT answer   lowest 0.076   highest 0.237

  NO CLEAN GAP: the worst answerable query (0.235) scores below the best
  unanswerable one (0.237). No single threshold separates them, so
  retrieval needs improving rather than the threshold retuning.
```

That output is the honest one, and it is the point of the script. On the eight
evaluation questions the two distributions *do* separate, and 0.22 works. Across
a wider set they overlap by two thousandths — meaning no threshold is safe, and
the real fix is better retrieval (embedding-based search over the corpus), not
more tuning. A calibration tool that told you 0.22 was "correct" would be lying.

---

## Offline mode, and what it cannot do

With no API key everything runs: planning, the parallel workers, all four
budgets, deduplication, conflict detection, quote verification, citation
checking, the whole evaluation suite. What changes is how similarity is measured,
and the UI says which is in use.

| | with a key | without |
|---|---|---|
| similarity | **meaning** (embeddings) | words |
| "productivity rose 12%" vs "productivity increased 12.4%" | duplicate ✓ | **missed** (0.51) |
| "rose 12 percent" vs "fell 3 percent" | conflict ✓ | conflict ✓ |
| "benefits of X" vs "advantages of X" (sub-questions) | duplicate ✓ | **missed** |

There is deliberately **no offline embedder**. In project 01 a hashing embedder
was useful because it still found documents by shared vocabulary. Here the
question is subtler — "rose 12%" and "increased 12.4%" share almost no words — and
a word-matching embedder would answer it no better than the word comparison
already there, while pretending to be something more.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/research` | research a question, return a cited report |
| `GET` | `/api/sources` | every document in the corpus, so you can check the citations |
| `GET` | `/api/config` | including whether similarity is by meaning or by words |
| `GET` | `/api/health`, `/api/metrics` | operations |

```bash
curl -s localhost:8030/api/research -H 'Content-Type: application/json' \
  -d '{"question":"Is the four-day work week good for productivity?","max_tool_calls":5}' \
  | python3 -m json.tool
```

`/api/research` runs the whole thing and returns when done — up to ~25 seconds
with a real model. That is honest rather than ideal; the time budget is what stops
it being worse. A production version would return a run id immediately and stream
progress, and the `RunTrace` object is already shaped for that.

---

## Where every file lives

```
src/research_agent/
  layer0_shared/
    budget.py          THE BUDGET - shared, thread-safe, with a reserve
    similarity.py      duplicate vs conflict, and why they need different tests
    llm_client.py      OpenAI + the offline rule model + BudgetedModel
    embeddings.py      used only to compare claims; None without a key
    text_tools.py, cost.py, metrics.py, logging_setup.py
  layer1_config/       settings.py - all four budgets
  layer2_models/       schemas.py - Evidence, Conflict, Report, RunTrace
  layer3_sources/      base.py (the interface), local_corpus.py, corpus/, registry.py
  layer4_planner/      step1_decompose, step2_validate_plan
  layer5_workers/      step1_search, step2_extract_evidence (quote checking),
                       step3_worker, step4_pool (parallel, one budget)
  layer6_evidence/     step1_deduplicate, step2_detect_conflicts
  layer7_synthesis/    prompts, step1_build_report, step2_verify_report
  layer8_api/          research_service.py (the orchestrator), routes.py, main.py
  layer9_evaluation/   golden_questions.json, checks.py, run_eval.py
ui/                    index.html, style.css, app.js (no build step)
tests/                 62 tests, all offline
scripts/               calibrate_coverage.py, budget_sweep.py
```

---

## Adding a real search provider

`layer3_sources/base.py` is the whole contract: a `name`, a `search()` returning
`SearchHit` objects, and a `get_document()`. Register it in `registry.py` and
nothing above layer 3 changes.

Three things to get right, all of them noted in that file's docstrings: a
timeout; a failure that returns an empty list rather than raising (one provider
being down should thin the report, not end the run); and a credibility rating for
whatever comes back, because `SourceType` is what the whole weighting rests on.

The bundled corpus stays useful after you add one — it is what makes the
evaluation suite reproducible on any machine.

## Project layout

This is an installable package, not a directory that happens to be importable.
`pip install -e ".[dev]"` puts `research_agent` on the path, so every import
resolves the same way whether you are running tests, the server, a script, or
the container.

```
pyproject.toml         dependencies, ruff, mypy and pytest configuration
src/research_agent/
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
