from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.models.census_models import Townland

log = logging.getLogger(__name__)


_LOCATIONAL_QUALIFIERS: frozenset[str] = frozenset(
    {"UPPER", "LOWER", "EAST", "WEST", "NORTH", "SOUTH", "BEG", "MORE"}
)

_TYPE_QUALIFIER_RE = re.compile(
    r"\(\s*(?:civil\s+parish|electoral\s+division|barony|county|townland)\s*\)",
    re.IGNORECASE,
)

MATCH_THRESHOLD_HIGH: float = 0.85
MATCH_THRESHOLD_LOW:  float = 0.40

_ALIAS_MAP: dict[str, str] = {}
_ALIAS_MAP_LOADED: bool = False


def normalize_townland_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""

    s = unicodedata.normalize("NFC", name)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.strip()
    s = " ".join(s.split())
    s = re.sub(r"^[Tt]ownland\s+of\s+", "", s)
    s = _TYPE_QUALIFIER_RE.sub("", s)
    s = re.sub(r"[()]", "", s)
    s = " ".join(s.split())
    s = re.sub(r"[^\w\s\-']", "", s)
    return s.strip().upper()


def extract_qualifier(raw: str) -> Optional[str]:
    m = re.search(r"\(\s*(\w+(?:\s+\w+)?)\s*\)", raw)
    if not m:
        return None
    first = m.group(1).strip().upper().split()[0]
    if first in _LOCATIONAL_QUALIFIERS:
        return m.group(1).strip().upper()
    return None


def resolve_alias(name: str) -> str:
    _ensure_alias_map_loaded()
    return _ALIAS_MAP.get(name, name)


def canonical_name(raw: str) -> str:
    return resolve_alias(normalize_townland_name(raw))


@dataclass
class GeomResult:
    wkt: Optional[str]
    centroid_lat: Optional[float]
    centroid_lon: Optional[float]
    flags: list[str]
    valid: bool


def validate_and_clean_geometry(wkt: Optional[str]) -> GeomResult:
    if not wkt:
        return GeomResult(None, None, None, [], False)

    try:
        from shapely import wkt as _swkt
        from shapely.validation import make_valid
    except ImportError:
        log.warning("townland_service.shapely_unavailable — geometry validation skipped")
        return GeomResult(wkt, None, None, ["shapely_unavailable"], False)

    flags: list[str] = []

    try:
        geom = _swkt.loads(wkt)
    except Exception as exc:
        flags.append("wkt_parse_error")
        log.debug("townland_service.geom_parse_error | %s", exc)
        return GeomResult(None, None, None, flags, False)

    if not geom.is_valid:
        flags.append("geometry_invalid")
        try:
            repaired = make_valid(geom)
            if repaired.is_valid:
                geom = repaired
                flags.append("geometry_repaired_make_valid")
            else:
                buffered = geom.buffer(0)
                if buffered.is_valid:
                    geom = buffered
                    flags.append("geometry_repaired_buffer")
                else:
                    flags.append("geometry_unrecoverable")
                    return GeomResult(wkt, None, None, flags, False)
        except Exception as exc:
            flags.append("geometry_repair_failed")
            log.debug("townland_service.geom_repair_failed | %s", exc)
            return GeomResult(wkt, None, None, flags, False)

    clean_wkt = geom.wkt if any("repaired" in f for f in flags) else wkt

    try:
        rep = geom.representative_point()
        if not rep.within(geom):
            flags.append("representative_point_outside_polygon")
            log.warning(
                "townland_service.centroid_outside_polygon | flags=%s", flags
            )

        centroid = geom.centroid
        if not centroid.within(geom):
            flags.append("geometric_centroid_outside_polygon")

        return GeomResult(
            wkt=clean_wkt,
            centroid_lat=rep.y,
            centroid_lon=rep.x,
            flags=flags,
            valid=True,
        )
    except Exception as exc:
        flags.append("centroid_error")
        log.debug("townland_service.centroid_error | %s", exc)
        return GeomResult(clean_wkt, None, None, flags, False)


@dataclass
class MatchFeatures:
    external_id_match: bool = False
    jaro_winkler:      float = 0.0
    gaelic_similarity: float = 0.0
    same_civil_parish: bool  = False
    same_barony:       bool  = False
    area_ratio:        float = 0.0
    polygon_iou:       float = 0.0

    @property
    def score(self) -> float:
        if self.external_id_match:
            return 0.99
        return (
            self.jaro_winkler        * 0.35
            + self.gaelic_similarity * 0.10
            + (0.15 if self.same_civil_parish else 0.0)
            + (0.10 if self.same_barony       else 0.0)
            + self.area_ratio        * 0.10
            + self.polygon_iou       * 0.20
        )

    @property
    def has_corroboration(self) -> bool:
        return (
            self.external_id_match
            or self.polygon_iou >= 0.8
            or (self.same_civil_parish and self.same_barony and self.area_ratio >= 0.90)
        )

    def to_dict(self) -> dict:
        return {
            "external_id_match":  self.external_id_match,
            "jaro_winkler":       round(self.jaro_winkler, 4),
            "gaelic_similarity":  round(self.gaelic_similarity, 4),
            "same_civil_parish":  self.same_civil_parish,
            "same_barony":        self.same_barony,
            "area_ratio":         round(self.area_ratio, 4),
            "polygon_iou":        round(self.polygon_iou, 4),
            "score":              round(self.score, 4),
            "has_corroboration":  self.has_corroboration,
        }


