# Ask Pipeline Eval — Baseline Post-Migration

**Run label:** `baseline_post_migration`  
**Timestamp:** 2026-06-09 21:29:22 UTC  
**Questions run:** 75 (75 total: 70 pre-migration cases + 5 new G-series)  

---

## 1. Global Metrics

| Metric | Value |
|--------|-------|
| Routing accuracy | 89.3% |
| Entity label accuracy | 100.0% |
| SQL-id resolution | 100.0% |
| KG-URI resolution | 100.0% |
| SQL exec success | 100.0% |
| Aggregation correctness | 100.0% |
| Answer facts found rate | 74.5% |
| Honest-refusal rate (G-series) | 0.0% |
| Template hit rate | 100.0% |
| LLM calls required | 0 |
| Lane routing accuracy | 72.0% |
| p50 latency | 329 ms |
| p90 latency | 1909 ms |
| p95 latency | 3854 ms |

---

## 2. Execution Accuracy by Route

| Route | SQL exec success (%) |
|-------|---------------------|
| template | 100.0 |
| semantic_layer (deterministic) | 100.0 |
| verified_analysis | 100.0 |
| template_miss (LLM fallback) | None |

---

## 3. Per-Lane Breakdown

| Lane | N | Key metric |
|------|---|------------|
| Analytical | 50 | agg_acc=100.0% |
| Relational | 12 | subgraph_recall=1.0 |
| Comparative | 5 | sqlite_capture=100.0% / kg_capture=100.0% |
| Fallback / G-series | 8 (8 G) | honest_refusal=0.0% |

---

## 4. Routing Confusion Matrix

| Expected \ Actual | semantic_layer | template | verified_analysis |
|---|---|---|---|
| **llm** | 4 | 2 | 2 |
| **template** | 39 | 13 | 2 |
| **verified_analysis** | 8 | 0 | 5 |

---

## 5. Per-Question Results

