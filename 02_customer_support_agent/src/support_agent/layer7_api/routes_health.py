"""
LAYER 7 - API: HEALTH, CONFIGURATION AND METRICS
================================================
/api/health   is the process alive and can it reach its database
/api/config   what this deployment is configured to do (never secrets)
/api/metrics  the numbers a support team watches
"""

from fastapi import APIRouter, Depends

from support_agent.layer0_shared.metrics import metrics
from support_agent.layer1_config.settings import ApiKeyRecord, settings
from support_agent.layer3_storage.database import get_database
from support_agent.layer3_storage.seed_data import seed
from support_agent.layer7_api.security import require_human_agent

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    database_ok = True
    error_text = ""
    counts: dict = {}

    try:
        database = get_database()
        counts = {
            "orders": database.connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"],
            "conversations": database.connection.execute(
                "SELECT COUNT(*) AS n FROM conversations"
            ).fetchone()["n"],
            "tickets": database.count_tickets(),
            "audit_rows": database.count_audit(),
        }
    except Exception as error:
        database_ok = False
        error_text = str(error)

    if database_ok:
        status_text = "ok"
    else:
        status_text = "degraded"

    return {
        "status": status_text,
        "database_reachable": database_ok,
        "database_error": error_text,
        "counts": counts,
    }


@router.get("/api/config")
async def configuration() -> dict:
    return settings.describe()


@router.get("/api/metrics")
async def read_metrics() -> dict:
    """Open on purpose: no customer data, no secrets, and nobody scrapes a
    metrics endpoint that needs a key."""
    return metrics.snapshot()


@router.post("/api/demo/reset")
async def reset_demo(caller: ApiKeyRecord = Depends(require_human_agent)) -> dict:
    """
    Put the demo back to its starting state.

    Offered because this is a learning project: refunds issued by a previous run
    change how the next one behaves, which makes the demo confusing.
    """
    database = get_database()
    database.reset_agent_data()
    counts = seed(database, fresh=True)
    metrics.reset()
    return {"status": "reset", "seeded": counts}
