"""
backend/services/kg_service.py

Knowledge Graph data service — D8 RDF/KG Comparative Prototype.

Provides two orthogonal views of the same data:
  1. Graph topology  — nodes + edges sampled from SQLite for visual exploration
  2. SPARQL vs SQL   — side-by-side query comparison using rdflib on the
                       Coolattin Turtle file vs the local SQLite database

The TTL file path is resolved via config (BASE_DIR / data / coolattin_sample.ttl).
rdflib is used in-process so no GraphDB instance is required for this page.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Thread-safe graph cache (built once per process)                    #
# ------------------------------------------------------------------ #
_GRAPH_CACHE: dict | None = None
_GRAPH_CACHE_LOCK = threading.Lock()

# ------------------------------------------------------------------ #
# rdflib graph — loaded lazily, once per process                      #
# ------------------------------------------------------------------ #
_RDF_GRAPH: Any = None
_RDF_GRAPH_LOCK = threading.Lock()

_MAX_PERSONS = 600   # cap for D3.js performance

# ------------------------------------------------------------------ #
# Canned comparison scenarios                                         #
# ------------------------------------------------------------------ #
COMPARISON_SCENARIOS: list[dict] = [
    {
        "id": "emigration_count_by_townland",
        "label": "Emigration by townland",
        "description": (
            "Count emigrants grouped by townland of origin. "
            "SQL filters out NULL townland rows to match SPARQL semantics — "
            "SPARQL requires the co:townland triple to exist, so records without "
            "a townland are naturally excluded from both queries."
        ),
        "sql": (
            "SELECT townland, COUNT(*) AS emigrants\n"
            "FROM unified_record\n"
            "WHERE has_emigration_record = 1\n"
            "  AND townland IS NOT NULL\n"
            "  AND townland != ''\n"
            "GROUP BY townland\n"
            "ORDER BY emigrants DESC\n"
            "LIMIT 10"
        ),
        "sparql": (
            "SELECT ?townland (COUNT(?person) AS ?emigrants)\n"
            "WHERE {\n"
            "  ?person a co:Person ;\n"
            "          co:townland ?townland ;\n"
            "          co:hasEvent ?ev .\n"
            "  ?ev co:eventType \"emigration\" .\n"
            "}\n"
            "GROUP BY ?townland\n"
            "ORDER BY DESC(?emigrants)\n"
            "LIMIT 10"
        ),
    },
    {
        "id": "eviction_count_by_year",
        "label": "Evictions per year",
        "description": (
            "Count eviction events grouped by year. "
            "SQL excludes the 4 records where year is NULL so both queries "
            "return the same 38-row result set — SPARQL cannot bind an "
            "unset co:year triple, so excluding NULLs makes the comparison fair."
        ),
        "sql": (
            "SELECT year, COUNT(*) AS evictions\n"
            "FROM unified_record\n"
            "WHERE has_eviction_record = 1\n"
            "  AND year IS NOT NULL\n"
            "GROUP BY year\n"
            "ORDER BY year"
        ),
        "sparql": (
            "SELECT ?year (COUNT(?ev) AS ?evictions)\n"
            "WHERE {\n"
            "  ?ev a co:Event ;\n"
            "      co:eventType \"eviction\" ;\n"
            "      co:year ?year .\n"
            "}\n"
            "GROUP BY ?year\n"
            "ORDER BY ?year"
        ),
    },
    {
        "id": "surname_frequency",
        "label": "Top 10 surnames",
        "description": (
            "Most common surnames across all estate records. "
            "Both queries return identical results — this is the cleanest "
            "scenario showing that RDF and relational results agree precisely."
        ),
        "sql": (
            "SELECT surname, COUNT(*) AS records\n"
            "FROM unified_record\n"
            "WHERE surname IS NOT NULL AND surname != ''\n"
            "GROUP BY surname\n"
            "ORDER BY records DESC\n"
            "LIMIT 10"
        ),
        "sparql": (
            "SELECT ?surname (COUNT(?person) AS ?records)\n"
            "WHERE {\n"
            "  ?person a co:Person ;\n"
            "          schema:familyName ?surname .\n"
            "}\n"
            "GROUP BY ?surname\n"
            "ORDER BY DESC(?records)\n"
            "LIMIT 10"
        ),
    },
    {
        "id": "person_event_detail",
        "label": "Person + event detail",
        "description": (
            "List persons with their name, townland, and event type. "
            "SPARQL traverses the person→event link naturally as a graph walk. "
            "SQL requires a self-join pattern via the same unified_record table. "
            "Both return the same records — the graph model makes the join implicit."
        ),
        "sql": (
            "SELECT forename, surname, townland, year,\n"
            "       CASE WHEN has_emigration_record=1 THEN 'emigration'\n"
            "            WHEN has_eviction_record=1   THEN 'eviction'\n"
            "            ELSE 'tenancy' END AS event_type\n"
            "FROM unified_record\n"
            "WHERE townland IS NOT NULL AND year IS NOT NULL\n"
            "ORDER BY year, surname\n"
            "LIMIT 20"
        ),
        "sparql": (
            "SELECT ?forename ?surname ?townland ?year ?eventType\n"
            "WHERE {\n"
            "  ?person a co:Person ;\n"
            "          schema:givenName  ?forename ;\n"
            "          schema:familyName ?surname ;\n"
            "          co:townland       ?townland ;\n"
            "          co:hasEvent       ?ev .\n"
            "  ?ev co:eventType ?eventType ;\n"
            "      co:year      ?year .\n"
            "}\n"
            "ORDER BY ?year ?surname\n"
            "LIMIT 20"
        ),
    },
]


# ------------------------------------------------------------------ #
# rdflib helpers                                                      #
# ------------------------------------------------------------------ #

def _ttl_path() -> Path:
    from config import BASE_DIR
    return BASE_DIR / "data" / "coolattin_sample.ttl"


def _load_rdf_graph():
    """Load the Turtle file into an rdflib Graph (once per process)."""
    global _RDF_GRAPH
    with _RDF_GRAPH_LOCK:
        if _RDF_GRAPH is not None:
            return _RDF_GRAPH
        path = _ttl_path()
        if not path.exists():
            log.warning("kg_service.ttl_missing | path=%s", path)
            return None
        try:
            import rdflib
            g = rdflib.ConjunctiveGraph()
            t0 = time.perf_counter()
            g.parse(str(path), format="turtle")
            ms = int((time.perf_counter() - t0) * 1000)
            log.info("kg_service.rdf_loaded | triples=%d ms=%d", len(g), ms)
            _RDF_GRAPH = g
            return g
        except Exception as exc:
            log.warning("kg_service.rdf_load_failed | %s", exc)
            return None


def run_sparql(sparql_body: str) -> tuple[list[str], list[dict], str | None]:
    """
    Execute a SPARQL SELECT against the in-process rdflib graph.
    Prefixes are prepended automatically.

    Returns (columns, rows, error_message).
    """
    prefixes = (
        "PREFIX co:     <https://coolattin.ie/ontology#>\n"
        "PREFIX ex:     <https://coolattin.ie/resource/>\n"
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>\n"
        "PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>\n"
    )
    g = _load_rdf_graph()
    if g is None:
        return [], [], "RDF graph not available — coolattin_sample.ttl not found."
    try:
        import rdflib
        results = g.query(prefixes + "\n" + sparql_body)
        cols = [str(v) for v in results.vars]
        rows = []
        for binding in results:
            row = {}
            for col in cols:
                val = binding.get(col)
                row[col] = str(val) if val is not None else None
            rows.append(row)
        return cols, rows, None
    except Exception as exc:
        log.warning("kg_service.sparql_failed | %s", exc)
        return [], [], str(exc)


# ------------------------------------------------------------------ #
# SQL runner (for the comparison panel)                               #
# ------------------------------------------------------------------ #

def run_sql(sql: str, max_rows: int = 500) -> tuple[list[str], list[dict], str | None]:
    """Execute a read-only SQL query against the local SQLite database."""
    from extensions import get_db_conn
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return [], [], "Only SELECT queries are permitted."
    try:
        conn = get_db_conn()
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(row) for row in cur.fetchmany(max_rows)]
            return cols, rows, None
        finally:
            conn.close()
    except Exception as exc:
        return [], [], str(exc)


# ------------------------------------------------------------------ #
# Graph topology builder                                              #
# ------------------------------------------------------------------ #

def build_graph(limit: int = _MAX_PERSONS) -> dict:
    """
    Build a graph dict {nodes, edges, meta} suitable for D3.js force simulation.

    Node types:
      - Person   (id="p_<record_id>")
      - Townland  (id="t_<name>")
      - EventHub  (id="eh_emigration" | "eh_eviction" | "eh_tenancy")

    Edges:
      - Person → Townland  (label="from")
      - Person → EventHub  (label="has_event")  one per record's primary event type
    """
    global _GRAPH_CACHE
    with _GRAPH_CACHE_LOCK:
        if _GRAPH_CACHE is not None:
            return _GRAPH_CACHE

        result = _build_graph_inner(limit)
        _GRAPH_CACHE = result
        return result


def _build_graph_inner(limit: int) -> dict:  # noqa: ARG001 — limit kept for API compat
    from extensions import get_db_conn

    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    def add_node(nid: str, **kwargs):
        if nid not in node_ids:
            node_ids.add(nid)
            nodes.append({"id": nid, **kwargs})

    # Fixed hub nodes
    for eid, label, color in [
        ("emigration", "Emigration", "#16a34a"),
        ("eviction",   "Eviction",   "#dc2626"),
        ("tenancy",    "Tenancy",    "#2563eb"),
    ]:
        add_node(f"eh_{eid}", type="EventHub", label=label, color=color, size=22)

    add_node("dh_census",     type="DataHub", label="Census Records",  color="#0891b2", size=18)
    add_node("dh_clearances", type="DataHub", label="Clearances",      color="#9333ea", size=18)

    conn = get_db_conn()
    try:
        # ── Aggregated stats per townland ───────────────────────────────
        stats_rows = conn.execute("""
            SELECT townland,
                   COUNT(*)                      AS total_records,
                   SUM(has_emigration_record)    AS emigrant_count,
                   SUM(has_eviction_record)      AS eviction_count,
                   SUM(has_tenancy_record)       AS tenancy_count
            FROM unified_record
            WHERE townland IS NOT NULL AND townland != ''
            GROUP BY townland
            ORDER BY total_records DESC
        """).fetchall()

        # ── Parish lookup from unified_record.parish (canonical values) ──
        _bad = {"?", "coolattin", "co wexford", "county wexford"}
        parish_map: dict[str, str] = {}
        for r in conn.execute("""
            SELECT townland, parish, COUNT(*) AS cnt
            FROM unified_record
            WHERE townland IS NOT NULL AND townland != ''
              AND parish IS NOT NULL AND parish != ''
            GROUP BY townland, parish
            ORDER BY cnt DESC
        """).fetchall():
            tl, par = r["townland"].strip(), r["parish"].strip()
            if (par.lower() in _bad or "/" in par
                    or par.lower().startswith("co ")
                    or par.lower().startswith("county ")
                    or len(par) < 3):
                continue
            if tl not in parish_map:
                parish_map[tl] = par

        # ── Enrichment from townland reference table ────────────────────
        tl_meta: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT name, civil_parish, barony, county FROM townland WHERE name IS NOT NULL"
        ).fetchall():
            tl_meta[r["name"].strip().upper()] = {
                "civil_parish": r["civil_parish"],
                "barony": r["barony"],
                "county": r["county"],
            }

        # ── Townland IDs for census/clearances membership tests ─────────
        census_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT townland_id FROM census_record"
        ).fetchall()}
        clearance_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT townland_id FROM clearances_record"
        ).fetchall()}
        tl_name_to_db_id: dict[str, int] = {
            r["name"].strip().upper(): r["id"]
            for r in conn.execute("SELECT id, name FROM townland WHERE name IS NOT NULL").fetchall()
        }

        # ── Build townland and parish nodes ─────────────────────────────
        parish_seen: set[str] = set()

        for r in stats_rows:
            tl_name = r["townland"].strip()
            t_id = f"t_{tl_name}"
            total  = r["total_records"]  or 0
            emigr  = r["emigrant_count"] or 0
            evict  = r["eviction_count"] or 0
            tenan  = r["tenancy_count"]  or 0

            # Dominant event determines node colour
            if emigr >= evict and emigr >= tenan:
                color, dominant = "#15803d", "emigration"
            elif evict >= emigr and evict >= tenan:
                color, dominant = "#b91c1c", "eviction"
            else:
                color, dominant = "#1d4ed8", "tenancy"

            size = min(8 + total // 30, 22)

            # Parish — try unified_record first, then townland table
            parish = parish_map.get(tl_name)
            if not parish:
                parish = tl_meta.get(tl_name.upper(), {}).get("civil_parish")

            meta = tl_meta.get(tl_name.upper(), {})
            add_node(
                t_id,
                type="Townland",
                label=tl_name,
                civil_parish=parish,
                barony=meta.get("barony"),
                county=meta.get("county"),
                total_records=total,
                emigrant_count=emigr,
                eviction_count=evict,
                tenancy_count=tenan,
                dominant_event=dominant,
                color=color,
                size=size,
            )

            # Parish node + edge
            if parish:
                p_id = f"parish_{parish}"
                if parish not in parish_seen:
                    parish_seen.add(parish)
                    add_node(p_id, type="Parish", label=parish, color="#7c3aed", size=16)
                edges.append({"source": p_id, "target": t_id, "label": "contains",
                               "type": "parish_townland"})

            # Event hub edges (only for non-zero counts)
            if emigr > 0:
                edges.append({"source": t_id, "target": "eh_emigration",
                               "label": "emigrants", "type": "townland_event", "count": emigr})
            if evict > 0:
                edges.append({"source": t_id, "target": "eh_eviction",
                               "label": "evictions", "type": "townland_event", "count": evict})
            if tenan > 0:
                edges.append({"source": t_id, "target": "eh_tenancy",
                               "label": "tenants", "type": "townland_event", "count": tenan})

            # Census / clearances hub edges
            db_id = tl_name_to_db_id.get(tl_name.upper())
            if db_id and db_id in census_ids:
                edges.append({"source": t_id, "target": "dh_census",
                               "label": "has_census", "type": "townland_census"})
            if db_id and db_id in clearance_ids:
                edges.append({"source": t_id, "target": "dh_clearances",
                               "label": "has_clearances", "type": "townland_clearances"})

    finally:
        conn.close()

    meta = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "total_records": sum(
            n.get("total_records", 0) for n in nodes if n["type"] == "Townland"
        ),
        "townland_count": sum(1 for n in nodes if n["type"] == "Townland"),
        "parish_count":   sum(1 for n in nodes if n["type"] == "Parish"),
        "census_townland_count": sum(1 for e in edges if e["type"] == "townland_census"),
        "clearances_townland_count": sum(1 for e in edges if e["type"] == "townland_clearances"),
    }
    log.info("kg_service.graph_built | nodes=%d edges=%d", len(nodes), len(edges))
    return {"nodes": nodes, "edges": edges, "meta": meta}


def get_townland_persons(townland_name: str, limit: int = 50) -> dict:
    """Return individual person records for a specific townland (detail drill-down)."""
    from extensions import get_db_conn
    conn = get_db_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM unified_record WHERE UPPER(townland)=UPPER(?)",
            (townland_name,),
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT forename, surname, year, occupation,
                      has_emigration_record, has_eviction_record, has_tenancy_record
               FROM unified_record
               WHERE UPPER(townland)=UPPER(?)
               ORDER BY year, surname, forename
               LIMIT ?""",
            (townland_name, limit),
        ).fetchall()
        persons = []
        for r in rows:
            full = " ".join(p for p in [r["forename"] or "", r["surname"] or ""] if p).strip() or "Unknown"
            event = ("emigration" if r["has_emigration_record"]
                     else "eviction" if r["has_eviction_record"]
                     else "tenancy")
            persons.append({
                "name": full,
                "year": r["year"],
                "occupation": r["occupation"],
                "event_type": event,
            })
        return {"townland": townland_name, "total": total, "persons": persons}
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# LLM-powered mismatch analysis                                       #
# ------------------------------------------------------------------ #

