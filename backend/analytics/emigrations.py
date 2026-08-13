from __future__ import annotations
import os
import pandas as pd
from pathlib import Path

from .utils import find_data_file
from .base import AnalyticsResult, KPI, Chart


DATA_PATH = find_data_file("emigrations_records.csv")


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


class EmigrationAnalytics:
    dataset_id = "emigration"
    dataset_name = "Emigration"
    description = "Emigration trends over time and top destinations."

    def compute(self) -> AnalyticsResult:
        if not DATA_PATH.exists():
            raise FileNotFoundError(f"Missing file: {DATA_PATH}")

        df = pd.read_csv(DATA_PATH)

        year_col = _pick_col(df, ["year", "date", "departure_year", "emigration_year"])
        dest_col = _pick_col(df, ["destination", "destination_country", "country", "to"])
        count_col = _pick_col(df, ["count", "records", "num", "n"])

        if count_col is None:
            df["_count"] = 1
            count_col = "_count"

        total = int(pd.to_numeric(df[count_col], errors="coerce").fillna(0).sum())

        kpis = [
            KPI("Total Records", f"{total:,}", "Total emigration rows (or count sum)"),
            KPI("Detected Year Column", year_col or "None"),
            KPI("Detected Destination Column", dest_col or "None"),
            KPI("Detected Count Column", count_col),
        ]

        charts: list[Chart] = []
        notes: list[str] = []

        if year_col:
            years = pd.to_numeric(df[year_col], errors="coerce").dropna().astype(int)
            df2 = df.loc[years.index].copy()
            df2["_year"] = years.values
            by_year = df2.groupby("_year")[count_col].sum().sort_index()
            if len(by_year) > 35:
                by_year = by_year.tail(35)

            charts.append(
                Chart(
                    chart_id="emigYear",
                    title="Emigration Over Time",
                    type="line",
                    data={
                        "labels": [str(int(y)) for y in by_year.index.tolist()],
                        "datasets": [{"label": "Emigration", "data": [float(x) for x in by_year.values.tolist()], "tension": 0.25}],
                    },
                    options={"scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No year column detected; add a `year` field to enable timeline chart.")

        if dest_col:
            top = df.groupby(dest_col)[count_col].sum().sort_values(ascending=False).head(10)
            charts.append(
                Chart(
                    chart_id="emigDest",
                    title="Top Destinations",
                    type="bar",
                    data={
                        "labels": [str(x) for x in top.index.tolist()],
                        "datasets": [{"label": "Records", "data": [float(x) for x in top.values.tolist()]}],
                    },
                    options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No destination column detected; add `destination_country` or similar.")

        notes.append("These charts are computed from your real file in /data/emigrations_records.csv.")
        return AnalyticsResult(self.dataset_id, self.dataset_name, self.description, kpis, charts, notes)


MODULE = EmigrationAnalytics()
