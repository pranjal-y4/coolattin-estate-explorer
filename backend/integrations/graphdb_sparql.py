from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

from backend.config import ActiveConfig

log = logging.getLogger(__name__)

_PROBE_CACHE_TTL_SUCCESS_S = 30.0
_PROBE_CACHE_TTL_FAILURE_S = 300.0
_probe_cache: dict[str, float | bool | None] = {
    "checked_at": 0.0,
    "status": None,
}

PREFIXES = """
PREFIX co:     <https://coolattin.ie/ontology#>
PREFIX ex:     <https://coolattin.ie/resource/>
PREFIX schema: <https://schema.org/>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
"""


def _execute(sparql: str) -> tuple[list[str], list[dict]]:
    endpoint = ActiveConfig.GRAPHDB_SPARQL_ENDPOINT
    timeout = ActiveConfig.GRAPHDB_REQUEST_TIMEOUT
    full_query = PREFIXES + "\n" + sparql
    log.debug("graphdb_sparql.execute | preview=%s", full_query[:200].replace("\n", " "))

    resp = requests.post(
        endpoint,
        data={"query": full_query},
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    vars_ = data.get("head", {}).get("vars", [])
    bindings = data.get("results", {}).get("bindings", [])
    return vars_, bindings


def _val(binding: dict, key: str) -> Optional[str]:
    val = binding.get(key, {}).get("value")
    return val if val is not None else None


def _int_val(binding: dict, key: str) -> Optional[int]:
    raw = _val(binding, key)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def query(sparql: str) -> tuple[list[str], list[dict]]:
    if not ActiveConfig.GRAPHDB_ENABLED:
        log.debug("graphdb_sparql.disabled")
        return [], []

    try:
        vars_, bindings = _execute(sparql)
    except requests.exceptions.ConnectionError:
        log.warning("graphdb_sparql.connection_refused | endpoint=%s", ActiveConfig.GRAPHDB_SPARQL_ENDPOINT)
        return [], []
    except requests.exceptions.Timeout:
        log.warning("graphdb_sparql.timeout | endpoint=%s", ActiveConfig.GRAPHDB_SPARQL_ENDPOINT)
        return [], []
    except Exception as exc:
        log.warning("graphdb_sparql.error | %s", exc)
        return [], []

    if not vars_:
        return [], []

    rows = [{c: _val(b, c) for c in vars_} for b in bindings]
    log.debug("graphdb_sparql.results | columns=%s rows=%d", vars_, len(rows))
    return vars_, rows


def _size_endpoint() -> str:
    base = ActiveConfig.GRAPHDB_SPARQL_ENDPOINT.rstrip("/")
    return base + "/size"


def _probe_timeout_seconds() -> int:
    timeout = int(getattr(ActiveConfig, "GRAPHDB_REQUEST_TIMEOUT", 15) or 15)
    return max(1, min(timeout, 5))


def probe(*, force: bool = False) -> bool:
    global _probe_cache
    if not ActiveConfig.GRAPHDB_ENABLED:
        return False
    now = time.monotonic()
    cached = _probe_cache.get("status")
    checked_at = float(_probe_cache.get("checked_at") or 0.0)
    if not force and cached is not None:
        ttl = _PROBE_CACHE_TTL_SUCCESS_S if cached else _PROBE_CACHE_TTL_FAILURE_S
        if (now - checked_at) < ttl:
            return bool(cached)
    try:
        resp = requests.get(_size_endpoint(), timeout=_probe_timeout_seconds())
        resp.raise_for_status()
        if not _probe_cache.get("status"):
            log.info("graphdb_sparql.probe_ok | endpoint=%s", ActiveConfig.GRAPHDB_SPARQL_ENDPOINT)
        _probe_cache = {"checked_at": now, "status": True}
        return True
    except Exception as exc:
        if _probe_cache.get("status") is not False:
            log.warning("graphdb_sparql.probe_failed | endpoint=%s error=%s", ActiveConfig.GRAPHDB_SPARQL_ENDPOINT, exc)
        else:
            log.debug("graphdb_sparql.probe_still_offline | endpoint=%s", ActiveConfig.GRAPHDB_SPARQL_ENDPOINT)
        _probe_cache = {"checked_at": now, "status": False}
        return False


def triple_count() -> int:
    if not ActiveConfig.GRAPHDB_ENABLED:
        return -1
    if not probe():
        return -1
    try:
        resp = requests.get(_size_endpoint(), timeout=_probe_timeout_seconds())
        resp.raise_for_status()
        return int(resp.text.strip())
    except Exception as exc:
        log.warning("graphdb_sparql.triple_count_failed | %s", exc)
        return -1


def get_entity_neighborhood(
    entity_label: str,
    k: int = 2,
    max_nodes: int = 50,
) -> list[tuple[str, str, str]]:
    if not ActiveConfig.GRAPHDB_ENABLED:
        return []

    label_lower = entity_label.strip().lower().replace('"', '\\"')

    hop1_sparql = f"""
    SELECT DISTINCT ?subjectLabel ?pred ?obj ?objLabel
    WHERE {{
      ?subject rdfs:label ?subjectLabel .
      FILTER(LCASE(STR(?subjectLabel)) = "{label_lower}")
      ?subject ?pred ?obj .
      OPTIONAL {{ ?obj rdfs:label ?objLabel }}
      FILTER(!isBlank(?obj))
      FILTER(!isBlank(?subject))
    }}
    LIMIT {max_nodes}
    """

    triples: list[tuple[str, str, str]] = []
    seen_mid_uris: set[str] = set()

    try:
        _, bindings = _execute(hop1_sparql)
    except Exception as exc:
        log.debug("graphdb_sparql.get_entity_neighborhood hop1_failed error=%s", exc)
        return []

    for b in bindings:
        s_label   = _val(b, "subjectLabel") or entity_label
        pred_uri  = _val(b, "pred") or ""
        pred_lbl  = _pred_label(pred_uri)
        if not pred_lbl:
            continue
        obj_raw   = _val(b, "obj") or ""
        obj_label = _val(b, "objLabel") or _shorten_uri(obj_raw)
        if obj_label:
            triples.append((s_label, pred_lbl, obj_label))
            if obj_raw.startswith("http"):
                seen_mid_uris.add(obj_raw)

    if k >= 2 and seen_mid_uris:
        for mid_uri in list(seen_mid_uris)[:4]:
            hop2_sparql = f"""
            SELECT DISTINCT ?midLabel ?pred2 ?obj2 ?obj2Label
            WHERE {{
              BIND(<{mid_uri}> AS ?mid)
              OPTIONAL {{ ?mid rdfs:label ?midLabel }}
              ?mid ?pred2 ?obj2 .
              OPTIONAL {{ ?obj2 rdfs:label ?obj2Label }}
              FILTER(!isBlank(?obj2))
            }}
            LIMIT 15
            """
            try:
                _, h2_bindings = _execute(hop2_sparql)
                for b in h2_bindings:
                    mid_lbl  = _val(b, "midLabel") or _shorten_uri(mid_uri)
                    p2_uri   = _val(b, "pred2") or ""
                    p2_lbl   = _pred_label(p2_uri)
                    if not p2_lbl:
                        continue
                    o2_raw   = _val(b, "obj2") or ""
                    o2_label = _val(b, "obj2Label") or _shorten_uri(o2_raw)
                    if o2_label:
                        triples.append((mid_lbl, p2_lbl, o2_label))
            except Exception as exc2:
                log.debug(
                    "graphdb_sparql.get_entity_neighborhood hop2_failed mid=%s error=%s",
                    mid_uri, exc2,
                )

    log.debug(
        "graphdb_sparql.get_entity_neighborhood | label=%s k=%d triples=%d",
        entity_label, k, len(triples),
    )
    return triples[:max_nodes]


def _pred_label(pred_uri: str) -> str:
    if not pred_uri:
        return ""
    _omit = {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "http://www.opengis.net/ont/geosparql#asWKT",
        "http://www.opengis.net/ont/geosparql#hasCentroid",
        "http://www.opengis.net/ont/geosparql#hasGeometry",
    }
    if pred_uri in _omit:
        return ""
    known: dict[str, str] = {
        "https://coolattin.ie/ontology#civilParish":  "civil parish",
        "https://coolattin.ie/ontology#barony":       "barony",
        "https://coolattin.ie/ontology#county":       "county",
        "https://coolattin.ie/ontology#inParish":     "in parish",
        "https://coolattin.ie/ontology#inBarony":     "in barony",
        "https://coolattin.ie/ontology#year":         "year",
        "https://coolattin.ie/ontology#count":        "count",
        "http://www.w3.org/2000/01/rdf-schema#label": "label",
        "https://schema.org/name":                    "name",
    }
    if pred_uri in known:
        return known[pred_uri]
    for prefix in (
        "https://coolattin.ie/ontology#",
        "https://schema.org/",
        "http://www.w3.org/2000/01/rdf-schema#",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "http://www.w3.org/2001/XMLSchema#",
    ):
        if pred_uri.startswith(prefix):
            local = pred_uri[len(prefix):]
            return _camel_to_words(local)
    frag = pred_uri.split("#")[-1] if "#" in pred_uri else pred_uri.rstrip("/").split("/")[-1]
    return _camel_to_words(frag) if frag else ""


def _shorten_uri(uri: str) -> str:
    if not uri or not uri.startswith("http"):
        return uri
    frag = uri.split("#")[-1] if "#" in uri else uri.rstrip("/").split("/")[-1]
    return frag


def _camel_to_words(s: str) -> str:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return spaced.lower().strip()
