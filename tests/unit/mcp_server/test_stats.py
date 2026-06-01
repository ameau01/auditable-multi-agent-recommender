"""Unit tests for src/mcp_server/_stats.py.

Exercises the non-trivial logic (percentile interpolation, time-pattern
grouping, threshold detection, histogram binning) against crafted inputs
that do not depend on the published dataset.
"""

from __future__ import annotations

import pytest

from src.mcp_server._stats import (
    percentiles,
    summary_statistics,
    time_pattern,
    find_breaches,
    metric_distribution,
)


# ============================================================
# percentiles
# ============================================================
class TestPercentiles:
    def test_basic_p50_is_median(self):
        # [1,2,3,4,5] -> p50 == 3.0
        out = percentiles([1.0, 2.0, 3.0, 4.0, 5.0], [50])
        assert out["p50"] == 3.0
        assert out["mean"] == 3.0

    def test_p95_uses_linear_interpolation(self):
        # 10 values 1..10. p95 rank = 0.95 * 9 = 8.55, interpolating
        # between sorted_vals[8]=9 and sorted_vals[9]=10 -> 9.55.
        out = percentiles([float(i) for i in range(1, 11)], [95])
        assert out["p95"] == pytest.approx(9.55)

    def test_multiple_percentiles_returned(self):
        out = percentiles([1.0, 2.0, 3.0, 4.0], [25, 50, 75])
        assert set(out.keys()) == {"mean", "p25", "p50", "p75"}

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="at least one value"):
            percentiles([], [50])

    def test_out_of_range_percentile_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            percentiles([1.0, 2.0], [150])

    def test_p0_is_min_p100_is_max(self):
        out = percentiles([5.0, 1.0, 4.0, 2.0, 3.0], [0, 100])
        assert out["p0"] == 1.0
        assert out["p100"] == 5.0


# ============================================================
# summary_statistics
# ============================================================
class TestSummaryStatistics:
    def test_returns_p50_p90_p95_mean(self):
        recs = [{"x": v} for v in [10.0, 20.0, 30.0, 40.0, 50.0]]
        out = summary_statistics(recs, "x")
        assert set(out.keys()) == {"mean", "p50", "p90", "p95"}
        assert out["p50"] == 30.0
        assert out["mean"] == 30.0

    def test_missing_metric_raises(self):
        recs = [{"y": 1.0}]
        with pytest.raises(ValueError, match="not found"):
            summary_statistics(recs, "x")

    def test_null_values_skipped(self):
        recs = [{"x": 1.0}, {"x": None}, {"x": 3.0}]
        out = summary_statistics(recs, "x")
        assert out["mean"] == 2.0


# ============================================================
# time_pattern
# ============================================================
class TestTimePattern:
    def _records_for_hour(self, hour: int, value: float, n: int = 4):
        """n records on different days, all at the given hour."""
        return [
            {"timestamp": f"2026-05-0{1 + d}T{hour:02d}:00:00Z", "x": value}
            for d in range(n)
        ]

    def test_groups_by_hour_of_day(self):
        # 4 records at hour 10 with value 50, 4 records at hour 14 with value 80.
        recs = self._records_for_hour(10, 50.0) + self._records_for_hour(14, 80.0)
        out = time_pattern(recs, "x")
        assert out["by_hour_of_day"][10] == 50.0
        assert out["by_hour_of_day"][14] == 80.0
        # Hour with no records is None
        assert out["by_hour_of_day"][3] is None

    def test_groups_by_weekday(self):
        # 2026-05-01 is a Friday (weekday=4)
        recs = [
            {"timestamp": "2026-05-01T10:00:00Z", "x": 100.0},  # Fri
            {"timestamp": "2026-05-02T10:00:00Z", "x": 200.0},  # Sat
            {"timestamp": "2026-05-03T10:00:00Z", "x": 200.0},  # Sun
        ]
        out = time_pattern(recs, "x")
        assert out["by_weekday"][4] == 100.0  # Friday
        assert out["by_weekday"][5] == 200.0  # Saturday
        assert out["by_weekday"][6] == 200.0  # Sunday
        assert out["by_weekday"][0] is None   # Monday absent

    def test_n_records_excludes_nulls(self):
        recs = [
            {"timestamp": "2026-05-01T10:00:00Z", "x": 1.0},
            {"timestamp": "2026-05-01T11:00:00Z", "x": None},
            {"timestamp": "2026-05-01T12:00:00Z", "x": 3.0},
        ]
        out = time_pattern(recs, "x")
        assert out["n_records"] == 2


# ============================================================
# find_breaches
# ============================================================
class TestFindBreaches:
    def test_gt_breach_detection(self):
        recs = [
            {"timestamp": "t1", "x": 10.0},
            {"timestamp": "t2", "x": 50.0},
            {"timestamp": "t3", "x": 100.0},
        ]
        b = find_breaches(recs, "x", threshold=30.0, comparator="gt")
        assert len(b) == 2
        assert b[0] == {"timestamp": "t2", "value": 50.0}
        assert b[1] == {"timestamp": "t3", "value": 100.0}

    def test_lt_breach_detection(self):
        recs = [{"timestamp": "t1", "x": 5.0}, {"timestamp": "t2", "x": 50.0}]
        b = find_breaches(recs, "x", threshold=10.0, comparator="lt")
        assert len(b) == 1
        assert b[0]["timestamp"] == "t1"

    def test_threshold_boundary_is_not_a_breach(self):
        # Strict comparator: value equal to threshold is NOT a breach.
        recs = [{"timestamp": "t1", "x": 30.0}]
        assert find_breaches(recs, "x", threshold=30.0, comparator="gt") == []
        assert find_breaches(recs, "x", threshold=30.0, comparator="lt") == []

    def test_bad_comparator_raises(self):
        with pytest.raises(ValueError, match="comparator"):
            find_breaches([{"x": 1.0}], "x", 0.0, comparator="ge")

    def test_missing_metric_raises(self):
        with pytest.raises(ValueError, match="not found"):
            find_breaches([{"y": 1.0}], "x", 0.0)


# ============================================================
# metric_distribution
# ============================================================
class TestMetricDistribution:
    def test_bins_sum_to_total(self):
        recs = [{"x": float(i)} for i in range(100)]
        d = metric_distribution(recs, "x", n_bins=10)
        assert sum(b["count"] for b in d["bins"]) == 100

    def test_correct_range(self):
        recs = [{"x": v} for v in [1.0, 5.0, 10.0]]
        d = metric_distribution(recs, "x", n_bins=3)
        assert d["min"] == 1.0
        assert d["max"] == 10.0
        assert d["n_bins"] == 3

    def test_degenerate_range_single_bin(self):
        # All values equal -> one bin holding everything.
        recs = [{"x": 5.0}] * 7
        d = metric_distribution(recs, "x", n_bins=10)
        assert d["n_bins"] == 1
        assert d["bins"][0]["count"] == 7

    def test_empty_metric_raises(self):
        with pytest.raises(ValueError):
            metric_distribution([], "x", n_bins=5)

    def test_zero_bins_raises(self):
        with pytest.raises(ValueError, match="n_bins must be >= 1"):
            metric_distribution([{"x": 1.0}, {"x": 2.0}], "x", n_bins=0)
