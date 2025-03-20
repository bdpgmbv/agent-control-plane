"""
LAYER 0 - SHARED: THE LANGUAGE MODEL CLIENT
===========================================
One small interface, two implementations:

    OpenAiChatClient   - real calls to an OpenAI "mini" model.
    OfflineChatClient  - no network. Answers by picking the best sentences out
                         of the retrieved context and citing them.

Why bother with the offline one?
    * The tests must run with no API key and no bill.
    * You can demonstrate the whole pipeline before you have a key.
    * It proves the system does not depend on any one vendor.

Both return the same LlmResult, so no other layer needs to know which is in use.
"""

import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

from rag_assistant.layer0_shared.context_format import find_question, parse_context_blocks
from rag_assistant.layer0_shared.cost import chat_cost_usd, rough_token_count
from rag_assistant.layer0_shared.text_tools import to_sentences, word_overlap_score
from rag_assistant.layer1_config.settings import settings

if TYPE_CHECKING:
    # Only for type checking, so this module still imports
    # without the OpenAI SDK present.
    from openai.types.chat import ChatCompletionMessageParam

# What the assistant says when the documents do not contain the answer.
# Defined once so the API, the UI and the tests all agree on it.
ABSTAIN_SENTENCE = (
    "I don't know based on the documents I have access to. "
    "Nothing in the knowledge base answers this question."
)


class LlmResult:
    """The model's reply plus everything a production team wants to record."""

    def __init__(
        self,
        text: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        finish_reason: str = "stop",
    ) -> None:
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason


