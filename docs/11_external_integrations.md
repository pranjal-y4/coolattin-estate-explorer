# 11 — External Integrations

Technical reference for every module in `backend/integrations/` and the small
amount of glue code outside that directory that talks to those modules. This
covers the VRTI Knowledge Graph SPARQL client, the local GraphDB SPARQL
client, the townlands.ie reference-data loader, SPARQL-injection defenses,
and connection/retry/caching behaviour.

Config values referenced here (`VRTI_SPARQL_ENDPOINT`, `VRTI_REQUEST_TIMEOUT`,
`GRAPHDB_SPARQL_ENDPOINT`, `GRAPHDB_ENABLED`, `GRAPHDB_REQUEST_TIMEOUT`) are
already tabulated in `docs/01_architecture_overview.md` §"Config keys" — not
re-derived here except where the actual client code diverges from what that
table implies (see §1.6).

---

## 1. `backend/integrations/vrti_sparql.py` — VRTI Knowledge Graph client

826 lines. Module docstring calls it out explicitly: **"THIS IS THE ONLY
MODULE THAT CONSTRUCTS SPARQL QUERIES"** against the remote VRTI endpoint.
Routes and services never see SPARQL syntax — they call typed functions that
return dataclasses or plain dicts/lists.

### 1.1 Endpoint and constants

```python
SPARQL_ENDPOINT = "https://virtuoso.virtualtreasury.ie/sparql/"
REQUEST_TIMEOUT = 30  # seconds
```

Both are **module-level literals**, not read from `config.py` / `ActiveConfig`
— see the discrepancy noted in §1.6. `PRESENT_DAY_PLACES_GRAPH` is a second
constant naming the specific named graph almost every query targets:

```python
PRESENT_DAY_PLACES_GRAPH = "https://kg.virtualtreasury.ie/graph/present-day-places-v1"
```

### 1.2 Centralised PREFIX block

Every query is built by string-concatenating a shared `PREFIXES` block in
front of the query body inside `_execute()`:

```python
PREFIXES = """
PREFIX crm:  <http://erlangen-crm.org/current/>
PREFIX vrti: <https://ont.virtualtreasury.ie/ontology#>
PREFIX geo:  <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""
```

`crm:` is **CIDOC-CRM** (Erlangen CRM/OWL variant, `erlangen-crm.org/current/`
— the standard cultural-heritage ontology for describing events, places, and
provenance). The KG uses two CIDOC-CRM properties pervasively:

- `crm:P2_has_type` — typing a place as a `vrti:PresentDayTownland` /
  `vrti:PresentDayParish` / `vrti:PresentDayBarony`.
- `crm:P89_falls_within` — the spatial-containment edge used for the entire
  townland → parish → barony → county hierarchy walk.

Two further CIDOC-CRM properties appear only in `get_townland_details_by_name`
and `get_external_links`: `crm:P67i_is_referred_to_by` (images) and
`crm:P71i_is_listed_in` (external reference links, e.g. logainm.ie).

`vrti:` is the VRTI project's own ontology namespace
(`https://ont.virtualtreasury.ie/ontology#`), used for entity-type classes
(`vrti:PresentDayTownland`, `vrti:PresentDayParish`, `vrti:PresentDayBarony`)
and for identifier datatype properties (`vrti:OsmIdentifier`,
`vrti:OsiIdentifier`, `vrti:VrtiIdentifier`). `geo:` is OGC GeoSPARQL
(`geo:hasGeometry`, `geo:hasCentroid`, `geo:asWKT`) for WKT boundary/centroid
literals. `owl:`, `xsd:`, `skos:` are declared but not visibly used in any of
the query bodies read for this document — likely defensive/future-proofing
declarations.

### 1.3 DTOs (data transfer objects)

Two dataclasses carry parsed KG data out of this module:

| DTO | Fields |
|---|---|
| `TownlandDTO` | `uri, name, name_gaelic, wkt_geometry, centroid_wkt, centroid_lat, centroid_lon, barony, civil_parish, county, osm_id, osi_id, vrti_id, images: list, links: list, centroid_flag` |
| `CensusRecordDTO` | `townland_uri, townland_name, year, male, female, inhabited, uninhabited` |

`TownlandDTO.to_dict()` is the serialisation boundary into JSON API responses
— note it renames `wkt_geometry` → `boundary_wkt` and drops `centroid_wkt`,
`vrti_id` remains, `centroid_flag` is **not** included in the dict (callers
that need the flag must read the dataclass field directly, not the dict).

### 1.4 Public query functions

All nine public functions, in file order:

