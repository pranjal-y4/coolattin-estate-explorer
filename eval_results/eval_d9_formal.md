# Ask Pipeline Eval — Baseline Post-Migration

**Run label:** `d9_formal`  
**Timestamp:** 2026-06-10 13:20:14 UTC  
**Questions run:** 83 (75 total: 70 pre-migration cases + 5 new G-series)  

---

## 1. Global Metrics

| Metric | Value |
|--------|-------|
| Routing accuracy | 80.7% |
| Entity label accuracy | 100.0% |
| SQL-id resolution | 100.0% |
| KG-URI resolution | 100.0% |
| SQL exec success | 100.0% |
| Aggregation correctness | 74.2% |
| Answer facts found rate | 65.5% |
| Honest-refusal rate (G-series) | 0.0% |
| Template hit rate | 100.0% |
| LLM calls required | 0 |
| Lane routing accuracy | 65.1% |
| p50 latency | 409 ms |
| p90 latency | 2557 ms |
| p95 latency | 4537 ms |

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
| Fallback / G-series | 16 (16 G) | honest_refusal=0.0% |

---

## 4. Routing Confusion Matrix

| Expected \ Actual | semantic_layer | template | verified_analysis |
|---|---|---|---|
| **llm** | 9 | 5 | 2 |
| **template** | 39 | 13 | 2 |
| **verified_analysis** | 8 | 0 | 5 |

---

## 5. Per-Question Results

