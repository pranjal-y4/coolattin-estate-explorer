from __future__ import annotations

from typing import Any

ANALYTICAL = "analytical"
RELATIONAL = "relational"
COMPARATIVE = "comparative"
FALLBACK = "fallback"

_COMPARATIVE_KEYWORDS = frozenset({
    "compare", "compared to", "compared with", "versus", " vs ", "vs.",
    "difference between", "contrast", "relative to", "how does", "how did",
    "better than", "worse than", "more than", "less than",
    "higher than", "lower than", "against",
})

_RELATIONAL_KEYWORDS = frozenset({
    "related to", "connected to", "connection between", "link between",
    "in the same parish", "same parish", "same barony",
    "part of", "belong to", "belongs to", "neighbouring", "neighboring",
    "adjacent to", "bordering", "relationship between", "linked to",
})

_HIERARCHY_KEYWORDS = frozenset({
    "which parish", "what parish", "civil parish", "in the parish",
    "in the barony", "which barony", "what barony",
    "in the county", "which county", "what county",
    "townlands in", "where is", "where does",
    "located in", "situated in", "falls within",
})

_HERITAGE_KEYWORDS = frozenset({
    "heritage", "archaeological", "monument", "ring fort", "holy well",
    "history of", "tell me about", "describe", "what is the history",
    "historically", "historic", "fortification", "earthwork",
})

_SENSEMAKING_KEYWORDS = frozenset({
    "overview", "about the estate", "about coolattin", "what was",
    "describe the estate", "coolattin estate", "what kind of",
    "background", "summary of", "general context",
})

_ANALYTICAL_KEYWORDS = frozenset({
    "how many", "how much", "total", "count of", "number of", "average", "mean",
    "proportion", "percent", "percentage", "per year", "by year", "trend",
    "over time", "distribution", "breakdown", "most", "least", "highest",
    "lowest", "maximum", "minimum", "sum of", "rate", "ratio",
})

_ANALYTICAL_INTENTS = frozenset({"population", "eviction", "emigration", "tenancy", "people"})

_STRONG_RELATIONAL = _RELATIONAL_KEYWORDS | _HIERARCHY_KEYWORDS


def classify_intent(
    question: str,
    analysis: dict[str, Any],
    slot_fill: Any | None,
) -> str:
    q = (question or "").lower()
    primary_intent = analysis.get("primary_intent", "")
    output_mode = analysis.get("output_mode", "")

    if any(kw in q for kw in _COMPARATIVE_KEYWORDS):
        return COMPARATIVE

    has_relational = any(kw in q for kw in _RELATIONAL_KEYWORDS)
    has_hierarchy = any(kw in q for kw in _HIERARCHY_KEYWORDS)
    has_heritage = any(kw in q for kw in _HERITAGE_KEYWORDS)
    has_sensemaking = any(kw in q for kw in _SENSEMAKING_KEYWORDS)
    geography_intent = primary_intent == "geography"

    if geography_intent or has_relational or has_hierarchy or has_heritage or has_sensemaking:
        if (has_heritage or has_sensemaking) and not (has_relational or has_hierarchy or geography_intent):
            pure_count = (
                output_mode in {"count", "aggregate"}
                and any(kw in q for kw in _ANALYTICAL_KEYWORDS)
            )
            if pure_count:
                return ANALYTICAL

            _has_person = bool(
                analysis.get("surname")
                or analysis.get("forename")
                or analysis.get("canonical_name")
            )
            if _has_person:
                return FALLBACK

        return RELATIONAL

    analytical = (
        primary_intent in _ANALYTICAL_INTENTS
        or output_mode in {"count", "aggregate", "trend", "list", "grouped"}
        or any(kw in q for kw in _ANALYTICAL_KEYWORDS)
        or slot_fill is not None
    )
    if analytical:
        return ANALYTICAL

    return FALLBACK
