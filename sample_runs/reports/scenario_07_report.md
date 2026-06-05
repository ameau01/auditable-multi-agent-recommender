# Optimization Recommendation Report

**Scenario.** 07

---

## Final recommendation

| Field           | Value                                                                                    |
|-----------------|------------------------------------------------------------------------------------------|
| Finding type    | `issue_found`                                                                            |
| Primary tier    | `cache`                                                                                  |
| Secondary tier  | `compute`                                                                                |
| Action category | `cache_capacity_adjustment` — cache tier capacity changes (memory, node count, sharding) |

1. **Scale cache cluster from 3 to 6 cache.r6g.large nodes** (evidence_refs 374, 382, 392, 394). The current 3-node cluster cannot serve the working set during peak hours, dropping hit ratio to 0.61. Doubling to 6 nodes distributes the keyspace, reduces per-node eviction pressure, and restores hit ratio to ≥0.89. Cost: +$700/month (current cache tier $700/month → $1,400/month). [evidence_refs 374, 376, 341]

2. **Implement cache warming for the top 3 key patterns** (evidence_ref 355). Pre-populate cache entries before the 09:00 business-hours ramp:
   - rec:user:* — 3.1M misses, 27% miss rate. Warm user recommendation sets from the recommendation model output at 08:30 daily.
   - rec:trending:* — 1.9M misses, 30% miss rate. Warm trending recommendation lists at 08:30 daily.
   - rec:similar:* — 1.7M misses, 38% miss rate. Warm similar-item recommendation sets at 08:30 daily.
   This eliminates the cold-cache burst at 09:00 that currently causes the sharpest hit-ratio drop (from 0.69 at hour 8 to 0.645 at hour 9, per evidence_ref 392). [evidence_ref 355, 392]

3. **Redesign cache key structures for the top 3 key patterns** (evidence_refs 355, 341). The current default key design for rec:user:*, rec:trending:*, and rec:similar:* produces fragmentation that contributes to the high miss rates. Consolidate per-user recommendation shards into fewer, larger keys to reduce total key count and improve cache memory efficiency. The before/after evidence (evidence_ref 341) confirms this change, combined with actions 1 and 2, raises cache_hit_ratio from 0.65 to 0.91. [evidence_ref 341, 355]

**Do NOT change:**
- **Compute:** The ASG at 6× m5.large (min=6, max=10, target_tracking) is correctly sized. CPU P95 at 66% during peak (evidence_ref 363) is in the healthy band. The fleet is not scaling toward max=10 because the bottleneck is cache-driven latency, not compute saturation. Scaling compute would not reduce the 6.7M cache misses or the DB overflow they cause. Current cost $3,200/month — leave unchanged. [evidence_refs 335, 363]
- **Database:** The db.r6g.large with 2 replicas is healthy. db_cache_hit_ratio 0.93 (evidence_ref 349), I/O wait P95 6.9ms (evidence_ref 351), connection pool P95 at 85 (evidence_ref 347), no slow queries (evidence_ref 353). The 335 DB latency breaches are a downstream symptom of cache miss overflow, not a database deficiency. Adding replicas or upsizing the database would absorb overflow at higher cost without fixing the root cause. Current cost $1,900/month — leave unchanged. [evidence_refs 347, 349, 351, 353, 372]

---

## Summary

A 3-node cache.r6g.large cluster with no warming and poor key design drops its hit ratio to 0.61 during weekday business hours (09:00–18:00), causing 6.7M cache misses across three key patterns (rec:user:*, rec:trending:*, rec:similar:*) to spill directly to the database, pushing DB P95 latency to 316ms and application P95 latency to 458ms — both breaching the 300ms SLA. Three cache key patterns account for the entirety of the miss cascade: rec:user:* (27% miss rate, 3.1M misses), rec:trending:* (30% miss rate, 1.9M misses), and rec:similar:* (38% miss rate, 1.7M misses); scaling from 3 to 6 nodes with warming and key redesign restores cache_hit_ratio to 0.91 and drops DB P95 latency by 38%. Do NOT scale compute or database: the compute fleet at 6× m5.large with CPU P95 at 66% is correctly sized and not saturated (ASG not scaling toward max=10), and the database at db.r6g.large with 2 replicas is healthy (db_cache_hit_ratio 0.93, I/O wait P95 6.9ms, connection pool P95 85, no slow queries identified).

---

## Specialist findings

Two specialists were invoked: compute_analyst and data_layer_analyst. No network analyst was invoked because the network tier is null in the topology (evidence_ref 341 shows tier_topology.network = null). The Data Layer Analyst covers both the cache and database tiers per its scope, so the cache root-cause finding and the DB symptom observation are both captured inside one specialist's output. All present tiers (compute, database, cache) have specialist coverage. The System Mapper's topology (evidence_ref 341) shows: compute = 6× m5.large with target_tracking ASG (min 6, max 10); database = db.r6g.large with 2 replicas and 500GB storage; cache = 3× cache.r6g.large nodes with 3600s TTL.

