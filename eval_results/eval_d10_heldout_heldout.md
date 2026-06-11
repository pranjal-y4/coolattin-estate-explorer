# Ask Pipeline Eval — Baseline Post-Migration

**Run label:** `d10_heldout_heldout`  
**Timestamp:** 2026-06-10 19:53:39 UTC  
**Questions run:** 35  

---

## 1. Global Metrics

| Metric | Value |
|--------|-------|
| Routing accuracy | 71.4% |
| Entity label accuracy | 94.7% |
| SQL-id resolution | 93.3% |
| KG-URI resolution | 93.3% |
| SQL exec success | 100.0% |
| Aggregation correctness | 77.3% |
| Answer facts found rate | 51.9% |
| Honest-refusal rate (G-series) | 0.0% |
| Template hit rate | 100.0% |
| LLM calls required | 0 |
| Lane routing accuracy | 57.1% |
| p50 latency | 151 ms |
| p90 latency | 1168 ms |
| p95 latency | 1990 ms |

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
| Analytical | 19 | agg_acc=100.0% |
| Relational | 4 | subgraph_recall=1.0 |
| Comparative | 2 | sqlite_capture=100.0% / kg_capture=100.0% |
| Fallback / G-series | 10 (10 G) | honest_refusal=0.0% |

---

## 4. Routing Confusion Matrix

| Expected \ Actual | semantic_layer | template | verified_analysis |
|---|---|---|---|
| **llm** | 7 | 2 | 1 |
| **template** | 20 | 3 | 1 |
| **verified_analysis** | 1 | 0 | 0 |

---

## 5. Per-Question Results

| ID | Cat | Code | Expected | Actual | Rt | Ag | Ln | ms |
|----|-----|------|----------|--------|----|----|----|-----|
| hh_emi_01_carnew | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 7 |
| hh_emi_02_1849 | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 5 |
| hh_emi_03_1847 | emigrati | A | template | semantic_layer | ✓ | ✓ | ✓ | 6 |
| hh_evic_01_1850 | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 470 |
| hh_evic_02_1855 | eviction | A | template | semantic_layer | ✓ | ✓ | ✓ | 151 |
| hh_evic_03_tinahely | eviction | A | template | semantic_layer | ✓ | - | ✓ | 5 |
| hh_cen_01_tinahely_1841 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 5 |
| hh_cen_02_carnew_1841 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 5 |
| hh_cen_03_tinahely_1851 | census | A | template | semantic_layer | ✓ | ✓ | ✗ | 5 |
| hh_cen_04_1871 | census | A | template | semantic_layer | ✓ | - | ✓ | 119 |
| hh_ppl_01_doyle | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 1156 |
| hh_ppl_02_kelly | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 632 |
| hh_ppl_03_whelan | people | P | template | semantic_layer | ✓ | ✓ | ✓ | 1168 |
| hh_ppl_04_tinahely_list | people | P | template | template | ✓ | - | ✗ | 7 |
| hh_ten_01_tinahely | tenancy | A | template | semantic_layer | ✓ | ✓ | ✓ | 7 |
| hh_ten_02_carnew | tenancy | A | template | semantic_layer | ✓ | ✓ | ✓ | 6 |
| hh_ten_03_female | tenancy | A | llm | semantic_layer | ✗ | ✗ | ✗ | 1867 |
| hh_ten_04_farmers | tenancy | A | llm | semantic_layer | ✗ | ✗ | ✗ | 1025 |
| hh_geo_01_shillelagh | geograph | A | llm | semantic_layer | ✗ | ✗ | ✗ | 1143 |
| hh_geo_02_tinahely_barony | geograph | R | template | semantic_layer | ✓ | ✓ | ✓ | 813 |
| hh_geo_03_carnew_parish | geograph | R | template | template | ✓ | ✓ | ✓ | 524 |
| hh_her_01_ringfort_townlands | heritage | H | verified | semantic_layer | ✓ | - | ✓ | 2778 |
| hh_her_02_tinahely | heritage | H | template | template | ✓ | - | ✓ | 1019 |
| hh_er_01_tynehely | entity | I | template | semantic_layer | ✓ | - | ✓ | 154 |
| hh_er_02_carnew_census | entity | I | template | semantic_layer | ✓ | - | ✓ | 8 |
| hh_er_03_whelan_surname | entity | I | template | verified_analysi | ✓ | - | ✗ | 956 |
| hh_cmp_01_tinahely_1841 | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 171 |
| hh_cmp_02_carnew_emi | comparat | X | template | semantic_layer | ✓ | ✓ | ✓ | 182 |
| hh_fbl_01_tenant_widows | fallback | A | llm | semantic_layer | ✗ | ✗ | ✗ | 451 |
| hh_fbl_02_scarawalsh_tenants | fallback | A | llm | semantic_layer | ✗ | ✗ | ✗ | 1990 |
| hh_gen_01_agent | general | G | llm | verified_analysi | ✗ | - | ✗ | 24 |
| hh_gen_02_schools | general | G | llm | template | ✗ | - | ✗ | 19 |
| hh_gen_03_language | general | G | llm | semantic_layer | ✗ | - | ✗ | 10 |
| hh_gen_04_aftermath | general | G | llm | template | ✗ | - | ✗ | 12 |
| hh_gen_05_compensation | general | G | llm | semantic_layer | ✗ | - | ✗ | 6 |

---

## 6. Failures Requiring Attention

- **hh_ten_03_female**: routing: expected `llm` got `semantic_layer`; agg: expected `284` got `None`
- **hh_ten_04_farmers**: routing: expected `llm` got `semantic_layer`; agg: expected `822` got `None`
- **hh_geo_01_shillelagh**: routing: expected `llm` got `semantic_layer`; agg: expected `36` got `None`
- **hh_fbl_01_tenant_widows**: routing: expected `llm` got `semantic_layer`; agg: expected `489` got `None`
- **hh_fbl_02_scarawalsh_tenants**: routing: expected `llm` got `semantic_layer`; agg: expected `540` got `None`
- **hh_gen_01_agent**: routing: expected `llm` got `verified_analysis`
- **hh_gen_02_schools**: routing: expected `llm` got `template`
- **hh_gen_03_language**: routing: expected `llm` got `semantic_layer`
- **hh_gen_04_aftermath**: routing: expected `llm` got `template`
- **hh_gen_05_compensation**: routing: expected `llm` got `semantic_layer`

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

_Generated by `ask_eval.py --phase d10_heldout_heldout`_