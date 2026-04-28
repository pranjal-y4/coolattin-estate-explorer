#!/usr/bin/env python3
"""
Script to map townlands between GeoJSON and CSV data
Generates a mapping with exact matches and fuzzy matching
"""

import json
import pandas as pd
from fuzzywuzzy import fuzz
import argparse
import os

def load_geojson_townlands(filepath):
    """Load townland names from GeoJSON file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    townlands = []
    for feature in data.get('features', []):
        name = feature['properties'].get('TL_ENGLISH')
        if name:
            townlands.append({
                'json_name': name.strip(),
                'json_name_upper': name.strip().upper()
            })
    return townlands

def load_csv_townlands(filepath):
    """Load townland names from CSV file"""
    df = pd.read_csv(filepath)
    townlands = []
    for name in df['townland_clean'].unique():
        if pd.notna(name):
            townlands.append({
                'csv_name': str(name).strip(),
                'csv_name_upper': str(name).strip().upper()
            })
    return townlands

def fuzzy_match(json_name, csv_name, threshold=85):
    """Check if names match using fuzzy string matching"""
    score = fuzz.ratio(json_name.upper(), csv_name.upper())
    return score >= threshold, score

def normalize_name(name):
    """Normalize townland name for matching"""
    name = name.upper()
    # Remove common variations
    name = name.replace(' CO ', ' ').replace(' OR ', ' ')
    name = ' '.join(name.split())  # Normalize whitespace
    return name

def map_townlands(json_townlands, csv_townlands, output_file):
    """Map townlands and generate output"""

    # Create output list
    mappings = []
    unmatched_csv = []
    unmatched_json = []

    # Track matched records
    matched_csv = set()
    matched_json = set()

    # First pass: Exact matches (case-insensitive)
    json_dict = {t['json_name_upper']: t for t in json_townlands}
    csv_dict = {t['csv_name_upper']: t for t in csv_townlands}

    for csv_t in csv_townlands:
        csv_name_upper = csv_t['csv_name_upper']
        if csv_name_upper in json_dict:
            # Exact match
            json_t = json_dict[csv_name_upper]
            mappings.append({
                'csv_name': csv_t['csv_name'],
                'json_name': json_t['json_name'],
                'match_type': 'exact',
                'match_score': 100,
                'approval': 'YES'
            })
            matched_csv.add(csv_t['csv_name'])
            matched_json.add(json_t['json_name'])

    # Second pass: Fuzzy matching
    for csv_t in csv_townlands:
        if csv_t['csv_name'] in matched_csv:
            continue

        best_match = None
        best_score = 0

        for json_t in json_townlands:
            if json_t['json_name'] in matched_json:
                continue

            match, score = fuzzy_match(json_t['json_name'], csv_t['csv_name'])
            if match and score > best_score:
                best_score = score
                best_match = json_t

        if best_match:
            mappings.append({
                'csv_name': csv_t['csv_name'],
                'json_name': best_match['json_name'],
                'match_type': 'fuzzy',
                'match_score': best_score,
                'approval': 'PENDING'
            })
            matched_csv.add(csv_t['csv_name'])
            matched_json.add(best_match['json_name'])
        else:
            unmatched_csv.append(csv_t['csv_name'])

    # Collect unmatched JSON townlands
    for json_t in json_townlands:
        if json_t['json_name'] not in matched_json:
            unmatched_json.append(json_t['json_name'])

    # Create DataFrame
    df_mappings = pd.DataFrame(mappings)

    # Add count of records for each matched townland
    # This will require loading the full CSV data
    full_csv = pd.read_csv('/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/aggregated_records.csv')
    townland_counts = full_csv['townland_clean'].value_counts()

    def get_count(csv_name):
        return townland_counts.get(csv_name, 0)

    df_mappings['record_count'] = df_mappings['csv_name'].apply(get_count)

    # Reorder columns
    df_mappings = df_mappings[['csv_name', 'json_name', 'match_type', 'match_score', 'record_count', 'approval']]

    # Save to CSV
    df_mappings.to_csv(output_file, index=False)

    # Print summary
    print(f"Mapping Summary:")
    print(f"  Total CSV townlands: {len(csv_townlands)}")
    print(f"  Total JSON townlands: {len(json_townlands)}")
    print(f"  Exact matches: {len(df_mappings[df_mappings['approval'] == 'YES'])}")
    print(f"  Fuzzy matches (pending): {len(df_mappings[df_mappings['approval'] == 'PENDING'])}")
    print(f"  Unmatched CSV: {len(unmatched_csv)}")
    print(f"  Unmatched JSON: {len(unmatched_json)}")
    print(f"\nOutput saved to: {output_file}")

    # Show sample of mappings
    print("\nSample mappings:")
    print(df_mappings.head(20).to_string(index=False))

    # Save unmatched lists
    unmatched_file = output_file.replace('.csv', '_unmatched.txt')
    with open(unmatched_file, 'w') as f:
        f.write("Unmatched CSV townlands:\n")
        f.write('\n'.join(unmatched_csv))
        f.write("\n\nUnmatched JSON townlands:\n")
        f.write('\n'.join(unmatched_json))
    print(f"\nUnmatched list saved to: {unmatched_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Map townlands between GeoJSON and CSV')
    parser.add_argument('--json', default='/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/townlands.json',
                        help='Path to GeoJSON file')
    parser.add_argument('--csv', default='/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/aggregated_records.csv',
                        help='Path to CSV file')
    parser.add_argument('--output', default='/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/townland_mapping.csv',
                        help='Output CSV file')
    parser.add_argument('--threshold', type=int, default=85,
                        help='Fuzzy match threshold (0-100)')

    args = parser.parse_args()

    print(f"Loading GeoJSON from: {args.json}")
    json_townlands = load_geojson_townlands(args.json)

    print(f"Loading CSV from: {args.csv}")
    csv_townlands = load_csv_townlands(args.csv)

    map_townlands(json_townlands, csv_townlands, args.output)
