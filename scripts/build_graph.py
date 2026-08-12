from __future__ import annotations

import argparse
import csv
import json
import logging
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_graph")

HAS_EVENT         = "HAS_EVENT"
OCCURRED_IN       = "OCCURRED_IN"
DEPARTED_VIA      = "DEPARTED_VIA"
MEMBER_OF         = "MEMBER_OF"
CO_RESIDENT_WITH  = "CO_RESIDENT_WITH"
CHIEF_TENANT_OF   = "CHIEF_TENANT_OF"
UNDER_TENANT_OF   = "UNDER_TENANT_OF"
WITHIN            = "WITHIN"
HAS_OBSERVATION   = "HAS_OBSERVATION"
LOCATED_IN        = "LOCATED_IN"
NEAR              = "NEAR"
REFERS_TO         = "REFERS_TO"
SAME_AS           = "SAME_AS"
LINKED_TO         = "LINKED_TO"
DERIVED_FROM      = "DERIVED_FROM"
IN_COMMUNITY      = "IN_COMMUNITY"


def _upsert_node(conn, node_id: str, label: str, name: str, props: dict) -> None:
    conn.execute(
        """
        INSERT INTO graph_nodes (node_id, label, name, props)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            label = excluded.label,
            name  = excluded.name,
            props = excluded.props
        """,
        (node_id, label, name, json.dumps(props, ensure_ascii=False)),
    )


def _upsert_edge(conn, src: str, dst: str, rel_type: str, props: dict | None = None) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO graph_edges (src, dst, rel_type, props)
        VALUES (?, ?, ?, ?)
        """,
        (src, dst, rel_type, json.dumps(props or {}, ensure_ascii=False)),
    )


def _norm(s: object) -> str:
    return str(s or "").strip().upper()


def create_tables(conn, wipe: bool) -> None:
    if wipe:
        log.info("step1: wiping graph_nodes and graph_edges")
        conn.executescript("""
            DROP TABLE IF EXISTS graph_edges;
            DROP TABLE IF EXISTS graph_nodes;
        """)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            node_id   TEXT PRIMARY KEY,
            label     TEXT NOT NULL,
            name      TEXT,
            props     TEXT,
            community TEXT,
            embedding BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_gn_label ON graph_nodes(label);

        CREATE TABLE IF NOT EXISTS graph_edges (
            src      TEXT NOT NULL REFERENCES graph_nodes(node_id),
            dst      TEXT NOT NULL REFERENCES graph_nodes(node_id),
            rel_type TEXT NOT NULL,
            props    TEXT,
            PRIMARY KEY (src, dst, rel_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ge_src ON graph_edges(src, rel_type);
        CREATE INDEX IF NOT EXISTS idx_ge_dst ON graph_edges(dst, rel_type);
    """)
    conn.commit()
    log.info("step1: tables ready")


