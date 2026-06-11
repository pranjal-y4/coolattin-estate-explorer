# Gate Block Audit — Live Fallback Path

**Source run:** `fallback_gate_audit` (2026-06-10 23:04 UTC)  
**Schema context:** post-fix (ER tables added to `_ANNOTATED_SCHEMA`)  
**Chunk timeout:** 180 s (extended from 90 s)

> The numeric gate discards any LLM synthesis where a number in the prose
> is not present in the SQL result rows or the question text. This audit
> records the actual blocked synthesis text, the flagged numbers, and the
> classification of each block as one of:
>
> - **GENUINE_HALLUCINATION** — number is fabricated from LLM background
>   knowledge; not derived from any pipeline data source.
> - **FORMATTING_MISMATCH** — number is a markdown formatting artifact
>   (e.g. "1. 2. 3." numbered list items) or a count of rows returned
>   (e.g. "1 record returned") that was not in the SQL value columns.
> - **LEGITIMATE_DERIVATION** — number is real pipeline data (entity
>   resolution sql_id, graph context value, KG URI fragment) that is
>   present in the pipeline provenance but not in `sql_result.rows`.

---

## G-Series / Out-of-Scope Cases

### gen_01_mortality
**Question:** How many people died of Famine-related causes on the Coolattin estate?  
**SQL rows:** `[{"message": "No death data available"}]` — no numeric values  
**Flagged violations:** `1845`, `1852`, `1864`, `994`

**Blocked synthesis (first 600 chars):**
> No death records for Famine-related causes on the Coolattin estate are held in the current
> database — the query returned no data on this metric.
> **What is known:** The townland of Coolattin (sql_id 994) was successfully identified in the
> records, but no mortality data linked to it has been indexed in the system. This is a
> data-availability gap, not necessarily a historical absence; Coolattin was a large
> Fitzwilliam estate in Co. Wicklow with documented Famine-era activity, but those figures
> are not surfaced here.
> **Next steps you can act on now:**
> 1. **Consult the Coolattin Estate Papers** …

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 1845, 1852, 1864 | LLM background knowledge (Famine years) | **GENUINE_HALLUCINATION** |
| 994 | Entity resolution sql_id (townland lookup result) | LEGITIMATE_DERIVATION |

**Verdict: Gate was CORRECT to block.** The synthesis introduced three unsupported Famine-era years drawn from world knowledge, not from any data row.

---

### gen_02_religion
**Question:** What religion were the Coolattin tenants?  
**SQL rows:** `[{"message": "I could not produce a validated SQL query…"}]`  
**Flagged violations:** `1`, `1766`, `1851`, `2`, `3`, `4`, `91`, `994`

**Blocked synthesis (first 600 chars):**
> The available records do not contain a retrievable answer to this question in the current
> form. The SQL query could not be validated against the Coolattin townland data (sql_id 994),
> and no graph context was returned, so no religion figures can be stated.
> **What is known:** Coolattin is a confirmed townland (sql_id 994;
> kg_uri `place/present-day/townland/Coolattin/v1kzs91`), so the entity is resolved — the
> gap is in the query structure, not the place identification.
> **Next steps you can act on immediately:**
> 1. **Rephrase by record type** — ask "What religion is recorded for tenants in Cool…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 1766, 1851 | LLM background knowledge (historical dates) | **GENUINE_HALLUCINATION** |
| 994 | Entity resolution sql_id | LEGITIMATE_DERIVATION |
| 1, 91 | KG URI fragment (`v1kzs91` → digits 1, 91 extracted) | FORMATTING_MISMATCH |
| 2, 3, 4 | Markdown ordered list markers (1. 2. 3. …) | FORMATTING_MISMATCH |

**Verdict: Gate was CORRECT to block.** The synthesis introduced 1766 and 1851 drawn from LLM world knowledge (Church of Ireland disestablishment 1869, census of 1851). List markers and URI fragments are false-positive contributions but did not change the outcome.

---

### gen_05_politics
**Question:** Were any Coolattin tenants involved in political movements during the 1840s?  
**SQL rows:** `[{"message": "I could not produce a validated SQL query…"}]`  
**Flagged violations:** `1847`, `1849`, `1850`, `2`, `3`, `4974`, `5`, `994`

