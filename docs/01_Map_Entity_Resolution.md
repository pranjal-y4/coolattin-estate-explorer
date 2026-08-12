# Map Entity Resolution, Data Cleaning & Townland Mapping

## Overview

The Explore Map page visualises historical Coolattin Estate townlands on an interactive Leaflet.js map. Behind the scenes, a multi-stage pipeline stitches together three heterogeneous sources — a hand-digitised estate GeoJSON file, the VRTI Knowledge Graph (a remote Virtuoso RDF endpoint), and a townlands.ie reference snapshot — into a single coherent townland record per place. Because each source uses slightly different name spellings, character encodings, and administrative hierarchies, entity resolution and data cleaning are critical to making the map work correctly.

---

## Data Sources

| Source | Format | What it provides |
|---|---|---|
| Estate GeoJSON (`frontend/static/data/townlands.json`) | GeoJSON FeatureCollection | Polygon geometry, estate identifiers (TD_ID, GUID), area (m²), Irish-language name, population survey columns (1827–1868), clearances columns (1847–1856) |
| VRTI Knowledge Graph | SPARQL / RDF (Virtuoso) | Semantic centroid (WKT POINT), full polygon boundary (WKT), place hierarchy (civil parish → barony → county), OSM/OSI/VRTI identifiers, linked images and external references, standard census years (1841–1891) |
| Townlands.ie Reference Snapshot (`data/seed/wicklow_townlands_reference.json`) | JSON | Canonical English spelling, Gaelic name, barony, civil parish, electoral division, area in hectares |

Each source is authoritative for different fields. The ingest pipeline merges them while using the normalised townland name as the shared join key.

---

## Entity Resolution Pipeline

### Stage 1 — Name Normalisation

Every townland name — regardless of source — is passed through `townland_service.normalize_townland_name()` before it is stored, queried, or matched. This function applies five sequential transformations:

1. **Strip and collapse whitespace** — removes leading/trailing spaces and collapses internal runs of spaces or tabs to a single space.
2. **Remove "Townland of" prefix** — the estate GeoJSON occasionally prefixes names with this administrative label; it is stripped entirely.
3. **Remove parenthetical qualifiers** — suffixes such as *(Electoral Division)*, *(Civil Parish)*, or *(Upper)* carried inside parentheses are discarded, because the same place appears in multiple sources with and without them.
4. **Remove punctuation, preserving hyphens and apostrophes** — commas, full stops, and other punctuation are stripped; hyphens (e.g., *Ballin-a-cor*) and apostrophes (e.g., *O'Brien's*) are retained because they are semantically meaningful.
5. **Uppercase** — the final canonical form is stored entirely in uppercase. This collapses all case variants (*Ballinacor*, *BALLINACOR*, *ballinacor*) into one key.

```
"Townland of Ballinacor (Upper)"  →  "BALLINACOR"
"coolbawn or coolballintaggart"   →  "COOLBAWN OR COOLBALLINTAGGART"
```

The function is defined in `backend/services/townland_service.py` and imported by every ingest job and service that handles townland names.

### Stage 2 — Alias Resolution

After normalisation, the name is passed through `townland_service.resolve_alias()`. This function consults a module-level alias map loaded once at import time from `data/seed/townland_aliases.json`. The alias map records historical, anglicised, and variant spellings that normalisation alone cannot collapse:

```json
{
  "BALLINACOR UPPER": "BALLINACOR",
  "BALLINACOR LOWER": "BALLINACOR",
  "KILCAVAN": "KILCAVAN UPPER"
}
```

If the normalised name has an entry in the alias map, it is replaced with the canonical form. If not, the name is returned as-is. This two-step approach — normalise first, alias-resolve second — keeps the alias map minimal (only genuine variant spellings, not every casing or punctuation variation).

The full pipeline is exposed as `canonical_name(raw)`, which chains both steps:

```python
def canonical_name(raw: str) -> str:
    return resolve_alias(normalize_townland_name(raw))
```

### Stage 3 — VRTI Knowledge Graph Case-Insensitive Lookup

