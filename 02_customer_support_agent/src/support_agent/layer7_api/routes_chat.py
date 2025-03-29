"""
LAYER 7 - API: TALKING TO THE AGENT
===================================
One endpoint does the work. Everything interesting happened in layer 6; this is
the thin layer that turns HTTP into a call and a call into HTTP.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from support_agent.layer0_shared.logging_setup import get_logger
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer1_config.settings import ApiKeyRecord
from support_agent.layer2_models.schemas import ChatRequest, ChatResponse
from support_agent.layer3_storage.database import get_database
from support_agent.layer7_api.dependencies import get_agent
from support_agent.layer7_api.security import customer_for, require_caller

router = APIRouter()
log = get_logger(__name__)


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    caller: ApiKeyRecord = Depends(require_caller),
    acting_for: str = Query(default="", description="Support staff only: which customer to act for."),
) -> ChatResponse:
    """Send one message to the agent and get its reply."""
    if request.message.strip() == "":
        raise HTTPException(status_code=400, detail="The message is empty.")

    customer_id = customer_for(caller, acting_for)

    try:
        return get_agent().handle(
            message_text=request.message,
            conversation_id=request.conversation_id,
            caller=caller,
            customer_id=customer_id,
        )
    except Exception as error:
        metrics.increment("failures_total")
        log.exception("chat turn failed")
        raise HTTPException(status_code=500, detail="The agent failed: %s" % error) from error


@router.get("/api/conversations")
async def list_conversations(caller: ApiKeyRecord = Depends(require_caller)) -> list[dict]:
    """Conversations this caller may see."""
    database = get_database()

    if caller.is_human_agent():
        rows = database.connection.execute(
            "SELECT * FROM conversations ORDER BY last_active_at DESC LIMIT 100"
        ).fetchall()
    else:
        rows = database.connection.execute(
            "SELECT * FROM conversations WHERE customer_id = ? ORDER BY last_active_at DESC LIMIT 100",
            (caller.customer_id,),
        ).fetchall()

    conversations: list[dict] = []
    for row in rows:
        conversations.append(dict(row))
    return conversations


@router.get("/api/conversations/{conversation_id}")
async def read_conversation(
    conversation_id: str,
    caller: ApiKeyRecord = Depends(require_caller),
) -> dict:
    """The full transcript, including the tool calls."""
    database = get_database()
    conversation = database.get_conversation(conversation_id)

    # Not found and not-yours give the same answer, so the endpoint cannot be
    # used to discover which conversation ids exist.
    if conversation is None or not caller.may_act_for(conversation["customer_id"]):
        raise HTTPException(status_code=404, detail="No such conversation.")

    messages: list[dict] = []
    for message in database.read_messages(conversation_id):
        calls: list[dict] = []
        for call in message.tool_calls:
            calls.append(call.model_dump())

        messages.append(
            {
                "role": message.role,
                "content": message.content,
                "tool_calls": calls,
                "tool_name": message.tool_name,
                "created_at": message.created_at,
            }
        )

    return {"conversation": conversation, "messages": messages}
