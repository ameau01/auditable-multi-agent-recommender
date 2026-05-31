# Scenario 08: Database Bottleneck Impact

A cross-tier scenario. Slow database queries during business hours
cascade into elevated compute-tier application latency. Compute itself
is correctly sized. The bottleneck lives in the database.

## Why this scenario was picked for the sample

It shows the value of cross-tier reasoning. A single-tier agent looking
only at compute would see elevated latency and recommend scaling
compute. That would not fix anything. The right answer is to optimize
the slow queries on the database, where the actual problem lives.

This scenario also has a clear cross-tier correlation in
`correlation_evidence.json`: a Pearson coefficient of 0.945 between
database query latency and compute application latency at a 15-minute
lag during weekday business hours. The agent should see that signal and
trace the cause back to the database.

## The data

| File                              | What to read it for                          |
|-----------------------------------|----------------------------------------------|
| `metadata.json`                   | scenario narrative, top_queries fixture      |
| `main.tf`                         | the deployed Terraform                       |
| `compute_telemetry.json`          | shows latency rising during business hours   |
| `database_telemetry.json`         | shows the slow queries with their p95 timing |
| `cache_telemetry.json`            | empty (no cache tier in this scenario)       |
| `network_telemetry.json`          | empty (no network tier in this scenario)     |
| `correlation_evidence.json`       | the lag-15 DB-to-compute correlation         |
| `handcrafted_recommendation.json` | the gold answer                              |

## The cross-tier correlation

```json
{
  "tier_a": "database",
  "tier_b": "compute",
  "metric_a": "db_query_p95_latency_ms",
  "metric_b": "application_p95_latency_ms",
  "coefficient": 0.945,
  "lag_minutes": 15,
  "alignment_score": 0.979
}
```

Database query latency leads compute application latency by 15 minutes
with a Pearson coefficient of 0.945. A correlation that strong with
that lag is the signature of cascading latency, not coincidence.

## The fixture: top_queries

The scenario's `metadata.json` ships a `top_queries` list with six
slow SQL queries, each with a call count and a p95 latency. The gold
answer cites these queries by their composite-index targets. An agent
that ignores the fixture and proposes a generic "optimize the database"
will fail the Rich tier of the evaluator (see `docs/eval-set.md`).

Sample query from the fixture:

```sql
SELECT c.*, ci.* FROM carts c JOIN cart_items ci ON ci.cart_id = c.id
WHERE c.user_id = ?
-- count: 6,048,000   p95: 820 ms
```

## The gold answer in one line

Optimize the top six slowest SQL queries by adding composite indexes.
Add two read replicas with read/write splitting. Do not scale compute,
the bottleneck is in the database tier.

The full answer is in `handcrafted_recommendation.json`. Look at
`specific_change` first, then `evidence.correlation_observations` for
how the agent should cite the cross-tier signal.

## The cost story

This scenario has a **negative** `savings_monthly_usd`. That is
expected. Adding two read replicas raises the database bill by about
$2,400 per month. The optimization fixes an active SLA breach on a
tier-1 checkout service, where the cost of continued latency far
exceeds the replica spend. The gold answer is honest about this
trade-off in the `cost_impact.notes` field.
