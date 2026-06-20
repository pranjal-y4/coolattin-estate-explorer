"""
backend/services/workhouse_entity_resolution.py

Persisted workhouse-to-unified-record entity resolution.

This pipeline is deliberately independent from Ask-page pgvector retrieval.
It uses deterministic normalisation, fuzzy blocking, transparent scoring, and
reviewable candidate links stored in SQLite.
"""
from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from extensions import get_db_conn

from backend.services.entity_resolution import (
    build_unified_index,
    clean_estate_name_field,
    generate_candidates,
    normalise_person_fields,
    normalise_place_name,
    score_candidate,
)

log = logging.getLogger(__name__)

_LABEL_TO_CONFIDENCE = {
    "CONFIRMED_MATCH": "High",
    "POSSIBLE_MATCH": "Medium",
    "WEAK_CANDIDATE": "Low",
    "NO_MATCH": "Low",
}

_PAYLOAD_SKIP_KEYS = frozenset({"raw_name", "source_record_id", "forename", "surname"})
_PAYLOAD_JUNK_VALUES = frozenset({"", '"', "'", ",", ".", "-", "--"})


def _extract_workhouse_detail(source_payload_json: str | None) -> dict[str, Any]:
    """Decode source_payload_json and return only non-null, displayable fields."""
    if not source_payload_json:
        return {}
    try:
        raw = json.loads(source_payload_json)
    except Exception:
        return {}
    return {
        k: v
        for k, v in raw.items()
        if not k.startswith("_")
        and k not in _PAYLOAD_SKIP_KEYS
        and v is not None
        and str(v).strip() not in _PAYLOAD_JUNK_VALUES
    }
_TOWNLAND_ID_CACHE: dict[str, int | None] = {}


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _workhouse_record_id(row: dict[str, Any], index: int) -> str:
    sheet = str(row.get("source_sheet") or "workhouse").strip() or "workhouse"
    register_number = _safe_int(row.get("register_number"))
    if register_number:
        return f"{sheet}:{register_number}:{index + 1}"
    return f"{sheet}:row:{index + 1}"


def _canonical_townland_id(normalised_place: str) -> int | None:
    if not normalised_place:
        return None
    if normalised_place in _TOWNLAND_ID_CACHE:
        return _TOWNLAND_ID_CACHE[normalised_place]
    conn = get_db_conn()
    try:
        row = conn.execute(
            """
            SELECT id
            FROM townland
            WHERE name = ? OR civil_parish = ?
            ORDER BY id
            LIMIT 1
            """,
            (normalised_place, normalised_place),
        ).fetchone()
        if row:
            _TOWNLAND_ID_CACHE[normalised_place] = int(row["id"])
            return _TOWNLAND_ID_CACHE[normalised_place]
        fuzzy = conn.execute(
            """
            SELECT id
            FROM townland
            WHERE name LIKE ? OR civil_parish LIKE ?
            ORDER BY id
            LIMIT 1
            """,
            (f"%{normalised_place}%", f"%{normalised_place}%"),
        ).fetchone()
        _TOWNLAND_ID_CACHE[normalised_place] = int(fuzzy["id"]) if fuzzy else None
        return _TOWNLAND_ID_CACHE[normalised_place]
    finally:
        conn.close()


def build_source_mentions(limit: int | None = None) -> list[dict[str, Any]]:
    from backend.services.workhouse_service import get_workhouse

    mentions: list[dict[str, Any]] = []
    workhouse_rows = get_workhouse()
    if limit:
        workhouse_rows = workhouse_rows[: max(0, int(limit))]
    for idx, row in enumerate(workhouse_rows):
        source_record_id = _workhouse_record_id(row, idx)
        person = normalise_person_fields(
            row.get("raw_name") or "",
            surname_first=True,
        )
        normalised_place = normalise_place_name(row.get("electoral_division"))
        event_year = _safe_int(row.get("_year_admitted") or row.get("_year_left"))
        age = _safe_int(row.get("age"))
        inferred_birth_year = event_year - age if event_year and age else None
        household_fields = " ".join(
            str(x).strip()
            for x in (
                row.get("spouse"),
                row.get("children_count"),
                row.get("status"),
            )
            if x not in (None, "")
        )
        occupation_norm = str(row.get("employment") or "").strip().upper()
        mentions.append(
            {
                "source_table": "workhouse_excel",
                "source_record_id": source_record_id,
                "raw_name": row.get("raw_name"),
                "normalised_name": person["normalised_name"],
                "forename": person["forename"],
                "surname": person["surname"],
                "phonetic_forename": person["phonetic_forename"],
                "phonetic_surname": person["phonetic_surname"],
                "forename_initial": person["forename_initial"],
                "raw_place": row.get("electoral_division"),
                "normalised_place": normalised_place,
                "canonical_townland_id": _canonical_townland_id(normalised_place),
                "event_year": event_year,
                "age": age,
                "inferred_birth_year": inferred_birth_year,
                "occupation": row.get("employment"),
                "occupation_norm": occupation_norm,
                "household_fields": household_fields,
                "gender": (str(row.get("sex") or "").strip().upper()[:1] or None),
                "source_payload_json": json.dumps(row, ensure_ascii=True),
            }
        )
    return mentions


