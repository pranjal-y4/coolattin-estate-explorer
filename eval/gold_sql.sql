-- eval/gold_sql.sql
-- Hand-verified gold SQL for the Coolattin Ask pipeline evaluation gold set.
-- All queries verified against coolattin.db on 2026-06-09.
-- Headline aggregates: emigration=6016 | eviction_unique=4108 | tenancy=5247
--                       total_records=13707 | clearances_sum=7763
--
-- Catalogue codes:
--   A       = Analytical aggregate (count/sum, single table or simple join)
--   A-trend = Time-series / per-year breakdown
--   R       = Relational / hierarchy / sensemaking
--   C       = Comparative (across groups within the DB)
--   H       = Heritage (monument, holy well, ring fort)
--   I       = Identity / entity resolution
--   G       = General / out-of-scope (no verifiable SQL — honest refusal expected)
--   X       = Cross-source (SQLite vs VRTI KG agreement check)
--   P       = People / persons / surname queries

-- ===========================================================================
-- A  EMIGRATION
-- ===========================================================================

-- emi_01_total  [A]
-- Q: How many people emigrated from the Coolattin estate?
-- Expected: 6016
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1;

-- emi_02_townland_ballynultagh  [A]
-- Q: How many people emigrated from Ballynultagh?
-- Expected: 400
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'BALLYNULTAGH';

-- emi_03_townland_killinure  [A]
-- Q: How many people emigrated from Killinure?
-- Expected: 294
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'KILLINURE';

-- emi_04_per_year_trend  [A-trend]
-- Q: Show emigration broken down by year
-- Expected: time series; year 1847 appears with count 2211
SELECT year, COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND year IS NOT NULL
GROUP BY year
ORDER BY year;

-- emi_05_canada_total  [A-trend]
-- Q: How many people emigrated to Canada?
-- Expected: peak year 1848 (787); time-series result
SELECT year, COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND is_canada_destination = 1
  AND year IS NOT NULL
GROUP BY year
ORDER BY year;

-- emi_06_canada_ship  [A]
-- Q: Which ship carried the most Coolattin families to Canada?
-- Expected: Glenlyon (324 records)
SELECT ship_name, COUNT(DISTINCT record_id) AS families
FROM unified_record
WHERE has_emigration_record = 1
  AND is_canada_destination = 1
  AND ship_name IS NOT NULL
  AND TRIM(ship_name) != ''
GROUP BY ship_name
ORDER BY families DESC
LIMIT 1;

-- emi_07_ships_list  [A]
-- Q: List the ships used for emigration from the estate
-- Expected: 27 distinct ship names including Glenlyon, Star, Jessie
SELECT DISTINCT ship_name
FROM unified_record
WHERE ship_name IS NOT NULL
  AND TRIM(ship_name) != ''
ORDER BY ship_name;

-- emi_08_in_1848  [A]
-- Q: How many people emigrated in 1848?
-- Expected: 1290
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND year = 1848;

-- ===========================================================================
-- A  EVICTIONS
-- ===========================================================================

-- evic_01_total  [A]
-- Q: How many evictions were recorded in total?
-- Expected: 7763 (sum of clearances_record.count)
SELECT SUM(count) AS total_evictions
FROM clearances_record;

-- evic_02_worst_year  [A]
-- Q: Which year had the most evictions?
-- Expected: 1847 (2681)
SELECT year, SUM(count) AS n
FROM clearances_record
GROUP BY year
ORDER BY n DESC
LIMIT 1;

-- evic_03_townland_ballinacor  [A]
-- Q: How many evictions happened in Ballinacor?
-- Expected: 122 total across all years
SELECT SUM(c.count) AS total_evictions
FROM clearances_record c
JOIN townland t ON c.townland_id = t.id
WHERE UPPER(t.name) = 'BALLINACOR';

-- evic_04_per_year  [A-trend]
-- Q: Show evictions per year
-- Expected: time series 1847–1856
SELECT year, SUM(count) AS n
FROM clearances_record
GROUP BY year
ORDER BY year;

-- evic_05_people_list  [P]
-- Q: List the people who were evicted
SELECT record_id, surname, forename, townland_norm, year
FROM unified_record
WHERE has_eviction_record = 1
ORDER BY surname, forename
LIMIT 100;