def score_pair(a: dict, b: dict) -> MatchFeatures:
    from rapidfuzz.distance import JaroWinkler as _JW

    feats = MatchFeatures()

    for id_key in ("osm_id", "osi_id", "vrti_id", "kg_uri", "logainm_id"):
        av, bv = a.get(id_key), b.get(id_key)
        if av and bv and av == bv:
            feats.external_id_match = True
            break

    an = normalize_townland_name(a.get("name") or "")
    bn = normalize_townland_name(b.get("name") or "")
    if an and bn:
        feats.jaro_winkler = _JW.normalized_similarity(an, bn)

    ag = (a.get("name_gaelic") or "").strip().upper()
    bg = (b.get("name_gaelic") or "").strip().upper()
    if ag and bg:
        feats.gaelic_similarity = _JW.normalized_similarity(ag, bg)

    ap = (a.get("civil_parish") or "").strip().upper()
    bp = (b.get("civil_parish") or "").strip().upper()
    feats.same_civil_parish = bool(ap and ap == bp)

    ab = (a.get("barony") or "").strip().upper()
    bb = (b.get("barony") or "").strip().upper()
    feats.same_barony = bool(ab and ab == bb)

    aa, ba_ = a.get("area_sqm"), b.get("area_sqm")
    if aa and ba_ and aa > 0 and ba_ > 0:
        feats.area_ratio = min(aa, ba_) / max(aa, ba_)

    feats.polygon_iou = _polygon_iou(a.get("wkt_geometry"), b.get("wkt_geometry"))

    return feats


def _polygon_iou(wkt_a: Optional[str], wkt_b: Optional[str]) -> float:
    if not wkt_a or not wkt_b:
        return 0.0
    try:
        from shapely import wkt as _swkt
        ga = _swkt.loads(wkt_a)
        gb = _swkt.loads(wkt_b)
        inter = ga.intersection(gb).area
        union = ga.union(gb).area
        return inter / union if union else 0.0
    except Exception:
        return 0.0


def decide_match(features: MatchFeatures) -> str:
    if features.external_id_match:
        return "merge"

    s = features.score
    if s < MATCH_THRESHOLD_LOW:
        return "reject"

    if s >= MATCH_THRESHOLD_HIGH and features.has_corroboration:
        return "merge"

    if features.polygon_iou >= 0.8 and features.jaro_winkler >= 0.80:
        return "merge"

    if (
        features.same_civil_parish
        and features.same_barony
        and features.area_ratio >= 0.90
        and features.jaro_winkler >= 0.90
    ):
        return "merge"

    return "review"


def _block_key(record: dict) -> str:
    name   = normalize_townland_name(record.get("name") or "")
    prefix = name[:3] if len(name) >= 3 else name
    county = (record.get("county") or "").strip().upper()
    ctag   = county[:4] if county else "UNKN"
    return f"{ctag}:{prefix}"


def build_candidate_blocks(records: list[dict]) -> dict[str, list[dict]]:
    raw: dict[str, list[dict]] = {}
    for rec in records:
        raw.setdefault(_block_key(rec), []).append(rec)
    return {k: v for k, v in raw.items() if len(v) >= 2}


def transitive_closure(pairs: list[tuple]) -> list[list]:
    parent: dict = {}

    def _find(x):
        if parent.setdefault(x, x) != x:
            parent[x] = _find(parent[x])
        return parent[x]

    def _union(x, y):
        parent[_find(x)] = _find(y)

    all_ids: set = set()
    for a, b in pairs:
        all_ids.update((a, b))
        _union(a, b)

    clusters: dict = {}
    for node in all_ids:
        clusters.setdefault(_find(node), []).append(node)

    return list(clusters.values())


def pick_canonical(cluster_records: list[dict]) -> dict:
    def _score(r: dict) -> tuple:
        src_rank = {"kg": 2, "json": 1}.get(r.get("source") or "", 0)
        populated = sum(
            1 for v in r.values()
            if v is not None and v != [] and v != ""
        )
        return (src_rank, populated)

    return max(cluster_records, key=_score)


def generate_quality_report() -> dict:
    try:
        from backend.repositories import match_review_repository
        return match_review_repository.quality_summary()
    except Exception as exc:
        log.warning("townland_service.quality_report_failed | error=%s", exc)
        return {"error": str(exc)}


def record_reviewer_decision(
    match_id: int,
    decision: str,
    note: str = "",
) -> None:
    from backend.repositories import match_review_repository
    match_review_repository.apply_decision(match_id, decision, note)


