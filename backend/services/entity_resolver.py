"""
backend/services/entity_resolver.py

Phase 1 — Shared entity resolution with vector linking.

Public API
----------
resolve_entity(text, entity_type) -> EntityResolution
    Maps a free-text entity mention to its canonical form, SQLite id, and
    VRTI KG URI.  ALL downstream lanes (SQL template compiler, SPARQL
    subgraph engine, fusion reconciler) must call this function and reference
    the same resolved entity — never derive entity identity independently.

Algorithm
---------
1. Alias lookup  — townland_aliases.json contains verified variant→canonical mappings.
2. Exact DB hit  — case-insensitive match against townland.name.
3. Vector search — character-trigram TF-IDF matrix (numpy, no external ML deps).
                   Handles spelling variants, phonetic alternates, and partial names.
4. Rapidfuzz fallback — existing fuzzy scorer for rare edge cases.

The vector index is built lazily on first call and held in module-level memory.
It can be rebuilt by calling invalidate_index() (e.g. after an ingest run).

Entity types
------------
"townland" — resolves to {sql_id, kg_uri, civil_parish, barony, county, lat, lon}
"surname"  — resolves to {sql_id=None, kg_uri=None, occurrence_count}
"year"     — trivial parse, returned as-is

Security note: this module never executes user-supplied SQL.
"""
from __future__ import annotations

import json
import logging
import pickle
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
_ALIASES_PATH = _ROOT / "data" / "seed" / "townland_aliases.json"
_INDEX_PATH = _ROOT / "data" / "entity_index.pkl"

# ── Module-level index cache ──────────────────────────────────────────────────
_index_lock = threading.Lock()
_INDEX_CACHE: dict[str, Any] = {"index": None, "built_at": 0.0}
_INDEX_MAX_AGE = 3600.0  # seconds before a cold rebuild is forced

# Minimum vector-search confidence to return a match
_MIN_VECTOR_SCORE = 0.55
_MIN_EXACT_SCORE = 1.0


# ── EntityResolution ──────────────────────────────────────────────────────────

@dataclass
class EntityResolution:
    """
    Canonical representation of a resolved entity.

    Every downstream pipeline lane (SQL compiler, SPARQL engine, fusion layer)
    receives this object so sql_id and kg_uri are shared, never re-derived.
    """
    label: str | None           # canonical display name  e.g. "Ballinacor"
    label_norm: str | None      # UPPER normalised for SQL  e.g. "BALLINACOR"
    sql_id: int | None          # townland.id in local SQLite
    kg_uri: str | None          # VRTI KG URI  https://kg.virtualtreasury.ie/...
    confidence: float           # 0.0–1.0
    match_type: str             # "exact"|"alias"|"vector"|"fuzzy"|"none"
    entity_type: str            # "townland"|"surname"|"year"
    civil_parish: str | None = None
    barony: str | None = None
    county: str | None = None
    centroid_lat: float | None = None
    centroid_lon: float | None = None
    warning: str | None = None
    candidates: list[dict] = field(default_factory=list)


def _null(entity_type: str = "townland", warning: str | None = None) -> EntityResolution:
    return EntityResolution(
        label=None, label_norm=None, sql_id=None, kg_uri=None,
        confidence=0.0, match_type="none", entity_type=entity_type,
        warning=warning,
    )


# ── Character-trigram vector index ────────────────────────────────────────────

def _trigrams(text: str) -> list[str]:
    """Sliding character trigrams with sentence padding."""
    padded = f"  {text.strip().lower()}  "
    return [padded[i : i + 3] for i in range(len(padded) - 2)]


