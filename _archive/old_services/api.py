from flask import Blueprint, jsonify, send_file
import pandas as pd
import json
from pathlib import Path

api = Blueprint("api", __name__)
DATA_DIR = Path("coolattin/static/data")

# Load unified database once at startup
_unified_df = None

def get_unified_data():
    global _unified_df
    if _unified_df is None:
        _unified_df = pd.read_csv(DATA_DIR / "unified_processed.csv")
    return _unified_df

@api.route("/api/map-data")
def map_data():
    df = get_unified_data()
    # Return records with townland data
    df = df[df['townland'].notna() & (df['townland'] != '')]
    return jsonify(df.to_dict(orient="records"))

@api.route("/api/unified-data")
def unified_data():
    """Return unified database records with optional filtering"""
    df = get_unified_data()
    return jsonify(df.to_dict(orient="records"))

@api.route("/api/unified-search")
def unified_search():
    """Search unified database"""
    from flask import request
    df = get_unified_data()
    
    surname = request.args.get('surname', '').strip()
    forename = request.args.get('forename', '').strip()
    townland = request.args.get('townland', '').strip()
    year = request.args.get('year', '').strip()
    
    if surname:
        df = df[df['surname'].str.lower().str.contains(surname.lower(), na=False)]
    if forename:
        df = df[df['forename'].str.lower().str.contains(forename.lower(), na=False)]
    if townland:
        df = df[df['townland'].str.lower().str.contains(townland.lower(), na=False)]
    if year:
        df = df[df['year'].astype(str).str.contains(year, na=False)]
    
    return jsonify(df.head(100).to_dict(orient="records"))

@api.route("/api/unified-stats")
def unified_stats():
    """Return statistics from unified database"""
    df = get_unified_data()
    return jsonify({
        'total_records': len(df),
        'unique_surnames': int(df['surname'].nunique()),
        'unique_forenames': int(df['forename'].nunique()),
        'unique_townlands': int(df['townland'].nunique()),
        'unique_estates': int(df['estate'].nunique()),
    })

@api.route("/api/flagged-download")
def flagged_download():
    return send_file(DATA_DIR / "flagged.csv", as_attachment=True)
