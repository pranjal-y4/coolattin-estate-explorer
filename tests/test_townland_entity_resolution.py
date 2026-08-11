"""
tests/test_townland_entity_resolution.py

End-to-end tests for source-townland entity resolution.

Each test runs against a throwaway SQLite database built by ensure_schema(), so
the real coolattin.db is never touched.  Test 8 and 9 go all the way to the map
FeatureCollection the frontend consumes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import extensions
from backend.repositories import match_review_repository, townland_repository
from backend.services import map_service
from backend.services.townland_resolution import (
    SourceTownland,
    resolve_source_townland,
)
from backend.services.townland_service import resolve_alias, resolve_compound

# A square around Coolattin, and a clearly disjoint one 3 degrees west.
WICKLOW_SQUARE = "POLYGON ((-6.6 52.7, -6.5 52.7, -6.5 52.8, -6.6 52.8, -6.6 52.7))"
FAR_SQUARE = "POLYGON ((-9.6 52.7, -9.5 52.7, -9.5 52.8, -9.6 52.8, -9.6 52.7))"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh database with the production schema; nothing else seeded."""
    db_path = tmp_path / "test_coolattin.db"
    extensions.init_db(db_path)
    extensions.ensure_schema()
    map_service.invalidate_townland_featurecollection()
    yield db_path
    map_service.invalidate_townland_featurecollection()


def _seed_canonical(**overrides) -> dict:
    fields = {
        "name": "COOLATTIN",
        "county": "WICKLOW",
        "barony": "SHILLELAGH",
        "civil_parish": "CARNEW",
        "area_sqm": 1_000_000.0,
        "wkt_geometry": WICKLOW_SQUARE,
        "source": "geojson",
    }
    fields.update(overrides)
    townland_id, entity_id = townland_repository.insert_canonical(fields)
    return {"id": townland_id, "entity_id": entity_id, **fields}


def _source(**overrides) -> SourceTownland:
    defaults = {
        "name": "Coolattin",
        "source": "test_source",
        "source_record_id": "T-001",
        "county": "WICKLOW",
    }
    defaults.update(overrides)
    return SourceTownland(**defaults)


# ---------------------------------------------------------------------------
# Test 1 — exact canonical match
# ---------------------------------------------------------------------------

def test_exact_canonical_match_reuses_entity(db):
    existing = _seed_canonical()

    result = resolve_source_townland(_source(name="Coolattin"))

    assert result.status == "matched"
    assert result.method == "exact"
    assert result.entity_id == existing["entity_id"]
    assert result.townland_id == existing["id"]
    assert townland_repository.count() == 1, "no duplicate canonical townland"

    xref = match_review_repository.find_xref("test_source", "T-001")
    assert xref is not None
    assert xref["entity_id"] == existing["entity_id"]
    assert xref["status"] == "confirmed"
    assert xref["source_name"] == "Coolattin"


# ---------------------------------------------------------------------------
# Test 2 — known alias
# ---------------------------------------------------------------------------

def test_known_alias_resolves_to_canonical(db):
    assert resolve_alias("COOLLATTIN") == "COOLATTIN", "alias layer must be seeded"
    existing = _seed_canonical()

    result = resolve_source_townland(_source(name="Coollattin", source_record_id="T-002"))

    assert result.status == "matched"
    assert result.method == "alias"
    assert result.entity_id == existing["entity_id"]
    assert townland_repository.count() == 1

    xref = match_review_repository.find_xref("test_source", "T-002")
    assert xref["source_name"] == "Coollattin", "observed spelling preserved"