-- evic_06_in_1849  [A]
-- Q: How many evictions happened in 1849?
-- Expected: 1016
SELECT SUM(count) AS n
FROM clearances_record
WHERE year = 1849;

-- ===========================================================================
-- A  CENSUS / POPULATION
-- ===========================================================================

-- cen_01_estate_1841  [A]
-- Q: What was the total population of the estate in 1841?
-- Expected: 119300
SELECT SUM(total) AS population
FROM census_record
WHERE year = 1841;

-- cen_02_estate_1851  [A]
-- Q: What was the estate population in 1851?
-- Expected: 91860
SELECT SUM(total) AS population
FROM census_record
WHERE year = 1851;

-- cen_03_ballinacor_1841  [A]
-- Q: What was the population of Ballinacor in 1841?
-- Expected: 55
SELECT SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE UPPER(t.name) = 'BALLINACOR'
  AND c.year = 1841;

-- cen_04_famine_decline  [A-trend]
-- Q: How did the population decline from 1841 to 1851?
-- Expected: 119300 → 91860 (−27440, −23.0%)
SELECT year, SUM(total) AS population
FROM census_record
WHERE year IN (1841, 1851)
GROUP BY year
ORDER BY year;

-- cen_05_trend_1841_1861  [A-trend]
-- Q: What was the population trend from 1841 to 1861?
-- Expected: time series; 1841=119300, 1861=81429
SELECT c.year, SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE c.year BETWEEN 1841 AND 1861
GROUP BY c.year
ORDER BY c.year;

-- cen_06_uninhabited  [A]
-- Q: How many uninhabited houses were recorded?
SELECT SUM(uninhabited) AS uninhabited_houses
FROM census_record
WHERE uninhabited IS NOT NULL;

-- cen_07_all_years  [A-trend]
-- Q: Show the estate population across all census years
-- Expected: time series across 1827–1891
SELECT c.year, SUM(c.total) AS population
FROM census_record c
GROUP BY c.year
ORDER BY c.year;

-- cen_08_by_parish  [A]
-- Q: Show population breakdown by parish
SELECT t.civil_parish, SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE t.civil_parish IS NOT NULL
  AND TRIM(t.civil_parish) != ''
  AND c.year = 1841
GROUP BY t.civil_parish
ORDER BY population DESC;

-- ===========================================================================
-- A / R  GEOGRAPHY
-- ===========================================================================

-- geo_01_total_townlands  [A]
-- Q: How many townlands are there in the estate?
-- Expected: 4225
SELECT COUNT(*) AS townland_count
FROM townland;

-- geo_02_parish_count  [A]
-- Q: How many civil parishes are there?
-- Expected: 22
SELECT COUNT(DISTINCT civil_parish) AS parish_count
FROM townland
WHERE civil_parish IS NOT NULL
  AND TRIM(civil_parish) != '';

-- geo_03_parish_list  [A]
-- Q: List all civil parishes in the estate
SELECT DISTINCT civil_parish
FROM townland
WHERE civil_parish IS NOT NULL
  AND TRIM(civil_parish) != ''
ORDER BY civil_parish;

-- geo_04_ballinacor_parish  [R]
-- Q: Which parish is Ballinacor in?
-- Expected (SQLite): Kilbride  [KG: Ballinacor — known discrepancy]
SELECT civil_parish
FROM townland
WHERE UPPER(name) = 'BALLINACOR'
LIMIT 1;

-- geo_05_baronies  [A]
-- Q: What baronies are in the estate?
-- Expected: 11 distinct baronies including Shillelagh, Arklow, Ballinacor South
SELECT DISTINCT barony
FROM townland
WHERE barony IS NOT NULL
ORDER BY barony;

-- geo_06_nearby_coolattin  [R]
-- Q: Show me townlands near Coolattin
-- Note: spatial proximity requires lat/lon — this query returns same-parish fallback
SELECT t2.name, t2.civil_parish, t2.barony
FROM townland t1
JOIN townland t2 ON t2.civil_parish = t1.civil_parish AND t2.id != t1.id
WHERE UPPER(t1.name) = 'COOLATTIN'
ORDER BY t2.name
LIMIT 20;

