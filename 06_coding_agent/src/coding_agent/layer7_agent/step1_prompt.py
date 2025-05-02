"""
LAYER 7, STEP 1 - WHAT THE AGENT IS ASKED, AND HOW ITS ANSWER IS READ
=====================================================================
One system prompt, one JSON contract, and a parser that assumes the reply is
imperfect.

The prompt tells the agent it may not edit tests. The prompt does not ENFORCE
that - layer 6 refuses the edit and layer 8 rejects the patch. This is the same
division as every other project in this series: the prompt makes the right thing
easy, the code makes the wrong thing impossible. Writing "you must not" and then
trusting it is how you end up with a green suite and a deleted assertion.

Two things in the prompt are doing real work beyond politeness:

  the failure output      the agent is given the actual assertion and values,
                          not a summary of them. "expected 120.0, got 80.0" is
                          the entire diagnosis for most of these bugs
  what it already tried   repeating a failed edit is the most common way an
                          agent burns its budget. It has no memory between
                          calls, so the history has to be handed back every time
"""

import json
import re

from coding_agent.layer2_models.schemas import FileEdit

SYSTEM_PROMPT = """You are a careful software engineer fixing a bug in a Python repository.

HOW YOU WORK

You are given a repository map, some file contents, and the output of a failing
test suite. You reply with JSON describing what you want to do next. You will be
called again with the result, so you can work in several small steps.

THE RULES

1. The tests are the specification. You may NOT edit, delete, weaken or skip any
   test. Attempts to do so are refused by the system that applies your edits.
   If a test looks wrong to you, say so in "thinking" and fix the source anyway -
   your job is to make the existing tests pass.

2. Edits are exact string replacements. "old_text" must appear in the file
   EXACTLY ONCE, character for character, including indentation. Quote enough
   surrounding lines to make it unique. Do not rewrite whole files.

3. Change as little as possible. A one-line fix that makes the suite green is
   better than a rewrite that also makes it green.

4. Fix the cause, not the symptom. Do not wrap the failing call in try/except,
   do not special-case the exact values the test uses, and do not add a branch
   that detects the test. Those make the suite green and the software worse.

YOUR REPLY

Reply with a single JSON object and nothing else:

{
  "thinking": "one or two sentences on what you think is wrong and why",
  "read_files": ["path/to/file.py"],
  "edits": [
    {
      "path": "path/to/file.py",
      "old_text": "the exact text to replace",
      "new_text": "what it becomes",
      "reason": "why this fixes it"
    }
  ],
  "done": false
}

Use "read_files" when you need to see a file before deciding, and leave "edits"
empty in that case. Use "edits" when you know what to change. Set "done" to true
only if you believe the work is finished and no edit is needed."""


TASK_TEMPLATE = """## The issue

%s

## The failing test(s)

%s

## Test output

```
%s
```

## The repository

%s
"""

HISTORY_TEMPLATE = """## What you have already tried

%s

Do not repeat an edit that was refused or that did not help. If you are stuck,
read a file you have not looked at yet.
"""


def build_user_prompt(issue: str, target_tests: list[str], test_output: str,
                      context_text: str, history: list[str]) -> str:
    tests_text = "\n".join(target_tests) if len(target_tests) > 0 else "(the whole suite)"

    prompt = TASK_TEMPLATE % (issue, tests_text, test_output, context_text)

    if len(history) > 0:
        lines = []
        for index, entry in enumerate(history):
            lines.append("%d. %s" % (index + 1, entry))
        prompt = prompt + "\n" + HISTORY_TEMPLATE % "\n".join(lines)

    return prompt


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline >= 0:
            cleaned = cleaned[newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def parse_reply(text: str) -> tuple[dict, str]:
    """
    Read the agent's JSON. Returns (parsed, error message).

    The recovery path matters more here than in the other projects. A reply
    carrying an edit is mostly source code inside a JSON string, so it is full of
    quotes, backslashes and newlines - the exact things that make a model's JSON
    come out malformed. Giving up on the first JSONDecodeError would throw away a
    correct patch over a stray character, so the first {...} span is tried as
    well before reporting failure.
    """
    cleaned = strip_json_fence(text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed, ""
    except json.JSONDecodeError as error:
        first_error = str(error)

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match is not None:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed, ""
            except json.JSONDecodeError:
                pass

        return {}, ("the reply was not valid JSON (%s). Reply with a single "
                    "JSON object and nothing else." % first_error)

    return {}, "the reply was JSON but not an object"


def edits_from_reply(parsed: dict) -> tuple[list[FileEdit], list[str]]:
    """Returns (edits, complaints about malformed entries)."""
    edits: list[FileEdit] = []
    complaints: list[str] = []

    raw_edits = parsed.get("edits")
    if raw_edits is None:
        return edits, complaints
    if not isinstance(raw_edits, list):
        return edits, ["'edits' must be a list"]

    for index, entry in enumerate(raw_edits):
        if not isinstance(entry, dict):
            complaints.append("edit %d is not an object" % (index + 1))
            continue

        path = entry.get("path")
        old_text = entry.get("old_text")
        new_text = entry.get("new_text")

        if path is None or str(path).strip() == "":
            complaints.append("edit %d has no 'path'" % (index + 1))
            continue
        if old_text is None:
            complaints.append("edit %d has no 'old_text'" % (index + 1))
            continue
        if new_text is None:
            complaints.append("edit %d has no 'new_text'" % (index + 1))
            continue

        edits.append(FileEdit(
            path=str(path).strip(),
            old_text=str(old_text),
            new_text=str(new_text),
            reason=str(entry.get("reason", "")),
        ))

    return edits, complaints


def files_from_reply(parsed: dict) -> list[str]:
    raw = parsed.get("read_files")
    if raw is None or not isinstance(raw, list):
        return []
    wanted = []
    for entry in raw:
        text = str(entry).strip()
        if text != "":
            wanted.append(text)
    return wanted


def thinking_from_reply(parsed: dict) -> str:
    return str(parsed.get("thinking", "")).strip()


def done_from_reply(parsed: dict) -> bool:
    return parsed.get("done") is True