| Function | Purpose | Called by |
|---|---|---|
| `get_townlands(county=None, limit=2000)` | Bulk fetch of all townlands (+ parish/barony/county via hierarchy join) in a named graph, deduplicated by URI | `townland_service` (cache miss/refresh), `jobs/townlands_ingest.py`, `jobs/full_ingest.py`, `refresh_service.py` |
| `get_wicklow_townlands(limit=2000)` | Back-compat wrapper: `get_townlands(county="Wicklow", ...)` | — |
| `get_townland_details_by_name(name, county=None)` | Full single-townland detail: Gaelic name, centroid, boundary, images, links, OSM/OSI/VRTI IDs. Case-insensitive match, plus reversed `"X or Y"` ↔ `"Y or X"` alternate-name handling | `census_service.py`, `ask_service._kg_context()` (via ThreadPoolExecutor), `jobs/full_ingest.py` |
| `get_census_records_for_townland(townland_uri, years=None)` | Census rows for one townland URI, optional year filter | `census_service` (per-townland cache miss) |
| `get_census_records_for_county(county=None, year=None)` | Census rows for all townlands in a county via the place-hierarchy join, optional year filter, `LIMIT 10000` | `census_service` (full cache miss), `jobs/census_ingest.py`, `jobs/full_ingest.py` |
| `get_parish_names(county=None, limit=200)` | Lightweight list of parish name strings only — no geometry, "significantly faster" per docstring | `ask_service._get_cached_parish_data()` |
| `get_place_hierarchy(entity_uri)` | Single-URI hierarchy lookup → `{townland_name, parish, barony, county}` | `subgraph_engine._expand_vrti()` (Phase 3) |
| `get_sibling_townlands(entity_uri, limit=20)` | 2-hop: same-parish sibling townlands | `subgraph_engine._expand_vrti()` (Phase 3) |
| `get_external_links(entity_uri)` | OSM/OSI/VRTI IDs + external links for one URI | `subgraph_engine._expand_vrti()` (Phase 3) |
| `probe_endpoint()` | Minimal connectivity check (`SELECT (1 AS ?ping) WHERE {}`) | `jobs/townlands_ingest.py`, `jobs/census_ingest.py`, `jobs/full_ingest.py` |

### 1.5 Illustrative query text

The core hierarchy pattern, shown here from `get_townlands()`, recurs (with
variations) in five of the nine functions:

```sparql
SELECT DISTINCT ?Place ?Name ?NameGaelic ?Parish ?Barony ?County ?WKT
WHERE {
  GRAPH <https://kg.virtualtreasury.ie/graph/present-day-places-v1> {
    ?Place crm:P2_has_type vrti:PresentDayTownland ;
           rdfs:label ?Name .
    FILTER(langMatches(lang(?Name), "en"))

    OPTIONAL {
      ?Place rdfs:label ?NameGaelic .
      FILTER(langMatches(lang(?NameGaelic), "ga"))
    }
    OPTIONAL {
      ?Place geo:hasGeometry ?Geom .
      ?Geom geo:asWKT ?WKT .
    }
    OPTIONAL {
      ?Place crm:P89_falls_within ?ParishPlace .
      ?ParishPlace crm:P2_has_type vrti:PresentDayParish ;
                   rdfs:label ?Parish .
      FILTER(langMatches(lang(?Parish), "en"))
      OPTIONAL {
        ?ParishPlace crm:P89_falls_within ?BaronPlace .
        ?BaronPlace crm:P2_has_type vrti:PresentDayBarony ;
                    rdfs:label ?Barony .
        FILTER(langMatches(lang(?Barony), "en"))
        OPTIONAL {
          ?BaronPlace crm:P89_falls_within ?CountyPlace .
          ?CountyPlace rdfs:label ?County .
          FILTER(langMatches(lang(?County), "en"))
        }
      }
    }
  }
  {county_filter}
}
ORDER BY ?Name
LIMIT {limit}
```

Note the recurring `FILTER(langMatches(lang(?X), "en"))` idiom — the KG
stores bilingual `rdfs:label`s (English + Gaelic `"ga"` tag) on the same
subject, so every label fetch must disambiguate by language tag or risk
picking up the Gaelic string where an English one is expected (and vice
versa for `?NameGaelic`, which filters `"ga"`).

The census pattern (`get_census_records_for_townland` /
`get_census_records_for_county`) is structurally different — a flat join, no
recursive OPTIONALs:

```sparql
?CensusRecord vrti:relatesTo ?Townland ;
              vrti:censusYear ?Year ;
              vrti:malePopulation ?Male ;
              vrti:femalePopulation ?Female .
OPTIONAL { ?CensusRecord vrti:inhabitedHouses ?Inhabited . }
OPTIONAL { ?CensusRecord vrti:uninhabitedHouses ?Uninhabited . }
```

The docstring on `get_census_records_for_townland` explicitly flags this as
provisional: *"Census property names (vrti:censusYear etc.) are inferred from
the VRTI ontology pattern. If the KG uses different predicates, update the
query here. The service layer will fall back to CSV seed on empty result."*

### 1.6 Response parsing — SPARQL JSON bindings → Python

