# Optimization Recommendation Report

**Scenario.** 02

---

## Final recommendation

| Field           | Value                                                        |
|-----------------|--------------------------------------------------------------|
| Finding type    | `issue_found`                                                |
| Primary tier    | `compute`                                                    |
| Action category | `scaling_policy_change` — auto-scaling policy and thresholds |

1. **Create an ASG with predictive auto-scaling for the 6× m5.large fleet** (refs 49, 55, 57, 61):
   - Instance class: m5.large (retain current class — memory p95=63.8% on m5.large is healthy; downsizing to m5.medium would halve RAM to 4 GiB and push memory utilization into an unsafe band).
   - ASG min (baseline): 4 instances.
   - ASG max: 9 instances.
   - Scaling trigger: cpu_p95 > 65% sustained for 2 consecutive data points (5-minute intervals).
   - Scale-out action: add instances in increments of 2 until CPU drops below 65% or max (9) is reached.
   - Scale-in action: remove 1 instance when cpu_p95 < 50% sustained for 15 minutes, down to min (4).
   - Pre-warm: enable predictive scaling with 30-minute look-ahead, seeded from the weekday 10:00/15:00 pattern. This pre-warms 5 additional instances by 09:30 and 14:30 on weekdays.
   - Cooldown: 300 seconds between scale actions.

2. **Projected post-change fleet behavior:**
   - Off-peak (20 of 24 weekday hours + all weekend): 4× m5.large, CPU 45–65% — within healthy band.
   - Peak (weekday 10:00–10:59, 15:00–15:59): 9× m5.large, CPU 60–64% — SLA-compliant with headroom.
   - Latency P95 at peak: projected 180–240ms (current off-peak P50=194.8ms is the best proxy for unconstrained latency; with CPU at 62% instead of 93%, latency returns to this band).

3. **Do NOT change:**
   - Do NOT add database, cache, or network tiers — none exist in the infrastructure (ref 53: $0.00 for all three), and no evidence suggests they are needed.
   - Do NOT downsize to m5.medium — memory p95=63.8% (ref 45) on m5.large (8 GiB) would become ~127.6% on m5.medium (4 GiB), causing OOM failures.
   - Do NOT increase the fixed fleet count (e.g., to 9 permanently) — this would cost $7,800/mo (+$2,600/mo) instead of the ~$3,724/mo achieved by auto-scaling, wasting $4,076/mo in off-peak idle capacity.

---

## Summary

A fixed 6× m5.large fleet with scaling_policy=none saturates at 88–96% CPU during two predictable one-hour weekday windows (10:00 and 15:00), cascading into 80 latency breaches reaching 438–572ms against the 300ms P95 SLA target. All 80 CPU breaches above 80% are perfectly timestamp-aligned with all 80 latency breaches above 300ms, confirming that insufficient peak compute capacity is the sole mechanism — the fleet is binary: healthy at 30–43% CPU off-peak, saturated at 88–96% during peaks, with zero observations in between. Database, cache, and network tiers are absent from the infrastructure ($0.00 spend each per ref 53), so no other tier requires action; the entire intervention is scoped to compute auto-scaling policy.

---

## Specialist findings

Only the Compute Analyst was invoked. The System Mapper's cost breakdown (evidence_ref=53) shows $0.00 for database, cache, and network tiers, indicating these tiers are not present in the app-02 infrastructure. Consequently, neither the Data Layer Analyst nor the Network Analyst was invoked — there are no database, cache, or network resources to analyze. The entire application stack is a compute-only deployment: 6× m5.large instances with no backing data store or cache layer visible in the infrastructure configuration. This means the compute tier is the sole tier available for diagnosis, and any performance issue must be attributable to compute capacity or configuration.

| Agent | Finding type | Confidence | Key observation | Evidence refs |
|---|---|---|---|---|
| compute_analyst | `issue_found` | high | The fixed 6× m5.large fleet with no auto-scaling policy hits CPU saturation (88–96% p95) exclusively during two recurring weekday one-hour windows (10:00 and 15:00), producing exactly 80 application latency SLA breaches (438–572ms vs the 300ms target) that are perfectly timestamp-aligned with the CPU spikes. Outside these windows, CPU sits in a healthy 30–43% band and latency remains well under SLA, confirming that insufficient peak compute capacity — not a systemic overload or data-layer bottleneck — is the root cause. | [43, 45, 47, 49, 51, 53, 55, 57, 59, 61] |

---

## Cross-tier analysis

**Drift-check.**

- _compute_analyst_ (tight): The Compute Analyst's conclusion that a fixed fleet without auto-scaling causes SLA breaches during predictable peak windows is tightly supported by converging evidence from multiple independent observations. The bimodal CPU distribution (ref 55) shows 1,264 records in the 30–43% healthy band and 80 records in 88–96% with zero values between, ruling out gradual degradation. The time-pattern analysis (ref 57) isolates the spikes to exactly hours 10 and 15 on weekdays (avg ~76.5% vs 31–39% for all other hours). The 80 CPU breaches above 80% (ref 61) are perfectly timestamp-aligned with the 80 latency breaches above 300ms SLA (ref 59), establishing direct causation from CPU saturation to latency degradation. The configuration (ref 49) confirms scaling_policy=none with no ASG bounds, explaining why the fleet cannot absorb these predictable demand peaks. Memory at p95=63.8% (ref 45) is healthy, ruling out memory pressure as a contributing factor. The cost data (ref 53) confirms no other tiers exist, so the analyst correctly scoped the issue to compute alone.

