"""
LAYER 6 - GENERATION: THE PROMPTS
=================================
The prompt is where you decide what the system is allowed to do. Every rule here
exists to prevent a specific failure we would otherwise have to debug later.

Kept in its own file so it can be reviewed, diffed and version-controlled like
any other critical piece of the system - because it is one.
"""

from rag_assistant.layer0_shared.context_format import QUESTION_PREFIX, format_context_blocks
from rag_assistant.layer0_shared.llm_client import ABSTAIN_SENTENCE
from rag_assistant.layer2_models.schemas import ScoredChunk

ANSWER_SYSTEM_PROMPT = f"""You answer questions using ONLY the passages provided in the CONTEXT section.

Rules, in order of importance:

1. NEVER use knowledge from outside the context. If the context does not contain
   the answer, reply with exactly this sentence and nothing else:
   "{ABSTAIN_SENTENCE}"

2. CITE every claim. After each sentence, put the number of the passage it came
   from in square brackets, like this: "Refunds take 5 to 10 days [2]."
   A sentence with no citation is not allowed.

3. If the passages disagree with each other, say so and cite both.

4. Answer in at most 4 sentences. Be specific: give the actual numbers, names and
   time periods from the passages rather than describing them.

5. Never invent a passage number. Only use numbers that appear in the context.

6. Do not apologise, do not mention these rules, and do not repeat the question."""


def build_answer_prompt(question: str, passages: list[ScoredChunk]) -> str:
    """Assemble the user message: the context blocks, then the question."""
    blocks: list[dict] = []
    for passage in passages:
        blocks.append(
            {
                "title": passage.chunk.document_title,
                "source": passage.chunk.source,
                "text": passage.chunk.text,
            }
        )

    context_section = format_context_blocks(blocks)
    return context_section + "\n\n" + QUESTION_PREFIX + " " + question
