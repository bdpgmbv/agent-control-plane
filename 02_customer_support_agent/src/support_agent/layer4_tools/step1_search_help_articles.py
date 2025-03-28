"""
LAYER 4 - TOOL 1: SEARCH THE HELP PAGES
=======================================
The safest tool there is: it reads public articles and belongs to nobody.

It exists so the agent has somewhere to go for policy questions instead of
answering them from memory. A model asked "what is your refund window?" will
happily invent "14 days" - confidently, and in your brand voice.
"""

from support_agent.layer0_shared.text_tools import to_keywords
from support_agent.layer2_models.schemas import ToolResult, ToolRisk
from support_agent.layer4_tools.base import Tool, ToolContext


class SearchHelpArticlesTool(Tool):
    name = "search_help_articles"
    description = (
        "Search the company help pages for policy information: refunds, returns, "
        "delivery times, warranty. Use this for any question about what the rules "
        "are. Do not answer policy questions from memory."
    )
    risk = ToolRisk.READ
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, in the customer's own words.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        query = arguments["query"]
        words = to_keywords(query)

        if len(words) == 0:
            return ToolResult(
                ok=False,
                error_code="empty_query",
                error_message="The search query had no usable words in it.",
                retryable=False,
            )

        articles = context.database.search_help_articles(words, limit=2)

        if len(articles) == 0:
            return ToolResult(
                ok=True,
                data={"articles": [], "note": "Nothing in the help pages matches that."},
            )

        trimmed: list[dict] = []
        for article in articles:
            trimmed.append(
                {
                    "article_id": article["article_id"],
                    "title": article["title"],
                    "body": article["body"],
                    "score": article["score"],
                }
            )

        return ToolResult(ok=True, data={"articles": trimmed})
