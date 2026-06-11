# Ask Pipeline Eval — Baseline Post-Migration

**Run label:** `d10_heldout`  
**Timestamp:** 2026-06-10 19:53:22 UTC  
**Questions run:** 83  

---

## 1. Global Metrics

| Metric | Value |
|--------|-------|
| Routing accuracy | 100.0% |
| Entity label accuracy | 100.0% |
| SQL-id resolution | 100.0% |
| KG-URI resolution | 100.0% |
| SQL exec success | 100.0% |
| Aggregation correctness | 74.2% |
| Answer facts found rate | 65.5% |
| Honest-refusal rate (G-series) | 100.0% |
| Template hit rate | 80.7% |
| LLM calls required | 16 |
| Lane routing accuracy | 65.1% |
| p50 latency | 407 ms |
| p90 latency | 2616 ms |
| p95 latency | 4657 ms |

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
| Relational | 12 | subgraph_recall=0.833 |
| Comparative | 5 | sqlite_capture=100.0% / kg_capture=100.0% |
| Fallback / G-series | 16 (16 G) | honest_refusal=100.0% |

---

## 4. Routing Confusion Matrix

| Expected \ Actual | semantic_layer | template | template_miss | verified_analysis |
|---|---|---|---|---|
| **llm** | 0 | 0 | 16 | 0 |
| **template** | 39 | 13 | 0 | 2 |
| **verified_analysis** | 8 | 0 | 0 | 5 |

---

## 5. Per-Question Results