def _load_unified_records() -> list[dict[str, Any]]:
    from backend.services.unified_service import get_unified

    base_df = get_unified()
    df = base_df.astype(object).where(base_df.notna(), None)
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        person = normalise_person_fields(
            raw_name=clean_estate_name_field(row.get("canonical_name") or ""),
            forename=clean_estate_name_field(row.get("forename")),
            surname=clean_estate_name_field(row.get("surname")),
            surname_first=False,
        )
        normalised_place = normalise_place_name(
            row.get("townland") or row.get("townland_official_name") or row.get("parish")
        )
        event_year = _safe_int(row.get("year"))
        age = _safe_int(row.get("age") or row.get("age_head_of_household"))
        inferred_birth_year = event_year - age if event_year and age else None
        gender_raw = str(row.get("gender") or "").strip().upper()
        records.append(
            {
                **row,
                "normalised_name": person["normalised_name"],
                "forename": person["forename"],
                "surname": person["surname"],
                "phonetic_forename": person["phonetic_forename"],
                "phonetic_surname": person["phonetic_surname"],
                "forename_initial": person["forename_initial"],
                "normalised_place": normalised_place,
                "event_year": event_year,
                "age": age,
                "inferred_birth_year": inferred_birth_year,
                "occupation_norm": str(row.get("occupation") or "").strip().upper(),
                "household_fields": " ".join(
                    str(x).strip()
                    for x in (row.get("household_list"), row.get("family_key"))
                    if x not in (None, "")
                ),
                "gender": gender_raw[:1] if gender_raw else None,
            }
        )
    return records


def _clear_resolution_tables(conn) -> None:
    conn.execute("DELETE FROM entity_resolution_decisions")
    conn.execute("DELETE FROM workhouse_unified_links")
    conn.execute("DELETE FROM entity_resolution_candidates")
    conn.execute("DELETE FROM source_mentions")


def _insert_source_mention(conn, mention: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO source_mentions (
          source_table, source_record_id, raw_name, normalised_name, forename, surname,
          phonetic_forename, phonetic_surname, raw_place, normalised_place,
          canonical_townland_id, event_year, age, inferred_birth_year,
          occupation, household_fields, source_payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            mention["source_table"],
            mention["source_record_id"],
            mention.get("raw_name"),
            mention.get("normalised_name"),
            mention.get("forename"),
            mention.get("surname"),
            mention.get("phonetic_forename"),
            mention.get("phonetic_surname"),
            mention.get("raw_place"),
            mention.get("normalised_place"),
            mention.get("canonical_townland_id"),
            mention.get("event_year"),
            mention.get("age"),
            mention.get("inferred_birth_year"),
            mention.get("occupation"),
            mention.get("household_fields"),
            mention.get("source_payload_json"),
        ),
    )
    return int(cur.lastrowid)


