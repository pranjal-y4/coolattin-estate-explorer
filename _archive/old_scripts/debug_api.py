
import sys
import os
from pathlib import Path
import pandas as pd

sys.path.append(os.getcwd())

from coolattin.app import create_app

app = create_app()
client = app.test_client()

print("Testing /api/unified/records...")
response = client.get("/api/unified/records?limit=1")
print(f"Status: {response.status_code}")
if response.status_code == 500:
    print("Error detected. Please check the logs.")
else:
    print(response.data.decode('utf-8'))