| ID | Cat | Code | Expected | Actual | Rt | Ag | Ln | ms |
|----|-----|------|----------|--------|----|----|----|-----|
| emi_01_total | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 323 |
| emi_02_townland_ballynultagh | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 7 |
| emi_03_townland_killinure | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 7 |
| emi_04_per_year_trend | emigrati | A-trend | template | semantic_layer | ✓ | - | ✓ | 411 |
| emi_05_canada_total | emigrati | A-trend | verified | semantic_layer | ✓ | - | ✓ | 138 |
| emi_06_canada_ship | emigrati | A | verified | semantic_layer | ✓ | ✓ | ✓ | 11 |
| emi_07_ships_list | emigrati | A | template | semantic_layer | ✓ | - | ✓ | 844 |
| emi_08_in_1848 | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 8 |
| evic_01_total | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 478 |
| evic_02_worst_year | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 837 |
| evic_03_townland_ballinacor | eviction | A | template | semantic_layer | ✓ | - | ✓ | 5 |
| evic_04_per_year | eviction | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 407 |
| evic_05_people_list | eviction | P | template | semantic_layer | ✓ | - | ✓ | 7 |
| evic_06_in_1849 | eviction | A | template | semantic_layer | ✓ | - | ✓ | 144 |
| cen_01_estate_1841 | census | A | template | semantic_layer | ✓ | - | ✓ | 121 |
| cen_02_estate_1851 | census | A | template | semantic_layer | ✓ | - | ✗ | 120 |
| cen_03_ballinacor_1841 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 9 |
| cen_04_famine_decline | census | A-trend | template | semantic_layer | ✓ | - | ✗ | 136 |
| cen_05_trend_1841_1861 | census | A-trend | verified | semantic_layer | ✓ | ✓ | ✗ | 118 |
| cen_06_uninhabited | census | A | template | semantic_layer | ✓ | - | ✓ | 1156 |
| cen_07_all_years | census | A-trend | template | semantic_layer | ✓ | ✓ | ✓ | 345 |
| cen_08_by_parish | census | A | template | semantic_layer | ✓ | - | ✓ | 166 |
| geo_01_total_townlands | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 460 |
| geo_02_parish_count | geograph | A | template | semantic_layer | ✓ | ✓ | ✗ | 457 |
| geo_03_parish_list | geograph | A | template | template | ✓ | - | ✗ | 1722 |
| geo_04_ballinacor_parish | geograph | R | template | semantic_layer | ✓ | ✓ | ✓ | 915 |
| geo_05_baronies | geograph | A | template | template | ✓ | - | ✗ | 534 |
| geo_06_nearby_coolattin | geograph | R | template | template | ✓ | - | ✓ | 5 |
| geo_07_by_county | geograph | A | template | semantic_layer | ✓ | - | ✗ | 998 |
| ppl_01_total_records | people | A | template | template | ✓ | - | ✓ | 22 |
| ppl_02_byrne_records | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 89953 |
| ppl_03_murphy_list | people | P | template | verified_analysi | ✓ | - | ✗ | 1959 |
| ppl_04_widows_count | people | A | verified | semantic_layer | ✓ | ✓ | ✓ | 966 |
| ppl_05_widows_children | people | A | verified | semantic_layer | ✓ | - | ✓ | 2616 |
| ppl_06_heads_of_household | people | P | template | template | ✓ | - | ✗ | 1806 |
| ppl_07_ballynultagh_people | people | P | template | template | ✓ | - | ✗ | 7 |
| ppl_08_in_1847 | people | A | template | template | ✓ | - | ✓ | 133 |
| ten_01_total | tenancy | A | template | semantic_layer | ✓ | ✓ | ✓ | 457 |
| ten_02_gender_avg | tenancy | C | verified | semantic_layer | ✓ | - | ✓ | 3740 |
| ten_03_coolattin_tenants | tenancy | P | template | semantic_layer | ✓ | - | ✓ | 7 |
| ten_04_largest_holdings | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 4877 |
| ten_05_smallest_plots | tenancy | A | verified | semantic_layer | ✓ | - | ✓ | 1042 |
| ten_06_per_townland | tenancy | A | template | semantic_layer | ✓ | - | ✓ | 982 |
| her_01_holy_well_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4558 |
| her_02_ring_fort_population | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 4657 |
| her_03_holy_well_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 1697 |
| her_04_ring_fort_count | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 867 |
| her_05_holy_well_townlands | heritage | H | verified | verified_analysi | ✓ | - | ✓ | 924 |
| ov_01_famine_impact | overview | R | template | template | ✓ | - | ✓ | 1836 |
| ov_02_estate_summary | overview | R | template | template | ✓ | - | ✓ | 1046 |
| ov_03_emi_and_evic | overview | A | template | semantic_layer | ✓ | - | ✓ | 111 |
| ov_04_emi_vs_population | overview | C | template | semantic_layer | ✓ | - | ✓ | 1642 |
| ov_05_records_per_year | overview | A-trend | template | template | ✓ | - | ✓ | 99 |
| er_01_exact_ballinacor | entity | I | template | semantic_layer | ✓ | - | ✓ | 6 |
| er_02_spelling_variant | entity | I | template | semantic_layer | ✓ | - | ✓ | 191 |
| er_03_spelling_ballynultach | entity | I | template | semantic_layer | ✓ | - | ✓ | 214 |
| er_04_coolattin_kg_uri | entity | I | template | semantic_layer | ✓ | - | ✓ | 7 |
| er_05_surname_byrne_exact | entity | I | template | verified_analysi | ✓ | - | ✗ | 873 |
| er_06_surname_fuzzy | entity | I | template | template | ✓ | - | ✗ | 349 |
| rel_01_ballinacor_barony | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 438 |
| rel_02_ballynultagh_county | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 433 |
| rel_03_ballinacor_parish_siblings | relation | R | template | semantic_layer | ✓ | ✓ | ✓ | 407 |
| rel_04_estate_overview | relation | R | template | template | ✓ | - | ✓ | 434 |
| rel_05_historical_monuments | heritage | H | template | template | ✓ | - | ✓ | 423 |
| cmp_01_emigration_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 192 |
| cmp_02_population_vs_kg | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 179 |
| cmp_03_eviction_agree | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 175 |
| fbl_01_rent | fallback | G | llm | template_miss | ✓ | - | ✗ | 10 |
| fbl_02_crops | fallback | G | llm | template_miss | ✓ | - | ✓ | 8 |
| fbl_03_fitzwilliam | fallback | G | llm | template_miss | ✓ | - | ✗ | 20 |
| gen_01_mortality | general | G | llm | template_miss | ✓ | - | ✗ | 4 |
| gen_02_religion | general | G | llm | template_miss | ✓ | - | ✗ | 4 |
| gen_03_other_estates | general | G | llm | template_miss | ✓ | - | ✗ | 4 |
| gen_04_weather | general | G | llm | template_miss | ✓ | - | ✗ | 2413 |
| gen_05_politics | general | G | llm | template_miss | ✓ | - | ✗ | 4 |
| er_wh_01_linked_count | entity | I | llm | template_miss | ✓ | ✗ | ✗ | 1026 |
| er_wh_02_confirmed_matches | entity | I | llm | template_miss | ✓ | ✗ | ✗ | 2188 |
| er_wh_03_review_needed | entity | I | llm | template_miss | ✓ | ✗ | ✗ | 2688 |
| er_wh_04_mentions_count | entity | I | llm | template_miss | ✓ | ✗ | ✗ | 4712 |
| fbl_04_children_emigrated | fallback | A | llm | template_miss | ✓ | ✗ | ✗ | 5 |
| fbl_05_avg_rent_owed | fallback | A | llm | template_miss | ✓ | ✗ | ✗ | 6 |
| fbl_06_widows_emigrated | fallback | A | llm | template_miss | ✓ | ✗ | ✗ | 4 |
| fbl_07_er_candidate_count | fallback | I | llm | template_miss | ✓ | ✗ | ✗ | 6696 |

---

## 6. Failures Requiring Attention

- **er_wh_01_linked_count**: agg: expected `139` got `None`
- **er_wh_02_confirmed_matches**: agg: expected `3` got `None`
- **er_wh_03_review_needed**: agg: expected `136` got `None`
- **er_wh_04_mentions_count**: agg: expected `8214` got `None`
- **fbl_04_children_emigrated**: agg: expected `2610` got `None`
- **fbl_05_avg_rent_owed**: agg: expected `38.07` got `None`
- **fbl_06_widows_emigrated**: agg: expected `15` got `None`
- **fbl_07_er_candidate_count**: agg: expected `22928` got `None`

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

_Generated by `ask_eval.py --phase d10_heldout`_