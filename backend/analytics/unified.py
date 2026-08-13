from __future__ import annotations

from pathlib import Path
import pandas as pd

from .utils import find_data_file
from .base import AnalyticsResult, KPI, Chart


DATA_PATH = find_data_file("unified_processed.csv")


class UnifiedAnalytics:
    dataset_id = "unified"
    dataset_name = "Unified Database"
    description = "Complete historical records from all sources combined into single dataset"

    def compute(self) -> AnalyticsResult:
        if not DATA_PATH.exists():
            raise FileNotFoundError(f"Missing file: {DATA_PATH}")

        df = pd.read_csv(DATA_PATH)

        total = len(df)
        surname_count = df['surname'].notna().sum()
        forename_count = df['forename'].notna().sum()
        townland_count = df['townland'].notna().sum()
        year_count = df['year'].notna().sum()
        estate_count = df['estate'].notna().sum()

        kpis = [
            KPI("Total Records", f"{total:,}", "All source records combined"),
            KPI("Unique Surnames", f"{df['surname'].nunique():,}", "Distinct family names"),
            KPI("Unique Townlands", f"{df['townland'].nunique():,}", "Distinct locations"),
            KPI("Records with Year", f"{year_count:,} ({100*year_count/total:.0f}%)", "Dated records"),
            KPI("Records with Estate", f"{estate_count:,} ({100*estate_count/total:.0f}%)", "Estate information"),
        ]

        charts: list[Chart] = []
        notes: list[str] = []

        if 'year' in df.columns:
            years = pd.to_numeric(df['year'], errors='coerce').dropna().astype(int)
            if len(years) > 0:
                year_dist = years.value_counts().sort_index()
                charts.append(
                    Chart(
                        chart_id="unifiedYearTrend",
                        title="Records Over Time",
                        type="line",
                        data={
                            "labels": [str(int(y)) for y in year_dist.index.tolist()],
                            "datasets": [{"label": "Records", "data": [int(x) for x in year_dist.values.tolist()], "tension": 0.25}],
                        },
                        options={"scales": {"y": {"beginAtZero": True}}},
                    )
                )

        if 'surname' in df.columns:
            top_surnames = df['surname'].dropna().value_counts().head(15)
            if len(top_surnames) > 0:
                charts.append(
                    Chart(
                        chart_id="unifiedTopSurnames",
                        title="Top Family Names",
                        type="bar",
                        data={
                            "labels": top_surnames.index.tolist(),
                            "datasets": [{"label": "Occurrences", "data": [int(x) for x in top_surnames.values.tolist()]}],
                        },
                        options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                    )
                )

        if 'townland' in df.columns:
            top_townlands = df['townland'].dropna().value_counts().head(15)
            if len(top_townlands) > 0:
                charts.append(
                    Chart(
                        chart_id="unifiedTopTownlands",
                        title="Top Townlands",
                        type="bar",
                        data={
                            "labels": top_townlands.index.tolist(),
                            "datasets": [{"label": "Records", "data": [int(x) for x in top_townlands.values.tolist()]}],
                        },
                        options={"plugins": {"legend": {"display": False}}, "scales": {"y": {"beginAtZero": True}}},
                    )
                )

        if 'estate' in df.columns:
            estates = df['estate'].dropna().value_counts()
            if len(estates) > 0:
                charts.append(
                    Chart(
                        chart_id="unifiedEstates",
                        title="Records by Estate",
                        type="doughnut",
                        data={
                            "labels": estates.index.tolist()[:10],
                            "datasets": [{"data": [int(x) for x in estates.values.tolist()[:10]]}],
                        },
                        options={"plugins": {"legend": {"position": "right"}}},
                    )
                )

        if 'gender' in df.columns:
            genders = df['gender'].dropna().value_counts()
            if len(genders) > 0:
                charts.append(
                    Chart(
                        chart_id="unifiedGender",
                        title="Gender Distribution",
                        type="doughnut",
                        data={
                            "labels": genders.index.tolist(),
                            "datasets": [{"data": [int(x) for x in genders.values.tolist()]}],
                        },
                        options={"plugins": {"legend": {"position": "right"}}},
                    )
                )

        notes.append(f"Data source: {DATA_PATH}")
        notes.append(f"Preprocessed and cleaned from unified database")

        return AnalyticsResult(self.dataset_id, self.dataset_name, self.description, kpis, charts, notes)


MODULE = UnifiedAnalytics()