def test_compound_alias_is_not_a_one_to_one_alias(db):
    """A compound name must never collapse into a single canonical townland."""
    assert resolve_compound("BALLARD AND CRONE") == ["BALLARD", "CRONE"]
    assert resolve_alias("BALLARD AND CRONE") == "BALLARD AND CRONE"

    ballard = _seed_canonical(name="BALLARD")
    _seed_canonical(name="CRONE", wkt_geometry=None)

    result = resolve_source_townland(
        _source(name="Ballard And Crone", source_record_id="T-003")
    )

    assert result.status == "ambiguous"
    assert result.method == "compound_ambiguous"
    assert townland_repository.count() == 2, "no canonical entity invented"
    xref = match_review_repository.find_xref("test_source", "T-003")
    assert xref["status"] == "pending"
    assert xref["entity_id"] == ballard["entity_id"]


# ---------------------------------------------------------------------------
# Test 3 — typo / variant with strong independent context
# ---------------------------------------------------------------------------

def test_variant_name_with_shared_authority_id_auto_resolves(db):
    existing = _seed_canonical(name="BALLINACOR", kg_uri="http://vrti.ie/kg/townland/ballinacor")

    result = resolve_source_townland(_source(
        name="Ballinacorr",
        source_record_id="T-010",
        kg_uri="http://vrti.ie/kg/townland/ballinacor",
    ))

    assert result.status == "matched"
    assert result.method == "authority_id"
    assert result.entity_id == existing["entity_id"]
    assert townland_repository.count() == 1


def test_variant_name_with_matching_geometry_auto_resolves(db):
    existing = _seed_canonical(name="BALLINACOR")

    result = resolve_source_townland(_source(
        name="Ballinacorr",
        source_record_id="T-011",
        barony="SHILLELAGH",
        civil_parish="CARNEW",
        area_sqm=1_000_000.0,
        wkt_geometry=WICKLOW_SQUARE,
    ))

    assert result.status == "matched"
    assert result.method == "corroborated"
    assert result.entity_id == existing["entity_id"]
    assert townland_repository.count() == 1


# ---------------------------------------------------------------------------
# Test 4 — similar name, conflicting location
# ---------------------------------------------------------------------------

def test_similar_name_conflicting_location_does_not_merge(db):
    existing = _seed_canonical(name="BALLINACOR")

    result = resolve_source_townland(_source(
        name="Ballinacorr",
        source_record_id="T-020",
        county="CLARE",
        barony="BUNRATTY",
        civil_parish="KILLALOE",
        wkt_geometry=FAR_SQUARE,
    ))

    assert result.status != "matched"
    assert result.entity_id != existing["entity_id"]
    assert townland_repository.count() == 2, "records stay separate"


def test_same_name_in_a_different_county_is_a_different_townland(db):
    """Irish townland names repeat across counties — a name is not a place."""
    existing = _seed_canonical(name="BALLINACOR", kg_uri="http://vrti.ie/kg/townland/ballinacor")

    result = resolve_source_townland(_source(
        name="Ballinacor",
        source_record_id="T-021",
        county="CLARE",
        barony="BUNRATTY",
    ))

    assert result.entity_id != existing["entity_id"]
    assert result.status == "created"
    assert any("name match rejected" in c for c in result.conflicts)
    assert townland_repository.count() == 2


def test_shared_authority_id_with_conflicting_hierarchy_is_not_merged(db):
    _seed_canonical(name="BALLINACOR", kg_uri="http://vrti.ie/kg/townland/ballinacor")

    conflicting = resolve_source_townland(_source(
        name="Ballinacor Beg",
        source_record_id="T-022",
        county="CLARE",
        barony="BUNRATTY",
        kg_uri="http://vrti.ie/kg/townland/ballinacor",
    ))

    assert conflicting.method != "authority_id", "authority id alone cannot override a conflict"
    assert any("barony" in c or "county" in c for c in conflicting.conflicts)


# ---------------------------------------------------------------------------
# Test 5 — ambiguous match goes to review
# ---------------------------------------------------------------------------

