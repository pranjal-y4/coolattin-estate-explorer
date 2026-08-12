#!/usr/bin/env python3

import csv
import json
import os

EXTRA = "extra_datasets"
OUT = "frontend/static/data"

IRELAND_LAT = (51.2, 55.5)
IRELAND_LNG = (-10.7, -5.3)


def valid_irish_coord(lat_str, lng_str):
    try:
        lat = float(lat_str)
        lng = float(lng_str)
    except (ValueError, TypeError):
        return False
    if lat == 0.0 and lng == 0.0:
        return False
    return IRELAND_LAT[0] <= lat <= IRELAND_LAT[1] and IRELAND_LNG[0] <= lng <= IRELAND_LNG[1]


def csv_to_geojson_wicklow(csv_path, lat_field, lng_field, county_field, properties_map):
    features = []
    skipped_county = 0
    skipped_coord = 0

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            county = row.get(county_field, "").strip().upper()
            if county != "WICKLOW":
                skipped_county += 1
                continue

            lat_s = row.get(lat_field, "").strip()
            lng_s = row.get(lng_field, "").strip()
            if not valid_irish_coord(lat_s, lng_s):
                skipped_coord += 1
                continue

            props = {out_k: row.get(src_k, "").strip() for out_k, src_k in properties_map.items()}
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(lng_s), float(lat_s)]},
                "properties": props,
            })

    print(f"  kept={len(features)}, skipped_county={skipped_county}, skipped_bad_coord={skipped_coord}")
    return {"type": "FeatureCollection", "features": features}


def main():
    os.makedirs(OUT, exist_ok=True)

    print("Processing ASI …")
    asi = csv_to_geojson_wicklow(
        f"{EXTRA}/NMS_OpenData_20230823.csv",
        lat_field="LATITUDE",
        lng_field="LONGITUDE",
        county_field="COUNTY",
        properties_map={
            "id":               "ENTITY_ID",
            "smrs":             "SMRS",
            "county":           "COUNTY",
            "townland":         "TOWNLAND",
            "class_code":       "CLASS_CODE",
            "monument_class":   "MONUMENT_CLASS",
            "first_edition":    "FIRST_EDITION",
            "latest_edition":   "LATEST_EDITION",
            "source_link":      "WEBSITE_LINK",
            "notes":            "WEB_NOTES",
        },
    )
    with open(f"{OUT}/asi_wicklow.geojson", "w", encoding="utf-8") as f:
        json.dump(asi, f, separators=(",", ":"))
    print(f"  → {OUT}/asi_wicklow.geojson ({len(asi['features'])} features)\n")

    print("Processing Holy Wells …")
    hw = csv_to_geojson_wicklow(
        f"{EXTRA}/NMS_OpenData_20230420_HolyWell_csv.csv",
        lat_field="LATITUDE",
        lng_field="LONGITUDE",
        county_field="COUNTY",
        properties_map={
            "id":               "ENTITY_ID",
            "smrs":             "SMRS",
            "county":           "COUNTY",
            "townland":         "TOWNLAND",
            "class_code":       "CLASS_CODE",
            "monument_type":    "MONUMENT_T",
            "first_edit":       "FIRST_EDIT",
            "latest_edit":      "LATEST_EDI",
            "source_link":      "WEBSITE_LI",
            "notes":            "WEB_NOTES",
        },
    )
    with open(f"{OUT}/holywells_wicklow.geojson", "w", encoding="utf-8") as f:
        json.dump(hw, f, separators=(",", ":"))
    print(f"  → {OUT}/holywells_wicklow.geojson ({len(hw['features'])} features)\n")

    print("Processing Monuments to Visit …")
    mtv = csv_to_geojson_wicklow(
        f"{EXTRA}/NMSMonumentsToVisit.csv",
        lat_field="LATITUDE",
        lng_field="LONGITUDE",
        county_field="COUNTY",
        properties_map={
            "id":               "ENTITY_ID",
            "smrs":             "SMRS",
            "name":             "NAME",
            "county":           "COUNTY",
            "locality":         "LOCALITY",
            "monument_class":   "MONUMENT_CLASS",
            "source_link":      "WEBSITE_LINK",
            "external_link":    "external_link",
            "notes":            "MonumentsToVisit_INFO",
        },
    )
    with open(f"{OUT}/monuments_wicklow.geojson", "w", encoding="utf-8") as f:
        json.dump(mtv, f, separators=(",", ":"))
    print(f"  → {OUT}/monuments_wicklow.geojson ({len(mtv['features'])} features)\n")

    print("Done. All Wicklow GeoJSON files written to", OUT)


if __name__ == "__main__":
    main()
