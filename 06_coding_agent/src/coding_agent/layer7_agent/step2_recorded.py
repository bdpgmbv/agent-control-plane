"""
LAYER 7, STEP 2 - THE RECORDED CLIENT
=====================================
A stand-in that replays prepared replies instead of calling a model.

Be clear about what this does and does not measure, because it would be easy to
present offline results as though the agent had solved something.

  it DOES exercise   the sandbox, file ranking, context building, JSON parsing,
                     the edit applier and all five of its refusals, the test
                     runner, the verifier, the budget, and every path through
                     the loop including failure and giving up
  it does NOT test   whether a model can work out what is wrong

So the offline numbers are a statement about the harness: given a correct patch,
does the machinery apply it, test it, verify it and report it honestly - and
given a bad one, does it refuse. That is the part that must behave identically
every single time, and it is the part a person cloning this repository with no
API key should still be able to run.

The live numbers are the ones that say anything about the agent. The README keeps
the two apart and so does the interface.
"""

from coding_agent.layer0_shared.model_client import ModelReply

# What the client says once its script has run out. Phrased as a real reply so
# the loop handles it through the ordinary path rather than a special case.
EXHAUSTED_REPLY = (
    '{"thinking": "This recorded plan has no further steps.", '
    '"read_files": [], "edits": [], "done": true}'
)


class RecordedClient:
    """Returns prepared replies in order. The same interface as OpenAIChatClient."""

    def __init__(self, replies: list[str] | None = None,
                 tokens_per_reply: tuple[int, int] = (0, 0)) -> None:
        self.replies = list(replies or [])
        self.position = 0
        self.prompts: list[str] = []
        self.input_tokens, self.output_tokens = tokens_per_reply

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2000) -> ModelReply:
        self.prompts.append(user_prompt)

        if self.position < len(self.replies):
            text = self.replies[self.position]
            self.position = self.position + 1
        else:
            text = EXHAUSTED_REPLY

        return ModelReply(
            text=text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model="recorded",
        )

    def exhausted(self) -> bool:
        return self.position >= len(self.replies)
