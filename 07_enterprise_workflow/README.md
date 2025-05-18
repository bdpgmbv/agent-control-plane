# 07 — Enterprise Multi-Agent Workflow

A business process that takes days, involves several agents and a human
approval, touches systems that fail — and **survives the process being killed**.

Everything in this series so far has been short-lived: you ask, it answers, it's
finished. If the server restarted mid-request the work was simply gone, and that
was acceptable. Here it is not.

**This project is about what changes when the work cannot be lost.** The answer
turns out to be almost everything: where the state lives, what a step is allowed
to assume, what "retry" means, and what you do about the one thing that cannot
be made atomic.

```bash
make install     # venv (python3.12) + dependencies
make run         # http://127.0.0.1:8070
make check       # tests, lint, types, scenarios, and real process kills
```

Runs with **no API key**. Three steps use a model; the engine, the retries, the
approvals and the cleanup never do.

---

## Measured results

### Killing a real worker process

`make crash` starts a worker, kills it with `SIGKILL`, starts a fresh one, and
checks what the database says afterwards. Twelve interruptions:

| | |
|---|---|
| **irreversible effects that happened twice** | **0** |
| runs that recovered and completed | 12 of 12 |
| killed cleanly between steps | 7 |
| killed **inside** a step, in the dual-write gap | 5 |

The mid-step kills are aimed, not hoped for. The step is told to pause between
calling the outside system and writing down that it called it; the test waits
until the step is `running`, kills it, and then **asserts the side effect is
absent** — proving the crash landed in the window it claims to test.

### Every path the engine can take

`make eval` — 8 scenarios, offline and live:

```
happy_path                    succeeded
transient_then_ok             succeeded     the licence vendor fails twice, then works
gives_up_and_undoes           compensated   payroll never works; everything before it is undone
permanent_failure_no_retries  failed        a start date in the past, tried once
approved                      succeeded     parked for a human, who said yes
rejected                      compensated   parked for a human, who said no
cancelled                     cancelled     stopped halfway, and cleaned up
empty_request                 failed        nothing to read
```

8 of 8 offline **and** live. No effect happened twice; no failed run left
anything behind.

Test suite: **83 tests**, all offline, no key required.

---

## The thing that cannot be made atomic

Three steps each do two things that must both happen or neither: they call an
outside system, and they write down that they called it. **There is no way to
make those atomic.** Whichever order you pick, the process can die in the gap.

```
call first, then record  →  it happened, and we have no record.
                            A resume does it AGAIN.
record first, then call  →  we have a record of something that never happened.
```

You do not fix this by being careful. You fix it by making the **second call
harmless** — hand the outside system a key it recognises, so asking twice
produces one account and returns the same id both times. Our own record then
becomes an optimisation rather than the guarantee.

That is why every id in `layer5_steps/step2_external.py` is *derived from the
input* rather than generated. Those services stand in for ones that accept an
idempotency key — because **a service that does not accept one cannot be used
safely in a workflow that can be interrupted**, and noticing that is most of the
value of building this.

The idempotency key is keyed on the account, never on the attempt. If it were
keyed on a timestamp or a retry number, every retry would look like new work,
and the person would be enrolled in payroll twice. Twice enrolled is twice paid.

---

## Why there is no `for step in steps:`

The obvious way to write a workflow engine is a loop over the step list, keeping
the position in a variable. That works until the process stops existing — and
then the position is gone, and there is no way to work out where it got to
except by guessing from side effects.

So there is no position variable. `tick()` asks the database for one claimable
step, does it, and returns:

```
claim a step
if it needs a human and nobody has answered  →  park the run, stop
run it
write down what happened
make the next step available
```

The sequence lives in a `position` column and the `ready`/`blocked` states, so
it survives the process. It also means **two workers need no coordination
beyond the claim** — start three more with `make worker` and the work simply
divides between them.

### The claim is a compare-and-swap

Two workers look for work at the same moment and see the same ready step.
Written the obvious way — `SELECT` a step, then `UPDATE` it — both claim it and
the account gets created twice. So the claim is a single statement with the old
state in its `WHERE` clause:

