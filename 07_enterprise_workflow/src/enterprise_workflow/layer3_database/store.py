"""
LAYER 3 - THE STORE
===================
Everything the engine knows lives here. The engine itself is stateless: it reads
a step, runs it, writes the result, and forgets. Kill it between any two of
those and the database still describes exactly where the workflow got to.

The method worth reading is `claim_next_step`. Everything else is bookkeeping.

CLAIMING, AND WHY IT IS A COMPARE-AND-SWAP
------------------------------------------
Two workers look for work at the same moment. Both see the same READY step.
Written the obvious way - SELECT a step, then UPDATE it - both claim it and the
step runs twice, which means the account gets created twice.

So the claim is a single UPDATE with the old state in its WHERE clause:

    UPDATE steps SET state = 'claimed', claimed_by = ?
     WHERE run_id = ? AND step_name = ? AND state = 'ready'

Whichever worker's UPDATE touches a row has the claim; the other one changes
nothing and moves on. The database decides, once, and the losing worker finds
out by being told it changed zero rows. There is no window between the check and
the change because there is no check - the condition IS the change.

The SELECT before it is only there to pick a candidate. It is allowed to be
wrong, and often is under load; the UPDATE is what is authoritative.
"""

import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from enterprise_workflow.layer2_models.schemas import (
    Approval,
    ApprovalState,
    Event,
    Run,
    RunState,
    SideEffect,
    StepRecord,
    StepState,
    now_text,
    now_utc,
)

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def to_json(value: dict) -> str:
    return json.dumps(value or {})


def from_json(text: str) -> dict:
    if text is None or text.strip() == "":
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


class AlreadyDone(Exception):
    """This exact side effect is already recorded. Read it back; do not repeat it."""


