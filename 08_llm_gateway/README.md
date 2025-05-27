# 08 — LLM Gateway and Evaluation Platform

One door in front of every model call, and a way to tell whether a change helped.

Every application in projects 01–07 called a model directly. That works until you
have four of them, and then nobody can answer four questions: what did we spend
yesterday, which caller spent it, what happens when the vendor is down, and is
the new prompt actually better or does it just feel better. This project answers
those four, and the fourth one is the hard one.

```bash
make install
make run            # http://127.0.0.1:8080
```

No API key needed. Without one the gateway serves two simulated models whose
skill, latency and failure behaviour are fixed numbers, so the routing, caching,
budget and significance machinery can be exercised for free and in the same way
every time. Paste a key into `.env` and the same code calls `gpt-4o-mini`.

`gpt-4o-mini` is the only live model the gateway will route to. `gpt-4o` costs
sixteen times more per token and nothing here needs it, so it is not in the
provider's model list at all — one mistyped route cannot quietly spend at
sixteen times the rate. The price table still knows it, so the cost arithmetic
stays readable and testable.

---

## Measured results

All offline, from `make eval`, 845 requests:

```
  0. THE BENCHMARK ITSELF
     all 5 questions are scoreable - the solver and the dataset agree

  1. CACHE SCOPING       nothing leaked between scopes across 3 attempts
  2. FALLBACK            held - answered by offline-strong after 3 attempt(s)
  3. CACHE               20 of 20 repeat requests answered for free
  5. WHAT IT COST        845 requests, $0.000000, p50 0.0064s, p95 0.0068s
```

### The same experiment, at three sample sizes

This is the point of the project. One real effect — a prompt that asks for
working-out, worth 18 points — measured three times as the samples arrive:

```
after  40 requests: direct 64%, stepwise 50%
    Not enough graded samples yet: direct has 22 and stepwise has 18, and this
    platform will not look below 30 per arm.

after 200 requests: direct 52%, stepwise 64%
    Too close to call. stepwise is ahead by 12.2 points, but the difference
    could plausibly be anywhere from -1.5 to 25.8 - which includes zero.
    About 255 more samples per variant would settle it.

after 800 requests: direct 54%, stepwise 69%
    stepwise is better. The difference is 14.9 points (95% confident it is
    between 8.2 and 21.6), which would happen by chance about 0.00% of the time.
```

Three things worth noticing.

At 40 requests the platform **refuses to answer**, and at that moment `direct`
was ahead by 14 points. A dashboard showing a bare percentage would have shown
the losing arm winning by a wide margin, and somebody would have shipped it.

At 200 it says "too close to call" and estimates 255 more samples per arm. That
estimate was roughly right: the verdict arrived shortly after.

At 800 it names a winner *and* gives the range. "14.9 points" on its own is
false precision — the honest claim is "somewhere between 8 and 22, and
definitely not zero".

### The same thing, live

`make eval-live`, 845 requests through `gpt-4o-mini`, total spend **$0.0359**:

```
 40 requests:  refuses to answer (20 per arm, below the 30 minimum)
200 requests:  direct 85%, stepwise 97%  -> +12.1 points, 95% CI 4.4 to 19.8, p 0.45%
800 requests:  direct 86%, stepwise 96%  -> +10.0 points, 95% CI 6.2 to 13.9, p 0.00%

answered by:   gpt-4o-mini   769 requests   $0.035913   mean 0.979s
               offline-fast   76 requests   $0.000000   mean 0.087s
latency:       p50 0.5786s, p95 2.0461s
```

Two things this run showed that the offline one could not.

**76 of 845 requests were answered by the offline model.** Nobody asked for that.
The account was returning intermittent `429 no credits remaining`, so roughly
9% of traffic fell down the chain to the last resort and was served for free.
That is the fallback working, and it is also the reason the by-model breakdown
is printed at all: without it the run looks like an unusually cheap, unusually
fast success, and a measurement taken during it would be part simulated model
wearing a live label.

**The live effect reached significance at 200 requests rather than 800.** Same
experiment, same prompts, fewer samples needed - because `gpt-4o-mini` is more
consistent than the simulated model, so the noise is smaller. A sample size is
not a property of the test; it is a property of the effect and the variance.

---

## The question this project is really about

> We changed the prompt and the success rate went from 71% to 74%. Ship it?

You cannot answer that from two numbers. You need to know how many requests each
number came from. At 50 requests per arm a 3-point difference is noise; at 50,000
it is real. Everything in `layer8_experiments/` exists to make that difference
visible instead of leaving it to whoever is most confident in the meeting.

`step1_statistics.py` is a two-proportion z-test, a confidence interval and a
sample-size estimate, in about 120 lines of plain Python with no dependencies —
`math.erf` for the normal CDF, bisection for its inverse. It is not much code.
The reason it is rare in application codebases is not difficulty.