```sql
UPDATE steps SET state = 'claimed', claimed_by = ?
 WHERE run_id = ? AND step_name = ? AND state = 'ready'
```

Whichever worker's `UPDATE` touches a row has the claim; the other changes
nothing and moves on. There is no window between the check and the change,
because there is no check — **the condition is the change**.

### Crash recovery is one timestamp

A worker claims a step for `LEASE_SECONDS`. If it dies, the lease stops being
renewed, expires, and the step returns to `ready` for anyone. No heartbeat
table, no supervisor, no leader election: just a timestamp that stops moving
when the process stops existing.

There is no lease length that is correct. Too short and a slow-but-healthy step
is taken off a worker still running it, so the step runs twice. Too long and a
crash stalls the run. That is not a flaw in the setting — it is the shape of the
problem, and it is why every side-effecting step has to be idempotent whatever
the lease is set to.

---

## Compensation is not a rollback

Step six fails for good. Steps three, four and five already changed the world:
there is a directory account, an equipment order and a licence subscription for
somebody who is not joining.

A database would roll those back. Nothing here can, because they did not happen
in a database — they happened at a vendor, and a vendor has no memory of our
intentions. So we do the **opposite** of each, in reverse order.

**Reverse order is not tidiness.** The licences are attached to the account.
Delete the account first and the licence release fails, because the thing it was
attached to is gone — and now the subscription bills forever with nothing
pointing at it.

And it leaves the world in an *acceptable* state, not the original one. The
account existed for four minutes and that is in the vendor's audit log for seven
years. **Sending the welcome email is the last step in the workflow** precisely
because nothing can unsend it — ordering steps by how hard they are to undo is a
design decision made when the workflow is written, and it is the only defence
against a step that cannot be undone at all.

When compensation itself fails — it is asking the same flaky vendors — the run
stays in `compensating` and names the specific thing still outstanding. An
orphaned licence subscription somebody knows about costs money; one nobody knows
about costs money for years.

---

## Where the model is used

Three places, and nowhere else:

| | |
|---|---|
| **intake** | turn a manager's free-text request into structured fields |
| **policy** | write the sentence the approver reads |
| **drafting** | write the welcome note |

Validating, creating, retrying, approving, undoing — all ordinary code, for the
reason project 05 spent a whole project on: a model is excellent at reading an
unstructured sentence and has no business deciding whether a payment already
happened.

**No agent touches the database, decides whether a step succeeded, or chooses
what runs next.** They take text and return text. There is a test that asserts
it, because an agent with state is a thing that can be lost when a process dies.

A model failure is not a special case — it is one more unreliable external
service, and it gets the same retry policy as the licence vendor.

---

## Bugs found while building this

**1. The crash test passed while testing almost nothing.** The first version let
a worker finish N steps and then killed it — but the workflow runs in
milliseconds offline, so by the time the parent noticed, the worker had already
finished everything. Seven of eight rows were killing an idle process. Fixed by
making the worker crash at a point the *test* chooses (`--crash-after`, using
`os._exit` so nothing is flushed), and by adding a "really?" column that fails
the run if the kill did not actually interrupt anything. **This is the fifth
time in this series a safety suite has passed for the wrong reason.**

**2. Killing between steps is the easy case.** Even fixed, the test only covered
clean boundaries. The interesting crash is *inside* a step, in the microsecond
window between the vendor call and the database write. Aiming at it needed a
deliberately widened gap, and an assertion that the side effect really was
absent at the moment of the kill.

**3. A rejected run left a live account behind.** Rejection set the run to
`failed` directly instead of going through the path that cleans up — so a person
who was refused an equipment budget kept a working directory account, and
nothing anywhere said so. There are now three ways a run can end badly and
exactly one function that does it.

**4. A cancelled run was never noticed at all.** The claim query deliberately
skips cancelled runs, so a cancelled run had nothing claimable and was never
looked at again. It sat in `running` for ever, with whatever it had created
still out there. Cancellation is now housekeeping, not something discovered
while claiming.

**5. Cleaning up overwrote why the run ended.** A cancelled run that was cleaned
up came out as `compensated`, indistinguishable from one that failed. "It was
cancelled" and "we cleaned up after it" are different facts and a reader needs
both.