class WorkflowStore:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False because the API serves requests on one thread
        # while a worker advances runs on another. Every write below goes through
        # an explicit transaction, so the sharing is safe - but it is sharing, and
        # saying so here is better than leaving the next reader to discover it.
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_FILE.read_text())
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # ================================================================
    #  Runs
    # ================================================================

    def create_run(self, workflow_name: str, run_input: dict,
                   step_names: list[str]) -> Run:
        """
        Create the run and ALL of its steps in one transaction.

        Writing the steps up front rather than discovering them as the workflow
        goes is what makes a half-finished run readable: the plan is on disk
        before any of it has been attempted, so a run that died at step three
        still shows steps four to seven waiting, rather than looking complete.
        """
        run = Run(run_id=new_id("run"), workflow_name=workflow_name,
                  state=RunState.PENDING, input=run_input or {})

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO runs (run_id, workflow_name, state, input_json,
                                  context_json, error, cancel_requested,
                                  created_at, updated_at, finished_at)
                VALUES (?, ?, ?, ?, ?, '', 0, ?, ?, '')
                """,
                (run.run_id, run.workflow_name, run.state.value,
                 to_json(run.input), to_json(run.context),
                 run.created_at, run.updated_at),
            )

            for position, step_name in enumerate(step_names):
                # Only the first step starts READY. The rest wait for their turn,
                # which is what stops a worker claiming step five of a run whose
                # step two has not happened yet.
                state = StepState.READY if position == 0 else StepState.BLOCKED
                self.connection.execute(
                    """
                    INSERT INTO steps (run_id, step_name, position, state, attempts,
                                       output_json, error, failure_kind, claimed_by,
                                       lease_expires_at, next_attempt_at,
                                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, '{}', '', '', '', '', '', ?, ?)
                    """,
                    (run.run_id, step_name, position, state.value,
                     run.created_at, run.updated_at),
                )

            self.connection.execute(
                "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                "VALUES (?, '', 'run.created', ?, ?)",
                (run.run_id, to_json({"workflow": workflow_name,
                                      "steps": len(step_names)}), now_text()),
            )

        return run

    def run_from_row(self, row) -> Run:
        return Run(
            run_id=row["run_id"],
            workflow_name=row["workflow_name"],
            state=RunState(row["state"]),
            input=from_json(row["input_json"]),
            context=from_json(row["context_json"]),
            error=row["error"],
            cancel_requested=bool(row["cancel_requested"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
        )

    def get_run(self, run_id: str) -> Run | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return self.run_from_row(row)

    def list_runs(self, limit: int = 50, state: str = "") -> list[Run]:
        if state != "":
            rows = self.connection.execute(
                "SELECT * FROM runs WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                (state, limit)).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

        runs = []
        for row in rows:
            runs.append(self.run_from_row(row))
        return runs

    def set_run_state(self, run_id: str, state: RunState, error: str = "") -> None:
        finished_at = now_text() if state.is_finished() else ""
        with self.connection:
            self.connection.execute(
                """
                UPDATE runs SET state = ?, error = ?, updated_at = ?,
                                finished_at = CASE WHEN ? = '' THEN finished_at ELSE ? END
                 WHERE run_id = ?
                """,
                (state.value, error, now_text(), finished_at, finished_at, run_id),
            )

    def merge_run_context(self, run_id: str, additions: dict) -> None:
        """
        Fold a step's output into the run's context, in one transaction.

        Read-modify-write on a JSON blob is only safe because a run has at most
        one step running at a time. If steps ever run in parallel this becomes a
        lost update, and the fix is per-step output columns rather than a
        shared blob - which is why every step's output is ALSO stored on its own
        row, and this context is a convenience rather than the record.
        """
        with self.connection:
            row = self.connection.execute(
                "SELECT context_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return
            context = from_json(row["context_json"])
            for key, value in (additions or {}).items():
                context[key] = value
            self.connection.execute(
                "UPDATE runs SET context_json = ?, updated_at = ? WHERE run_id = ?",
                (to_json(context), now_text(), run_id),
            )

    def request_cancel(self, run_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE runs SET cancel_requested = 1, updated_at = ? "
                " WHERE run_id = ? AND state NOT IN ('succeeded','failed','compensated','cancelled')",
                (now_text(), run_id),
            )
            changed = cursor.rowcount > 0
            if changed:
                self.connection.execute(
                    "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                    "VALUES (?, '', 'run.cancel_requested', '{}', ?)",
                    (run_id, now_text()),
                )
        return changed

    # ================================================================
    #  Steps
    # ================================================================

    def step_from_row(self, row) -> StepRecord:
        return StepRecord(
            run_id=row["run_id"],
            step_name=row["step_name"],
            position=row["position"],
            state=StepState(row["state"]),
            attempts=row["attempts"],
            output=from_json(row["output_json"]),
            error=row["error"],
            failure_kind=row["failure_kind"],
            claimed_by=row["claimed_by"],
            lease_expires_at=row["lease_expires_at"],
            next_attempt_at=row["next_attempt_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_steps(self, run_id: str) -> list[StepRecord]:
        rows = self.connection.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY position", (run_id,)).fetchall()
        steps = []
        for row in rows:
            steps.append(self.step_from_row(row))
        return steps

    def get_step(self, run_id: str, step_name: str) -> StepRecord | None:
        row = self.connection.execute(
            "SELECT * FROM steps WHERE run_id = ? AND step_name = ?",
            (run_id, step_name)).fetchone()
        if row is None:
            return None
        return self.step_from_row(row)

    def claim_next_step(self, worker_id: str, lease_seconds: float) -> StepRecord | None:
        """
        Take ownership of one step, or return None.

        See the note at the top of this file. The SELECT picks a candidate and
        is allowed to be wrong; the UPDATE's WHERE clause is what decides.
        """
        moment = now_utc()
        now = moment.isoformat()
        lease_until = (moment + timedelta(seconds=lease_seconds)).isoformat()

        # BEGIN IMMEDIATE takes the write lock now rather than when the UPDATE
        # runs, so two workers serialise here instead of one of them discovering
        # a "database is locked" error half way through a claim.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT s.* FROM steps s
                  JOIN runs r ON r.run_id = s.run_id
                 WHERE s.state = 'ready'
                   AND (s.next_attempt_at = '' OR s.next_attempt_at <= ?)
                   AND r.state IN ('pending', 'running', 'compensating')
                   AND r.cancel_requested = 0
                 ORDER BY r.created_at, s.position
                 LIMIT 1
                """,
                (now,),
            ).fetchone()

            if row is None:
                self.connection.execute("COMMIT")
                return None

            cursor = self.connection.execute(
                """
                UPDATE steps
                   SET state = 'claimed', claimed_by = ?, lease_expires_at = ?,
                       updated_at = ?
                 WHERE run_id = ? AND step_name = ? AND state = 'ready'
                """,
                (worker_id, lease_until, now, row["run_id"], row["step_name"]),
            )

            if cursor.rowcount != 1:
                # Another worker got there between the SELECT and the UPDATE.
                # Nothing is wrong; this worker simply has no claim.
                self.connection.execute("COMMIT")
                return None

            self.connection.execute(
                "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                "VALUES (?, ?, 'step.claimed', ?, ?)",
                (row["run_id"], row["step_name"],
                 to_json({"worker": worker_id, "lease_until": lease_until}), now),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

        claimed = self.get_step(row["run_id"], row["step_name"])
        return claimed

    def release_expired_leases(self) -> int:
        """
        Give back steps whose worker died.

        A worker that is killed mid-step leaves the step CLAIMED or RUNNING with
        a lease that stops being renewed. Once it expires the step returns to
        READY and somebody else may take it. This is the entire crash-recovery
        mechanism: no heartbeat table, no supervisor, just a timestamp that
        stops moving when the process stops existing.
        """
        now = now_text()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE steps
                   SET state = 'ready', claimed_by = '', lease_expires_at = '',
                       updated_at = ?
                 WHERE state IN ('claimed', 'running')
                   AND lease_expires_at != ''
                   AND lease_expires_at <= ?
                """,
                (now, now),
            )
            released = cursor.rowcount

            if released > 0:
                self.connection.execute(
                    "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                    "SELECT run_id, step_name, 'step.lease_expired', '{}', ? FROM steps "
                    " WHERE state = 'ready' AND claimed_by = ''",
                    (now,),
                )
        return released

    def set_step_state(self, run_id: str, step_name: str, state: StepState,
                       output: dict | None = None, error: str = "",
                       failure_kind: str = "", next_attempt_at: str = "",
                       clear_lease: bool = True) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE steps
                   SET state = ?,
                       output_json = COALESCE(?, output_json),
                       error = ?,
                       failure_kind = ?,
                       next_attempt_at = ?,
                       claimed_by = CASE WHEN ? THEN '' ELSE claimed_by END,
                       lease_expires_at = CASE WHEN ? THEN '' ELSE lease_expires_at END,
                       updated_at = ?
                 WHERE run_id = ? AND step_name = ?
                """,
                (state.value,
                 to_json(output) if output is not None else None,
                 error, failure_kind, next_attempt_at,
                 1 if clear_lease else 0, 1 if clear_lease else 0,
                 now_text(), run_id, step_name),
            )

    def start_step(self, run_id: str, step_name: str, worker_id: str) -> bool:
        """
        Move a claimed step to running and count the attempt.

        The attempt is counted HERE, before the step does anything, not after it
        finishes. A step that is killed mid-run must still have consumed an
        attempt, or a step that crashes the worker every time is retried for
        ever - the one failure mode a retry limit exists to prevent.
        """
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE steps SET state = 'running', attempts = attempts + 1, updated_at = ?
                 WHERE run_id = ? AND step_name = ? AND state = 'claimed' AND claimed_by = ?
                """,
                (now_text(), run_id, step_name, worker_id),
            )
            started = cursor.rowcount == 1
            if started:
                self.connection.execute(
                    "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                    "VALUES (?, ?, 'step.started', ?, ?)",
                    (run_id, step_name, to_json({"worker": worker_id}), now_text()),
                )
        return started

    def unblock_next_step(self, run_id: str, after_position: int) -> str:
        """Make the next step available. Returns its name, or "" if there is none."""
        with self.connection:
            row = self.connection.execute(
                "SELECT step_name FROM steps WHERE run_id = ? AND position = ? AND state = 'blocked'",
                (run_id, after_position + 1)).fetchone()
            if row is None:
                return ""
            self.connection.execute(
                "UPDATE steps SET state = 'ready', updated_at = ? WHERE run_id = ? AND step_name = ?",
                (now_text(), run_id, row["step_name"]))
        return row["step_name"]

    # ================================================================
    #  Side effects - the idempotency record
    # ================================================================

    def record_side_effect(self, run_id: str, step_name: str, idempotency_key: str,
                           kind: str, detail: dict) -> SideEffect:
        """
        Write down that something irreversible happened.

        Raises AlreadyDone if this exact key is already recorded. The caller is
        expected to catch that and read the existing record rather than doing
        the work again - which is what makes a step safe to run twice, and
        therefore safe to interrupt.
        """
        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO side_effects (run_id, step_name, idempotency_key,
                                              kind, detail_json, compensated, at)
                    VALUES (?, ?, ?, ?, ?, 0, ?)
                    """,
                    (run_id, step_name, idempotency_key, kind, to_json(detail), now_text()),
                )
        except sqlite3.IntegrityError as error:
            raise AlreadyDone(
                "the effect %r has already been recorded for this workflow"
                % idempotency_key
            ) from error

        return SideEffect(effect_id=cursor.lastrowid, run_id=run_id, step_name=step_name,
                          idempotency_key=idempotency_key, kind=kind, detail=detail)

    def find_side_effect(self, idempotency_key: str) -> SideEffect | None:
        row = self.connection.execute(
            "SELECT * FROM side_effects WHERE idempotency_key = ?",
            (idempotency_key,)).fetchone()
        if row is None:
            return None
        return SideEffect(
            effect_id=row["effect_id"], run_id=row["run_id"], step_name=row["step_name"],
            idempotency_key=row["idempotency_key"], kind=row["kind"],
            detail=from_json(row["detail_json"]),
            compensated=bool(row["compensated"]), at=row["at"],
        )

    def list_side_effects(self, run_id: str) -> list[SideEffect]:
        rows = self.connection.execute(
            "SELECT * FROM side_effects WHERE run_id = ? ORDER BY effect_id", (run_id,)).fetchall()
        effects = []
        for row in rows:
            effects.append(SideEffect(
                effect_id=row["effect_id"], run_id=row["run_id"], step_name=row["step_name"],
                idempotency_key=row["idempotency_key"], kind=row["kind"],
                detail=from_json(row["detail_json"]),
                compensated=bool(row["compensated"]), at=row["at"],
            ))
        return effects

    def mark_compensated(self, effect_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE side_effects SET compensated = 1 WHERE effect_id = ?", (effect_id,))

    # ================================================================
    #  Approvals
    # ================================================================

    def create_approval(self, run_id: str, step_name: str, question: str,
                        detail: dict, expiry_hours: float) -> Approval:
        approval = Approval(
            approval_id=new_id("apr"), run_id=run_id, step_name=step_name,
            question=question, detail=detail or {},
            expires_at=(now_utc() + timedelta(hours=expiry_hours)).isoformat(),
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO approvals (approval_id, run_id, step_name, question,
                                       detail_json, state, decided_by, decided_at,
                                       note, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, '', '', '', ?, ?)
                """,
                (approval.approval_id, run_id, step_name, question,
                 to_json(approval.detail), approval.state.value,
                 approval.expires_at, approval.created_at),
            )
            self.connection.execute(
                "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                "VALUES (?, ?, 'approval.requested', ?, ?)",
                (run_id, step_name, to_json({"question": question}), now_text()))
        return approval

    def approval_from_row(self, row) -> Approval:
        return Approval(
            approval_id=row["approval_id"], run_id=row["run_id"],
            step_name=row["step_name"], question=row["question"],
            detail=from_json(row["detail_json"]), state=ApprovalState(row["state"]),
            decided_by=row["decided_by"], decided_at=row["decided_at"],
            note=row["note"], expires_at=row["expires_at"], created_at=row["created_at"],
        )

    def get_approval(self, approval_id: str) -> Approval | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            return None
        return self.approval_from_row(row)

    def pending_approval_for(self, run_id: str, step_name: str) -> Approval | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND step_name = ? AND state = 'pending' "
            " ORDER BY created_at DESC LIMIT 1", (run_id, step_name)).fetchone()
        if row is None:
            return None
        return self.approval_from_row(row)

    def list_approvals(self, run_id: str = "", pending_only: bool = False) -> list[Approval]:
        clauses = []
        parameters: list = []
        if run_id != "":
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if pending_only:
            clauses.append("state = 'pending'")
        where = (" WHERE " + " AND ".join(clauses)) if len(clauses) > 0 else ""

        rows = self.connection.execute(
            "SELECT * FROM approvals" + where + " ORDER BY created_at", parameters).fetchall()
        approvals = []
        for row in rows:
            approvals.append(self.approval_from_row(row))
        return approvals

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str,
                        note: str = "") -> bool:
        """
        Record a decision, once.

        The WHERE clause carries `state = 'pending'` for the same reason the
        claim does: two managers clicking at the same moment must not both
        decide, and the second one should be told the question is already
        answered rather than silently overwriting the first.
        """
        state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE approvals SET state = ?, decided_by = ?, decided_at = ?, note = ?
                 WHERE approval_id = ? AND state = 'pending'
                """,
                (state.value, decided_by, now_text(), note, approval_id),
            )
            decided = cursor.rowcount == 1
            if decided:
                row = self.connection.execute(
                    "SELECT run_id, step_name FROM approvals WHERE approval_id = ?",
                    (approval_id,)).fetchone()
                self.connection.execute(
                    "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row["run_id"], row["step_name"],
                     "approval.approved" if approved else "approval.rejected",
                     to_json({"by": decided_by, "note": note}), now_text()))
        return decided

    def expire_approvals(self) -> int:
        now = now_text()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE approvals SET state = 'expired' "
                " WHERE state = 'pending' AND expires_at != '' AND expires_at <= ?",
                (now,))
        return cursor.rowcount

    # ================================================================
    #  Events
    # ================================================================

    def add_event(self, run_id: str, kind: str, step_name: str = "",
                  detail: dict | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO events (run_id, step_name, kind, detail_json, at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, step_name, kind, to_json(detail or {}), now_text()))

    def list_events(self, run_id: str, limit: int = 500) -> list[Event]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY event_id LIMIT ?",
            (run_id, limit)).fetchall()
        events = []
        for row in rows:
            events.append(Event(
                event_id=row["event_id"], run_id=row["run_id"],
                step_name=row["step_name"], kind=row["kind"],
                detail=from_json(row["detail_json"]), at=row["at"]))
        return events

    def clear(self) -> None:
        with self.connection:
            for table in ("side_effects", "events", "approvals", "steps", "runs"):
                self.connection.execute("DELETE FROM %s" % table)
