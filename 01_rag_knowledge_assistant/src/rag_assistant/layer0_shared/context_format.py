"""
LAYER 0 - SHARED: CONTEXT BLOCK FORMAT
======================================
The exact text format used to show retrieved passages to the model.

It lives in one file because two very different pieces of code must agree on it:

    1. Layer 6 builds the prompt with format_context_blocks().
    2. The offline (no API key) model reads it back with parse_context_blocks().

The layout, with fences so the boundaries are never ambiguous:

    <<<CONTEXT
    [1] title: Refund Policy | source: refunds.md
    Customers may request a refund within 30 days ...

    [2] title: Shipping | source: shipping.md
    Delivery takes five working days ...
    >>>END CONTEXT

    QUESTION: how long do I have to ask for a refund?
"""

import re

CONTEXT_START = "<<<CONTEXT"
CONTEXT_END = ">>>END CONTEXT"
QUESTION_PREFIX = "QUESTION:"

BLOCK_HEADER = re.compile(r"^\[(\d+)\]\s*title:\s*(.*?)\s*\|\s*source:\s*(.*)$")


def format_context_blocks(passages: list[dict]) -> str:
    """
    Build the fenced context section of the prompt.

    Each passage is a dict with the keys: title, source, text.
    The marker number is the position in the list, counting from 1.
    """
    lines: list[str] = [CONTEXT_START]

    marker = 0
    for passage in passages:
        marker = marker + 1
        title = passage.get("title", "untitled")
        source = passage.get("source", "unknown")
        text = passage.get("text", "")
        lines.append(f"[{marker}] title: {title} | source: {source}")
        lines.append(text.strip())
        lines.append("")

    lines.append(CONTEXT_END)
    return "\n".join(lines)


def extract_context_section(prompt_text: str) -> str:
    """Return only the text between the two fences."""
    start_position = prompt_text.find(CONTEXT_START)
    if start_position == -1:
        return ""

    start_position = start_position + len(CONTEXT_START)
    end_position = prompt_text.find(CONTEXT_END, start_position)
    if end_position == -1:
        return prompt_text[start_position:]
    return prompt_text[start_position:end_position]


def parse_context_blocks(prompt_text: str) -> list[dict]:
    """
    Read the blocks back out of a prompt. Returns a list of dicts shaped
    {"marker": int, "title": str, "source": str, "text": str}.
    """
    section = extract_context_section(prompt_text)
    if section == "":
        return []

    blocks: list[dict] = []
    current: dict | None = None

    for raw_line in section.splitlines():
        line = raw_line.strip()
        header = BLOCK_HEADER.match(line)

        if header is not None:
            if current is not None:
                current["text"] = current["text"].strip()
                blocks.append(current)
            current = {
                "marker": int(header.group(1)),
                "title": header.group(2),
                "source": header.group(3),
                "text": "",
            }
            continue

        if current is not None and line != "":
            current["text"] = current["text"] + line + " "

    if current is not None:
        current["text"] = current["text"].strip()
        blocks.append(current)

    return blocks


def find_question(prompt_text: str) -> str:
    """Pull the question back out of a prompt. Returns '' when it is absent."""
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip()
        if line.startswith(QUESTION_PREFIX):
            return line[len(QUESTION_PREFIX) :].strip()
    return ""
