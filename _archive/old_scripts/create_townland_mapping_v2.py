#!/usr/bin/env python3

import json
import pandas as pd
from fuzzywuzzy import fuzz
from datetime import datetime
import glob
import os

def extract_townlands_from_csv(file_path, townland_columns=None):
    try:
        df = pd.read_csv(file_path)
        if townland_columns is None:
            townland_columns = [col for col in df.columns if 'townland' in col.lower()]
        
        townlands = set()
        for col in townland_columns:
            if col in df.columns:
                townlands.update([str(t).strip() for t in df[col].unique() if pd.notna(t)])
        
        return townlands, len(df)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return set(), 0

def create_mapping_template():
    template = {
        'mapping_template': {
            'description': 'Use this template to manually map townlands between datasets',
            'columns': [
                'source_file',
                'source_townland',
                'source_townland_upper',
                'target_townland',
                'match_score',
                'exact_match',
                'notes',
                'approved_by',
                'approval_date'
            ]
        }
    }
    return template

def create_blank_mapping_file(output_path):
    columns = [
        'source_file',
        'source_townland',
        'source_townland_upper',
        'target_townland',
        'match_score',
        'exact_match',
        'notes',
        'approved_by',
        'approval_date'
    ]
    df = pd.DataFrame(columns=columns)
    df.to_csv(output_path, index=False)
    return output_path