When the ingest pipeline enriches a townland from the VRTI KG, it calls `vrti_sparql.get_townland_details_by_name(name, county)`. The SPARQL query performs a case-insensitive match using `LCASE(STR(?Name))` rather than a string equality check:

```sparql
FILTER(LCASE(STR(?Name)) = LCASE(?searchName))
```

This means a townland stored in the local DB as `"BALLYMANUS"` can be matched against the KG record labelled `"Ballymanus"` without any pre-processing of the KG result.

The KG sometimes records a single place under two names separated by "or" — for example, *"Coolbawn or Coolballintaggart"*. The SPARQL client handles this in two ways:

- It searches for the exact normalised name.
- If no result is found, it splits on " or " and attempts a reversed search (*"Coolballintaggart or Coolbawn"*), so that direction of the alias does not matter.

### Stage 4 — Reference Reconciliation

After ingest from the KG, each townland is passed to `townland_service.reconcile_with_reference()`. This function loads the townlands.ie reference snapshot into an in-memory dict keyed by normalised name:

```python
name_index = build_name_index(load_wicklow_reference())
```

For each townland in the DB, if a matching entry is found in the index, the fields `barony`, `civil_parish`, `electoral_division`, and `gaelic_name` are written back to the DB row. These fields are not always available from the VRTI KG directly.

When a townland from the estate GeoJSON cannot be matched in the reference index — because the estate name is a historical variant not present in the modern townlands.ie dataset — the mismatch is logged to `data/source_snapshots/reconciliation_gaps.csv` for manual review. This file is gitignored and accumulates over ingest runs.

### Stage 5 — SPARQL Deduplication

The VRTI KG sometimes returns multiple rows for the same townland URI — for example, if a townland spans two civil parishes, the SPARQL query returns one row per parish. The `get_townlands()` function deduplicates results by URI:

```python
seen_uris: dict[str, TownlandDTO] = {}
for row in results:
    if row.uri in seen_uris:
        # Fill missing fields from the duplicate row
        existing = seen_uris[row.uri]
        for field in ["barony", "civil_parish", ...]:
            if not getattr(existing, field):
                setattr(existing, field, getattr(row, field))
    else:
        seen_uris[row.uri] = row
```

This ensures each townland is represented by exactly one record, with the most complete set of fields drawn from all rows.

---

## Data Cleaning Details

### WKT Centroid Parsing

VRTI returns centroids as WKT POINT strings. The format used by VRTI is non-standard — it uses `POINT(lat lon)` rather than the GeoSPARQL convention of `POINT(lon lat)`. The parser `_parse_point_wkt()` applies a sanity check to detect and correct coordinate swaps:

- Irish latitude falls between 51°N and 55°N.
- Irish longitude falls between −5° and −10° (west).

If the parsed values fail this sanity check, the parser swaps the two values and logs a warning. If both orderings fail the check, the centroid is discarded and the townland falls back to a computed centroid from the GeoJSON polygon.

### GeoJSON Centroid Computation

Where VRTI does not provide a centroid, `build_centroids_from_geojson()` computes one from the estate GeoJSON polygon. The computation averages all coordinates of the outer ring:

```python
coords = feature["geometry"]["coordinates"][0]  # outer ring
lat = sum(c[1] for c in coords) / len(coords)
lon = sum(c[0] for c in coords) / len(coords)
```

This is a simple arithmetic centroid rather than a true geometric centroid (which would require area-weighted computation), but it is accurate enough for placing map markers within estate-scale polygons.

### Unicode Normalisation

Fancy typographic apostrophes (`'`, `'`) and quotation marks that occasionally appear in Irish place names scraped from web sources are converted to standard ASCII apostrophes before normalisation, ensuring `"O’Brien"` and `"O'Brien"` normalise to the same canonical form.

### Population and Clearances Columns

The estate GeoJSON encodes historical population and clearances data as numeric columns on each feature's `properties` object:

| Column | Years | Meaning |
|---|---|---|
| `T_POP_1827`, `T_POP_1839`, `T_POP_1848`, `T_POP_1850`, `T_POP_1860`, `T_POP_1868` | 1827–1868 | Estate population surveys |
| `Clearances_1847` … `Clearances_1856` | 1847–1856 | Evictions per year |