def test_ambiguous_match_is_queued_for_review(db):
    existing = _seed_canonical(name="BALLINACOR", wkt_geometry=None)

    result = resolve_source_townland(_source(
        name="Ballinacorr",
        source_record_id="T-030",
        barony="SHILLELAGH",
    ))

    assert result.status == "review"
    assert result.review_id
    assert result.entity_id != existing["entity_id"], "not merged before review"

    pending = match_review_repository.get_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert {pending[0]["townland_id_a"], pending[0]["townland_id_b"]} == {
        result.townland_id, existing["id"]
    }

    xref = match_review_repository.find_xref("test_source", "T-030")
    assert xref["status"] == "pending"


def test_confirming_a_review_merges_entity_ids(db):
    existing = _seed_canonical(name="BALLINACOR", wkt_geometry=None)
    result = resolve_source_townland(_source(
        name="Ballinacorr", source_record_id="T-031", barony="SHILLELAGH",
    ))
    assert result.status == "review"

    match_review_repository.apply_decision(result.review_id, "confirmed", "same place")

    merged = townland_repository.find_row_by_id(result.townland_id)
    other = townland_repository.find_row_by_id(existing["id"])
    assert merged["entity_id"] == other["entity_id"], "shared canonical entity id"


# ---------------------------------------------------------------------------
# Test 6 — genuinely new townland
# ---------------------------------------------------------------------------

def test_genuinely_new_townland_creates_canonical_entity(db):
    _seed_canonical(name="COOLATTIN")

    result = resolve_source_townland(_source(
        name="Kilpipe",
        source_record_id="T-040",
        barony="SHILLELAGH",
        wkt_geometry=WICKLOW_SQUARE,
    ))

    assert result.status == "created"
    assert result.canonical_name == "KILPIPE"
    assert result.has_geometry
    assert townland_repository.count() == 2

    row = townland_repository.find_row_by_entity_id(result.entity_id)
    assert row["name"] == "KILPIPE"
    assert row["barony"] == "SHILLELAGH"
    assert row["civil_parish"] is None, "unknown stays NULL, never invented"

    xref = match_review_repository.find_xref("test_source", "T-040")
    assert xref["entity_id"] == result.entity_id
    assert xref["source_name"] == "Kilpipe"


def test_new_canonical_uses_the_alias_resolved_name(db):
    """A variant spelling seen first must not become the canonical form."""
    assert resolve_alias("KILQUIGGAN") == "KILQUIGGIN"

    variant = resolve_source_townland(_source(name="Kilquiggan", source_record_id="T-041"))
    assert variant.status == "created"
    assert variant.canonical_name == "KILQUIGGIN", "created under the canonical name"

    later = resolve_source_townland(_source(name="Kilquiggin", source_record_id="T-042"))
    assert later.status == "matched"
    assert later.entity_id == variant.entity_id, "no split entity"
    assert townland_repository.count() == 1

    xref = match_review_repository.find_xref("test_source", "T-041")
    assert xref["source_name"] == "Kilquiggan", "observed variant kept as provenance"


# ---------------------------------------------------------------------------
# Test 7 — re-import is idempotent
# ---------------------------------------------------------------------------

