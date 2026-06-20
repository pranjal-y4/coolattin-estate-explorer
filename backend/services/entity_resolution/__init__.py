"""
Entity-resolution helpers for persisted workhouse-to-unified-record linking.
"""

from .candidates import build_unified_index, generate_candidates
from .normalise import (
    clean_estate_name_field,
    normalise_person_fields,
    normalise_place_name,
    phonetic_code,
)
from .scoring import score_candidate

__all__ = [
    "build_unified_index",
    "clean_estate_name_field",
    "generate_candidates",
    "normalise_person_fields",
    "normalise_place_name",
    "phonetic_code",
    "score_candidate",
]
