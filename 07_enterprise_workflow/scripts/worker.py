"""
A worker process.

    python scripts/worker.py --database /path/to.db

Claims steps and runs them until it is stopped. Holds nothing: every worker is
interchangeable with every other, and killing one loses at most the step that
was in flight - which another worker picks up once the lease expires.

This exists as its own process so that scripts/crash_test.py can send it
SIGKILL. Simulating a crash in-process proves the code handles a simulated
crash; killing the process proves it handles a crash.
"""

import argparse
import os
import sys
import time

from enterprise_workflow.layer1_config.settings import SETTINGS
from enterprise_workflow.layer3_database.store import WorkflowStore
from enterprise_workflow.layer4_agents.step1_client import build_chat_client
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY, WORKFLOWS
from enterprise_workflow.layer6_engine.step1_engine import Engine, EngineSettings


def build_engine(database: str, worker_id: str, offline: bool) -> Engine:
    store = WorkflowStore(database)
    client = None if offline else build_chat_client()
    settings = EngineSettings(
        lease_seconds=SETTINGS.lease_seconds,
        max_attempts=SETTINGS.max_attempts,
        retry_base_seconds=SETTINGS.retry_base_seconds,
        retry_max_seconds=SETTINGS.retry_max_seconds,
        approval_required_above=SETTINGS.approval_required_above,
        approval_expiry_hours=SETTINGS.approval_expiry_hours,
    )
    return Engine(store, REGISTRY, WORKFLOWS, settings, client=client, worker_id=worker_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=SETTINGS.sqlite_path)
    parser.add_argument("--worker-id", default="worker-%d" % os.getpid())
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="do one unit of work and stop")
    parser.add_argument("--crash-after", type=int, default=0,
                        help="after this many completed steps, die instantly")
    arguments = parser.parse_args()

    engine = build_engine(arguments.database, arguments.worker_id,
                          arguments.offline or SETTINGS.is_offline())

    if arguments.once:
        result = engine.tick()
        print("%s %s %s" % (result.what, result.run_id, result.step_name), flush=True)
        return 0

    print("worker %s started on %s" % (arguments.worker_id, arguments.database), flush=True)

    if arguments.crash_after > 0:
        # os._exit, not sys.exit and not an exception: no finally blocks, no
        # atexit handlers, no buffer flush. The closest a process can get to
        # being unplugged from inside, and the same thing SIGKILL does from
        # outside - but at a moment the test chooses.
        completed = 0
        while True:
            result = engine.tick()
            if result.did_work and result.what in ("succeeded", "run_succeeded"):
                completed = completed + 1
                if completed >= arguments.crash_after:
                    print("crashing after %d step(s)" % completed, flush=True)
                    os._exit(9)
            if not result.did_work:
                time.sleep(SETTINGS.poll_seconds)

    try:
        engine.work_forever(poll_seconds=SETTINGS.poll_seconds)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