| ID | Cat | Code | Expected | Actual | Rt | Ag | Ln | ms |
|----|-----|------|----------|--------|----|----|----|-----|
| emi_01_total | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 340 |
| emi_02_townland_ballynultagh | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 8 |
| emi_03_townland_killinure | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 8 |
| emi_04_per_year_trend | emigrati | A-trend | template | semantic_layer | ✓ | - | ✓ | 469 |
| emi_05_canada_total | emigrati | A-trend | verified | semantic_layer | ✓ | - | ✓ | 140 |
| emi_06_canada_ship | emigrati | A | verified | semantic_layer | ✓ | ✓ | ✓ | 10 |
| emi_07_ships_list | emigrati | A | template | semantic_layer | ✓ | - | ✓ | 836 |
| emi_08_in_1848 | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 8 |
| evic_01_total | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 474 |
| evic_02_worst_year | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 855 |
| evic_03_townland_ballinacor | eviction | A | template | semantic_layer | ✓ | - | ✓ | 6 |
| evic_04_per_year | eviction | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 409 |
| evic_05_people_list | eviction | P | template | semantic_layer | ✓ | - | ✓ | 8 |
| evic_06_in_1849 | eviction | A | template | semantic_layer | ✓ | - | ✓ | 141 |
| cen_01_estate_1841 | census | A | template | semantic_layer | ✓ | - | ✓ | 120 |
| cen_02_estate_1851 | census | A | template | semantic_layer | ✓ | - | ✗ | 118 |
| cen_03_ballinacor_1841 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 5 |
| cen_04_famine_decline | census | A-trend | template | semantic_layer | ✓ | - | ✗ | 135 |
| cen_05_trend_1841_1861 | census | A-trend | verified | semantic_layer | ✓ | ✓ | ✗ | 132 |
| cen_06_uninhabited | census | A | template | semantic_layer | ✓ | - | ✓ | 1155 |
| cen_07_all_years | census | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 335 |
| cen_08_by_parish | census | A | template | semantic_layer | ✓ | - | ✓ | 170 |
| geo_01_total_townlands | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 464 |
| geo_02_parish_count | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 439 |
| geo_03_parish_list | geograph | A | template | template | ✓ | - | ✗ | 1709 |
| geo_04_ballinacor_parish | geograph | R | template | semantic_layer | ✓ | ✓ | ✓ | 1445 |
| geo_05_baronies | geograph | A | template | template | ✓ | - | ✗ | 514 |
| geo_06_nearby_coolattin | geograph | R | template | template | ✓ | - | ✓ | 5 |
| geo_07_by_county | geograph | A | template | semantic_layer | ✓ | - | ✗ | 947 |
| ppl_01_total_records | people | A | template | template | ✓ | - | ✓ | 20 |
| ppl_02_byrne_records | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 90105 |
| ppl_03_murphy_list | people | P | template | verified_analysi | ✓ | - | ✗ | 1862 |
| ppl_04_widows_count | people | A | verified | semantic_layer | ✓ | ✓ | ✓ | 953 |
| ppl_05_widows_children | people | A | verified | semantic_layer | ✓ | - | ✓ | 2557 |
| ppl_06_heads_of_household | people | P | template | template | ✓ | - | ✗ | 1774 |
| ppl_07_ballynultagh_people | people | P | template | template | ✓ | - | ✗ | 7 |
| ppl_08_in_1847 | people | A | template | template | ✓ | - | ✓ | 134 |
| ten_01_total | tenancy | A | template | semantic_layer | ✓ | ✓ | ✓ | 471 |
| ten_02_gender_avg | tenancy | C | verified | semantic_layer | ✓ | - | ✓ | 3643 |
| ten_03_coolattin_tenants | tenancy | P | template | semantic_layer | ✓ | - | ✓ | 8 |
| ten_04_largest_holdings | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 4629 |
| ten_05_smallest_plots | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 1031 |
| ten_06_per_townland | tenancy | A | template | semantic_layer | ✓ | - | ✓ | 951 |
| her_01_holy_well_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4537 |
| her_02_ring_fort_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4462 |
| her_03_holy_well_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 1723 |
| her_04_ring_fort_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 855 |
| her_05_holy_well_townlands | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 921 |
| ov_01_famine_impact | overview | R | template | template | ✓ | - | ✓ | 1797 |
| ov_02_estate_summary | overview | R | template | template | ✓ | - | ✓ | 1064 |
| ov_03_emi_and_evic | overview | A | template | semantic_layer | ✓ | - | ✓ | 109 |
| ov_04_emi_vs_population | overview | C | template | semantic_layer | ✓ | - | ✓ | 1618 |
| ov_05_records_per_year | overview | A-trend | template | template | ✓ | - | ✓ | 104 |
| er_01_exact_ballinacor | entity | I | template | semantic_layer | ✓ | - | ✓ | 7 |
| er_02_spelling_variant | entity | I | template | semantic_layer | ✓ | - | ✓ | 193 |
| er_03_spelling_ballynultach | entity | I | template | semantic_layer | ✓ | - | ✓ | 196 |
| er_04_coolattin_kg_uri | entity | I | template | semantic_layer | ✓ | - | ✓ | 7 |
| er_05_surname_byrne_exact | entity | I | template | verified_analysi | ✓ | - | ✗ | 877 |
| er_06_surname_fuzzy | entity | I | template | template | ✓ | - | ✗ | 360 |
| rel_01_ballinacor_barony | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 740 |
| rel_02_ballynultagh_county | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 1655 |
| rel_03_ballinacor_parish_siblings | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 1111 |
| rel_04_estate_overview | relation | R | template | template | ✓ | - | ✓ | 832 |
| rel_05_historical_monuments | heritage | H | template | template | ✓ | - | ✓ | 699 |
| cmp_01_emigration_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 346 |
| cmp_02_population_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 259 |
| cmp_03_eviction_agree | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 253 |
| fbl_01_rent | fallback | G | llm | semantic_layer | ✗ | - | ✗ | 17 |
| fbl_02_crops | fallback | G | llm | verified_analysi | ✗ | - | ✓ | 15 |
| fbl_03_fitzwilliam | fallback | G | llm | template | ✗ | - | ✗ | 34 |
| gen_01_mortality | general | G | llm | template | ✗ | - | ✗ | 9 |
| gen_02_religion | general | G | llm | semantic_layer | ✗ | - | ✗ | 7 |
| gen_03_other_estates | general | G | llm | semantic_layer | ✗ | - | ✗ | 7 |
| gen_04_weather | general | G | llm | verified_analysi | ✗ | - | ✗ | 2478 |
| gen_05_politics | general | G | llm | semantic_layer | ✗ | - | ✗ | 9 |
| er_wh_01_linked_count | entity | I | llm | template | ✗ | ✗ | ✗ | 1023 |
| er_wh_02_confirmed_matches | entity | I | llm | template | ✗ | ✗ | ✗ | 2216 |
| er_wh_03_review_needed | entity | I | llm | template | ✗ | ✗ | ✗ | 2724 |
| er_wh_04_mentions_count | entity | I | llm | semantic_layer | ✗ | ✗ | ✗ | 4706 |
| fbl_04_children_emigrated | fallback | A | llm | semantic_layer | ✗ | ✗ | ✗ | 11 |
| fbl_05_avg_rent_owed | fallback | A | llm | semantic_layer | ✗ | ✗ | ✗ | 8 |
| fbl_06_widows_emigrated | fallback | A | llm | semantic_layer | ✗ | ✗ | ✗ | 10 |
| fbl_07_er_candidate_count | fallback | I | llm | semantic_layer | ✗ | ✗ | ✗ | 6661 |

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
- **er_wh_01_linked_count**: routing: expected `llm` got `template`; agg: expected `139` got `None`
- **er_wh_02_confirmed_matches**: routing: expected `llm` got `template`; agg: expected `3` got `None`
- **er_wh_03_review_needed**: routing: expected `llm` got `template`; agg: expected `136` got `None`
- **er_wh_04_mentions_count**: routing: expected `llm` got `semantic_layer`; agg: expected `8214` got `None`
- **fbl_04_children_emigrated**: routing: expected `llm` got `semantic_layer`; agg: expected `2610` got `None`
- **fbl_05_avg_rent_owed**: routing: expected `llm` got `semantic_layer`; agg: expected `38.07` got `None`
- **fbl_06_widows_emigrated**: routing: expected `llm` got `semantic_layer`; agg: expected `15` got `None`
- **fbl_07_er_candidate_count**: routing: expected `llm` got `semantic_layer`; agg: expected `22928` got `None`

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

_Generated by `ask_eval.py --phase d9_formal`_