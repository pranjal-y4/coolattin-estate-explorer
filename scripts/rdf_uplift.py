from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote as _url_quote

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
DB_PATH = PROJECT_ROOT / "coolattin.db"
OUT_PATH = PROJECT_ROOT / "data" / "seed" / "coolattin.ttl"


PREFIXES = """\
@prefix co:     <https://coolattin.ie/ontology#> .
@prefix ex:     <https://coolattin.ie/resource/> .
@prefix schema: <https://schema.org/> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .
@prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
"""

ONTOLOGY_HEADER = """\
co:Person        a rdfs:Class ; rdfs:label "Person" .
co:Event         a rdfs:Class ; rdfs:label "Event" .
co:Townland      a rdfs:Class ; rdfs:label "Townland" .
co:CensusRecord  a rdfs:Class ; rdfs:label "CensusRecord" .
co:Clearance     a rdfs:Class ; rdfs:label "Clearance" .

co:townland      a rdf:Property ; rdfs:label "townland" .
co:year          a rdf:Property ; rdfs:label "year" ; rdfs:range xsd:integer .
co:eventType     a rdf:Property ; rdfs:label "eventType" .
co:estate        a rdf:Property ; rdfs:label "estate" .
co:role          a rdf:Property ; rdfs:label "role" .
co:parish        a rdf:Property ; rdfs:label "parish" .
co:occupation    a rdf:Property ; rdfs:label "occupation" .
co:hasEvent      a rdf:Property ; rdfs:label "hasEvent" .
co:forPerson     a rdf:Property ; rdfs:label "forPerson" .
co:civilParish   a rdf:Property ; rdfs:label "civilParish" .
co:barony        a rdf:Property ; rdfs:label "barony" .
co:county        a rdf:Property ; rdfs:label "county" .
co:totalPopulation a rdf:Property ; rdfs:label "totalPopulation" ; rdfs:range xsd:integer .
co:forTownland   a rdf:Property ; rdfs:label "forTownland" .
co:count         a rdf:Property ; rdfs:label "count" ; rdfs:range xsd:integer .
"""


def _safe_uri(s: str) -> str:
    return _url_quote(str(s).strip().replace(" ", "_"), safe="")


