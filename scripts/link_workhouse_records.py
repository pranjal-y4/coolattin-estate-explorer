#!/usr/bin/env python3
"""
Build persisted workhouse-to-unified-record entity-resolution links.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from create_app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Link workhouse records to unified records.")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for workhouse rows.")
    parser.add_argument(
        "--audit-dir",
        default="exports/workhouse_er",
        help="Directory for audit CSV/JSON output.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        from backend.services.workhouse_entity_resolution import link_workhouse_records

        summary = link_workhouse_records(
            limit=args.limit or None,
            audit_dir=Path(args.audit_dir),
        )
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
