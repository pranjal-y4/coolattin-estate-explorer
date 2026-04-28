# coolattin/services/fuzzy.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

# Try RapidFuzz; fallback to difflib if environment mismatch
try:
    from rapidfuzz import fuzz  # type: ignore
    def _score(a: str, b: str) -> int:
        return int(fuzz.token_sort_ratio(a, b))
except Exception:
    import difflib
    def _score(a: str, b: str) -> int:
        return int(difflib.SequenceMatcher(None, a, b).ratio() * 100)

NAME_OK = re.compile(r"^[A-Za-z' -]+$")

def norm_text(x: object) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def norm_key(x: object) -> str:
    s = norm_text(x).lower()
    s = s.replace("’", "'")
    # Keep some symbols that might be part of multi-person or complex entries
    s = re.sub(r"[^a-z0-9' &_/-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def is_consistent_single_name(x: object) -> bool:
    """
    User wants to keep multi-person / multi-surname strings.
    Only reject if truly empty or nonsense.
    """
    s = norm_text(x)
    if not s or s.lower() == "nan" or s.strip() == "?" or s.strip() == "unknown":
        return False
    # If it's mostly punctuation, reject
    if not re.search(r"[A-Za-z]", s):
        return False
    return True

def surname_similarity(a: object, b: object) -> int:
    return _score(norm_key(a), norm_key(b))

def person_similarity(
    s1: str, f1: str, 
    s2: str, f2: str,
    *,
    surname_weight: float = 0.7,
    forename_weight: float = 0.3
) -> int:
    """
    Combines surname and forename similarity.
    If forename is missing in either, it defaults to a surname-only score (or lower penalty).
    """
    s_score = surname_similarity(s1, s2)
    
    f1_norm = norm_key(f1)
    f2_norm = norm_key(f2)
    
    if not f1_norm or not f2_norm:
        # If one forename is missing, we rely on surname but maybe penalise slightly 
        # or just return surname score. Here we return surname score.
        return s_score
    
    f_score = _score(f1_norm, f2_norm)
    
    # Composite score
    combined = (s_score * surname_weight) + (f_score * forename_weight)
    return int(combined)

@dataclass
class MatchEdge:
    left_row_id: str
    right_row_id: str
    score: int
    left_surname: str
    right_surname: str
    left_forename: str
    right_forename: str
    townland: str

def cluster_individuals(
    rows: List[Dict],
    *,
    townland_key: str,
    year_key: str,
    surname_key: str,
    forename_key: str,
    id_key: str,
    threshold: int = 85,
    max_year_gap: int = 20,
) -> Tuple[Dict[str, str], List[MatchEdge]]:
    """
    Clusters records into 'individuals' based on Surname + Forename.
    
    Returns:
      canonical_by_rowid: row_id -> individual_id (string)
      edges: list of fuzzy links used
    """
    def yr(r: Dict) -> Optional[int]:
        v = r.get(year_key, "")
        if v is None:
            return None
        s = str(v).strip()
        if "-" in s:
            s = s.split("-", 1)[0].strip()
        try:
            return int(float(s))
        except Exception:
            return None

    eligible: List[Dict] = []
    for r in rows:
        if not is_consistent_single_name(r.get(surname_key, "")):
            continue
        eligible.append(r)

    # Sort by priority and time
    eligible.sort(key=lambda r: (
        0 if r.get("source") == "tenancies" else 1,
        norm_key(r.get(townland_key, "")), 
        yr(r) or 0, 
        norm_key(r.get(surname_key, ""))
    ))

    clusters: List[Dict] = []
    # Optimization: index clusters by first letter of surname, townland, and year bucket
    clusters_by_prefix: Dict[str, List[int]] = {}
    clusters_by_townland: Dict[str, List[int]] = {}
    clusters_by_year: Dict[int, List[int]] = {} # e.g. year // 10
    
    canonical_by_rowid: Dict[str, str] = {}
    edges: List[MatchEdge] = []

    for r in eligible:
        rid = str(r.get(id_key))
        sraw = norm_text(r.get(surname_key, ""))
        fraw = norm_text(r.get(forename_key, ""))
        traw = norm_text(r.get(townland_key, "")).upper()
        y = yr(r)

        best_idx = None
        best_score = -1

        # Determine which clusters to compare against
        prefix = norm_key(sraw)[:1]
        
        # We only consider clusters that match the prefix OR townland
        # AND are within a reasonable year range.
        potential_by_name = []
        if prefix in clusters_by_prefix:
            potential_by_name = clusters_by_prefix[prefix]
        
        potential_by_town = []
        if traw and traw != "NAN" and traw in clusters_by_townland:
            potential_by_town = clusters_by_townland[traw]
            
        # Combine candidates (union)
        candidate_indices = set(potential_by_name) | set(potential_by_town)
            
        for i in candidate_indices:
            c = clusters[i]
            cy = c.get("year")
            
            # Time filter
            if y is not None and cy is not None and abs(y - cy) > max_year_gap:
                continue

            score = person_similarity(c["canon_surname"], c["canon_forename"], sraw, fraw)
            if score >= threshold and score > best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            # Create a new cluster / individual identity
            idx = len(clusters)
            indiv_label = f"{sraw}, {fraw}".strip(", ")
            clusters.append({
                "canon_surname": sraw,
                "canon_forename": fraw,
                "canon_label": indiv_label,
                "townland": traw,
                "year": y,
                "members": [r],
            })
            canonical_by_rowid[rid] = indiv_label
            
            # Update indices
            if prefix:
                clusters_by_prefix.setdefault(prefix, []).append(idx)
            if traw and traw != "NAN":
                clusters_by_townland.setdefault(traw, []).append(idx)
        else:
            c = clusters[best_idx]
            left = c["members"][0]
            edges.append(MatchEdge(
                left_row_id=str(left.get(id_key)),
                right_row_id=rid,
                score=best_score,
                left_surname=c["canon_surname"],
                right_surname=sraw,
                left_forename=c["canon_forename"],
                right_forename=fraw,
                townland=traw or "GLOBAL",
            ))
            c["members"].append(r)
            canonical_by_rowid[rid] = c["canon_label"]

    return canonical_by_rowid, edges
