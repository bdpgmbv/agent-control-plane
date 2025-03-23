"""
LAYER 7 - API: METRICS AND CACHE ADMIN
======================================
The numbers a production team looks at, and the one button they press when the
cache is serving something stale.
"""

from fastapi import APIRouter, Depends

from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer1_config.settings import ApiKeyRecord
from rag_assistant.layer7_api.dependencies import get_cache
from rag_assistant.layer7_api.security import require_admin

router = APIRouter()


@router.get("/api/metrics")
async def read_metrics() -> dict:
    """
    Everything measured since the process started.

    Deliberately open: a metrics endpoint that needs a key is a metrics endpoint
    nobody scrapes. It contains no customer data and no secrets.
    """
    snapshot = metrics.snapshot()
    snapshot["cache"] = get_cache().describe()
    return snapshot


@router.post("/api/cache/clear")
async def clear_cache(caller: ApiKeyRecord = Depends(require_admin)) -> dict:
    """Empty the answer cache. Needed after you change or delete a document."""
    get_cache().clear()
    return {"status": "cleared", "cache": get_cache().describe()}