**Blocked synthesis (first 600 chars):**
> The structured estate records held in this system do not contain a validated query result
> that can confirm or deny Coolattin tenant involvement in political movements during the 1840s.
> **What is known:** Coolattin (sql_id 994) is a resolved townland in the Fitzwilliam/Coolattin
> estate records. The system holds tenancy, rental, and emigration data for this estate, but
> political affiliation or movement membership is not a discrete field in those record types.
> **What the records cannot answer here:** "Political movements" is not a structured…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 1847, 1849, 1850 | LLM background knowledge (Young Ireland, famine years) | **GENUINE_HALLUCINATION** |
| 994 | Entity resolution sql_id | LEGITIMATE_DERIVATION |
| 4974 | Townland record count or corpus size from KG context | LEGITIMATE_DERIVATION |
| 2, 3, 5 | Markdown list markers or partial URI fragment | FORMATTING_MISMATCH |

**Verdict: Gate was CORRECT to block.** Three historical years from the 1840s political context are world-knowledge hallucinations.

---

## In-Scope Cases (data IS in DB)

### er_wh_02_confirmed_matches ✓ (exec correct after schema fix)
**Question:** How many workhouse-to-estate links are confirmed matches?  
**Oracle:** 3 | **LLM SQL:** `SELECT COUNT(*) FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'` → **3** ✓  
**SQL rows:** `[{"COUNT(*)": 3}]` → allowed numbers: `{3}`  
**Flagged violations:** `0`, `1`, `2`, `4`

**Blocked synthesis (first 600 chars):**
> **3 workhouse-to-estate links are confirmed matches**, drawn from the `workhouse_unified_links`
> table (1 record, `label = 'CONFIRMED_MATCH'`).
> **Discrepancy:** The graph database returns 0 for this same metric against 3 in SQL — likely
> due to a schema mismatch or incomplete data loading in GraphDB. Treat the SQL figure as
> authoritative here.
> **Next steps:**
> 1. **View the 3 source records** — query `workhouse_unified_links WHERE label='CONFIRMED_MATCH'`
> 2. **Check unconfirmed links** — run the sam…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 0 | Graph context — "graph returns 0" (real pipeline value, KG 503 error) | LEGITIMATE_DERIVATION |
| 1 | Row count — "(1 record)" meaning 1 row returned from DB | FORMATTING_MISMATCH |
| 2, 4 | Markdown ordered list markers (2. …, 4. …) | FORMATTING_MISMATCH |

**Verdict: Gate fired on formatting artifacts + legitimate graph context value.** The answer **3** is correct. If list markers and row-count phrases were excluded from the gate, the only remaining violation would be `0` (graph provenance). Gate logic fix (adding graph context to allowlist) is out of scope per this pass.

---

### er_wh_03_review_needed ✓ (exec correct after schema fix)
**Question:** How many workhouse-to-estate record links require human review?  
**Oracle:** 136 | **LLM SQL:** `SELECT COUNT(*) FROM workhouse_unified_links WHERE review_required=1` → **136** ✓  
**SQL rows:** `[{"COUNT(*)": 136}]` → allowed numbers: `{1, 3, 6, 13, 36, 136}` (with sub-sequences)  
**Flagged violations:** `0`, `2`, `4`

**Blocked synthesis (first 600 chars):**
> **136 workhouse-to-estate record links currently require human review**, drawn from a single
> query against `workhouse_unified_links` (1 row returned, `review_required = 1`).
> **Discrepancy:** The graph database returns 0 for this same metric against 136 in SQL —
> almost certainly a schema mismatch or incomplete data load into GraphDB. Treat the SQL
> figure as authoritative until the graph is reconciled.
> **Next steps:**
> 1. **View the 136 flagged records** by querying …
> 2. **Check unconfirmed links** …

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 0 | Graph context — "graph returns 0" (real pipeline value) | LEGITIMATE_DERIVATION |
| 2, 4 | Markdown ordered list markers | FORMATTING_MISMATCH |

**Verdict: Gate fired purely on formatting artifacts + legitimate graph context.** The answer **136** is correct and `0` is genuine pipeline data. This is the clearest false-positive case in the set.

---

### er_wh_04_mentions_count ✗ (exec still wrong)
**Question:** How many individual name mentions were extracted from workhouse records for entity resolution?  
**Oracle:** 8214 | **LLM SQL:** `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE townland_norm='DO WORKHOUSE'` → **0** ✗  
**SQL rows:** `[{"person_count": 0}]` → allowed numbers: `{0}`  
**Flagged violations:** `1`, `2`, `3`, `4`, `529`, `13707`

