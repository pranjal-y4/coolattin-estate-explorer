from __future__ import annotations
import json, time
from pathlib import Path
from coolattin.services.datahub import DataHub

def main():
    base = Path(__file__).resolve().parent / "coolattin"
    static_data = base / "static" / "data"

    if (static_data / "tenancies.csv").exists():
        hub = DataHub(data_dir=static_data)
    else:
        hub = DataHub(base=base)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "options": hub.options(),
        "stats": hub.stats(),
        "families": hub.query_families(),
        "fuzzy_debug": hub.fuzzy_debug_sample(120),
    }

    out_path = static_data / "precomputed.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ wrote", out_path)

if __name__ == "__main__":
    main()
