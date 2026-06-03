"""Wire-layer integration test for the MCP server.

Drives the server through the MCP client API (in-process via stdio
transport), so we exercise the real protocol path without needing a
GUI client like Claude Desktop.

Test strategy:
  1. Spawn the server through `mcp` client's StdioServerParameters.
  2. Call tools/list, assert the catalog matches what we expect.
  3. Call tools/call for one representative tool per family against
     scenario 08 (or a similar fixture), assert the response shape.

We intentionally don't exhaustively call all 18 tools end-to-end — the
unit tests for each tool family + the data_loader already cover
correctness. This test guarantees the wire layer works.

These tests need network access to Hugging Face on first run to populate
the .hf_cache. They're skipped in sandboxed CI environments that lack
network access (detected via a connection attempt at the top of the
file).
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest


_DATASET_REPO = "ameau01/synthesized-cloud-optimization-recommendations"

# Hard wall-clock budget for the network probe. `socket.create_connection`'s
# `timeout=` argument governs only TCP connect, NOT `getaddrinfo`; a
# misconfigured / unreachable DNS resolver can hang pytest collection
# past any per-call timeout. Running the probe in a daemon thread with
# `join(timeout=)` enforces a real ceiling on collection time.
_PROBE_TIMEOUT_SECONDS = 4.0


def _has_hf_network() -> bool:
    """Confirm HF is reachable AND not rate-limited.

    Two checks: (1) TCP connect to huggingface.co:443 succeeds, and
    (2) a HEAD request to the dataset metadata endpoint returns 2xx.
    The second check catches HF's HTTP 429 ("Too Many Requests") rate
    limit that fires on rapid CI pushes.

    Both checks run in a background daemon thread with a hard wall-clock
    budget. If the probe is still alive after the budget elapses (DNS
    stuck, slow network, VPN reconnecting, anything) we abandon it and
    treat the environment as offline. Because the thread is a daemon,
    it doesn't keep pytest alive after the test process exits.

    Two opt-outs that skip the probe entirely (no network I/O at all):
      - `SKIP_HF_TESTS=1` env var (explicit user request).
      - `NO_NETWORK=1` env var (sandboxed / air-gapped runs).
    """
    if os.environ.get("SKIP_HF_TESTS") or os.environ.get("NO_NETWORK"):
        return False

    result: list[bool] = [False]

    def probe() -> None:
        # Step 1 — basic reachability
        try:
            with socket.create_connection(("huggingface.co", 443), timeout=3):
                pass
        except (OSError, socket.timeout):
            return
        # Step 2 — actual HF API probe
        try:
            req = urllib.request.Request(
                f"https://huggingface.co/api/datasets/{_DATASET_REPO}",
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result[0] = 200 <= resp.status < 300
        except urllib.error.HTTPError:
            # 429, 403, 404 — all mean "tests would fail when fetching".
            return
        except (urllib.error.URLError, OSError, TimeoutError):
            return

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout=_PROBE_TIMEOUT_SECONDS)
    # Thread still running after the budget → treat as offline.
    if t.is_alive():
        return False
    return result[0]


pytestmark = pytest.mark.skipif(
    not _has_hf_network(),
    reason=(
        "HF Hub unreachable, rate-limited, or skipped via "
        "SKIP_HF_TESTS / NO_NETWORK env; skipping wire test"
    ),
)


# Lazy imports — only happen when the suite isn't skipped.
def _client_context():
    """Return an async context manager that yields an MCP ClientSession
    connected to our server over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="python",
        args=["-m", "src.mcp_server"],
    )
    return stdio_client(params), ClientSession


