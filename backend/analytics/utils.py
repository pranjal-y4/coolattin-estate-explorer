from __future__ import annotations

import traceback
from pathlib import Path
from typing import Tuple, Optional

from backend.config import BASE_DIR, Config

from .base import AnalyticsResult, AnalyticsModule, KPI


def find_data_file(filename: str) -> Path:
    candidates = [
        Config.STATIC_DATA_DIR / filename,
        BASE_DIR / "data" / filename,
        BASE_DIR / filename,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def safe_compute(module: AnalyticsModule) -> Tuple[Optional[AnalyticsResult], Optional[str]]:
    try:
        return module.compute(), None
    except Exception:
        err = traceback.format_exc()
        placeholder = AnalyticsResult(
            dataset_id=getattr(module, "dataset_id", "unknown"),
            dataset_name=getattr(module, "dataset_name", "Unknown Dataset"),
            description=getattr(module, "description", ""),
            kpis=[KPI("Status", "Error", "This dataset analytics failed to compute")],
            charts=[],
            notes=["Fix the dataset module or CSV schema. See traceback below."],
        )
        return placeholder, err