def _persist_candidate(
    conn,
    mention_id: int,
    candidate: dict[str, Any],
    score_result,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO entity_resolution_candidates (
          mention_id, candidate_source_table, candidate_record_id, candidate_name,
          candidate_place, candidate_year, score, label, evidence_json,
          conflicts_json, missing_evidence_json, review_required, created_at, updated_at
        ) VALUES (?, 'unified_record', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            mention_id,
            str(candidate.get("record_id") or ""),
            candidate.get("canonical_name"),
            candidate.get("townland") or candidate.get("parish"),
            candidate.get("event_year"),
            score_result.score,
            score_result.label,
            json.dumps(score_result.evidence, ensure_ascii=True),
            json.dumps(score_result.conflicts, ensure_ascii=True),
            json.dumps(score_result.missing_evidence, ensure_ascii=True),
            1 if score_result.label == "POSSIBLE_MATCH" else 0,
        ),
    )
    candidate_id = int(cur.lastrowid)
    if score_result.label in {"CONFIRMED_MATCH", "POSSIBLE_MATCH"}:
        conn.execute(
            """
            INSERT INTO workhouse_unified_links (
              mention_id, unified_record_id, score, label, review_required,
              supporting_evidence_json, conflicting_evidence_json,
              missing_evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                mention_id,
                str(candidate.get("record_id") or ""),
                score_result.score,
                score_result.label,
                1 if score_result.label == "POSSIBLE_MATCH" else 0,
                json.dumps(score_result.evidence, ensure_ascii=True),
                json.dumps(score_result.conflicts, ensure_ascii=True),
                json.dumps(score_result.missing_evidence, ensure_ascii=True),
            ),
        )
    return candidate_id


def link_workhouse_records(
    *,
    limit: int | None = None,
    audit_dir: str | Path | None = None,
) -> dict[str, Any]:
    mentions = build_source_mentions(limit=limit)
    unified_records = _load_unified_records()
    # Pre-build blocking index once; avoids O(n_workhouse × n_unified) scan
    unified_idx = build_unified_index(unified_records)

    conn = get_db_conn()
    audit_rows: list[dict[str, Any]] = []
    ambiguous_name_counter: Counter[str] = Counter()
    summary = {
        "workhouse_records_processed": 0,
        "source_mentions_created": 0,
        "confirmed_links": 0,
        "possible_links": 0,
        "weak_candidates": 0,
        "unresolved_records": 0,
        "requiring_review": 0,
    }
    try:
        _clear_resolution_tables(conn)
        for mention in mentions:
            summary["workhouse_records_processed"] += 1
            mention_id = _insert_source_mention(conn, mention)
            summary["source_mentions_created"] += 1

            candidates = generate_candidates(mention, unified_records, unified_index=unified_idx)
            if not candidates:
                summary["unresolved_records"] += 1
                continue

            strong_candidate_count = 0
            for candidate in candidates:
                score_result = score_candidate(mention, candidate)
                if score_result.label == "NO_MATCH":
                    continue
                if score_result.label in {"CONFIRMED_MATCH", "POSSIBLE_MATCH"}:
                    strong_candidate_count += 1
                _persist_candidate(conn, mention_id, candidate, score_result)

                if score_result.label == "CONFIRMED_MATCH":
                    summary["confirmed_links"] += 1
                elif score_result.label == "POSSIBLE_MATCH":
                    summary["possible_links"] += 1
                    summary["requiring_review"] += 1
                elif score_result.label == "WEAK_CANDIDATE":
                    summary["weak_candidates"] += 1

                audit_rows.append(
                    {
                        "workhouse_record_id": mention["source_record_id"],
                        "candidate_unified_record_id": candidate.get("record_id"),
                        "score": score_result.score,
                        "label": score_result.label,
                        "evidence": "; ".join(score_result.evidence),
                        "conflicts": "; ".join(score_result.conflicts),
                        "review_required": score_result.label == "POSSIBLE_MATCH",
                    }
                )
            if strong_candidate_count == 0:
                summary["unresolved_records"] += 1
            elif strong_candidate_count > 1:
                ambiguous_name_counter[mention["normalised_name"] or mention["raw_name"] or "UNKNOWN"] += 1

        conn.commit()
    finally:
        conn.close()

    if audit_dir:
        out_dir = Path(audit_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "workhouse_er_audit.csv"
        json_path = out_dir / "workhouse_er_audit.json"
        if audit_rows:
            with csv_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(audit_rows[0].keys()))
                writer.writeheader()
                writer.writerows(audit_rows)
            json_path.write_text(json.dumps(audit_rows, indent=2), encoding="utf-8")

    summary["top_ambiguous_names"] = ambiguous_name_counter.most_common(10)
    return summary


def has_persisted_links() -> bool:
    conn = get_db_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM workhouse_unified_links").fetchone()
        return bool(int(row["n"] or 0))
    finally:
        conn.close()


def _rows_for_record(record_id: str) -> list[dict[str, Any]]:
    conn = get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT
              l.unified_record_id, l.score, l.label, l.review_required,
              l.supporting_evidence_json, l.conflicting_evidence_json, l.missing_evidence_json,
              s.id AS mention_id, s.source_record_id, s.raw_name, s.raw_place, s.event_year,
              s.age, s.occupation, s.source_payload_json
            FROM workhouse_unified_links l
            JOIN source_mentions s ON s.id = l.mention_id
            WHERE l.unified_record_id = ?
            ORDER BY l.score DESC, s.source_record_id
            """,
            (str(record_id),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_record_resolution(record_id: str) -> dict[str, Any]:
    rows = _rows_for_record(record_id)
    linked: list[dict[str, Any]] = []
    possible: list[dict[str, Any]] = []
    supporting: list[str] = []
    conflicting: list[str] = []
    mention_ids: list[int] = []

    for row in rows:
        evidence = json.loads(row.get("supporting_evidence_json") or "[]")
        conflicts = json.loads(row.get("conflicting_evidence_json") or "[]")
        missing = json.loads(row.get("missing_evidence_json") or "[]")
        payload = {
            "source": "workhouse",
            "source_record_id": row.get("source_record_id"),
            "name": row.get("raw_name"),
            "place": row.get("raw_place"),
            "year": row.get("event_year"),
            "age": row.get("age"),
            "occupation": row.get("occupation"),
            "confidence_score": row.get("score"),
            "confidence": _LABEL_TO_CONFIDENCE.get(row.get("label"), "Low"),
            "label": row.get("label"),
            "why_it_matched": evidence,
            "what_evidence_is_missing": missing,
            "conflicting_evidence": conflicts,
            "review_required": bool(row.get("review_required")),
            "workhouse_detail": _extract_workhouse_detail(row.get("source_payload_json")),
        }
        mention_ids.append(int(row["mention_id"]))
        supporting.extend(evidence)
        conflicting.extend(conflicts)
        if row.get("label") == "CONFIRMED_MATCH":
            linked.append(payload)
        elif row.get("label") == "POSSIBLE_MATCH":
            possible.append(payload)

    ambiguous = len(set(mention_ids)) != len(mention_ids) or len(possible) > 1
    note = None
    if ambiguous:
        note = (
            "Multiple plausible workhouse links were found for this identity. "
            "Please review the possible matches before treating them as the same person."
        )

    return {
        "record_id": str(record_id),
        "linked_workhouse_records": linked,
        "possible_workhouse_matches": possible,
        "please_check_records": possible,
        "identity_is_ambiguous": ambiguous,
        "identity_disambiguation_note": note,
        "supporting_evidence": sorted(set(supporting)),
        "conflicting_evidence": sorted(set(conflicting)),
    }


def get_resolution_map(record_ids: list[str]) -> dict[str, dict[str, Any]]:
    """
    Return a map of record_id → resolution dict for all given record IDs.
    Uses a single batched SQL query instead of one query per record, so it
    is safe to call with thousands of IDs (e.g. the unfiltered records page).
    """
    if not record_ids:
        return {}

    # Only query records that actually have links — avoids a huge IN clause
    # when most records have no workhouse match.
    linked_ids: set[str] = set()
    conn = get_db_conn()
    try:
        rows_all = conn.execute(
            """
            SELECT
              l.unified_record_id, l.score, l.label, l.review_required,
              l.supporting_evidence_json, l.conflicting_evidence_json, l.missing_evidence_json,
              s.id AS mention_id, s.source_record_id, s.raw_name, s.raw_place, s.event_year,
              s.age, s.occupation, s.source_payload_json
            FROM workhouse_unified_links l
            JOIN source_mentions s ON s.id = l.mention_id
            ORDER BY l.unified_record_id, l.score DESC
            """
        ).fetchall()
    finally:
        conn.close()

    # Group rows by unified_record_id in memory
    from collections import defaultdict
    rows_by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows_all:
        rows_by_id[row["unified_record_id"]].append(dict(row))

    result: dict[str, dict[str, Any]] = {}
    requested = set(str(r) for r in record_ids)

    for record_id in requested:
        rows = rows_by_id.get(str(record_id), [])
        linked: list[dict[str, Any]] = []
        possible: list[dict[str, Any]] = []
        supporting: list[str] = []
        conflicting: list[str] = []
        mention_ids: list[int] = []

        for row in rows:
            evidence = json.loads(row.get("supporting_evidence_json") or "[]")
            conflicts = json.loads(row.get("conflicting_evidence_json") or "[]")
            missing = json.loads(row.get("missing_evidence_json") or "[]")
            payload = {
                "source": "workhouse",
                "source_record_id": row.get("source_record_id"),
                "name": row.get("raw_name"),
                "place": row.get("raw_place"),
                "year": row.get("event_year"),
                "age": row.get("age"),
                "occupation": row.get("occupation"),
                "confidence_score": row.get("score"),
                "confidence": _LABEL_TO_CONFIDENCE.get(row.get("label"), "Low"),
                "label": row.get("label"),
                "why_it_matched": evidence,
                "what_evidence_is_missing": missing,
                "conflicting_evidence": conflicts,
                "review_required": bool(row.get("review_required")),
                "workhouse_detail": _extract_workhouse_detail(row.get("source_payload_json")),
            }
            mention_ids.append(int(row["mention_id"]))
            supporting.extend(evidence)
            conflicting.extend(conflicts)
            if row.get("label") == "CONFIRMED_MATCH":
                linked.append(payload)
            elif row.get("label") == "POSSIBLE_MATCH":
                possible.append(payload)

        ambiguous = len(set(mention_ids)) != len(mention_ids) or len(possible) > 1
        result[record_id] = {
            "record_id": record_id,
            "linked_workhouse_records": linked,
            "possible_workhouse_matches": possible,
            "please_check_records": possible,
            "identity_is_ambiguous": ambiguous,
            "identity_disambiguation_note": (
                "Multiple plausible workhouse links were found for this identity. "
                "Please review the possible matches before treating them as the same person."
                if ambiguous else None
            ),
            "supporting_evidence": sorted(set(supporting)),
            "conflicting_evidence": sorted(set(conflicting)),
        }

    return result


def get_matches_for_record(record_id: str) -> dict[str, Any]:
    resolution = get_record_resolution(record_id)
    matches = resolution["linked_workhouse_records"] + resolution["possible_workhouse_matches"]
    return {
        "record_id": str(record_id),
        "count": len(matches),
        "matches": matches,
        **resolution,
    }


def validation_summary(example_limit: int = 10) -> dict[str, Any]:
    conn = get_db_conn()
    try:
        counts = {
            "source_mentions_created": int(conn.execute("SELECT COUNT(*) AS n FROM source_mentions").fetchone()["n"] or 0),
            "confirmed_links": int(conn.execute("SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'").fetchone()["n"] or 0),
            "possible_links": int(conn.execute("SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE label='POSSIBLE_MATCH'").fetchone()["n"] or 0),
            "weak_candidates": int(conn.execute("SELECT COUNT(*) AS n FROM entity_resolution_candidates WHERE label='WEAK_CANDIDATE'").fetchone()["n"] or 0),
            "requiring_review": int(conn.execute("SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE review_required=1").fetchone()["n"] or 0),
        }
        processed = counts["source_mentions_created"]
        linked_mentions = int(
            conn.execute(
                "SELECT COUNT(DISTINCT mention_id) AS n FROM workhouse_unified_links"
            ).fetchone()["n"]
            or 0
        )
        counts["unresolved_records"] = max(processed - linked_mentions, 0)

        top_ambiguous = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.normalised_name AS name, COUNT(*) AS candidate_count
                FROM entity_resolution_candidates c
                JOIN source_mentions s ON s.id = c.mention_id
                WHERE c.label IN ('CONFIRMED_MATCH', 'POSSIBLE_MATCH')
                GROUP BY s.id, s.normalised_name
                HAVING COUNT(*) > 1
                ORDER BY candidate_count DESC, name
                LIMIT 10
                """
            ).fetchall()
        ]

        confirmed_examples = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.source_record_id AS workhouse_record_id,
                       l.unified_record_id, l.score, l.label,
                       s.raw_name, s.raw_place, s.event_year
                FROM workhouse_unified_links l
                JOIN source_mentions s ON s.id = l.mention_id
                WHERE l.label='CONFIRMED_MATCH'
                ORDER BY l.score DESC
                LIMIT ?
                """,
                (int(example_limit),),
            ).fetchall()
        ]
        possible_examples = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.source_record_id AS workhouse_record_id,
                       l.unified_record_id, l.score, l.label,
                       s.raw_name, s.raw_place, s.event_year
                FROM workhouse_unified_links l
                JOIN source_mentions s ON s.id = l.mention_id
                WHERE l.label='POSSIBLE_MATCH'
                ORDER BY l.score DESC
                LIMIT ?
                """,
                (int(example_limit),),
            ).fetchall()
        ]
        return {
            **counts,
            "top_ambiguous_names": top_ambiguous,
            "confirmed_examples": confirmed_examples,
            "please_check_examples": possible_examples,
        }
    finally:
        conn.close()
