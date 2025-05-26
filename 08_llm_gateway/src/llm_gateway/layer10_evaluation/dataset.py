"""
LAYER 10 - SOMETHING CHECKABLE TO ASK
=====================================
Small arithmetic word problems, with an answer a function can verify.

This is the least glamorous file in the project and it is doing the most work.
An A/B platform is only as good as its success metric, and "which answer is
better" is usually a judgement - which means grading is slow, expensive,
inconsistent between graders, and impossible to run offline.

Arithmetic is none of those things. The answer is right or it is not, a grader
is four lines long, and the same question graded twice gives the same result. It
means the significance machinery can be demonstrated against a REAL success
rate, and it means the offline evaluation measures something rather than
pretending to.

The real lesson generalises past arithmetic: wherever a checkable metric exists,
use it, even if it is narrower than what you actually care about. A narrow metric
you can measure a thousand times beats a broad one you can measure nine times.

`{n}` is substituted with a varying number so a run of several hundred requests
does not ask the same question repeatedly - which the cache would answer for
free, making every arm look identical.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Question:
    """
    A prompt with a `{n}` in it, and a function that says what the answer is.

    A dataclass rather than a dict because a dict of mixed values types out as
    `object`, and then nothing can be said about either field - including by the
    type checker, which is how a typo in one of them would reach production.
    """

    prompt: str
    answer: Callable[[int], int]


QUESTIONS = [
    Question(
        prompt="A box holds {n} pencils. Each pencil costs 3. "
                 "How much does the whole box cost altogether?",
        answer=lambda n: n * 3,
    ),
    Question(
        prompt="A shelf has {n} books. Each book weighs 2. "
                 "What is the total weight altogether?",
        answer=lambda n: n * 2,
    ),
    Question(
        prompt="A jar had {n} sweets. Someone takes 4 away. "
                 "How many are left?",
        answer=lambda n: n - 4,
    ),
    Question(
        prompt="A team scored {n} points, then scored 7 more. "
                 "What is the total?",
        answer=lambda n: n + 7,
    ),
    Question(
        # This one used to read "Two bottles are returned", with the answer
        # n * 5 - 2. That is wrong twice over: returning two bottles takes 2 x 5
        # off the bill, not 2 - and the offline solver looked for the word
        # "returns", never matched "are returned", and answered n * 5. So the
        # dataset said 98, the solver said 100, the true answer was 90, and this
        # question was graded WRONG on every single request. A fifth of the
        # benchmark scored zero for every model and every prompt, which quietly
        # capped every arm at 80% and shrank an 18-point effect to about 8.
        # Nothing failed. The rates just came out low, which looks like a
        # finding rather than a bug. Subtracting from the total says what it
        # means, and the solver understands "discount".
        prompt="A crate holds {n} bottles. Each bottle costs 5. "
                 "A discount takes 2 off the total. How much altogether?",
        answer=lambda n: n * 5 - 2,
    ),
]

NUMBER_PATTERN = re.compile(r"-?\d+")


def grade(answer_text: str, expected: int) -> float:
    """
    1.0 if the expected number appears in the answer, 0.0 if not.

    Deliberately lenient about FORM and strict about VALUE. The model may say
    "The answer is 36" or show its working first; either is fine. What it may
    not do is produce a different number. Grading the wording as well would
    measure two things at once and tell you which only by accident.
    """
    for match in NUMBER_PATTERN.finditer(answer_text):
        if int(match.group(0)) == expected:
            return 1.0
    return 0.0


def question_at(index: int, varying: int) -> tuple[str, int]:
    question = QUESTIONS[index % len(QUESTIONS)]
    return (question.prompt.replace("{n}", str(varying)), question.answer(varying))
