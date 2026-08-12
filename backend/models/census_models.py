from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Townland:
    id: Optional[int] = None
    entity_id: Optional[str] = None
    name: str = ""
    qualifier: Optional[str] = None
    logainm_id: Optional[str] = None
    name_gaelic: Optional[str] = None
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    electoral_division: Optional[str] = None
    placename_theme: Optional[str] = None
    description: Optional[str] = None
    td_id: Optional[str] = None
    guid: Optional[str] = None
    area_sqm: Optional[float] = None
    kg_uri: Optional[str] = None
    wkt_geometry: Optional[str] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    county: Optional[str] = None
    osm_id: Optional[str] = None
    osi_id: Optional[str] = None
    vrti_id: Optional[str] = None
    images: list = field(default_factory=list)
    links: list = field(default_factory=list)
    geometry_flag: Optional[str] = None
    source: str = "json"
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
    id: Optional[int] = None
    townland_id: Optional[int] = None
    townland_name: str = ""
    year: int = 0
    male: Optional[int] = None
    female: Optional[int] = None
    total: Optional[int] = None
    inhabited: Optional[int] = None
    uninhabited: Optional[int] = None
    source: str = "json"
    kg_uri: Optional[str] = None
    last_synced_at: Optional[str] = None

    def __post_init__(self) -> None:
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
    id: Optional[int] = None
    townland_id: Optional[int] = None
    townland_name: str = ""
    year: int = 0
    count: Optional[int] = None
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
    dataset_key: str = ""
    last_synced_at: Optional[str] = None
    source: Optional[str] = None
    query_hash: Optional[str] = None
    record_count: int = 0
    export_file: Optional[str] = None
    is_stale: bool = False


@dataclass
class CensusFilters:
    year: Optional[int] = None
    townland: Optional[str] = None
    barony: Optional[str] = None
    page: int = 1
    limit: int = 100

    def dataset_key(self) -> str:
        parts = ["wicklow_census"]
        if self.year:
            parts.append(str(self.year))
        if self.townland:
            parts.append(self.townland.upper())
        return "_".join(parts)


@dataclass
class CensusMeta:
    source: str = "database"
    cache_status: str = "hit"
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
    data: list = field(default_factory=list)
    meta: CensusMeta = field(default_factory=CensusMeta)
