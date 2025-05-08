-- The workflow IS this database. A running worker holds nothing that matters.
--
-- Read the constraints rather than the columns: they are where the guarantees
-- live. Two in particular do the heavy lifting.
--
--   steps has a PRIMARY KEY of (run_id, step_name), so a step of a run exists
--   exactly once no matter how many workers are looking at it.
--
--   side_effects has UNIQUE(idempotency_key), so the SECOND attempt to create
--   the same account is rejected by the database rather than by a step
--   remembering to check first. That is the difference between idempotency you
--   can rely on and idempotency you hope for.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    workflow_name     TEXT NOT NULL,
    state             TEXT NOT NULL,
    input_json        TEXT NOT NULL DEFAULT '{}',
    context_json      TEXT NOT NULL DEFAULT '{}',
    error             TEXT NOT NULL DEFAULT '',
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS runs_by_state ON runs (state);

CREATE TABLE IF NOT EXISTS steps (
    run_id            TEXT NOT NULL,
    step_name         TEXT NOT NULL,
    position          INTEGER NOT NULL,
    state             TEXT NOT NULL,
    attempts          INTEGER NOT NULL DEFAULT 0,
    output_json       TEXT NOT NULL DEFAULT '{}',
    error             TEXT NOT NULL DEFAULT '',
    failure_kind      TEXT NOT NULL DEFAULT '',
    claimed_by        TEXT NOT NULL DEFAULT '',
    lease_expires_at  TEXT NOT NULL DEFAULT '',
    next_attempt_at   TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    PRIMARY KEY (run_id, step_name),
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);

-- The claim query orders by these, so they are worth an index: without it every
-- claim scans every step ever run, and the engine gets slower the longer it has
-- been in service.
CREATE INDEX IF NOT EXISTS steps_claimable ON steps (state, next_attempt_at, position);
CREATE INDEX IF NOT EXISTS steps_by_run ON steps (run_id, position);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    step_name     TEXT NOT NULL,
    question      TEXT NOT NULL DEFAULT '',
    detail_json   TEXT NOT NULL DEFAULT '{}',
    state         TEXT NOT NULL,
    decided_by    TEXT NOT NULL DEFAULT '',
    decided_at    TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    expires_at    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,

    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS approvals_pending ON approvals (state, run_id);

-- Append-only. Nothing updates or deletes a row here.
CREATE TABLE IF NOT EXISTS events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    step_name    TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    detail_json  TEXT NOT NULL DEFAULT '{}',
    at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, event_id);

-- Things that happened in the outside world and cannot be undone by forgetting.
CREATE TABLE IF NOT EXISTS side_effects (
    effect_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    step_name        TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    kind             TEXT NOT NULL,
    detail_json      TEXT NOT NULL DEFAULT '{}',
    compensated      INTEGER NOT NULL DEFAULT 0,
    at               TEXT NOT NULL,

    -- The whole idempotency guarantee, in one line. A second attempt at the
    -- same effect fails to insert, and the step reads back what the first
    -- attempt did instead of doing it again.
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS side_effects_by_run ON side_effects (run_id, effect_id);