class _EntityIndex:
    """
    In-memory character-trigram TF-IDF vector index.

    Stores one normalised numpy vector per entity name. Cosine similarity is
    computed as a single matrix-vector dot product at query time — O(n*d).
    For n=5000 entities, d≈500 vocab, this takes < 1 ms.

    No external ML libraries required.
    """
    __slots__ = ("labels", "meta", "matrix", "vocab", "entity_types")

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.meta: list[dict] = []
        self.matrix: np.ndarray | None = None
        self.vocab: dict[str, int] = {}
        self.entity_types: list[str] = []

    # ── Build ──────────────────────────────────────────────────────────────

    def build(self, entries: list[dict]) -> None:
        """
        Build index from a list of entity dicts.
        Required keys: label, entity_type
        Optional keys: label_for_embed, sql_id, kg_uri, civil_parish, barony,
                       county, centroid_lat, centroid_lon
        """
        # Collect vocabulary from all embed texts
        all_tg: set[str] = set()
        for e in entries:
            embed_text = e.get("label_for_embed") or e["label"]
            for tg in _trigrams(embed_text):
                all_tg.add(tg)
        self.vocab = {tg: i for i, tg in enumerate(sorted(all_tg))}

        self.labels = [e["label"] for e in entries]
        self.entity_types = [e.get("entity_type", "townland") for e in entries]
        self.meta = [
            {
                "sql_id": e.get("sql_id"),
                "kg_uri": e.get("kg_uri"),
                "entity_type": e.get("entity_type", "townland"),
                "civil_parish": e.get("civil_parish"),
                "barony": e.get("barony"),
                "county": e.get("county"),
                "centroid_lat": e.get("centroid_lat"),
                "centroid_lon": e.get("centroid_lon"),
            }
            for e in entries
        ]
        rows = [
            self._embed(e.get("label_for_embed") or e["label"])
            for e in entries
        ]
        self.matrix = np.vstack(rows).astype(np.float32)

    # ── Embed ──────────────────────────────────────────────────────────────

    def _embed(self, text: str) -> np.ndarray:
        tgs = _trigrams(text)
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        for tg in tgs:
            idx = self.vocab.get(tg)
            if idx is not None:
                vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec

    # ── Search ─────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        entity_type: str = "townland",
        k: int = 8,
        min_score: float = _MIN_VECTOR_SCORE,
    ) -> list[dict]:
        """
        Return up to k matches sorted by cosine similarity.
        Only returns entities of the requested entity_type.
        """
        if self.matrix is None or not self.labels:
            return []

        qvec = self._embed(query).astype(np.float32)
        if qvec.sum() == 0.0:
            return []

        scores: np.ndarray = self.matrix @ qvec  # cosine sim

        # Zero out wrong entity types so they don't pollute top-k
        type_mask = np.array(
            [et == entity_type for et in self.entity_types], dtype=np.float32
        )
        scores = scores * type_mask + (type_mask - 1.0)  # masked → -1.0

        top_idx = np.argsort(scores)[::-1][:k]
        results = []
        for idx in top_idx:
            sc = float(scores[idx])
            if sc < min_score:
                break
            results.append(
                {
                    "label": self.labels[idx],
                    "score": round(sc, 4),
                    **self.meta[int(idx)],
                }
            )
        return results

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=4)
        log.info("entity_resolver.index_saved path=%s entries=%d", path, len(self.labels))

    @classmethod
    def load(cls, path: Path) -> "_EntityIndex":
        with open(path, "rb") as f:
            obj = pickle.load(f)
        log.info("entity_resolver.index_loaded path=%s entries=%d", path, len(obj.labels))
        return obj


# ── Catalog builders ──────────────────────────────────────────────────────────

def _load_aliases() -> dict[str, str]:
    """Return {variant_upper: canonical_upper} from townland_aliases.json."""
    try:
        raw: dict[str, str] = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
        return {k.upper(): v.upper() for k, v in raw.items()}
    except Exception as exc:
        log.warning("entity_resolver.aliases_load_failed error=%s", exc)
        return {}