-- geo_07_by_county  [A]
-- Q: How many townlands are in each county?
SELECT county, COUNT(*) AS townland_count
FROM townland
WHERE county IS NOT NULL
GROUP BY county
ORDER BY townland_count DESC;

-- ===========================================================================
-- A / P  PEOPLE
-- ===========================================================================

-- ppl_01_total_records  [A]
-- Q: How many people are in the records?
-- Expected: 13707
SELECT COUNT(DISTINCT record_id) AS total_records
FROM unified_record;

-- ppl_02_byrne_records  [P]
-- Q: How many records mention the surname Byrne?
-- Expected: 1290
SELECT COUNT(DISTINCT record_id) AS person_count
FROM unified_record
WHERE UPPER(surname) = 'BYRNE';

-- ppl_03_murphy_list  [P]
-- Q: List all people named Murphy in the estate records
-- Count: 290
SELECT record_id, forename, surname, townland_norm, year
FROM unified_record
WHERE UPPER(surname) = 'MURPHY'
ORDER BY year, surname, forename;

-- ppl_04_widows_count  [A]
-- Q: How many widows are recorded in the estate records?
-- Expected: 811
SELECT COUNT(DISTINCT record_id) AS widow_count
FROM unified_record
WHERE is_widow = 1;

-- ppl_05_widows_children  [A]
-- Q: What proportion of widows had recorded children?
-- Expected: 233 / 811 = 28.7%
SELECT
    SUM(CASE WHEN children_count > 0 THEN 1 ELSE 0 END) AS widows_with_children,
    COUNT(*) AS total_widows,
    ROUND(100.0 * SUM(CASE WHEN children_count > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct
FROM (
    SELECT DISTINCT record_id, children_count
    FROM unified_record
    WHERE is_widow = 1
) w;

-- ppl_06_heads_of_household  [P]
-- Q: List all heads of household in the estate
SELECT record_id, surname, forename, townland_norm, year
FROM unified_record
WHERE relationship_to_head_of_household IS NOT NULL
  AND (UPPER(relationship_to_head_of_household) = 'HEAD'
       OR relationship_to_head_of_household LIKE '%ead%')
ORDER BY townland_norm, year;

-- ppl_07_ballynultagh_people  [P]
-- Q: List all people recorded in Ballynultagh
SELECT record_id, surname, forename, year, has_emigration_record, has_eviction_record, has_tenancy_record
FROM unified_record
WHERE townland_norm = 'BALLYNULTAGH'
ORDER BY year, surname;

-- ppl_08_in_1847  [A]
-- Q: How many people were recorded in 1847?
SELECT COUNT(DISTINCT record_id) AS n
FROM unified_record
WHERE year = 1847;

-- ===========================================================================
-- A  TENANCY
-- ===========================================================================

-- ten_01_total  [A]
-- Q: How many tenants are recorded?
-- Expected: 5247
SELECT COUNT(DISTINCT record_id) AS tenancy_count
FROM unified_record
WHERE has_tenancy_record = 1;

-- ten_02_gender_avg  [C]
-- Q: What is the average landholding for male versus female tenants?
-- Expected: Male avg=39.49 acres (n=889), Female avg=34.98 acres (n=134)
SELECT gender,
       COUNT(DISTINCT record_id) AS tenants,
       ROUND(AVG(holding_acres), 2) AS avg_holding_acres
FROM unified_record
WHERE has_tenancy_record = 1
  AND gender IN ('M', 'F')
  AND holding_acres IS NOT NULL
GROUP BY gender
ORDER BY gender;

-- ten_03_coolattin_tenants  [P]
-- Q: List tenants from Coolattin
SELECT record_id, surname, forename, year, holding_acres
FROM unified_record
WHERE has_tenancy_record = 1
  AND townland_norm = 'COOLATTIN'
ORDER BY year;

-- ten_04_largest_holdings  [A]
-- Q: Which tenants had the largest landholdings in their latest recorded year?
SELECT u.record_id, u.surname, u.forename, u.townland_norm,
       u.year, u.holding_acres
FROM unified_record u
JOIN (
    SELECT record_id, MAX(year) AS max_year
    FROM unified_record
    WHERE has_tenancy_record = 1 AND holding_acres IS NOT NULL
    GROUP BY record_id
) latest ON u.record_id = latest.record_id AND u.year = latest.max_year
WHERE u.has_tenancy_record = 1
ORDER BY u.holding_acres DESC
LIMIT 20;

-- ten_05_smallest_plots  [A]
-- Q: Which townlands have the smallest tenant plots?
SELECT townland_norm,
       ROUND(AVG(holding_acres), 2) AS avg_acres,
       COUNT(DISTINCT record_id) AS tenants
FROM unified_record
WHERE has_tenancy_record = 1
  AND holding_acres IS NOT NULL
  AND holding_acres > 0
GROUP BY townland_norm
HAVING tenants >= 3
ORDER BY avg_acres ASC
LIMIT 20;

-- ten_06_per_townland  [A]
-- Q: How many tenants are recorded per townland?
SELECT townland_norm, COUNT(DISTINCT record_id) AS tenants
FROM unified_record
WHERE has_tenancy_record = 1
GROUP BY townland_norm
ORDER BY tenants DESC;

-- ===========================================================================
-- H  HERITAGE
-- ===========================================================================

-- her_01_holy_well_population  [H]
-- Q: Are townlands with holy wells more populous than those without?
SELECT
    CASE WHEN h.townland_norm IS NOT NULL THEN 'Has holy well' ELSE 'No holy well' END AS group_label,
    COUNT(DISTINCT t.id) AS townland_count,
    ROUND(AVG(c.total), 0) AS avg_population_1841
FROM townland t
LEFT JOIN heritage_feature h
    ON h.townland_norm = UPPER(t.name) AND h.feature_group = 'holy_well'
LEFT JOIN census_record c ON c.townland_id = t.id AND c.year = 1841
GROUP BY group_label;

-- her_02_ring_fort_population  [H]
-- Q: Are townlands with ring forts more populous than those without?
SELECT
    CASE WHEN h.townland_norm IS NOT NULL THEN 'Has ring fort' ELSE 'No ring fort' END AS group_label,
    COUNT(DISTINCT t.id) AS townland_count,
    ROUND(AVG(c.total), 0) AS avg_population_1841
FROM townland t
LEFT JOIN heritage_feature h
    ON h.townland_norm = UPPER(t.name) AND h.feature_group = 'ring_fort'
LEFT JOIN census_record c ON c.townland_id = t.id AND c.year = 1841
GROUP BY group_label;

-- her_03_holy_well_count  [H]
-- Q: How many holy wells are recorded in the estate?
-- Expected: 68
SELECT COUNT(*) AS holy_well_count
FROM heritage_feature
WHERE feature_group = 'holy_well';

-- her_04_ring_fort_count  [H]
-- Q: How many ring forts are there in the estate?
-- Expected: 298
SELECT COUNT(*) AS ring_fort_count
FROM heritage_feature
WHERE feature_group = 'ring_fort';

-- her_05_holy_well_townlands  [H]
-- Q: Which townlands have holy wells?
-- Expected: 65 distinct townlands
SELECT townland_norm, COUNT(*) AS well_count
FROM heritage_feature
WHERE feature_group = 'holy_well'
GROUP BY townland_norm
ORDER BY townland_norm;

-- ===========================================================================
-- R  OVERVIEW / COMBINED
-- ===========================================================================

-- ov_01_famine_impact  [R]
-- Q: What was the impact of the Great Famine on the estate?
-- Expected: multi-source sensemaking; eviction/emigration facts
SELECT
    'emigration' AS source, COUNT(DISTINCT record_id) AS records, MIN(year) AS year_from, MAX(year) AS year_to
FROM unified_record WHERE has_emigration_record = 1 AND year BETWEEN 1847 AND 1856
UNION ALL
SELECT 'eviction', SUM(count), MIN(year), MAX(year) FROM clearances_record WHERE year BETWEEN 1847 AND 1856
UNION ALL
SELECT 'population_1841', SUM(total), 1841, 1841 FROM census_record WHERE year = 1841
UNION ALL
SELECT 'population_1851', SUM(total), 1851, 1851 FROM census_record WHERE year = 1851;

-- ov_02_estate_summary  [R]
-- Q: Give me an overview of the estate statistics
SELECT 'total_records'      AS metric, COUNT(DISTINCT record_id)                                              AS value FROM unified_record
UNION ALL
SELECT 'emigrations',        COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record = 1
UNION ALL
SELECT 'evictions_unique',   COUNT(DISTINCT record_id) FROM unified_record WHERE has_eviction_record = 1
UNION ALL
SELECT 'tenants',            COUNT(DISTINCT record_id) FROM unified_record WHERE has_tenancy_record = 1
UNION ALL
SELECT 'clearances_sum',     SUM(count) FROM clearances_record
UNION ALL
SELECT 'townlands',          COUNT(*) FROM townland
UNION ALL
SELECT 'civil_parishes',     COUNT(DISTINCT civil_parish) FROM townland WHERE civil_parish IS NOT NULL AND TRIM(civil_parish) != '';

-- ov_03_emi_and_evic  [A]
-- Q: How many people were both evicted and emigrated?
-- Expected: 0 (no overlap in estate records)
SELECT COUNT(DISTINCT record_id) AS n
FROM unified_record
WHERE has_emigration_record = 1
  AND has_eviction_record = 1;

-- ov_04_emi_vs_population  [C]
-- Q: Compare emigration numbers with census population over time
SELECT c.year,
       SUM(c.total)                                                             AS population,
       COUNT(DISTINCT u.record_id)                                              AS emigrations
FROM census_record c
JOIN townland t ON c.townland_id = t.id
LEFT JOIN unified_record u ON u.townland_norm = UPPER(t.name)
    AND u.has_emigration_record = 1
    AND u.year = c.year
GROUP BY c.year
ORDER BY c.year;

-- ov_05_records_per_year  [A-trend]
-- Q: How many records are there per year?
SELECT year, COUNT(DISTINCT record_id) AS records
FROM unified_record
WHERE year IS NOT NULL
GROUP BY year
ORDER BY year;

-- ===========================================================================
-- I  IDENTITY / ENTITY RESOLUTION  (SQL checks only; vector matching not tested here)
-- ===========================================================================

-- er_01_exact_ballinacor  [I]
-- Verify Ballinacor canonical record in townland table
-- Expected: id=355, civil_parish=Kilbride, barony=Arklow
SELECT id, name, civil_parish, barony, county
FROM townland
WHERE UPPER(name) = 'BALLINACOR'
LIMIT 1;

-- er_02_spelling_variant  [I]
-- Ballinacour → should resolve to BALLINACOR (id=355)
-- (Vector/fuzzy resolution tested at runtime; this SQL checks canonical row)
SELECT id, name FROM townland WHERE UPPER(name) = 'BALLINACOR' LIMIT 1;

-- er_03_spelling_ballynultach  [I]
-- Ballynultach → should resolve to BALLYNULTAGH
SELECT id, name FROM townland WHERE UPPER(name) = 'BALLYNULTAGH' LIMIT 1;

-- er_04_coolattin_kg_uri  [I]
-- Verify Coolattin in townland table
SELECT id, name FROM townland WHERE UPPER(name) = 'COOLATTIN' LIMIT 1;

-- er_05_surname_byrne_exact  [I]
-- Q: List all Byrne family members  /  how many Byrne records?
-- Expected: 1290
SELECT COUNT(DISTINCT record_id) AS n
FROM unified_record
WHERE UPPER(surname) = 'BYRNE';

-- er_06_surname_fuzzy  [I]
-- Kavanah → should resolve to KAVANAGH
-- Expected: 148 records
SELECT COUNT(DISTINCT record_id) AS n
FROM unified_record
WHERE UPPER(surname) = 'KAVANAGH';

-- ===========================================================================
-- R  RELATIONAL / HIERARCHY
-- ===========================================================================

-- rel_01_ballinacor_barony  [R]
-- Q: Which barony does Ballinacor belong to?
-- Expected (SQLite): Arklow  [KG: Ballinacor South — known discrepancy]
SELECT barony
FROM townland
WHERE UPPER(name) = 'BALLINACOR'
LIMIT 1;

-- rel_02_ballynultagh_county  [R]
-- Q: What county and barony does Ballynultagh fall within?
-- Expected: county=Wicklow, barony=Shillelagh
SELECT barony, county
FROM townland
WHERE UPPER(name) = 'BALLYNULTAGH'
LIMIT 1;

-- rel_03_ballinacor_parish_siblings  [R]
-- Q: What other townlands are in the same parish as Ballinacor?
-- Expected (SQLite parish): Kilbride
SELECT t2.name, t2.civil_parish, t2.barony
FROM townland t1
JOIN townland t2 ON t2.civil_parish = t1.civil_parish AND t2.id != t1.id
WHERE UPPER(t1.name) = 'BALLINACOR'
ORDER BY t2.name;

-- rel_04_estate_overview  [R]
-- Q: Tell me about the Coolattin estate and its history
-- (Handled by subgraph engine + multi-source retrieval — SQL below verifies core facts)
SELECT id, name, civil_parish, barony, county
FROM townland
WHERE UPPER(name) = 'COOLATTIN'
LIMIT 1;

-- rel_05_historical_monuments  [H]
-- Q: Tell me about the historical monuments in Ballinacor
SELECT feature_group, monument_class, feature_name, source_link
FROM heritage_feature
WHERE townland_norm = 'BALLINACOR'
ORDER BY feature_group;

-- ===========================================================================
-- X  CROSS-SOURCE (SQLite vs VRTI KG)
-- ===========================================================================

-- cmp_01_emigration_vs_kg  [X]
-- Q: Compare the emigration count from Ballynultagh in the estate records vs the KG
-- Expected (SQLite): 400
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'BALLYNULTAGH';

-- cmp_02_population_vs_kg  [X]
-- Q: How does the 1841 population of Ballinacor in estate records compare to VRTI KG?
-- Expected (SQLite): 55
SELECT SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE UPPER(t.name) = 'BALLINACOR'
  AND c.year = 1841;

-- cmp_03_eviction_agree  [X]
-- Q: Compare the eviction total for Ballinacor from the estate records vs the KG
-- Expected (SQLite): 122
SELECT SUM(c.count) AS total_evictions
FROM clearances_record c
JOIN townland t ON c.townland_id = t.id
WHERE UPPER(t.name) = 'BALLINACOR';

-- ===========================================================================
-- G  GENERAL / OUT-OF-SCOPE
-- These questions have no meaningful SQL in the Coolattin DB.
-- Expected behaviour: route to template_miss (honest refusal / LLM fallback).
-- ===========================================================================

-- fbl_01_rent  [G]
-- Q: What was the average rent paid by tenants on the Coolattin estate?
-- Note: rent_owed column exists but no template/metric for average — LLM fallback expected.
-- Proxy SQL (not a verified answer — for reference only):
SELECT ROUND(AVG(rent_owed), 2) AS avg_rent_owed
FROM unified_record
WHERE has_tenancy_record = 1
  AND rent_owed IS NOT NULL AND rent_owed > 0;

-- fbl_02_crops  [G]
-- Q: What crops were typically grown in the Coolattin area during the 1840s?
-- NO SQL — crop data not in the database.

-- fbl_03_fitzwilliam  [G]
-- Q: What was the Fitzwilliam family's approach to managing the Coolattin estate?
-- NO SQL — management records not in the database.

-- gen_01_mortality  [G]
-- Q: How many people died of Famine-related causes on the Coolattin estate?
-- NO SQL — mortality/cause-of-death data not in the database.

-- gen_02_religion  [G]
-- Q: What religion were the Coolattin tenants?
-- NO SQL — religious affiliation not recorded.

-- gen_03_other_estates  [G]
-- Q: How did eviction rates at Coolattin compare to other Irish estates?
-- NO SQL — only Coolattin data is available; cross-estate comparison not possible.

-- gen_04_weather  [G]
-- Q: What was the weather like in County Wicklow during the 1840s?
-- NO SQL — meteorological data not in the database.

-- gen_05_politics  [G]
-- Q: Were any Coolattin tenants involved in political movements during the 1840s?
-- NO SQL — political/organisational records not in the database.