def test_reimport_is_idempotent(db):
    _seed_canonical(name="COOLATTIN")
    src = _source(name="Kilpipe", source_record_id="T-050", wkt_geometry=WICKLOW_SQUARE)

    first = resolve_source_townland(src)
    second = resolve_source_townland(
        _source(name="Kilpipe", source_record_id="T-050", wkt_geometry=WICKLOW_SQUARE)
    )

    assert first.entity_id == second.entity_id
    assert first.townland_id == second.townland_id
    assert second.status == "matched"
    assert townland_repository.count() == 2

    conn = extensions.get_db_conn()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM townland_xref WHERE source='test_source' AND source_record_id='T-050'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM match_review").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM field_provenance WHERE entity_id = ?", (first.entity_id,)
        ).fetchone()[0] == conn.execute(
            "SELECT COUNT(DISTINCT field_name) FROM field_provenance WHERE entity_id = ?",
            (first.entity_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def test_ambiguous_reimport_does_not_duplicate_review(db):
    _seed_canonical(name="BALLINACOR", wkt_geometry=None)
    for _ in range(2):
        resolve_source_townland(_source(
            name="Ballinacorr", source_record_id="T-051", barony="SHILLELAGH",
        ))

    assert len(match_review_repository.get_pending()) == 1
    assert townland_repository.count() == 2


# ---------------------------------------------------------------------------
# Test 8 — map visibility
# ---------------------------------------------------------------------------

def test_new_townland_with_geometry_reaches_the_map(db, monkeypatch, tmp_path):
    empty_geojson = tmp_path / "townlands.json"
    empty_geojson.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    from config import ActiveConfig
    monkeypatch.setattr(ActiveConfig, "STATIC_DATA_DIR", tmp_path, raising=False)
    map_service.invalidate_townland_featurecollection()

    result = resolve_source_townland(_source(
        name="Kilpipe",
        source_record_id="T-060",
        wkt_geometry=WICKLOW_SQUARE,
    ))
    assert result.status == "created"

    collection = map_service.build_townland_featurecollection()
    drawn = {
        f["properties"].get("entity_id"): f
        for f in collection["features"]
    }
    assert result.entity_id in drawn, "canonical id present in the map data"

    feature = drawn[result.entity_id]
    assert feature["properties"]["TL_ENGLISH"] == "KILPIPE"
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["geometry"]["coordinates"], "real geometry, not a placeholder"
    assert collection["meta"]["appended_from_database"] == 1

    centroids = map_service.build_centroids()
    assert "KILPIPE" in centroids


# ---------------------------------------------------------------------------
# Test 9 — new townland without geometry
# ---------------------------------------------------------------------------

def test_new_townland_without_geometry_is_kept_but_not_drawn(db, monkeypatch, tmp_path):
    empty_geojson = tmp_path / "townlands.json"
    empty_geojson.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    from config import ActiveConfig
    monkeypatch.setattr(ActiveConfig, "STATIC_DATA_DIR", tmp_path, raising=False)
    map_service.invalidate_townland_featurecollection()

    result = resolve_source_townland(_source(
        name="Kilpipe",
        source_record_id="T-070",
    ))

    assert result.status == "created"
    assert result.has_geometry is False
    assert "geometry" in result.missing_evidence

    row = townland_repository.find_row_by_entity_id(result.entity_id)
    assert row is not None, "canonical townland stays in the database"
    assert row["wkt_geometry"] is None, "no fabricated polygon"

    collection = map_service.build_townland_featurecollection()
    assert collection["features"] == []
    assert collection["meta"]["resolved_without_geometry"] == 1


# ---------------------------------------------------------------------------
# Integration — the route the frontend actually calls
# ---------------------------------------------------------------------------

def test_map_request_serves_resolved_townland_over_http(db, monkeypatch, tmp_path):
    """GET /static/data/townlands.json must carry newly resolved townlands."""
    (tmp_path / "townlands.json").write_text(
        '{"type":"FeatureCollection","features":[]}', encoding="utf-8"
    )

    from config import ActiveConfig, DevelopmentConfig
    monkeypatch.setattr(ActiveConfig, "STATIC_DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(DevelopmentConfig, "DATABASE_PATH", db, raising=False)
    map_service.invalidate_townland_featurecollection()

    result = resolve_source_townland(_source(
        name="Kilpipe", source_record_id="T-080", wkt_geometry=WICKLOW_SQUARE,
    ))
    assert result.status == "created"

    from create_app import create_app
    app = create_app(DevelopmentConfig)
    client = app.test_client()

    response = client.get("/static/data/townlands.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert "no-cache" in response.headers.get("Cache-Control", "")

    payload = response.get_json()
    matches = [
        f for f in payload["features"]
        if f["properties"].get("entity_id") == result.entity_id
    ]
    assert len(matches) == 1, "resolved townland present exactly once in the map response"
    assert matches[0]["properties"]["TL_ENGLISH"] == "KILPIPE"
    assert matches[0]["geometry"]["coordinates"]
