-- Everything the gateway remembers.
--
-- Note what is NOT here: a spend table. Money spent is derived from traces with
-- a SUM, never accumulated in a counter alongside them. A counter and a log of
-- the things it counts will disagree eventually - a failed write here, a retry
-- there - and when they do, the counter is the one people trust and the log is
-- the one that is right. One source, queried, cannot drift from itself.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS traces (
    request_id      TEXT PRIMARY KEY,
    at              TEXT NOT NULL,
    day             TEXT NOT NULL,          -- for the daily budget query
    api_key_owner   TEXT NOT NULL DEFAULT '',
    task            TEXT NOT NULL DEFAULT '',
    route           TEXT NOT NULL DEFAULT '',

    prompt_hash     TEXT NOT NULL DEFAULT '',
    prompt_preview  TEXT NOT NULL DEFAULT '',

    outcome         TEXT NOT NULL,
    message         TEXT NOT NULL DEFAULT '',
    cache           TEXT NOT NULL DEFAULT 'miss',

    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    attempts_json   TEXT NOT NULL DEFAULT '[]',

    experiment      TEXT NOT NULL DEFAULT '',
    variant         TEXT NOT NULL DEFAULT '',
    score           REAL,                   -- NULL until somebody grades it

    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    seconds         REAL NOT NULL DEFAULT 0
);

-- The budget check runs on every single request, so it gets its own index.
-- Without it the gateway gets slower every day it stays up.
CREATE INDEX IF NOT EXISTS traces_spend ON traces (api_key_owner, day);
CREATE INDEX IF NOT EXISTS traces_recent ON traces (at DESC);
CREATE INDEX IF NOT EXISTS traces_experiment ON traces (experiment, variant);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key     TEXT PRIMARY KEY,
    scope         TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    embedding_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL DEFAULT '',
    hits          INTEGER NOT NULL DEFAULT 0
);

-- The semantic lookup only ever compares within one scope, so the index leads
-- with it. This is not an optimisation: a lookup that could cross scopes is the
-- bug project 01 shipped, where one user's cached answer was served to another.
CREATE INDEX IF NOT EXISTS cache_by_scope ON cache_entries (scope, expires_at);

CREATE TABLE IF NOT EXISTS experiments (
    name          TEXT PRIMARY KEY,
    question      TEXT NOT NULL DEFAULT '',
    variants_json TEXT NOT NULL DEFAULT '[]',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);