### The verdict is a sentence, not a p-value

```
stepwise is better. The difference is 14.9 points (95% confident it is between
8.2 and 21.6), which would happen by chance about 0.00% of the time.
```

Nobody has to remember what p < 0.05 means. The three things a reader needs —
which one won, by how much, and how sure — are all in the sentence, and the
interval is drawn in the UI with a red line at zero so "includes zero" is
something you see rather than something you work out.

---

## Order of operations

`layer9_api/gateway.py` is the whole system in one readable function:

```
who is calling      -> layer 7, limits
which variant       -> layer 8, experiments
which models        -> layer 5, routing
have we answered    -> layer 6, cache
ask somebody        -> layer 4, providers, through the router
write it down       -> layer 3, always
```

The order is not arbitrary.

**Limits before everything**, because a refused request should cost nothing.

**The cache before the providers**, because a hit should cost nothing either.

**The cache after the rate limit**, though — which looks backwards, since cache
hits are free. Check the cache first and a caller can hammer the gateway without
limit, which sounds harmless until the hammering is a retry loop that never
stops.

**A trace is written whatever happens.** Refused, failed, cached or fine: exactly
one row per request. A gateway that records only successes cannot tell you why
yesterday was expensive, and it cannot tell you that a third of your traffic is
bouncing off a rate limit nobody has looked at since it was set.

---

## Two failures are not the same failure

`layer2_models/schemas.py` classifies every provider failure, and the class
decides what happens next:

| Failure | Retry? | Fall back? | Why |
|---|---|---|---|
| `TIMEOUT`, `UNREACHABLE`, `SERVER_ERROR` | yes | yes | might work in a moment |
| `RATE_LIMITED` | yes | yes | will work in a moment |
| `BAD_REQUEST` | **no** | **no** | the request is wrong; a second model will reject it too |
| `AUTH` | **no** | yes | this key is wrong; another provider's may not be |
| `CONTENT_FILTER` | **no** | **no** | deliberate refusal, not a fault |

Retrying a `BAD_REQUEST` is how one malformed request becomes thirty. Falling
back on a `CONTENT_FILTER` is how a refusal becomes a shopping trip for a model
that will agree.

The retry loop is nested inside the fallback loop: try each model a few times
with increasing gaps, then move to the next model. Give up only when the chain
is exhausted, and report every attempt in the trace so the reason is legible
afterwards.

---

## The cache bug from project 01, fixed properly

Project 01 in this series shipped a semantic cache that could serve one user's
answer to another. The cause was mundane: the scope was part of the cache *key*,
but the semantic search compared the new question against **every** stored
embedding and returned the closest one regardless of scope. An exact-match
lookup was safe; a reworded question was not.

So here the scope appears twice, on purpose:

```python
# layer6_cache/step1_cache.py
key = build_cache_key(scope, system, prompt, model, temperature, max_tokens)
...
candidates = self.store.candidates_for_scope(scope)   # and again in the WHERE clause
```

`make eval` asks a scoped question as one owner and then asks the same question
reworded as another, and fails the whole run if the answer comes back.

That check has now been wrong twice, in two different directions, which is worth
more than the fix. First it compared the two answers to each other — but both
came from a deterministic model, so identical text meant nothing and the check
was measuring the provider, not the cache. Then, once it looked at the trace's
`cache` field instead, it flagged a legitimate `MISS`. A test that passes because
of a coincidence is worse than no test, because it is now also evidence.

---

## Where the model is used, and where it deliberately is not

Live, the gateway calls `gpt-4o-mini`, with an offline model behind it as the
last resort, and uses `text-embedding-3-small` for the semantic cache. The
fallback is deliberately *downward* — to something free that still answers —
rather than upward to a pricier model, because the failure it is protecting
against is the vendor being unavailable, not the answer being too hard.

The statistics are pure arithmetic. No model is asked whether a difference is
significant, and no model grades the benchmark — `layer10_evaluation/dataset.py`
uses arithmetic word problems precisely so that grading is a four-line function
whose answer is the same every time. An A/B platform is only as good as its
success metric, and "which answer is better" is usually a judgement, which means
grading becomes slow, expensive and inconsistent between graders.

A narrow metric you can measure a thousand times beats a broad one you can
measure nine times.

---

## Bugs found while building this

Kept because each one was a wrong assumption, not a typo.

**The benchmark was one-fifth dead and it looked like a result.** One question
read "A crate holds 20 bottles. Each bottle costs 5. Two bottles are returned.
How much for the rest altogether?" with the expected answer `n * 5 - 2` = 98.
That arithmetic is wrong — two bottles returned is 2 × 5 off the bill, so the
true answer is 90 — and the offline solver, which looked for the word "returns"
and never matched "are returned", answered 100. Three different numbers. That
question was therefore graded **incorrect on every single request**, for every
model and every prompt variant.