def materialise_nodes(conn) -> tuple[dict[str, int], set[str]]:
    counts: dict[str, int] = {}

    townland_rows = conn.execute(
        "SELECT id, name, civil_parish, barony, county, centroid_lat, centroid_lon, "
        "       kg_uri, entity_id "
        "FROM townland"
    ).fetchall()

    parishes: set[str] = set()
    baronies: set[str] = set()
    counties: set[str] = set()

    for r in townland_rows:
        node_id = f"townland:{_norm(r['name'])}"
        props = {
            "db_id": r["id"],
            "entity_id": r["entity_id"] or "",
            "civil_parish": r["civil_parish"] or "",
            "barony": r["barony"] or "",
            "county": r["county"] or "",
            "lat": r["centroid_lat"],
            "lon": r["centroid_lon"],
            "kg_uri": r["kg_uri"] or "",
        }
        _upsert_node(conn, node_id, "Townland", r["name"] or "", props)

        if r["civil_parish"]:
            parishes.add(r["civil_parish"])
        if r["barony"]:
            baronies.add(r["barony"])
        if r["county"]:
            counties.add(r["county"])

    for p in parishes:
        _upsert_node(conn, f"parish:{_norm(p)}", "CivilParish", p, {})
    for b in baronies:
        _upsert_node(conn, f"barony:{_norm(b)}", "Barony", b, {})
    for c in counties:
        _upsert_node(conn, f"county:{_norm(c)}", "County", c, {})

    counts["Townland"] = len(townland_rows)
    counts["CivilParish"] = len(parishes)
    counts["Barony"] = len(baronies)
    log.info("step2: places | townlands=%d parishes=%d baronies=%d", len(townland_rows), len(parishes), len(baronies))

    import pandas as pd
    from config import ActiveConfig

    csv_path = ActiveConfig.STATIC_DATA_DIR / "unified_processed.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        n_persons = 0
        for _, row in df.iterrows():
            rid = str(row.get("record_id", "")).strip()
            if not rid:
                continue
            node_id = f"person:{rid}"
            surname = str(row.get("surname", "") or "")
            forename = str(row.get("forename", "") or "")
            name = f"{forename} {surname}".strip() or rid
            props = {
                "record_id": rid,
                "surname": surname,
                "forename": forename,
                "canonical_name": str(row.get("canonical_name", "") or ""),
                "townland": str(row.get("townland", "") or ""),
                "year": int(row["year"]) if "year" in row and pd.notna(row.get("year")) else None,
                "role": str(row.get("role", "") or ""),
                "ship_name": str(row.get("ship_name", "") or ""),
                "has_emigration": bool(row.get("has_emigration_record", False)),
                "has_eviction": bool(row.get("has_eviction_record", False)),
                "occupation": str(row.get("occupation", "") or ""),
                "family_key": str(row.get("family_key", "") or ""),
            }
            _upsert_node(conn, node_id, "Person", name, props)
            n_persons += 1

            if props["has_emigration"] and props.get("ship_name"):
                yr = props["year"] or 0
                ev_id = f"event:emigration:{rid}"
                _upsert_node(conn, ev_id, "EmigrationEvent", f"Emigration {yr}", {
                    "year": yr,
                    "ship_name": props["ship_name"],
                    "departure": str(row.get("departure", "") or ""),
                    "arrival": str(row.get("arrival", "") or ""),
                })

            if props["has_eviction"]:
                yr = props["year"] or 0
                ev_id = f"event:eviction:{rid}"
                _upsert_node(conn, ev_id, "EvictionEvent", f"Eviction {yr}", {
                    "year": yr,
                })

        counts["Person"] = n_persons
        log.info("step2: persons=%d", n_persons)
    else:
        log.warning("step2: unified_processed.csv not found at %s", csv_path)
        counts["Person"] = 0

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "source_mentions" in tables:
        wh_rows = conn.execute(
            "SELECT id, source_record_id, raw_name, normalised_name, raw_place, event_year "
            "FROM source_mentions"
        ).fetchall()
        for r in wh_rows:
            node_id = f"workhouse:{r['source_record_id']}"
            _upsert_node(conn, node_id, "WorkhouseRecord", r["raw_name"] or "", {
                "mention_id": r["id"],
                "source_record_id": r["source_record_id"],
                "normalised_name": r["normalised_name"] or "",
                "raw_place": r["raw_place"] or "",
                "event_year": r["event_year"],
            })
        counts["WorkhouseRecord"] = len(wh_rows)
        log.info("step2: workhouse_records=%d", len(wh_rows))

    conn.commit()

    node_set = {row[0] for row in conn.execute("SELECT node_id FROM graph_nodes").fetchall()}
    log.info("step2: node_set size=%d", len(node_set))
    return counts, node_set


def write_reconciliation_gaps(conn) -> int:
    gaps_path = ROOT / "data" / "source_snapshots" / "reconciliation_gaps.csv"
    gaps_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["townland_name", "has_parish", "has_barony", "has_county", "detected_at"]

    existing_names: set[str] = set()
    if gaps_path.exists():
        with gaps_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                existing_names.add(row.get("townland_name", ""))

    rows = conn.execute(
        "SELECT name, civil_parish, barony, county FROM townland "
        "WHERE (civil_parish IS NULL OR civil_parish = '') "
        "   OR (barony IS NULL OR barony = '') "
        "   OR (county IS NULL OR county = '')"
    ).fetchall()

    timestamp = time.strftime("%Y-%m-%d")
    new_rows = []
    for r in rows:
        name = r["name"] or ""
        if name in existing_names:
            continue
        new_rows.append({
            "townland_name": name,
            "has_parish": bool(r["civil_parish"]),
            "has_barony": bool(r["barony"]),
            "has_county": bool(r["county"]),
            "detected_at": timestamp,
        })
        existing_names.add(name)

    if new_rows:
        write_header = not gaps_path.exists() or gaps_path.stat().st_size == 0
        with gaps_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)

    log.info("step2b: reconciliation_gaps appended=%d total_known_gaps=%d", len(new_rows), len(existing_names))
    return len(new_rows)