def _build_catalog() -> list[dict]:
    """
    Fetch all townlands and top surnames from the DB.
    Returns a list of entity dicts ready for _EntityIndex.build().
    """
    from extensions import get_db_conn

    conn = get_db_conn()
    entries: list[dict] = []
    try:
        # ── Townlands ──────────────────────────────────────────────────────
        rows = conn.execute(
            """
            SELECT
              id, name, name_gaelic, civil_parish, barony, county,
              centroid_lat, centroid_lon, kg_uri
            FROM townland
            WHERE name IS NOT NULL AND TRIM(name) != ''
            """
        ).fetchall()
        for row in rows:
            name: str = row["name"]
            gaelic: str | None = row["name_gaelic"]
            # Primary entry
            entries.append(
                {
                    "label": name,
                    "label_for_embed": name,
                    "entity_type": "townland",
                    "sql_id": row["id"],
                    "kg_uri": row["kg_uri"],
                    "civil_parish": row["civil_parish"],
                    "barony": row["barony"],
                    "county": row["county"],
                    "centroid_lat": row["centroid_lat"],
                    "centroid_lon": row["centroid_lon"],
                }
            )
            # Gaelic name as alternate embedding (same sql_id / kg_uri)
            if gaelic and gaelic.strip() and gaelic.strip().lower() != name.strip().lower():
                entries.append(
                    {
                        "label": name,               # canonical label stays English
                        "label_for_embed": gaelic,   # but embed the Gaelic form
                        "entity_type": "townland",
                        "sql_id": row["id"],
                        "kg_uri": row["kg_uri"],
                        "civil_parish": row["civil_parish"],
                        "barony": row["barony"],
                        "county": row["county"],
                        "centroid_lat": row["centroid_lat"],
                        "centroid_lon": row["centroid_lon"],
                    }
                )

        # ── Top surnames ───────────────────────────────────────────────────
        surname_rows = conn.execute(
            """
            SELECT UPPER(surname) AS surname, COUNT(DISTINCT record_id) AS n
            FROM unified_record
            WHERE surname IS NOT NULL AND TRIM(surname) != ''
            GROUP BY UPPER(surname)
            ORDER BY n DESC
            LIMIT 2000
            """
        ).fetchall()
        for srow in surname_rows:
            sname: str = srow["surname"]
            entries.append(
                {
                    "label": sname.title(),
                    "label_for_embed": sname,
                    "entity_type": "surname",
                    "sql_id": None,
                    "kg_uri": None,
                    "occurrence_count": srow["n"],
                }
            )
    finally:
        conn.close()

    log.info(
        "entity_resolver.catalog_built townlands=%d surnames=%d total=%d",
        sum(1 for e in entries if e["entity_type"] == "townland"),
        sum(1 for e in entries if e["entity_type"] == "surname"),
        len(entries),
    )
    return entries


# ── Index lifecycle ────────────────────────────────────────────────────────────

def _get_index() -> _EntityIndex | None:
    """
    Return the cached entity index, building it if necessary.
    Thread-safe. Never raises — returns None on failure.
    """
    now = time.time()

    with _index_lock:
        cached = _INDEX_CACHE.get("index")
        built_at = float(_INDEX_CACHE.get("built_at") or 0.0)
        if cached is not None and (now - built_at) < _INDEX_MAX_AGE:
            return cached  # type: ignore[return-value]

    # Try loading from disk first (fast path on restart)
    if _INDEX_PATH.exists():
        try:
            age = now - _INDEX_PATH.stat().st_mtime
            if age < _INDEX_MAX_AGE:
                idx = _EntityIndex.load(_INDEX_PATH)
                with _index_lock:
                    _INDEX_CACHE["index"] = idx
                    _INDEX_CACHE["built_at"] = now
                return idx
        except Exception as exc:
            log.warning("entity_resolver.index_load_failed path=%s error=%s", _INDEX_PATH, exc)

    # Build from scratch
    try:
        t0 = time.perf_counter()
        catalog = _build_catalog()
        aliases = _load_aliases()
        # Expand aliases into catalog: each alias variant → same sql_id/kg_uri as canonical
        alias_entries = _expand_aliases(aliases, catalog)
        all_entries = catalog + alias_entries
        idx = _EntityIndex()
        idx.build(all_entries)
        elapsed = int((time.perf_counter() - t0) * 1000)
        log.info("entity_resolver.index_built entries=%d ms=%d", len(all_entries), elapsed)

        # Persist to disk
        try:
            idx.save(_INDEX_PATH)
        except Exception as exc:
            log.warning("entity_resolver.index_save_failed error=%s", exc)

        with _index_lock:
            _INDEX_CACHE["index"] = idx
            _INDEX_CACHE["built_at"] = now
        return idx
    except Exception as exc:
        log.error("entity_resolver.index_build_failed error=%s", exc)
        return None