Nothing failed. There was no error and no exception. Every arm was quietly capped
at 80%, an 18-point effect measured as 8, and the A/B demo kept concluding "too
close to call" at 800 samples — which reads as an interesting finding about
statistical power rather than a broken ruler. It surfaced only because the
reasoning bonus was a known 18 points and the measured gap was 8, and that gap
was worth chasing.

A broken benchmark question hides itself, because it lowers every arm by the same
amount. `make eval` now checks the ruler before it measures anything and fails
the run if the solver and the dataset disagree, and three tests assert the
benchmark is scoreable and that the effect it is supposed to find is really
there.

**Samples were not independent.** The evaluation asked for verdicts at 40, 200
and 800 requests by running batches of 40, 160 and 600 — each starting its
question index at 0. So 800 requests carried only 600 distinct questions, and a
significance test that assumes independent samples was being handed duplicates
of a deterministic model's answers. The verdict would have been overconfident in
the one place this project exists to be honest.

**`make eval-live` measured the offline models.** Every request in the
evaluation used the default task, `general`, whose route chain is
`[offline-fast, offline-strong]`. So `--live` swapped the embedder and made the
live providers available, and then the benchmark ran on the simulated models
anyway while the Makefile described the target as "measure it with real
models". Nothing failed; the numbers were real numbers, measured from the wrong
thing. The evaluation now selects the `live` route when `--live` is passed, and
prints which models actually answered - because in a live run the chain ends
with an offline model, so a vendor having a bad afternoon would otherwise show
up as unusually cheap and fast rather than as a fallback.

**The fallback check passed without any fallback.** It always broke
`offline-fast`, which is *last* in the live chain - so in a live run
`gpt-4o-mini` answered on the first attempt and the check reported "held"
having exercised nothing. It now breaks whichever model is *first* in the chain
under test, and requires that more than one model was tried and that the broken
one did not answer. Sixth occurrence of the same pattern in this series.

**The embedder substituted a vector from a different space, and called it a
fallback.** `OpenAIProvider.embed` caught every exception and returned
`hashing_embedding(text)` instead, on the reasoning that a cache which cannot be
written is a missed saving while a request failing because of it is an outage.
The first half is right; the conclusion is not. A hashed vector is not a worse
embedding, it is a vector in a **different space**. Stored entries were embedded
by the real model, so the cosine similarity against them is a number with no
meaning — and it is then checked against the threshold like any other number.
It can clear it. The "graceful degradation" was a way for the gateway to answer
a question nobody asked, which is the same failure the cache scoping exists to
prevent, arriving through the other door.

It surfaced because the API account ran out of credits mid-session, and
`tune_threshold.py --live` then printed the offline embedder's numbers under a
heading saying "real embeddings" — the live threshold would have been set from
offline data. Embeddings now raise `EmbeddingUnavailable`; the cache skips the
semantic step for that request and stores the entry with no vector at all,
which still serves exact repeats and never writes a vector from one embedder
next to another's.

**The threshold script compared its measurement against the wrong number.** It
measured whichever embedder `--live` selected, then compared the result to
`SETTINGS.semantic_threshold()` — which follows whether a key happens to be in
`.env`. Once a key was pasted in, the offline run measured the offline embedder,
held the result against the *live* threshold, announced that a correctly-set
0.710 was "OUTSIDE that range", and exited non-zero. The measurement was right
and the thing it was held against was wrong, which is the same mistake as the
threshold it exists to prevent. It also used to exit 0 while printing "OUTSIDE
that range", so CI stayed green over a cache that was off.

**And then a test of all this passed for the wrong reason.** The new test
asserted that a broken embedder produces a cache miss. It used
`"How long have I got to return something?"` as the rewording — a rewording of a
*different* question, scoring **0.0000** against the stored one under the hashing
embedder. The lookup missed because the questions were unrelated, not because the
embedder was down, and the test passed with the bug deliberately put back. It now
uses a rewording that scores 1.0000, and a control test asserts that the same
lookup *does* hit when the embedder works — so a miss can only be caused by the
thing under test. That is the fifth time in this series a safety check has passed
for the wrong reason, and the pattern is always the same: a negative test with no
positive control is decoration.

**`fell_back()` compared providers, not models.** Found while the live chain
still ran `gpt-4o-mini` → `gpt-4o`: both are OpenAI, so comparing providers
reported zero fallbacks while the chain was working hard. The chain is now
mini-only and every fallback crosses providers, but the comparison is still on
models, because "did we end up somewhere other than first choice" is a question
about models and happened to be answerable by provider only by accident.