def _ttl_str(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _infer_event_type(row: sqlite3.Row) -> str:
    if row["has_emigration_record"]:
        return "emigration"
    if row["has_eviction_record"]:
        return "eviction"
    return "tenancy"


def _person_event_blocks(rows: list[sqlite3.Row]) -> list[str]:
    blocks: list[str] = []
    for row in rows:
        rid = (row["record_id"] or row["unique_id_no"] or "").strip()
        if not rid:
            continue

        person_uri = f"ex:person_{_safe_uri(rid)}"
        event_uri  = f"ex:event_{_safe_uri(rid)}"

        p: list[str] = [person_uri, "  a co:Person"]
        if row["surname"]:
            p.append(f'  ; schema:familyName "{_ttl_str(row["surname"])}"')
        if row["forename"]:
            p.append(f'  ; schema:givenName "{_ttl_str(row["forename"])}"')
        if row["townland"]:
            p.append(f'  ; co:townland "{_ttl_str(row["townland"])}"')
        if row["parish"]:
            p.append(f'  ; co:parish "{_ttl_str(row["parish"])}"')
        estate = row["estate"] or "Fitzwilliam"
        p.append(f'  ; co:estate "{_ttl_str(estate)}"')
        if row["role"]:
            p.append(f'  ; co:role "{_ttl_str(row["role"])}"')
        if row["occupation"]:
            p.append(f'  ; co:occupation "{_ttl_str(row["occupation"])}"')
        p.append(f"  ; co:hasEvent {event_uri}")
        p.append(".")
        blocks.append("\n".join(p))

        ev: list[str] = [event_uri, "  a co:Event"]
        ev.append(f"  ; co:forPerson {person_uri}")
        ev.append(f'  ; co:eventType "{_infer_event_type(row)}"')
        if row["year"]:
            ev.append(f"  ; co:year {int(row['year'])}")
        ev.append(".")
        blocks.append("\n".join(ev))

    return blocks


def _townland_blocks(rows: list[sqlite3.Row]) -> list[str]:
    blocks: list[str] = []
    for row in rows:
        tid = row["id"]
        name = (row["name"] or "").strip()
        if not name:
            continue

        t_uri = f"ex:townland_{tid}"
        t: list[str] = [t_uri, "  a co:Townland"]
        t.append(f'  ; rdfs:label "{_ttl_str(name)}"')
        if row["civil_parish"]:
            t.append(f'  ; co:civilParish "{_ttl_str(row["civil_parish"])}"')
        if row["barony"]:
            t.append(f'  ; co:barony "{_ttl_str(row["barony"])}"')
        if row["county"]:
            t.append(f'  ; co:county "{_ttl_str(row["county"])}"')
        t.append(".")
        blocks.append("\n".join(t))
    return blocks


def _census_blocks(rows: list[sqlite3.Row]) -> list[str]:
    blocks: list[str] = []
    for row in rows:
        cid = row["id"]
        if row["total"] is None:
            continue

        c_uri = f"ex:census_{cid}"
        c: list[str] = [c_uri, "  a co:CensusRecord"]
        c.append(f"  ; co:year {int(row['year'])}")
        c.append(f"  ; co:totalPopulation {int(row['total'])}")
        c.append(f"  ; co:forTownland ex:townland_{row['townland_id']}")
        c.append(".")
        blocks.append("\n".join(c))
    return blocks


def _clearance_blocks(rows: list[sqlite3.Row]) -> list[str]:
    blocks: list[str] = []
    for row in rows:
        rid = row["id"]
        if row["count"] is None:
            continue

        cl_uri = f"ex:clearance_{rid}"
        cl: list[str] = [cl_uri, "  a co:Clearance"]
        cl.append(f"  ; co:year {int(row['year'])}")
        cl.append(f"  ; co:count {int(row['count'])}")
        cl.append(f"  ; co:forTownland ex:townland_{row['townland_id']}")
        cl.append(".")
        blocks.append("\n".join(cl))
    return blocks


def generate_ttl(db_path: Path, limit: int = 0) -> str:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    with conn:
        unified = conn.execute(
            "SELECT * FROM unified_record ORDER BY id"
        ).fetchall()
        if limit > 0:
            unified = unified[:limit]

        townlands  = conn.execute("SELECT * FROM townland ORDER BY id").fetchall()
        census     = conn.execute("SELECT * FROM census_record ORDER BY id").fetchall()
        clearances = conn.execute("SELECT * FROM clearances_record ORDER BY id").fetchall()

    conn.close()

    blocks: list[str] = [PREFIXES, ONTOLOGY_HEADER]
    blocks.extend(_person_event_blocks(unified))
    blocks.extend(_townland_blocks(townlands))
    blocks.extend(_census_blocks(census))
    blocks.extend(_clearance_blocks(clearances))

    return "\n\n".join(blocks) + "\n"


def import_to_graphdb(ttl_path: Path, endpoint: str, repo: str) -> None:
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' package required for --import.", file=sys.stderr)
        sys.exit(1)

    url = f"{endpoint.rstrip('/')}/repositories/{repo}/statements"
    print(f"Importing {ttl_path.name} → {url} …")

    with ttl_path.open("rb") as fh:
        resp = requests.post(
            url,
            data=fh,
            headers={"Content-Type": "text/turtle"},
            timeout=180,
        )
    if resp.status_code in (200, 204):
        print(f"Import successful (HTTP {resp.status_code}).")
    else:
        print(f"Import failed (HTTP {resp.status_code}): {resp.text[:400]}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Turtle RDF from Coolattin SQLite database."
    )
    parser.add_argument("--db", default=str(DB_PATH),
                        help="Path to SQLite database (default: coolattin.db).")
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="Output .ttl path (default: data/seed/coolattin.ttl).")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max unified_record rows to include (default: all).")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="POST the generated TTL to GraphDB after writing.")
    parser.add_argument("--repo", default="coolattin",
                        help="GraphDB repository name (default: coolattin).")
    parser.add_argument("--endpoint", default=os.environ.get("GRAPHDB_ENDPOINT", "http://localhost:7200"),
                        help="GraphDB base URL.")
    args = parser.parse_args()

    db = Path(args.db)
    out = Path(args.out)

    if not db.exists():
        print(f"ERROR: database not found at {db}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {db} …")
    ttl = generate_ttl(db, limit=args.limit)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ttl, encoding="utf-8")

    size_kb = len(ttl) // 1024
    triple_est = ttl.count("\n.")
    print(f"Written {out} (~{size_kb} KB, ~{triple_est} subject blocks).")

    if args.do_import:
        import_to_graphdb(out, args.endpoint, args.repo)


if __name__ == "__main__":
    main()