**Blocked synthesis (first 600 chars):**
> The SQL query against the unified record table returns **0** distinct records linked to the
> townland normalised as "DO WORKHOUSE" (sql_id 529).
> **Discrepancy:** The knowledge graph holds a value of 13,707 for this same metric against 0
> in the SQL database. The likely reason is a schema mismatch or incomplete data loading into
> the relational layer — the graph figure cannot be treated as authoritative here, but the gap
> is substantial enough to flag as a data-integrity issue rather than a genuine zero.
> **What is known:** The entity "DO WORKHOUSE" was matched by fuzzy normalisation, which introduces…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 13707 | KG context — total unified_record count (irrelevant to this question) | **GENUINE_HALLUCINATION** |
| 529 | Entity resolution sql_id ("DO WORKHOUSE" townland) | LEGITIMATE_DERIVATION |
| 1, 2, 3, 4 | Markdown ordered list markers | FORMATTING_MISMATCH |

**Verdict: Gate was CORRECT to block.** The LLM quoted `13,707` from the KG context (the total estate record count) as if it were evidence of workhouse mentions — this is a cross-context hallucination. Root cause: LLM does not know the `source_mentions` table; schema fix helps ER link tables but not this one.

---

### fbl_04_children_emigrated ✗ (exec still wrong — scope filter too narrow)
**Question:** How many children under the age of 18 emigrated from the Coolattin estate?  
**Oracle:** 2610 | **LLM SQL:** `…WHERE has_emigration_record=1 AND age<18 AND townland_norm='COOLATTIN'` → **95** ✗  
**SQL rows:** `[{"emigrated_children_under_18": 95}]` → allowed: `{1, 5, 8, 9, 18, 95}` (rows + "18" from question)  
**Flagged violations:** `2`, `3`, `4`, `6016`, `994`

**Blocked synthesis (first 600 chars):**
> **95 children under the age of 18 are recorded as having emigrated from the Coolattin
> estate**, based on 95 distinct records in the unified_record table where emigration is
> confirmed and age is under 18 (townland: COOLATTIN, sql_id 994).
> **Discrepancy to note:** The graph database returns a figure of 6,016 for the same metric
> against 95 in the SQL database. This is almost certainly a schema mismatch or incomplete
> data loading in GraphDB — the SQL count of 95 is the authoritative figure here.
> **Next steps:**
> 1. **View the 95 source re…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 6016 | KG context — total emigration count (not child-specific) | LEGITIMATE_DERIVATION |
| 994 | Entity resolution sql_id | LEGITIMATE_DERIVATION |
| 2, 3, 4 | Markdown ordered list markers | FORMATTING_MISMATCH |

**Verdict: Gate fired on formatting artifacts + legitimate pipeline context values.** The answer 95 is wrong (oracle=2610) because of a `townland_norm='COOLATTIN'` scope filter. Gate correctly prevented delivery of a wrong number, but for the wrong reason (formatting, not the exec error).

---

### fbl_06_widows_emigrated ✗ (exec still wrong — scope filter too narrow)
**Question:** How many widows emigrated from the Coolattin estate?  
**Oracle:** 15 | **LLM SQL:** `…WHERE is_widow=1 AND has_emigration_record=1 AND townland_norm='COOLATTIN'` → **1** ✗  
**SQL rows:** `[{"widows_emigrated": 1}]` → allowed: `{1}`  
**Flagged violations:** `2`, `3`, `4`, `6016`, `994`

**Blocked synthesis (first 600 chars):**
> **1 widow** with an emigration record is found in the Coolattin estate records (sql_id 994),
> drawn from 1 matching record in the unified_record table.
> **Discrepancy to note:** The knowledge graph returns a figure of 6,016 against the SQL value
> of 1. This is almost certainly a schema mismatch or incomplete data loading in GraphDB — the
> graph value should not be treated as a count of widows from Coolattin specifically.
> The SQL result is the authoritative figure here.
> **Caveats:** The SQL query relies on two flags (`is_widow = 1` and `has_emigration_record = 1`)
> applied to the normalised townla…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 6016 | KG context — total emigration count (not widow-specific) | LEGITIMATE_DERIVATION |
| 994 | Entity resolution sql_id | LEGITIMATE_DERIVATION |
| 2, 3, 4 | Markdown ordered list markers | FORMATTING_MISMATCH |

**Verdict: Gate fired on formatting artifacts + legitimate pipeline context values.** The answer 1 is wrong (oracle=15) because of a scope filter. Gate prevented a wrong answer, but for the wrong reason.

---