def _expand_aliases(
    aliases: dict[str, str], catalog: list[dict]
) -> list[dict]:
    """
    For each alias variant, create an entry that embeds the variant text
    but carries the canonical entity's sql_id, kg_uri, etc.
    """
    # Build lookup from label_norm → catalog entry
    norm_to_entry: dict[str, dict] = {}
    for e in catalog:
        if e.get("entity_type") != "townland":
            continue
        norm = e["label"].upper()
        if norm not in norm_to_entry:
            norm_to_entry[norm] = e

    extra: list[dict] = []
    for variant_upper, canonical_upper in aliases.items():
        canonical_entry = norm_to_entry.get(canonical_upper)
        if canonical_entry is None:
            continue
        # Don't duplicate if variant IS the canonical
        if variant_upper == canonical_upper:
            continue
        extra.append(
            {
                "label": canonical_entry["label"],        # canonical label
                "label_for_embed": variant_upper.title(), # embed variant spelling
                "entity_type": "townland",
                "sql_id": canonical_entry["sql_id"],
                "kg_uri": canonical_entry.get("kg_uri"),
                "civil_parish": canonical_entry.get("civil_parish"),
                "barony": canonical_entry.get("barony"),
                "county": canonical_entry.get("county"),
                "centroid_lat": canonical_entry.get("centroid_lat"),
                "centroid_lon": canonical_entry.get("centroid_lon"),
            }
        )
    return extra


def invalidate_index() -> None:
    """Force a cold rebuild on the next call to resolve_entity()."""
    with _index_lock:
        _INDEX_CACHE["index"] = None
        _INDEX_CACHE["built_at"] = 0.0
    try:
        if _INDEX_PATH.exists():
            _INDEX_PATH.unlink()
    except Exception:
        pass
    log.info("entity_resolver.index_invalidated")


# ── Normalization helpers ──────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Uppercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


# ── Townland resolution ───────────────────────────────────────────────────────

def _resolve_townland(
    raw: str,
    min_confidence: float = _MIN_VECTOR_SCORE,
) -> EntityResolution:
    """
    Resolve a townland mention to its canonical entity.

    Resolution order:
      1. Exact UPPER match against the DB (confidence 1.0)
      2. Alias lookup  (confidence 0.98)
      3. Vector search (confidence = cosine score, ≥ min_confidence)
      4. Rapidfuzz fallback if vector unavailable (confidence ≈ ratio)
    """
    from extensions import get_db_conn

    raw_norm = _norm(raw)
    if not raw_norm:
        return _null("townland")

    aliases = _load_aliases()

    # ── Step 1: Exact DB match ────────────────────────────────────────────
    canonical_norm = aliases.get(raw_norm, raw_norm)
    conn = get_db_conn()
    try:
        row = conn.execute(
            """
            SELECT id, name, name_gaelic, civil_parish, barony, county,
                   centroid_lat, centroid_lon, kg_uri
            FROM townland
            WHERE UPPER(name) = ?
            LIMIT 1
            """,
            (canonical_norm,),
        ).fetchone()
    finally:
        conn.close()

    match_type = "exact" if canonical_norm == raw_norm else "alias"
    if row:
        return EntityResolution(
            label=row["name"],
            label_norm=row["name"].upper(),
            sql_id=int(row["id"]),
            kg_uri=row["kg_uri"],
            confidence=1.0 if match_type == "exact" else 0.98,
            match_type=match_type,
            entity_type="townland",
            civil_parish=row["civil_parish"],
            barony=row["barony"],
            county=row["county"],
            centroid_lat=row["centroid_lat"],
            centroid_lon=row["centroid_lon"],
        )

    # ── Step 2: Vector search ─────────────────────────────────────────────
    idx = _get_index()
    if idx is not None:
        hits = idx.search(raw, entity_type="townland", k=5, min_score=min_confidence)
        if hits:
            best = hits[0]
            confidence = float(best["score"])
            warning = None
            if confidence < 0.85:
                warning = (
                    f"'{raw}' was not found exactly. "
                    f"Using '{best['label']}' (vector similarity {confidence:.2f})."
                )
            return EntityResolution(
                label=best["label"],
                label_norm=best["label"].upper(),
                sql_id=best.get("sql_id"),
                kg_uri=best.get("kg_uri"),
                confidence=confidence,
                match_type="vector",
                entity_type="townland",
                civil_parish=best.get("civil_parish"),
                barony=best.get("barony"),
                county=best.get("county"),
                centroid_lat=best.get("centroid_lat"),
                centroid_lon=best.get("centroid_lon"),
                warning=warning,
                candidates=[
                    {"label": h["label"], "score": h["score"], "sql_id": h.get("sql_id")}
                    for h in hits[1:4]
                ],
            )

    # ── Step 3: Rapidfuzz fallback ────────────────────────────────────────
    try:
        from rapidfuzz import fuzz as _fuzz
        conn2 = get_db_conn()
        try:
            all_rows = conn2.execute(
                "SELECT id, name, civil_parish, barony, county, centroid_lat, centroid_lon, kg_uri FROM townland WHERE name IS NOT NULL LIMIT 5000"
            ).fetchall()
        finally:
            conn2.close()

        best_score = 0.0
        best_row = None
        for trow in all_rows:
            sc = float(_fuzz.token_sort_ratio(raw_norm, trow["name"].upper())) / 100.0
            if sc > best_score:
                best_score = sc
                best_row = trow

        if best_row and best_score >= min_confidence:
            return EntityResolution(
                label=best_row["name"],
                label_norm=best_row["name"].upper(),
                sql_id=int(best_row["id"]),
                kg_uri=best_row["kg_uri"],
                confidence=best_score,
                match_type="fuzzy",
                entity_type="townland",
                civil_parish=best_row["civil_parish"],
                barony=best_row["barony"],
                county=best_row["county"],
                centroid_lat=best_row["centroid_lat"],
                centroid_lon=best_row["centroid_lon"],
                warning=f"'{raw}' fuzzy-matched to '{best_row['name']}' ({best_score:.2f}).",
            )
    except Exception as exc:
        log.debug("entity_resolver.fuzzy_fallback_failed error=%s", exc)

    return _null("townland", warning=f"Could not resolve townland '{raw}'.")