**Conflict resolution.** No specialist disagreement. Only one specialist (compute_analyst) was invoked because database, cache, and network tiers are absent from the app-02 infrastructure (all show $0.00 spend in ref 53). The compute analyst's finding of issue_found is the sole actionable claim and will form the basis of the recommendation.

---

## Trade-off analysis

| Dimension | Value | Note |
|---|---|---|
| cost | -$1,476/month (28.4% reduction) | Current $5,200/mo for 6× fixed m5.large drops to ~$3,724/mo with auto-scaling (weighted avg ~4.3 instances). Savings come entirely from eliminating off-peak over-provisioning — 4 instances handle 30–43% CPU load that currently runs on 6. No new tier costs introduced; database, cache, and network remain absent at $0. |
| performance | P95 latency from 487.5ms to 180–240ms; 80 SLA breaches eliminated | Peak CPU drops from 88–96% on 6 instances to 60–64% on 9 instances, removing the CPU saturation that drove latency to 438–572ms during weekday 10:00/15:00 windows. Off-peak latency is already healthy (P50=194.8ms); the change restores peak latency to this unconstrained baseline. All 80 latency breaches above the 300ms SLA target are eliminated. |
| reliability | SLA compliance restored from ~94% to 99.9%+ during peak windows | 80 of 1,344 observation intervals breached the 300ms P95 SLA (5.95% breach rate). With peak CPU at 60–64% instead of 88–96%, these breaches are eliminated. The ASG min=4 ensures baseline capacity never drops below the level needed for off-peak traffic (CPU 45–65%), and the 30-minute pre-warm prevents cold-start latency at peak onset. |
| risk | Low — predictive scaling adds operational complexity but is mitigated by deterministic peak pattern | Primary risk is auto-scaling lag if peak timing shifts or a novel traffic pattern emerges. This is mitigated by the extremely deterministic bimodal pattern (zero CPU observations between 43% and 88%, peaks locked to weekday hours 10 and 15). The 30-minute pre-warm and cpu_p95 > 65% reactive trigger provide a two-layer safety net. Secondary risk: reducing from 6 to 4 off-peak instances raises off-peak CPU ceiling from 43% to 64.5% — still within the healthy band but with less headroom for unexpected off-peak spikes. The scale-out trigger at 65% catches any such spike before it reaches breach territory. |

This is the rare recommendation that improves all three axes simultaneously: cost decreases, performance improves, and reliability is restored. The mechanism is not a trade-off exchange but an elimination of waste — the fixed fleet is simultaneously over-provisioned 94% of the time (6 instances where 4 suffice) and under-provisioned 6% of the time (6 instances where 9 are needed). Auto-scaling resolves both failures with a single policy change. The alternative of scaling up to a fixed 9-instance fleet was explicitly rejected: it would cost $7,800/mo (+$2,600/mo over current) while leaving $4,076/mo of off-peak idle capacity, versus $3,724/mo with auto-scaling. The alternative of downsizing instance class to m5.medium was rejected because memory p95=63.8% on 8 GiB (m5.large) would exceed physical RAM on 4 GiB (m5.medium). The directional logic is that a predictable, bimodal workload is the ideal auto-scaling candidate — the pattern is deterministic enough for predictive pre-warming yet metric-detectable enough for reactive fallback.

---

## Evaluator confidence

**High.** Drift-check tight on the sole specialist invoked. The load-bearing observations are: (1) the bimodal CPU distribution (ref 55) showing exactly zero records between 43% and 88%, which establishes that demand is binary (healthy or saturated) with no intermediate state — this rules out gradual degradation, noisy-neighbor effects, or misconfigured application logic as alternative explanations; (2) the perfect 80-for-80 timestamp alignment between CPU breaches >80% (ref 61) and latency breaches >300ms (ref 59), which establishes direct causation from CPU saturation to SLA violation — if even a few latency breaches occurred without corresponding CPU breaches, an alternative root cause would need consideration; (3) the scaling_policy=none configuration (ref 49), which confirms the fleet has no mechanism to respond to demand changes, making the policy gap the actionable root cause rather than a capacity shortfall in the instance class or count. The absence of database, cache, and network tiers (ref 53, $0.00 across all three) is the load-bearing negative observation — it eliminates the possibility of a hidden data-layer or network bottleneck masquerading as a compute issue, and licenses scoping the entire intervention to compute alone. The cost derivation is arithmetic from the per-instance cost ($866.67/mo from $5,200/6) and the weighted instance-hour calculation, not estimated.


---

## Provenance

- Cycle: cycle_20260604_143726_ddaeaf53
- Application: app-02
- Evidence refs (audit_records ids): [62]

Inspect the full audit + harness trail with:

    scripts/show_audit_trail.sh app-02 cycle_20260604_143726_ddaeaf53
