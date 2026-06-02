# Graph Data Uplift — What Data is Lifted Into the Knowledge Graph Layer

## Overview

"Graph uplift" refers to the process of converting flat, tabular, or semi-structured historical data into RDF-compatible representations that can be queried via SPARQL and linked to external knowledge graphs. This project performs uplift in two directions:

1. **Inward uplift** — pulling structured facts from the VRTI Knowledge Graph (KG) into the local SQLite database, enriching estate records with semantic identifiers, geometries, and administrative hierarchies that the estate sources alone do not contain.
2. **Outward uplift** — representing Coolattin-specific estate data (tenancies, evictions, emigrations, census surveys) as RDF triples in a local GraphDB repository using a purpose-built `co:` ontology, enabling SPARQL queries over the estate as a graph.

Both directions are active in the current codebase. The inward uplift runs every time `full_ingest.py` or `census_ingest.py` executes. The outward uplift populates the local GraphDB repository used by the experimental GraphRAG path.

---

## Inward Uplift — VRTI Knowledge Graph → SQLite

### What VRTI Is

The Virtual Record Treasury of Ireland (VRTI) Knowledge Graph is a Linked Data repository hosted at `https://virtuoso.virtualtreasury.ie/sparql/`. It represents present-day Irish places — townlands, parishes, baronies, counties — as RDF resources conforming to a combination of the CIDOC-CRM ontology, the GeoSPARQL vocabulary, and a VRTI-specific extension namespace (`vrti:`).

The Coolattin application treats VRTI as an authoritative external source for three types of data:
- **Spatial data** — polygon boundaries and centroids for Irish townlands.
- **Administrative hierarchy** — the formal containment relationships between townland, parish, barony, and county.
- **Identifiers** — OSM IDs, OSI IDs, and VRTI internal URIs that link each place to external databases.

### VRTI Ontology Vocabulary

```
PREFIX crm:   <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX vrti:  <https://virtualtreasury.ie/ontology/>
PREFIX geo:   <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:   <http://www.w3.org/2002/07/owl#>
PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>
```

### VRTI Named Graph

All present-day place data lives in the named graph:

```
https://kg.virtualtreasury.ie/graph/present-day-places-v1
```

Queries must include `FROM <...>` or `GRAPH <...>` to scope results to this graph.

### Place Hierarchy in RDF

The KG models administrative containment as `crm:P89_falls_within` triples:

```turtle
<townland_uri>
    a                       crm:E53_Place ;
    crm:P2_has_type         vrti:PresentDayTownland ;
    rdfs:label              "Ballinacor"@en ;
    rdfs:label              "Baile na Cora"@ga ;
    crm:P89_falls_within    <parish_uri> .

<parish_uri>
    a                       crm:E53_Place ;
    crm:P2_has_type         vrti:PresentDayParish ;
    rdfs:label              "Wicklow"@en ;
    crm:P89_falls_within    <barony_uri> .

<barony_uri>
    crm:P89_falls_within    <county_uri> .
```

A single SPARQL traversal can therefore return a townland's full administrative address (civil parish, barony, county) in one query without multiple database joins.

### Geometry Uplift

Each townland resource carries two spatial predicates:

```turtle
<townland_uri>
    geo:hasCentroid  [
        geo:asWKT  "POINT(52.9874 -6.4123)"^^geo:wktLiteral
    ] ;
    geo:hasBoundingBox [ ... ] .
```

The WKT centroid is parsed by `vrti_sparql._parse_point_wkt()` and written to `centroid_lat` / `centroid_lon` in the SQLite `townland` table. The full polygon WKT (when available) is written to `wkt_geometry`.

### Identifier Uplift

Each townland resource in the KG carries formal cross-reference identifiers:

```turtle
<townland_uri>
    vrti:OsmIdentifier   "12345678" ;
    vrti:OsiIdentifier   "OSI-TL-000123" ;
    vrti:VrtiIdentifier  "VRTI-TL-4521" .
```

These are stored in `osm_id`, `osi_id`, and `vrti_id` columns in the `townland` table, enabling direct URL construction to OpenStreetMap (`https://www.openstreetmap.org/relation/<osm_id>`) and the OSI mapping portal.

### Media and Link Uplift

Each townland can have associated images and external links in the KG:

```turtle
<townland_uri>
    crm:P67i_is_referred_to_by  <image_uri> ;
    crm:P71i_is_listed_in       <link_uri> .
```