def materialise_edges(conn, node_set: set[str]) -> tuple[dict[str, int], list[dict]]:
    import pandas as pd
    from config import ActiveConfig

    skipped_edges: list[dict] = []

    def _safe_edge(src: str, dst: str, rel_type: str, props: dict | None = None) -> bool:
        if src not in node_set:
            skipped_edges.append({"src": src, "dst": dst, "rel_type": rel_type,
                                   "reason": f"src not in node set: {src}"})
            return False
        if dst not in node_set:
            skipped_edges.append({"src": src, "dst": dst, "rel_type": rel_type,
                                   "reason": f"dst not in node set: {dst}"})
            return False
        _upsert_edge(conn, src, dst, rel_type, props)
        return True

    counts: dict[str, int] = {}
    n_hier = 0
    n_person_place = 0
    n_events = 0
    n_er = 0

    townland_rows = conn.execute(
        "SELECT name, civil_parish, barony, county FROM townland"
    ).fetchall()

    for r in townland_rows:
        tl_id     = f"townland:{_norm(r['name'])}"
        parish_id = f"parish:{_norm(r['civil_parish'])}" if r["civil_parish"] else None
        barony_id = f"barony:{_norm(r['barony'])}"      if r["barony"]        else None
        county_id = f"county:{_norm(r['county'])}"      if r["county"]        else None

        if parish_id:
            if _safe_edge(tl_id, parish_id, WITHIN):
                n_hier += 1
            if barony_id:
                if _safe_edge(parish_id, barony_id, WITHIN):
                    n_hier += 1
                if county_id:
                    if _safe_edge(barony_id, county_id, WITHIN):
                        n_hier += 1
            elif county_id:
                if _safe_edge(parish_id, county_id, WITHIN):
                    n_hier += 1
        elif county_id:
            if _safe_edge(tl_id, county_id, WITHIN):
                n_hier += 1

    log.info("step3: hierarchy edges written=%d", n_hier)

    csv_path = ActiveConfig.STATIC_DATA_DIR / "unified_processed.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            rid = str(row.get("record_id", "")).strip()
            if not rid:
                continue
            person_id = f"person:{rid}"
            if person_id not in node_set:
                skipped_edges.append({"src": person_id, "dst": "*", "rel_type": "PERSON_EDGES",
                                       "reason": f"person node missing: {person_id}"})
                continue

            tl = str(row.get("townland", "") or "").strip()
            if tl:
                tl_id = f"townland:{_norm(tl)}"
                year_val = int(row["year"]) if "year" in row and pd.notna(row.get("year")) else None
                if _safe_edge(person_id, tl_id, LOCATED_IN, {"year": year_val}):
                    n_person_place += 1

            if row.get("has_emigration_record"):
                ev_id = f"event:emigration:{rid}"
                if _safe_edge(person_id, ev_id, HAS_EVENT):
                    if tl:
                        _safe_edge(ev_id, f"townland:{_norm(tl)}", OCCURRED_IN)
                    ship = str(row.get("ship_name", "") or "").strip()
                    if ship:
                        voyage_id = f"voyage:{_norm(ship)}"
                        if voyage_id not in node_set:
                            conn.execute(
                                "INSERT OR IGNORE INTO graph_nodes (node_id, label, name, props) "
                                "VALUES (?, 'Voyage', ?, ?)",
                                (voyage_id, ship, json.dumps({"ship_name": ship})),
                            )
                            node_set.add(voyage_id)
                        _safe_edge(ev_id, voyage_id, DEPARTED_VIA)
                    n_events += 1

            if row.get("has_eviction_record"):
                ev_id = f"event:eviction:{rid}"
                if _safe_edge(person_id, ev_id, HAS_EVENT):
                    if tl:
                        _safe_edge(ev_id, f"townland:{_norm(tl)}", OCCURRED_IN)
                    n_events += 1

    obs_rows = conn.execute(
        "SELECT t.name AS tl_name, cr.year, cr.total, cr.male, cr.female "
        "FROM census_record cr JOIN townland t ON t.id = cr.townland_id"
    ).fetchall()
    for r in obs_rows:
        obs_id = f"obs:census:{_norm(r['tl_name'])}:{r['year']}"
        _upsert_node(conn, obs_id, "CensusObservation", f"Census {r['year']}", {
            "year": r["year"], "total": r["total"], "male": r["male"], "female": r["female"],
        })
        node_set.add(obs_id)
        _safe_edge(f"townland:{_norm(r['tl_name'])}", obs_id, HAS_OBSERVATION)

    cl_rows = conn.execute(
        "SELECT t.name AS tl_name, cl.year, cl.count "
        "FROM clearances_record cl JOIN townland t ON t.id = cl.townland_id"
    ).fetchall()
    for r in cl_rows:
        obs_id = f"obs:clearance:{_norm(r['tl_name'])}:{r['year']}"
        _upsert_node(conn, obs_id, "ClearanceObservation", f"Clearance {r['year']}", {
            "year": r["year"], "count": r["count"],
        })
        node_set.add(obs_id)
        _safe_edge(f"townland:{_norm(r['tl_name'])}", obs_id, HAS_OBSERVATION)

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "workhouse_unified_links" in tables:
        er_rows = conn.execute(
            "SELECT sm.source_record_id, wul.unified_record_id, wul.score, wul.label "
            "FROM workhouse_unified_links wul "
            "JOIN source_mentions sm ON sm.id = wul.mention_id "
            "WHERE wul.label IN ('CONFIRMED_MATCH', 'POSSIBLE_MATCH')"
        ).fetchall()
        for r in er_rows:
            wh_id     = f"workhouse:{r['source_record_id']}"
            person_id = f"person:{r['unified_record_id']}"
            if _safe_edge(wh_id, person_id, LINKED_TO,
                          {"confidence": r["score"], "band": r["label"]}):
                n_er += 1

    conn.commit()
    log.info(
        "step3: edges | hierarchy=%d person_place=%d events=%d er_links=%d skipped=%d",
        n_hier, n_person_place, n_events, n_er, len(skipped_edges),
    )
    counts.update({"hierarchy": n_hier, "person_place": n_person_place,
                   "events": n_events, "er_links": n_er})
    return counts, skipped_edges