| ID | Cat | Code | Expected | Actual | Rt | Ag | Ln | ms |
|----|-----|------|----------|--------|----|----|----|-----|
| emi_01_total | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 218 |
| emi_02_townland_ballynultagh | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 9 |
| emi_03_townland_killinure | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 9 |
| emi_04_per_year_trend | emigrati | A-trend | template | semantic_layer | ✓ | - | ✓ | 522 |
| emi_05_canada_total | emigrati | A-trend | verified | semantic_layer | ✓ | - | ✓ | 167 |
| emi_06_canada_ship | emigrati | A | verified | semantic_layer | ✓ | ✓ | ✓ | 12 |
| emi_07_ships_list | emigrati | A | template | semantic_layer | ✓ | - | ✓ | 1086 |
| emi_08_in_1848 | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 6 |
| evic_01_total | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 491 |
| evic_02_worst_year | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 893 |
| evic_03_townland_ballinacor | eviction | A | template | semantic_layer | ✓ | - | ✓ | 5 |
| evic_04_per_year | eviction | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 414 |
| evic_05_people_list | eviction | P | template | semantic_layer | ✓ | - | ✓ | 7 |
| evic_06_in_1849 | eviction | A | template | semantic_layer | ✓ | - | ✓ | 147 |
| cen_01_estate_1841 | census | A | template | semantic_layer | ✓ | - | ✓ | 120 |
| cen_02_estate_1851 | census | A | template | semantic_layer | ✓ | - | ✗ | 118 |
| cen_03_ballinacor_1841 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 6 |
| cen_04_famine_decline | census | A-trend | template | semantic_layer | ✓ | - | ✗ | 152 |
| cen_05_trend_1841_1861 | census | A-trend | verified | semantic_layer | ✓ | ✓ | ✗ | 142 |
| cen_06_uninhabited | census | A | template | semantic_layer | ✓ | - | ✓ | 1232 |
| cen_07_all_years | census | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 329 |
| cen_08_by_parish | census | A | template | semantic_layer | ✓ | - | ✓ | 163 |
| geo_01_total_townlands | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 464 |
| geo_02_parish_count | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 445 |
| geo_03_parish_list | geograph | A | template | template | ✓ | - | ✗ | 1687 |
| geo_04_ballinacor_parish | geograph | R | template | semantic_layer | ✓ | ✓ | ✓ | 559 |
| geo_05_baronies | geograph | A | template | template | ✓ | - | ✗ | 545 |
| geo_06_nearby_coolattin | geograph | R | template | template | ✓ | - | ✓ | 7 |
| geo_07_by_county | geograph | A | template | semantic_layer | ✓ | - | ✗ | 983 |
| ppl_01_total_records | people | A | template | template | ✓ | - | ✓ | 21 |
| ppl_02_byrne_records | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 2658 |
| ppl_03_murphy_list | people | P | template | verified_analysi | ✓ | - | ✗ | 1797 |
| ppl_04_widows_count | people | A | verified | semantic_layer | ✓ | ✓ | ✓ | 999 |
| ppl_05_widows_children | people | A | verified | semantic_layer | ✓ | - | ✓ | 2613 |
| ppl_06_heads_of_household | people | P | template | template | ✓ | - | ✗ | 1852 |
| ppl_07_ballynultagh_people | people | P | template | template | ✓ | - | ✗ | 7 |
| ppl_08_in_1847 | people | A | template | template | ✓ | - | ✓ | 131 |
| ten_01_total | tenancy | A | template | semantic_layer | ✓ | ✓ | ✓ | 465 |
| ten_02_gender_avg | tenancy | C | verified | semantic_layer | ✓ | - | ✓ | 3854 |
| ten_03_coolattin_tenants | tenancy | P | template | semantic_layer | ✓ | - | ✓ | 7 |
| ten_04_largest_holdings | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 4725 |
| ten_05_smallest_plots | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 1090 |
| ten_06_per_townland | tenancy | A | template | semantic_layer | ✓ | - | ✓ | 957 |
| her_01_holy_well_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4575 |
| her_02_ring_fort_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4638 |
| her_03_holy_well_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 1688 |
| her_04_ring_fort_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 870 |
| her_05_holy_well_townlands | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 1034 |
| ov_01_famine_impact | overview | R | template | template | ✓ | - | ✓ | 1909 |
| ov_02_estate_summary | overview | R | template | template | ✓ | - | ✓ | 1048 |
| ov_03_emi_and_evic | overview | A | template | semantic_layer | ✓ | - | ✓ | 109 |
| ov_04_emi_vs_population | overview | C | template | semantic_layer | ✓ | - | ✓ | 1643 |
| ov_05_records_per_year | overview | A-trend | template | template | ✓ | - | ✓ | 103 |
| er_01_exact_ballinacor | entity | I | template | semantic_layer | ✓ | - | ✓ | 6 |
| er_02_spelling_variant | entity | I | template | semantic_layer | ✓ | - | ✓ | 206 |
| er_03_spelling_ballynultach | entity | I | template | semantic_layer | ✓ | - | ✓ | 204 |
| er_04_coolattin_kg_uri | entity | I | template | semantic_layer | ✓ | - | ✓ | 6 |
| er_05_surname_byrne_exact | entity | I | template | verified_analysi | ✓ | - | ✗ | 888 |
| er_06_surname_fuzzy | entity | I | template | template | ✓ | - | ✗ | 190 |
| rel_01_ballinacor_barony | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 628 |
| rel_02_ballynultagh_county | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 682 |
| rel_03_ballinacor_parish_siblings | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 560 |
| rel_04_estate_overview | relation | R | template | template | ✓ | - | ✓ | 630 |
| rel_05_historical_monuments | heritage | H | template | template | ✓ | - | ✓ | 585 |
| cmp_01_emigration_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 182 |
| cmp_02_population_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 183 |
| cmp_03_eviction_agree | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 180 |
| fbl_01_rent | fallback | G | llm | semantic_layer | ✗ | - | ✗ | 27 |
| fbl_02_crops | fallback | G | llm | verified_analysi | ✗ | - | ✓ | 21 |
| fbl_03_fitzwilliam | fallback | G | llm | template | ✗ | - | ✗ | 33 |
| gen_01_mortality | general | G | llm | template | ✗ | - | ✗ | 9 |
| gen_02_religion | general | G | llm | semantic_layer | ✗ | - | ✗ | 7 |
| gen_03_other_estates | general | G | llm | semantic_layer | ✗ | - | ✗ | 6 |
| gen_04_weather | general | G | llm | verified_analysi | ✗ | - | ✗ | 2520 |
| gen_05_politics | general | G | llm | semantic_layer | ✗ | - | ✗ | 8 |

---

## 6. Failures Requiring Attention

- **fbl_01_rent**: routing: expected `llm` got `semantic_layer`
- **fbl_02_crops**: routing: expected `llm` got `verified_analysis`
- **fbl_03_fitzwilliam**: routing: expected `llm` got `template`
- **gen_01_mortality**: routing: expected `llm` got `template`
- **gen_02_religion**: routing: expected `llm` got `semantic_layer`
- **gen_03_other_estates**: routing: expected `llm` got `semantic_layer`
- **gen_04_weather**: routing: expected `llm` got `verified_analysis`
- **gen_05_politics**: routing: expected `llm` got `semantic_layer`

---

## 7. Headline Aggregates (verified against coolattin.db)

| Metric | Value |
|--------|-------|
| Total unified records | 13 707 |
| Records with emigration | 6 016 |
| Records with eviction (unique) | 4 108 |
| Records with tenancy | 5 247 |
| Total clearances (clearances_record.count) | 7 763 |
| Townlands | 4 225 |
| Civil parishes | 22 |
| Baronies | 11 |
| Widows | 811 |
| Holy wells | 68 |
| Ring forts | 298 |

_Generated by `ask_eval.py --phase baseline_post_migration`_