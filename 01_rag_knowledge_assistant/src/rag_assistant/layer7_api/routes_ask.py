"""
LAYER 7 - API: ASKING QUESTIONS
===============================
Two endpoints for the same operation:

  POST /api/ask         - wait for the whole answer, get everything at once.
                          Use this from other programs, and from the evaluation
                          suite, because it returns the verified result.

  POST /api/ask/stream  - words appear as they are generated, then a final event
                          carries the citations and scores. Use this from a UI:
                          a person will wait four seconds for a streaming answer
                          and give up after two seconds of a blank screen.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from rag_assistant.layer0_shared.logging_setup import get_logger
from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer1_config.settings import ApiKeyRecord
from rag_assistant.layer2_models.schemas import AnswerResponse, AskRequest
from rag_assistant.layer3_storage.index_guard import IndexMismatchError
from rag_assistant.layer7_api.dependencies import get_service
from rag_assistant.layer7_api.security import require_caller

router = APIRouter()
log = get_logger(__name__)


@router.post("/api/ask", response_model=AnswerResponse)
async def ask(
    request: AskRequest,
    caller: ApiKeyRecord = Depends(require_caller),
) -> AnswerResponse:
    """Answer one question, with citations."""
    if request.question.strip() == "":
        raise HTTPException(status_code=400, detail="The question is empty.")

    try:
        return get_service().ask(request, allowed_tags=caller.allowed_tags)
    except IndexMismatchError as error:
        # 409 Conflict: the request is fine, the stored data is not.
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        metrics.increment("failures_total")
        log.exception("ask failed")
        raise HTTPException(status_code=500, detail="Answering failed: %s" % error) from error


@router.post("/api/ask/stream")
async def ask_stream(
    request: AskRequest,
    caller: ApiKeyRecord = Depends(require_caller),
):
    """
    Answer one question as a stream of Server-Sent Events.

    Each line is "data: {json}". Two event kinds:
        {"type": "token", "text": "..."}   one piece of the answer
        {"type": "done",  ...}             citations, groundedness, confidence
    """
    if request.question.strip() == "":
        raise HTTPException(status_code=400, detail="The question is empty.")

    service = get_service()

    def event_stream():
        try:
            for kind, payload in service.stream_answer(request, allowed_tags=caller.allowed_tags):
                if kind == "token":
                    message = {"type": "token", "text": payload}
                else:
                    message = {"type": "done"}
                    for key in payload:
                        message[key] = payload[key]
                yield "data: " + json.dumps(message) + "\n\n"
        except Exception as error:
            metrics.increment("failures_total")
            log.exception("stream failed")
            yield "data: " + json.dumps({"type": "error", "message": str(error)}) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/retrieve")
async def retrieve_only(
    request: AskRequest,
    caller: ApiKeyRecord = Depends(require_caller),
) -> dict:
    """
    Show what retrieval found, with no answer generated.

    This endpoint exists for debugging. When an answer is wrong, the first thing
    you need to know is whether retrieval found the right passage at all.
    """
    outcome = get_service().retrieve_only(
        question=request.question,
        allowed_tags=caller.allowed_tags,
        top_k=request.top_k,
    )

    passages: list[dict] = []
    for passage in outcome.passages:
        passages.append(
            {
                "chunk_id": passage.chunk.chunk_id,
                "document_title": passage.chunk.document_title,
                "source": passage.chunk.source,
                "access_tag": passage.chunk.access_tag,
                "heading": passage.chunk.metadata.get("heading", ""),
                "text": passage.chunk.text,
                "fused_score": passage.score,
                "vector_rank": passage.vector_rank,
                "keyword_rank": passage.keyword_rank,
                "vector_score": passage.vector_score,
                "keyword_score": passage.keyword_score,
                "rerank_score": passage.rerank_score,
                "reason": passage.reason,
            }
        )

    return {"passages": passages, "trace": outcome.trace.model_dump()}
