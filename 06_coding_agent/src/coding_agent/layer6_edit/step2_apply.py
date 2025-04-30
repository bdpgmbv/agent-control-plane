"""
LAYER 6, STEP 2 - APPLYING AN EDIT
==================================
An edit is an exact old string replaced by an exact new string in one named file.

Five rules, each of which exists because of a specific way this goes wrong.

1. THE OLD STRING MUST BE FOUND.
   If it is not there, the model is describing a file it has imagined. Fail, and
   say so, rather than doing nothing and reporting success.

2. IT MUST APPEAR EXACTLY ONCE.
   `return 0.0` appears four times in pricing.py. Replacing "the first one" is a
   coin flip that the agent has no way to know it lost, and the test that then
   fails will point at a line the agent never intended to touch. An ambiguous
   edit is refused with the count, so the model can quote more surrounding lines
   and try again - which is the correct repair and one it can actually make.

3. THE FILE MUST NOT BE PROTECTED.
   See step 1.

4. THE RESULT MUST STILL PARSE.
   Checked in memory before anything is written. A patch that leaves the module
   unimportable makes every test in the suite fail at collection, which buries
   the real failure under thirty unrelated ones and costs an iteration to work
   out. Catching it here turns that into one clear message.

5. ALL OR NOTHING.
   A batch is applied to in-memory copies first. If any edit in the batch fails,
   nothing is written. Half-applied batches leave source in a state neither the
   agent nor the tests can explain, and the agent then spends its next iteration
   debugging damage the harness caused.

Rule 5 is also what makes several edits to the SAME file work: each one is
applied to the evolving in-memory content, so a second edit can depend on the
first having happened.
"""

import ast

from coding_agent.layer2_models.schemas import EditResult, EditStatus, FileEdit
from coding_agent.layer3_workspace.step1_sandbox import FileTooLarge, PathOutsideWorkspace
from coding_agent.layer6_edit.step1_protect import is_protected


def check_syntax(path: str, source: str) -> str:
    """Returns an error description, or "" if it parses."""
    if not path.endswith(".py"):
        return ""
    try:
        ast.parse(source)
    except SyntaxError as error:
        return "line %s: %s" % (error.lineno, error.msg)
    return ""


# How many lines of context to show around each ambiguous match.
AMBIGUITY_CONTEXT_LINES = 3


def describe_ambiguity(path: str, source: str, old_text: str, occurrences: int) -> str:
    """
    Show the agent WHERE the matches are, not just that there are several.

    The first version of this message said "include more of the surrounding
    lines to make it unique" and stopped there. That reads like helpful advice
    and is nearly useless: the agent cannot see the file's line numbers, so it
    has no way to know which surrounding lines to add, and it does the only
    thing left - quotes the same text again with slightly different whitespace.

    Watching a live run, the model diagnosed the bug correctly on its first
    attempt and then spent its entire budget failing to express the fix, because
    every refusal told it what was wrong and nothing about how to be right. Six
    iterations, twenty-one thousand tokens, and not one edit applied.

    An error message in an agent loop is not a log line. It is the only channel
    through which the agent learns anything, so it has to carry enough to act on.
    """
    lines = source.split("\n")

    # Find the line each occurrence starts on.
    positions = []
    search_from = 0
    while True:
        found_at = source.find(old_text, search_from)
        if found_at < 0:
            break
        positions.append(source.count("\n", 0, found_at) + 1)
        search_from = found_at + 1

    pieces = []
    pieces.append(
        "that text appears %d times in %s, so there is no way to know which one "
        "you mean. Below is each one, together with a block of text that IS "
        "unique. Copy one of those blocks into old_text exactly as shown."
        % (occurrences, path)
    )

    for number in positions:
        start = number - AMBIGUITY_CONTEXT_LINES
        if start < 1:
            start = 1
        end = number + AMBIGUITY_CONTEXT_LINES
        if end > len(lines):
            end = len(lines)

        pieces.append("")
        pieces.append("  --- around line %d ---" % number)
        for index in range(start, end + 1):
            marker = ">>" if index == number else "  "
            pieces.append("  %s %4d | %s" % (marker, index, lines[index - 1]))

        unique_block = smallest_unique_block(lines, number, source)
        if unique_block is not None:
            pieces.append("")
            pieces.append("  a unique old_text for THIS one:")
            for block_line in unique_block.split("\n"):
                pieces.append("      " + block_line)

    return "\n".join(pieces)