# ── Surname resolution ─────────────────────────────────────────────────────────

def _resolve_surname(raw: str) -> EntityResolution:
    """
    Resolve a surname mention. Surnames do not have SQL ids or KG URIs in VRTI.
    Returns the normalized label and a confidence based on DB occurrence count.
    """
    from extensions import get_db_conn

    raw_norm = _norm(raw)
    if not raw_norm:
        return _null("surname")

    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE UPPER(surname)=?",
            (raw_norm,),
        ).fetchone()
    finally:
        conn.close()

    n = int(row["n"]) if row else 0
    if n > 0:
        return EntityResolution(
            label=raw_norm.title(),
            label_norm=raw_norm,
            sql_id=None,
            kg_uri=None,
            confidence=1.0,
            match_type="exact",
            entity_type="surname",
        )

    # Try vector search for surnames
    idx = _get_index()
    if idx is not None:
        hits = idx.search(raw, entity_type="surname", k=3, min_score=0.6)
        if hits:
            best = hits[0]
            return EntityResolution(
                label=best["label"],
                label_norm=best["label"].upper(),
                sql_id=None,
                kg_uri=None,
                confidence=float(best["score"]),
                match_type="vector",
                entity_type="surname",
                warning=f"Surname '{raw}' not found. Did you mean '{best['label']}'?",
                candidates=[
                    {"label": h["label"], "score": h["score"]}
                    for h in hits[1:3]
                ],
            )

    return _null("surname", warning=f"Surname '{raw}' not found in estate records.")


# ── Year resolution ────────────────────────────────────────────────────────────

def _resolve_year(raw: str) -> EntityResolution:
    m = re.search(r"\b(18[0-9]{2}|19[0-2][0-9])\b", raw)
    if m:
        yr = m.group(1)
        return EntityResolution(
            label=yr, label_norm=yr, sql_id=None, kg_uri=None,
            confidence=1.0, match_type="exact", entity_type="year",
        )
    return _null("year", warning=f"Could not parse year from '{raw}'.")


# ── Public entry point ────────────────────────────────────────────────────────

def resolve_entity(
    text: str,
    entity_type: str = "townland",
    *,
    min_confidence: float = _MIN_VECTOR_SCORE,
) -> EntityResolution:
    """
    Resolve a free-text entity mention to its canonical form.

    Parameters
    ----------
    text          : The raw text to resolve (townland name, surname, or year).
    entity_type   : "townland" | "surname" | "year".
    min_confidence: Minimum confidence to return a non-null result.

    Returns
    -------
    EntityResolution — never raises; returns match_type="none" on failure.
    """
    raw = (text or "").strip()
    if not raw:
        return _null(entity_type)
    try:
        if entity_type == "year":
            return _resolve_year(raw)
        if entity_type == "surname":
            return _resolve_surname(raw)
        return _resolve_townland(raw, min_confidence=min_confidence)
    except Exception as exc:
        log.warning("entity_resolver.resolve_failed entity_type=%s text=%r error=%s", entity_type, text, exc)
        return _null(entity_type, warning=f"Entity resolution failed: {exc}")
