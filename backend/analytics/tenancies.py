from __future__ import annotations

from pathlib import Path
import pandas as pd

from .utils import find_data_file
from .base import AnalyticsResult, KPI, Chart


DATA_PATH = find_data_file("tenancies.csv")


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


class TenanciesAnalytics:
    dataset_id = "tenancies"
    dataset_name = "Tenancies"
    description = "Tenancy coverage by townland, year trends (if present), and common surnames (if present)."

    def compute(self) -> AnalyticsResult:
        if not DATA_PATH.exists():
            raise FileNotFoundError(f"Missing file: {DATA_PATH}")

        df = pd.read_csv(DATA_PATH)

        townland_col = _pick_col(df, ["townland", "town", "place", "location"])
        year_col = _pick_col(df, ["year", "date", "start_year", "from", "lease_year"])
        surname_col = _pick_col(df, ["surname", "last_name", "family", "tenant_surname", "name"])
        count_col = _pick_col(df, ["count", "records", "num", "n"])

        if count_col is None:
            df["_count"] = 1
            count_col = "_count"

        total = int(pd.to_numeric(df[count_col], errors="coerce").fillna(0).sum())

        kpis = [
            KPI("Total Tenancy Records", f"{total:,}", "Rows or count-sum"),
            KPI("Detected Townland Column", townland_col or "None"),
            KPI("Detected Year Column", year_col or "None"),
            KPI("Detected Surname Column", surname_col or "None"),
        ]

        charts: list[Chart] = []
        notes: list[str] = []

        if townland_col:
            top_townlands = df.groupby(townland_col)[count_col].sum().sort_values(ascending=False).head(10)
            charts.append(
                Chart(
                    chart_id="tenTopTownlands",
                    title="Top Townlands (Tenancies)",
                    type="bar",
                    data={
                        "labels": [str(x) for x in top_townlands.index.tolist()],
                        "datasets": [{"label": "Tenancies", "data": [float(x) for x in top_townlands.values.tolist()]}],
                    },
                    options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No townland column detected; add `townland` to enable spatial analytics.")

        if year_col:
            years = pd.to_numeric(df[year_col], errors="coerce").dropna().astype(int)
            df2 = df.loc[years.index].copy()
            df2["_year"] = years.values
            by_year = df2.groupby("_year")[count_col].sum().sort_index()
            if len(by_year) > 35:
                by_year = by_year.tail(35)

            charts.append(
                Chart(
                    chart_id="tenYearTrend",
                    title="Tenancies Over Time",
                    type="line",
                    data={
                        "labels": [str(int(y)) for y in by_year.index.tolist()],
                        "datasets": [{"label": "Tenancies", "data": [float(x) for x in by_year.values.tolist()], "tension": 0.25}],
                    },
                    options={"scales": {"y": {"beginAtZero": True}}},
                )
            )
        else:
            notes.append("No year column detected; add `year` to enable timeline chart.")

        if surname_col:
            s = df[surname_col].astype(str).fillna("").str.strip()
            s2 = s.apply(lambda x: x.split()[-1] if " " in x else x).replace("", pd.NA).dropna()
            top_names = s2.value_counts().head(10)

            if len(top_names):
                charts.append(
                    Chart(
                        chart_id="tenTopNames",
                        title="Top Family Names (Surnames)",
                        type="bar",
                        data={
                            "labels": top_names.index.tolist(),
                            "datasets": [{"label": "Occurrences", "data": top_names.values.tolist()}],
                        },
                        options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                    )
                )
        else:
            notes.append("No surname/name column detected; add `surname` to enable family distribution chart.")

        notes.append(f"Loaded from: {DATA_PATH}")

        return AnalyticsResult(self.dataset_id, self.dataset_name, self.description, kpis, charts, notes)


MODULE = TenanciesAnalytics()