`_execute(query: str) -> list[dict]` issues a `GET` against
`SPARQL_ENDPOINT` with `params={"query": full_query, "format":
"application/sparql-results+json"}` and `timeout=REQUEST_TIMEOUT`, then
returns `resp.json()["results"]["bindings"]` — the raw
[SPARQL 1.1 Query Results JSON Format](https://www.w3.org/TR/sparql11-results-json/)
binding list (one dict per row, each dict keyed by SELECT variable name,
each value itself a `{"type": ..., "value": ...}` object).

Two helpers unwrap that shape:

```python
def _val(binding: dict, key: str) -> Optional[str]:
    """Safely extract a string value from a SPARQL binding dict."""
    return binding.get(key, {}).get("value") or None

def _int_val(binding: dict, key: str) -> Optional[int]:
    raw = _val(binding, key)
    if raw is None:
        return None
    try:
        return int(float(raw))          # tolerates "1841.0"-style literals
    except (ValueError, TypeError):
        return None
```

Every public function then loops over bindings, calls `_val`/`_int_val` per
field, and either (a) builds a DTO directly, or (b) — for functions that can
return multiple rows per logical entity due to OPTIONAL fan-out (e.g. a
townland with two parishes, or multiple images/links) — first groups rows by
URI into a `dict[str, dict]` (`seen` in `get_townlands`, `by_uri` in
`get_townland_details_by_name`) using set accumulation for multi-valued
fields (`images`, `links`, `counties`) and first-non-null-wins for
single-valued fields, then builds one DTO per group at the end.

`get_townland_details_by_name` additionally parses WKT `POINT(...)` centroid
literals via `_parse_point_wkt()`, which corrects for a KG data quirk: the
docstring states VRTI stores centroids as `POINT(lat lon)` — "empirically
confirmed as lat-first, which is non-standard GeoSPARQL (lon-first) order."
The function tries both orderings against an Ireland bounding-box sanity
check (`51.0–55.5°N, -11.0..-5.0°W`) and, if neither ordering is plausible,
returns `(None, None, flag)` with a `centroid_out_of_range:(...)` /
`wkt_parse_error:...` / `unknown_point_format` flag string rather than
silently discarding bad data — the caller writes that flag into
`TownlandDTO.centroid_flag`.

### 1.7 Timeout handling

`REQUEST_TIMEOUT = 30` is passed as the single `timeout=` value to every
`requests.get()` call (no separate connect/read timeout split). On
`requests.exceptions.Timeout`, `_execute()` logs
`vrti_sparql.timeout | endpoint=...` at ERROR and **re-raises** — it does not
swallow the exception itself. Every public function wraps its own
`_execute()` call in a `try/except Exception` and converts any exception
(timeout, `RequestException`, JSON decode error) into a safe empty return
(`[]`, `None`, or `{}` depending on the function's return type), logging a
WARNING. Net effect: a VRTI timeout never raises up through the service
layer, but each affected call is silently degraded to "not found"/"empty."

**Discrepancy vs. `config.py`:** `config.py` defines both
`VRTI_SPARQL_ENDPOINT` (hardcoded, matches this module's constant) and
`VRTI_REQUEST_TIMEOUT: int = int(os.environ.get("VRTI_REQUEST_TIMEOUT",
"30"))` (env-overridable). `vrti_sparql.py` does **not** import `config` or
`ActiveConfig` at all (`grep -n "^import\|^from"` on the file shows only
`logging`, `dataclasses`, `typing`, `requests`) — it uses its own hardcoded
module constant `REQUEST_TIMEOUT = 30`. Setting `VRTI_REQUEST_TIMEOUT` in the
environment therefore has **no effect** on the actual client; the config
value is dead as far as this module is concerned. This is the opposite of
`graphdb_sparql.py`, which correctly reads `ActiveConfig.GRAPHDB_REQUEST_TIMEOUT`
at call time (see §2.1). Both values happen to default to `30`, which is
presumably why this divergence has not caused an observed bug.

### 1.8 The five-minute VRTI offline cooldown

**This mechanism does not live in `vrti_sparql.py` itself** — the module has
no retry/circuit-breaker logic of its own; every function is a stateless
request-then-parse-then-degrade. The cooldown is implemented one layer up, in
`backend/services/ask_service.py`, around lines 142–145 and 7659–7793:

```python
_VRTI_PARISH_CACHE: dict[str, Any] = {}
_VRTI_CACHE_TTL = 3600                       # seconds — parish-name cache TTL
_VRTI_STATUS_CACHE: dict[str, Any] = {"down_until": 0.0}
_VRTI_UNAVAILABLE_COOLDOWN = 300             # seconds — the "five-minute" cooldown

def _vrti_temporarily_unavailable() -> bool:
    with _vrti_cache_lock:
        return float(_VRTI_STATUS_CACHE.get("down_until") or 0.0) > time.time()

def _mark_vrti_temporarily_unavailable() -> None:
    with _vrti_cache_lock:
        _VRTI_STATUS_CACHE["down_until"] = time.time() + _VRTI_UNAVAILABLE_COOLDOWN
```

Mechanics:

- **Tracking**: a single module-level dict `_VRTI_STATUS_CACHE = {"down_until": <unix
  timestamp>}` guarded by a `threading.Lock` (`_vrti_cache_lock`). There is no
  per-endpoint or per-function granularity — one process-wide flag covers all
  VRTI calls made through `ask_service`.
- **Trigger**: `_mark_vrti_temporarily_unavailable()` is called from two
  sites — (a) inside `_get_cached_parish_data()` when `vrti_sparql.get_parish_names()`
  either raises or returns an empty list, and (b) inside `_kg_context()`'s
  `ThreadPoolExecutor` loop, in the `except Exception` branch around each
  `vrti_sparql.get_townland_details_by_name()` future — i.e. any single
  failed townland lookup during KG enrichment trips the cooldown for
  *everything*, not just that lookup.
- **Effect while active**: `_kg_context()` checks
  `_vrti_temporarily_unavailable()` before even opening the
  `ThreadPoolExecutor` (`if unique_names and not
  _vrti_temporarily_unavailable():`), and `_get_cached_parish_data()` checks
  it as its very first line and returns `(None, [])` immediately. So once
  tripped, **no VRTI HTTP requests are attempted at all** for the remainder
  of the 300-second window — this is what prevents cascading failures/latency
  when the remote endpoint is down or slow (each Ask request would otherwise
  pay the full 30 s `REQUEST_TIMEOUT` per townland lookup).
- **Reset**: purely time-based — `down_until` is simply a future timestamp;
  once `time.time()` passes it, `_vrti_temporarily_unavailable()` returns
  `False` again and the very next call attempts VRTI again (no explicit
  "reset" call exists; a successful call doesn't clear `down_until` early,
  but that's moot since it will have already expired or never been set).
- **Separate TTL cache**: `_VRTI_PARISH_CACHE` (1-hour TTL, keyed
  `f"parishes:{county}"`) is an independent positive-result cache — it holds
  successfully-fetched parish name lists so repeated questions about the same
  county don't re-hit VRTI even when it's healthy. This is orthogonal to the
  cooldown, which only guards against repeated *failures*.

This cooldown is specific to the KG-enrichment path used by the default
orchestrated Ask pipeline (`_kg_context()`). Other call sites of
`vrti_sparql` — `census_service.py`, `refresh_service.py`, the ingest jobs —
have no equivalent cooldown; they call `vrti_sparql` functions directly and
rely on each function's own per-call try/except-degrade behaviour (§1.7).

---

## 2. `backend/integrations/graphdb_sparql.py` — local GraphDB client

369 lines. Docstring: **"THIS IS THE ONLY MODULE THAT QUERIES THE LOCAL
GRAPHDB INSTANCE."** Talks to a self-hosted GraphDB repository — described in
the module docstring as "D8 RDF/KG comparative prototype" — used to compare
SQL-derived answers against an independently-modelled RDF version of the same
estate data.

### 2.1 Endpoint, config-driven (unlike VRTI client)

Unlike `vrti_sparql.py`, this module **does** import and read config at call
time:

```python
from config import ActiveConfig
...
endpoint = ActiveConfig.GRAPHDB_SPARQL_ENDPOINT
timeout = ActiveConfig.GRAPHDB_REQUEST_TIMEOUT
```

Default endpoint (per `config.py`, documented in `01_architecture_overview.md`):
`http://localhost:7200/repositories/coolattin`. `GRAPHDB_ENABLED` (default
`true`) gates nearly every public function — `query()`, `triple_count()`, and
`get_entity_neighborhood()` all short-circuit to an empty/disabled result if
`ActiveConfig.GRAPHDB_ENABLED` is falsy, without making any HTTP call.

### 2.2 Ontology / prefix — confirms CLAUDE.md's "co: ontology" claim

```python
PREFIXES = """
PREFIX co:     <https://coolattin.ie/ontology#>
PREFIX ex:     <https://coolattin.ie/resource/>
PREFIX schema: <https://schema.org/>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
"""
```

`co:` (`https://coolattin.ie/ontology#`) is a project-specific ontology
distinct from VRTI's CIDOC-CRM-based schema — confirmed. Known `co:`
predicates surfacing in this module's own code (via the `_pred_label()`
lookup table, §2.4): `co:civilParish`, `co:barony`, `co:county`,
`co:inParish`, `co:inBarony`, `co:year`, `co:count`. The LLM-driven SPARQL
generator in `ask_service._generate_graphdb_sparql()` (outside this module —
see §2.6) documents a fuller schema: `co:Person`, `co:hasEvent`,
`co:eventType`, `schema:familyName`, `schema:givenName`, `co:townland`,
`co:parish`, `co:occupation`, `co:estate`.

### 2.3 HTTP method — POST, not GET

```python
# Use POST per SPARQL 1.1 Protocol §2.1.3 — this GraphDB instance hangs on GET
resp = requests.post(
    endpoint,
    data={"query": full_query},
    headers={
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    timeout=timeout,
)
```

This is the opposite of `vrti_sparql._execute()`, which uses `requests.get()`
with `query` as a URL param. The code comment explains why: the local GraphDB
instance "hangs on GET." Response parsing returns both `head.vars` (the
ordered SELECT variable list) and `results.bindings`, unlike VRTI's client
which only ever returns `bindings`.

### 2.4 Public API

| Function | Purpose |
|---|---|
| `query(sparql) -> (columns, rows)` | General-purpose SELECT executor. Returns `([], [])` on disabled/connection-error/timeout/any exception — never raises. Rows are `dict`s keyed by variable name (built from `head.vars`, not `bindings[0].keys()`, specifically so OPTIONAL columns unbound in row 0 still appear as keys in every row — this keeps the shape compatible with SQLite row dicts so the frontend's `renderTable()` JS helper works identically for both backends) |
| `probe(force=False) -> bool` | Cached liveness check via the `/size` REST endpoint (not SPARQL) |
| `triple_count() -> int` | Triple count via `/size`; returns `-1` if disabled or `probe()` fails |
| `get_entity_neighborhood(entity_label, k=2, max_nodes=50) -> list[(s,p,o)]` | 1–2 hop neighbourhood expansion around a named entity, used by `subgraph_engine` |

`query()` is a thin, callers-supply-the-SPARQL executor — it does not
validate or restrict the query text (no read-only/SELECT-only enforcement
visible in this module; see §4 for where such enforcement, if any, actually
happens).

### 2.5 Probe caching (distinct from the VRTI cooldown)

```python
_PROBE_CACHE_TTL_SUCCESS_S = 30.0    # re-check a healthy GraphDB every 30 s
_PROBE_CACHE_TTL_FAILURE_S = 300.0   # don't spam warnings when GraphDB is offline
_probe_cache: dict[str, float | bool | None] = {"checked_at": 0.0, "status": None}
```

`probe(force=False)` uses `time.monotonic()` (not wall-clock `time.time()` —
immune to system clock changes) and an **asymmetric TTL**: a confirmed-healthy
result is trusted for only 30 s before re-probing, but a confirmed-failure
result is trusted for 300 s (same 5-minute figure as the VRTI cooldown, but
implemented independently and for a different purpose — this is a
liveness-probe cache, not a "stop calling the KG entirely" circuit breaker).
`force=True` (used by the two `tests/test_graphdb_sparql.py` tests) bypasses
the cache and always re-probes. The probe itself hits
`{GRAPHDB_SPARQL_ENDPOINT.rstrip('/')}/size` — GraphDB's REST triple-count
endpoint, chosen specifically to "avoid SPARQL query overhead" per the
docstring — with a timeout clamped to `max(1, min(GRAPHDB_REQUEST_TIMEOUT, 5))`
seconds (`_probe_timeout_seconds()`), i.e. never more than 5 s regardless of
the configured request timeout, "so an unhealthy GraphDB does not stall every
Ask request."

`tests/test_graphdb_sparql.py` verifies exactly this caching contract: both
`test_probe_caches_success` and `test_probe_caches_failure` monkeypatch
`requests.get`, call `probe(force=True)` once and `probe()` (uncached) again,
and assert the underlying HTTP call fires only once (`calls["n"] == 1`) —
confirming the second call was served from `_probe_cache` rather than
re-hitting the network, for both the success and failure branches.

### 2.6 Connection-failure handling — graceful, not loud

`query()` explicitly catches `requests.exceptions.ConnectionError` (e.g.
GraphDB not running / nothing listening on `localhost:7200`) and
`requests.exceptions.Timeout` as distinct branches, each logging a WARNING
and returning `([], [])`; a catch-all `except Exception` covers everything
else. **No exception ever propagates out of `query()`.** Same pattern in
`probe()` and `triple_count()`. This matches the module docstring's stated
design goal: "Unavailability is graceful: returns `[]` with a logged
warning." There is no loud-failure mode anywhere in this client — GraphDB
being down never raises, it only ever degrades to empty results plus a log
line.

### 2.7 Verifying CLAUDE.md's "GraphDB SPARQL is dead in the default pipeline" claim

**Claim confirmed as accurate**, with the precise mechanism located:

- In `ask_service._orchestrated_pipeline_stream()` (the default pipeline,
  `ASK_USE_NEW_PIPELINE=true`), `intent_route` is hardcoded:
  `intent_route = "direct"` (line 2895) and is never reassigned anywhere in
  that function.
- Stage 4.5 in that same function (line 3190) is gated by:
  `if ActiveConfig.GRAPHDB_ENABLED and intent_route in (_RELATIONAL, _COMPARATIVE):`
  — since `intent_route` is always `"direct"`, this branch is unreachable
  dead code in the default pipeline, exactly as CLAUDE.md states.
- **Where GraphDB is actually still reachable**: inside
  `answer_question_stream()` (the legacy pipeline, `ASK_USE_NEW_PIPELINE=false`
  — CLAUDE.md's own architecture table identifies this as the inline legacy
  implementation). There, `_intent_route` is computed for real via
  `intent_router.classify_intent()` (line 3857) and can genuinely become
  `"relational"` or `"comparative"`. Two independent GraphDB access paths
  fire in that case:
  1. **`subgraph_engine.retrieve_subgraph()` → `_expand_graphdb()`** (line
     4025 onward calls into `backend/services/subgraph_engine.py`, which
     internally calls `graphdb_sparql.get_entity_neighborhood()` — no LLM
     involved, pure rdfs:label lookup + 1–2 hop traversal; see §2.8).
  2. **`_generate_graphdb_sparql()` + `graphdb_sparql.query()`** (line 4099,
     also gated on `_intent_route in (_RELATIONAL, _COMPARATIVE)`) — an
     LLM-or-template-generated SPARQL SELECT executed for SQL-vs-KG
     cross-validation ("D8 comparative prototype"). Full detail on SQL/SPARQL
     cross-checking and synthesis belongs to
     `docs/07_ask_pipeline_safety_execution_streaming.md`; noted here only to
     confirm the connectivity claim.
- **A third, always-live path regardless of pipeline**: the
  `/api/kg/graphdb-status` route (`backend/routes/kg_explore.py`) calls
  `graphdb_sparql.probe()` and `triple_count()` directly for a live
  health-check UI widget — unrelated to `intent_route` entirely.

### 2.8 `get_entity_neighborhood()` — traversal detail

Called from `subgraph_engine._expand_graphdb(seeds)` with `entity_label =
seed["label"]` (a resolved townland name — from Phase 1 identity resolution
or the entity-resolver's vector index, not raw unvalidated user text) and
`k=2, max_nodes=40`.

Hop 1 finds the subject whose `rdfs:label` case-insensitively matches the
entity label, then all `?subject ?pred ?obj` triples off it (blank nodes and
the object's own `rdfs:label`, if any, fetched via `OPTIONAL`). Hop 2 (when
`k >= 2`) re-queries up to 4 of the hop-1 object URIs (`seen_mid_uris`,
capped `[:4]`) for their own outgoing triples. `_pred_label()` converts
predicate URIs to short human-readable labels for the LLM context window,
explicitly omitting `rdf:type` and GeoSPARQL geometry predicates ("too noisy
in the context window") via a small block-list, falling back to
camelCase-to-words conversion (`_camel_to_words`) for any `co:`/`schema:`
predicate not in its small known-label lookup table.

---

## 3. `backend/integrations/townlands_reference.py` — townlands.ie reference loader

112 lines. This is a **read-only, local-file client** — the module docstring
is explicit that it never performs live scraping:

> "THIS MODULE READS FROM A LOCAL SEED FILE, NOT FROM LIVE SCRAPING."

Source attribution in the docstring: `https://www.townlands.ie/wicklow/`,
described as the "Reconciliation authority for canonical Wicklow townland
names, barony, civil parish, and electoral division context." The seed file
path is:

```python
REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "wicklow_townlands_reference.json"
```

i.e. `data/seed/wicklow_townlands_reference.json`, populated once by running
`python -m coolattin.jobs.townlands_ingest` (a one-shot job; re-running it is
the only way this data refreshes — there is no TTL or staleness check in this
module itself). The docstring gives the rationale for the file-cache-not-live
design: townlands.ie has no public JSON API, scraping on every request would
be "unreliable and rude to the host," and the canonical list "changes
rarely."

### 3.1 Data model and access functions

```python
@dataclass
class TownlandReference:
    name: str                           # canonical English name (townlands.ie)
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    electoral_division: Optional[str] = None
    gaelic_name: Optional[str] = None
    area_ha: Optional[float] = None
    townlands_ie_url: Optional[str] = None
```

- `load_wicklow_reference() -> list[TownlandReference]` — reads and
  JSON-parses the seed file. Returns `[]` with a `log.warning` (pointing the
  operator at the ingest command) if the file is missing, and `[]` with a
  `log.error` on `json.JSONDecodeError`/`OSError`. Never raises.
- `build_name_index(refs) -> dict[str, TownlandReference]` — builds an
  O(1) lookup dict keyed by `normalize_townland_name(ref.name)`, importing
  that normaliser from `backend.services.townland_service` so the key space
  is guaranteed consistent with whatever normalisation the rest of the app
  applies when matching townland names elsewhere. This function is the sole
  reason this module has an internal dependency on the `services` layer
  (imported lazily, inside the function body, to avoid a module-load-time
  circular import).

Client/data-access mechanics stop here — this module has no SQL, no writing,
and no direct role in the ingest-time survivorship/reconciliation algorithm
that actually merges townlands.ie data with VRTI/GeoJSON/estate-CSV data
during a full ingest. That algorithm (field-level provenance winners,
`field_provenance` table) belongs to `docs/03_data_ingestion_and_refresh.md`.

---

## 4. SPARQL injection prevention

Aim/Objective 5.2 (per `docs/introduction.md`): *"Implement per-IP rate
limiting ..., Content Security Policy headers, SPARQL injection prevention,
and read-only SQL enforcement to secure the public-facing API."* Only the
SPARQL-injection piece is in scope for this document; rate limiting, CSP, and
SQL sanitisation belong to the security-focused doc covering `ask_service`'s
`_sanitize_and_validate_sql()`.

### 4.1 Where user-influenced strings actually reach SPARQL text

Across `vrti_sparql.py` and `graphdb_sparql.py`, string values are
interpolated into SPARQL query text via Python f-strings — there is **no
parameterised-query mechanism** in either client (SPARQL protocol has no
native bind-parameter equivalent to SQL placeholders; escaping in the query
string is the only defense available). Two categories of interpolated value
exist:

1. **String literal values** (inserted inside `"..."` quotes in the SPARQL
   text) — these are the injection-relevant case, since an unescaped `"`
   in the value could close the literal early and let attacker-controlled
   SPARQL syntax follow.
2. **URI/IRI values** (inserted inside `<...>` angle brackets, e.g.
   `<{entity_uri}>`, `<{mid_uri}>`) — these are not quoted-string contexts,
   so a `"` character in the value is not directly literal-breaking, but a
   `>` character in an attacker-controlled URI could still break out of the
   `<...>` context. Every URI interpolated this way in the codebase
   originates from a value **already round-tripped through the KG or the
   local `townland` DB table** (`kg_uri` columns, or a `?Place`/`?sibling`
   binding echoed back from a prior query) — never directly from raw HTTP
   request body/query-string text — so these are not user-input-controlled
   in practice, even though the client code itself does not enforce that.

### 4.2 Escaping actually applied

`vrti_sparql.get_townland_details_by_name(name, county=None)`:

```python
name_lower = name.strip().lower().replace('"', '\\"')
```

`graphdb_sparql.get_entity_neighborhood(entity_label, ...)`:

```python
label_lower = entity_label.strip().lower().replace('"', '\\"')
```

Both escape a literal `"` to `\"` before embedding the value inside a SPARQL
double-quoted string literal (`FILTER(LCASE(STR(?Name)) = "{name_lower}")` /
`FILTER(LCASE(STR(?subjectLabel)) = "{label_lower}")`). This is the one
consistent, explicit SPARQL-injection defense present in the integrations
layer, and it covers the two functions whose string argument is closest to
being derived from free-text user input (a townland name typed or matched
from an Ask-page question).

### 4.3 Gaps found (not escaped)

Several other functions interpolate a `county` parameter into a SPARQL string
literal **without** any escaping:

```python
# get_townlands / get_parish_names
county_filter = f'FILTER(STR(?County) = "{county}")' if county else ""
# get_census_records_for_county
FILTER(STR(?CountyLabel) = "{county}")
```

In practice this is low-risk: every call site in the codebase passes a fixed
literal string for `county` — `"Wicklow"` or `None` — hardcoded in the
calling Python, never taken from a request body, query string, or LLM output
(confirmed via `grep` across `backend/services/*.py`, `backend/routes/*.py`,
`backend/jobs/*.py` — every call is `county="Wicklow"` or `county=None`). So
the gap is real at the code level (the function itself offers no protection
if ever called with a user-supplied county) but not currently exploitable
through any live code path.

A related, adjacent gap exists just outside the two integrations modules
proper: `backend/services/semantic_layer.py`'s `compile_sparql()` (used to
build a GraphDB aggregate query for the legacy pipeline's ANALYTICAL route)
reuses a helper called `_esc()`:

```python
def _esc(value: str) -> str:
    """Escape single-quote in a value embedded in a SQL string literal."""
    return str(value).replace("'", "''")
```

— explicitly documented as a **SQL**-literal escaper (doubling `'`), but it
is also applied to a value destined for a SPARQL **double**-quoted literal:
`filter_triples.append(f'FILTER(UCASE(STR(?name)) = "{norm}")')` where `norm
= _esc(str(sf.filters["townland"]))`. Doubling single quotes does nothing to
protect a double-quoted SPARQL string; a `"` character in a slot-filled
townland value would not be escaped by `_esc()`. This function lives outside
`backend/integrations/` (it belongs to the semantic-layer/slot-fill
subsystem) and is only reachable via the legacy pipeline's rule-based
ANALYTICAL fast lane, so it is noted here for completeness rather than
covered in depth — full slot-fill compiler detail belongs to whichever doc
covers `semantic_layer.py`.

### 4.4 Read-only enforcement

All SPARQL issued by both clients is `SELECT`-only by construction — no
function in `vrti_sparql.py` or `graphdb_sparql.py` builds `INSERT`,
`DELETE`, `LOAD`, or `CLEAR` SPARQL Update statements, and `graphdb_sparql.query()`
(the one function that accepts a caller-supplied SPARQL string rather than
building its own) does not validate that the passed string is a `SELECT` —
it relies entirely on callers (the LLM-SPARQL-generation prompt in
`ask_service._generate_graphdb_sparql()`, which instructs the model to
output "a SPARQL 1.1 SELECT query" only) to never pass an update operation.
There is no query-type allowlist/regex check inside `graphdb_sparql.py`
itself equivalent to `ask_service._sanitize_and_validate_sql()`'s SQL
read-only guard.

---

## 5. Retry logic, connection pooling, session reuse, caching

| Mechanism | VRTI client | GraphDB client |
|---|---|---|
| HTTP library calls | `requests.get()` per call (module-level function, no `requests.Session`) | `requests.post()` / `requests.get()` per call (module-level function, no `requests.Session`) |
| Connection pooling | None explicit — each call opens a fresh connection via the `requests` default adapter (no shared `Session`, so no urllib3 connection-pool reuse across calls) | Same — no `Session` object anywhere in the module |
| Retry-on-failure | **None** — a single failed request is not retried; the calling function catches the exception and degrades to an empty result | **None** — same pattern |
| Result caching | None inside `vrti_sparql.py` itself. Caching exists one layer up in `ask_service.py`: `_VRTI_PARISH_CACHE` (1 h TTL, parish names) and the townland-detail lookups implicitly cached by `census_service`'s DB-first read pattern (out of scope here — see the census/data-ingestion doc) | None inside `graphdb_sparql.py` for query *results*. The only caching is the `probe()` liveness cache (§2.5, 30 s success / 300 s failure TTL) — actual SPARQL `query()` results are never cached in this module |
| Circuit breaker / cooldown | The 5-minute `_VRTI_UNAVAILABLE_COOLDOWN` in `ask_service.py` (§1.8) — external to this module | No equivalent full circuit-breaker; the `probe()` cache's 300 s failure TTL serves a similar dampening role but only gates the lightweight `/size` probe, not `query()` calls made without first checking `probe()` |

Neither client uses `urllib3.util.Retry`, `tenacity`, or any other retry
library — confirmed via `grep -n "Session(\|Retry\|urllib3\|retry"` across
both files, which returns no matches.

---

## 6. LLM provider status checking

`tests/test_llm_status.py` exercises
`ask_service._friendly_openrouter_connection_issue(exc)` — a pure function
that pattern-matches an already-raised connection exception's string
representation (DNS failure, timeout, etc.) and returns a
`(friendly_hint, technical_detail, issue_code)` tuple for display in the Ask
UI (`issue_code` values observed in the tests: `"dns_unreachable"`,
`"timeout"`). This function, and the LLM cascade it supports
(Claude → Grok → OpenRouter/Ollama, per `docs/07_ask_pipeline_safety_execution_streaming.md`),
**live entirely in `backend/services/ask_service.py`**, not in
`backend/integrations/`. There is no `backend/integrations/` module for any
LLM provider — OpenRouter/Anthropic/xAI HTTP calls are made directly from
`ask_service.py`. Nothing further on LLM connectivity is documented here to
avoid duplicating `docs/07_ask_pipeline_safety_execution_streaming.md`, which
owns the full cascade, synthesis, and hallucination-gate detail.

---

## 7. Summary of verified/corrected claims

| Claim (source) | Verdict |
|---|---|
| "co: ontology" used by local GraphDB (CLAUDE.md) | **Confirmed** — `PREFIX co: <https://coolattin.ie/ontology#>` in `graphdb_sparql.py` |
| GraphDB SPARQL is "dead" in the default pipeline because `intent_route` is always `"direct"` (CLAUDE.md) | **Confirmed** — `intent_route = "direct"` hardcoded in `_orchestrated_pipeline_stream()`; the gating `if ... intent_route in (_RELATIONAL, _COMPARATIVE)` at Stage 4.5 is unreachable there |
| GraphDB is reachable via the legacy pipeline's RELATIONAL route | **Confirmed and expanded** — two distinct paths: `subgraph_engine._expand_graphdb()` (no LLM) and `_generate_graphdb_sparql()` + `graphdb_sparql.query()` (LLM/template-generated), both inside `answer_question_stream()`; plus a third always-on path, `/api/kg/graphdb-status` |
| Five-minute VRTI offline cooldown to prevent cascading failures (project memory) | **Confirmed, located precisely** — `_VRTI_UNAVAILABLE_COOLDOWN = 300` in `ask_service.py`, tracked via module-level `_VRTI_STATUS_CACHE["down_until"]` timestamp, reset only by time elapsing, guards `_kg_context()` and `_get_cached_parish_data()` |
| `VRTI_REQUEST_TIMEOUT` config value governs the VRTI client's timeout | **False / dead config** — `vrti_sparql.py` never imports `config`; it hardcodes `REQUEST_TIMEOUT = 30` independently. Both happen to equal `30` by coincidence, masking the disconnect |
| SPARQL injection prevention exists (Objective 5.2) | **Partially confirmed** — quote-escaping present and correct in the two functions handling free-text-derived names (`get_townland_details_by_name`, `get_entity_neighborhood`); absent for `county` parameters (currently safe only because all call sites pass hardcoded literals) and inconsistent in `semantic_layer._esc()` (a SQL escaper reused for a SPARQL double-quoted context) |