def smallest_unique_block(lines: list[str], line_number: int, source: str,
                          maximum_lines: int = 12) -> str | None:
    """
    Grow a window around a line until the text it covers appears only once.

    This is the harness doing work the agent kept failing to do. Watching live
    runs, the model diagnosed the bug immediately and then could not express the
    fix: it needed a quotation unique in the file, and it went on requoting the
    same duplicated line until the budget ran out, even once every match was
    shown to it with line numbers.

    Constructing that quotation is not a judgement call. It is a search over
    windows for the smallest one that occurs exactly once, which is arithmetic,
    and arithmetic belongs in the harness. Leaving it to the model was asking
    the component that is bad at exactness to be exact.

    Returns None if no window up to `maximum_lines` is unique, which happens in
    genuinely repetitive files and is worth saying rather than guessing at.
    """
    for span in range(1, maximum_lines + 1):
        for start in range(line_number - span + 1, line_number + 1):
            if start < 1:
                continue
            end = start + span - 1
            if end > len(lines):
                continue

            block = "\n".join(lines[start - 1:end])
            if block.strip() == "":
                continue
            if source.count(block) == 1:
                return block

    return None


def validate_one(edit: FileEdit, current_source: str) -> tuple[EditStatus, str, int, str]:
    """
    Check an edit against the content it would be applied to.

    Returns (status, message, occurrences, new_source).
    """
    if edit.old_text == edit.new_text:
        return (EditStatus.INVALID,
                "the old and new text are identical, so this edit does nothing",
                0, current_source)

    if edit.old_text == "":
        return (EditStatus.INVALID,
                "the old text is empty. An edit must quote the exact text to "
                "replace, not an empty string",
                0, current_source)

    occurrences = current_source.count(edit.old_text)

    if occurrences == 0:
        return (EditStatus.NOT_FOUND,
                "that exact text is not in %s. Check the indentation and quote "
                "it exactly as it appears in the file" % edit.path,
                0, current_source)

    if occurrences > 1:
        return (EditStatus.AMBIGUOUS,
                describe_ambiguity(edit.path, current_source, edit.old_text, occurrences),
                occurrences, current_source)

    new_source = current_source.replace(edit.old_text, edit.new_text, 1)

    syntax_error = check_syntax(edit.path, new_source)
    if syntax_error != "":
        return (EditStatus.SYNTAX_ERROR,
                "applying this would leave %s unparseable (%s)"
                % (edit.path, syntax_error),
                1, current_source)

    return (EditStatus.APPLIED, "", 1, new_source)


def apply_edits(workspace, edits: list[FileEdit], protected_globs: list[str],
                max_file_bytes: int = 400000) -> tuple[list[EditResult], bool]:
    """
    Apply a batch. Returns (results, everything_applied).

    Nothing reaches the disk unless every edit in the batch is valid.
    """
    results: list[EditResult] = []
    working: dict = {}          # path -> the evolving content
    all_valid = True

    for edit in edits:
        # ---- the path itself ----
        blocked, why = is_protected(edit.path, protected_globs)
        if blocked:
            results.append(EditResult(
                edit=edit, status=EditStatus.PROTECTED,
                message=("%s may not be edited: %s. The tests are the "
                         "specification for this task - fix the source so the "
                         "existing tests pass." % (edit.path, why)),
            ))
            all_valid = False
            continue

        try:
            workspace.resolve(edit.path)
        except PathOutsideWorkspace as error:
            results.append(EditResult(
                edit=edit, status=EditStatus.OUTSIDE_WORKSPACE,
                message=str(error),
            ))
            all_valid = False
            continue

        # ---- the content ----
        if edit.path in working:
            current = working[edit.path]
        else:
            if not workspace.exists(edit.path):
                results.append(EditResult(
                    edit=edit, status=EditStatus.NOT_FOUND,
                    message="there is no file at %s" % edit.path,
                ))
                all_valid = False
                continue
            try:
                current = workspace.read(edit.path, max_file_bytes)
            except FileTooLarge as error:
                results.append(EditResult(
                    edit=edit, status=EditStatus.TOO_LARGE, message=str(error),
                ))
                all_valid = False
                continue

        status, message, occurrences, updated = validate_one(edit, current)
        results.append(EditResult(
            edit=edit, status=status, message=message, occurrences=occurrences,
        ))

        if status != EditStatus.APPLIED:
            all_valid = False
            continue

        working[edit.path] = updated

    if not all_valid:
        # Nothing is written. Every result already says why.
        return results, False

    for path, content in working.items():
        workspace.write(path, content, max_file_bytes)

    return results, True


def describe_results(results: list[EditResult]) -> str:
    """What the agent is told about its own edits."""
    lines = []
    for result in results:
        if result.ok():
            lines.append("applied: %s" % result.edit.path)
        else:
            lines.append("REFUSED (%s): %s" % (result.status.value, result.message))
    return "\n".join(lines)
