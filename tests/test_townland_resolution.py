"""
tests/test_townland_resolution.py

Unit tests for the townland entity-resolution pipeline.

All tests are pure-function tests — no database or Flask app context is needed.
The four scenarios below correspond directly to the correctness requirements
in the refactoring spec.
"""
from __future__ import annotations

import sys
import os

# Ensure project root is on sys.path so imports resolve without a running app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.services.townland_service import (
    canonical_name,
    decide_match,
    extract_qualifier,
    normalize_townland_name,
    score_pair,
    transitive_closure,
    MATCH_THRESHOLD_HIGH,
    MATCH_THRESHOLD_LOW,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(**kwargs) -> dict:
    """Build a minimal townland record dict with sensible defaults."""
    defaults = {
        "name":         "BALLYMANUS",
        "name_gaelic":  None,
        "barony":       None,
        "civil_parish": None,
        "county":       "Wicklow",
        "area_sqm":     None,
        "wkt_geometry": None,
        "osm_id":       None,
        "osi_id":       None,
        "vrti_id":      None,
        "kg_uri":       None,
        "logainm_id":   None,
        "source":       "json",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Test 1 — Locational qualifiers (Upper / Lower) must stay distinct
# ---------------------------------------------------------------------------

class TestLocationalQualifierPreservation:

    def test_upper_preserved_in_canonical_name(self):
        result = normalize_townland_name("Ballinacor (Upper)")
        assert result == "BALLINACOR UPPER"

    def test_lower_preserved_in_canonical_name(self):
        result = normalize_townland_name("Ballinacor (Lower)")
        assert result == "BALLINACOR LOWER"

    def test_upper_and_lower_are_distinct(self):
        upper = normalize_townland_name("Ballinacor (Upper)")
        lower = normalize_townland_name("Ballinacor (Lower)")
        assert upper != lower

    def test_type_qualifier_civil_parish_stripped(self):
        result = normalize_townland_name("Ballinacor (Civil Parish)")
        assert result == "BALLINACOR"
        assert "CIVIL" not in result
        assert "PARISH" not in result

    def test_type_qualifier_electoral_division_stripped(self):
        result = normalize_townland_name("Rathdrum (Electoral Division)")
        assert result == "RATHDRUM"

    def test_extract_qualifier_upper(self):
        assert extract_qualifier("Ballinacor (Upper)") == "UPPER"

    def test_extract_qualifier_lower(self):
        assert extract_qualifier("Ballinacor (Lower)") == "LOWER"

    def test_extract_qualifier_civil_parish_is_none(self):
        assert extract_qualifier("Ballinacor (Civil Parish)") is None

    def test_extract_qualifier_no_parens_is_none(self):
        assert extract_qualifier("Ballymanus") is None

    def test_nfc_normalisation_applied(self):
        # Precomposed and decomposed forms should produce the same result
        composed   = "Cloghé" # é as single codepoint
        decomposed = "Cloghé" # e + combining acute
        assert normalize_townland_name(composed) == normalize_townland_name(decomposed)

    def test_townland_of_prefix_stripped(self):
        result = normalize_townland_name("Townland of Ballinacor (Upper)")
        assert result == "BALLINACOR UPPER"

    def test_ballinacor_upper_not_aliased_to_ballinacor(self):
        # canonical_name must NOT collapse "BALLINACOR UPPER" to "BALLINACOR"
        assert canonical_name("Ballinacor (Upper)") == "BALLINACOR UPPER"
        assert canonical_name("Ballinacor (Lower)") == "BALLINACOR LOWER"


# ---------------------------------------------------------------------------
# Test 2 — Same name, different baronies: must stay distinct (no auto-merge)
# ---------------------------------------------------------------------------

class TestSameNameDifferentBarony:

    def test_same_name_different_barony_not_merged(self):
        """
        Two records with identical names but different baronies should never
        auto-merge: name similarity alone cannot satisfy has_corroboration.
        """
        rec_a = _rec(
            name="BALLARD",
            barony="BALLINACOR",
            civil_parish="DUNGANSTOWN WEST",
            county="Wicklow",
            area_sqm=120_000,
        )
        rec_b = _rec(
            name="BALLARD",
            barony="RATHDOWN",
            civil_parish="POWERSCOURT",
            county="Wicklow",
            area_sqm=95_000,
        )
        feats = score_pair(rec_a, rec_b)
        decision = decide_match(feats)

        assert decision != "merge", (
            f"Should not merge same-named townlands in different baronies; "
            f"score={feats.score:.3f} corroboration={feats.has_corroboration} "
            f"feats={feats.to_dict()}"
        )

    def test_name_similarity_alone_never_corroborates(self):
        """
        has_corroboration must be False when the only signal is name similarity.
        """
        rec_a = _rec(name="COOLBAWN", barony="A")
        rec_b = _rec(name="COOLBAWN", barony="B")
        feats = score_pair(rec_a, rec_b)

        assert feats.has_corroboration is False

    def test_same_parish_barony_area_with_high_name_similarity_merges(self):
        """
        Same parish + barony + area within tolerance + near-identical names
        must auto-merge via the explicit administrative corroboration path.
        """
        rec_a = _rec(
            name="RATHNEW",
            barony="ARKLOW",
            civil_parish="RATHNEW",
            county="Wicklow",
            area_sqm=500_000,
        )
        rec_b = _rec(
            name="RATHNEW",
            barony="ARKLOW",
            civil_parish="RATHNEW",
            county="Wicklow",
            area_sqm=497_000,   # within 1% — corroborated
        )
        feats = score_pair(rec_a, rec_b)
        assert feats.same_civil_parish is True
        assert feats.same_barony is True
        assert feats.area_ratio >= 0.90
        assert feats.jaro_winkler >= 0.90
        assert decide_match(feats) == "merge"

    def test_perfect_name_without_corroboration_goes_to_review_or_reject(self):
        """
        Even a perfect name match without external ID / IoU / parish+barony+area
        must land in 'review' or 'reject', never 'merge'.
        """
        rec_a = _rec(name="KILCAVAN")
        rec_b = _rec(name="KILCAVAN")
        feats = score_pair(rec_a, rec_b)
        decision = decide_match(feats)
        assert decision in ("review", "reject")
        assert decision != "merge"


# ---------------------------------------------------------------------------
# Test 3 — Shared external ID overrides name mismatch
# ---------------------------------------------------------------------------

class TestExternalIdMatchOverridesName:

    def test_shared_osm_id_is_decisive(self):
        """
        Two records sharing an OSM ID must produce a score of 0.99 and
        decide as 'merge', regardless of name divergence.
        """
        rec_a = _rec(name="COOLBAWN", osm_id="W99999999")
        rec_b = _rec(name="COOLBALLINTAGGART", osm_id="W99999999")

        feats = score_pair(rec_a, rec_b)

        assert feats.external_id_match is True
        assert feats.score >= 0.99
        assert decide_match(feats) == "merge"

    def test_shared_vrti_id_is_decisive(self):
        rec_a = _rec(name="AGHOWLE UPPER", vrti_id="VRTI-12345")
        rec_b = _rec(name="AGHOWLE",       vrti_id="VRTI-12345")

        feats = score_pair(rec_a, rec_b)

        assert feats.external_id_match is True
        assert decide_match(feats) == "merge"

    def test_shared_logainm_id_is_decisive(self):
        rec_a = _rec(name="RATHNEW",   logainm_id="logainm-567")
        rec_b = _rec(name="RATHENEW",  logainm_id="logainm-567")

        feats = score_pair(rec_a, rec_b)

        assert feats.external_id_match is True
        assert decide_match(feats) == "merge"

    def test_no_shared_id_does_not_force_merge(self):
        rec_a = _rec(name="COOLBAWN",          osm_id="W111")
        rec_b = _rec(name="COOLBALLINTAGGART",  osm_id="W222")

        feats = score_pair(rec_a, rec_b)

        assert feats.external_id_match is False
        assert feats.score < 0.99


# ---------------------------------------------------------------------------
# Test 4 — Low polygon IoU pair sent to review, not merged
# ---------------------------------------------------------------------------

class TestLowIouGoesToReview:
    # Two small non-overlapping rectangles in the Wicklow area
    # IoU = 0 (no overlap) — well below the 0.8 auto-merge threshold
    _WKT_A = (
        "POLYGON((-6.30 52.80, -6.25 52.80, -6.25 52.85,"
        " -6.30 52.85, -6.30 52.80))"
    )
    _WKT_B = (
        "POLYGON((-6.20 52.80, -6.15 52.80, -6.15 52.85,"
        " -6.20 52.85, -6.20 52.80))"
    )

    def test_non_overlapping_polygons_produce_zero_iou(self):
        from backend.services.townland_service import _polygon_iou
        iou = _polygon_iou(self._WKT_A, self._WKT_B)
        assert iou == pytest.approx(0.0)

    def test_low_iou_pair_not_auto_merged(self):
        """
        Similar names + same county + low polygon IoU should not auto-merge.
        Without IoU >= 0.8 and without matching external IDs, has_corroboration
        is False, so the decision must be 'review' or 'reject'.
        """
        rec_a = _rec(
            name="COOLBAWN",
            barony="SHILLELAGH",
            civil_parish="MOYACOMB",
            county="Wicklow",
            area_sqm=220_000,
            wkt_geometry=self._WKT_A,
        )
        rec_b = _rec(
            name="COOLBAWN",
            barony="ARKLOW",
            civil_parish="KILGORMAN",
            county="Wicklow",
            area_sqm=180_000,
            wkt_geometry=self._WKT_B,
        )
        feats = score_pair(rec_a, rec_b)

        assert feats.polygon_iou < 0.8
        assert feats.has_corroboration is False
        assert decide_match(feats) != "merge"

    def test_low_iou_same_name_lands_in_review_band(self):
        """
        Identical names produce a score > LOW threshold → 'review', not 'reject'.
        """
        rec_a = _rec(
            name="COOLBAWN",
            barony="SHILLELAGH",
            civil_parish="MOYACOMB",
            area_sqm=220_000,
            wkt_geometry=self._WKT_A,
        )
        rec_b = _rec(
            name="COOLBAWN",
            barony="ARKLOW",
            civil_parish="KILGORMAN",
            area_sqm=180_000,
            wkt_geometry=self._WKT_B,
        )
        feats = score_pair(rec_a, rec_b)
        assert feats.score > MATCH_THRESHOLD_LOW, (
            f"Score {feats.score:.3f} is below MATCH_THRESHOLD_LOW={MATCH_THRESHOLD_LOW};"
            " same-name pair should be in the review band, not auto-rejected"
        )
        assert decide_match(feats) == "review"


# ---------------------------------------------------------------------------
# Bonus: transitive closure clustering
# ---------------------------------------------------------------------------

class TestTransitiveClosure:

    def test_single_pair(self):
        clusters = transitive_closure([("A", "B")])
        assert len(clusters) == 1
        assert set(clusters[0]) == {"A", "B"}

    def test_chain(self):
        # A-B and B-C → single cluster {A,B,C}
        clusters = transitive_closure([("A", "B"), ("B", "C")])
        assert len(clusters) == 1
        assert set(clusters[0]) == {"A", "B", "C"}

    def test_two_independent_pairs(self):
        clusters = transitive_closure([("A", "B"), ("C", "D")])
        assert len(clusters) == 2
        cluster_sets = [set(c) for c in clusters]
        assert {"A", "B"} in cluster_sets
        assert {"C", "D"} in cluster_sets