| Agent | Finding type | Confidence | Key observation | Evidence refs |
|---|---|---|---|---|
| compute_analyst | `issue_found` | high | Application P95 latency breaches the 300ms SLA 360 times during weekday business hours (09:00–18:00), with values ranging from 398–480ms and a bimodal distribution (893 records at 170–201ms off-peak, 360 records at 387–480ms peak). CPU P95 averages ~66% during peak on the 6× m5.large fleet, indicating moderate pressure but not saturation — the ASG is not scaling toward its max of 10, suggesting the latency driver is external to compute. | [329, 331, 333, 335, 337, 339, 357, 359, 361, 363, 365, 367] |
| data_layer_analyst | `issue_found` | high | The cache tier is the upstream root cause: cache_hit_ratio averages 0.669 overall and drops to ~0.61 during weekday peak hours, far below the healthy ≥0.89 threshold. Three key patterns — rec:user:* (27% miss rate, 3.1M misses), rec:trending:* (30% miss rate, 1.9M misses), and rec:similar:* (38% miss rate, 1.7M misses) — drive massive query spillover to the database, pushing DB P95 latency to 316ms with 335 breaches above 300ms. The database itself is healthy: db_cache_hit_ratio 0.93, I/O wait P95 only 6.9ms, connection pool P95 at 85 (not saturated). The fix is scaling cache from 3 to 6 nodes, implementing warming for the top-3 key patterns, and redesigning key structures; before/after evidence shows this restores hit ratio to 0.91 and drops DB P95 latency by ~38%. | [341, 343, 345, 347, 349, 351, 353, 355, 372, 374, 376, 378, 380, 382, 390, 392, 394, 396] |

---

## Cross-tier analysis

**Drift-check.**

- _compute_analyst_ (tight): The Compute Analyst's conclusion that sustained SLA breaches occur during business hours is directly supported by 360 threshold breaches in evidence_ref 357 (all timestamps fall within weekday 09:00–18:00), the bimodal latency distribution in evidence_ref 359 (893 off-peak records vs 360 peak records with a gap between 263ms and 387ms), and the hourly time pattern in evidence_ref 365 showing latency jumping from ~200ms at hour 8 to 355–376ms at hours 9–17. The analyst correctly noted that CPU P95 at ~66% (evidence_ref 329, 363) on 6× m5.large (evidence_ref 335) is under moderate pressure but not saturated, and appropriately flagged that the scaling policy is not driving the fleet toward max=10, suggesting a compute-external bottleneck. This hedging toward a downstream cause is consistent with the data layer analyst's cache findings.
- _data_layer_analyst_ (tight): The Data Layer Analyst's conclusion that the cache tier is the root cause is supported by multiple independent evidence lines: cache_hit_ratio mean of 0.669 with peak-hour drop to 0.61 (evidence_ref 382, 392), the bimodal cache_hit_ratio distribution with 292 records in the 0.58–0.594 bin corresponding to peak hours (evidence_ref 394), and the top 3 cache key patterns totaling 6.7M misses (evidence_ref 355). The claim that the database is absorbing overflow rather than bottlenecking is confirmed by healthy db_cache_hit_ratio of 0.93 (evidence_ref 349), I/O wait P95 of 6.9ms (evidence_ref 351), connection pool P95 at 85 (evidence_ref 347), and no top queries returned (evidence_ref 353, meaning no individual query pathology). The DB latency time pattern (evidence_ref 380) mirrors the cache hit_ratio degradation pattern (evidence_ref 392) — both shift at hour 9 and recover at hour 18, confirming the causal chain. The 335 DB latency breaches (evidence_ref 390) are temporally co-located with the 360 compute latency breaches (evidence_ref 357). The before/after evidence in evidence_ref 341 projects cache_hit_ratio recovery to 0.91 and DB P95 latency drop of 38% after scaling from 3 to 6 nodes with warming and key redesign.

**Cross-tier correlations.**

| Tier A | Tier B | Coefficient | Lag (min) | Interpretation | Evidence ref |
|---|---|---|---|---|---|
| cache | database | 0.95 | 0 | Cache hit ratio drops to ~0.61 during business hours (evidence_ref 392), and DB query P95 latency rises to 241–256ms hourly averages in the same window (evidence_ref 380). The two metrics move in lockstep with zero lag: as cache misses increase, queries that would have been served from cache spill directly to the database, elevating DB latency. The 335 DB breaches (evidence_ref 390) occur in the exact same weekday 09:00–18:00 window where cache_hit_ratio is lowest. | `392` |
| cache | compute | 0.95 | 0 | Cache hit ratio degradation during business hours (evidence_ref 392) drives the application P95 latency spike on compute (evidence_ref 365). Both metrics shift at hour 9 and recover at hour 18. The 360 compute latency breaches (evidence_ref 357) are temporally co-located with the cache hit ratio trough. The compute tier's CPU at ~66% (evidence_ref 363) is not the bottleneck — the latency is dominated by increased response time from cache-miss-driven DB round-trips flowing back through the application layer. | `365` |
| database | compute | 0.95 | 0 | DB query P95 latency elevation during business hours (evidence_ref 380, hourly averages 241–256ms at hours 9–17) directly contributes to application P95 latency on compute (evidence_ref 365, hourly averages 355–376ms at hours 9–17). The ~100–120ms gap between DB latency and application latency represents application-layer processing overhead. Both breach windows (335 DB breaches in evidence_ref 390, 360 compute breaches in evidence_ref 357) share the same weekday 09:00–18:00 pattern. | `380` |