# ============================================================
# tools/list
# ============================================================
def test_server_exposes_18_tools():
    async def go():
        stdio_cm, ClientSession = _client_context()
        async with stdio_cm as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                names = {t.name for t in tools_resp.tools}
        return names

    names = asyncio.run(go())
    expected = {
        # telemetry
        "get_time_series", "get_summary_statistics", "get_time_pattern",
        "detect_threshold_breaches", "get_metric_distribution", "get_configuration",
        # context
        "get_business_context", "get_sla_target", "get_monthly_cost",
        "get_before_after_evidence",
        # specials
        "get_per_instance_breakout", "get_top_queries", "get_top_cache_keys",
        # scenarios
        "list_scenarios", "get_scenario_metadata", "get_terraform",
        "get_correlation_evidence", "get_handcrafted_recommendation",
    }
    missing = expected - names
    extra = names - expected
    assert not missing, f"missing tools: {sorted(missing)}"
    assert not extra, f"unexpected tools: {sorted(extra)}"


# ============================================================
# tools/call — one per family
# ============================================================
def _call_tool(name: str, arguments: dict) -> dict:
    """Helper: spawn server, call one tool, return the parsed result."""
    import json as _json

    async def go():
        stdio_cm, ClientSession = _client_context()
        async with stdio_cm as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                # MCP results come back as a list of content blocks; for
                # JSON-returning tools the first block is a TextContent
                # carrying the JSON string.
                first = result.content[0]
                return _json.loads(first.text)

    return asyncio.run(go())


def test_list_scenarios_returns_18_app_names():
    result = _call_tool("list_scenarios", {})
    assert "app_names" in result
    assert len(result["app_names"]) == 18
    assert all(name.startswith("app-") for name in result["app_names"])


def test_get_summary_statistics_telemetry():
    """The summary-stats response wraps its body in `statistics:` (envelope
    + named body convention, matching business_context / cost_baseline /
    before_after_evidence). See docs/mcp-server.md."""
    result = _call_tool("get_summary_statistics",
                        {"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"})
    assert result["app_name"] == "app-08"
    assert result["tier"] == "compute"
    assert result["metric"] == "cpu_p95"
    assert "statistics" in result
    assert {"mean", "p50", "p90", "p95"}.issubset(result["statistics"].keys())


def test_get_business_context_context():
    result = _call_tool("get_business_context", {"app_name": "app-08"})
    assert result["app_name"] == "app-08"
    assert "business_context" in result


def test_get_top_queries_specials():
    # Scenario 08 has the cross-tier DB cascade — has top_queries evidence.
    result = _call_tool("get_top_queries", {"app_name": "app-08"})
    assert result["app_name"] == "app-08"
    assert isinstance(result["top_queries"], list)
    assert len(result["top_queries"]) > 0


def test_get_per_instance_breakout_specials():
    """Closes Issue-PIB: the previous tool read the wrong key from the
    metadata and silently returned an empty payload for every scenario.
    Scenario 05 is the one that carries per-instance evidence; if this
    test starts seeing [], the key-rename regressed."""
    result = _call_tool("get_per_instance_breakout", {"app_name": "app-05"})
    assert result["app_name"] == "app-05"
    assert "per_instance_breakdown" in result
    assert isinstance(result["per_instance_breakdown"], list)
    assert len(result["per_instance_breakdown"]) > 0
    record = result["per_instance_breakdown"][0]
    assert "instance_id" in record
    assert "cpu_band" in record


def test_get_sla_target_extracted_from_business_context():
    """Closes Issue-SLA: tool reads the flat sla_target_* fields from
    business_context and returns them under a typed body. Old behaviour
    returned {sla: null} for every scenario."""
    result = _call_tool("get_sla_target", {"app_name": "app-08"})
    assert result["app_name"] == "app-08"
    assert "sla_target" in result
    sla = result["sla_target"]
    assert sla["p95_ms"] is not None
    assert sla["availability_pct"] is not None


def test_get_terraform_scenarios():
    result = _call_tool("get_terraform", {"app_name": "app-08"})
    assert result["app_name"] == "app-08"
    assert "aws_" in result["terraform"]  # raw HCL text
