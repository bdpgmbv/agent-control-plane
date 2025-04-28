"""
LAYER 4, STEP 2 - WHICH FILES MATTER
====================================
Score every file against the issue text and the failing tests, and hand the agent
the few that look relevant.

One signal does most of the work and costs nothing: `tests/test_pricing.py`
almost certainly exercises `pricing.py`. Stripping the `test_` prefix off a
failing test file and looking for a module with that name is a single string
operation, and on this benchmark it puts the right file first every time.

That is worth noticing. The expensive, general approach is to embed the issue and
every file and compare vectors. The cheap, specific approach is a naming
convention the whole Python world already follows. Both find the file; only one
of them needs a model.

The other signals are ordinary term matching, and they exist to catch the cases
where the convention does not apply.
"""

import re
from dataclasses import dataclass, field

WORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Words that appear in every issue and tell you nothing about which file to open.
UNINFORMATIVE = {
    "the", "and", "for", "that", "this", "with", "from", "not", "but", "are",
    "was", "were", "has", "have", "had", "been", "being", "should", "would",
    "could", "when", "then", "than", "there", "their", "they", "which", "what",
    "test", "tests", "testing", "fails", "failing", "failed", "fail", "error",
    "bug", "issue", "fix", "fixed", "broken", "expected", "actual", "assert",
    "python", "code", "file", "files", "function", "method", "class", "returns",
    "return", "value", "values", "def", "self", "none", "true", "false",
}

# How much each kind of evidence is worth. The naming convention dominates on
# purpose - it is the strongest and cheapest signal available.
SCORE_TEST_MODULE_MATCH = 12.0
SCORE_HINT_PATH = 10.0
SCORE_NAME_IN_PATH = 4.0
SCORE_DEFINITION_MATCH = 3.0
SCORE_CONTENT_MATCH = 1.0
SCORE_PARSE_ERROR = 8.0

# A test file is never the place to fix a bug in this project, and offering it to
# the agent is an invitation to edit it.
PENALTY_TEST_FILE = -50.0


@dataclass
class RankedFile:
    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def meaningful_words(text: str) -> list[str]:
    found = []
    seen = set()
    for match in WORD_PATTERN.finditer(str(text).lower()):
        word = match.group(0)
        if word in UNINFORMATIVE or word in seen:
            continue
        seen.add(word)
        found.append(word)
    return found


def looks_like_a_test_file(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    if "/tests/" in lowered or lowered.startswith("tests/"):
        return True
    name = lowered.split("/")[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def module_names_from_tests(target_tests: list[str]) -> list[str]:
    """
    "tests/test_pricing.py::test_tax_is_added" -> "pricing"

    The convention is not a guess. It is how essentially every Python project
    names its tests, and leaning on it turns file selection from a search
    problem into a string operation.
    """
    names = []
    for entry in target_tests:
        path_part = str(entry).split("::")[0]
        filename = path_part.replace("\\", "/").split("/")[-1]
        if not filename.endswith(".py"):
            continue
        stem = filename[:-3]
        if stem.startswith("test_"):
            stem = stem[5:]
        elif stem.endswith("_test"):
            stem = stem[:-5]
        if stem != "" and stem not in names:
            names.append(stem)
    return names


def rank_files(outlines, issue_text: str, target_tests: list[str] | None = None,
               hint_paths: list[str] | None = None,
               file_contents: dict | None = None) -> list[RankedFile]:
    if target_tests is None:
        target_tests = []
    if hint_paths is None:
        hint_paths = []
    if file_contents is None:
        file_contents = {}

    issue_words = meaningful_words(issue_text)
    wanted_modules = module_names_from_tests(target_tests)

    ranked = []
    for outline in outlines:
        entry = RankedFile(path=outline.path)

        if looks_like_a_test_file(outline.path):
            entry.score = entry.score + PENALTY_TEST_FILE
            entry.reasons.append("test file, never the place to fix a bug here")
            ranked.append(entry)
            continue

        stem = outline.path.replace("\\", "/").split("/")[-1]
        if stem.endswith(".py"):
            stem = stem[:-3]

        for module in wanted_modules:
            if stem == module:
                entry.score = entry.score + SCORE_TEST_MODULE_MATCH
                entry.reasons.append("named by the failing test file (test_%s.py)" % module)

        for hint in hint_paths:
            if hint == outline.path:
                entry.score = entry.score + SCORE_HINT_PATH
                entry.reasons.append("named in the issue")

        lowered_path = outline.path.lower()
        for word in issue_words:
            if word in lowered_path:
                entry.score = entry.score + SCORE_NAME_IN_PATH
                entry.reasons.append("'%s' appears in the path" % word)

        lowered_names = []
        for name in outline.names():
            lowered_names.append(name.lower())
        joined_names = " ".join(lowered_names)
        for word in issue_words:
            if word in joined_names:
                entry.score = entry.score + SCORE_DEFINITION_MATCH
                entry.reasons.append("defines something called '%s'" % word)

        content = file_contents.get(outline.path, "")
        if content != "":
            lowered_content = content.lower()
            hits = 0
            for word in issue_words:
                if word in lowered_content:
                    hits = hits + 1
            if hits > 0:
                entry.score = entry.score + SCORE_CONTENT_MATCH * hits
                entry.reasons.append("mentions %d word(s) from the issue" % hits)

        if outline.parse_error != "":
            entry.score = entry.score + SCORE_PARSE_ERROR
            entry.reasons.append("does not parse")

        ranked.append(entry)

    def sort_key(entry):
        return (-entry.score, entry.path)

    ranked.sort(key=sort_key)
    return ranked


# A file only earns a place if it scores near the best one. Taking everything
# with a positive score put four files in the prompt when one mattered: the
# other three scored 1.0 or 2.0 against a top score of 26.0, purely for sharing
# an ordinary English word with the issue. That is not relevance, and it cost
# 60% of the context window to say so.
MINIMUM_SCORE_FRACTION = 0.15

# ... and a floor, for the case where nothing scores well. When the best file
# only manages 4.0, fifteen percent of it would admit almost anything.
MINIMUM_ABSOLUTE_SCORE = 2.5


def choose_files(ranked: list[RankedFile], limit: int) -> list[RankedFile]:
    """
    The files worth putting in the prompt.

    Two cutoffs, and the relative one is the useful one: a file scoring 1.0 when
    the best scores 26.0 shares a word, it does not share a subject.
    """
    if len(ranked) == 0:
        return []

    top_score = ranked[0].score
    cutoff = top_score * MINIMUM_SCORE_FRACTION
    if cutoff < MINIMUM_ABSOLUTE_SCORE:
        cutoff = MINIMUM_ABSOLUTE_SCORE

    chosen = []
    for entry in ranked:
        if entry.score < cutoff:
            continue
        chosen.append(entry)
        if len(chosen) >= limit:
            break
    return chosen
