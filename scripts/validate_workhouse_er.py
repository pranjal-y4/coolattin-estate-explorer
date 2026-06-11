#!/usr/bin/env python3
"""
Print a validation summary for persisted workhouse entity-resolution results.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from create_app import create_app


def main() -> int:
    app = create_app()
    with app.app_context():
        from backend.services.workhouse_entity_resolution import validation_summary

        summary = validation_summary(example_limit=10)
        print(f"workhouse_records_processed: {summary.get('source_mentions_created', 0)}")
        print(f"source_mentions_created: {summary.get('source_mentions_created', 0)}")
        print(f"confirmed_links: {summary.get('confirmed_links', 0)}")
        print(f"possible_links: {summary.get('possible_links', 0)}")
        print(f"weak_candidates: {summary.get('weak_candidates', 0)}")
        print(f"unresolved_records: {summary.get('unresolved_records', 0)}")
        print(f"requiring_review: {summary.get('requiring_review', 0)}")
        print("top_ambiguous_names:")
        for item in summary.get("top_ambiguous_names", []):
            print(f"  - {item.get('name')}: {item.get('candidate_count')}")
        print("example_confirmed_matches:")
        for item in summary.get("confirmed_examples", []):
            print(
                f"  - {item.get('raw_name')} -> {item.get('unified_record_id')} "
                f"({item.get('score')}, {item.get('label')})"
            )
        print("example_please_check_records:")
        for item in summary.get("please_check_examples", []):
            print(
                f"  - {item.get('raw_name')} -> {item.get('unified_record_id')} "
                f"({item.get('score')}, {item.get('label')})"
            )
        print("precision_recall: no golden set configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
