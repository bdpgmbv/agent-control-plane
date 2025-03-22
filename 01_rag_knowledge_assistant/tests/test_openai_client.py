"""
TESTS FOR THE OPENAI CODE PATH - without an API key and without spending money.
===============================================================================
Every other test runs the offline provider. That covers the pipeline, but it
leaves one real gap: the code that actually talks to OpenAI never runs, so a
mistake in it - reading the wrong usage field, mishandling a streaming chunk,
miscalculating cost - would not be caught until the first real request.

So we replace the OpenAI SDK client with a stand-in that returns exactly the
shapes the real one returns, and check our handling of them.

WHAT THIS PROVES
    Our request arguments, our response parsing, our token accounting and our
    cost arithmetic are correct.

WHAT IT CANNOT PROVE
    That the real service behaves as documented, or that the model's answers are
    any good. Those need a key. Be clear about the boundary rather than claiming
    more coverage than you have.
"""

from rag_assistant.layer0_shared.cost import chat_cost_usd
from rag_assistant.layer0_shared.llm_client import OpenAiChatClient

# --------------------------------------------------------------------------
#  Stand-ins shaped exactly like the OpenAI SDK's response objects
# --------------------------------------------------------------------------

class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content, prompt_tokens, completion_tokens):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(prompt_tokens, completion_tokens)


class FakeDelta:
    def __init__(self, content):
        self.content = content


class FakeStreamChoice:
    def __init__(self, content):
        self.delta = FakeDelta(content)


class FakeStreamEvent:
    def __init__(self, content=None, usage=None):
        if content is None:
            self.choices = []
        else:
            self.choices = [FakeStreamChoice(content)]
        self.usage = usage


class FakeCompletions:
    """Records what we sent, and returns what OpenAI would return."""

    def __init__(self, reply_text="Refunds take 30 days [1].", stream_pieces=None):
        self.reply_text = reply_text
        self.stream_pieces = stream_pieces or ["Refunds ", "take ", "30 days [1]."]
        self.last_arguments = None

    def create(self, **arguments):
        self.last_arguments = arguments

        if arguments.get("stream") is True:
            events = []
            for piece in self.stream_pieces:
                events.append(FakeStreamEvent(content=piece))
            # The real API sends usage in a final chunk with no choices.
            events.append(FakeStreamEvent(usage=FakeUsage(120, 30)))
            return events

        return FakeResponse(self.reply_text, prompt_tokens=250, completion_tokens=40)


class FakeOpenAiSdk:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


def build_client_with_fake(completions) -> OpenAiChatClient:
    """An OpenAiChatClient whose SDK client has been swapped for the fake."""
    client = OpenAiChatClient.__new__(OpenAiChatClient)
    client.client = FakeOpenAiSdk(completions)
    client.model = "gpt-4o-mini"
    client.temperature = 0.0
    client.max_output_tokens = 900
    client.is_live = True
    return client


# --------------------------------------------------------------------------
#  Tests
# --------------------------------------------------------------------------

def test_the_request_is_built_correctly():
    completions = FakeCompletions()
    client = build_client_with_fake(completions)

    client.complete(system_prompt="SYSTEM RULES", user_prompt="the question")

    sent = completions.last_arguments
    assert sent["model"] == "gpt-4o-mini"
    assert sent["temperature"] == 0.0
    assert sent["max_tokens"] == 900
    assert sent["messages"][0] == {"role": "system", "content": "SYSTEM RULES"}
    assert sent["messages"][1] == {"role": "user", "content": "the question"}
    # json_mode was not asked for, so no response_format should be sent.
    assert "response_format" not in sent


def test_json_mode_asks_for_a_json_object():
    completions = FakeCompletions(reply_text='{"queries": ["a", "b"]}')
    client = build_client_with_fake(completions)

    client.complete("system", "user", json_mode=True)

    assert completions.last_arguments["response_format"] == {"type": "json_object"}


def test_the_reply_and_its_token_usage_are_read_correctly():
    completions = FakeCompletions(reply_text="  Refunds take 30 days [1].  ")
    client = build_client_with_fake(completions)

    result = client.complete("system", "user")

    assert result.text == "Refunds take 30 days [1]."     # whitespace trimmed
    assert result.prompt_tokens == 250
    assert result.completion_tokens == 40
    assert result.model == "gpt-4o-mini"
    assert result.finish_reason == "stop"


def test_the_cost_matches_the_price_table():
    completions = FakeCompletions()
    client = build_client_with_fake(completions)

    result = client.complete("system", "user")

    assert result.cost_usd == chat_cost_usd("gpt-4o-mini", 250, 40)
    assert result.cost_usd > 0


def test_a_reply_with_no_content_does_not_crash():
    """The API may return content=None. Treat it as an empty answer, not a crash."""
    completions = FakeCompletions(reply_text=None)
    client = build_client_with_fake(completions)

    result = client.complete("system", "user")
    assert result.text == ""


def test_streaming_yields_the_pieces_in_order():
    completions = FakeCompletions(stream_pieces=["Express ", "costs ", "12.99."])
    client = build_client_with_fake(completions)

    collected = []
    for piece in client.stream("system", "user"):
        collected.append(piece)

    assert "".join(collected) == "Express costs 12.99."
    assert completions.last_arguments["stream"] is True
    # Without this option the provider sends no usage at all, and the streaming
    # path silently reports zero tokens and zero cost.
    assert completions.last_arguments["stream_options"] == {"include_usage": True}


def test_streaming_fills_in_the_token_usage():
    completions = FakeCompletions()
    client = build_client_with_fake(completions)

    usage: dict = {}
    for _piece in client.stream("system", "user", usage_sink=usage):
        pass

    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 30
    assert usage["cost_usd"] == chat_cost_usd("gpt-4o-mini", 120, 30)


def test_a_final_chunk_with_no_choices_is_not_treated_as_text():
    """
    The usage chunk arrives with an empty choices list. Indexing choices[0]
    without checking is the classic crash in a streaming client.
    """
    completions = FakeCompletions(stream_pieces=["only piece"])
    client = build_client_with_fake(completions)

    collected = []
    for piece in client.stream("system", "user"):
        collected.append(piece)

    assert collected == ["only piece"]
