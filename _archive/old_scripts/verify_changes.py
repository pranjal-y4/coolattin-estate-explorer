import sys
import os
import pandas as pd

sys.path.append(os.getcwd())

from coolattin.services.datahub import DataHub
from scripts.generate_audit_report import generate_audit

def main():
    print("--- Verification Started ---")
    
    matched_path = "coolattin/static/data/matched_records.csv"
    if os.path.exists(matched_path):
        os.remove(matched_path)
    
    print("Initializing DataHub (this will trigger precompute)...")
    hub = DataHub()
    
    print("Generating updated Audit Report...")
    generate_audit()
    
    print("Checking for unmatch.csv...")
    unmatch_path = "coolattin/static/data/unmatch.csv"
    if os.path.exists(unmatch_path):
        df_unmatch = pd.read_csv(unmatch_path)
        print(f"unmatch.csv created with {len(df_unmatch)} records.")
    else:
        print("unmatch.csv NOT found.")

    print("Checking matched_records.csv for individual_id...")
    df_matched = pd.read_csv(matched_path)
    if "individual_id" in df_matched.columns:
        print("individual_id found in matched_records.csv.")
        unique_indivs = df_matched["individual_id"].dropna().nunique()
        print(f"Total unique individuals identified: {unique_indivs}")
    else:
        print("individual_id NOT found.")

    print("--- Verification Finished ---")

if __name__ == "__main__":
    main()
