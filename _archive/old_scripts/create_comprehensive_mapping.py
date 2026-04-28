#!/usr/bin/env python3
"""
Townland Mapping Analysis & Detailed Report Generator
-----------------------------------------------------
This script creates a comprehensive mapping file comparing townlands
across all datasets in the Coolattin Estate project.
"""

import json
import pandas as pd
from fuzzywuzzy import fuzz
import os
from datetime import datetime

def analyze_and_report():
    """Generate comprehensive townland mapping report"""

    # 1. Load all townland sources
    print("Loading data sources...")

    # JSON Townlands
    json_path = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/townlands.json'
    with open(json_path, 'r') as f:
        json_data = json.load(f)
    json_townlands = [f['properties']['TL_ENGLISH'].strip() for f in json_data['features'] if f['properties'].get('TL_ENGLISH')]

    # Aggregated CSV (source for 516 townlands in Map Explorer dropdown)
    csv_path = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/aggregated_records.csv'
    df_csv = pd.read_csv(csv_path)
    csv_townlands = [str(t).strip() for t in df_csv['townland_clean'].unique() if pd.notna(t)]

    # Unified census data
    census_path = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/unified_census.csv'
    df_census = pd.read_csv(census_path)
    census_townlands = [str(t).strip().upper() for t in df_census['Townland'].unique() if pd.notna(t)]

    # Other data sources for reference
    tenancies_path = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/tenancies.csv'
    wicklow_census_path = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/wicklow-census-data.csv'

    # 2. Create comprehensive mapping
    mappings = []

    # Normalize names for matching
    def normalize(name):
        return ' '.join(str(name).strip().upper().split())

    # Create dictionaries
    json_dict = {normalize(t): t for t in json_townlands}
    csv_dict = {normalize(t): t for t in csv_townlands}
    census_dict = {normalize(t): t for t in census_townlands}

    # 3. Match townlands across all datasets
    all_normalized = set(list(json_dict.keys()) + list(csv_dict.keys()) + list(census_dict.keys()))

    for norm_name in all_normalized:
        json_name = json_dict.get(norm_name)
        csv_name = csv_dict.get(norm_name)
        census_name = census_dict.get(norm_name)

        # Determine matches
        json_match = "YES" if json_name else "NO"
        csv_match = "YES" if csv_name else "NO"
        census_match = "YES" if census_name else "NO"

        # Find exact matches
        if json_name and csv_name and csv_name == json_name:
            csv_to_json_match = "EXACT"
        elif json_name and csv_name:
            score = fuzz.ratio(csv_name.upper(), json_name.upper())
            csv_to_json_match = f"FUZZY ({score}%)"
        else:
            csv_to_json_match = "NO MATCH"

        if csv_name and census_name:
            csv_to_census_match = "EXACT"
        else:
            csv_to_census_match = "NO MATCH"

        # Get record counts
        record_count = len(df_csv[df_csv['townland_clean'].str.strip() == csv_name]) if csv_name else 0

        # Determine approval status
        if json_match == "YES" and csv_match == "YES" and csv_to_json_match == "EXACT":
            approval = "APPROVED"
        elif json_match == "YES" and csv_match == "YES" and "FUZZY" in csv_to_json_match:
            approval = "PENDING_REVIEW"
        elif csv_match == "YES" or census_match == "YES":
            approval = "APPROVED"
        else:
            approval = "NEEDS_REVIEW"

        mappings.append({
            'normalized_name': norm_name,
            'json_townland': json_name if json_name else '',
            'csv_townland': csv_name if csv_name else '',
            'census_townland': census_name if census_name else '',
            'in_json': json_match,
            'in_csv': csv_match,
            'in_census': census_match,
            'csv_to_json_match': csv_to_json_match,
            'csv_to_census_match': csv_to_census_match,
            'record_count': record_count,
            'approval_status': approval
        })

    # Create DataFrame
    df_mappings = pd.DataFrame(mappings)

    # 4. Generate summary statistics
    summary = {
        'generated_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sources': {
            'json': {
                'file': json_path,
                'townland_count': len(json_townlands),
                'description': 'GeoJSON file containing map boundaries for townlands',
                'data_content': 'TL_ENGLISH field contains official townland names'
            },
            'csv_aggregated': {
                'file': csv_path,
                'townland_count': len(csv_townlands),
                'description': 'Aggregated records CSV (source for Map Explorer dropdown)',
                'data_content': 'Contains townland_clean field with normalized townland names from tenancies, evictions, emigration, workhouse records'
            },
            'census': {
                'file': census_path,
                'townland_count': len(census_townlands),
                'description': 'Unified census data CSV (source for Census page)',
                'data_content': 'Contains Townland field with official census townland names'
            },
            'tenancies': {
                'file': tenancies_path,
                'description': 'Original tenancy records',
                'data_content': 'Original source for some townland data'
            },
            'wicklow_census': {
                'file': wicklow_census_path,
                'description': 'Original Wicklow census data',
                'data_content': 'Original source for census townland data'
            }
        },
        'matches': {
            'in_all_three': len(df_mappings[(df_mappings['in_json'] == 'YES') & 
                                            (df_mappings['in_csv'] == 'YES') & 
                                            (df_mappings['in_census'] == 'YES')]),
            'in_json_and_csv': len(df_mappings[(df_mappings['in_json'] == 'YES') & 
                                              (df_mappings['in_csv'] == 'YES')]),
            'in_csv_only': len(df_mappings[(df_mappings['in_csv'] == 'YES') & 
                                          (df_mappings['in_json'] == 'NO') & 
                                          (df_mappings['in_census'] == 'NO')]),
            'in_json_only': len(df_mappings[(df_mappings['in_json'] == 'YES') & 
                                           (df_mappings['in_csv'] == 'NO')]),
            'csv_json_exact_match': len(df_mappings[df_mappings['csv_to_json_match'] == 'EXACT']),
            'csv_json_fuzzy_match': len(df_mappings[df_mappings['csv_to_json_match'].str.startswith('FUZZY')]),
            'csv_census_exact_match': len(df_mappings[df_mappings['csv_to_census_match'] == 'EXACT'])
        },
        'approval_summary': {
            'approved': len(df_mappings[df_mappings['approval_status'] == 'APPROVED']),
            'pending_review': len(df_mappings[df_mappings['approval_status'] == 'PENDING_REVIEW']),
            'needs_review': len(df_mappings[df_mappings['approval_status'] == 'NEEDS_REVIEW'])
        },
        'total_records': {
            'in_csv': len(df_csv),
            'mapped_to_json': df_mappings[df_mappings['in_json'] == 'YES']['record_count'].sum()
        }
    }

    # 5. Save detailed report
    report_file = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/townland_mapping_detailed_report.csv'
    df_mappings.to_csv(report_file, index=False)

    # 6. Save summary JSON
    summary_file = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data/townland_mapping_summary.json'
    
    # Convert numpy types to Python native types for JSON serialization
    summary['matches'] = {k: int(v) for k, v in summary['matches'].items()}
    summary['approval_summary'] = {k: int(v) for k, v in summary['approval_summary'].items()}
    summary['total_records'] = {k: int(v) for k, v in summary['total_records'].items()}
    
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)

    # 7. Create unmatched lists
    unmatched_json = df_mappings[df_mappings['in_json'] == 'YES']['normalized_name'].tolist()
    unmatched_csv = df_mappings[df_mappings['in_csv'] == 'YES']['normalized_name'].tolist()

    # 8. Print comprehensive summary
    print("\n" + "="*70)
    print("TOWNLAND MAPPING DETAILED REPORT")
    print("="*70)
    print(f"Generated: {summary['generated_date']}")
    print()
    print("DATA SOURCES:")
    print("-"*70)
    for source, info in summary['sources'].items():
        print(f"\n{source.upper()}:")
        print(f"  File: {info['file']}")
        if 'townland_count' in info:
            print(f"  Townlands: {info['townland_count']}")
        print(f"  Description: {info['description']}")
        print(f"  Data Content: {info['data_content']}")
    print()
    print("MATCHING STATISTICS:")
    print("-"*70)
    print(f"  Townlands in all three datasets: {summary['matches']['in_all_three']}")
    print(f"  Townlands in JSON + CSV: {summary['matches']['in_json_and_csv']}")
    print(f"  Townlands in CSV only: {summary['matches']['in_csv_only']}")
    print(f"  Townlands in JSON only: {summary['matches']['in_json_only']}")
    print()
    print(f"  CSV to JSON exact matches: {summary['matches']['csv_json_exact_match']}")
    print(f"  CSV to JSON fuzzy matches: {summary['matches']['csv_json_fuzzy_match']}")
    print(f"  CSV to Census exact matches: {summary['matches']['csv_census_exact_match']}")
    print()
    print("APPROVAL STATUS:")
    print("-"*70)
    print(f"  APPROVED: {summary['approval_summary']['approved']}")
    print(f"  PENDING_REVIEW: {summary['approval_summary']['pending_review']}")
    print(f"  NEEDS_REVIEW: {summary['approval_summary']['needs_review']}")
    print()
    print("RECORD STATISTICS:")
    print("-"*70)
    print(f"  Total records in CSV: {summary['total_records']['in_csv']:,}")
    print(f"  Records mapped to JSON: {summary['total_records']['mapped_to_json']:,}")
    print()
    print("OUTPUT FILES:")
    print("-"*70)
    print(f"  Detailed mapping: {report_file}")
    print(f"  Summary statistics: {summary_file}")
    print()
    print("="*70)

    # 9. Show sample of mappings
    print("\nSAMPLE MAPPINGS (20 rows):")
    print("="*70)
    sample_cols = ['normalized_name', 'json_townland', 'csv_townland', 'census_townland', 
                   'csv_to_json_match', 'csv_to_census_match', 'record_count', 'approval_status']
    print(df_mappings[sample_cols].head(20).to_string(index=False))
    print()

    # 10. Show unmatched items
    print("\nUNMATCHED ITEMS:")
    print("-"*70)
    
    # Unmatched JSON
    unmatched_json_list = df_mappings[(df_mappings['in_json'] == 'YES') & 
                                      (df_mappings['in_csv'] == 'NO')]['json_townland'].tolist()
    if unmatched_json_list:
        print(f"\nUnmatched JSON townlands ({len(unmatched_json_list)}):")
        print(", ".join(unmatched_json_list[:20]) + ("..." if len(unmatched_json_list) > 20 else ""))

    # Unmatched Census
    unmatched_census_list = df_mappings[(df_mappings['in_census'] == 'YES') & 
                                        (df_mappings['in_csv'] == 'NO')]['census_townland'].tolist()
    if unmatched_census_list:
        print(f"\nUnmatched Census townlands ({len(unmatched_census_list)}):")
        print(", ".join(unmatched_census_list[:20]) + ("..." if len(unmatched_census_list) > 20 else ""))

    # Show need review items
    print("\nNEEDS REVIEW ITEMS:")
    print("-"*70)
    needs_review = df_mappings[df_mappings['approval_status'] == 'NEEDS_REVIEW']
    print(f"Total needs review: {len(needs_review)}")
    if len(needs_review) > 0:
        print("\nSample needs review:")
        print(needs_review[sample_cols].head(10).to_string(index=False))

    return df_mappings, summary


if __name__ == "__main__":
    analyze_and_report()
