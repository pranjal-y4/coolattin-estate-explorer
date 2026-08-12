# Graph Build Report

**Built:** 2026-06-11 04:28:09
**Verdict:** BUILD CLEAN

## Hierarchy strategy
County link is chained (townland→parish→barony→county).  When an intermediate
level is absent the lowest present descendant links directly to the nearest present
ancestor (nearest-available-ancestor, no sentinel nodes).  Gaps logged to
`data/source_snapshots/reconciliation_gaps.csv`.

## Counts
| Metric | Value |
|--------|-------|
| Total nodes | 49081 |
| Total edges | 64307 |
| Communities | 3501 |
| Nodes embedded | 0 |
| Orphan rate | 25.0% (12278/49081) |
| Skipped edges | 7960 |
| Dangling edges | 0 |

## Nodes by label
| Label | Count |
|-------|-------|
| Person | 13707 |
| WorkhouseRecord | 8214 |
| CensusObservation | 8033 |
| EmigrationEvent | 6016 |
| Townland | 4225 |
| EvictionEvent | 4108 |
| Community | 3501 |
| ClearanceObservation | 1211 |
| Voyage | 28 |
| CivilParish | 22 |
| Barony | 11 |
| County | 5 |

## Skipped edges
Total skipped: 7960

| Reason | Count |
|--------|-------|
| dst not in node set | 7960 |

### Skipped edge details (first 50)
| src | rel_type | dst | reason |
|-----|----------|-----|--------|
| `person:CL5948` | LOCATED_IN | `townland:BALLYCUMBER` | dst not in node set: townland:BALLYCUMBER |
| `event:eviction:CL5948` | OCCURRED_IN | `townland:BALLYCUMBER` | dst not in node set: townland:BALLYCUMBER |
| `person:CL5902` | LOCATED_IN | `townland:CORRAVANISH` | dst not in node set: townland:CORRAVANISH |
| `event:eviction:CL5902` | OCCURRED_IN | `townland:CORRAVANISH` | dst not in node set: townland:CORRAVANISH |
| `person:CL5271` | LOCATED_IN | `townland:ABBEY & LAND` | dst not in node set: townland:ABBEY & LAND |
| `event:eviction:CL5271` | OCCURRED_IN | `townland:ABBEY & LAND` | dst not in node set: townland:ABBEY & LAND |
| `person:CL39` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL40` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL41` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL42` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL43` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL45` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL48` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL49` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL50` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL52` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL53` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL54` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL57` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL60` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL393` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL395` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL398` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL401` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL405` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL406` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL407` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL409` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL412` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL415` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL418` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL422` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL426` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL479` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL497` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1310` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1311` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1312` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1313` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1314` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1315` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1316` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1317` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL1318` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL2266` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `event:eviction:CL2266` | OCCURRED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL2267` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `event:eviction:CL2267` | OCCURRED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `person:CL2268` | LOCATED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |
| `event:eviction:CL2268` | OCCURRED_IN | `townland:AGHOLD` | dst not in node set: townland:AGHOLD |

## Validation
### Warnings
- Orphan rate 25.0% exceeds 2% threshold (12278/49081)
- 4612 Person nodes have no direct Townland edge
- 17954 retrievable nodes missing passport embedding

## Verdict: BUILD CLEAN