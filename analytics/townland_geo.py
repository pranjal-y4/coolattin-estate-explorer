from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

from .base import AnalyticsResult, KPI, Chart


DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "townlands.json"


class TownlandsGeoAnalytics:
    dataset_id = "townlands_geo"
    dataset_name = "Townlands (GeoJSON)"
    description = "GeoJSON coverage and property readiness for mapping."

    def compute(self) -> AnalyticsResult:
        if not DATA_PATH.exists():
            raise FileNotFoundError(f"Missing file: {DATA_PATH}")

        geo = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        features = geo.get("features", [])
        total = len(features)

        prop_keys = Counter()
        for feat in features:
            props = feat.get("properties", {}) or {}
            for k in props.keys():
                prop_keys[k] += 1

        kpis = [
            KPI("Townland Features", f"{total:,}", "GeoJSON features"),
            KPI("Property Keys", f"{len(prop_keys):,}", "Unique property fields found"),
            KPI("Map Ready", "Yes" if total else "No"),
            KPI("Top Property", prop_keys.most_common(1)[0][0] if prop_keys else "None"),
        ]

        charts: list[Chart] = []
        notes = [
            "If you include a stable join key in properties (e.g., `townland_id`), analytics can join to the map cleanly.",
            f"Top property keys: {', '.join([k for k,_ in prop_keys.most_common(10)])}" if prop_keys else "No properties detected.",
        ]

        return AnalyticsResult(self.dataset_id, self.dataset_name, self.description, kpis, charts, notes)


MODULE = TownlandsGeoAnalytics()