These are aggregated into JSON arrays and stored as `images_json` and `links_json` on the townland row. The frontend renders them in the map popup for enriched place information.

### Census Data Uplift

The VRTI KG also contains structured census records linked to place URIs. Each census record describes population counts for a given townland and year:

```turtle
<census_uri>
    vrti:censusYear           "1841"^^xsd:integer ;
    vrti:malePopulation       "234"^^xsd:integer ;
    vrti:femalePopulation     "229"^^xsd:integer ;
    vrti:inhabitedHouses      "42"^^xsd:integer ;
    vrti:uninhabitedHouses    "3"^^xsd:integer ;
    crm:P78i_is_identified_by <townland_uri> .
```

The `census_ingest.py` job queries these records for County Wicklow and writes them as rows in the local `census_record` table with `source='kg'`. This covers the standard decennial census years (1841, 1851, 1861, 1871, 1881, 1891).

### SPARQL Queries Used for Uplift

**Townland uplift** (`vrti_sparql.get_townlands()`):

```sparql
SELECT DISTINCT ?Place ?Name ?NameGaelic
       ?CentroidWKT ?Barony ?Parish ?County
       ?OsmId ?OsiId ?VrtiId ?Image ?Link
FROM <https://kg.virtualtreasury.ie/graph/present-day-places-v1>
WHERE {
    ?Place a crm:E53_Place ;
           crm:P2_has_type vrti:PresentDayTownland ;
           rdfs:label ?Name .
    FILTER(langMatches(lang(?Name), "en"))

    OPTIONAL { ?Place rdfs:label ?NameGaelic .
               FILTER(langMatches(lang(?NameGaelic), "ga")) }
    OPTIONAL { ?Place geo:hasCentroid/geo:asWKT ?CentroidWKT }
    OPTIONAL { ?Place crm:P89_falls_within ?Parish_Place .
               ?Parish_Place crm:P2_has_type vrti:PresentDayParish ;
                             rdfs:label ?Parish . }
    OPTIONAL { ... barony ... }
    OPTIONAL { ... county ... }
    OPTIONAL { ?Place vrti:OsmIdentifier ?OsmId }
    OPTIONAL { ?Place vrti:OsiIdentifier ?OsiId }
    OPTIONAL { ?Place vrti:VrtiIdentifier ?VrtiId }
    OPTIONAL { ?Place crm:P67i_is_referred_to_by ?Image }
    OPTIONAL { ?Place crm:P71i_is_listed_in ?Link }
}
LIMIT 2000
```

**Census uplift** (`vrti_sparql.get_census_records_for_county()`):

```sparql
SELECT ?TownlandURI ?TownlandName ?Year
       ?Male ?Female ?Inhabited ?Uninhabited
FROM <https://kg.virtualtreasury.ie/graph/present-day-places-v1>
WHERE {
    ?Place crm:P2_has_type vrti:PresentDayTownland ;
           rdfs:label ?TownlandName .
    FILTER(langMatches(lang(?TownlandName), "en"))

    ?CensusRecord vrti:censusYear ?Year ;
                  vrti:malePopulation ?Male ;
                  vrti:femalePopulation ?Female ;
                  crm:P78i_is_identified_by ?Place .
    OPTIONAL { ?CensusRecord vrti:inhabitedHouses ?Inhabited }
    OPTIONAL { ?CensusRecord vrti:uninhabitedHouses ?Uninhabited }

    ?Place crm:P89_falls_within* ?County_Place .
    ?County_Place crm:P2_has_type vrti:PresentDayCounty ;
                  rdfs:label "Wicklow"@en .
}
```

---

## Outward Uplift — Estate Data → Local GraphDB (RDF)

### Purpose

The local GraphDB instance (`http://localhost:7200/repositories/coolattin`) holds the Coolattin estate records expressed as RDF triples using a purpose-built ontology. This is Dissertation objective D8 — demonstrating that the estate's tabular records can be semantically uplifted and queried as a knowledge graph, enabling graph-native operations (path traversal, semantic joins, ontological inference) that are not possible with relational SQL.

### Coolattin Ontology Namespaces

