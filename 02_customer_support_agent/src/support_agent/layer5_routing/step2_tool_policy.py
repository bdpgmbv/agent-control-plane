"""
LAYER 5 - ROUTING, STEP 2: WHICH TOOLS IS THIS TURN ALLOWED TO USE?
===================================================================
The intent narrows the tool list. The caller's role narrows it again.

This is a NARROWING filter, never a widening one - exactly like the access-tag
rule in project 01. A customer cannot gain a tool by asking for it, and a badly
classified intent can only ever reduce what is available, not add to it.

Defence in depth, to be clear about what this is and is not:

    this layer       the wrong tool is not even offered to the model
    layer 4 tools    the tool checks permissions itself, every time

The second one is the real defence. This one exists because the cheapest way to
stop a model misusing a tool is not to show it the tool.
"""

from support_agent.layer1_config.settings import ApiKeyRecord
from support_agent.layer2_models.schemas import Intent
from support_agent.layer4_tools.base import Tool
from support_agent.layer4_tools.registry import tools_for_intent

# Tools only a human support agent may drive. A customer-facing conversation
# never gets these, whatever the intent.
HUMAN_AGENT_ONLY_TOOLS: list[str] = []

# Tools a customer-facing conversation may never use, regardless of intent.
# Empty today; this is where you would put "close_account" or "apply_credit".
CUSTOMER_FORBIDDEN_TOOLS: list[str] = []


def allowed_tools(intent: Intent, caller: ApiKeyRecord) -> list[Tool]:
    """The tools this turn may use."""
    candidates = tools_for_intent(intent)

    permitted: list[Tool] = []
    for tool in candidates:
        if not caller.is_human_agent():
            if tool.name in HUMAN_AGENT_ONLY_TOOLS:
                continue
            if tool.name in CUSTOMER_FORBIDDEN_TOOLS:
                continue
        permitted.append(tool)

    return permitted


def allowed_tool_names(intent: Intent, caller: ApiKeyRecord) -> list[str]:
    names: list[str] = []
    for tool in allowed_tools(intent, caller):
        names.append(tool.name)
    return names
