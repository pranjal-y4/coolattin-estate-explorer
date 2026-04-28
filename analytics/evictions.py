from __future__ import annotations
import pandas as pd
from pathlib import Path

from .base import AnalyticsResult, KPI, Chart


DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "evictions_records.csv"


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols:
            return cols[cand.lower()]
    for cand in candidates:
        for c in df.columns:
            if cand.lower() in c.lower():
                return c
    return None


class EvictionsAnalytics:
    dataset_id = "evictions"
    dataset_name = "Evictions"
    description = "Eviction patterns by decade and townland."

    def compute(self) -> AnalyticsResult:
        if not DATA_PATH.exists():
            raise FileNotFoundError(f"Missing file: {DATA_PATH}")

        df = pd.read_csv(DATA_PATH)

        townland_col = _pick_col(df, ["townland", "town", "place", "location"])
        year_col = _pick_col(df, ["year", "date", "eviction_year"])
        count_col = _pick_col(df, ["count", "records", "num", "n"])

        if count_col is None:
            df["_count"] = 1
            count_col = "_count"

        total = int(pd.to_numeric(df[count_col], errors="coerce").fillna(0).sum())

        kpis = [
            KPI("Total Records", f"{total:,}"),
            KPI("Detected Townland Column", townland_col or "None"),
            KPI("Detected Year Column", year_col or "None"),
            KPI("Detected Count Column", count_col),
        ]

        charts: list[Chart] = []
        notes: list[str] = []

        if year_col:
            years = pd.to_numeric(df[year_col], errors="coerce").dropna().astype(int)
            df2 = df.loc[years.index].copy()
            df2["_year"] = years.values
            df2["_decade"] = (df2["_year"] // 10) * 10
            by_decade = df2.groupby("_decade")[count_col].sum().sort_index()

            charts.append(
                Chart(
                    chart_id="evictDecade",
                    title="Evictions by Decade",
                    type="line",
                    data={
                        "labels": [f"{int(d)}s" for d in by_decade.index.tolist()],
                        "datasets": [{"label": "Evictions", "data": [float(x) for x in by_decade.values.tolist()], "tension": 0.25}],
                    },
                    options={"scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No year column detected; add `year` to enable timeline chart.")

        if townland_col:
            top = df.groupby(townland_col)[count_col].sum().sort_values(ascending=False).head(10)
            charts.append(
                Chart(
                    chart_id="evictTownlands",
                    title="Top Townlands (Evictions)",
                    type="bar",
                    data={
                        "labels": [str(x) for x in top.index.tolist()],
                        "datasets": [{"label": "Evictions", "data": [float(x) for x in top.values.tolist()]}],
                    },
                    options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No townland column detected; add `townland` to enable townland chart.")

        return AnalyticsResult(self.dataset_id, self.dataset_name, self.description, kpis, charts, notes)


MODULE = EvictionsAnalytics()
