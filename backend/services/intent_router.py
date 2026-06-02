"""
backend/services/intent_router.py

Phase 5 — Lightweight intent router.

Classifies each question into one of four route labels:
  ANALYTICAL   — aggregate / count / trend questions       → Phase 2 (semantic layer)
  RELATIONAL   — multi-hop / hierarchy / heritage          → Phase 3 (subgraph engine)
  COMPARATIVE  — cross-source comparison                   → Phase 6 (fan out + fuse)
  FALLBACK     — everything else                           → Phase 7 (LLM free-form SQL)

Fast-lane checks (Phase 4 high-confidence template/metric matches) bypass this router
entirely — they short-circuit before classify_intent is ever called.

Priority order inside classify_intent:
  1. COMPARATIVE — explicit cross-source comparison signal
  2. RELATIONAL  — multi-hop / hierarchy / heritage signal (no pure-count override)
  3. ANALYTICAL  — aggregate / count / metric signal
  4. FALLBACK    — default
"""
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

_ANALYTICAL_INTENTS = frozenset({"population", "eviction", "emigration", "tenancy"})

# Keyword sets that signal genuine relational depth (not just passing mentions).
_STRONG_RELATIONAL = _RELATIONAL_KEYWORDS | _HIERARCHY_KEYWORDS


def classify_intent(
    question: str,
    analysis: dict[str, Any],
    slot_fill: Any | None,
) -> str:
    """
    Return one of ANALYTICAL, RELATIONAL, COMPARATIVE, or FALLBACK.

    Called only when no Phase 4 fast-lane match is available — the fast lane
    (high-confidence template, verified analysis, direct memory reuse, high-confidence
    semantic slot fill) bypasses this function entirely.
    """
    q = (question or "").lower()
    primary_intent = analysis.get("primary_intent", "")
    output_mode = analysis.get("output_mode", "")

    # ── 1. COMPARATIVE — highest priority; both stores must contribute ────────
    if any(kw in q for kw in _COMPARATIVE_KEYWORDS):
        return COMPARATIVE

    # ── 2. RELATIONAL — multi-hop / hierarchy / heritage / sensemaking ────────
    has_relational = any(kw in q for kw in _RELATIONAL_KEYWORDS)
    has_hierarchy = any(kw in q for kw in _HIERARCHY_KEYWORDS)
    has_heritage = any(kw in q for kw in _HERITAGE_KEYWORDS)
    has_sensemaking = any(kw in q for kw in _SENSEMAKING_KEYWORDS)
    geography_intent = primary_intent == "geography"

    if geography_intent or has_relational or has_hierarchy or has_heritage or has_sensemaking:
        # Core Rule 1 override: a pure count question with no genuine relational
        # depth (e.g. "how many ring forts are in the parish?") stays on the SQL
        # path.  Only heritage/sensemaking keywords trigger this check; explicit
        # relational or hierarchy keywords always win.
        if (has_heritage or has_sensemaking) and not (has_relational or has_hierarchy or geography_intent):
            pure_count = (
                output_mode in {"count", "aggregate"}
                and any(kw in q for kw in _ANALYTICAL_KEYWORDS)
            )
            if pure_count:
                return ANALYTICAL
        return RELATIONAL

    # ── 3. ANALYTICAL — aggregate / metric / count questions ──────────────────
    analytical = (
        primary_intent in _ANALYTICAL_INTENTS
        or output_mode in {"count", "aggregate", "trend"}
        or any(kw in q for kw in _ANALYTICAL_KEYWORDS)
        or slot_fill is not None  # semantic layer found a candidate
    )
    if analytical:
        return ANALYTICAL

    # ── 4. FALLBACK — free-form LLM SQL generation ────────────────────────────
    return FALLBACK
