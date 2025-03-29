"""
LAYER 7 - API: WHO IS TALKING
=============================
Two kinds of caller:

    customer     acts only for themselves. Their customer id comes from the key,
                 never from the request body.
    agent_human  support staff. Can act for any customer and can approve held
                 actions.

THE CUSTOMER ID COMES FROM THE KEY, NOT THE REQUEST
    If the request could say "I am CUST-1001", every customer could read every
    other customer's orders by editing one field. The key decides who you are;
    the body only decides what you are asking.
"""

from fastapi import Header, HTTPException, status

from support_agent.layer1_config.settings import ApiKeyRecord, settings

API_KEY_HEADER_NAME = "X-API-Key"

# Used when REQUIRE_API_KEY=false, so the demo can be opened without keys.
OPEN_ACCESS_IDENTITY = ApiKeyRecord(key="open-access", role="agent_human", customer_id="*")


def identify_caller(api_key: str | None) -> ApiKeyRecord:
    if not settings.require_api_key:
        return OPEN_ACCESS_IDENTITY

    if api_key is None or api_key.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing %s header." % API_KEY_HEADER_NAME,
        )

    record = settings.find_api_key(api_key.strip())
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown API key.")

    return record


async def require_caller(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    """Any authenticated caller."""
    return identify_caller(x_api_key)


async def require_human_agent(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    """Support staff only. Approvals and the audit log live behind this."""
    caller = identify_caller(x_api_key)
    if not caller.is_human_agent():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This needs a support agent key. Your key has role '%s'." % caller.role,
        )
    return caller


def customer_for(caller: ApiKeyRecord, requested_customer_id: str = "") -> str:
    """
    Which customer this turn acts for.

    A customer key always resolves to itself, whatever the request asked for.
    Only a human agent may name a different customer.
    """
    if caller.is_human_agent():
        if requested_customer_id.strip() != "":
            return requested_customer_id.strip()
        return "CUST-1001"
    return caller.customer_id
