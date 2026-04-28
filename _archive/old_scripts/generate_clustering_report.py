import pandas as pd
import sys

def generate_report():
    print("Loading matched records...")
    try:
        df = pd.read_csv("coolattin/static/data/matched_records.csv")
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return

    # Normalize fields for grouping
    df["townland"] = df["townland_norm"].fillna("Unknown").astype(str)
    df["canon"] = df["surname_canon"].fillna("Unassigned").astype(str)
    df["year"] = df["year_norm"].fillna("").astype(str)

    # Sort
    df = df.sort_values(["townland", "canon", "year", "surname_raw"])

    report_path = "coolattin/static/data/clustering_report.md"
    
    print(f"Generating report at {report_path}...")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Coolattin Records - Clustering Report\n\n")
        f.write("This report details how individual records have been grouped into Families (Clusters) based on Townland, Surname Similarity, and Time.\n\n")
        
        # Iterate Townlands
        for town, town_grp in df.groupby("townland"):
            f.write(f"## Townland: {town}\n\n")
            
            # Iterate Families within Townland
            # Note: Although clustering is global, we present it by Townland as requested
            for canon, fam_grp in town_grp.groupby("canon"):
                f.write(f"### Family: **{canon}** ({len(fam_grp)} records)\n")
                
                # Table header
                f.write("| Source | Year | Surname (Raw) | Forename | Details |\n")
                f.write("| --- | --- | --- | --- | --- |\n")
                
                for _, row in fam_grp.iterrows():
                    src = row.get('source', '')
                    yr = row.get('year', '')
                    s_raw = row.get('surname_raw', '')
                    f_raw = row.get('forename_raw', '')
                    
                    # Construct extra details
                    extras = []
                    if src == "tenancies":
                        extras.append(f"Rent: £{row.get('rent_owed', '')}")
                        extras.append(f"Acres: {row.get('acres_irish', '')}")
                    elif src == "emigration":
                        extras.append(f"Ship: {row.get('ship_name', '')}")
                    
                    det_str = "; ".join(extras)
                    
                    f.write(f"| {src} | {yr} | {s_raw} | {f_raw} | {det_str} |\n")
                
                f.write("\n")
            f.write("---\n\n")

    print("Done.")

if __name__ == "__main__":
    generate_report()
