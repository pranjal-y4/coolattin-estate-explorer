# RQ4 Results — Geospatial and Knowledge-Graph Enrichment

Maps to Section 6.5. Produced by `eval_plan/scripts/rq4_enrichment.py`, run
2026-08-03. Raw output: `eval_plan/evidence/RQ4_raw_output.txt`.

**Scope note, read first**: every number below is against the **local, in-process
GraphRAG substrate** (`graph_nodes`/`graph_edges`, built by `scripts/build_graph.py`,
loaded into NetworkX at runtime — see `backend/services/graphrag.py`). This is a
**separate system from the external GraphDB SPARQL store** (the co: ontology instance
on the Azure VM at `51.120.71.162:7200`) that the `/kg-explore` page's comparison tool
uses. I could not probe the external GraphDB from this session — the pre-allowed probe
commands target `localhost` (the local Flask dev server and a local GraphDB port), and
neither was running. **If you need the external GraphDB's live state (empty vs.
populated) for the 143,123-triple figure, that needs to be checked separately** (e.g.
by starting the app locally and hitting `/api/kg/graphdb-status`, or reaching the actual
VM). Do not present the coverage figures below as evidence about the external GraphDB —
they are entirely about the local GraphRAG graph.

---

## Coverage by context type

| Context type | Total | Enriched | Coverage | Note |
|---|---|---|---|---|
| **Administrative geography** (Townland → CivilParish/Barony/County, `WITHIN` edges) | 152 (estate townlands) | 133 | **87.5%** | Full 4,225-row national townland reference: only 184/4,225 (4.4%) — scope any claim to the 152 estate townlands, not the full reference table |
| **Connected records** (workhouse-to-estate links, `LINKED_TO` edges) | 5,134 (`workhouse_unified_links` rows) | 140 | **2.7%** | **Stale** — see below |
| **Population patterns** (census/clearance observations, `HAS_OBSERVATION` edges) | 8,033 census + 1,211 clearances | 8,033 + 1,211 | **100% / 100%** | Every census and clearance record has a graph edge |
| **Landscape features** (heritage monuments/wells/sites) | 366 (`heritage_feature` rows) | 0 | **0%** | Not ingested into the graph substrate at all |

### Connected records — real and important: the graph is stale

The GraphRAG substrate has only **140** `LINKED_TO` (WorkhouseRecord→Person) edges, but
the actual `workhouse_unified_links` table currently holds **5,134** rows (873
`CONFIRMED_MATCH` + 4,261 `POSSIBLE_MATCH` — see the main evidence doc's ER-numbers
finding). The graph was evidently built (`scripts/build_graph.py`) at an earlier point
when far fewer links existed, and has not been rebuilt since the entity-resolution
pipeline was re-run/expanded. **Any GraphRAG-retrieved answer about a specific person's
workhouse connection is drawing on a dataset 37x smaller than what's actually linked in
the database.** This is a genuine, reportable gap — either re-run `scripts/build_graph.py`
before final evaluation, or report this staleness explicitly as a known limitation with
the exact before/after numbers above.

### Landscape features — a genuine, total gap, not a rounding error

Zero heritage-related node labels exist anywhere in `graph_nodes` (checked directly by
label pattern match — no `Heritage`/`Monument`/`Well`-labelled rows at all). The 366
`heritage_feature` rows are served only via the dedicated `/heritage` page and its own
static GeoJSON files (per `CLAUDE.md`'s architecture notes) — they were never ingested
into the graph substrate that GraphRAG traversal uses. Any Ask-page question that
should draw on landscape/heritage context via GraphRAG structurally cannot — there is
nothing there to retrieve. This is worth stating as a named limitation/future-work item
in §7.3 and §8.3, not glossed over.

### Population patterns — fully covered, the one clean 100%

Both census (8,033/8,033) and clearances (1,211/1,211) observations have exact 1:1 edge
coverage in the graph — every row in both tables is reachable via `HAS_OBSERVATION` from
its `Townland` node. This is the one context type where the coverage claim can be made
without caveats.

---

## Edge completeness (LOCATED_IN)

Repeats the figure from the main evidence doc, included here for completeness against
the master plan's table shape:

| | Value |
|---|---|
| Person nodes | 13,707 |
| With `LOCATED_IN` edge | 9,095 |
| **Gap** | **4,612 (33.6%)** |

---

## Precision spot-checks — not run in this pass

The eval plan asks for a 15-30 sample precision check per context type against an
authoritative source (geo boundaries, census figures, heritage register). I did not
attempt this — it requires either manual visual verification against maps/records or a
live network call to an external geocoding/authority service, and I didn't want to fire
external network requests without checking scope with you first. If you want this, tell
me which authority source to check against (OSM Nominatim, OSI boundaries, the NMS
heritage register) and I can build a sampling + comparison script; the actual
correct/incorrect judgment on landscape/heritage items may still need your eye rather
than an automated check.

---

## Summary table for §6.8 / master plan matrix

| Metric | Result | N | Target | Verdict |
|---|---|---|---|---|
| Coverage — administrative geography | 87.5% | 152 | report honestly | Reasonably met |
| Coverage — connected records | 2.7% | 5,134 real links | report the gap | **Not met — stale graph, needs rebuild** |
| Coverage — population patterns | 100% / 100% | 8,033 + 1,211 | report honestly | Met |
| Coverage — landscape features | 0% | 366 | report the gap | **Not met — total gap, structural** |
| LOCATED_IN edge completeness | 66.4% (33.6% gap) | 13,707 | report the gap | Named future-work item |
| Precision (any type) | Not measured | — | >90% | Not run — needs authoritative source + scope decision |
