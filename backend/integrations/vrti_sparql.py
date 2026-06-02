"""
coolattin/integrations/vrti_sparql.py

VRTI Knowledge Graph SPARQL client.

======================================================
THIS IS THE ONLY MODULE THAT CONSTRUCTS SPARQL QUERIES
======================================================

All KG communication is centralised here so that:
  - Prefixes are defined once and never scattered across the codebase.
  - Routes and services never see SPARQL syntax.
  - The endpoint URL lives in one place.
  - Query timeouts are applied uniformly.
  - Response parsing is normalised into typed DTOs.

Endpoint: https://virtuoso.virtualtreasury.ie/sparql/

How to extend:
  Add a new typed method below.  Never add inline SPARQL anywhere else.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Endpoint                                                             #
# ------------------------------------------------------------------ #
SPARQL_ENDPOINT = "https://virtuoso.virtualtreasury.ie/sparql/"
REQUEST_TIMEOUT = 30  # seconds

# ------------------------------------------------------------------ #
# Centralised PREFIX block                                             #
# All SPARQL queries produced by this module begin with this block.   #
# ------------------------------------------------------------------ #
PREFIXES = """
PREFIX crm:  <http://erlangen-crm.org/current/>
PREFIX vrti: <https://ont.virtualtreasury.ie/ontology#>
PREFIX geo:  <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""

# KG named graphs
PRESENT_DAY_PLACES_GRAPH = "https://kg.virtualtreasury.ie/graph/present-day-places-v1"


# ------------------------------------------------------------------ #
# DTOs (Data Transfer Objects)                                         #
# Plain dataclasses — carry parsed KG data into the service layer.    #
# ------------------------------------------------------------------ #

@dataclass
class TownlandDTO:
    """A townland record as returned from the VRTI KG."""
    uri: str
    name: str
    name_gaelic: Optional[str] = None
    wkt_geometry: Optional[str] = None   # WKT POLYGON boundary
    centroid_wkt: Optional[str] = None   # WKT POINT centroid
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    county: Optional[str] = None
    osm_id: Optional[str] = None
    osi_id: Optional[str] = None
    vrti_id: Optional[str] = None
    images: list = field(default_factory=list)    # image URLs from KG
    links: list = field(default_factory=list)     # external links (logainm, townlands.ie)
    centroid_flag: Optional[str] = None           # set when coordinate swap-check fails

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "name": self.name,
            "name_gaelic": self.name_gaelic,
            "boundary_wkt": self.wkt_geometry,
            "centroid_lat": self.centroid_lat,
            "centroid_lon": self.centroid_lon,
            "barony": self.barony,
            "civil_parish": self.civil_parish,
            "county": self.county,
            "osm_id": self.osm_id,
            "osi_id": self.osi_id,
            "vrti_id": self.vrti_id,
            "images": self.images,
            "links": self.links,
        }


@dataclass
class CensusRecordDTO:
    """A census record as returned from the VRTI KG."""
    townland_uri: str
    townland_name: str
    year: int
    male: Optional[int] = None
    female: Optional[int] = None
    inhabited: Optional[int] = None
    uninhabited: Optional[int] = None


# ------------------------------------------------------------------ #
# Internal helpers                                                     #
# ------------------------------------------------------------------ #

