from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from backend.models.census_models import CensusRecord
from backend.services.townland_service import canonical_name
from config import ActiveConfig

STANDARD_CENSUS_YEARS = [1841, 1851, 1861, 1871, 1881, 1891]


def load_standard_census_seed_records(
    *,
    allowed_townlands: Optional[set[str]] = None,
    years: Optional[Iterable[int]] = None,
) -> list[CensusRecord]:
    seed_path = _resolve_seed_path()
    if not seed_path:
        return []

    year_list = list(years) if years else list(STANDARD_CENSUS_YEARS)
    year_set = set(year_list)
    grouped: dict[tuple[str, int], dict] = defaultdict(_empty_bucket)

    with seed_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_name = str(row.get("Townland") or "").strip()
            if not raw_name:
                continue

            townland = canonical_name(raw_name)
            if not townland:
                continue
            if allowed_townlands is not None and townland not in allowed_townlands:
                continue

            for year in year_list:
                male = _int_or_none(row.get(f"{year} Male"))
                female = _int_or_none(row.get(f"{year} Female"))
                inhabited = _int_or_none(row.get(f"{year} Inhabited"))
                uninhabited = _int_or_none(row.get(f"{year} Uninhabited"))

                if all(v is None for v in (male, female, inhabited, uninhabited)):
                    continue

                bucket = grouped[(townland, year)]
                if male is not None:
                    bucket["male"] += male
                    bucket["seen_male"] = True
                if female is not None:
                    bucket["female"] += female
                    bucket["seen_female"] = True
                if inhabited is not None:
                    bucket["inhabited"] += inhabited
                    bucket["seen_inhabited"] = True
                if uninhabited is not None:
                    bucket["uninhabited"] += uninhabited
                    bucket["seen_uninhabited"] = True

    records: list[CensusRecord] = []
    for (townland, year), bucket in sorted(grouped.items()):
        if year not in year_set:
            continue
        records.append(
            CensusRecord(
                townland_name=townland,
                year=year,
                male=bucket["male"] if bucket["seen_male"] else None,
                female=bucket["female"] if bucket["seen_female"] else None,
                inhabited=bucket["inhabited"] if bucket["seen_inhabited"] else None,
                uninhabited=bucket["uninhabited"] if bucket["seen_uninhabited"] else None,
                source="csv_seed",
                kg_uri=None,
            )
        )
    return records


def _resolve_seed_path() -> Optional[Path]:
    for candidate in (
        ActiveConfig.STATIC_DATA_DIR / "unified_census.csv",
        ActiveConfig.STATIC_DATA_DIR / "wicklow-census-data.csv",
    ):
        if candidate.exists():
            return candidate
    return None


def _int_or_none(value) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _empty_bucket() -> dict:
    return {
        "male": 0,
        "female": 0,
        "inhabited": 0,
        "uninhabited": 0,
        "seen_male": False,
        "seen_female": False,
        "seen_inhabited": False,
        "seen_uninhabited": False,
    }
