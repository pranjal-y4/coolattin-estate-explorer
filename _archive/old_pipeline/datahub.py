# coolattin/services/datahub.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import re
import pandas as pd

from .fuzzy import (
    is_consistent_single_name,
    cluster_individuals,
)

@dataclass
class PrecomputePaths:
    base: Path
    matched_csv: Path
    checked_csv: Path
    flagged_csv: Path
    stats_txt: Path
    stats_txt: Path
    edges_csv: Path
    compiled_json: Path
    townland_index_json: Path

class DataHub:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parents[1] / "static" / "data"
        self.paths = PrecomputePaths(
            base=base,
            matched_csv=base / "matched_records.csv",
            checked_csv=base / "checked_records.csv",
            flagged_csv=base / "flagged_records.csv",
            stats_txt=base / "fuzzy_stats.txt",
            edges_csv=base / "fuzzy_matches.csv",
            compiled_json=base / "compiled_records.json",
            townland_index_json=base / "townland_index.json",
        )

        self._townlands_geo = None
        self._townland_centroids = None

        self.ensure_precomputed()
        
        # Only load if files exist
        if self.paths.matched_csv.exists():
            self.matched = pd.read_csv(self.paths.matched_csv)
        else:
            self.matched = pd.DataFrame()
            
        if self.paths.checked_csv.exists():
            self.checked = pd.read_csv(self.paths.checked_csv)
        else:
            self.checked = pd.DataFrame()
            
        if self.paths.flagged_csv.exists():
            self.flagged = pd.read_csv(self.paths.flagged_csv)
        else:
            self.flagged = pd.DataFrame()

    # -----------------------
    # Precompute
    # -----------------------
    def ensure_precomputed(self) -> None:
        if self.paths.matched_csv.exists() and self.paths.compiled_json.exists():
            return
        self._precompute_all()

    def _read_csv(self, name: str) -> pd.DataFrame:
        p = self.paths.base / name
        if not p.exists():
            raise FileNotFoundError(f"Missing {p}. Put your CSVs inside coolattin/static/data/")
        return pd.read_csv(p)

    def _precompute_all(self) -> None:
        # Check if source files exist - if not, skip precomputation
        base = self.paths.base
        ten_path = base / "tenancies.csv"
        emi_path = base / "emigrations_records.csv"
        evi_path = base / "evictions_records.csv"
        
        if not ten_path.exists() or not emi_path.exists() or not evi_path.exists():
            print("Note: Source CSV files not found. Skipping precomputation.")
            print("Unified database will be used as primary source.")
            return
            
        ten = self._read_csv("tenancies.csv")
        emi = self._read_csv("emigrations_records.csv")
        evi = self._read_csv("evictions_records.csv")

        # Try to read census, but don't hard crash if missing
        cen_s = pd.DataFrame()
        try:
            cen = self._read_csv("wicklow-census-data.csv")
            # Census is townland-level; we’ll store it as “census” events by townland (no surname)
            cen_s = cen.copy()
            cen_s["source"] = "census"
            cen_s["id"] = cen_s.index.astype(str)
            cen_s["townland_norm"] = cen_s["Townland"].astype(str).str.strip().str.upper()
            cen_s["surname_raw"] = ""  # no surname in this census file
            cen_s["forename_raw"] = ""
            cen_s["year_norm"] = ""  # multi-year columns
            cen_s["details"] = cen_s.apply(lambda r: self._census_to_details(r), axis=1)
        except Exception:
            pass

        # Standardise
        def std(df: pd.DataFrame, source: str) -> pd.DataFrame:
            out = df.copy()
            out["source"] = source

            # id
            if "id" not in out.columns:
                out["id"] = out.index.astype(str)

            # year: handle year ranges
            if "year" in out.columns:
                out["year_norm"] = out["year"].astype(str).str.split("-").str[0].str.strip()
            else:
                out["year_norm"] = ""
            
            # clean year nan
            out.loc[out["year_norm"].str.lower() == "nan", "year_norm"] = ""

            # townland (with fallbacks) - Vectorized
            out["townland_norm"] = ""
            for col in ["townland_official", "townland", "Townland", "townland1", "townland2"]:
                if col in out.columns:
                    # Fill where still empty or invalid
                    mask = (out["townland_norm"] == "") | (out["townland_norm"] == "0") | (out["townland_norm"] == "NAN")
                    out.loc[mask, "townland_norm"] = out.loc[mask, col].fillna("").astype(str).str.strip().str.upper()
            
            # Final cleanup for townland_norm
            for val in ["NAN", "0", "NULL", "NONE"]:
                out.loc[out["townland_norm"] == val, "townland_norm"] = ""

            # Extract Surname (with fallbacks)
            out["surname_raw"] = ""
            if "surname" in out.columns:
                out["surname_raw"] = out["surname"].fillna("").astype(str).str.strip()
            
            if "surname_tenant_chief" in out.columns:
                mask = (out["surname_raw"] == "") | (out["surname_raw"].str.lower() == "nan")
                out.loc[mask, "surname_raw"] = out.loc[mask, "surname_tenant_chief"].fillna("").astype(str).str.strip()
            
            # Extract Forename (with fallbacks)
            out["forename_raw"] = ""
            if "forename" in out.columns:
                out["forename_raw"] = out["forename"].fillna("").astype(str).str.strip()
            
            if "forename_tenant_chief" in out.columns:
                mask = (out["forename_raw"] == "") | (out["forename_raw"].str.lower() == "nan")
                out.loc[mask, "forename_raw"] = out.loc[mask, "forename_tenant_chief"].fillna("").astype(str).str.strip()
            
            # Clean up both and strip markers
            for col in ["surname_raw", "forename_raw"]:
                mask = out[col].str.lower() == "nan"
                out.loc[mask, col] = ""
                # Strip "Multiple chief tenants" marker often found in forenames
                out[col] = out[col].str.replace("Multiple chief tenants", "", case=False).str.strip()

            # keep a printable “details” text (for the story panel)
            out["details"] = out.apply(lambda r: self._row_to_details(r), axis=1)
            return out

        ten_s = std(ten, "tenancies")
        emi_s = std(emi, "emigrations")
        evi_s = std(evi, "evictions")

        # Combine
        all_rows = pd.concat([ten_s, emi_s, evi_s], ignore_index=True)

        # Flag truly problematic records (completely unnamed or junk)
        # Only reject if BOTH surname and forename are missing/junk.
        def check_valid(r):
            s_ok = is_consistent_single_name(r["surname_raw"])
            f_ok = is_consistent_single_name(r["forename_raw"])
            return s_ok or f_ok

        all_rows["is_valid"] = all_rows.apply(check_valid, axis=1)
        
        # Even if match is not found, it stays in the main dataset (it will be a singleton cluster).
        unmatch = all_rows.loc[~all_rows["is_valid"]].copy()
        checked = all_rows.loc[all_rows["is_valid"]].copy()

        # Fuzzy: cluster within townland (and year proximity)
        rows_dict = checked.to_dict(orient="records")
        canon_map, edges = cluster_individuals(
            rows_dict,
            townland_key="townland_norm",
            year_key="year_norm",
            surname_key="surname_raw",
            forename_key="forename_raw",
            id_key="id",
            threshold=85,
            max_year_gap=20,
        )

        checked["surname_canon"] = checked["id"].astype(str).map(lambda x: canon_map.get(x, "")).fillna("")
        checked["individual_id"] = checked["surname_canon"] 
        checked["surname_ok"] = True
        
        # Build matched_records: keep individual_id for consistent rows, blank for flagged
        matched = all_rows.copy()
        matched["surname_canon"] = matched["id"].astype(str).map(lambda x: canon_map.get(x, "")).fillna("")
        matched["individual_id"] = matched["surname_canon"] # for alias
        matched["surname_ok"] = matched["is_valid"]

        # Save outputs
        self.paths.base.mkdir(parents=True, exist_ok=True)
        unmatch.to_csv(self.paths.base / "unmatch.csv", index=False)
        unmatch.to_csv(self.paths.flagged_csv, index=False) # legacy
        checked.to_csv(self.paths.checked_csv, index=False)
        matched.to_csv(self.paths.matched_csv, index=False)

        # Save edges
        edges_df = pd.DataFrame([{
            "townland": e.townland,
            "left_row_id": e.left_row_id,
            "right_row_id": e.right_row_id,
            "score": e.score,
            "left_surname": e.left_surname,
            "right_surname": e.right_surname,
            "left_forename": e.left_forename,
            "right_forename": e.right_forename,
        } for e in edges])
        edges_df.to_csv(self.paths.edges_csv, index=False)

        # Stats text
        stats_text = self._build_stats_text(matched, checked, unmatch, edges_df, cen_s)
        self.paths.stats_txt.write_text(stats_text, encoding="utf-8")

        # Compile Static Data for UI
        self._compile_static_data(matched)

    def _compile_static_data(self, matched: pd.DataFrame) -> None:
        # 1. Compiled Records: Global mapping of { surname_canon: [records...] }
        # Convert all to dict records first
        all_recs = matched.to_dict(orient="records")
        compiled = {}
        
        # Helper to clean nan
        def clean_val(v):
            if pd.isna(v): return ""
            s = str(v).strip()
            if s.lower() == "nan": return ""
            return s

        for r in all_recs:
            # Clean all fields in the record immediately
            for k, v in r.items():
                r[k] = clean_val(v)

            # Store a list of potential townland matches for the UI to filter on
            tls = set()
            for k in ["townland_norm", "townland", "townland1", "townland2", "Townland"]:
                v = r.get(k)
                if v and str(v).strip() and str(v).lower() != "nan" and str(v) != "0":
                    tls.add(str(v).strip().upper())
            r["_townlands"] = list(tls)

            canon = r.get("surname_canon")
            if not canon: 
                canon = r.get("surname_raw")
            
            if not canon: continue
            
            if canon not in compiled:
                compiled[canon] = []
            
            compiled[canon].append(r)

        self.paths.compiled_json.write_text(json.dumps(compiled, indent=2), encoding="utf-8")

        # 2. Townland Index: { townland: { tenants: [...], families: {name: {count, sources}, ...} } }
        townland_index = {}
        
        # Group by townland
        for r in all_recs:
            tl = str(r.get("townland_norm", "")).upper()
            if not tl:
                tl = str(r.get("townland", "")).upper()
            if not tl:
                tl = str(r.get("townland1", "")).upper()
                
            if not tl: continue
            
            if tl not in townland_index:
                townland_index[tl] = {"tenants": {}, "families": {}}
            
            # Families / Everyone (Surnames) with Counts and Sources
            # We use surname_canon if available, otherwise raw surname so no one is lost
            canon = r.get("surname_canon")
            if not canon or str(canon).strip() == "":
                canon = r.get("surname_raw")
            
            src = r.get("source", "unknown")
            
            if canon:
                if canon not in townland_index[tl]["families"]:
                    townland_index[tl]["families"][canon] = {"count": 0, "sources": set()}
                
                f_entry = townland_index[tl]["families"][canon]
                f_entry["count"] += 1
                f_entry["sources"].add(src)
            
            # Tenants History: Timeline Logic (strictly from tenancies source for tenure history)
            src_lower = str(src).lower()
            role = r.get("tenant_type", "Tenant") 
            if not role: role = "Tenant"
            
            if src_lower == "tenancies":
                if role in ["Tenant", "Chief Tenant"]:
                    fn = r.get("forename_raw", "")
                    sn = r.get("surname_raw", "")
                    yr_str = str(r.get("year", "")).strip()
                    
                    # Extract integer year for sorting/ranges
                    yr_int = 0
                    if yr_str:
                        m = re.match(r"(\d{4})", yr_str)
                        if m: yr_int = int(m.group(1))

                    if fn or sn:
                        name = f"{fn} {sn}".strip()
                        key = f"{name}|{role}"
                        
                        if key not in townland_index[tl]["tenants"]:
                            townland_index[tl]["tenants"][key] = {
                                "name": name,
                                "years": set(),
                                "role": role,
                                "source": src_lower,
                                "surname": canon if canon else sn,
                                "ids": []
                            }
                        if yr_str:
                            townland_index[tl]["tenants"][key]["years"].add(yr_str)
                        townland_index[tl]["tenants"][key]["ids"].append(str(r.get("id")))

        # After collecting all, flatten into final JSON structure
        final_index = {}
        for tl in townland_index:
            # Flatten Tenants
            t_dict = townland_index[tl]["tenants"]
            final_tenants = []
            for k, t in t_dict.items():
                yrs = sorted(list(t["years"]))
                yr_ints = sorted(list(set([int(re.match(r"(\d{4})", y).group(1)) for y in yrs if re.match(r"(\d{4})", y)])))
                
                display_yr = ", ".join(yrs)
                if len(yr_ints) > 1:
                    display_yr = f"{yr_ints[0]}-{yr_ints[-1]}"
                elif len(yr_ints) == 1:
                    display_yr = str(yr_ints[0])

                final_tenants.append({
                    "name": t["name"],
                    "year": display_yr,
                    "year_int": yr_ints[0] if yr_ints else 0,
                    "role": t["role"],
                    "source": t["source"],
                    "surname": t["surname"],
                    "ids": t["ids"]
                })
            
            # Flatten Families
            raw_families = townland_index[tl]["families"]
            final_families = sorted([
                {
                    "name": name, 
                    "count": info["count"], 
                    "sources": sorted(list(info["sources"]))
                } 
                for name, info in raw_families.items()
            ], key=lambda x: x["name"])

            final_index[tl] = {
                "tenants": sorted(final_tenants, key=lambda x: x["year_int"]),
                "families": final_families
            }
            
        self.paths.townland_index_json.write_text(json.dumps(final_index, indent=2), encoding="utf-8")

    def _row_to_details(self, r: pd.Series) -> str:
        def safe_str(val):
            s = str(val).strip()
            if s.upper() == "NAN" or s.lower() == "nan": return ""
            return s

        src = safe_str(r.get("source", ""))
        year = safe_str(r.get("year", r.get("year_norm", "")))
        town = safe_str(r.get("townland_norm", ""))
        s = safe_str(r.get("surname_raw", ""))
        f = safe_str(r.get("forename_raw", ""))

        parts = [f"{src}"]
        if year:
            parts.append(f"year={year}")
        if town:
            parts.append(f"townland={town}")
        if s:
            parts.append(f"name={s}{(', ' + f) if f else ''}")

        # EXCLUDE internal columns or already-shown fields
        sub_exclude = {
            "id", "source", "year", "year_norm", "townland", "townland_official", 
            "townland_norm", "surname", "surname_raw", "forename", "forename_raw", 
            "surname_canon", "surname_ok", "details", "townland1", "townland2"
        }
        
        # Iterate over ALL columns in the row
        for col, val in r.items():
            c_str = str(col).strip()
            if c_str in sub_exclude:
                continue
            
            # Check if value is meaningful
            if pd.notna(val) and str(val).strip():
                parts.append(f"{c_str}={str(val).strip()}")

        return " · ".join(parts)

    def _census_to_details(self, r: pd.Series) -> str:
        # quick summary: total pop for each census year columns if available
        tl = str(r.get("Townland", "")).strip()
        parts = [f"census townland={tl}"]
        for yr in ["1841", "1851", "1861", "1871", "1881", "1891"]:
            m = r.get(f"{yr} Male", "")
            f = r.get(f"{yr} Female", "")
            if pd.notna(m) and pd.notna(f) and str(m).strip() and str(f).strip():
                try:
                    total = int(float(m)) + int(float(f))
                    parts.append(f"{yr} pop={total}")
                except Exception:
                    pass
        return " · ".join(parts)

    def _build_stats_text(
        self,
        matched: pd.DataFrame,
        checked: pd.DataFrame,
        flagged: pd.DataFrame,
        edges_df: pd.DataFrame,
        census_df: pd.DataFrame,
    ) -> str:
        # counts
        by_source = matched.groupby("source")["id"].count().to_dict()
        total_records = int(matched.shape[0])
        total_checked = int(checked.shape[0])
        total_flagged = int(flagged.shape[0])

        # “linked” = checked rows that have at least one fuzzy connection (belongs to a cluster size>1)
        linked_row_ids = set(edges_df["left_row_id"].astype(str).tolist() + edges_df["right_row_id"].astype(str).tolist())
        linked_records = sum(checked["id"].astype(str).isin(linked_row_ids))
        link_rate = (linked_records / total_checked * 100.0) if total_checked else 0.0

        # IMPORTANT: this can never be >100 now
        # edges count = pair links; not a rate
        edge_count = int(edges_df.shape[0])

        census_rows = int(census_df.shape[0])

        lines = []
        lines.append("COOLATTIN — FUZZY LINKAGE SUMMARY")
        lines.append("")
        lines.append(f"Total records (3 person-level datasets): {total_records:,}")
        lines.append(f"Checked records (single-surname):        {total_checked:,}")
        lines.append(f"Flagged records (inconsistent surname): {total_flagged:,}")
        lines.append("")
        lines.append("Breakdown (person-level):")
        for k in ["tenancies", "emigrations", "evictions"]:
            lines.append(f"  - {k}: {int(by_source.get(k, 0)):,}")
        lines.append("")
        lines.append(f"Census rows (townland-level): {census_rows:,}")
        lines.append("")
        lines.append(f"Fuzzy links (edges/pairs): {edge_count:,}")
        lines.append(f"Linked checked records:    {linked_records:,}")
        lines.append(f"Link rate (linked/checked): {link_rate:.1f}%")
        lines.append("")
        lines.append("Notes:")
        lines.append("  - Flagged records are excluded from surname dropdown and fuzzy linkage.")
        lines.append("  - Link rate is capped logically (<=100%) because it is record-based, not edge-based.")
        return "\n".join(lines)

    # -----------------------
    # Geo: townlands + centroids
    # -----------------------
    def townlands_geo(self) -> Dict:
        if self._townlands_geo is None:
            p = self.paths.base / "townlands.json"
            self._townlands_geo = json.loads(p.read_text(encoding="utf-8"))
        return self._townlands_geo

    def townland_centroids(self) -> Dict[str, Tuple[float, float]]:
        if self._townland_centroids is not None:
            return self._townland_centroids

        geo = self.townlands_geo()
        out: Dict[str, Tuple[float, float]] = {}

        def centroid(coords: List) -> Tuple[float, float]:
            # coords is list of [lon, lat] rings; use first ring
            ring = coords[0]
            xs = [pt[0] for pt in ring]
            ys = [pt[1] for pt in ring]
            return (sum(ys) / len(ys), sum(xs) / len(xs))  # lat, lon

        for f in geo.get("features", []):
            props = f.get("properties", {}) or {}
            name = str(props.get("TL_ENGLISH", "")).strip().upper()
            geom = f.get("geometry", {}) or {}
            gtype = geom.get("type")

            if not name:
                continue

            if gtype == "Polygon":
                lat, lon = centroid(geom.get("coordinates", []))
                out[name] = (lat, lon)
            elif gtype == "MultiPolygon":
                # take first polygon
                mp = geom.get("coordinates", [])
                if mp and mp[0]:
                    lat, lon = centroid(mp[0])
                    out[name] = (lat, lon)

        self._townland_centroids = out
        return out

    # -----------------------
    # API helpers
    # -----------------------
    def all_townlands(self) -> List[str]:
        return sorted(self.townland_centroids().keys())

    def surnames_checked_canonical(self) -> List[str]:
        s = self.checked["surname_canon"].dropna().astype(str)
        s = s[s.str.strip() != ""]
        return sorted(s.unique().tolist())

    def records_for_surname(self, surname: str, types: Optional[List[str]] = None) -> pd.DataFrame:
        df = self.matched.copy()
        s = surname.strip()
        df = df[df["surname_canon"].astype(str) == s]
        if types:
            df = df[df["source"].isin(types)]
        return df

    def records_for_townland(self, townland: str, types: Optional[List[str]] = None) -> pd.DataFrame:
        df = self.matched.copy()
        t = townland.strip().upper()
        df = df[df["townland_norm"].astype(str) == t]
        if types:
            df = df[df["source"].isin(types)]
        return df

    def stats_text(self) -> str:
        return self.paths.stats_txt.read_text(encoding="utf-8")
