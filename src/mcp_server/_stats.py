"""Pure statistical helpers consumed by the per-tier telemetry tools.

Each function operates on a list of telemetry records (the shape returned
by `data_loader.load_scenario(sid)[tier_telemetry_key]`) plus a metric
name. The server is otherwise thin: it slices the right tier, then hands
records to these helpers.

Design notes:

- None of these helpers reach into the dataset. They take records and
  return primitives. Tools call them after slicing.
- `find_breaches` takes a caller-supplied threshold rather than reading
  a healthy_band from the dataset, because the dataset does not carry
  per-metric bands. The agent decides what counts as a breach using the
  SLA target it gets from `get_sla_target`.
- `time_pattern` returns a hour-of-day + weekday mean grid. That is the
  minimum a downstream consumer needs to spot business-hours and
  weekday/weekend patterns. The agent does the labeling.
- All functions are pure (no I/O, no globals) so unit tests can exercise
  them with crafted inputs.
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean


def _values_for(records: list[dict], metric: str) -> list[float]:
    """Pull a metric series out of telemetry records, skipping any missing values.

    Raises ValueError if the metric is not present on any record.
    """
    out: list[float] = []
    found = False
    for r in records:
        if metric in r:
            found = True
            v = r[metric]
            if v is not None:
                out.append(float(v))
    if not found:
        raise ValueError(f"metric {metric!r} not found in any record")
    return out


def percentiles(values: list[float], qs: list[int]) -> dict[str, float]:
    """Return {percentile_name: value} for the requested percentiles.

    Uses linear interpolation between order statistics. Result keys are
    formatted as 'p50', 'p90', etc. The 'mean' key is always included
    as a convenience since every caller wants it.

    Raises ValueError on empty input.
    """
    if not values:
        raise ValueError("percentiles requires at least one value")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    out: dict[str, float] = {"mean": mean(sorted_vals)}
    for q in qs:
        if not 0 <= q <= 100:
            raise ValueError(f"percentile {q} out of range 0..100")
        # Linear-interpolation rank, matching numpy's default.
        rank = q / 100 * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        out[f"p{q}"] = sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac
    return out


def summary_statistics(records: list[dict], metric: str) -> dict[str, float]:
    """Convenience wrapper: percentiles(50, 90, 95) + mean for one metric.

    Returns {"p50": ..., "p90": ..., "p95": ..., "mean": ...}.
    """
    values = _values_for(records, metric)
    return percentiles(values, [50, 90, 95])


def time_pattern(records: list[dict], metric: str) -> dict[str, dict]:
    """Group a metric series by hour-of-day and weekday, return mean per group.

    Returns:
      {
        "by_hour_of_day": {0: avg, 1: avg, ..., 23: avg},
        "by_weekday":     {0: avg (Mon), 1: avg, ..., 6: avg (Sun)},
        "n_records":      <int>
      }

    The hourly + weekday breakdown together is enough for the agent to
    notice (a) business-hours spikes and (b) weekday-vs-weekend patterns.
    """
    by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
    by_weekday: dict[int, list[float]] = {d: [] for d in range(7)}
    n = 0
    for r in records:
        if metric not in r or r[metric] is None:
            continue
        ts_raw = r.get("timestamp")
        if not ts_raw:
            continue
        # Records use ISO-8601 with trailing 'Z' for UTC; normalize.
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        v = float(r[metric])
        by_hour[ts.hour].append(v)
        by_weekday[ts.weekday()].append(v)
        n += 1
    return {
        "by_hour_of_day": {
            h: mean(vs) if vs else None for h, vs in by_hour.items()
        },
        "by_weekday": {
            d: mean(vs) if vs else None for d, vs in by_weekday.items()
        },
        "n_records": n,
    }


def find_breaches(
    records: list[dict],
    metric: str,
    threshold: float,
    comparator: str = "gt",
) -> list[dict]:
    """Return the windows where a metric breaches a caller-supplied threshold.

    Args:
      records: telemetry list (one entry per timestamp).
      metric: field name to inspect.
      threshold: numeric cutoff.
      comparator: 'gt' (default, value > threshold is a breach) or 'lt'.

    Returns:
      List of {timestamp, value} dicts, one per breaching record.

    Raises ValueError on bad comparator or missing metric.
    """
    if comparator not in ("gt", "lt"):
        raise ValueError(f"comparator must be 'gt' or 'lt', got {comparator!r}")
    op = (lambda v: v > threshold) if comparator == "gt" else (lambda v: v < threshold)
    breaches: list[dict] = []
    found = False
    for r in records:
        if metric in r:
            found = True
            v = r[metric]
            if v is not None and op(float(v)):
                breaches.append({"timestamp": r.get("timestamp"), "value": float(v)})
    if not found:
        raise ValueError(f"metric {metric!r} not found in any record")
    return breaches


def metric_distribution(
    records: list[dict],
    metric: str,
    n_bins: int = 10,
) -> dict:
    """Return a histogram of the metric's values across all records.

    Returns:
      {
        "min": float, "max": float, "n_bins": int,
        "bins":  [{"lo": float, "hi": float, "count": int}, ...]
      }

    Bins are uniform-width on the [min, max] range. An empty series
    raises ValueError to surface the bad request immediately.
    """
    values = _values_for(records, metric)
    if not values:
        raise ValueError(f"metric {metric!r} has no non-null values")
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    lo, hi = min(values), max(values)
    if lo == hi:
        # Degenerate range: all values equal. Single bin holding everything.
        return {
            "min": lo, "max": hi, "n_bins": 1,
            "bins": [{"lo": lo, "hi": hi, "count": len(values)}],
        }
    width = (hi - lo) / n_bins
    bins = [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": 0}
            for i in range(n_bins)]
    for v in values:
        # Last bin is inclusive on the right; others exclusive.
        idx = min(int((v - lo) / width), n_bins - 1)
        bins[idx]["count"] += 1
    return {"min": lo, "max": hi, "n_bins": n_bins, "bins": bins}
