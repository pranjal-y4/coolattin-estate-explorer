import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from coolattin.services.datahub import DataHub

print("Initializing DataHub to trigger precomputation...")
try:
    hub = DataHub()
    print("DataHub initialized successfully.")
    if os.path.exists("coolattin/static/data/matched_records.csv"):
        print("matched_records.csv generated successfully.")
    else:
        print("matched_records.csv NOT found after initialization.")
except Exception as e:
    print(f"Error initializing DataHub: {e}")
