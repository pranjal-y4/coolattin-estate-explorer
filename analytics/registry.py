from __future__ import annotations

import importlib
from pathlib import Path
from typing import Dict, List, Tuple

from .base import AnalyticsModule


def discover_modules() -> Dict[str, AnalyticsModule]:
    modules: Dict[str, AnalyticsModule] = {}

    analytics_dir = Path(__file__).parent
    package_name = __package__

    for py_file in analytics_dir.glob("*.py"):
        name = py_file.stem
        if name in {"__init__", "base", "registry", "utils"}:
            continue

        mod = importlib.import_module(f"{package_name}.{name}")

        if not hasattr(mod, "MODULE"):
            continue

        module_obj = getattr(mod, "MODULE")
        if not hasattr(module_obj, "dataset_id") or not hasattr(module_obj, "compute"):
            continue

        did = module_obj.dataset_id
        if did in modules:
            raise RuntimeError(
                f"Duplicate dataset_id='{did}' detected.\n"
                f"Fix: ensure each analytics file uses a unique dataset_id.\n"
                f"Conflict: {modules[did].__class__.__name__} vs {module_obj.__class__.__name__}"
            )

        modules[did] = module_obj

    return modules


def list_datasets(modules: Dict[str, AnalyticsModule]) -> List[Tuple[str, str]]:
    items = [(mid, modules[mid].dataset_name) for mid in modules.keys()]
    items.sort(key=lambda x: x[1].lower())
    return items
