import pandas as pd
import sys
import os

sys.path.append(os.getcwd())

from coolattin.services.fuzzy import norm_key, _score

def generate_audit():
    print("Loading data for audit...")
    try:
        df = pd.read_csv("coolattin/static/data/matched_records.csv")
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return

    report_path = "coolattin/static/data/audit_report.md"
    print(f"Generating audit report at {report_path}...")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Coolattin Data Audit: Normalization & Fuzzy Logic\n\n")
        f.write("This report provides a record-by-record breakdown of how raw data was processed, normalized, and grouped.\n\n")
        
        f.write("## 1. Normalization Logic\n")
        f.write("The system applies the following `norm_key` function to all Surnames before matching:\n")
        f.write("1.  **Lowercase** the input.\n")
        f.write("2.  **Strip** leading/trailing whitespace.\n")
        f.write("3.  **Remove** all characters except `a-z`, `'`, `-`, and space.\n")
        f.write("4.  **Collapse** multiple spaces into one.\n\n")
        
        f.write("## 2. Fuzzy Matching Logic\n")
        f.write("Pairs of records within the **same Townland** (and within **15 years**) are compared using `Token Sort Ratio`.\n")
        f.write("-   **Threshold**: 80 (or 90 depending on config)\n")
        f.write("-   **Score calculation**: `_score(norm_a, norm_b)`\n\n")
        
        f.write("---\n\n")
        
def generate_audit():
    print("Loading data for audit...")
    try:
        df = pd.read_csv("coolattin/static/data/matched_records.csv")
    except Exception as e:
        print(f"Error loading matched_records.csv: {e}")
        return

    try:
        unmatch_df = pd.read_csv("coolattin/static/data/unmatch.csv")
    except Exception:
        unmatch_df = pd.DataFrame()

    report_path = "coolattin/static/data/audit_report.md"
    print(f"Generating audit report at {report_path}...")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Coolattin Data Audit: Individual Mapping & Fuzzy Logic\n\n")
        f.write("This report provides a record-by-record breakdown of how individuals were identified across different CSV sources.\n\n")
        
        f.write("## 1. Individual Mapping Logic\n")
        f.write("The system identifies individuals using a composite fuzzy score of **Surname** and **Forename**.\n")
        f.write("-   **Surname Weight**: 70%\n")
        f.write("-   **Forename Weight**: 30%\n")
        f.write("-   **Time Gap**: Max 20 years between linked records.\n")
        f.write("-   **Townland**: Records are grouped by townland for reporting but matching is global to catch movements.\n\n")
        
        f.write("## 2. Source Coverage\n")
        f.write("Records are pulled from: `tenancies.csv`, `emigrations_records.csv`, and `evictions_records.csv`.\n\n")
        
        f.write("---\n\n")
        
        f.write("## 3. Individual Life Graphs (by Townland Grouping)\n\n")

        if "townland_norm" not in df.columns:
            df["townland_norm"] = df["townland"].astype(str).str.upper().str.strip()

        for town, town_grp in df.groupby("townland_norm"):
            f.write(f"### Townland: {town}\n\n")
            
            for canon, indiv_grp in town_grp.groupby("surname_canon"):
                f.write(f"#### Individual: **{canon}** ({len(indiv_grp)} records)\n")
                
                unique_names = indiv_grp.apply(lambda r: f"{r['forename_raw']} {r['surname_raw']}".strip(), axis=1).unique()
                if len(unique_names) > 1:
                    f.write("\n> **Fuzzy Match Explanation:**\n")
                    f.write(f"> Linked records with slightly differing names: {', '.join(unique_names)}\n")
                    f.write("\n")

                f.write("| Raw Name | Year | Source | ID | Status |\n")
                f.write("| --- | --- | --- | --- | --- |\n")
                
                for _, row in indiv_grp.iterrows():
                    fn = str(row.get("forename_raw", ""))
                    sn = str(row.get("surname_raw", ""))
                    raw_name = f"{fn} {sn}".strip()
                    yr = row.get("year", "")
                    src = row.get("source", "")
                    rid = row.get("id", "")
                    
                    status = "✅ Mapped" if row.get("surname_ok", True) else "⚠️ Low Confidence"
                    f.write(f"| {raw_name} | {yr} | `{src}` | {rid} | {status} |\n")
                
                f.write("\n")
            f.write("---\n")

        if not unmatch_df.empty:
            f.write("\n## 4. Unmatched / Inconsistent Records\n\n")
            f.write("The following records were excluded from individual mapping due to naming inconsistencies (e.g., multiple people in one row, 'Unnamed', etc.).\n\n")
            f.write("| Raw Surname | Raw Forename | Year | Source | ID | Reason |\n")
            f.write("| --- | --- | --- | --- | --- | --- |\n")
            for _, row in unmatch_df.iterrows():
                sn = str(row.get("surname_raw", ""))
                fn = str(row.get("forename_raw", ""))
                yr = row.get("year", "")
                src = row.get("source", "")
                rid = row.get("id", "")
                
                reason = "Multi-person/Symbol" if any(t in sn for t in [";", "&", "/", "|"]) else "Missing Name"
                f.write(f"| {sn} | {fn} | {yr} | `{src}` | {rid} | {reason} |\n")
            f.write("\n")

if __name__ == "__main__":
    generate_audit()
