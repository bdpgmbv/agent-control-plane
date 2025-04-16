"""
LAYER 9 - API: THE ENDPOINTS
============================
Thin. Everything interesting happened in layers 4 to 8.

/api/schema is worth having: an analyst who can see exactly which tables and
columns the copilot was offered can tell at a glance whether a wrong answer came
from bad SQL or from the wrong tables being retrieved.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status

from sql_copilot.layer0_shared.logging_setup import get_logger
from sql_copilot.layer0_shared.metrics import metrics
from sql_copilot.layer1_config.settings import ApiKeyRecord, settings
from sql_copilot.layer2_models.schemas import AskRequest, AskResponse
from sql_copilot.layer3_database.connection import get_database
from sql_copilot.layer3_database.schema_sql import ALLOWED_TABLES
from sql_copilot.layer4_schema.step1_introspect import get_schema
from sql_copilot.layer9_api.copilot_service import CopilotService

router = APIRouter()
log = get_logger(__name__)

_service: CopilotService | None = None

OPEN_ACCESS = ApiKeyRecord(key="open", role="admin")


def get_service() -> CopilotService:
    global _service
    if _service is None:
        _service = CopilotService()
    return _service


def reset_service() -> None:
    global _service
    _service = None


async def require_caller(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    if not settings.require_api_key:
        return OPEN_ACCESS

    if x_api_key is None or x_api_key.strip() == "":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header.")

    record = settings.find_api_key(x_api_key.strip())
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown API key.")
    return record


@router.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest, caller: ApiKeyRecord = Depends(require_caller)) -> AskResponse:
    """Answer one analytics question."""
    if request.question.strip() == "":
        raise HTTPException(status_code=400, detail="The question is empty.")

    if len(request.question) > 500:
        raise HTTPException(status_code=400, detail="The question is too long (500 characters maximum).")

    try:
        return get_service().ask(request)
    except Exception as error:
        metrics.increment("failures_total")
        log.exception("ask failed")
        raise HTTPException(status_code=500, detail="The copilot failed: %s" % error) from error


@router.get("/api/schema")
async def read_schema() -> dict:
    """
    The tables the copilot can see, and the ones it cannot.

    Showing both is deliberate. "employee_salaries exists and is not available"
    is more useful to an analyst than pretending it is not there, and it makes the
    allowlist something you can check rather than something you have to trust.
    """
    database = get_database()
    schema = get_schema(database)

    tables: list[dict] = []
    for table in schema.tables:
        columns: list[dict] = []
        for column in table.columns:
            columns.append(
                {
                    "name": column.name,
                    "data_type": column.data_type,
                    "description": column.description,
                    "is_primary_key": column.is_primary_key,
                    "references": column.references,
                }
            )
        tables.append(
            {
                "name": table.name,
                "description": table.description,
                "row_count": table.row_count,
                "columns": columns,
            }
        )

    hidden: list[str] = []
    for name in database.list_table_names():
        if name not in ALLOWED_TABLES:
            hidden.append(name)

    return {"tables": tables, "allowed": ALLOWED_TABLES, "not_available": hidden}


@router.get("/api/health")
async def health() -> dict:
    database_ok = True
    error_text = ""
    table_count = 0

    try:
        table_count = len(get_database().list_table_names())
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
        "tables_in_file": table_count,
        "tables_available": len(ALLOWED_TABLES),
    }


@router.get("/api/config")
async def configuration() -> dict:
    return settings.describe()


@router.get("/api/metrics")
async def read_metrics() -> dict:
    return metrics.snapshot()
