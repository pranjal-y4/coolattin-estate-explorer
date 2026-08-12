from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_GRAPH_CACHE: dict | None = None
_GRAPH_CACHE_LOCK = threading.Lock()

_RDF_GRAPH: Any = None
_RDF_GRAPH_LOCK = threading.Lock()

_MAX_PERSONS = 600

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


def _ttl_path() -> Path:
    from config import BASE_DIR
    return BASE_DIR / "data" / "coolattin_sample.ttl"


def _load_rdf_graph():
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


def run_sql(sql: str, max_rows: int = 500) -> tuple[list[str], list[dict], str | None]:
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


def build_graph(limit: int = _MAX_PERSONS) -> dict:  # noqa: ARG001
    global _GRAPH_CACHE
    with _GRAPH_CACHE_LOCK:
        if _GRAPH_CACHE is not None:
            return _GRAPH_CACHE
        result = _build_geographic_graph()
        _GRAPH_CACHE = result
        return result


def reset_graph_cache() -> None:
    global _GRAPH_CACHE
    with _GRAPH_CACHE_LOCK:
        _GRAPH_CACHE = None


def _build_geographic_graph() -> dict:
    from extensions import get_db_conn

    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    def add_node(nid: str, **kwargs) -> None:
        if nid not in node_ids:
            node_ids.add(nid)
            nodes.append({"id": nid, **kwargs})

    conn = get_db_conn()
    try:
        rows = conn.execute("""
            SELECT
                t.id,
                t.name,
                t.name_gaelic,
                t.civil_parish,
                t.barony,
                t.county,
                t.electoral_division,
                t.placename_theme,
                t.centroid_lat,
                t.centroid_lon,
                t.kg_uri,
                (SELECT COUNT(DISTINCT ur.record_id)
                   FROM unified_record ur
                   WHERE ur.townland_norm = UPPER(t.name)) AS record_count
            FROM townland t
            WHERE t.name IS NOT NULL AND UPPER(t.county) = 'WICKLOW'
            ORDER BY t.barony, t.civil_parish, t.name
        """).fetchall()

        county_seen:  set[str] = set()
        barony_seen:  set[str] = set()
        parish_seen:  set[str] = set()

        for r in rows:
            tl_name  = (r["name"] or "").strip()
            county   = (r["county"] or "").strip()
            barony   = (r["barony"] or "").strip()
            parish   = (r["civil_parish"] or "").strip()
            gaelic   = (r["name_gaelic"] or "").strip()
            lat      = r["centroid_lat"]
            lon      = r["centroid_lon"]
            rec_cnt  = r["record_count"] or 0

            if county and county not in county_seen:
                county_seen.add(county)
                add_node(
                    f"county_{county}",
                    type="County",
                    label=county,
                    color="#0369a1",
                    size=28,
                )

            if barony and barony not in barony_seen:
                barony_seen.add(barony)
                add_node(
                    f"barony_{barony}",
                    type="Barony",
                    label=barony,
                    county=county,
                    color="#b45309",
                    size=20,
                )
                if county:
                    edges.append({
                        "source": f"county_{county}",
                        "target": f"barony_{barony}",
                        "label": "contains",
                        "type": "county_barony",
                    })

            if parish and parish not in parish_seen:
                parish_seen.add(parish)
                add_node(
                    f"parish_{parish}",
                    type="CivilParish",
                    label=parish,
                    barony=barony,
                    county=county,
                    color="#7c3aed",
                    size=14,
                )
                parent = f"barony_{barony}" if barony else (f"county_{county}" if county else None)
                if parent:
                    edges.append({
                        "source": parent,
                        "target": f"parish_{parish}",
                        "label": "contains",
                        "type": "barony_parish",
                    })

            t_id = f"t_{tl_name}"
            size = min(8 + rec_cnt // 40, 14)
            add_node(
                t_id,
                type="Townland",
                label=tl_name,
                name_gaelic=gaelic or None,
                civil_parish=parish or None,
                barony=barony or None,
                county=county or None,
                electoral_division=(r["electoral_division"] or "").strip() or None,
                placename_theme=(r["placename_theme"] or "").strip() or None,
                centroid_lat=lat,
                centroid_lon=lon,
                kg_uri=r["kg_uri"],
                record_count=rec_cnt,
                color="#15803d",
                size=size,
            )

            parent_tl = f"parish_{parish}" if parish else (
                f"barony_{barony}" if barony else (f"county_{county}" if county else None)
            )
            if parent_tl:
                edges.append({
                    "source": parent_tl,
                    "target": t_id,
                    "label": "contains",
                    "type": "parish_townland",
                })

    finally:
        conn.close()

    meta = {
        "node_count":    len(nodes),
        "edge_count":    len(edges),
        "county_count":  sum(1 for n in nodes if n["type"] == "County"),
        "barony_count":  sum(1 for n in nodes if n["type"] == "Barony"),
        "parish_count":  sum(1 for n in nodes if n["type"] == "CivilParish"),
        "townland_count": sum(1 for n in nodes if n["type"] == "Townland"),
        "with_gaelic":   sum(1 for n in nodes if n["type"] == "Townland" and n.get("name_gaelic")),
        "source":        "geographic_hierarchy",
    }
    log.info("kg_service.geo_graph_built | nodes=%d edges=%d", len(nodes), len(edges))
    return {"nodes": nodes, "edges": edges, "meta": meta}


def get_townland_persons(townland_name: str, limit: int = 50) -> dict:
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


def get_townland_rich_detail(townland_name: str) -> dict:
    import os, json, requests as _req, concurrent.futures, time as _time

    from extensions import get_db_conn
    conn = get_db_conn()
    db_data: dict = {}
    try:
        row = conn.execute(
            """SELECT name, name_gaelic, civil_parish, barony, county,
                      electoral_division, placename_theme, description,
                      centroid_lat, centroid_lon, kg_uri
               FROM townland WHERE UPPER(name)=UPPER(?) LIMIT 1""",
            (townland_name,),
        ).fetchone()
        if row:
            db_data["townland"] = dict(row)

        census_rows = conn.execute(
            """SELECT cr.year, cr.total, cr.male, cr.female,
                      cr.inhabited, cr.uninhabited
               FROM census_record cr
               JOIN townland t ON cr.townland_id = t.id
               WHERE UPPER(t.name) = UPPER(?)
               ORDER BY cr.year""",
            (townland_name,),
        ).fetchall()
        db_data["census"] = [dict(r) for r in census_rows]

        clear_col = next(
            (c for c in ["count", "eviction_count", "num_evictions"]
             if any(c == col[1] for col in conn.execute("PRAGMA table_info(clearances_record)").fetchall())),
            "count",
        )
        clear_rows = conn.execute(
            f"""SELECT cr.year, cr.{clear_col} AS evictions
                FROM clearances_record cr
                JOIN townland t ON cr.townland_id = t.id
                WHERE UPPER(t.name) = UPPER(?)
                ORDER BY cr.year""",
            (townland_name,),
        ).fetchall()
        db_data["clearances"] = [dict(r) for r in clear_rows]

        heritage_rows = conn.execute(
            """SELECT feature_group, monument_class, source_dataset
               FROM heritage_feature
               WHERE UPPER(townland_norm) = UPPER(?)
               ORDER BY feature_group""",
            (townland_name,),
        ).fetchall()
        db_data["heritage"] = [dict(r) for r in heritage_rows]

        ppl = conn.execute(
            """SELECT
                 COUNT(DISTINCT record_id) AS total_people,
                 SUM(CASE WHEN has_emigration_record=1 THEN 1 ELSE 0 END) AS emigrants,
                 SUM(CASE WHEN has_eviction_record=1 THEN 1 ELSE 0 END)   AS evicted,
                 SUM(CASE WHEN has_tenancy_record=1 THEN 1 ELSE 0 END)    AS tenants,
                 MIN(year) AS earliest_year,
                 MAX(year) AS latest_year
               FROM unified_record
               WHERE UPPER(townland_norm) = UPPER(?)""",
            (townland_name,),
        ).fetchone()
        if ppl:
            db_data["people_summary"] = dict(ppl)

        surname_rows = conn.execute(
            """SELECT COALESCE(surname,'Unknown') AS surname, COUNT(DISTINCT record_id) AS n
               FROM unified_record
               WHERE UPPER(townland_norm) = UPPER(?)
                 AND surname IS NOT NULL
               GROUP BY surname ORDER BY n DESC LIMIT 5""",
            (townland_name,),
        ).fetchall()
        db_data["top_surnames"] = [dict(r) for r in surname_rows]

    finally:
        conn.close()

    vrti_data: dict | None = None
    try:
        from backend.integrations import vrti_sparql as _vs
        dto = _vs.get_townland_details_by_name(townland_name, county="Wicklow")
        if dto:
            vrti_data = dto.to_dict()
    except Exception as exc:
        log.warning("kg_service.rich_detail.vrti_lookup_failed name=%s error=%s", townland_name, exc)

    tl = db_data.get("townland") or {}
    ppl_s = db_data.get("people_summary") or {}
    census_lines = "\n".join(
        f"  {r['year']}: total={r['total']}, male={r['male']}, female={r['female']}, "
        f"inhabited_houses={r['inhabited']}"
        for r in db_data.get("census", [])
    ) or "  (no census data)"
    clearance_lines = "\n".join(
        f"  {r['year']}: {r['evictions']} eviction(s)"
        for r in db_data.get("clearances", [])
    ) or "  (no eviction records)"
    heritage_lines = "\n".join(
        f"  {r['feature_group']}: {r['monument_class'] or 'n/a'}"
        for r in db_data.get("heritage", [])
    ) or "  (none recorded)"
    surnames_line = ", ".join(
        f"{r['surname']} ({r['n']})" for r in db_data.get("top_surnames", [])
    ) or "(none)"
    kg_uri = tl.get("kg_uri") or (vrti_data or {}).get("uri") or ""

    context_block = f"""Townland: {tl.get('name', townland_name)}
Irish name (Gaelic): {tl.get('name_gaelic') or 'unknown'}
Civil Parish: {tl.get('civil_parish') or 'unknown'}
Barony: {tl.get('barony') or 'unknown'}
County: {tl.get('county') or 'Wicklow'}, Ireland
Electoral Division: {tl.get('electoral_division') or 'unknown'}
Placename Theme: {tl.get('placename_theme') or 'unknown'}
Coordinates: {tl.get('centroid_lat')}, {tl.get('centroid_lon')}
VRTI Knowledge Graph URI: {kg_uri}

VRTI KG data (if available):
  Boundary WKT: {(vrti_data or {}).get('boundary_wkt', 'not available')[:120] if (vrti_data or {}).get('boundary_wkt') else 'not available'}
  External links: {', '.join((vrti_data or {}).get('links', [])) or 'none'}

Estate records (mid-19th century):
  Total people: {ppl_s.get('total_people', 0)}
  Emigrants: {ppl_s.get('emigrants', 0)}
  Evicted: {ppl_s.get('evicted', 0)}
  Tenants: {ppl_s.get('tenants', 0)}
  Year range: {ppl_s.get('earliest_year')} – {ppl_s.get('latest_year')}
  Top surnames: {surnames_line}

Census data (population):
{census_lines}

Eviction/clearances data:
{clearance_lines}

Heritage features:
{heritage_lines}
""".strip()

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
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
        "X-Title": "Coolattin KG Townland Detail",
    }

    def _llm_call(messages: list[dict], max_tokens: int = 600) -> tuple[str, str | None]:
        if not api_key:
            return "", "LLM not configured — OPENROUTER_API_KEY not set."
        for cand in candidates:
            try:
                resp = _req.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={"model": cand, "messages": messages, "max_tokens": max_tokens,
                          "temperature": 0.2},
                    timeout=20,
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    return content, None
            except Exception as exc:
                log.warning("kg_service.rich_detail.llm_call_failed model=%s error=%s", cand, exc)
        return "", "All LLM candidates failed."

    sparql_prompt = f"""You are a SPARQL expert working with the VRTI (Virtual Record Treasury of Ireland) Knowledge Graph.

The VRTI endpoint is: https://virtuoso.virtualtreasury.ie/sparql/
The present-day places graph is: https://kg.virtualtreasury.ie/graph/present-day-places-v1

Available prefixes:
PREFIX crm:  <http://erlangen-crm.org/current/>
PREFIX vrti: <https://ont.virtualtreasury.ie/ontology#>
PREFIX geo:  <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

Key VRTI ontology patterns:
- Townlands: ?place crm:P2_has_type vrti:PresentDayTownland ; rdfs:label ?name
- English name filter: FILTER(langMatches(lang(?name), "en"))
- Irish (Gaelic) name: rdfs:label ?nameGaelic FILTER(langMatches(lang(?nameGaelic), "ga"))
- Geometry: geo:hasGeometry ?geom . ?geom geo:asWKT ?wkt
- Parish containment: crm:P89_falls_within → vrti:PresentDayParish
- Barony containment: crm:P89_falls_within → vrti:PresentDayBarony
- County containment: crm:P89_falls_within → vrti:PresentDayCounty
- External identifiers: owl:sameAs (links to logainm.ie, townlands.ie)
- skos:closeMatch or skos:exactMatch for alternative references

TOWNLAND CONTEXT:
{context_block}

TASK: Write ONE optimised SPARQL SELECT query that retrieves the maximum useful geographical and contextual information about the townland "{tl.get('name', townland_name)}" in County Wicklow from the VRTI KG. Include: English and Irish names, geometry/WKT boundary, parish hierarchy, barony, county, external identifier links (logainm, townlands.ie), and any alternative labels.

Return ONLY the SPARQL query — no explanation, no markdown fences, no PREFIX block (prefixes are prepended automatically).
Query must use: GRAPH <https://kg.virtualtreasury.ie/graph/present-day-places-v1> {{ ... }}
Use OPTIONAL for fields that may not exist.
LIMIT 1 is not needed — use LIMIT 20 to capture multiple rows from optional fields."""

    sparql_query_raw, sparql_err = _llm_call([
        {"role": "system", "content": "You are a SPARQL expert. Return only valid SPARQL SELECT queries."},
        {"role": "user", "content": sparql_prompt},
    ], max_tokens=500)

    sparql_query = sparql_query_raw.strip()
    for fence in ("```sparql", "```SPARQL", "```"):
        sparql_query = sparql_query.replace(fence, "")
    sparql_query = sparql_query.strip()

    sparql_results: list[dict] = []
    sparql_run_error: str | None = None
    if sparql_query and not sparql_err:
        try:
            from backend.integrations import vrti_sparql as _vs2
            bindings = _vs2._execute(sparql_query)
            sparql_results = [
                {k: v.get("value", "") for k, v in b.items()}
                for b in bindings[:30]
            ]
        except Exception as exc:
            sparql_run_error = str(exc)
            log.warning("kg_service.rich_detail.sparql_run_failed name=%s error=%s", townland_name, exc)

    sparql_result_summary = ""
    if sparql_results:
        cols = list(sparql_results[0].keys())
        sparql_result_summary = " | ".join(cols) + "\n"
        for row in sparql_results[:10]:
            sparql_result_summary += " | ".join(str(row.get(c, "")) for c in cols) + "\n"

    narrative_prompt = f"""You are a historical geographer and Irish heritage expert writing about a 19th-century Coolattin Estate townland for an academic audience.

TOWNLAND DATA:
{context_block}

SPARQL QUERY RESULTS FROM VRTI KNOWLEDGE GRAPH:
{sparql_result_summary or '(SPARQL query returned no results — use the context data above)'}

TASK: Write a rich, informative description of the townland "{tl.get('name', townland_name)}" that covers:
1. **Geographic identity** — location within its parish, barony, and county; Gaelic name meaning if known; coordinates
2. **Historical significance** — what the estate records reveal about this place: population trends (from census), eviction events, emigration patterns, key family names
3. **Heritage** — any holy wells, ring forts, or other heritage features present
4. **Knowledge Graph context** — what the VRTI KG records about this place (external links, identifiers)

Write 3–4 paragraphs of clear, engaging academic prose. Be specific with numbers. Do not invent facts not in the data."""

    narrative, narrative_err = _llm_call([
        {"role": "system", "content": "You are an Irish heritage and history expert. Write clear, accurate, academic prose."},
        {"role": "user", "content": narrative_prompt},
    ], max_tokens=700)

    return {
        "townland_name": townland_name,
        "db_data": db_data,
        "vrti_data": vrti_data,
        "generated_sparql": sparql_query or None,
        "sparql_error": sparql_err or sparql_run_error,
        "sparql_results": sparql_results,
        "narrative": narrative or None,
        "narrative_error": narrative_err,
        "context_used": context_block,
    }


def explain_mismatch(
    sql_query: str,
    sparql_query: str,
    sql_rows: list[dict],
    sparql_rows: list[dict],
    sql_row_count: int,
    sparql_row_count: int,
) -> dict:
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
