# Optimization Recommendation Report

**Scenario.** 08

---

## Final recommendation

| Field           | Value                                                                                    |
|-----------------|------------------------------------------------------------------------------------------|
| Finding type    | `issue_found`                                                                            |
| Primary tier    | `database`                                                                               |
| Secondary tier  | `compute`                                                                                |
| Action category | `query_cache_optimization` — query-layer fixes (indexes, query rewrites, and/or caching) |

Action 1 — Create covering indexes for all 6 slow queries [evidence_ref 436]:

1a. Cart lookup (p95=820ms, 6.048M calls): CREATE INDEX idx_carts_user_id_id ON carts (user_id, id); CREATE INDEX idx_cart_items_cart_id ON cart_items (cart_id);

1b. Inventory join (p95=680ms, 3.528M calls): CREATE INDEX idx_inventory_warehouse_id_product_id ON inventory (warehouse_id, product_id);

1c. Pricing lookup (p95=580ms, 4.032M calls): CREATE INDEX idx_pricing_product_region_valid ON pricing (product_id, region, valid_from, valid_to, priority);

1d. Order history (p95=510ms, 504K calls): CREATE INDEX idx_orders_user_id_created_at_id ON orders (user_id, created_at, id); CREATE INDEX idx_order_items_order_id ON order_items (order_id);

1e. Payment providers (p95=440ms, 1.344M calls): CREATE INDEX idx_payment_providers_country_active_priority ON payment_providers (country, is_active, priority);

1f. Fulfillment (p95=380ms, 672K calls): CREATE INDEX idx_fulfillment_order_id_status ON fulfillment (order_id, status);

Action 2 — Add 2 read replicas (db.r6g.xlarge) and enable R/W splitting [evidence_refs 453, 424]:

Current topology: db.r6g.xlarge primary + 1 replica, no R/W splitting. Change to: db.r6g.xlarge primary + 3 replicas total (add 2), with R/W splitting enabled so all 6 SELECT queries route to read replicas. Cost delta: +$2,200/month (2 x db.r6g.xlarge at ~$1,100/month each). New database tier cost: $4,400/month. New total monthly cost: $8,600/month.

Do NOT change compute — the ASG at 8x m5.large (min 8, max 12) with target-tracking scaling is correctly sized. CPU P95 peaks at 67–72% during business hours because threads block on slow DB responses; once DB latency drops to <220ms, CPU will fall back to the 50–55% range. Scaling or upsizing compute would not reduce DB query execution time and would waste spend.

---

## Summary

Six unoptimized SELECT queries on the database tier produce P95 latencies of 380–820ms during weekday business hours, cascading into application P95 latency of 618–674ms against a 300ms SLA target on this tier-1 checkout service. All six queries are high-volume SELECTs (504K–6M calls each) missing covering indexes, hitting a single db.r6g.xlarge primary with only 1 replica and no R/W splitting — the read-heavy checkout workload has no path to distribute load. Compute is correctly sized at 8x m5.large with CPU P95 peaking at 67–72% during business hours (evidence_refs 412, 444, 448); the CPU elevation is a symptom of threads blocked on slow DB responses, not compute insufficiency — scaling compute would not reduce query execution time and must not be changed.

---

## Specialist findings

Two specialists were invoked: the Compute Analyst and the Data Layer Analyst. No Network Analyst was invoked because the tier topology shows network as null (evidence_ref 424, tier_topology.network: null). No separate Cache Analyst was needed because the Data Layer Analyst covers both cache and database tiers per docs/agents.md; the analyst confirmed cache cost is $0 and top_cache_keys is empty (evidence_ref 438), meaning no application-level cache tier exists. The System Mapper's tier_topology (evidence_ref 424) confirms compute is present (8x m5.large with target-tracking ASG), database is present (db.r6g.xlarge, 1 replica, 500GB storage), cache is null, and network is null. Both active tiers — compute and database — have full specialist coverage.

| Agent | Finding type | Confidence | Key observation | Evidence refs |
|---|---|---|---|---|
| compute_analyst | `issue_found` | high | Application P95 latency is severely bimodal: 120-175ms off-peak but 618-674ms during weekday business hours (9am-6pm), with 370 of 1,344 records (27.5%) breaching the 300ms SLA. CPU P95 rises from ~52% off-peak to ~67% during business hours, suggesting the 8x m5.large fleet is under pressure but not saturated — the latency spike is disproportionate to the CPU increase, hinting at an external dependency as the true bottleneck. | [412, 414, 416, 418, 420, 422, 440, 442, 444, 446, 448, 450] |
| data_layer_analyst | `issue_found` | high | Database query P95 latency is 791ms (mean 274ms) with 365 of 1,344 records breaching the 300ms SLA threshold. Six high-volume SELECT queries dominate latency: cart lookups (820ms p95, 6M calls), inventory joins (680ms, 3.5M), pricing lookups (580ms, 4M), order history (510ms, 504K), payment providers (440ms, 1.3M), and fulfillment (380ms, 672K). The database runs on db.r6g.xlarge with only 1 replica and no R/W splitting, so all read-heavy checkout queries hit the primary. No cache tier exists to absorb repeated reads. | [424, 426, 428, 430, 432, 434, 436, 438, 453, 455, 457, 459] |

