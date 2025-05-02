"""Reading whatever the model sends back."""

import json

from coding_agent.layer7_agent.step1_prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
    done_from_reply,
    edits_from_reply,
    files_from_reply,
    parse_reply,
    thinking_from_reply,
)


def test_the_prompt_says_the_tests_are_the_specification():
    assert "may NOT edit" in SYSTEM_PROMPT
    assert "specification" in SYSTEM_PROMPT
    assert "EXACTLY ONCE" in SYSTEM_PROMPT


def test_plain_json_is_read():
    parsed, error = parse_reply('{"thinking":"t","edits":[]}')
    assert error == ""
    assert thinking_from_reply(parsed) == "t"


def test_a_markdown_fence_is_stripped():
    parsed, error = parse_reply('```json\n{"thinking":"t","edits":[]}\n```')
    assert error == ""


def test_prose_around_the_json_is_survived():
    parsed, error = parse_reply('Sure!\n{"thinking":"t","edits":[]}\nHope that helps.')
    assert error == ""
    assert thinking_from_reply(parsed) == "t"


def test_a_reply_that_is_not_json_is_reported_not_crashed():
    parsed, error = parse_reply("I am not able to help with that.")
    assert parsed == {}
    assert "not valid JSON" in error


def test_source_code_inside_json_survives_the_round_trip():
    # A reply carrying an edit is mostly source inside a JSON string - quotes,
    # backslashes and newlines, which is exactly what breaks a model's JSON.
    original = '    if code == "HALFOFF":\n        return round_money(subtotal / 2.0)'
    payload = json.dumps({"thinking": "x", "edits": [
        {"path": "a.py", "old_text": original, "new_text": original + "  # note"}]})

    parsed, error = parse_reply(payload)
    edits, complaints = edits_from_reply(parsed)
    assert error == ""
    assert complaints == []
    assert edits[0].old_text == original


def test_an_edit_missing_a_field_is_complained_about_not_dropped_silently():
    parsed, _ = parse_reply('{"edits":[{"path":"a.py","old_text":"x"}]}')
    edits, complaints = edits_from_reply(parsed)
    assert edits == []
    assert "new_text" in complaints[0]


def test_read_files_and_done_are_read():
    parsed, _ = parse_reply('{"read_files":["a.py","b.py"],"edits":[],"done":true}')
    assert files_from_reply(parsed) == ["a.py", "b.py"]
    assert done_from_reply(parsed)


def test_history_is_included_when_there_is_any():
    without = build_user_prompt("issue", ["t::x"], "output", "context", [])
    assert "already tried" not in without

    with_history = build_user_prompt("issue", ["t::x"], "output", "context",
                                     ["tried something"])
    assert "already tried" in with_history
    assert "tried something" in with_history
