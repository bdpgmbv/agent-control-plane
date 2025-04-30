"""
LAYER 6, STEP 1 - FILES THE AGENT MAY NOT EDIT
==============================================
The test files.

This is the single most important rule in the project, and it exists because of
how an agent behaves when it cannot solve the problem. It does not stop. It looks
for another way to make the signal go green, and the assertion is right there.

    def test_tax_is_added_not_subtracted():
    -    assert total_with_tax(100.0) == 120.0
    +    assert total_with_tax(100.0) == 80.0

That is a patch that makes the suite pass, and a model asked to describe it will
say it corrected the expected value. Nothing about the sentence is a lie from
where it is standing. The tests are the specification, and an agent allowed to
edit the specification is an agent that cannot fail.

So the rule is enforced twice, on purpose:

  here, in layer 6    the edit is refused before it is written
  in layer 8          the test files are hashed before and after, and a patch
                      that moved them is rejected even if it was applied

Two enforcement points for one rule looks redundant until you ask what happens if
somebody adds a second way to write files. Layer 6 guards the door; layer 8 checks
the room afterwards and does not care how anything got in.
"""

import fnmatch
from pathlib import PurePosixPath


def normalise(path: str) -> str:
    return str(path).replace("\\", "/").strip().lstrip("./")


def is_protected(path: str, patterns: list[str]) -> tuple[bool, str]:
    """
    Returns (protected, why).

    Each pattern is tried three ways, because "tests/*" is written by people who
    mean "anything under a tests directory" and fnmatch does not agree - it will
    not match "shopkit/tests/test_money.py", which is exactly the file they meant.
    Matching the pattern against the full path, the basename, and every directory
    component covers how the patterns are actually written.
    """
    cleaned = normalise(path)
    if cleaned == "":
        return False, ""

    parts = PurePosixPath(cleaned).parts
    basename = parts[-1] if len(parts) > 0 else cleaned

    for pattern in patterns:
        pattern = pattern.strip()
        if pattern == "":
            continue

        if fnmatch.fnmatch(cleaned, pattern):
            return True, "matches the protected pattern %r" % pattern

        if fnmatch.fnmatch(basename, pattern):
            return True, "the filename matches the protected pattern %r" % pattern

        # "tests/*" meaning "anything inside a directory called tests".
        directory_part = pattern.split("/")[0]
        if directory_part != "" and "*" not in directory_part:
            for part in parts[:-1]:
                if part == directory_part:
                    return True, ("it is inside a %r directory, which is protected"
                                  % directory_part)

    return False, ""


def protected_paths(paths: list[str], patterns: list[str]) -> list[str]:
    found = []
    for path in paths:
        blocked, _ = is_protected(path, patterns)
        if blocked:
            found.append(path)
    return found
