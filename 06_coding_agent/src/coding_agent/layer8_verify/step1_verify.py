"""
LAYER 8 - IS THIS PATCH ACCEPTABLE?
===================================
Five checks. All of them must pass. A green test suite on its own is not enough
and this file is the reason the project is worth building.

    1. the target test passes           it does the thing that was asked
    2. nothing that passed now fails    it did not buy that by breaking something
    3. the tests were not touched       it did not buy it by editing the spec
    4. something actually changed       a patch that changes nothing is not a fix
    5. no suspicious shortcuts          it fixed the cause, not the symptom

CHECK 3 IS THE ONE.

An agent that cannot work out why `total_with_tax(100) == 120` fails has an
obvious alternative: change the 120. The suite goes green, the exit code is zero,
and every metric built on "did the tests pass" reports success. Ask the model what
it did and it will tell you it corrected the expected value - which is a true
description of the edit and a false description of the work.

The only defence is to refuse to take the test suite's word for it. The test files
are hashed before the agent starts and again at the end, and if any hash moved the
patch is rejected no matter how green the run was. Layer 6 also refuses the edit
at the door; this checks the room afterwards and does not care how anything got in.

CHECK 5 is softer and honest about being softer. Wrapping a call in a bare
try/except, or branching on the exact value a test uses, makes the suite green
while making the software worse. These are recognised by pattern, patterns can be
evaded, and a determined cheat will get past them. It is a smoke detector, not a
lock, and it is labelled as one - a warning that is reported rather than an
absolute bar, so a legitimate try/except in a patch does not get thrown out.
"""

import re

from coding_agent.layer2_models.schemas import TestRun, Verdict, VerdictReason
from coding_agent.layer6_edit.step1_protect import is_protected

# Shapes that usually mean "make the symptom go away". Each is reported, not
# treated as proof - real code contains all of these for real reasons.
SUSPICIOUS_PATTERNS = [
    (re.compile(r"except\s*:"), "a bare 'except:' that swallows every error"),
    (re.compile(r"except\s+Exception\s*:\s*\n\s*(pass|return)\b"),
     "an 'except Exception' that silently passes or returns"),
    (re.compile(r"@pytest\.mark\.(skip|xfail)"), "a test being skipped or marked xfail"),
    (re.compile(r"pytest\.skip\("), "a call to pytest.skip"),
    (re.compile(r"#\s*(type|noqa|pragma:\s*no\s*cover)"), "a linter or coverage suppression"),
]


def check_target_tests(final_run: TestRun, target_tests: list[str]) -> VerdictReason:
    if len(target_tests) == 0:
        if final_run.is_green():
            return VerdictReason(code="target_tests_pass", passed=True,
                                 detail="no specific target, and the whole suite is green")
        return VerdictReason(code="target_tests_pass", passed=False,
                             detail="no specific target, and the suite is not green: %s"
                                    % final_run.summary())

    missing = []
    failing = []
    for wanted in target_tests:
        if wanted in final_run.failed or wanted in final_run.errors:
            failing.append(wanted)
        elif wanted not in final_run.passed:
            missing.append(wanted)

    if len(failing) > 0:
        return VerdictReason(code="target_tests_pass", passed=False,
                             detail="still failing: %s" % ", ".join(failing))
    if len(missing) > 0:
        # Not in passed, not in failed. The test stopped existing, which is
        # what happens when it gets renamed or deleted rather than fixed.
        return VerdictReason(
            code="target_tests_pass", passed=False,
            detail=("%s did not run at all. A target test that neither passes "
                    "nor fails has usually been removed or renamed."
                    % ", ".join(missing)))

    return VerdictReason(code="target_tests_pass", passed=True,
                         detail="%d of %d passing" % (len(target_tests), len(target_tests)))


def check_no_regressions(baseline_run: TestRun, final_run: TestRun) -> VerdictReason:
    """
    Every test that passed before must still pass.

    This is the check that separates a fix from a trade. It is easy to make one
    test green by changing behaviour something else relied on, and the agent has
    no reason to look - it was only ever shown one failure.
    """
    broken = []
    vanished = []

    for name in baseline_run.passed:
        if name in final_run.failed or name in final_run.errors:
            broken.append(name)
        elif name not in final_run.passed:
            vanished.append(name)

    if len(broken) > 0:
        return VerdictReason(
            code="no_regressions", passed=False,
            detail="%d test(s) passed before and fail now: %s"
                   % (len(broken), ", ".join(broken[:5])))

    if len(vanished) > 0:
        return VerdictReason(
            code="no_regressions", passed=False,
            detail="%d test(s) passed before and no longer run at all: %s"
                   % (len(vanished), ", ".join(vanished[:5])))

    return VerdictReason(code="no_regressions", passed=True,
                         detail="all %d previously passing test(s) still pass"
                                % len(baseline_run.passed))


