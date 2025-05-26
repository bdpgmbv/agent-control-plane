"""
Measure the semantic cache threshold instead of guessing it.

    python scripts/tune_threshold.py            the offline hashing embedder
    python scripts/tune_threshold.py --live     real embeddings, costs a little

SEMANTIC_THRESHOLD decides when two questions count as the same question. Too
high and the cache never hits, which costs money. Too low and the gateway
confidently answers a question nobody asked, which costs trust and is much
harder to notice - the answer is fluent, plausible, and about something else.

So this runs pairs that SHOULD match and pairs that should NOT, and reports
where the two groups separate. If they overlap, it says so rather than picking
a number from the middle of the overlap and calling it tuned. A threshold that
cannot separate the two groups means the embedder is not good enough for this
job, and no setting fixes that.
"""

import sys

from llm_gateway.layer1_config.settings import SETTINGS
from llm_gateway.layer4_providers.step2_base import EmbeddingUnavailable
from llm_gateway.layer4_providers.step3_offline import cosine_similarity, hashing_embedding

# Pairs that mean the same thing. A hit here saves money.
SAME = [
    ("What is the refund window for online orders?",
     "For online orders, what is the refund window?"),
    ("How long do I have to return something?",
     "How long have I got to return something?"),
    ("What is the delivery charge to Scotland?",
     "What is the delivery charge for Scotland?"),
    ("Can I change my delivery address after ordering?",
     "After ordering, can I change my delivery address?"),
    ("How do I reset my password?",
     "How do I go about resetting my password?"),
]

# Pairs that do NOT. A hit here is the gateway answering the wrong question.
DIFFERENT = [
    ("What is the refund window for online orders?",
     "What is the delivery charge to Scotland?"),
    ("How do I reset my password?",
     "How do I change my email address?"),
    ("Can I change my delivery address after ordering?",
     "Can I cancel my order after it has shipped?"),
    ("What is the refund window for online orders?",
     "What is the refund window for in-store purchases?"),
    ("How long do I have to return something?",
     "How long does delivery take?"),
]


def build_embedder(live: bool):
    if not live:
        return hashing_embedding

    from llm_gateway.layer4_providers.step4_openai import OpenAIProvider

    provider = OpenAIProvider(SETTINGS.openai_api_key, SETTINGS.openai_timeout_seconds)

    def embed(text: str) -> list[float]:
        return provider.embed(text)

    return embed


def main() -> int:
    live = "--live" in sys.argv
    if live and SETTINGS.is_offline():
        print("--live was asked for, but there is no OPENAI_API_KEY in .env.")
        return 2

    embedder = build_embedder(live)

    # Which threshold this run is actually about. It has to follow `live`, NOT
    # SETTINGS.semantic_threshold(), which follows whether a key happens to be
    # in .env. Once a key was pasted in, this script measured the OFFLINE
    # embedder and compared the result against the LIVE threshold, then
    # announced that a correctly-set 0.710 was "OUTSIDE that range" and exited
    # non-zero. The measurement was right and the thing it was held against was
    # the wrong number, which is the same mistake as the threshold it exists to
    # prevent.
    if live:
        label = "real embeddings"
        variable = "SEMANTIC_THRESHOLD"
        configured = SETTINGS.semantic_threshold_live
    else:
        label = "the offline hashing embedder"
        variable = "SEMANTIC_THRESHOLD_OFFLINE"
        configured = SETTINGS.semantic_threshold_offline

    print("=" * 78)
    print("  SEMANTIC THRESHOLD, measured against %s" % label)
    print("=" * 78)
    print()

    same_scores = []
    print("  SHOULD match (a hit here saves money)")
    for first, second in SAME:
        # A measurement made with a different embedder than the one named at the
        # top of this report is worse than no measurement, because the number it
        # prints will be pasted into .env. So stop here and say why.
        try:
            score = cosine_similarity(embedder(first), embedder(second))
        except EmbeddingUnavailable as error:
            print()
            print("  Real embeddings are unavailable, so there is nothing to")
            print("  measure. The reason from the API:")
            print()
            print("    %s" % str(error)[:200])
            print()
            print("  No threshold is suggested. A number measured with the")
            print("  offline embedder would be wrong for the live one.")
            return 3
        same_scores.append(score)
        print("     %.4f  %s" % (score, first[:52]))

    different_scores = []
    print()
    print("  should NOT match (a hit here answers the wrong question)")
    for first, second in DIFFERENT:
        score = cosine_similarity(embedder(first), embedder(second))
        different_scores.append(score)
        print("     %.4f  %s" % (score, first[:52]))

    lowest_same = min(same_scores)
    highest_different = max(different_scores)

    print()
    print("  " + "-" * 74)
    print("  lowest score among pairs that SHOULD match:    %.4f" % lowest_same)
    print("  highest score among pairs that should NOT:     %.4f" % highest_different)
    print()

    if lowest_same > highest_different:
        suggested = round((lowest_same + highest_different) / 2.0, 3)
        print("  The two groups separate cleanly, with a gap of %.4f."
              % (lowest_same - highest_different))
        print("  Anything in (%.4f, %.4f) works. The midpoint is %.3f."
              % (highest_different, lowest_same, suggested))
        print()
        print("  %s=%.3f" % (variable, suggested))
        print()
        if highest_different < configured < lowest_same:
            print("  Currently set to %.3f, which is inside that range." % configured)
            return 0

        # Exits non-zero, because this is the failure that hides. A threshold
        # set too high turns the semantic cache OFF while looking configured:
        # nothing errors, every lookup simply misses, and the only symptom is a
        # bill nobody can account for. This project shipped 0.93 against an
        # embedder whose genuine matches scored 0.71.
        print("  Currently set to %.3f, which is OUTSIDE that range - so the"
              % configured)
        if configured >= lowest_same:
            print("  semantic cache is effectively OFF: real matches score below it.")
        else:
            print("  cache will serve answers to questions that do not match.")
        return 1

    print("  NO CLEAN GAP. The two groups overlap: some pairs that should not")
    print("  match score higher than some pairs that should. There is no")
    print("  threshold that gets both right, and picking one from the middle")
    print("  of the overlap would be choosing which kind of error to make")
    print("  while appearing to have measured something.")
    print()
    print("  Either the embedder is not good enough for this job, or the test")
    print("  pairs are badly chosen. Both are worth knowing; neither is fixed")
    print("  by a setting.")
    print()
    print("  For safety, a threshold above %.4f makes no wrong matches and"
          % highest_different)
    given_up = 0
    for score in same_scores:
        if score <= highest_different:
            given_up = given_up + 1
    print("  gives up %d of %d right ones." % (given_up, len(same_scores)))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
