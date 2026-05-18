"""
coolattin/integrations/graphdb_sparql.py

Local GraphDB SPARQL client — D8 RDF/KG comparative prototype.

=====================================================================
THIS IS THE ONLY MODULE THAT QUERIES THE LOCAL GRAPHDB INSTANCE
=====================================================================

All GraphDB communication is centralised here so that:
  - The endpoint URL and timeout come from config, not scattered constants.
  - Callers never see SPARQL or HTTP — they receive list[dict] bindings.
  - Unavailability is graceful: returns [] with a logged warning.

Repository: configured via GRAPHDB_SPARQL_ENDPOINT in config / .env
Default:    http://localhost:7200/repositories/coolattin

RDF prefix convention for the Coolattin KG:
  co:     https://coolattin.ie/ontology#
  ex:     https://coolattin.ie/resource/
  schema: https://schema.org/
  xsd:    http://www.w3.org/2001/XMLSchema#
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from config import ActiveConfig

log = logging.getLogger(__name__)

PREFIXES = """
PREFIX co:     <https://coolattin.ie/ontology#>
PREFIX ex:     <https://coolattin.ie/resource/>
PREFIX schema: <https://schema.org/>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
"""


# ------------------------------------------------------------------ #
# Internal helpers                                                     #
# ------------------------------------------------------------------ #

def _execute(sparql: str) -> tuple[list[str], list[dict]]:
    """
    Execute a SPARQL SELECT against the local GraphDB repository.
    Returns (vars, bindings) where vars is the ordered list of selected variable
    names from head.vars (always complete, even when OPTIONAL fields are unbound
    in some rows) and bindings is the raw binding list.
    Raises on HTTP / network error (caller must handle).
    """
    endpoint = ActiveConfig.GRAPHDB_SPARQL_ENDPOINT
    timeout = ActiveConfig.GRAPHDB_REQUEST_TIMEOUT
    full_query = PREFIXES + "\n" + sparql
    log.debug("graphdb_sparql.execute | preview=%s", full_query[:200].replace("\n", " "))

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


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def query(sparql: str) -> tuple[list[str], list[dict]]:
    """
    Execute a SPARQL SELECT and return (columns, rows) in table format.

    Always returns safely — callers receive ([], []) if GraphDB is
    unavailable or the query fails.

    Parameters
    ----------
    sparql : str
        A SPARQL SELECT query (without PREFIX declarations — they are
        prepended automatically).

    Returns
    -------
    columns : list[str]    variable names from the SELECT clause (from head.vars,
                           so OPTIONAL variables missing in the first binding are
                           still included)
    rows    : list[dict]   one dict per result row, keyed by variable name,
                           matching the format used by SQLite rows so the same
                           JS renderTable() call works for both backends
    """
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

    # Rows as dicts (keyed by variable name) so the JS renderTable() helper
    # can access row[columnName] identically for SQL and SPARQL results.
    # Use head.vars (not bindings[0].keys()) so OPTIONAL columns that happen
    # to be unbound in the first row are still present in every row dict.
    rows = [{c: _val(b, c) for c in vars_} for b in bindings]
    log.debug("graphdb_sparql.results | columns=%s rows=%d", vars_, len(rows))
    return vars_, rows


def _size_endpoint() -> str:
    """GraphDB REST /size endpoint — returns triple count as plain text, no SPARQL overhead."""
    base = ActiveConfig.GRAPHDB_SPARQL_ENDPOINT.rstrip("/")
    # endpoint is .../repositories/<name> — append /size
    return base + "/size"


def probe() -> bool:
    """Return True if the GraphDB repository is reachable (uses /size REST endpoint)."""
    if not ActiveConfig.GRAPHDB_ENABLED:
        return False
    try:
        resp = requests.get(_size_endpoint(), timeout=5)
        resp.raise_for_status()
        return True
    except Exception:
        return False


def triple_count() -> int:
    """
    Return the total number of triples in the repository.
    Returns 0 if the repo is empty, -1 if unreachable or disabled.
    Uses the /size REST endpoint to avoid SPARQL query overhead.
    """
    if not ActiveConfig.GRAPHDB_ENABLED:
        return -1
    try:
        resp = requests.get(_size_endpoint(), timeout=5)
        resp.raise_for_status()
        return int(resp.text.strip())
    except Exception as exc:
        log.warning("graphdb_sparql.triple_count_failed | %s", exc)
        return -1