def get_wicklow_townlands() -> dict:
    from backend.repositories import townland_repository, refresh_state_repository
    from backend.config import ActiveConfig
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    db_count = townland_repository.count()

    if db_count > 0:
        state = refresh_state_repository.get(
            "full_ingest", stale_after_days=ActiveConfig.TOWNLAND_STALE_AFTER_DAYS
        )
        cache_status = "hit"
        if state and state.is_stale:
            cache_status = "stale_refresh"
            log.info("townland_service.townlands_stale — serving DB, refresh recommended")
        townlands = [t.to_dict() for t in townland_repository.find_all()]
        return {
            "data": townlands,
            "meta": {
                "source": "database",
                "cache_status": cache_status,
                "generated_at": now_iso,
                "record_count": len(townlands),
            },
        }

    log.warning(
        "townland_service.db_empty — no townlands in database. "
        "Run: python -m coolattin.jobs.full_ingest"
    )
    return {
        "data": [],
        "meta": {
            "source": "database",
            "cache_status": "miss",
            "generated_at": now_iso,
            "record_count": 0,
            "hint": "Run python -m coolattin.jobs.full_ingest to populate the database.",
        },
    }


def build_centroids_from_geojson(geojson_path: Path) -> dict[str, tuple[float, float]]:
    if not geojson_path.exists():
        log.warning("townland_service.geojson_missing | path=%s", geojson_path)
        return {}

    geo = json.loads(geojson_path.read_text(encoding="utf-8"))

    try:
        from shapely.geometry import shape as _shape
        use_shapely = True
    except ImportError:
        use_shapely = False
        log.warning("townland_service.shapely_unavailable — using arithmetic centroid")

    def _arith(coords: list) -> tuple[float, float]:
        ring = coords[0]
        return (
            sum(p[1] for p in ring) / len(ring),
            sum(p[0] for p in ring) / len(ring),
        )

    out: dict[str, tuple[float, float]] = {}
    for feat in geo.get("features", []):
        props = feat.get("properties") or {}
        name  = str(props.get("TL_ENGLISH", "")).strip()
        geom  = feat.get("geometry") or {}
        if not name or not geom:
            continue
        try:
            if use_shapely:
                shp = _shape(geom)
                rep = shp.representative_point()
                if not rep.within(shp):
                    log.warning(
                        "townland_service.centroid_outside_polygon | name=%s", name
                    )
                lat, lon = rep.y, rep.x
            else:
                gtype  = geom.get("type")
                coords = geom.get("coordinates", [])
                if gtype == "Polygon":
                    lat, lon = _arith(coords)
                elif gtype == "MultiPolygon":
                    lat, lon = _arith(coords[0]) if coords else (0, 0)
                else:
                    continue
            out[name] = (lat, lon)
        except Exception as exc:
            log.debug("townland_service.centroid_error | name=%s error=%s", name, exc)

    log.info("townland_service.centroids_built | count=%d", len(out))
    return out


def reconcile_with_reference(townlands: list[Townland]) -> list[Townland]:
    from backend.integrations.townlands_reference import (
        load_wicklow_reference,
        build_name_index,
    )

    refs = load_wicklow_reference()
    if not refs:
        log.warning("townland_service.reconcile — reference empty, skipping enrichment")
        return townlands

    index = build_name_index(refs)
    gaps  = []

    for t in townlands:
        key = normalize_townland_name(t.name)
        ref = index.get(key)
        if ref:
            t.barony           = t.barony           or ref.barony
            t.civil_parish     = t.civil_parish     or ref.civil_parish
            t.electoral_division = t.electoral_division or ref.electoral_division
            if not t.name_gaelic and ref.gaelic_name:
                t.name_gaelic = ref.gaelic_name
        else:
            gaps.append(t.name)
            log.debug("townland_service.reconcile_gap | name=%s", t.name)

    if gaps:
        log.info(
            "townland_service.reconcile | enriched=%d gaps=%d",
            len(townlands) - len(gaps),
            len(gaps),
        )
        _write_reconciliation_gaps(gaps)

    return townlands


def _ensure_alias_map_loaded() -> None:
    global _ALIAS_MAP, _ALIAS_MAP_LOADED
    if _ALIAS_MAP_LOADED:
        return
    from backend.config import ActiveConfig
    alias_path = ActiveConfig.DATA_SEED_DIR / "townland_aliases.json"
    if alias_path.exists():
        try:
            raw = json.loads(alias_path.read_text(encoding="utf-8"))
            _ALIAS_MAP = {
                normalize_townland_name(k): normalize_townland_name(v)
                for k, v in raw.items()
                if not k.startswith("_")
            }
            log.info("townland_service.alias_map_loaded | entries=%d", len(_ALIAS_MAP))
        except Exception as exc:
            log.warning("townland_service.alias_map_load_failed | error=%s", exc)
    _ALIAS_MAP_LOADED = True


def _write_reconciliation_gaps(gaps: list[str]) -> None:
    from backend.config import ActiveConfig
    path = ActiveConfig.DATA_SNAPSHOT_DIR / "reconciliation_gaps.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("townland_name\n")
        for g in gaps:
            f.write(f"{g}\n")
    log.info("townland_service.gaps_written | path=%s count=%d", path, len(gaps))