**6. The prompt and the code implemented different rules.** The intake prompt
said *"do not infer a department from a job title"*, so a live run read "backend
engineer" and returned `null` — and then failed validation. The offline pattern
matcher inferred `engineering` happily. The same request succeeded offline and
failed live. Two implementations of one contract will disagree eventually; the
fix is to make the contract explicit in both.

**7. "Salary is 95000" was not a salary.** The offline matcher required a
currency symbol or comma grouping, which is not how people write it. It now
looks for a number introduced by the word *salary* first — and that is also
better, because a bare amount in a hiring request could just as easily be the
equipment budget.

**8. "Hiring Ada Lovelace" was the new joiner's name.** Three capitalised words
in a row matched the name pattern, so the payroll record would have said
"Hiring Ada Lovelace". The name now comes from the email address where there is
one, because an email local part has a reliable structure and a sentence does
not.

**9. Silently skipping a money gate.** When the settings object was absent,
`needs_approval` would have raised — and the obvious fix, returning "no approval
needed", would have skipped a spending limit because a setting was not wired up.
An unknown limit now means *ask a person*.

---

## Honest limits

**SQLite, one machine.** The claim is safe because `BEGIN IMMEDIATE` serialises
writers, which is true for SQLite and for Postgres with `SELECT … FOR UPDATE
SKIP LOCKED`. It is not true of every database, and a store that does not
support it cannot hold this engine.

**Steps within a run are sequential.** `merge_run_context` does a
read-modify-write on a JSON blob, which is safe only because one step of a run
runs at a time. Parallel steps would need per-step output columns — which is why
every step's output is *also* stored on its own row, and the shared context is a
convenience rather than the record.

**The external services are simulated.** They are not simplified in the way that
matters — each can be told to fail a given number of times, and none of them is
idempotent, which is the realistic part. But no real vendor has been asked how
it behaves under a duplicate request.

**The lease is a guess.** 30 seconds suits steps that take under a second. A
step that legitimately takes two minutes needs either a longer lease or a
renewal heartbeat, and this engine has neither.

---

## Project layout

```
pyproject.toml         dependencies, ruff, mypy and pytest configuration
src/enterprise_workflow/
  layer0_shared/       structured logging
  layer1_config/       every limit, with the reason it has that value
  layer2_models/       the objects every layer agrees on, and the state machines
  layer3_database/     schema.sql and the store - where the truth lives
  layer4_agents/       the model client and the three agents
  layer5_steps/        the step interface, the external systems, the workflow
  layer6_engine/       claim, run, record, advance
  layer7_recovery/     undoing what already happened
  layer8_approvals/    the human queue
  layer9_api/          assembly, FastAPI, the interface
  layer10_evaluation/  the scenarios and the numbers
scripts/               worker.py  crash_test.py  evaluate.py
tests/                 83 tests, all offline
ui/                    index.html  style.css  app.js   (no build step)
```

## API

| method | path | what it does |
|---|---|---|
| GET | `/api/health` | mode, settings, run counts, queue depth |
| POST | `/api/runs` | start a workflow |
| GET | `/api/runs` | every run and its progress |
| GET | `/api/runs/{id}` | one run: steps, approvals, events, side effects |
| POST | `/api/runs/{id}/cancel` | stop it — takes effect between steps |
| POST | `/api/runs/{id}/simulate-crash` | kill the worker mid-run, and watch it recover |
| GET | `/api/approvals` | what is waiting for a person |
| POST | `/api/approvals/{id}/decide` | approve or reject |
| POST | `/api/worker/start` · `/stop` | control the background worker |

## Configuration

```ini
LEASE_SECONDS=30              # then another worker may take the step
MAX_ATTEMPTS=3                # per step, and only if the step is retryable
RETRY_BASE_SECONDS=1.0        # doubling, capped at RETRY_MAX_SECONDS
APPROVAL_REQUIRED_ABOVE=2000  # above this, a human decides
APPROVAL_EXPIRY_HOURS=72      # unanswered after this, the run fails
```

Python 3.12: pydantic has no prebuilt wheel for 3.14, so a newer interpreter
tries to compile Rust and fails. `make install` checks and says so.
