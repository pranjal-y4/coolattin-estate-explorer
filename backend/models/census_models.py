"""
coolattin/models/census_models.py

Pure Python dataclasses for census domain objects.
These are the in-memory representations used across the service and
repository layers.  They have no database or HTTP concerns.

Separation rule:
  - Models: data shape only
  - Repositories: read/write models to DB
  - Services: business logic, decides when to call repositories vs KG
  - Schemas (see below): serialise models for HTTP responses
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Townland:
    """
    A single townland — fully enriched from the estate GeoJSON and the VRTI KG.

    Name source   : estate GeoJSON (TL_ENGLISH)
    Measurements  : estate GeoJSON (AREA → area_sqm)
    Identifiers   : estate GeoJSON (TD_ID, GUID) + KG (osm_id, osi_id, vrti_id, kg_uri)
    Geography     : KG (wkt_geometry, centroid_lat/lon)
    Hierarchy     : KG (barony, civil_parish, county)
    Media / links : KG (images_json, links_json)
    """
    id: Optional[int] = None
    entity_id: Optional[str] = None     # UUID surrogate key (assigned at insert)
    name: str = ""                       # canonical UPPER-CASE English name
    qualifier: Optional[str] = None     # locational qualifier: UPPER/LOWER/etc.
    logainm_id: Optional[str] = None    # logainm.ie place identifier
    name_gaelic: Optional[str] = None   # Irish/Gaelic name (from GeoJSON TL_GAEILGE or KG)
    barony: Optional[str] = None        # from KG place hierarchy
    civil_parish: Optional[str] = None  # from KG place hierarchy
    electoral_division: Optional[str] = None
    placename_theme: Optional[str] = None
    description: Optional[str] = None
    # Estate GeoJSON identifiers
    td_id: Optional[str] = None         # TD_ID from GeoJSON
    guid: Optional[str] = None          # GUID from GeoJSON
    # Measurements
    area_sqm: Optional[float] = None    # area in square metres (GeoJSON AREA)
    # KG identifiers & geography
    kg_uri: Optional[str] = None        # VRTI KG subject URI
    wkt_geometry: Optional[str] = None  # boundary WKT polygon from KG
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    county: Optional[str] = None
    osm_id: Optional[str] = None
    osi_id: Optional[str] = None
    vrti_id: Optional[str] = None
    images: list = field(default_factory=list)   # image URLs from KG
    links: list = field(default_factory=list)    # external links from KG
    geometry_flag: Optional[str] = None  # geometry/centroid quality flags
    source: str = "json"                 # 'json' | 'kg' | 'manual'
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "name": self.name,
            "qualifier": self.qualifier,
            "logainm_id": self.logainm_id,
            "name_gaelic": self.name_gaelic,
            "barony": self.barony,
            "civil_parish": self.civil_parish,
            "electoral_division": self.electoral_division,
            "placename_theme": self.placename_theme,
            "description": self.description,
            "td_id": self.td_id,
            "guid": self.guid,
            "area_sqm": self.area_sqm,
            "kg_uri": self.kg_uri,
            "centroid_lat": self.centroid_lat,
            "centroid_lon": self.centroid_lon,
            "county": self.county,
            "osm_id": self.osm_id,
            "osi_id": self.osi_id,
            "vrti_id": self.vrti_id,
            "images": self.images,
            "links": self.links,
            "geometry_flag": self.geometry_flag,
            "source": self.source,
        }


@dataclass
class CensusRecord:
    """
    One row of population data for a specific townland in a specific year.

    Covers two source types:
      - Standard census years (1841, 1851, 1861, 1871, 1881, 1891): from the VRTI KG.
        male, female, inhabited, uninhabited are all populated.
      - Estate survey years (1827, 1839, 1848, 1850, 1860, 1868): from the estate GeoJSON.
        Only total is populated (male/female not recorded in this source).
    """
    id: Optional[int] = None
    townland_id: Optional[int] = None
    townland_name: str = ""              # denormalised for convenience
    year: int = 0
    male: Optional[int] = None          # null for estate survey records
    female: Optional[int] = None        # null for estate survey records
    total: Optional[int] = None         # always populated
    inhabited: Optional[int] = None
    uninhabited: Optional[int] = None
    source: str = "json"                 # 'json' | 'kg' | 'manual'
    kg_uri: Optional[str] = None        # KG URI of the census-record entity (kg source only)
    last_synced_at: Optional[str] = None

    def __post_init__(self) -> None:
        # Always recompute total from components if not explicitly set
        if self.total is None and (self.male is not None or self.female is not None):
            self.total = (self.male or 0) + (self.female or 0)

    def to_dict(self) -> dict:
        return {
            "townland": self.townland_name,
            "year": self.year,
            "male": self.male,
            "female": self.female,
            "total": self.total,
            "inhabited": self.inhabited,
            "uninhabited": self.uninhabited,
            "source": self.source,
            "last_synced_at": self.last_synced_at,
        }


@dataclass
class ClearancesRecord:
    """
    Estate eviction (clearances) count for a specific townland in a specific year.

    Source: estate GeoJSON (Clearances_1847 … Clearances_1856).
    Years covered: 1847–1856.
    """
    id: Optional[int] = None
    townland_id: Optional[int] = None
    townland_name: str = ""              # denormalised for convenience
    year: int = 0
    count: Optional[int] = None         # number of clearances that year
    source: str = "json"

    def to_dict(self) -> dict:
        return {
            "townland": self.townland_name,
            "year": self.year,
            "count": self.count,
            "source": self.source,
        }


@dataclass
class RefreshState:
    """
    Tracks the last time a named dataset was fetched from the KG.
    Used by the service layer to decide DB-first vs KG-second.
    """
    dataset_key: str = ""
    last_synced_at: Optional[str] = None
    source: Optional[str] = None        # 'kg_refresh' | 'csv_seed'
    query_hash: Optional[str] = None
    record_count: int = 0
    export_file: Optional[str] = None   # path to latest Excel export
    is_stale: bool = False              # computed by repository


@dataclass
class CensusFilters:
    """Query parameters for census data requests."""
    year: Optional[int] = None
    townland: Optional[str] = None      # will be normalised by service
    barony: Optional[str] = None
    page: int = 1
    limit: int = 100

    def dataset_key(self) -> str:
        """Unique key for this filter scope — used as refresh_state key."""
        parts = ["wicklow_census"]
        if self.year:
            parts.append(str(self.year))
        if self.townland:
            parts.append(self.townland.upper())
        return "_".join(parts)


@dataclass
class CensusMeta:
    """Metadata envelope attached to every census API response."""
    source: str = "database"            # 'database' | 'kg_refresh' | 'csv_seed'
    cache_status: str = "hit"           # 'hit' | 'miss' | 'stale_refresh'
    generated_at: str = ""
    record_count: int = 0
    export_file: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "cache_status": self.cache_status,
            "generated_at": self.generated_at,
            "record_count": self.record_count,
            "export_file": self.export_file,
        }


@dataclass
class CensusResponse:
    """Full census service response: data + metadata."""
    data: list = field(default_factory=list)
    meta: CensusMeta = field(default_factory=CensusMeta)