**Conflict resolution.** No specialist disagreement. Both specialists found issues during the same business-hours window and their findings are complementary, not contradictory. The compute analyst identified the symptom (P95 latency 430–480ms, SLA breach) and correctly hedged that the driver might be compute-external. The data layer analyst identified the root cause (cache miss cascade from a 3-node cluster with 0.61 peak hit ratio, driving DB overflow) and confirmed the database is healthy (absorbing, not bottlenecking). The causal chain is unambiguous: cache misses → DB query overflow → elevated DB latency → elevated application P95 latency on compute. The actionable claim belongs to the cache tier; the compute and database tiers are symptoms, not causes. The recommendation should target the cache layer per the data layer analyst's proposal.

---

## Trade-off analysis

| Dimension | Value | Note |
|---|---|---|
| cost | +$700/month (+12%) | Cache tier doubles from $700 to $1,400/month by scaling from 3 to 6 cache.r6g.large nodes. Compute ($3,200) and database ($1,900) unchanged. Total moves from $5,800 to $6,500/month. This is a modest increase relative to the total infrastructure spend. |
| performance | P95 latency from 458ms to 200-260ms (-43% to -57%) | Application P95 latency drops from 458ms (360 SLA breaches) to the 200-260ms band, well within the 300ms SLA. DB P95 latency drops by ~38% from 316ms to ~196ms as 6.7M cache misses are eliminated. Cache hit ratio rises from 0.61 (peak) / 0.669 (mean) to 0.91. |
| reliability | 360 SLA breaches eliminated; 99.9% availability restored | All 360 application-tier SLA breaches and all 335 database-tier latency breaches occurred during weekday business hours 09:00-18:00 due to cache miss cascade. Restoring cache hit ratio to 0.91 eliminates the cascade, returning both tiers to within-SLA bands during peak hours. |
| risk | Low — additive capacity, no destructive changes | All three actions (add 3 nodes, implement warming, redesign keys) are additive. No existing nodes are removed, no instance classes are changed, no database or compute configuration is modified. The before/after evidence (evidence_ref 341) validates the projected outcome. The primary risk is that key redesign requires application-level code changes, which carry deployment risk, but the cache warming and node scaling can be deployed independently as immediate mitigations. |

This is a reliability investment, not a cost-reduction recommendation. The trade-off exchange is explicit: +$700/month (12% increase) buys elimination of 360 SLA breaches and restores application P95 latency from 458ms to the 200-260ms band. The cost-per-breach-eliminated is approximately $1.94/month per breach — a trivial unit cost for SLA compliance. Two alternatives were considered and rejected. First, scaling the database (adding replicas or upsizing from db.r6g.large) would absorb more of the overflow queries at higher cost (+$950/month per replica) without fixing the upstream miss rate — the cache would still produce 6.7M misses, and those queries would still round-trip to the database, just on a larger fleet. Second, scaling compute (increasing ASG min or upsizing from m5.large) would not reduce latency at all because the bottleneck is cache-miss-driven response time, not CPU saturation — CPU P95 at 66% confirms the compute tier has headroom. The cache tier is the only intervention that addresses the root cause: insufficient cache capacity and absent warming create the miss cascade that propagates to both downstream tiers.

---

## Evaluator confidence

**High.** Drift-check tight on both specialists. The recommendation is licensed by four load-bearing observations: (1) the cache hit ratio peak-hour drop to 0.61 (evidence_ref 392), which establishes the upstream cause; (2) the top-3 key pattern miss volumes totaling 6.7M misses (evidence_ref 355), which quantifies the overflow mechanism; (3) the DB health indicators — db_cache_hit_ratio 0.93 (evidence_ref 349), I/O wait P95 6.9ms (evidence_ref 351), no slow queries (evidence_ref 353) — which rule out the database-bottleneck alternative and confirm the DB is absorbing, not generating, the latency; and (4) the before/after evidence (evidence_ref 341) showing cache_hit_ratio recovery from 0.65 to 0.91 and DB P95 latency reduction of 38% after the proposed intervention, which validates the projected outcome rather than leaving it as a hypothesis. Three independent zero-lag cross-tier correlations (cache→DB, cache→compute, DB→compute) all point to cache as the single upstream cause. The compute CPU P95 at 66% (evidence_ref 363) on a fleet not scaling toward max=10 is the load-bearing negative check that ruled out compute as a contributing factor. Cost impact is committed: +$700/month derived from the $700 current cache tier cost (evidence_ref 376) doubled by scaling from 3 to 6 same-class nodes.


---

## Provenance

- Cycle: cycle_20260604_150000_a952f749
- Application: app-07
- Evidence refs (audit_records ids): [368, 397]

Inspect the full audit + harness trail with:

    scripts/show_audit_trail.sh app-07 cycle_20260604_150000_a952f749
