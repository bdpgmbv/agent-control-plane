"""
LAYER 7 - API: AUTHENTICATION AND ACCESS CONTROL
================================================
Two separate questions, answered here and only here:

    WHO are you?          -> the API key in the X-API-Key header
    WHAT may you see?     -> the access tags attached to that key

The second one is what makes this different from a demo. Every document has an
access tag. Every key lists the tags it may read. The retrieval layer receives
that list and pushes it into the SQL query, so a document you are not allowed to
see is never loaded, never embedded into a prompt, and cannot leak into an answer.

Getting this wrong is the most expensive bug in a company knowledge assistant:
the answer is correct, well cited, and quotes a document the asker should never
have seen.
"""

from fastapi import Header, HTTPException, status

from rag_assistant.layer1_config.settings import ApiKeyRecord, settings

API_KEY_HEADER_NAME = "X-API-Key"

# The identity used when REQUIRE_API_KEY=false, so the demo can run open.
OPEN_ACCESS_IDENTITY = ApiKeyRecord(
    key="open-access",
    role="admin",
    allowed_tags=["public", "internal", "secret"],
)


def identify_caller(api_key: str | None) -> ApiKeyRecord:
    """Turn a presented key into an identity, or refuse."""
    if not settings.require_api_key:
        return OPEN_ACCESS_IDENTITY

    if api_key is None or api_key.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {API_KEY_HEADER_NAME} header.",
        )

    record = settings.find_api_key(api_key.strip())
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown API key.",
        )

    return record


async def require_caller(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    """FastAPI dependency: any authenticated caller."""
    return identify_caller(x_api_key)


async def require_admin(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    """FastAPI dependency: an admin caller. Used for ingest and delete."""
    caller = identify_caller(x_api_key)
    if not caller.may_write():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an admin key. Your key has role '%s'." % caller.role,
        )
    return caller