def build_communities(conn) -> int:
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        log.warning("step4: networkx not available — skipping community detection")
        return 0

    log.info("step4: building community graph")
    G = nx.Graph()

    for src, dst in conn.execute(
        "SELECT src, dst FROM graph_edges "
        "WHERE rel_type IN (?, ?, ?)",
        (LOCATED_IN, HAS_EVENT, MEMBER_OF),
    ).fetchall():
        G.add_edge(src, dst)

    if G.number_of_nodes() == 0:
        log.warning("step4: empty graph — no communities")
        return 0

    log.info("step4: running Louvain on %d nodes", G.number_of_nodes())
    try:
        parts = louvain_communities(G, seed=42)
    except Exception as exc:
        log.warning("step4: louvain_failed error=%s", exc)
        return 0

    n_communities = 0
    for idx, members in enumerate(parts):
        comm_id   = str(idx)
        comm_node = f"community:{comm_id}"
        persons = [m for m in list(members)[:5] if m.startswith("person:")]
        places  = [m for m in list(members)[:5] if m.startswith("townland:")]
        summary = (
            f"Community {idx}: "
            + (f"{len(persons)} persons" if persons else "")
            + (f", {len(places)} places" if places else "")
            + f" ({len(members)} total nodes)"
        )
        _upsert_node(conn, comm_node, "Community", f"Community {idx}",
                     {"summary": summary, "size": len(members)})

        for member in members:
            conn.execute(
                "UPDATE graph_nodes SET community = ? WHERE node_id = ?",
                (comm_id, member),
            )
            _upsert_edge(conn, member, comm_node, IN_COMMUNITY)

        n_communities += 1

    conn.commit()
    log.info("step4: communities=%d", n_communities)
    return n_communities


def _passport_text(node_id: str, label: str, name: str, props: dict) -> str:
    parts = [f"{label}: {name}"]
    if label == "Person":
        if props.get("townland"):
            parts.append(f"townland={props['townland']}")
        if props.get("year"):
            parts.append(f"year={props['year']}")
        if props.get("role"):
            parts.append(f"role={props['role']}")
        if props.get("has_emigration"):
            parts.append("emigrated")
        if props.get("has_eviction"):
            parts.append("evicted")
    elif label == "Townland":
        if props.get("civil_parish"):
            parts.append(f"parish={props['civil_parish']}")
        if props.get("barony"):
            parts.append(f"barony={props['barony']}")
    return "; ".join(parts)