def check_tests_unchanged(before_snapshot, after_snapshot,
                          protected_globs: list[str]) -> VerdictReason:
    modified, added, removed = after_snapshot.changed_against(before_snapshot)

    offences = []
    for path in modified:
        blocked, _ = is_protected(path, protected_globs)
        if blocked:
            offences.append("modified %s" % path)
    for path in added:
        blocked, _ = is_protected(path, protected_globs)
        if blocked:
            offences.append("added %s" % path)
    for path in removed:
        blocked, _ = is_protected(path, protected_globs)
        if blocked:
            offences.append("deleted %s" % path)

    if len(offences) > 0:
        return VerdictReason(
            code="tests_unchanged", passed=False,
            detail=("the specification was changed, which makes the green suite "
                    "meaningless: %s" % "; ".join(offences)))

    return VerdictReason(code="tests_unchanged", passed=True,
                         detail="no test or test-configuration file was touched")


def check_something_changed(before_snapshot, after_snapshot) -> VerdictReason:
    modified, added, removed = after_snapshot.changed_against(before_snapshot)
    total = len(modified) + len(added) + len(removed)

    if total == 0:
        return VerdictReason(
            code="something_changed", passed=False,
            detail=("no file was changed. If the suite is green, it was green "
                    "before the agent started, and nothing here was fixed."))

    return VerdictReason(code="something_changed", passed=True,
                         detail="%d file(s) changed" % total)


NUMBER_LITERAL_PATTERN = re.compile(r"\b\d+\.\d+\b")


def literals_copied_from_tests(new_text: str, old_text: str,
                               test_sources: list[str]) -> list[str]:
    """
    Numbers that the patch ADDED and that appear in the failing test.

    This is the fingerprint of a patch that hard-codes the answers:

        if subtotal == 100.0 and tax_rate == 20.0:
            return 120.0

    It passes every other check in this file. The target test goes green,
    nothing else breaks, no test file was touched, and a real source file
    genuinely changed. Only the content gives it away - the patch is quoting the
    test back at itself.

    It is a heuristic and it can be wrong in both directions. A legitimate fix
    can share a constant with its test (a VAT rate of 20.0, say), and a patient
    cheat can special-case on values the test computes rather than states. So
    this reports rather than rejects.
    """
    added_numbers = set()
    for match in NUMBER_LITERAL_PATTERN.finditer(new_text):
        if match.group(0) not in old_text:
            added_numbers.add(match.group(0))

    if len(added_numbers) == 0:
        return []

    joined_tests = "\n".join(test_sources)
    copied = []
    for number in sorted(added_numbers):
        if number in joined_tests:
            copied.append(number)
    return copied


def check_no_shortcuts(workspace, before_snapshot, after_snapshot,
                       before_contents: dict | None = None,
                       test_sources: list[str] | None = None) -> VerdictReason:
    """
    Look for the shapes of a symptom-suppressing patch. Reported, never a bar.

    This compares against the ACTUAL text the agent started from, which the
    caller supplies. The first version only had file hashes to work with, so it
    guessed from the size ratio whether a pattern was new - and a bare `except:`
    added to a file that grew by three percent fell inside the "probably was
    already there" window and was silently let through. A check that guesses at
    its own input is not a check.
    """
    if before_contents is None:
        before_contents = {}
    if test_sources is None:
        test_sources = []

    modified, added, _ = after_snapshot.changed_against(before_snapshot)

    touched = []
    for path in modified:
        touched.append(path)
    for path in added:
        touched.append(path)

    findings = []
    for path in touched:
        if not path.endswith(".py"):
            continue
        try:
            after_text = workspace.read(path)
        except Exception:
            continue
        old_text = before_contents.get(path, "")

        for pattern, description in SUSPICIOUS_PATTERNS:
            found_now = pattern.search(after_text) is not None
            found_before = pattern.search(old_text) is not None
            if found_now and not found_before:
                findings.append("%s now contains %s" % (path, description))

        copied = literals_copied_from_tests(after_text, old_text, test_sources)
        if len(copied) > 0:
            findings.append(
                "%s adds the literal value(s) %s, which also appear in the "
                "failing test - this is what hard-coding the expected answers "
                "looks like" % (path, ", ".join(copied)))

    if len(findings) > 0:
        return VerdictReason(
            code="no_shortcuts", passed=False,
            detail="needs a human look: %s" % "; ".join(findings))

    return VerdictReason(code="no_shortcuts", passed=True,
                         detail="no obvious symptom-suppressing patterns")


# Checks that make a patch unacceptable on their own. `no_shortcuts` is not
# among them: it is a heuristic, and heuristics should not silently reject work.
BLOCKING_CHECKS = ("target_tests_pass", "no_regressions", "tests_unchanged",
                   "something_changed")


def verify(workspace, baseline_run: TestRun, final_run: TestRun,
           before_snapshot, after_snapshot, target_tests: list[str],
           protected_globs: list[str],
           before_contents: dict | None = None,
           test_sources: list[str] | None = None) -> Verdict:
    verdict = Verdict()

    verdict.reasons.append(check_tests_unchanged(before_snapshot, after_snapshot, protected_globs))
    verdict.reasons.append(check_target_tests(final_run, target_tests))
    verdict.reasons.append(check_no_regressions(baseline_run, final_run))
    verdict.reasons.append(check_something_changed(before_snapshot, after_snapshot))
    verdict.reasons.append(check_no_shortcuts(workspace, before_snapshot, after_snapshot,
                                              before_contents, test_sources))

    accepted = True
    for reason in verdict.reasons:
        if reason.code in BLOCKING_CHECKS and not reason.passed:
            accepted = False
    verdict.accepted = accepted

    return verdict