class OpenAiChatClient:
    """Talks to OpenAI's chat completions endpoint."""

    def __init__(self, api_key: str, model: str, temperature: float, max_output_tokens: int) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.is_live = True

    def complete(self, system_prompt: str, user_prompt: str, json_mode: bool = False) -> LlmResult:
        started = time.perf_counter()

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Two explicit calls rather than one splatted dict. Splatting hides
        # every keyword from the type checker, which is the one thing it is
        # genuinely useful for on somebody else's SDK.
        if json_mode:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                messages=messages,
            )

        choice = response.choices[0]
        text = choice.message.content
        if text is None:
            text = ""

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage is not None:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return LlmResult(
            text=text.strip(),
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=chat_cost_usd(self.model, prompt_tokens, completion_tokens),
            latency_ms=elapsed_ms,
            finish_reason=str(choice.finish_reason),
        )

    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        usage_sink: dict | None = None,
    ) -> Iterator[str]:
        """
        Yield the answer piece by piece, so the UI can show it as it arrives.

        usage_sink is filled in once the stream finishes. Streaming would
        otherwise report no tokens and no cost at all, which quietly hides the
        spend of whichever path your users actually use.
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
            # Ask the provider to send a usage record in the final chunk.
            stream_options={"include_usage": True},
        )

        prompt_tokens = 0
        completion_tokens = 0

        for event in stream:
            if event.usage is not None:
                prompt_tokens = event.usage.prompt_tokens
                completion_tokens = event.usage.completion_tokens

            if len(event.choices) == 0:
                continue
            piece = event.choices[0].delta.content
            if piece is not None and piece != "":
                yield piece

        if usage_sink is not None:
            usage_sink["prompt_tokens"] = prompt_tokens
            usage_sink["completion_tokens"] = completion_tokens
            usage_sink["cost_usd"] = chat_cost_usd(self.model, prompt_tokens, completion_tokens)
            usage_sink["model"] = self.model


class OfflineChatClient:
    """
    A working answer generator that makes no network calls.

    How it answers:
        1. Read the context blocks out of the prompt.
        2. Always take the best sentence from passage [1], because the reranker
           already judged that passage the most relevant one.
        3. Add any other sentence that strongly matches the question.
        4. If there are no passages at all, abstain.

    BE CLEAR ABOUT WHAT THIS IS. It is a lexical stand-in, not a language model.
    It matches words, so it cannot tell that "can I get my money back" and
    "refund policy" are the same question - step 2 above is what stops it going
    badly wrong. It exists so the tests, the demo and the evaluation suite all
    run with no API key and no bill. Put a real key in .env and OpenAiChatClient
    takes over, and answer quality becomes the model's job.
    """

    def __init__(self, max_sentences: int = 2, minimum_sentence_score: float = 0.25) -> None:
        self.model = "offline-extractive"
        self.max_sentences = max_sentences
        self.minimum_sentence_score = minimum_sentence_score
        self.is_live = False

    def best_sentence_of(self, question: str, block: dict) -> str:
        """The sentence in one passage that best matches the question."""
        sentences = to_sentences(block["text"])
        if len(sentences) == 0:
            return ""

        best_sentence = sentences[0]
        best_score = word_overlap_score(question, sentences[0])

        for sentence in sentences[1:]:
            score = word_overlap_score(question, sentence)
            if score > best_score:
                best_score = score
                best_sentence = sentence

        return best_sentence

    def build_answer(self, user_prompt: str) -> str:
        question = find_question(user_prompt)
        blocks = parse_context_blocks(user_prompt)

        if len(blocks) == 0 or question == "":
            return ABSTAIN_SENTENCE

        chosen: list[str] = []
        used_sentences: set[str] = set()

        # Step 2: lead with the top passage. Retrieval already decided it was the
        # most relevant thing we have, so the answer should start there.
        top_block = blocks[0]
        leading_sentence = self.best_sentence_of(question, top_block)
        if leading_sentence != "":
            used_sentences.add(leading_sentence)
            chosen.append(f"{leading_sentence} [{top_block['marker']}]")

        # Step 3: add other sentences that clearly match the question.
        candidates: list[tuple[float, int, str]] = []
        for block in blocks:
            for sentence in to_sentences(block["text"]):
                score = word_overlap_score(question, sentence)
                if score >= self.minimum_sentence_score:
                    candidates.append((score, block["marker"], sentence))

        candidates.sort(key=lambda item: item[0], reverse=True)

        for _score, marker, sentence in candidates:
            if len(chosen) >= self.max_sentences:
                break
            if sentence in used_sentences:
                continue
            used_sentences.add(sentence)
            chosen.append(f"{sentence} [{marker}]")

        if len(chosen) == 0:
            return ABSTAIN_SENTENCE

        return " ".join(chosen)

    def complete(self, system_prompt: str, user_prompt: str, json_mode: bool = False) -> LlmResult:
        started = time.perf_counter()

        if json_mode:
            # The offline model does not pretend to produce structured judgements.
            # Callers that need JSON have their own offline path (see layer 8).
            text = "{}"
        else:
            text = self.build_answer(user_prompt)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return LlmResult(
            text=text,
            model=self.model,
            prompt_tokens=rough_token_count(system_prompt) + rough_token_count(user_prompt),
            completion_tokens=rough_token_count(text),
            cost_usd=0.0,
            latency_ms=elapsed_ms,
        )

    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        usage_sink: dict | None = None,
    ) -> Iterator[str]:
        answer = self.build_answer(user_prompt)
        for word in answer.split(" "):
            yield word + " "

        if usage_sink is not None:
            usage_sink["prompt_tokens"] = rough_token_count(system_prompt) + rough_token_count(user_prompt)
            usage_sink["completion_tokens"] = rough_token_count(answer)
            usage_sink["cost_usd"] = 0.0
            usage_sink["model"] = self.model


def build_chat_client():
    """Choose the chat client based on configuration. The only such decision."""
    if settings.using_real_llm():
        return OpenAiChatClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    return OfflineChatClient()


def build_judge_client():
    """A separate, cheaper client used only by the evaluation suite (layer 8)."""
    if settings.using_real_llm():
        return OpenAiChatClient(
            api_key=settings.openai_api_key,
            model=settings.judge_model,
            temperature=0.0,
            max_output_tokens=500,
        )
    return OfflineChatClient()