def build_embeddings(conn) -> int:
    try:
        from backend.services.local_embeddings import embed_texts_local
    except (ImportError, Exception) as exc:
        log.warning("step5: local_embeddings unavailable (%s) — skipping embeddings", exc)
        return 0

    EMBED_LABELS = {"Person", "Townland", "CivilParish", "EmigrationEvent", "EvictionEvent"}
    BATCH = 256

    rows = conn.execute(
        "SELECT node_id, label, name, props FROM graph_nodes WHERE embedding IS NULL"
    ).fetchall()
    embeddable = [r for r in rows if r["label"] in EMBED_LABELS]

    log.info("step5: embedding %d nodes (batch=%d)", len(embeddable), BATCH)
    n_embedded = 0

    for start in range(0, len(embeddable), BATCH):
        batch = embeddable[start: start + BATCH]
        texts = [
            _passport_text(
                r["node_id"], r["label"], r["name"] or "",
                json.loads(r["props"] or "{}"),
            )
            for r in batch
        ]
        try:
            vecs = embed_texts_local(texts, input_type="document")
        except Exception as exc:
            log.warning("step5: embedding batch %d failed (%s) — skipping batch", start, exc)
            continue
        for r, vec in zip(batch, vecs):
            if not vec:
                continue
            blob = struct.pack(f"{len(vec)}f", *vec)
            conn.execute(
                "UPDATE graph_nodes SET embedding = ? WHERE node_id = ?",
                (blob, r["node_id"]),
            )
            n_embedded += 1
        conn.commit()
        log.info("step5: embedded %d / %d", min(start + BATCH, len(embeddable)), len(embeddable))

    return n_embedded


def _check_reachability(conn) -> list[str]:
    adj: dict[str, list[str]] = {}
    for src, dst in conn.execute(
        "SELECT src, dst FROM graph_edges WHERE rel_type=?", (WITHIN,)
    ).fetchall():
        adj.setdefault(src, []).append(dst)

    townlands_with_county = conn.execute(
        "SELECT name, county FROM townland "
        "WHERE county IS NOT NULL AND county != ''"
    ).fetchall()

    unreachable: list[str] = []
    for row in townlands_with_county:
        tl_id     = f"townland:{_norm(row['name'])}"
        county_id = f"county:{_norm(row['county'])}"

        visited: set[str] = set()
        queue = [tl_id]
        found = False
        while queue and not found:
            node = queue.pop()
            if node == county_id:
                found = True
                break
            if node in visited:
                continue
            visited.add(node)
            queue.extend(adj.get(node, []))

        if not found:
            unreachable.append(row["name"])

    return unreachable