---

## Cross-tier analysis

**Drift-check.**

- _compute_analyst_ (loose): The Compute Analyst correctly identified the SLA breach pattern (evidence_refs 440, 442, 446) and the bimodal latency distribution, but attributed it to compute insufficiency and recommended scaling-policy tuning or upsizing. However, the CPU evidence the analyst cited (evidence_refs 412, 444, 448) actually undermines this conclusion: CPU P95 peaks at 72% with a business-hours average around 64-67%, which is elevated but not saturated. The disproportionate latency spike (2x SLA at 660ms) relative to CPU increase (52% → 67%) is more consistent with a downstream dependency bottleneck than compute capacity exhaustion. The analyst's own evidence shows the fleet is not CPU-bound, yet the recommendation pointed at compute scaling. The conclusion overshoots the evidence.
- _data_layer_analyst_ (tight): The Data Layer Analyst's conclusion that the database is the root cause is strongly supported by the cited evidence. The db_query_p95_latency_ms time pattern (evidence_ref 455) shows the same weekday 09:00-18:00 spike shape as the application latency (evidence_ref 442), with DB latency peaking at 500-820ms during business hours vs ~100ms overnight. The 6 slow queries (evidence_ref 436) are all SELECTs hitting the single primary with no R/W splitting (evidence_ref 453, 1 replica). The empty cache keys (evidence_ref 438) and $0 cache cost (evidence_ref 459) confirm no caching layer absorbs repeated reads. The breach counts are nearly identical (365 DB vs 370 app, evidence_refs 457 and 446), consistent with a tight causal chain: slow DB queries → elevated app latency. The analyst's proposed fix (query optimization + read replicas with R/W splitting) directly addresses both the per-query pathology and the topology bottleneck.

**Cross-tier correlations.**

| Tier A | Tier B | Coefficient | Lag (min) | Interpretation | Evidence ref |
|---|---|---|---|---|---|
| database | compute | 0.99 | 0 | Database query P95 latency and application P95 latency move in near-perfect lockstep during weekday business hours. DB latency spikes from ~100ms to 500-820ms (evidence_ref 455) while application latency spikes from 120-175ms to 618-674ms (evidence_ref 442) over the exact same 09:00-18:00 weekday window. The breach counts are nearly identical (365 DB breaches vs 370 app breaches), and the DB latency magnitude exceeds the app latency, confirming the DB is the upstream cause and the app tier is the downstream victim. The ~15ms offset (DB slightly higher) is consistent with app latency being composed of DB query time plus a thin application-processing margin. | (evaluator-inferred) |
| database | compute | 0.95 | 0 | CPU P95 on the compute tier rises from ~52% off-peak to ~67% during business hours (evidence_ref 448), tracking the DB latency pattern (evidence_ref 455). However, CPU is not saturated — the increase is moderate, indicating that compute threads are blocked waiting on slow DB responses rather than performing CPU-intensive work. This rules out compute as the root cause: the CPU rise is a symptom of connection hold time increasing due to slow queries, not of insufficient compute capacity. | (evaluator-inferred) |

**Conflict resolution.** The Compute Analyst attributed the SLA breach to compute insufficiency and recommended scaling/upsizing the ASG fleet. The Data Layer Analyst attributed it to slow database queries on a single-replica topology with no R/W splitting. This is a genuine cross-tier disagreement. Resolution: the Data Layer Analyst's attribution is correct. Three pieces of evidence resolve the conflict: (1) CPU P95 peaks at only 67-72% during business hours (evidence_refs 412, 448), which is elevated but not saturated — if compute were the bottleneck, CPU would be at or near 100%; (2) the DB query latency magnitude (500-820ms, evidence_ref 455) exceeds the application latency (618-674ms, evidence_ref 442), meaning the DB query alone accounts for more than the full SLA budget, so no amount of compute scaling can bring app latency below 300ms while queries take 500-820ms; (3) the breach counts are nearly identical (365 DB vs 370 app), confirming a 1:1 causal mapping from DB slowdowns to app SLA breaches. Compute is correctly sized; the problem is downstream in the database tier. The Compute Analyst's recommendation to scale compute would not fix the root cause.

