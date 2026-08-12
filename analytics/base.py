from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol


@dataclass
class KPI:
    label: str
    value: str
    hint: str = ""


@dataclass
class Chart:
    chart_id: str
    title: str
    type: str  # "bar" | "line" | "doughnut"
    data: Dict[str, Any]
    options: Dict[str, Any] | None = None


@dataclass
class AnalyticsResult:
    dataset_id: str
    dataset_name: str
    description: str
    kpis: List[KPI]
    charts: List[Chart]
    notes: List[str]


class AnalyticsModule(Protocol):
    dataset_id: str
    dataset_name: str
    description: str

    def compute(self) -> AnalyticsResult:
        ...