def _parse_point_wkt(
    wkt: Optional[str],
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Parse VRTI centroid WKT → (lat, lon, flag).

    The VRTI KG stores centroids as POINT(lat lon) — empirically confirmed
    as lat-first, which is non-standard GeoSPARQL (lon-first) order.

    Returns (lat, lon, None) on success.
    Returns (None, None, flag_reason) when both coordinate orderings fail
    the Ireland sanity check; the caller should write the flag to geometry_flag
    rather than silently discarding the centroid.
    """
    if not wkt:
        return None, None, None
    try:
        inner = wkt.strip().upper().replace("POINT(", "").replace(")", "")
        parts = inner.split()
        if len(parts) == 2:
            a, b = float(parts[0]), float(parts[1])
            # Ireland: 51–55.5°N, 5–11°W
            if 51.0 <= a <= 55.5 and -11.0 <= b <= -5.0:
                return a, b, None
            if 51.0 <= b <= 55.5 and -11.0 <= a <= -5.0:
                return b, a, None
            flag = f"coordinate_out_of_range:({a:.4f},{b:.4f})"
            log.warning("vrti_sparql._parse_point_wkt | %s wkt=%s", flag, wkt)
            return None, None, flag
    except (ValueError, AttributeError) as exc:
        return None, None, f"wkt_parse_error:{exc}"
    return None, None, "unknown_point_format"


def _execute(query: str) -> list[dict]:
    """
    Execute a SPARQL SELECT query against the VRTI endpoint.
    Returns the raw bindings list or raises on HTTP/network error.

    All queries are prefixed with the centralised PREFIX block.
    """
    full_query = PREFIXES + "\n" + query
    log.debug("vrti_sparql.execute | preview: %s", full_query[:300].replace("\n", " "))
    try:
        resp = requests.get(
            SPARQL_ENDPOINT,
            params={
                "query": full_query,
                "format": "application/sparql-results+json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        bindings = resp.json().get("results", {}).get("bindings", [])
        log.debug("vrti_sparql.results | count=%d", len(bindings))
        return bindings
    except requests.exceptions.Timeout:
        log.error("vrti_sparql.timeout | endpoint=%s", SPARQL_ENDPOINT)
        raise
    except requests.exceptions.RequestException as exc:
        log.error("vrti_sparql.request_failed | error=%s", str(exc))
        raise


def _val(binding: dict, key: str) -> Optional[str]:
    """Safely extract a string value from a SPARQL binding dict."""
    return binding.get(key, {}).get("value") or None


def _int_val(binding: dict, key: str) -> Optional[int]:
    """Safely extract an integer from a SPARQL binding."""
    raw = _val(binding, key)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ #
# Public API — called only by services, never by routes               #
# ------------------------------------------------------------------ #

def get_townlands(
    county: Optional[str] = None,
    limit: int = 2000,
) -> list[TownlandDTO]:
    """
    Fetch townlands from the VRTI present-day-places graph,
    including parish, barony, and county via the place hierarchy.

    Parameters
    ----------
    county : str or None
        If provided, restrict results to that county (e.g. "Wicklow").
        If None, all townlands available in the KG are returned.
    limit : int
        Maximum number of SPARQL result rows (before deduplication).

    Returns an empty list if the KG is unreachable.

    Called by: townland_service (on cache miss or forced refresh)
               jobs/townlands_ingest.py (batch ingestion)
    NOT called by: routes, templates, or frontend
    """
    # Build county filter only when a county is requested
    county_filter = f'FILTER(STR(?County) = "{county}")' if county else ""

    query = f"""
    SELECT DISTINCT ?Place ?Name ?NameGaelic ?Parish ?Barony ?County ?WKT
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        ?Place crm:P2_has_type vrti:PresentDayTownland ;
               rdfs:label ?Name .
        FILTER(langMatches(lang(?Name), "en"))

        OPTIONAL {{
          ?Place rdfs:label ?NameGaelic .
          FILTER(langMatches(lang(?NameGaelic), "ga"))
        }}
        OPTIONAL {{
          ?Place geo:hasGeometry ?Geom .
          ?Geom geo:asWKT ?WKT .
        }}
        OPTIONAL {{
          ?Place crm:P89_falls_within ?ParishPlace .
          ?ParishPlace crm:P2_has_type vrti:PresentDayParish ;
                       rdfs:label ?Parish .
          FILTER(langMatches(lang(?Parish), "en"))
          OPTIONAL {{
            ?ParishPlace crm:P89_falls_within ?BaronPlace .
            ?BaronPlace crm:P2_has_type vrti:PresentDayBarony ;
                        rdfs:label ?Barony .
            FILTER(langMatches(lang(?Barony), "en"))
            OPTIONAL {{
              ?BaronPlace crm:P89_falls_within ?CountyPlace .
              ?CountyPlace rdfs:label ?County .
              FILTER(langMatches(lang(?County), "en"))
            }}
          }}
        }}
      }}
      {county_filter}
    }}
    ORDER BY ?Name
    LIMIT {limit}
    """
    try:
        bindings = _execute(query)
    except Exception:
        log.warning("vrti_sparql.get_townlands county=%s failed — returning empty list", county)
        return []

    # Deduplicate by URI — multiple rows when a townland has multiple parishes/baronies
    seen: dict[str, TownlandDTO] = {}
    for b in bindings:
        uri = _val(b, "Place")
        name = _val(b, "Name")
        if not uri or not name:
            continue
        if uri not in seen:
            seen[uri] = TownlandDTO(
                uri=uri,
                name=name,
                name_gaelic=_val(b, "NameGaelic"),
                wkt_geometry=_val(b, "WKT"),
                barony=_val(b, "Barony"),
                civil_parish=_val(b, "Parish"),
                county=_val(b, "County"),
            )
        else:
            # Fill in missing fields from duplicate rows
            dto = seen[uri]
            if not dto.name_gaelic:
                dto.name_gaelic = _val(b, "NameGaelic")
            if not dto.barony:
                dto.barony = _val(b, "Barony")
            if not dto.civil_parish:
                dto.civil_parish = _val(b, "Parish")
            if not dto.county:
                dto.county = _val(b, "County")

    results = list(seen.values())
    log.info("vrti_sparql.get_townlands | county=%s returned=%d", county, len(results))
    return results


def get_wicklow_townlands(limit: int = 2000) -> list[TownlandDTO]:
    """Backward-compatible wrapper — fetches Wicklow townlands only."""
    return get_townlands(county="Wicklow", limit=limit)


def get_townland_details_by_name(
    name: str,
    county: Optional[str] = None,
) -> Optional[TownlandDTO]:
    """
    Fetch complete KG detail for a single townland by English name.

    Returns the best match with all available fields:
      - Gaelic name, parish, barony, county
      - Centroid lat/lon and boundary WKT polygon
      - Historic images and external links
      - OSM, OSI, VRTI identifiers

    If `county` is provided, prefers the URI that belongs to that county.
    If `county` is None, returns the first matching URI regardless of county.

    Returns None if not found or KG is unreachable.
    Called by: census_service (townland detail enrichment)
    """
    # Use case-insensitive matching so ALL-CAPS GeoJSON names (e.g. "BALLARD")
    # and mixed-case names with connectors (e.g. "COOLBAWN or COOLBALLINTAGGART")
    # both resolve correctly regardless of how the KG stores the label.
    #
    # Also handle reversed "X or Y" ↔ "Y or X" variants — the KG sometimes stores
    # the alternate-name form in opposite order from the GeoJSON label.
    # e.g. GeoJSON "COOLBAWN or COOLBALLINTAGGART" ↔ KG "Coolballintaggart or Coolbawn"
    name_lower = name.strip().lower().replace('"', '\\"')
    or_parts = [p.strip() for p in name_lower.split(" or ") if p.strip()]
    if len(or_parts) == 2:
        reversed_lower = f"{or_parts[1]} or {or_parts[0]}"
        name_filter = (
            f'FILTER(LCASE(STR(?Name)) = "{name_lower}" || '
            f'LCASE(STR(?Name)) = "{reversed_lower}")'
        )
    else:
        name_filter = f'FILTER(LCASE(STR(?Name)) = "{name_lower}")'

    query = f"""
    SELECT DISTINCT ?Place ?Name ?NameGaelic ?Parish ?Barony ?County
                    ?CentroidWKT ?BoundaryWKT ?Image ?Link
                    ?OsmId ?OsiId ?VrtiId
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        ?Place crm:P2_has_type vrti:PresentDayTownland ;
               rdfs:label ?Name .
        FILTER(langMatches(lang(?Name), "en"))
        {name_filter}

        OPTIONAL {{
          ?Place rdfs:label ?NameGaelic .
          FILTER(langMatches(lang(?NameGaelic), "ga"))
        }}
        OPTIONAL {{
          ?Place geo:hasCentroid ?Centroid .
          ?Centroid geo:asWKT ?CentroidWKT .
        }}
        OPTIONAL {{
          ?Place geo:hasBoundingBox ?Boundary .
          ?Boundary geo:asWKT ?BoundaryWKT .
        }}
        OPTIONAL {{ ?Place crm:P67i_is_referred_to_by ?Image . }}
        OPTIONAL {{ ?Place crm:P71i_is_listed_in ?Link . }}
        OPTIONAL {{ ?Place vrti:OsmIdentifier ?OsmId . }}
        OPTIONAL {{ ?Place vrti:OsiIdentifier ?OsiId . }}
        OPTIONAL {{ ?Place vrti:VrtiIdentifier ?VrtiId . }}
        OPTIONAL {{
          ?Place crm:P89_falls_within ?ParishPlace .
          ?ParishPlace crm:P2_has_type vrti:PresentDayParish ;
                       rdfs:label ?Parish .
          FILTER(langMatches(lang(?Parish), "en"))
          OPTIONAL {{
            ?ParishPlace crm:P89_falls_within ?BaronPlace .
            ?BaronPlace crm:P2_has_type vrti:PresentDayBarony ;
                        rdfs:label ?Barony .
            FILTER(langMatches(lang(?Barony), "en"))
            OPTIONAL {{
              ?BaronPlace crm:P89_falls_within ?CountyPlace .
              ?CountyPlace rdfs:label ?County .
              FILTER(langMatches(lang(?County), "en"))
            }}
          }}
        }}
      }}
    }}
    LIMIT 200
    """
    try:
        bindings = _execute(query)
    except Exception:
        log.warning("vrti_sparql.get_townland_details_by_name failed name=%s", name)
        return None

    if not bindings:
        return None

    # Group all rows by URI — collect images/links as sets
    by_uri: dict[str, dict] = {}
    for b in bindings:
        uri = _val(b, "Place")
        if not uri:
            continue
        if uri not in by_uri:
            by_uri[uri] = {
                "uri": uri,
                "name_gaelic": None,
                "barony": None,
                "civil_parish": None,
                "counties": set(),   # collect ALL counties for this URI
                "centroid_wkt": None,
                "boundary_wkt": None,
                "osm_id": None,
                "osi_id": None,
                "vrti_id": None,
                "images": set(),
                "links": set(),
            }
        d = by_uri[uri]
        if not d["name_gaelic"]:   d["name_gaelic"]   = _val(b, "NameGaelic")
        if not d["barony"]:        d["barony"]         = _val(b, "Barony")
        if not d["civil_parish"]:  d["civil_parish"]   = _val(b, "Parish")
        c = _val(b, "County")
        if c:                      d["counties"].add(c)
        if not d["centroid_wkt"]:  d["centroid_wkt"]   = _val(b, "CentroidWKT")
        if not d["boundary_wkt"]:  d["boundary_wkt"]   = _val(b, "BoundaryWKT")
        if not d["osm_id"]:        d["osm_id"]         = _val(b, "OsmId")
        if not d["osi_id"]:        d["osi_id"]         = _val(b, "OsiId")
        if not d["vrti_id"]:       d["vrti_id"]        = _val(b, "VrtiId")
        img = _val(b, "Image")
        if img:  d["images"].add(img)
        lnk = _val(b, "Link")
        if lnk:  d["links"].add(lnk)

    # If a county was requested, prefer the URI that belongs to it.
    # If no county requested (None), just take the first URI.
    chosen = None
    if county:
        county_lower = county.lower()
        for d in by_uri.values():
            if any(c.lower() == county_lower for c in d["counties"]):
                chosen = d
                break
    if chosen is None:
        chosen = next(iter(by_uri.values()))
    # Resolve the display county
    if county and any(c.lower() == county.lower() for c in chosen["counties"]):
        chosen_county = county
    else:
        chosen_county = next(iter(chosen["counties"]), None)

    lat, lon, centroid_flag = _parse_point_wkt(chosen["centroid_wkt"])

    dto = TownlandDTO(
        uri=chosen["uri"],
        name=name,
        name_gaelic=chosen["name_gaelic"],
        wkt_geometry=chosen["boundary_wkt"],
        centroid_wkt=chosen["centroid_wkt"],
        centroid_lat=lat,
        centroid_lon=lon,
        barony=chosen["barony"],
        civil_parish=chosen["civil_parish"],
        county=chosen_county,
        osm_id=chosen["osm_id"],
        osi_id=chosen["osi_id"],
        vrti_id=chosen["vrti_id"],
        images=sorted(chosen["images"]),
        links=sorted(chosen["links"]),
        centroid_flag=centroid_flag,
    )
    log.info(
        "vrti_sparql.get_townland_details_by_name | name=%s county=%s centroid=(%s,%s) images=%d",
        name, dto.county, lat, lon, len(dto.images),
    )
    return dto


def get_census_records_for_townland(
    townland_uri: str,
    years: list[int] | None = None,
) -> list[CensusRecordDTO]:
    """
    Fetch census records for a specific townland URI.
    Optionally filtered to specific census years.

    NOTE: Census property names (vrti:censusYear etc.) are inferred from the
    VRTI ontology pattern.  If the KG uses different predicates, update the
    query here.  The service layer will fall back to CSV seed on empty result.

    Called by: census_service (per-townland cache miss)
    """
    year_filter = ""
    if years:
        year_vals = ", ".join(str(y) for y in years)
        year_filter = f"FILTER(?Year IN ({year_vals}))"

    query = f"""
    SELECT ?TownlandName ?Year ?Male ?Female ?Inhabited ?Uninhabited
    WHERE {{
      BIND(<{townland_uri}> AS ?Townland)
      ?Townland rdfs:label ?TownlandName .
      FILTER(langMatches(lang(?TownlandName), "en"))

      ?CensusRecord vrti:relatesTo ?Townland ;
                    vrti:censusYear ?Year ;
                    vrti:malePopulation ?Male ;
                    vrti:femalePopulation ?Female .
      OPTIONAL {{ ?CensusRecord vrti:inhabitedHouses ?Inhabited . }}
      OPTIONAL {{ ?CensusRecord vrti:uninhabitedHouses ?Uninhabited . }}
      {year_filter}
    }}
    ORDER BY ?Year
    """
    try:
        bindings = _execute(query)
    except Exception:
        log.warning(
            "vrti_sparql.get_census_records_for_townland failed uri=%s", townland_uri
        )
        return []

    return [
        CensusRecordDTO(
            townland_uri=townland_uri,
            townland_name=_val(b, "TownlandName") or "",
            year=_int_val(b, "Year") or 0,
            male=_int_val(b, "Male"),
            female=_int_val(b, "Female"),
            inhabited=_int_val(b, "Inhabited"),
            uninhabited=_int_val(b, "Uninhabited"),
        )
        for b in bindings
        if _val(b, "Year")
    ]


def get_census_records_for_county(
    county: Optional[str] = None,
    year: int | None = None,
) -> list[CensusRecordDTO]:
    """
    Fetch all census records for all townlands, optionally filtered by county and/or year.

    Parameters
    ----------
    county : str or None
        If provided, restrict to townlands in that county via the place hierarchy
        (Townland → Parish → Barony → County).  If None, all available census
        records in the KG are returned.
    year : int or None
        If provided, restrict to a single census year.

    Called by: census_service (on full census cache miss)
               jobs/census_ingest.py (batch ingestion)
    """
    year_filter = f"FILTER(?Year = {year})" if year else ""

    # County filter: join through the place hierarchy only when needed
    if county:
        county_join = f"""
        GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
          ?Townland crm:P89_falls_within ?ParishPlace .
          ?ParishPlace crm:P2_has_type vrti:PresentDayParish ;
                       crm:P89_falls_within ?BaronPlace .
          ?BaronPlace crm:P2_has_type vrti:PresentDayBarony ;
                      crm:P89_falls_within ?CountyPlace .
          ?CountyPlace rdfs:label ?CountyLabel .
          FILTER(langMatches(lang(?CountyLabel), "en"))
          FILTER(STR(?CountyLabel) = "{county}")
        }}"""
    else:
        county_join = ""

    query = f"""
    SELECT ?Townland ?TownlandName ?Year ?Male ?Female ?Inhabited ?Uninhabited
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        ?Townland rdfs:label ?TownlandName ;
                  crm:P2_has_type vrti:PresentDayTownland .
        FILTER(langMatches(lang(?TownlandName), "en"))
      }}
      {county_join}

      ?CensusRecord vrti:relatesTo ?Townland ;
                    vrti:censusYear ?Year ;
                    vrti:malePopulation ?Male ;
                    vrti:femalePopulation ?Female .
      OPTIONAL {{ ?CensusRecord vrti:inhabitedHouses ?Inhabited . }}
      OPTIONAL {{ ?CensusRecord vrti:uninhabitedHouses ?Uninhabited . }}
      {year_filter}
    }}
    ORDER BY ?TownlandName ?Year
    LIMIT 10000
    """
    try:
        bindings = _execute(query)
    except Exception:
        log.warning("vrti_sparql.get_census_records_for_county failed county=%s", county)
        return []

    results = []
    for b in bindings:
        if not _val(b, "Year"):
            continue
        results.append(CensusRecordDTO(
            townland_uri=_val(b, "Townland") or "",
            townland_name=_val(b, "TownlandName") or "",
            year=_int_val(b, "Year") or 0,
            male=_int_val(b, "Male"),
            female=_int_val(b, "Female"),
            inhabited=_int_val(b, "Inhabited"),
            uninhabited=_int_val(b, "Uninhabited"),
        ))

    log.info("vrti_sparql.get_census_records_for_county | county=%s year=%s returned=%d",
             county, year, len(results))
    return results


def get_parish_names(
    county: Optional[str] = None,
    limit: int = 200,
) -> list[str]:
    """
    Fetch a list of civil parish names (lightweight — no geometry or centroids).

    Significantly faster than get_townlands() because it skips geometry triples
    and returns only the parish label.  Use this when you only need parish names
    for context or counting, not full townland records.

    Parameters
    ----------
    county : str or None
        If provided, restrict to parishes whose parent barony belongs to that county.
    limit : int
        Maximum number of parish names to return.

    Called by: ask_service (VRTI parish context, cached)
    """
    county_filter = f'FILTER(STR(?County) = "{county}")' if county else ""
    query = f"""
    SELECT DISTINCT ?Parish
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        ?ParishPlace crm:P2_has_type vrti:PresentDayParish ;
                     rdfs:label ?Parish .
        FILTER(langMatches(lang(?Parish), "en"))
        OPTIONAL {{
          ?ParishPlace crm:P89_falls_within ?BaronPlace .
          ?BaronPlace crm:P2_has_type vrti:PresentDayBarony ;
                      crm:P89_falls_within ?CountyPlace .
          ?CountyPlace rdfs:label ?County .
          FILTER(langMatches(lang(?County), "en"))
        }}
      }}
      {county_filter}
    }}
    ORDER BY ?Parish
    LIMIT {limit}
    """
    try:
        bindings = _execute(query)
        names = [_val(b, "Parish") for b in bindings if _val(b, "Parish")]
        log.info("vrti_sparql.get_parish_names | county=%s returned=%d", county, len(names))
        return names
    except Exception as exc:
        log.warning("vrti_sparql.get_parish_names county=%s failed: %s", county, exc)
        return []


def get_place_hierarchy(entity_uri: str) -> dict:
    """
    Fetch the administrative hierarchy for a single townland URI.

    Returns {townland_name, parish, barony, county} — any value may be None
    if the KG does not contain that level for this entity.

    Used by: subgraph_engine (Phase 3 hierarchy traversal)
    """
    query = f"""
    SELECT ?townlandName ?parish ?barony ?county
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        OPTIONAL {{
          <{entity_uri}> rdfs:label ?townlandName .
          FILTER(langMatches(lang(?townlandName), "en"))
        }}
        OPTIONAL {{
          <{entity_uri}> crm:P89_falls_within ?parishPlace .
          ?parishPlace crm:P2_has_type vrti:PresentDayParish ;
                       rdfs:label ?parish .
          FILTER(langMatches(lang(?parish), "en"))
          OPTIONAL {{
            ?parishPlace crm:P89_falls_within ?baronPlace .
            ?baronPlace crm:P2_has_type vrti:PresentDayBarony ;
                        rdfs:label ?barony .
            FILTER(langMatches(lang(?barony), "en"))
            OPTIONAL {{
              ?baronPlace crm:P89_falls_within ?countyPlace .
              ?countyPlace rdfs:label ?county .
              FILTER(langMatches(lang(?county), "en"))
            }}
          }}
        }}
      }}
    }}
    LIMIT 5
    """
    try:
        bindings = _execute(query)
        if not bindings:
            return {}
        b = bindings[0]
        return {
            "townland_name": _val(b, "townlandName"),
            "parish":        _val(b, "parish"),
            "barony":        _val(b, "barony"),
            "county":        _val(b, "county"),
        }
    except Exception:
        log.warning("vrti_sparql.get_place_hierarchy | uri=%s failed", entity_uri)
        return {}


def get_sibling_townlands(entity_uri: str, limit: int = 20) -> list[dict]:
    """
    Fetch townlands that share the same civil parish as entity_uri.
    This is a 2-hop traversal: townland → parish ← sibling.

    Returns [{name, uri}] sorted alphabetically.

    Used by: subgraph_engine (Phase 3 — same-parish neighbourhood)
    """
    query = f"""
    SELECT DISTINCT ?sibling ?siblingName
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        <{entity_uri}> crm:P89_falls_within ?parish .
        ?sibling crm:P89_falls_within ?parish ;
                 crm:P2_has_type vrti:PresentDayTownland ;
                 rdfs:label ?siblingName .
        FILTER(langMatches(lang(?siblingName), "en"))
        FILTER(?sibling != <{entity_uri}>)
      }}
    }}
    ORDER BY ?siblingName
    LIMIT {limit}
    """
    try:
        bindings = _execute(query)
        return [
            {"name": _val(b, "siblingName"), "uri": _val(b, "sibling")}
            for b in bindings
            if _val(b, "siblingName")
        ]
    except Exception:
        log.warning("vrti_sparql.get_sibling_townlands | uri=%s failed", entity_uri)
        return []


def get_external_links(entity_uri: str) -> dict:
    """
    Fetch external identifiers and reference links for an entity URI.

    Returns {osm_id, osi_id, vrti_id, links: list[str]}.
    All values default to None / [] if not found.

    Used by: subgraph_engine (Phase 3 — external provenance)
    """
    query = f"""
    SELECT ?pred ?obj
    WHERE {{
      GRAPH <{PRESENT_DAY_PLACES_GRAPH}> {{
        <{entity_uri}> ?pred ?obj .
        FILTER(?pred IN (
          vrti:OsmIdentifier,
          vrti:OsiIdentifier,
          vrti:VrtiIdentifier,
          crm:P67i_is_referred_to_by,
          crm:P71i_is_listed_in
        ))
        FILTER(!isBlank(?obj))
      }}
    }}
    LIMIT 20
    """
    result: dict = {"osm_id": None, "osi_id": None, "vrti_id": None, "links": []}
    try:
        bindings = _execute(query)
        for b in bindings:
            pred = _val(b, "pred") or ""
            obj  = _val(b, "obj") or ""
            if not obj:
                continue
            if "OsmIdentifier" in pred:
                result["osm_id"] = obj
            elif "OsiIdentifier" in pred:
                result["osi_id"] = obj
            elif "VrtiIdentifier" in pred:
                result["vrti_id"] = obj
            elif obj not in result["links"]:
                result["links"].append(obj)
    except Exception:
        log.warning("vrti_sparql.get_external_links | uri=%s failed", entity_uri)
    return result


def probe_endpoint() -> bool:
    """
    Simple connectivity check.  Returns True if the SPARQL endpoint
    responds to a minimal query, False otherwise.

    Used by health check endpoints and ingest jobs to fail fast.
    """
    try:
        bindings = _execute("SELECT (1 AS ?ping) WHERE {}")
        return len(bindings) > 0
    except Exception:
        return False
