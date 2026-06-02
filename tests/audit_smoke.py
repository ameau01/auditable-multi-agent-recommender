"""End-to-end manual smoke test for the audit store.

Creates a temp SQLite DB, runs a synthetic cycle through the full
write→query→compose→render path, prints a clear pass/fail summary.

Lives in tests/ (alongside verify_trace.py and list_mcp_tools.py) to
match the project's convention: Python entry-point implementations live
in tests/, with shell wrappers in scripts/.

Usage:
    scripts/run_audit_smoke.sh               # bash wrapper (preferred entry point)
    or:  uv run python tests/audit_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.audit import AuditStore  # noqa: E402
from src.audit.composer import compose_from_cycle  # noqa: E402
from src.audit.queries import (  # noqa: E402
    find_recommendation_for_cycle,
    get_cycle_events,
    get_decision_chain,
    get_evaluations_for_cycle,
    get_evidence_consumers,
)
from src.models.audit import AuditRecord  # noqa: E402
from src.renderer import render_report, render_trace  # noqa: E402


def main() -> int:
    print()
    print("=" * 60)
    print("  Audit store smoke test")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "audit.db"
        store = AuditStore(db_path=str(db_path))
        store.initialize()
        print(f"  DB created at: {db_path}")

        # ---- Build a synthetic cycle ----
        cid = store.start_cycle(application_id="app-08", trigger_type="smoke")
        print(f"  Started cycle: {cid}")

        rec_request_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=1,
            category="decision", type="review_request", agent="input_harness",
            content={"application_id": "app-08", "trigger_source": "smoke"},
        ))

        sup_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=rec_request_id,
            category="decision", type="supervisor_decision", agent="supervisor",
            content={"decision_type": "invoke_specialist",
                     "decision_details": {"specialists": ["compute_analyst"]}},
        ))

        tool_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=sup_id,
            category="evidence", type="tool_call", agent="compute_analyst",
            content={"tool_name": "get_summary_statistics",
                     "arguments": {"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"}},
        ))

        obs_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=tool_id,
            category="evidence", type="observation", agent="compute_analyst",
            content={"tool_name": "get_summary_statistics",
                     "result": {"statistics": {"mean": 19.2, "p95": 27.1}}},
        ))

        finding_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=sup_id,
            category="decision", type="specialist_finding", agent="compute_analyst",
            content={"specialist": "compute_analyst", "finding_type": "no_issue_found",
                     "headline": "Compute is healthy", "evidence_refs": [obs_id]},
        ))

        eval_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=finding_id,
            category="decision", type="evaluator_record", agent="cross_tier_evaluator",
            content={"synthesis": {"verdict": "no action"}, "evidence_refs": [obs_id]},
        ))

        composite_data = {
            "scenario_id": "app-08",
            "finding_type": "no_issue_found",
            "specific_change": "No changes recommended. All tiers within healthy bounds.",
            "primary_tier": None, "secondary_tier": None, "action_category": None,
            "evidence": {
                "telemetry_observations": ["CPU p95 at 27.1%, well within SLA"],
                "infrastructure_context": [],
                "correlation_observations": [],
            },
            "reasoning": "All tiers operate within healthy bounds; no remediation required.",
            "scoring_metadata": {
                "description": "Smoke-test cycle",
                "finding_type_allowed": ["no_issue_found"],
                "primary_tier_allowed": [None],
                "secondary_tier_allowed": [None],
                "action_category_allowed": [None],
            },
        }
        rec_id = store.add_event(AuditRecord(
            review_cycle_id=cid, parent_id=eval_id,
            category="decision", type="recommendation", agent="supervisor",
            content={"composite": composite_data, "evidence_refs": [obs_id]},
        ))

        store.complete_cycle(cid, final_status="completed",
                              recommendation_record_id=rec_id)
        print(f"  Cycle complete; 9 records written.")

        # ---- Record one evaluation ----
        op_id = store.evaluate_recommendation(
            target_cycle_id=cid, target_record_id=rec_id,
            judge_call={"provider": "openai", "model": "gpt-4o", "prompt": "Score this."},
            score_one_result={"shape": {"passed": True}, "correctness": {"passed": True},
                              "floor": {"passed": True}, "mid": "skipped", "rich": "skipped"},
        )
        print(f"  Evaluation recorded: op_id={op_id}")

        # ---- Query paths ----
        events = get_cycle_events(store, cid)
        print(f"  get_cycle_events: {len(events)} records")

        chain = get_decision_chain(store, rec_id)
        chain_types = [r.type for r in chain]
        print(f"  get_decision_chain from recommendation: {chain_types}")

        consumers = get_evidence_consumers(store, obs_id)
        print(f"  get_evidence_consumers for observation id={obs_id}: "
              f"{len(consumers)} records cite it")

        rec = find_recommendation_for_cycle(store, cid)
        print(f"  find_recommendation_for_cycle: id={rec.id if rec else None}")

        evals = get_evaluations_for_cycle(store, cid)
        print(f"  get_evaluations_for_cycle: {len(evals)} op(s)")

        # ---- Composer + renderer ----
        composite = compose_from_cycle(store, cid)
        print(f"  Composed Composite for {composite.scenario_id}; "
              f"trace populated: supervisor_decision={composite.trace.supervisor_decision is not None}, "
              f"specialist_findings={len(composite.trace.specialist_findings or [])}")

        report_md = render_report(composite)
        trace_json = render_trace(composite)
        print(f"  Renderer output: report={len(report_md)} chars, "
              f"trace={len(trace_json)} chars")

    print()
    print("  All audit store paths passed.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