| Prefix | Namespace | Purpose |
|---|---|---|
| `co:` | `https://coolattin.ie/ontology#` | Coolattin-specific classes and properties |
| `ex:` | `https://coolattin.ie/resource/` | Named resources (townlands, persons, events) |
| `schema:` | `https://schema.org/` | Reused Schema.org vocabulary for persons, places, events |
| `xsd:` | `http://www.w3.org/2001/XMLSchema#` | Datatypes |
| `rdf:`, `rdfs:`, `owl:` | W3C standard | RDF model, labels, class hierarchy |

### Classes Modelled in the Coolattin Ontology

**`co:Townland`** — a named place in the Coolattin estate:

```turtle
ex:Ballinacor
    a              co:Townland ;
    rdfs:label     "Ballinacor"@en ;
    co:canonicalName "BALLINACOR" ;
    co:civilParish  ex:Parish_Rathdrum ;
    co:barony       ex:Barony_Ballinacor ;
    schema:geo      [ schema:latitude  "52.9874"^^xsd:decimal ;
                      schema:longitude "-6.4123"^^xsd:decimal ] .
```

**`co:CensusRecord`** — a population count for a townland in a specific year:

```turtle
ex:Census_Ballinacor_1841
    a                   co:CensusRecord ;
    co:forTownland      ex:Ballinacor ;
    co:year             "1841"^^xsd:gYear ;
    co:malePop          "234"^^xsd:integer ;
    co:femalePop        "229"^^xsd:integer ;
    co:inhabitedHouses  "42"^^xsd:integer ;
    co:source           "kg" .
```

**`co:ClearancesRecord`** — an eviction event for a townland in a specific year:

```turtle
ex:Clearance_Ballinacor_1847
    a               co:ClearancesRecord ;
    co:forTownland  ex:Ballinacor ;
    co:year         "1847"^^xsd:gYear ;
    co:evictedCount "12"^^xsd:integer ;
    co:source       "json" .
```

**`co:Person`** — an individual from the unified estate records (tenants, emigrants, widows):

```turtle
ex:Person_12345
    a               co:Person ;
    schema:name     "Patrick Byrne" ;
    co:surname      "BYRNE" ;
    co:role         co:Role_Emigrant ;
    co:townland     ex:Ballinacor ;
    co:year         "1851"^^xsd:gYear ;
    co:gender       "M" ;
    co:occupation   "Labourer" ;
    co:isWidow      "false"^^xsd:boolean .
```

**`co:EstateSurvey`** — an estate population survey (distinct from official census):

```turtle
ex:Survey_Ballinacor_1827
    a               co:EstateSurvey ;
    co:forTownland  ex:Ballinacor ;
    co:year         "1827"^^xsd:gYear ;
    co:population   "387"^^xsd:integer ;
    co:source       "json" .
```

### What Data is Uplifted

All four tables from the SQLite schema are represented as RDF:

| SQLite Table | RDF Class | Triples per Row (approximate) |
|---|---|---|
| `townland` | `co:Townland` | 10–20 (name, hierarchy, geometry, identifiers) |
| `census_record` | `co:CensusRecord` | 7–9 (townland link, year, 4 count fields, source) |
| `clearances_record` | `co:ClearancesRecord` | 5–6 (townland link, year, count, source) |
| `unified_record` (persons) | `co:Person` | 8–15 (name, role, townland link, year, attributes) |

In addition, the estate GeoJSON data introduces:

- `co:EstateSurvey` records for the 6 estate population years (1827, 1839, 1848, 1850, 1860, 1868).
- `co:TownlandFeature` records for any heritage features (holy wells, ring forts) linked to a townland.

### Why RDF Uplift Matters

Converting the estate records to RDF enables queries that are difficult or verbose in SQL:

**Example — transitivity**: "Which townlands fall within the same barony as Ballinacor?"

In SQL this requires a self-join through the `townland` table on `barony`. In SPARQL:

```sparql
SELECT ?Townland
WHERE {
    ex:Ballinacor  co:barony   ?Barony .
    ?Townland      co:barony   ?Barony .
    FILTER(?Townland != ex:Ballinacor)
}
```

**Example — semantic typing**: "List all emigration events that occurred in townlands with a holy well."

In SQL this requires a JOIN between `unified_record`, `townland`, and a `heritage_feature` table plus a WHERE filter on `feature_group`. In SPARQL the type system can express this directly:

```sparql
SELECT ?Person ?Townland
WHERE {
    ?Person    a co:Person ;
               co:role co:Role_Emigrant ;
               co:townland ?Townland .
    ?Heritage  a co:TownlandFeature ;
               co:featureType co:HolyWell ;
               co:forTownland ?Townland .
}
```

