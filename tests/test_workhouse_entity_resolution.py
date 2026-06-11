from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from create_app import create_app
from backend.services.entity_resolution.normalise import (
    normalise_person_fields,
    normalise_place_name,
)
from backend.services.entity_resolution.candidates import generate_candidates
from backend.services.entity_resolution.scoring import score_candidate


def _strong_mention(**overrides):
    base = {
        "source_record_id": "wh:1",
        "normalised_name": "JOHN DOE",
        "forename": "JOHN",
        "surname": "DOE",
        "forename_initial": "J",
        "phonetic_surname": "T",
        "normalised_place": "COOLBOY",
        "event_year": 1850,
        "age": 40,
        "inferred_birth_year": 1810,
        "occupation_norm": "LABOURER",
        "household_fields": "MARY 2",
        "gender": "M",
    }
    base.update(overrides)
    return base


def _strong_candidate(record_id: str = "U1", **overrides):
    base = {
        "record_id": record_id,
        "canonical_name": "John Doe",
        "normalised_name": "JOHN DOE",
        "forename": "JOHN",
        "surname": "DOE",
        "forename_initial": "J",
        "phonetic_surname": "T",
        "normalised_place": "COOLBOY",
        "event_year": 1851,
        "age": 41,
        "inferred_birth_year": 1810,
        "occupation_norm": "LABOURER",
        "household_fields": "MARY 2",
        "gender": "M",
        "townland": "Coolboy",
        "parish": "Coolboy",
    }
    base.update(overrides)
    return base


def test_exact_match_scores_as_confirmed():
    result = score_candidate(_strong_mention(), _strong_candidate())
    assert result.label == "CONFIRMED_MATCH"
    assert result.score >= 0.85


def test_abbreviation_normalises_to_possible_john():
    person = normalise_person_fields("Doe Jno", surname_first=True)
    assert person["forename"] == "JOHN"
    assert person["surname"] == "DOE"


def test_place_variant_normalises_to_canonical_coolboy():
    assert normalise_place_name("Townland of Coolboy (Civil Parish)") == "COOLBOY"


def test_age_date_contradiction_is_not_auto_linked():
    mention = _strong_mention(inferred_birth_year=1780, age=70)
    candidate = _strong_candidate(inferred_birth_year=1845, age=5)
    result = score_candidate(mention, candidate)
    assert result.label == "NO_MATCH"
    assert "Impossible age/date conflict" in result.conflicts


def test_multiple_possible_candidates_are_generated():
    mention = _strong_mention()
    candidates = generate_candidates(
        mention,
        [
            _strong_candidate(record_id="U1"),
            _strong_candidate(record_id="U2", event_year=1852, inferred_birth_year=1811),
        ],
    )
    assert len(candidates) == 2


def test_workhouse_resolution_does_not_require_pgvector(monkeypatch):
    from backend.services.ask_pgvector import backend_status
    from backend.services.workhouse_entity_resolution import build_source_mentions

    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/no-pgvector.db")
    assert backend_status()["available"] is False
    mentions = build_source_mentions(limit=1)
    assert len(mentions) == 1


def test_api_returns_please_check_records_and_ambiguity(monkeypatch, tmp_path):
    class TestConfig:
        SECRET_KEY = "test"
        DATABASE_PATH = tmp_path / "test.db"
        VRTI_SPARQL_ENDPOINT = ""
        CENSUS_STALE_AFTER_DAYS = 7
        EXPORTS_DIR = tmp_path / "exports"
        STATIC_DATA_DIR = tmp_path

    app = create_app(config_class=TestConfig)
    sample_df = pd.DataFrame(
        [
            {
                "record_id": "U1",
                "surname": "Doe",
                "forename": "John",
                "townland": "Coolboy",
                "year": 1850,
                "estate": "Coolattin",
                "canonical_name": "John Doe",
                "has_emigration_record": False,
                "has_eviction_record": True,
                "has_tenancy_record": False,
            }
        ]
    )
    monkeypatch.setattr("backend.services.unified_service.get_unified", lambda: sample_df.copy())

    with app.app_context():
        from extensions import get_db_conn

        conn = get_db_conn()
        try:
            conn.execute(
                """
                INSERT INTO source_mentions (
                  source_table, source_record_id, raw_name, normalised_name, forename, surname,
                  phonetic_forename, phonetic_surname, raw_place, normalised_place,
                  event_year, age, occupation, household_fields, source_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "workhouse_excel",
                    "wh:1",
                    "John Doe",
                    "JOHN DOE",
                    "JOHN",
                    "DOE",
                    "JN",
                    "T",
                    "Coolboy",
                    "COOLBOY",
                    1850,
                    40,
                    "Labourer",
                    "MARY 2",
                    "{}",
                ),
            )
            mention_one = int(conn.execute("SELECT id FROM source_mentions WHERE source_record_id='wh:1'").fetchone()["id"])
            conn.execute(
                """
                INSERT INTO workhouse_unified_links (
                  mention_id, unified_record_id, score, label, review_required,
                  supporting_evidence_json, conflicting_evidence_json, missing_evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mention_one,
                    "U1",
                    0.72,
                    "POSSIBLE_MATCH",
                    1,
                    json.dumps(["Strong name similarity"]),
                    json.dumps(["Place conflict unresolved"]),
                    json.dumps(["No household corroboration"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO source_mentions (
                  source_table, source_record_id, raw_name, normalised_name, forename, surname,
                  phonetic_forename, phonetic_surname, raw_place, normalised_place,
                  event_year, age, occupation, household_fields, source_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "workhouse_excel",
                    "wh:2",
                    "John Doe",
                    "JOHN DOE",
                    "JOHN",
                    "DOE",
                    "JN",
                    "T",
                    "Coolboy",
                    "COOLBOY",
                    1851,
                    41,
                    "Labourer",
                    "MARY 2",
                    "{}",
                ),
            )
            mention_two = int(conn.execute("SELECT id FROM source_mentions WHERE source_record_id='wh:2'").fetchone()["id"])
            conn.execute(
                """
                INSERT INTO workhouse_unified_links (
                  mention_id, unified_record_id, score, label, review_required,
                  supporting_evidence_json, conflicting_evidence_json, missing_evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mention_two,
                    "U1",
                    0.69,
                    "POSSIBLE_MATCH",
                    1,
                    json.dumps(["Timeline broadly aligned"]),
                    json.dumps([]),
                    json.dumps(["Missing exact birth year"]),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    client = app.test_client()
    response = client.get("/api/unified/records?surname=Doe")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload[0]["identity_is_ambiguous"] is True
    assert payload[0]["linked_workhouse_records"] == []
    assert len(payload[0]["possible_workhouse_matches"]) == 2
    assert len(payload[0]["please_check_records"]) == 2
    assert {item["source_record_id"] for item in payload[0]["please_check_records"]} == {"wh:1", "wh:2"}
    assert payload[0]["conflicting_evidence"]


def test_ui_contains_please_check_records_section():
    js = Path("frontend/static/js/main.js").read_text(encoding="utf-8")
    assert "Please check these records" in js
    assert "please_check_records" in js
