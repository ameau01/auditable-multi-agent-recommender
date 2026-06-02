"""Tests for read-only query helpers.

Covers:
  - get_cycle_events returns all events for a cycle in insertion order
  - get_decision_chain CTE walks parent_id backward, filters to decisions
  - get_evidence_consumers via json_each finds decisions citing evidence
  - get_evaluations_for_cycle groups ops by op_id
  - find_recommendation_for_cycle returns the recommendation record
"""

from __future__ import annotations

import pytest

from src.audit import AuditStore
from src.audit.queries import (
    find_recommendation_for_cycle,
    get_cycle_events,
    get_decision_chain,
    get_evaluations_for_cycle,
    get_evidence_consumers,
)
from src.models.audit import AuditRecord


# ============================================================
# Fixture: build a synthetic cycle with a realistic event chain
# ============================================================
@pytest.fixture
def populated_cycle(store: AuditStore) -> tuple[str, dict[str, int]]:
    """Build one cycle with:
      cycle_started -> review_request -> thought -> tool_call (evidence)
                    -> observation (evidence) -> specialist_finding
                    -> evaluator_record -> recommendation -> cycle_completed

    Returns (cycle_id, dict mapping event-name -> row id) so tests can
    target specific records by name.
    """
    cid = store.start_cycle(application_id="app-08", trigger_type="test")
    ids: dict[str, int] = {}

    # Look up cycle_started id (record #1 in a fresh tmp DB)
    from sqlalchemy import text
    with store.engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM audit_records WHERE review_cycle_id = :c AND type = 'cycle_started'"),
            {"c": cid},
        ).fetchone()
        ids["cycle_started"] = int(row[0])

    # review_request — child of cycle_started
    ids["review_request"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["cycle_started"],
        category="decision", type="review_request", agent="input_harness",
        content={"application_id": "app-08", "trigger_source": "test"},
    ))

    # thought — child of review_request
    ids["thought"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["review_request"],
        category="decision", type="thought", agent="compute_analyst",
        content={"thought": "Should I look at CPU?"},
    ))

    # tool_call (evidence) — child of thought
    ids["tool_call"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["thought"],
        category="evidence", type="tool_call", agent="compute_analyst",
        content={"tool_name": "get_summary_statistics", "arguments": {"app_name": "app-08"}},
    ))

    # observation (evidence) — child of tool_call
    ids["observation"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["tool_call"],
        category="evidence", type="observation", agent="compute_analyst",
        content={"tool_name": "get_summary_statistics", "result": {"p95": 27.1}},
    ))

    # specialist_finding — child of thought; cites observation
    ids["specialist_finding"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["thought"],
        category="decision", type="specialist_finding", agent="compute_analyst",
        content={"specialist": "compute_analyst", "finding_type": "no_issue_found",
                 "evidence_refs": [ids["observation"]]},
    ))

    # evaluator_record — child of specialist_finding
    ids["evaluator_record"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["specialist_finding"],
        category="decision", type="evaluator_record", agent="cross_tier_evaluator",
        content={"synthesis": {"verdict": "no action"}, "evidence_refs": [ids["observation"]]},
    ))

    # recommendation — child of evaluator_record; cites observation
    ids["recommendation"] = store.add_event(AuditRecord(
        review_cycle_id=cid, parent_id=ids["evaluator_record"],
        category="decision", type="recommendation", agent="supervisor",
        content={"composite": {"scenario_id": "app-08", "specific_change": "No action"},
                 "evidence_refs": [ids["observation"]]},
    ))

    # cycle_completed
    ids["cycle_completed"] = store.complete_cycle(
        cid, final_status="completed", recommendation_record_id=ids["recommendation"],
    )

    return cid, ids


# ============================================================
# get_cycle_events
# ============================================================
def test_get_cycle_events_returns_all_in_order(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    cid, ids = populated_cycle
    events = get_cycle_events(store, cid)
    # 9 events: cycle_started + review_request + thought + tool_call +
    # observation + specialist_finding + evaluator_record + recommendation + cycle_completed
    assert len(events) == 9
    types = [e.type for e in events]
    assert types[0] == "cycle_started"
    assert types[-1] == "cycle_completed"


# ============================================================
# get_decision_chain (Report 1)
# ============================================================
def test_decision_chain_walks_back_from_recommendation(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    cid, ids = populated_cycle
    chain = get_decision_chain(store, ids["recommendation"])
    # The chain should include only decision-category records on the
    # path from recommendation back to cycle_started.
    types = [r.type for r in chain]
    # Chain reads from start (most recent) backward
    assert types[0] == "recommendation"
    assert "evaluator_record" in types
    assert "specialist_finding" in types
    assert "thought" in types
    assert "review_request" in types
    assert "cycle_started" in types
    # Evidence records (tool_call, observation) must NOT appear
    assert "tool_call" not in types
    assert "observation" not in types


# ============================================================
# get_evidence_consumers (Report 2)
# ============================================================
def test_evidence_consumers_finds_all_citers(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    cid, ids = populated_cycle
    obs_id = ids["observation"]
    consumers = get_evidence_consumers(store, obs_id)
    types = sorted({r.type for r in consumers})
    # The observation is cited by specialist_finding, evaluator_record,
    # and recommendation.
    assert "specialist_finding" in types
    assert "evaluator_record" in types
    assert "recommendation" in types


def test_evidence_consumers_no_false_positive_on_substring(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    """json_each (not LIKE) must not mis-match id 5 against ids
    15, 25, 50. This test catches a regression to the substring trap."""
    cid, ids = populated_cycle
    obs_id = ids["observation"]
    # Ids in this fixture are small (1..9), so substring-LIKE on id "5"
    # would mis-match nothing here. The deeper guarantee is structural:
    # json_each expands the JSON array into rows for exact-match join.
    consumers = get_evidence_consumers(store, obs_id)
    for c in consumers:
        refs = c.content.get("evidence_refs", [])
        assert obs_id in refs, f"Record id={c.id} returned but does not actually cite {obs_id}"


# ============================================================
# find_recommendation_for_cycle
# ============================================================
def test_find_recommendation_for_cycle(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    cid, ids = populated_cycle
    rec = find_recommendation_for_cycle(store, cid)
    assert rec is not None
    assert rec.type == "recommendation"
    assert rec.id == ids["recommendation"]


def test_find_recommendation_returns_none_for_empty_cycle(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")
    assert find_recommendation_for_cycle(store, cid) is None


# ============================================================
# get_evaluations_for_cycle
# ============================================================
def test_evaluations_for_cycle_grouped_by_op_id(
    store: AuditStore, populated_cycle: tuple[str, dict[str, int]],
) -> None:
    cid, ids = populated_cycle
    rec_id = ids["recommendation"]

    # Run two evaluations against the same recommendation
    op1 = store.evaluate_recommendation(
        target_cycle_id=cid, target_record_id=rec_id,
        judge_call={"provider": "openai", "model": "gpt-4o", "prompt": "v1"},
        score_one_result={"shape": {"passed": True}},
    )
    op2 = store.evaluate_recommendation(
        target_cycle_id=cid, target_record_id=rec_id,
        judge_call={"provider": "openai", "model": "gpt-4o", "prompt": "v2"},
        score_one_result={"shape": {"passed": True}},
    )

    groups = get_evaluations_for_cycle(store, cid)
    assert op1 in groups
    assert op2 in groups
    # Each op has the two-event chain (judge_call + evaluator_score)
    assert len(groups[op1]) == 2
    assert len(groups[op2]) == 2
    assert {r.type for r in groups[op1]} == {"judge_call", "evaluator_score"}