**The semantic threshold was guessed at 0.93.** `scripts/tune_threshold.py`
measures it instead: the lowest score among genuinely-matching pairs and the
highest among non-matching ones. The offline hashing embedder's genuine matches
scored around 0.71 and the live embedder's lowest was 0.9187, so 0.93 was wrong
for both — it turned the semantic cache off while appearing to configure it. The
two embedders now have separate measured defaults, because a number tuned against
one says nothing about the other.

**A rate limit refused 30 of 40 simulation requests.** The A/B simulation is one
caller making hundreds of requests, which is exactly what a per-caller rate limit
exists to stop. It now gets its own bucket, and reports refusals rather than
quietly producing a verdict from a quarter of the data.

**An unknown model in a route was treated as a bad request.** A typo in
configuration stopped the whole fallback chain, as though the *caller* had sent
something invalid. A model the gateway has never heard of is now skipped so the
chain continues; only a provider actually answering `BAD_REQUEST` stops it.

**`dict | JSONResponse` as a return type broke the endpoint.** FastAPI tried to
build a response model from the union. `response_model=None` says "I am handling
this myself".

---

## Honest limits

The offline models are not language models. They solve arithmetic word problems
and get them wrong at a fixed rate. That makes every number here reproducible and
free, and it means the evaluation measures the *platform*, not model quality.

The z-test assumes independent samples and reasonably large arms, which is why
the platform refuses to report below 30 per arm rather than printing a confident
interval from twelve requests.

Peeking is not corrected for. Looking at a running experiment repeatedly and
stopping when it first looks significant inflates false positives, and this
platform does not implement sequential testing. The refusal below 30 per arm and
the sample-size estimate push against the habit, but they do not fix it.

Cost figures use a hardcoded price table. Prices change; the table does not know.

The daily budget is a `SUM` over today's traces on every request, not a counter.
Correct, and it will not stay fast at millions of rows per day.

Rate limiting is per-process. Two replicas means two buckets.

---

## Project layout

```
src/llm_gateway/
  layer1_config/        settings, and thresholds measured per embedder
  layer2_models/        schemas, and what each failure class means
  layer3_storage/       traces, cache entries, experiments
  layer4_providers/     prices, the offline provider, OpenAI
  layer5_routing/       route table, retry-inside-fallback
  layer6_cache/         exact and semantic, scoped twice
  layer7_limits/        token bucket, daily budget
  layer8_experiments/   the z-test, assignment, verdicts in English
  layer9_api/           the gateway loop, FastAPI
  layer10_evaluation/   something checkable to ask
scripts/
  evaluate.py           five numbers, and a check on the ruler first
  tune_threshold.py     measures the semantic threshold; says so if there is no clean gap
ui/                     dashboard, try it, A/B, traces
tests/                  85 tests
```

## Commands

```bash
make test        85 tests, offline, free
make check       tests + ruff + mypy + eval
make eval        measure it offline
make eval-live   measure it with real models
make threshold   measure the semantic cache threshold instead of guessing
make run         http://127.0.0.1:8080
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/chat` | one request through the whole loop |
| `GET` | `/v1/traces` | recent traces, newest first |
| `GET` | `/v1/stats` | spend today, cache hit rate, latency percentiles |
| `POST` | `/v1/experiments` | create an experiment |
| `GET` | `/v1/experiments/{name}` | the verdict, as a sentence |
| `POST` | `/v1/simulate` | drive an experiment with N graded requests |
| `GET` | `/api/health` | liveness |

## Configuration

Everything has a working default; `.env` overrides it.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | *(none)* | absent means offline mode |
| `REQUESTS_PER_MINUTE` | `60` | per caller |
| `BURST` | `20` | bucket size |
| `DAILY_BUDGET_USD` | `5.00` | checked before the rate limit |
| `CACHE_ENABLED` | `true` | exact-match cache |
| `SEMANTIC_CACHE_ENABLED` | `true` | nearest-neighbour cache |
| `SEMANTIC_THRESHOLD` | `0.88` | measured, for the live embedder |
| `SEMANTIC_THRESHOLD_OFFLINE` | `0.71` | measured, for the hashing embedder |
| `CACHE_TTL_SECONDS` | `86400` | how long an answer stays usable |
| `CONFIDENCE_LEVEL` | `0.95` | interval width |
| `MINIMUM_SAMPLES_PER_ARM` | `30` | below this, no verdict |
| `MAX_ATTEMPTS_PER_PROVIDER` | `3` | retries before falling back |

## Docker

```bash
docker compose up --build       # http://127.0.0.1:8080
```

Non-root (uid 10001), `WORKDIR /srv`, database at `/srv/data/gateway.db`, and a
`HEALTHCHECK` that hits `/api/health`. Verified by starting the container and
posting a real request through it — not only by checking that it builds.