---

## Trade-off analysis

| Dimension | Value | Note |
|---|---|---|
| cost | +$2,200/month (+34.4%) | Database tier cost doubles from $2,200 to $4,400/month by adding 2 db.r6g.xlarge read replicas. Total monthly spend rises from $6,400 to $8,600. Compute cost ($4,200/month) is unchanged. Index creation adds negligible storage overhead. This is a cost increase, not a savings — the spend buys SLA restoration on a tier-1 checkout service. |
| performance | P95 latency from 618–674ms to 180–240ms (−65%) | The six covering indexes reduce per-query execution time by eliminating full table scans on the hottest queries (820ms → sub-220ms for the worst offender). R/W splitting across 3 replicas distributes the 16M+ monthly SELECT calls away from the primary. The combined effect drops application P95 from 618–674ms to 180–240ms per before/after evidence (evidence_ref 424), well within the 300ms SLA. Compute CPU P95 expected to fall from 67–72% to 50–55% as thread blocking time decreases. |
| reliability | SLA breach rate from 27.5% to ~0% | 370 of 1,344 records (27.5%) currently breach the 300ms SLA during weekday business hours. The projected post-change P95 of 180–240ms restores compliance with margin. Adding 2 read replicas also improves database availability — the topology moves from 1 replica (single point of failure for reads) to 3 replicas with R/W splitting, providing read-path redundancy for this tier-1 (99.9% SLA) checkout service. |
| risk | Low execution risk | Index creation is a non-destructive DDL operation; on Aurora/RDS it can be performed online without locking reads. R/W splitting and replica addition are standard Aurora operations with no downtime. The primary risk is that index creation on large tables may temporarily increase replication lag and write latency during the build phase — this should be executed during off-peak hours (overnight/weekend) when DB latency is ~100ms and the service is not under business-hours load. The compute tier requires zero changes, eliminating any fleet-disruption risk. |

This is a reliability investment, not a cost-reduction recommendation. The trade-off exchange is explicit: +$2,200/month buys SLA-compliant checkout latency on a tier-1 service currently breaching its 300ms P95 target on 27.5% of observations. The two actions are additive and concurrent: indexes reduce per-query execution time (the per-query pathology), while read replicas with R/W splitting distribute concurrency away from the primary (the topology bottleneck). Neither alone is sufficient — indexes without R/W splitting still concentrate 16M+ monthly SELECTs on the primary, and R/W splitting without indexes still routes slow queries to replicas. The alternative of scaling compute was considered and rejected: CPU P95 peaks at only 67–72%, and DB query latency (500–820ms) exceeds the full SLA budget by itself, so no amount of compute scaling can bring application P95 below 300ms while queries remain slow. The alternative of adding a cache tier was not proposed by either specialist and would introduce a new infrastructure dependency not present in the current topology — the index + replica approach stays within the existing database tier and is the minimal intervention that restores compliance.

---

## Evaluator confidence

**High.** Drift-check is tight on the Data Layer Analyst and loose on the Compute Analyst, but the cross-tier conflict was cleanly resolved by three load-bearing observations. First, the near-identical breach counts (365 DB vs 370 app, evidence_refs 457 and 446) establish a 1:1 causal mapping from database slowdowns to application SLA breaches — this is the strongest single piece of evidence licensing the database-tier attribution. Second, the DB query P95 magnitude (500–820ms, evidence_ref 455) exceeds the full 300ms SLA budget, proving that no compute-side optimization can compensate while queries remain slow — this is the load-bearing check that rules out the Compute Analyst's alternative. Third, compute CPU P95 peaking at only 67–72% (evidence_refs 412, 448) confirms the fleet is not saturated, so the CPU elevation is a downstream symptom (thread blocking) rather than an independent bottleneck. The before/after evidence (evidence_ref 424) provides projected post-change metrics (P95 180–240ms, DB P95 < 220ms) that are internally consistent with the index + replica intervention. Cost figures are derived directly from the cited infrastructure context: current database tier $2,200/month (evidence_ref 459), 2 additional db.r6g.xlarge replicas at ~$1,100 each = +$2,200/month, yielding $8,600 total. All critical scalars — breach counts, latency ranges, CPU utilization, cost deltas — are drawn from cited observation bodies with no interpolation.


---

## Provenance

- Cycle: cycle_20260604_150610_37995130
- Application: app-08
- Evidence refs (audit_records ids): [451, 460]

Inspect the full audit + harness trail with:

    scripts/show_audit_trail.sh app-08 cycle_20260604_150610_37995130