def main():
    data_dir = '/Users/pranjal/Desktop/Masters/Dissertation/Coolattin-app/coolattin/static/data'
    
    csv_files = {
        'unified_census.csv': ['Townland'],
        'aggregated_records.csv': ['townland_clean', 'townland_norm'],
        'tenancies.csv': ['townland', 'townland_clean'],
        'wicklow-census-data.csv': ['Townland', 'Townland_Name'],
        'workhouse_data_final.xlsx': ['Townland'],
    }
    
    geojson_path = os.path.join(data_dir, 'townlands.json')
    with open(geojson_path, 'r') as f:
        geojson_data = json.load(f)
    
    geojson_townlands = [f['properties']['TL_ENGLISH'].strip().upper() 
                         for f in geojson_data['features'] 
                         if f['properties'].get('TL_ENGLISH')]
    
    print("="*80)
    print("TOWNLAND MAPPING SYSTEM - COMPLETE DATASET EXTRACTION")
    print("="*80)
    print(f"\nGeoJSON file: {geojson_path}")
    print(f"Total townlands in GeoJSON: {len(geojson_townlands)}")
    print(f"\nGeoJSON townlands (first 20): {geojson_townlands[:20]}")
    print()
    
    all_mappings = []
    all_townlands_by_source = {}
    
    for csv_file, townland_cols in csv_files.items():
        csv_path = os.path.join(data_dir, csv_file)
        if not os.path.exists(csv_path):
            print(f"File not found: {csv_path}")
            continue
        
        townlands, record_count = extract_townlands_from_csv(csv_path, townland_cols)
        all_townlands_by_source[csv_file] = {
            'townlands': townlands,
            'record_count': record_count,
            'columns_used': townland_cols
        }
        
        print(f"File: {csv_file}")
        print(f"  Records: {record_count:,}")
        print(f"  Unique townlands: {len(townlands)}")
        print(f"  Columns used: {townland_cols}")
        
        for townland in townlands:
            townland_upper = townland.upper()
            
            if townland_upper in geojson_townlands:
                match_type = 'EXACT'
                match_score = 100
                matched_geojson = townland
            else:
                best_score = 0
                best_match = None
                for geo_town in geojson_townlands:
                    score = fuzz.ratio(townland_upper, geo_town)
                    if score > best_score and score >= 80:
                        best_score = score
                        best_match = geo_town
                
                if best_match:
                    match_type = 'FUZZY'
                    match_score = best_score
                    matched_geojson = best_match
                else:
                    match_type = 'NO_MATCH'
                    match_score = 0
                    matched_geojson = ''
            
            if match_type == 'EXACT':
                approval = 'APPROVED'
            elif match_type == 'FUZZY':
                approval = 'PENDING'
            else:
                approval = 'NEEDS_REVIEW'
            
            all_mappings.append({
                'source_file': csv_file,
                'source_townland': townland,
                'source_townland_upper': townland_upper,
                'geojson_townland': matched_geojson,
                'match_type': match_type,
                'match_score': match_score,
                'record_count': all_townlands_by_source[csv_file]['record_count'],
                'approval_status': approval
            })
        
        print(f"  APPROVED: {len([m for m in all_mappings if m['source_file'] == csv_file and m['approval_status'] == 'APPROVED'])}")
        print(f"  PENDING: {len([m for m in all_mappings if m['source_file'] == csv_file and m['approval_status'] == 'PENDING'])}")
        print(f"  NEEDS_REVIEW: {len([m for m in all_mappings if m['source_file'] == csv_file and m['approval_status'] == 'NEEDS_REVIEW'])}")
        print()
    
    df_mappings = pd.DataFrame(all_mappings)
    
    unique_townlands = set()
    for source_data in all_townlands_by_source.values():
        unique_townlands.update(source_data['townlands'])
    
    consolidated_mappings = []
    for townland in unique_townlands:
        townland_upper = townland.upper()
        sources = [f for f, data in all_townlands_by_source.items() 
                   if townland in data['townlands']]
        
        if townland_upper in geojson_townlands:
            match_type = 'EXACT'
            match_score = 100
            matched_geojson = townland
            approval = 'APPROVED'
        else:
            best_score = 0
            best_match = None
            for geo_town in geojson_townlands:
                score = fuzz.ratio(townland_upper, geo_town)
                if score > best_score and score >= 80:
                    best_score = score
                    best_match = geo_town
            
            if best_match:
                match_type = 'FUZZY'
                match_score = best_score
                matched_geojson = best_match
                approval = 'PENDING'
            else:
                match_type = 'NO_MATCH'
                match_score = 0
                matched_geojson = ''
                approval = 'NEEDS_REVIEW'
        
        consolidated_mappings.append({
            'consolidated_townland': townland,
            'consolidated_townland_upper': townland_upper,
            'sources': ', '.join(sources),
            'geojson_townland': matched_geojson,
            'match_type': match_type,
            'match_score': match_score,
            'approval_status': approval
        })
    
    df_consolidated = pd.DataFrame(consolidated_mappings)
    
    blank_template_path = os.path.join(data_dir, 'townland_mapping_blank_template.csv')
    create_blank_mapping_file(blank_template_path)
    
    detailed_output = os.path.join(data_dir, 'townland_mapping_detailed_all_sources.csv')
    df_mappings.to_csv(detailed_output, index=False)
    
    consolidated_output = os.path.join(data_dir, 'townland_mapping_consolidated.csv')
    df_consolidated.to_csv(consolidated_output, index=False)
    
    summary = {
        'generated_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'geojson_file': geojson_path,
        'geojson_townland_count': len(geojson_townlands),
        'source_files': {
            csv_file: {
                'record_count': data['record_count'],
                'townland_count': len(data['townlands']),
                'columns_used': data['columns_used']
            }
            for csv_file, data in all_townlands_by_source.items()
        },
        'consolidated_summary': {
            'total_unique_townlands': len(unique_townlands),
            'exact_matches': len(df_consolidated[df_consolidated['approval_status'] == 'APPROVED']),
            'fuzzy_matches': len(df_consolidated[df_consolidated['approval_status'] == 'PENDING']),
            'unmatched': len(df_consolidated[df_consolidated['approval_status'] == 'NEEDS_REVIEW'])
        }
    }
    
    summary_path = os.path.join(data_dir, 'townland_mapping_summary.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print("="*80)
    print("MAPPING SUMMARY")
    print("="*80)
    print(f"\nGeoJSON townlands: {len(geojson_townlands)}")
    print(f"\nSource files processed:")
    for csv_file, data in all_townlands_by_source.items():
        print(f"  - {csv_file}: {len(data['townlands'])} townlands")
    
    print(f"\nConsolidated view:")
    print(f"  Total unique townlands: {len(unique_townlands)}")
    print(f"  APPROVED (exact match): {summary['consolidated_summary']['exact_matches']}")
    print(f"  PENDING (fuzzy match): {summary['consolidated_summary']['fuzzy_matches']}")
    print(f"  NEEDS_REVIEW (no match): {summary['consolidated_summary']['unmatched']}")
    
    print(f"\nOutput files created:")
    print(f"  1. {detailed_output}")
    print(f"  2. {consolidated_output}")
    print(f"  3. {blank_template_path}")
    print(f"  4. {summary_path}")
    
    print("\n" + "="*80)
    print("SAMPLE CONSOLIDATED MAPPINGS (20 rows):")
    print("="*80)
    print(df_consolidated[['consolidated_townland', 'sources', 'geojson_townland', 
                          'match_type', 'match_score', 'approval_status']].head(20).to_string(index=False))
    
    print("\n" + "="*80)
    print("UNMATCHED TOWNLANDS (NEEDS_REVIEW):")
    print("="*80)
    unmatched = df_consolidated[df_consolidated['approval_status'] == 'NEEDS_REVIEW']
    if len(unmatched) > 0:
        print(unmatched[['consolidated_townland', 'sources']].to_string(index=False))
    else:
        print("All townlands matched!")
    
    return df_mappings, df_consolidated, summary

if __name__ == "__main__":
    main()
