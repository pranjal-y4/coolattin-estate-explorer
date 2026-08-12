from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "wicklow_townlands_reference.json"


@dataclass
class TownlandReference:
    name: str
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    electoral_division: Optional[str] = None
    gaelic_name: Optional[str] = None
    area_ha: Optional[float] = None
    townlands_ie_url: Optional[str] = None


def load_wicklow_reference() -> list[TownlandReference]:
    if not REFERENCE_PATH.exists():
        log.warning(
            "townlands_reference.seed_missing — no file at %s. "
            "Run: python -m coolattin.jobs.townlands_ingest",
            REFERENCE_PATH,
        )
        return []

    try:
        with open(REFERENCE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("townlands_reference.load_failed | error=%s", exc)
        return []

    refs = []
    for item in raw:
        refs.append(TownlandReference(
            name=item.get("name", ""),
            barony=item.get("barony"),
            civil_parish=item.get("civil_parish"),
            electoral_division=item.get("electoral_division"),
            gaelic_name=item.get("gaelic_name"),
            area_ha=item.get("area_ha"),
            townlands_ie_url=item.get("url"),
        ))

    log.info("townlands_reference.loaded | count=%d", len(refs))
    return refs


def build_name_index(refs: list[TownlandReference]) -> dict[str, TownlandReference]:
    from backend.services.townland_service import normalize_townland_name
    index: dict[str, TownlandReference] = {}
    for ref in refs:
        key = normalize_townland_name(ref.name)
        if key:
            index[key] = ref
    return index
