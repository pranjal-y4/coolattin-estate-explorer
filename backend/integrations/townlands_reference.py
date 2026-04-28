"""
coolattin/integrations/townlands_reference.py

Wicklow townlands reference integration.

Source: https://www.townlands.ie/wicklow/
Treatment: Reconciliation authority for canonical Wicklow townland names,
           barony, civil parish, and electoral division context.

=================================================================
THIS MODULE READS FROM A LOCAL SEED FILE, NOT FROM LIVE SCRAPING.
=================================================================

The seed file is at:
  coolattin/data/seed/wicklow_townlands_reference.json

It is populated once by running:
  python -m coolattin.jobs.townlands_ingest

After that, all calls to this module read locally.  The KG or web
is only re-queried when the ingest job is run again manually.

Why?
  - townlands.ie does not provide a public JSON API.
  - Scraping on every request is unreliable and rude to the host.
  - The canonical townland list changes rarely.
  - Caching locally avoids a single point of failure.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Populated once by jobs/townlands_ingest.py
REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "wicklow_townlands_reference.json"


@dataclass
class TownlandReference:
    """
    A Wicklow townland as recorded in the townlands.ie reference.
    This is the reconciliation canonical form.
    """
    name: str                           # canonical English name (townlands.ie)
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    electoral_division: Optional[str] = None
    gaelic_name: Optional[str] = None
    area_ha: Optional[float] = None
    townlands_ie_url: Optional[str] = None


def load_wicklow_reference() -> list[TownlandReference]:
    """
    Load the local townlands.ie reference snapshot.

    Returns an empty list (with a warning) if the seed file does not
    exist yet.  Run `python -m coolattin.jobs.townlands_ingest` to
    populate it.
    """
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
    """
    Build a lookup dict keyed by normalised canonical name.
    Used by townland_service for O(1) reconciliation lookups.

    Key is the output of townland_service.normalize_townland_name(),
    so this dict is consistent with all normalisation done elsewhere.
    """
    from backend.services.townland_service import normalize_townland_name
    index: dict[str, TownlandReference] = {}
    for ref in refs:
        key = normalize_townland_name(ref.name)
        if key:
            index[key] = ref
    return index