**Example — provenance chains**: SPARQL named graphs allow different data sources (KG uplift vs. estate GeoJSON vs. manual correction) to be tracked at the triple level, supporting citation and reproducibility requirements for academic submission.

---

## Combined Uplift Flow Diagram

```
Estate GeoJSON                    VRTI Knowledge Graph
   (TL_ENGLISH,                   (crm:, vrti:, geo:)
    AREA, coordinates,                    │
    T_POP_*, Clearances_*)                │ SPARQL query
          │                              │
          │                              ▼
          │                   vrti_sparql.get_townlands()
          │                   vrti_sparql.get_census_records_for_county()
          │                              │
          ▼                              ▼
    full_ingest.py ←──── KG enrichment ─────────────────┐
          │                                              │
          │  normalize + alias resolve + reconcile       │
          │                                              │
          ▼                                              │
     SQLite townland                                     │
     SQLite census_record  (source='json' or 'kg')       │
     SQLite clearances_record                            │
          │                                              │
          │                              ┌──────────────┘
          │                              │ townlands.ie reference snapshot
          ▼                              │ (data/seed/wicklow_townlands_reference.json)
   townland_service.reconcile_with_reference()
          │
          ▼
   Enriched SQLite DB
   (geometry, hierarchy, identifiers, media, census)
          │
          ├──────────────────────────────────────────────►  Map display
          │                                                  (centroid + polygon)
          │
          ├──► GraphDB Uplift Job
          │       │
          │       │  SQLite rows → RDF triples
          │       │  (co:Townland, co:CensusRecord, co:Person, ...)
          │       │
          │       ▼
          │    Local GraphDB repository
          │    "coolattin" (SPARQL endpoint port 7200)
          │       │
          │       ▼
          │    GraphRAG path in ask_service.py
          │    (comparative SPARQL alongside SQLite queries)
          │
          └──► Ask service SQL templates
                  (entity resolution + template substitution → SQL)
```

---

## Data Volumes (Typical After Full Ingest)

| Entity | Approximate Count | Source |
|---|---|---|
| Townlands | ~150 | Estate GeoJSON (KG-enriched) |
| Census records (standard years) | ~900 | VRTI KG (1841–1891, 6 years × 150 townlands) |
| Census records (estate surveys) | ~900 | Estate GeoJSON (1827–1868, 6 years × 150 townlands) |
| Clearances records | ~1 500 | Estate GeoJSON (1847–1856, 10 years × 150 townlands) |
| Unified records (persons) | ~10 000+ | Estate tabular records |
| RDF triples (GraphDB) | ~200 000+ | Uplifted from all of the above |

---

## Staleness and Refresh

The `refresh_state` table tracks when each dataset was last successfully ingested:

```sql
CREATE TABLE refresh_state (
    dataset   TEXT PRIMARY KEY,
    last_refresh TEXT,     -- ISO 8601 timestamp
    record_count INTEGER,
    status    TEXT
);
```

Staleness thresholds (configurable via `config.py`):

- `CENSUS_STALE_AFTER_DAYS`: 7 days (production: 1 day)
- `TOWNLAND_STALE_AFTER_DAYS`: 30 days (production: 7 days)

If the VRTI endpoint is unreachable during an ingest run, the pipeline falls back to the bundled CSV seed files in `data/seed/` rather than leaving the database empty. This ensures the application remains usable offline with the last-known-good data.

---

## Relationship to the VRTI KG vs. Local GraphDB

| Dimension | VRTI KG (remote) | Local GraphDB |
|---|---|---|
| Data | All of Ireland — townlands, parishes, census | Coolattin estate only |
| Ontology | CIDOC-CRM + vrti: extension | co: (purpose-built) |
| Access | SPARQL over HTTPS | SPARQL over localhost:7200 |
| Availability | Dependent on VTR infrastructure | Always available locally |
| Update model | Pulled by ingest jobs | Rebuilt from SQLite on demand |
| Query use | KG enrichment during ingest; GraphRAG path 4 | GraphRAG path 5; comparative analysis |
| Scope for dissertation | External authoritative reference | Internal experimental prototype (D8) |

The two graphs are complementary. The VRTI graph provides authoritative, wide-scope reference data. The local GraphDB provides a focused, dissertation-specific graph of the estate that can be queried, modified, and extended independently without affecting the external reference.
