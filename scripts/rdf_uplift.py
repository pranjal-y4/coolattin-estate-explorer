"""
scripts/rdf_uplift.py

RDF uplift for the Coolattin Estate Records — D8 prototype.

Reads unified_processed.csv and generates a Turtle (.ttl) RDF graph
representing persons, their townlands, years, and event types.

Usage
-----
  python3 scripts/rdf_uplift.py                      # generate TTL only
  python3 scripts/rdf_uplift.py --import             # generate + POST to GraphDB
  python3 scripts/rdf_uplift.py --limit 500          # sample first 500 rows
  python3 scripts/rdf_uplift.py --repo my-repo-name  # custom GraphDB repo name

Output: data/coolattin_sample.ttl

GraphDB REST endpoint (for --import):
  Reads GRAPHDB_ENDPOINT env var (default: http://localhost:7200)
  Repository name from --repo flag (default: coolattin)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from urllib.parse import quote as _url_quote

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
CSV_PATH = PROJECT_ROOT / "frontend" / "static" / "data" / "unified_processed.csv"
OUT_PATH = PROJECT_ROOT / "data" / "coolattin_sample.ttl"


# ── RDF vocabulary ────────────────────────────────────────────────────────────

PREFIXES = """\
@prefix co:     <https://coolattin.ie/ontology#> .
@prefix ex:     <https://coolattin.ie/resource/> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
"""

# Ontology declaration (minimal)
ONTOLOGY_HEADER = """\
co:Person        a rdfs:Class ; rdfs:label "Person" .
co:Event         a rdfs:Class ; rdfs:label "Event" .
co:townland      a rdf:Property ; rdfs:label "townland" .
co:year          a rdf:Property ; rdfs:label "year" ; rdfs:range xsd:integer .
co:eventType     a rdf:Property ; rdfs:label "eventType" .
co:estate        a rdf:Property ; rdfs:label "estate" .
co:role          a rdf:Property ; rdfs:label "role" .
co:parish        a rdf:Property ; rdfs:label "parish" .
co:occupation    a rdf:Property ; rdfs:label "occupation" .
co:hasEvent      a rdf:Property ; rdfs:label "hasEvent" .
co:forPerson     a rdf:Property ; rdfs:label "forPerson" .
"""


def _safe_uri_local(s: str) -> str:
    """Percent-encode a string for use as a URI local name."""
    return _url_quote(s.strip().replace(" ", "_"), safe="")


def _ttl_str(s: str) -> str:
    """Escape a string for Turtle literal output."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _infer_event_type(row: dict) -> str:
    """Determine primary event type from boolean flags."""
    if row.get("has_emigration_record", "").strip().lower() in ("true", "1", "yes"):
        return "emigration"
    if row.get("has_eviction_record", "").strip().lower() in ("true", "1", "yes"):
        return "eviction"
    if row.get("has_tenancy_record", "").strip().lower() in ("true", "1", "yes"):
        return "tenancy"
    legal = (row.get("legal_action") or "").strip().lower()
    if legal:
        return "eviction"
    return "tenancy"


def generate_ttl(rows: list[dict]) -> str:
    """Convert a list of CSV row dicts into a Turtle string."""
    lines: list[str] = [PREFIXES, ONTOLOGY_HEADER]

    for row in rows:
        rid = (row.get("record_id") or row.get("unique_id_no") or "").strip()
        if not rid:
            continue

        surname = (row.get("surname") or "").strip()
        forename = (row.get("forename") or "").strip()
        townland = (row.get("townland") or row.get("townland_official_name") or "").strip()
        parish = (row.get("parish") or "").strip()
        year_raw = (row.get("year") or "").strip()
        estate = (row.get("estate") or "Fitzwilliam").strip()
        role = (row.get("role") or "").strip()
        occupation = (row.get("occupation") or "").strip()
        event_type = _infer_event_type(row)

        person_uri = f"ex:person_{_safe_uri_local(rid)}"
        event_uri = f"ex:event_{_safe_uri_local(rid)}"

        triples: list[str] = [f"{person_uri}"]
        triples.append(f'  a co:Person')

        if surname:
            triples.append(f'  ; schema:familyName "{_ttl_str(surname)}"')
        if forename:
            triples.append(f'  ; schema:givenName "{_ttl_str(forename)}"')
        if townland:
            triples.append(f'  ; co:townland "{_ttl_str(townland)}"')
        if parish:
            triples.append(f'  ; co:parish "{_ttl_str(parish)}"')
        if estate:
            triples.append(f'  ; co:estate "{_ttl_str(estate)}"')
        if role:
            triples.append(f'  ; co:role "{_ttl_str(role)}"')
        if occupation:
            triples.append(f'  ; co:occupation "{_ttl_str(occupation)}"')
        triples.append(f'  ; co:hasEvent {event_uri}')
        triples.append('.')

        event_triples: list[str] = [f"{event_uri}"]
        event_triples.append(f'  a co:Event')
        event_triples.append(f'  ; co:forPerson {person_uri}')
        event_triples.append(f'  ; co:eventType "{event_type}"')
        if year_raw:
            try:
                year_int = int(float(year_raw))
                event_triples.append(f'  ; co:year {year_int}')
            except ValueError:
                pass
        event_triples.append('.')

        lines.append("\n".join(triples))
        lines.append("\n".join(event_triples))

    return "\n\n".join(lines) + "\n"


def import_to_graphdb(ttl_path: Path, endpoint: str, repo: str) -> None:
    """POST the TTL file to a GraphDB repository via its REST API."""
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' package required for --import. Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    url = f"{endpoint.rstrip('/')}/repositories/{repo}/statements"
    print(f"Importing {ttl_path.name} → {url} …")

    with ttl_path.open("rb") as fh:
        resp = requests.post(
            url,
            data=fh,
            headers={"Content-Type": "text/turtle"},
            timeout=120,
        )
    if resp.status_code in (200, 204):
        print(f"Import successful (HTTP {resp.status_code}).")
    else:
        print(f"Import failed (HTTP {resp.status_code}): {resp.text[:400]}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Turtle RDF from Coolattin unified records.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max rows to include (default: all).")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="POST the generated TTL to GraphDB after writing.")
    parser.add_argument("--repo", default="coolattin",
                        help="GraphDB repository name (default: coolattin).")
    parser.add_argument("--endpoint", default=os.environ.get("GRAPHDB_ENDPOINT", "http://localhost:7200"),
                        help="GraphDB base URL (default: $GRAPHDB_ENDPOINT or http://localhost:7200).")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {CSV_PATH} …")
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        all_rows = list(reader)

    if args.limit and args.limit > 0:
        all_rows = all_rows[: args.limit]

    print(f"Loaded {len(all_rows)} rows. Generating Turtle …")

    ttl_content = generate_ttl(all_rows)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(ttl_content, encoding="utf-8")

    triple_count = ttl_content.count(" .\n") + ttl_content.count(".\n\n")
    print(f"Written {OUT_PATH} (~{len(ttl_content)//1024} KB, ~{triple_count} triples).")

    if args.do_import:
        import_to_graphdb(OUT_PATH, args.endpoint, args.repo)


if __name__ == "__main__":
    main()