During ingest, these are extracted and written as individual rows in the `census_record` and `clearances_record` tables with `source='json'`. Missing or null values are treated as zero rather than as absent, because the estate GeoJSON uses zero to mean "no recorded evictions" rather than "data not collected".

---

## Database Schema for Townlands

The `townland` table (created and migrated by `extensions.py::ensure_schema()`) stores the merged record:

```sql
CREATE TABLE townland (
    id               INTEGER PRIMARY KEY,
    name             TEXT UNIQUE NOT NULL,      -- canonical UPPERCASE
    name_gaelic      TEXT,
    barony           TEXT,
    civil_parish     TEXT,
    electoral_division TEXT,
    placename_theme  TEXT,
    description      TEXT,
    td_id            TEXT,                      -- estate GeoJSON identifier
    guid             TEXT,                      -- estate GeoJSON GUID
    area_sqm         REAL,                      -- from GeoJSON AREA
    kg_uri           TEXT,                      -- VRTI KG URI
    wkt_geometry     TEXT,                      -- full polygon WKT
    centroid_lat     REAL,
    centroid_lon     REAL,
    county           TEXT,
    osm_id           TEXT,
    osi_id           TEXT,
    vrti_id          TEXT,
    images_json      TEXT DEFAULT '[]',
    links_json       TEXT DEFAULT '[]',
    source           TEXT                       -- 'json' | 'kg' | 'manual'
);
```

The `source` column records which pipeline stage was the last to write the record:

- `'json'` — from the estate GeoJSON only; KG enrichment has not run or found no match.
- `'kg'` — successfully enriched with KG geometry and centroid.
- `'manual'` — hand-corrected entry.

The upsert logic uses `COALESCE` to avoid overwriting populated fields with nulls from a later incomplete ingest run.

---

## Map Rendering Pipeline

Once the townland table is populated, the map page works as follows:

1. **Frontend requests layer config** — `GET /api/map/layers` returns the tile layer URLs (OSM, Esri Satellite, OpenTopoMap). The frontend never hardcodes tile URLs.
2. **Frontend loads GeoJSON** — The estate GeoJSON is served as a static file from `frontend/static/data/townlands.json`. Leaflet renders the polygon boundaries directly from this file.
3. **Frontend requests centroids** — For placing clickable markers, the frontend calls `GET /api/townlands` which returns all DB rows including `centroid_lat` and `centroid_lon`.
4. **Fuzzy townland suggestion** — The Ask page's townland hint field calls `GET /api/ask/townland-suggest?q=<partial>` which uses `rapidfuzz.fuzz.token_set_ratio` (falling back to `difflib` if rapidfuzz is not installed) to return the 8 best-matching townland names from the full catalog. This lets users type approximate names and still find the correct townland.

### Layer Switcher

`map.js::initLayerSwitcher()` fetches the layer config, constructs Leaflet `TileLayer` objects, and renders a custom layer switcher UI. The user's last-selected layer is persisted in `localStorage` so it survives page reloads. When the user switches to the satellite layer, the overlay labels layer is automatically enabled.

---

## Summary of Entity Resolution Decision Tree

```
Raw townland name (any source)
        │
        ▼
normalize_townland_name()
  • collapse whitespace
  • strip "Townland of" prefix
  • remove parenthetical qualifiers
  • remove non-essential punctuation
  • uppercase
        │
        ▼
resolve_alias()
  • consult townland_aliases.json
  • replace known variant → canonical form
        │
        ▼
canonical_name  (e.g. "BALLINACOR")
        │
        ├──► DB lookup (exact match on UNIQUE name column)
        │
        ├──► KG lookup (LCASE SPARQL filter + "or" variant fallback)
        │
        └──► Reference reconciliation (name_index from townlands.ie snapshot)
                    │
                    ▼
             Enriched DB row
             (geometry, hierarchy, identifiers, media)
                    │
                    ▼
             Map marker at (centroid_lat, centroid_lon)
             with popup showing barony, civil parish, population history
```
