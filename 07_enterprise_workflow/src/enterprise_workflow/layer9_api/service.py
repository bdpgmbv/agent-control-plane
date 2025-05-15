"""
LAYER 9 - ASSEMBLY
==================
Where the layers are wired together, and the only place that knows about all of
them.

The worker runs on a background thread here so that the interface shows runs
advancing without anyone having to start a second process. That is a convenience
for looking at it, and it is worth being clear that it is not the deployment
shape: in production the workers are separate processes, probably on separate
machines, and the API does no work at all.

The design allows both without changing a line, which is the point of putting
the state in the database. `scripts/worker.py` is the same engine with the same
store, and running three of them alongside the API simply means the work divides
between four claimants instead of one.
"""

import threading
import time

from enterprise_workflow.layer1_config.settings import SETTINGS
from enterprise_workflow.layer2_models.schemas import RunView
from enterprise_workflow.layer3_database.store import WorkflowStore
from enterprise_workflow.layer4_agents.step1_client import build_chat_client
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY, WORKFLOWS
from enterprise_workflow.layer6_engine.step1_engine import Engine, EngineSettings
from enterprise_workflow.layer7_recovery.step1_compensate import Compensator
from enterprise_workflow.layer8_approvals.step1_queue import ApprovalQueue


def build_engine_settings() -> EngineSettings:
    return EngineSettings(
        lease_seconds=SETTINGS.lease_seconds,
        max_attempts=SETTINGS.max_attempts,
        retry_base_seconds=SETTINGS.retry_base_seconds,
        retry_max_seconds=SETTINGS.retry_max_seconds,
        approval_required_above=SETTINGS.approval_required_above,
        approval_expiry_hours=SETTINGS.approval_expiry_hours,
    )


class WorkflowService:
    def __init__(self, database_path: str = "", offline: bool | None = None,
                 worker_id: str = "api-worker") -> None:
        self.store = WorkflowStore(database_path or SETTINGS.sqlite_path)

        if offline is None:
            offline = SETTINGS.is_offline()
        self.offline = offline
        self.client = None if offline else build_chat_client()

        self.compensator = Compensator(self.store, REGISTRY, self.client,
                                       build_engine_settings())
        self.engine = Engine(self.store, REGISTRY, WORKFLOWS, build_engine_settings(),
                             client=self.client, worker_id=worker_id,
                             compensator=self.compensator)
        self.queue = ApprovalQueue(self.store)

        self.worker_thread: threading.Thread | None = None
        self.stop_flag = threading.Event()

    # ---------------------------------------------------------------- worker

    def start_worker(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return

        def loop():
            while not self.stop_flag.is_set():
                try:
                    result = self.engine.tick()
                except Exception:
                    # A worker thread that dies takes every run with it. One
                    # broken run must not be able to do that.
                    time.sleep(1.0)
                    continue
                if not result.did_work:
                    time.sleep(SETTINGS.poll_seconds)

        self.stop_flag.clear()
        self.worker_thread = threading.Thread(target=loop, daemon=True,
                                              name="workflow-worker")
        self.worker_thread.start()

    def stop_worker(self) -> None:
        self.stop_flag.set()

    def worker_is_running(self) -> bool:
        return self.worker_thread is not None and self.worker_thread.is_alive()

    # ---------------------------------------------------------------- runs

    def start_run(self, workflow_name: str, run_input: dict):
        return self.engine.start(workflow_name, run_input)

    def view(self, run_id: str) -> RunView | None:
        run = self.store.get_run(run_id)
        if run is None:
            return None
        return RunView(
            run=run,
            steps=self.store.get_steps(run_id),
            approvals=self.store.list_approvals(run_id=run_id),
            events=self.store.list_events(run_id),
            side_effects=self.store.list_side_effects(run_id),
        )

    def advance(self, limit: int = 100) -> list:
        """Tick synchronously. Used by tests and the 'step once' button."""
        return self.engine.run_until_idle(limit)
