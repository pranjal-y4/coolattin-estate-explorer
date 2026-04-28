# Unified Database Analysis vs Fuzzy Matching Approach

Generated: 2026-02-25 17:16:07

## 1. Overview

- **Unified database**: 13,707 records (contains ALL source data)
- **Fuzzy matches**: 8,975 links identified
- **Separate datasets**: Tenancies (4,695), Emigrations (6,018), Evictions (4,695)

## 2. Fuzzy Matching Analysis

- Total fuzzy match links: **8,975**
- Score = 100 (exact): **4,961** (55.3%)
- Score 85-99 (fuzzy): **4,014**

- Matches across different datasets: **3,953**
- Matches within same dataset: **5,022**

## 3. Unified Database Analysis

- Unique person combinations (surname + forename): **5,969**
- People appearing 2+ times: **1,977**
- Total duplicate records: **9,715**

**Top 10 Most Common Names:**
  - |: 1598 records
  - Byrne|James: 121 records
  - Byrne|John: 103 records
  - Byrne|Mary: 82 records
  - Byrne|Michael: 75 records
  - Byrne|Pat: 65 records
  - Byrne|Thomas: 64 records
  - Taylor|Edward: 53 records
  - Byrne|William: 37 records
  - Doyle|Thomas: 35 records

## 4. Issues with Fuzzy Matching Approach


**Accuracy Check (200 sample fuzzy matches):**
- Same person (confirmed): **153** (76.5%)
- Different person: **47** (23.5%)

**Problems identified:**
1. **False positives**: Fuzzy matching links different people with similar names
2. **Limited context**: Doesn't consider year, townland, or other attributes
3. **Manual verification needed**: Many matches require human review
4. **Redundant with unified**: The unified database already contains all records

## 5. Recommended Approach


### Option A: Use Unified Database Only (Recommended)
**Advantages:**
- Single source of truth - no confusion from multiple datasets
- All data is already consolidated
- Reduces errors from fuzzy matching
- Easier to maintain and update

**Implementation:**
1. Clean and standardize the unified database (names, dates, townlands)
2. Use person_key (surname + forename) for searching
3. Add year and townland filters for more precise queries
4. Build analytics directly on unified database

### Option B: Enhanced Matching with Validation
If you still want to link records across time/place:
1. Use strict matching criteria: same surname + forename + townland + year
2. Add confidence scores based on additional attributes
3. Store links in a separate table with validation status
4. Allow manual verification for uncertain matches

### Option C: Hybrid Approach
1. Keep unified database as primary
2. Create a "person_clusters" table that groups related records
3. Use rule-based matching (not fuzzy) with exact criteria
4. Show related records in the UI for family/household research