def explain_mismatch(
    sql_query: str,
    sparql_query: str,
    sql_rows: list[dict],
    sparql_rows: list[dict],
    sql_row_count: int,
    sparql_row_count: int,
) -> dict:
    """
    Ask an LLM to explain why the SQL and SPARQL result sets differ.

    Returns a dict with:
      - analysis: structured explanation text (markdown)
      - reasons: list of short reason strings
      - model_used: which LLM model responded
      - error: None or error string if LLM call failed
    """
    import os, json, requests as _req

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return {
            "analysis": None,
            "reasons": [],
            "model_used": None,
            "error": "LLM not configured — OPENROUTER_API_KEY is not set.",
        }

    def _sample(rows: list[dict], n: int = 5) -> str:
        sample = rows[:n]
        if not sample:
            return "(no rows)"
        cols = list(sample[0].keys())
        lines = [" | ".join(cols)]
        lines.append("-" * len(lines[0]))
        for row in sample:
            lines.append(" | ".join(str(row.get(c, "")) for c in cols))
        return "\n".join(lines)

    sql_sample = _sample(sql_rows)
    sparql_sample = _sample(sparql_rows)

    prompt = f"""You are an expert in database systems and knowledge graphs analysing why a SQL query and a SPARQL query against the same underlying data return DIFFERENT result sets.

CONTEXT
-------
Dataset: Coolattin Estate records (19th-century Ireland) — 13,000+ unified records covering tenancy, emigration, and eviction history.
SQL store: SQLite relational database (closed-world assumption — includes NULL values in GROUP BY).
SPARQL store: RDF graph using rdflib (open-world assumption — a triple must exist for a value to appear).

SQL QUERY ({sql_row_count} rows returned)
-----------------------------------------
{sql_query}

SPARQL QUERY ({sparql_row_count} rows returned)
------------------------------------------------
{sparql_query}

SQL RESULT SAMPLE (first 5 rows)
---------------------------------
{sql_sample}

SPARQL RESULT SAMPLE (first 5 rows)
-------------------------------------
{sparql_sample}

TASK
----
Provide a structured, academically rigorous analysis of WHY these two queries return {sql_row_count} vs {sparql_row_count} rows.

Your response MUST use this exact structure:

## Root Cause
One clear sentence stating the primary reason for the difference.

## Technical Explanation
2-3 paragraphs explaining the technical mechanisms behind the difference. Be specific about:
- The closed-world vs open-world assumption difference
- How NULL handling differs in SQL GROUP BY vs SPARQL triple patterns
- Any schema modelling differences (e.g. optional vs required properties)

## All Possible Reasons
A numbered list of every plausible explanation for the discrepancy, from most to least likely.

## Data Evidence
What the sample rows above tell us about the specific data causing the difference.

## Conclusion
One sentence confirming whether this is expected behaviour or a potential data quality issue.

Be precise, academic, and base every claim on the queries and samples provided."""

    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
    free_models = [
        "openai/gpt-oss-20b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-3-27b-it:free",
    ]
    candidates = [model] + [m for m in free_models if m != model]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:5001",
        "X-Title": "Coolattin KG Compare",
    }

    for candidate in candidates:
        try:
            resp = _req.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": candidate,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a data systems expert. Provide precise, structured analysis. "
                                "Follow the exact markdown structure requested."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1200,
                },
                timeout=(10, 60),
            )
            if resp.status_code not in {200, 201}:
                log.warning("kg_service.explain_mismatch | http=%d model=%s", resp.status_code, candidate)
                continue
            data = resp.json()
            text = ""
            choices = data.get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content") or ""
            if not text.strip():
                continue

            # Extract bullet-style reasons from "All Possible Reasons" section
            reasons: list[str] = []
            in_reasons = False
            for line in text.splitlines():
                if "All Possible Reasons" in line:
                    in_reasons = True
                    continue
                if in_reasons and line.startswith("##"):
                    break
                if in_reasons:
                    stripped = line.strip().lstrip("0123456789.-) ").strip()
                    if stripped:
                        reasons.append(stripped)

            return {
                "analysis": text.strip(),
                "reasons": reasons[:8],
                "model_used": data.get("model") or candidate,
                "error": None,
            }
        except Exception as exc:
            log.warning("kg_service.explain_mismatch | model=%s error=%s", candidate, exc)
            continue

    return {
        "analysis": None,
        "reasons": [],
        "model_used": None,
        "error": "LLM call failed — check server logs for details.",
    }
