"""
LAYER 0 - SHARED: VOCABULARY KNOWLEDGE
======================================
Two lists that every serious search system has, and that tutorials always skip.

1. QUESTION_FRAMING_WORDS
   Words that describe the ACT of asking rather than the subject being asked
   about: "ask", "tell", "explain", "happens", "quickly".

   Why they cause real damage: they are rare inside documents, so a
   rarity-based weighting decides they are highly informative - the exact
   opposite of the truth. The question "How long do I have to ASK for a refund?"
   was being refused because no policy document contains the word "ask".

2. SYNONYM_GROUPS
   Everyday phrasing on one side, document phrasing on the other. A person says
   "money back"; the policy says "refund". A person says "my parcel never turned
   up"; the policy says "lost parcel ... delivery date".

   Used twice, for two different jobs:
     - query rewriting adds the document's words to the search (layer 5, step 1)
     - relevance scoring treats the two as equivalent (layer 5, term_weights)

WHERE THESE COME FROM IN A REAL SYSTEM
   Not imagination. You read the questions people actually asked, find the ones
   that returned nothing, and add the missing bridge. Every entry below exists
   because a specific question in the evaluation set failed without it, and the
   evaluation report is what proved the entry helped.

   And note the honest limit: a synonym list can never cover a language. That is
   what embeddings are for. This list exists so the system still works usefully
   with no API key - see the README's note on offline mode.
"""

# Words about the act of asking, not about the subject.
QUESTION_FRAMING_WORDS = {
    "ask", "asked", "asking", "asks",
    "tell", "telling", "tells", "told",
    "explain", "explains", "describe", "describes",
    "happen", "happened", "happening", "happens",
    "mean", "meaning", "means",
    "quickly", "quick", "soon", "fast",
    "never", "always", "ever",
    "anyone", "anybody", "someone", "somebody", "everyone",
    "possible", "allowed", "supposed",
    "regarding", "concerning", "wondering",
    "company", "companies",
}

# (everyday words, the words a document is likely to use instead)
SYNONYM_GROUPS = [
    (["money", "cash", "refund", "refunded", "reimbursed"], ["refund", "reimbursement", "credit"]),
    (["back", "return", "returned", "returning"], ["refund", "return"]),
    (["ship", "shipping", "shipped", "post", "posted", "arrive", "arrived", "arrival"],
     ["delivery", "delivered", "shipping", "dispatch"]),
    (["turn", "turned", "turns", "showed", "show", "shows", "appear", "appears"],
     ["delivery", "delivered", "appear", "arrive"]),
    (["lost", "missing", "disappeared"], ["lost", "parcel", "delivered"]),
    (["cost", "costs", "price", "priced", "charge", "charged", "fee", "fees", "expensive"],
     ["fee", "charge", "price", "cost", "dollars"]),
    (["delete", "deleted", "deletion", "erase", "erased", "purge", "purged"],
     ["retention", "retained", "purge", "delete"]),
    (["kept", "keep", "keeps", "store", "stored", "storage", "retain", "retained"],
     ["retention", "retained", "stored"]),
    (["safe", "safely", "secure", "secured", "security", "protected", "protection"],
     ["encryption", "encrypted", "security", "access"]),
    # CAREFUL. "pay" and "earn" point in opposite directions: a customer PAYS a
    # fee, an employee EARNS a salary. Grouping them made the question "what does
    # a level three engineer earn?" match "orders below 50 dollars pay a flat
    # fee". Synonyms must preserve direction, not just topic.
    (["salary", "salaries", "wage", "wages", "earn", "earns", "earning", "earned"],
     ["salary", "compensation", "band", "earns"]),
    (["pay", "paid", "pays", "payment", "payable"], ["fee", "charge", "cost", "payment"]),
    (["told", "notified", "informed", "warned"], ["notified", "notification"]),
    (["breach", "breached", "hack", "hacked", "leak", "leaked"], ["breach", "incident"]),
]


def build_synonym_lookup() -> dict[str, set[str]]:
    """
    Turn the groups above into a lookup: one word -> every word it may match.

    Built once at import. Every word in a group is treated as equivalent to every
    other word in that group, in both directions, because the person and the
    document can each use either side.
    """
    from rag_assistant.layer0_shared.text_tools import stem_word

    lookup: dict[str, set[str]] = {}

    for everyday_words, document_words in SYNONYM_GROUPS:
        members: set[str] = set()
        for word in everyday_words:
            members.add(stem_word(word))
        for word in document_words:
            members.add(stem_word(word))

        for member in members:
            if member not in lookup:
                lookup[member] = set()
            for other in members:
                lookup[member].add(other)

    return lookup


SYNONYM_LOOKUP = build_synonym_lookup()


def equivalent_words(stem: str) -> set[str]:
    """Every word this one may be considered a match for, including itself."""
    if stem in SYNONYM_LOOKUP:
        return SYNONYM_LOOKUP[stem]
    return {stem}
