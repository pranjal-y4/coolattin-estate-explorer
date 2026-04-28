# Coolattin Data Integration Readme (Updated after 20/02/2026 Meeting)

This document explains how the **unified dataset**, **workhouse dataset**, and **census dataset** are connected, and what was changed based on the meeting actions.

## 1) Data Sources in Use

- `coolattin/static/data/Coolattin unified database.xlsx`
  - Primary source of truth for person, tenancy, emigration, and eviction-related fields.
- `coolattin/static/data/unified_processed.csv`
  - Cleaned/standardized output generated from the unified workbook.
- `coolattin/static/data/workhouse_data_final.xlsx`
  - Workhouse records from **two sheets**:
    - `1-127`
    - `from 128`
- `coolattin/static/data/wicklow-census-data.csv`
  - Census totals by townland and year.
- `coolattin/static/data/townlands.json`
  - Townland polygons for map rendering.

## 2) How Unified Data Is Processed

Processing script:
- `coolattin/services/preprocess.py`

What it does:
- Loads `Coolattin unified database.xlsx`.
- Normalizes fields (names, townland, dates, casing).
- Standardizes `year` to integer (`Int64`) to remove values like `1843.0`.
- Creates canonical fields used in UI (`record_id`, `townland`, `role`, `household_list`, etc.).
- Creates source indicators:
  - `has_tenancy_record`
  - `has_emigration_record`
  - `has_eviction_record`
- Removes noisy collision columns (`surname_2`, `forename_2`, etc.) and uses explicit original-name aliases:
  - `chief_tenant_surname_original`
  - `chief_tenant_forename_original`
  - `under_tenant_surname_original`
  - `under_tenant_forename_original`

Output:
- `coolattin/static/data/unified_processed.csv`

## 3) How Workhouse Data Is Connected

Backend integration in:
- `coolattin/app.py`

Implementation:
- Both sheets from `workhouse_data_final.xlsx` are loaded and normalized.
- Name-based matching is performed against unified records.
- Electoral division is used as a location-strengthener where possible (matched against normalized place context).
- For each unified person, the API exposes:
  - `has_workhouse_record`
  - `workhouse_record_count`
- Detailed matches endpoint:
  - `GET /api/workhouse/match/<record_id>`

UI behavior:
- In person detailed modal, a **Workhouse Records** section appears when matches exist.
- Includes fields like source sheet, raw name, electoral division, status, employment, religion, admitted/left dates, etc.
- Includes caution text: records are potential relevant matches and require manual verification.

## 4) How Census Data Is Used

New route and UI:
- Route: `GET /census`
- Template: `coolattin/templates/census.html`
- Frontend: `coolattin/static/js/census.js`

Backend endpoints:
- `GET /api/census/townlands`
- `GET /api/census/records`
- `GET /api/census/summary`

Behavior:
- Census data is reshaped to long format by year (1841, 1851, 1861, 1871, 1881, 1891).
- Map is colored by selected year’s population.
- Clicking a townland shows detail panel (male/female/inhabited/uninhabited/total) plus year timeline.
- A year slider updates map and detail values interactively.

## 5) Changes Implemented from Meeting Minutes (20/02/2026)

### 1. Data Cleaning – Year Field
- Year values standardized to integer format.
- Decimal year displays removed.

### 2. Column Naming and Field Standardisation
- UI label rendering uses consistent title-case display.
- Internal noisy columns removed from display path.
- Canonical fields used for UI.

### 3. Popup Content Duplication
- Duplicate year display removed from popups.
- Fields displayed once per record card.

### 4. Tenants Section Structure
- Tenants split hierarchically into:
  - Chief Tenant
  - Under Tenant (nested)
- Modal width expanded for easier exploration.

### 5. Unified Data Integration
- Unified dataset is primary source for app views.
- Fuzzy-linked outputs are not used in current display flows.

### 6. Family Links in Household Members
- Household/family member links are clickable in detailed modal.

### 7. Surname Search and Selection
- Free-text surname input enabled.
- Dynamic suggestions added.
- Suggestions now respect selected townland context.
- Invalid manual surname input shows error and asks user to select from suggestions.

### 8. Column Hover Information
- Hover tooltips added for displayed column headers/labels.
- Full tooltip mapping now populated.

### 9. “Mountains in Common” Clarification
- `mountains_in_common` explicitly displayed.
- Shown consistently in detail view with tooltip explanation.

## 6) Additional UX Adjustments

- Evictions are not a separate top-level card in townland panel.
- Eviction context appears as source tags within tenant/family records.
- Townland value is emphasized (bold) in detailed record cards.
- Navbar updated:
  - **Census** added
  - **Analytics** removed

## 7) Key API Endpoints (Current)

- Unified:
  - `/api/unified/records`
  - `/api/unified/stats`
  - `/api/unified/townlands`
  - `/api/unified/surname-suggest`
- Workhouse:
  - `/api/workhouse/match/<record_id>`
- Census:
  - `/api/census/townlands`
  - `/api/census/records`
  - `/api/census/summary`

## 8) Run / Regenerate

Regenerate processed unified data:

```bash
python3.13 coolattin/services/preprocess.py
```

Run app:

```bash
python3.13 -m coolattin.app
```
