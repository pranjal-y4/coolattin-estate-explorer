from __future__ import annotations

import traceback
from typing import Tuple, Optional

from .base import AnalyticsResult, AnalyticsModule, KPI


def safe_compute(module: AnalyticsModule) -> Tuple[Optional[AnalyticsResult], Optional[str]]:
    """
    Runs module.compute() safely. Returns (result, error_string).
    If compute fails, error_string is shown on the analytics page.
    """
    try:
        return module.compute(), None
    except Exception:
        err = traceback.format_exc()
        # Return a minimal placeholder result so template can render
        placeholder = AnalyticsResult(
            dataset_id=getattr(module, "dataset_id", "unknown"),
            dataset_name=getattr(module, "dataset_name", "Unknown Dataset"),
            description=getattr(module, "description", ""),
            kpis=[KPI("Status", "Error", "This dataset analytics failed to compute")],
            charts=[],
            notes=["Fix the dataset module or CSV schema. See traceback below."],
        )
        return placeholder, err
