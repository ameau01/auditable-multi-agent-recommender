"""End-to-end integration test for the agent system.

Runs `run_cycle("app-08")` against the real MCP adapter (which reads
from the real HF dataset cache) and validates the resulting audit
trail and harness trail.

Skipped when HF is unreachable — same probe pattern as
tests/integration/test_mcp_server.py. Set NO_NETWORK=1 to opt out
explicitly.
"""

from __future__ import annotations

import os
import socket
import threading
import urllib.error
import urllib.request

import pytest

from src.agents.runner import run_cycle
from src.audit import AuditStore
from src.audit.queries import (
    find_recommendation_for_cycle,
    get_cycle_events,
    get_harness_events_for_cycle,
)
from src.audit.store import IN_MEMORY


_DATASET_REPO = "ameau01/synthesized-cloud-optimization-recommendations"
_PROBE_TIMEOUT_SECONDS = 4.0


def _has_hf_network() -> bool:
    """Same shape as tests/integration/test_mcp_server.py: env opt-outs,
    bounded probe budget. Keeps offline / sandboxed CI from hanging."""
    if os.environ.get("SKIP_HF_TESTS") or os.environ.get("NO_NETWORK"):
        return False

    result: list[bool] = [False]

    def probe() -> None:
        try:
            with socket.create_connection(("huggingface.co", 443), timeout=3):
                pass
        except (OSError, socket.timeout):
            return
        try:
            req = urllib.request.Request(
                f"https://huggingface.co/api/datasets/{_DATASET_REPO}",
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result[0] = 200 <= resp.status < 300
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, OSError, TimeoutError):
            return

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout=_PROBE_TIMEOUT_SECONDS)
    if t.is_alive():
        return False
    return result[0]


pytestmark = pytest.mark.skipif(
    not _has_hf_network(),
    reason=(
        "HF Hub unreachable, rate-limited, or skipped via "
        "SKIP_HF_TESTS / NO_NETWORK env; skipping agent integration test"
    ),
)


def _make_store() -> AuditStore:
    """In-memory store so the test never touches disk."""
    s = AuditStore(db_path=IN_MEMORY)
    s.initialize()
    return s


def test_agents_run_on_app_08_produces_expected_trail() -> None:
    """The canonical positive case. app-08 is the cross-tier scenario
    we use as the demo throughout the project."""
    store = _make_store()
    cycle_id = run_cycle("app-08", store=store, trigger_type="test")
    assert cycle_id.startswith("cycle_")

    # Audit rows: cycle_started, 2x tool_call+observation, system_mapper_output,
    # 2x supervisor_decision (dispatch_system_mapper + complete under the
    # supervisor-as-router pattern), cycle_completed.
    events = get_cycle_events(store, cycle_id)
    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e.type] = type_counts.get(e.type, 0) + 1
    assert type_counts.get("cycle_started") == 1
    assert type_counts.get("tool_call") == 2
    assert type_counts.get("observation") == 2
    assert type_counts.get("system_mapper_output") == 1
    assert type_counts.get("supervisor_decision") == 2
    assert type_counts.get("cycle_completed") == 1

    # Harness rows: 2 input_validation + 2 tool_call_policy_check + 3
    # reasoning_check decision_evidence_backed (one for system_mapper_output,
    # two for supervisor_decision) + 1 orchestration_check
    # cycle_completion_legitimate, all passed. 8 total.
    h_events = get_harness_events_for_cycle(store, cycle_id)
    assert len(h_events) == 8
    assert all(h.verdict == "passed" for h in h_events)

    # No recommendation row yet (skeleton mode invokes zero specialists).
    assert find_recommendation_for_cycle(store, cycle_id) is None

    # The terminal_state on cycle_completed must be 'no_specialists'.
    cycle_completed = next(e for e in events if e.type == "cycle_completed")
    assert cycle_completed.content["final_status"] == "no_specialists"


def test_agents_correctly_identifies_app_08_tiers() -> None:
    """app-08 is the cross-tier database scenario — compute + database.
    The system_mapper_output row should record exactly those two tiers."""
    store = _make_store()
    cycle_id = run_cycle("app-08", store=store, trigger_type="test")
    events = get_cycle_events(store, cycle_id)
    sys_row = next(e for e in events if e.type == "system_mapper_output")
    tiers = sys_row.content["tiers_detected"]
    assert "compute" in tiers
    assert "database" in tiers


def test_agents_rejects_bogus_app() -> None:
    """Unknown app-name is rejected at the Input Harness gate.
    System Mapper never runs."""
    store = _make_store()
    cycle_id = run_cycle("app-99", store=store, trigger_type="test")
    events = get_cycle_events(store, cycle_id)
    types = {e.type for e in events}
    assert "tool_call" not in types
    assert "system_mapper_output" not in types
    cycle_completed = next(e for e in events if e.type == "cycle_completed")
    assert cycle_completed.content["final_status"] == "rejected_input"