def validate_and_report(
    conn,
    node_counts: dict,
    edge_counts: dict,
    n_communities: int,
    n_embedded: int,
    skipped_edges: list[dict],
) -> None:
    errors: list[str] = []
    warnings: list[str] = []

    total_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

    label_counts = dict(conn.execute(
        "SELECT label, COUNT(*) FROM graph_nodes GROUP BY label"
    ).fetchall())

    orphans = conn.execute(
        "SELECT COUNT(*) FROM graph_nodes g "
        "WHERE NOT EXISTS (SELECT 1 FROM graph_edges WHERE src=g.node_id OR dst=g.node_id)"
    ).fetchone()[0]
    orphan_rate = (orphans / max(total_nodes, 1)) * 100
    if orphan_rate > 2.0:
        warnings.append(f"Orphan rate {orphan_rate:.1f}% exceeds 2% threshold ({orphans}/{total_nodes})")

    dangling = conn.execute(
        "SELECT COUNT(*) FROM graph_edges ge "
        "WHERE NOT EXISTS (SELECT 1 FROM graph_nodes WHERE node_id=ge.src) "
        "   OR NOT EXISTS (SELECT 1 FROM graph_nodes WHERE node_id=ge.dst)"
    ).fetchone()[0]
    if dangling > 0:
        errors.append(f"[BLOCKING] {dangling} dangling edges found (endpoint not in graph_nodes)")

    unreachable = _check_reachability(conn)
    if unreachable:
        errors.append(
            f"[BLOCKING] {len(unreachable)} townland(s) with a county cannot reach it via "
            f"WITHIN traversal: {unreachable[:10]}"
            + (" …" if len(unreachable) > 10 else "")
        )

    persons_no_place = conn.execute(
        "SELECT COUNT(DISTINCT g.node_id) FROM graph_nodes g "
        "WHERE g.label='Person' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM graph_edges e "
        "  JOIN graph_nodes dst ON dst.node_id=e.dst "
        "  WHERE e.src=g.node_id AND dst.label='Townland'"
        ")"
    ).fetchone()[0]
    if persons_no_place > 0:
        warnings.append(f"{persons_no_place} Person nodes have no direct Townland edge")

    unembedded = conn.execute(
        "SELECT COUNT(*) FROM graph_nodes "
        "WHERE label IN ('Person','Townland','CivilParish') AND embedding IS NULL"
    ).fetchone()[0]
    if unembedded > 0:
        warnings.append(f"{unembedded} retrievable nodes missing passport embedding")

    verdict = "BUILD CLEAN" if not errors else "NEEDS FIXES"

    report_lines = [
        "# Graph Build Report",
        "",
        f"**Built:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Verdict:** {verdict}",
        "",
        "## Hierarchy strategy",
        "County link is chained (townland→parish→barony→county).  When an intermediate",
        "level is absent the lowest present descendant links directly to the nearest present",
        "ancestor (nearest-available-ancestor, no sentinel nodes).  Gaps logged to",
        "`data/source_snapshots/reconciliation_gaps.csv`.",
        "",
        "## Counts",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total nodes | {total_nodes} |",
        f"| Total edges | {total_edges} |",
        f"| Communities | {n_communities} |",
        f"| Nodes embedded | {n_embedded} |",
        f"| Orphan rate | {orphan_rate:.1f}% ({orphans}/{total_nodes}) |",
        f"| Skipped edges | {len(skipped_edges)} |",
        f"| Dangling edges | {dangling} |",
        "",
        "## Nodes by label",
        "| Label | Count |",
        "|-------|-------|",
    ]
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        report_lines.append(f"| {label} | {count} |")

    report_lines += [
        "",
        "## Skipped edges",
    ]
    if skipped_edges:
        from collections import Counter
        reason_counts = Counter(e["reason"].split(":")[0].strip() for e in skipped_edges)
        report_lines.append(f"Total skipped: {len(skipped_edges)}")
        report_lines.append("")
        report_lines.append("| Reason | Count |")
        report_lines.append("|--------|-------|")
        for reason, cnt in reason_counts.most_common():
            report_lines.append(f"| {reason} | {cnt} |")
        report_lines.append("")
        report_lines.append("### Skipped edge details (first 50)")
        report_lines.append("| src | rel_type | dst | reason |")
        report_lines.append("|-----|----------|-----|--------|")
        for e in skipped_edges[:50]:
            report_lines.append(
                f"| `{e['src']}` | {e['rel_type']} | `{e['dst']}` | {e['reason']} |"
            )
    else:
        report_lines.append("None — all edge endpoints were present in the node set.")

    report_lines += [
        "",
        "## Validation",
    ]
    if errors:
        report_lines.append("### Errors (build FAILED)")
        report_lines.extend(f"- {e}" for e in errors)
    if warnings:
        report_lines.append("### Warnings")
        report_lines.extend(f"- {w}" for w in warnings)
    if not errors and not warnings:
        report_lines.append("All integrity checks passed.")

    report_lines += [
        "",
        f"## Verdict: {verdict}",
    ]

    report_path = ROOT / "eval_results" / "graph_build_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    log.info("step6: report written to %s  verdict=%s", report_path, verdict)

    for e in errors:
        log.error("VALIDATION ERROR: %s", e)
    for w in warnings:
        log.warning("VALIDATION WARNING: %s", w)

    if errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Coolattin property graph")
    parser.add_argument("--wipe", action="store_true", help="Drop and rebuild from scratch")
    args = parser.parse_args()

    from config import _load_local_env_files, ActiveConfig
    _load_local_env_files()

    from extensions import init_db, get_db_conn, ensure_schema
    init_db(ActiveConfig.DATABASE_PATH)
    ensure_schema()

    conn = get_db_conn()
    try:
        t_start = time.perf_counter()

        log.info("=== build_graph.py start (wipe=%s) ===", args.wipe)

        create_tables(conn, wipe=args.wipe)
        node_counts, node_set = materialise_nodes(conn)
        write_reconciliation_gaps(conn)
        edge_counts, skipped_edges = materialise_edges(conn, node_set)
        n_communities = build_communities(conn)
        n_embedded = build_embeddings(conn)
        validate_and_report(conn, node_counts, edge_counts, n_communities, n_embedded, skipped_edges)

        elapsed = time.perf_counter() - t_start
        log.info("=== build_graph.py complete in %.1fs ===", elapsed)

        try:
            from backend.services import graphrag
            graphrag.reload()
        except Exception:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