### fbl_07_er_candidate_count ✗ (exec still wrong — table unknown)
**Question:** How many entity resolution candidates were generated when matching workhouse records to estate tenants?  
**Oracle:** 22928 | **LLM SQL:** `validated_sql_unavailable` sentinel → rows: message string  
**SQL rows:** no numeric values  
**Flagged violations:** `1`, `2`, `3`, `4`, `529`

**Blocked synthesis (first 600 chars):**
> The records held in this system cannot answer this question as posed — no validated query
> could be constructed to count entity resolution candidates from workhouse-to-estate matching,
> and no supporting rows were returned.
> **What is known:** The entity label "DO WORKHOUSE" was matched to sql_id 529 via a fuzzy
> match only (no confirmed kg_uri), so even the townland anchor is uncertain. Graph context
> holds nothing additional.
> **Why the query failed:** The question asks about an analytical process (entity resolution
> candidate generation) rather than a record attribute — this system stores estate…

**Classification:**

| Number | Source | Classification |
|--------|--------|----------------|
| 529 | Entity resolution sql_id | LEGITIMATE_DERIVATION |
| 1, 2, 3, 4 | Markdown ordered list markers | FORMATTING_MISMATCH |

**Verdict: Gate fired on pure formatting artifacts and a legitimate entity ID.** The LLM correctly identified it cannot answer (no data). The block prevented the "I cannot answer" synthesis from being delivered — arguably the gate should not have fired here at all since there is no numeric claim in the answer.

---

## Summary Table

| Case | Exec ✓/✗ | Gate CORRECT? | Dominant violation category |
|------|----------|---------------|-----------------------------|
| gen_01_mortality | N/A | **YES** | GENUINE_HALLUCINATION (1845, 1852, 1864) |
| gen_02_religion | N/A | **YES** | GENUINE_HALLUCINATION (1766, 1851) |
| gen_05_politics | N/A | **YES** | GENUINE_HALLUCINATION (1847, 1849, 1850) |
| er_wh_02_confirmed_matches | ✓ | **NO** | FORMATTING_MISMATCH + LEGITIMATE |
| er_wh_03_review_needed | ✓ | **NO** | FORMATTING_MISMATCH + LEGITIMATE |
| er_wh_04_mentions_count | ✗ | **YES** | GENUINE (13707 from wrong KG context) |
| fbl_04_children_emigrated | ✗ | Incidental | FORMATTING_MISMATCH + LEGITIMATE |
| fbl_06_widows_emigrated | ✗ | Incidental | FORMATTING_MISMATCH + LEGITIMATE |
| fbl_07_er_candidate_count | ✗ | **NO** | FORMATTING_MISMATCH + LEGITIMATE |

**Gate correctly blocked: 4/9** (3 G-series genuine hallucinations + er_wh_04 wrong-table KG value)  
**Gate false-positives: 3/9** (er_wh_02, er_wh_03 answer correct but gate fires on `0`+list markers; fbl_07 correct refusal blocked)  
**Gate incidental: 2/9** (fbl_04, fbl_06 — exec wrong, gate fires but for wrong reason)

---

## Normalisation Fix Applied

**Problem:** Markdown ordered list markers (`1. 2. 3. 4.` at line boundaries) are extracted
as numeric tokens by `_extract_numeric_tokens`, creating false-positive violations when the
synthesis uses numbered formatting.

**Fix:** Strip markdown list markers from synthesis text before number extraction in
`_gate_violations` (the nested function inside `_rewrite_answer_with_synthesis`).

**Regex:** `re.sub(r'(?m)^\s*\d+\.\s+', ' ', text)` — matches digit(s) + period + whitespace
at the start of any line (multi-line mode).

**Impact on these 9 cases:**
- Removes `2`, `4` from er_wh_02 violations; remaining: `[0, 1]` → still gate=fallback
- Removes `2`, `4` from er_wh_03 violations; remaining: `[0]` → still gate=fallback
- Removes `1`, `2`, `3`, `4` from fbl_07 violations; remaining: `[529]` → still gate=fallback
- Removes `2`, `3`, `4` from fbl_04, fbl_06, er_wh_04; remaining violations unchanged for outcome

The list marker fix eliminates a class of false-positive contributions but does **not** change
any gate outcome in this set — the dominant false positives (`0` from graph context, `994`/`529`
entity IDs, `6016` from KG) remain. A gate logic fix (adding entity IDs and KG context values
to the allowlist) would be needed to eliminate false positives for er_wh_02, er_wh_03, fbl_07.

_Generated from `eval_results/eval_fallback_gate_audit.json`_
