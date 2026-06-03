"""CLI for inspecting the audit trail.

Run via:

    python -m src.audit.inspect <subcommand> [app-name] [cycle_id]

Subcommands:

    list                    Catalog of (app-name, cycle_id) pairs in the DB.
    show [APP] [CYCLE]      Raw dump of audit_records + harness_trail rows.
    trace [APP] [CYCLE]     Structured trace. `--type` takes a comma-
                            separated subset of {decisions, evidence}.
                            Default: decisions,evidence (both).

Flags that apply to `show` and `trace`:

    --content               Dump the full JSON content column for each row
                            instead of the per-row summary.

Resolution rules (apply to `show` and `trace`):

    no args                 → most recent cycle in the DB
    APP only                → most recent cycle for that app
    APP and CYCLE           → that specific cycle, after confirming it
                              belongs to APP (mismatch is a hard error)

There is no "latest" magic word. The default *is* most-recent — typing
the word would be redundant.

The `--list` flag on `show` or `trace` is an alias for the `list`
subcommand for users who want to discover what's available without
remembering a second verb.

Output is plain text. Add `--json` to any subcommand for machine output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from sqlalchemy import select, text

from ..common.init import get_audit_store
from ..models.audit import AuditRecord
from .queries import (
    get_cycle_events,
    get_harness_events_for_cycle,
    get_rejected_tool_calls_for_cycle,
)
from .schema import audit_records
from .store import AuditStore


# Validate APP arg shape: app-NN.
_APP_NAME_RE = re.compile(r"^app-\d{2}$")


# ============================================================
# Resolvers
# ============================================================
def _resolve_target(
    store: AuditStore,
    app: str | None,
    cycle: str | None,
) -> str:
    """Resolve a (app, cycle) pair into a concrete cycle_id.

    Logic:
      - cycle given, app given : confirm cycle belongs to app, return cycle.
      - cycle given, no app    : just return cycle (with sanity-check it exists).
      - no cycle, app given    : return the most recent cycle for app.
      - neither                : return the most recent cycle in the DB.
    """
    if cycle is not None:
        with store.engine.connect() as conn:
            row = conn.execute(
                select(audit_records.c.cycle_id, audit_records.c.content)
                .where(
                    (audit_records.c.cycle_id == cycle)
                    & (audit_records.c.type == "cycle_started")
                )
                .limit(1)
            ).fetchone()
        if row is None:
            print(f"ERROR: no cycle with id {cycle!r}.", file=sys.stderr)
            raise SystemExit(2)
        if app is not None:
            content = _json_loads(row[1])
            actual_app = content.get("application_id")
            if actual_app != app:
                print(
                    f"ERROR: cycle {cycle!r} belongs to {actual_app!r}, "
                    f"not {app!r}.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
        return cycle

    # No explicit cycle. Find the most recent cycle (filtered by app if given).
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(
                audit_records.c.cycle_id,
                audit_records.c.content,
            )
            .where(audit_records.c.type == "cycle_started")
            .order_by(audit_records.c.id.desc())
        ).fetchall()
    candidates: list[str] = []
    for cid, content_raw in rows:
        content = _json_loads(content_raw)
        if app is None or content.get("application_id") == app:
            candidates.append(cid)
    if not candidates:
        if app is not None:
            print(
                f"ERROR: no cycles found for {app!r}. Run one first:\n"
                f"  scripts/run_agents.sh {app}",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: no cycles in the audit DB yet. Run one first:\n"
                "  scripts/run_agents.sh app-08",
                file=sys.stderr,
            )
        raise SystemExit(2)
    return candidates[0]


def _validate_app_name(app: str | None) -> None:
    """Reject malformed app-name args at the CLI surface so they don't
    leak into resolver logic. None is valid (means 'any app')."""
    if app is None:
        return
    if not _APP_NAME_RE.match(app):
        print(
            f"ERROR: {app!r} is not a valid app-name. Expected 'app-NN' "
            "(e.g. 'app-08').",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ============================================================
# list — catalog of (app, cycle_id) pairs
# ============================================================
def _cmd_list(
    store: AuditStore,
    *,
    app_filter: str | None,
    limit: int,
    as_json: bool,
) -> int:
    """List recent cycles. When app_filter is set, only that app's cycles."""
    sql = text(
        """
        SELECT
            r.cycle_id,
            r.timestamp AS started_at,
            r.content    AS started_content,
            (SELECT content FROM audit_records
              WHERE cycle_id = r.cycle_id
                AND type = 'cycle_completed'
              LIMIT 1) AS completed_content
        FROM audit_records r
        WHERE r.type = 'cycle_started'
        ORDER BY r.id DESC
        """
    )
    with store.engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()

    items: list[dict[str, Any]] = []
    for r in rows:
        started_content = _json_loads(r["started_content"])
        if app_filter and started_content.get("application_id") != app_filter:
            continue
        completed_content = _json_loads(r["completed_content"])
        items.append({
            "app": started_content.get("application_id"),
            "cycle_id": r["cycle_id"],
            "started_at": str(r["started_at"]),
            "trigger_type": started_content.get("trigger_type"),
            "status": (
                completed_content.get("final_status")
                if completed_content else "(in progress)"
            ),
        })
        if len(items) >= limit:
            break

    if as_json:
        print(json.dumps(items, indent=2, default=str))
        return 0

    if not items:
        msg = (
            f"(no cycles for {app_filter})" if app_filter
            else "(no cycles in the audit DB)"
        )
        print(msg)
        return 0

    print(f"{'APP':<8s} {'CYCLE_ID':<48s} {'STARTED':<20s} {'STATUS':<18s}")
    print("-" * 100)
    for it in items:
        print(
            f"{it['app'] or '-':<8s} "
            f"{it['cycle_id']:<48s} "
            f"{it['started_at']:<20s} "
            f"{it['status']:<18s}"
        )
    return 0


# ============================================================
# show — raw dump for a cycle
# ============================================================
def _cmd_show(
    store: AuditStore,
    cycle_id: str,
    *,
    as_json: bool,
    show_content: bool,
) -> int:
    events = get_cycle_events(store, cycle_id)
    h_events = get_harness_events_for_cycle(store, cycle_id)
    rejs = get_rejected_tool_calls_for_cycle(store, cycle_id)
    app_name = _extract_app_name(events)

    if as_json:
        out = {
            "cycle_id": cycle_id,
            "audit_records": [_audit_to_dict(e) for e in events],
            "harness_trail": [
                {
                    "id": h.id,
                    "harness": h.harness,
                    "type": h.type,
                    "verdict": h.verdict,
                    "related_event_id": h.related_event_id,
                    "content": h.content,
                }
                for h in h_events
            ],
            "rejected_tool_calls": [
                {"id": r.id, "content": r.content} for r in rejs
            ],
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    header = f"=== cycle {cycle_id}"
    if app_name:
        header += f"  app={app_name}"
    header += " ==="
    print(header)
    print(f"\naudit_records ({len(events)})")
    if show_content:
        for e in events:
            print(
                f"\n[{e.id}] type={e.type} agent={e.agent or '-'} "
                f"parent_id={e.parent_id or '-'}"
            )
            print(_format_content(e.content))
    else:
        print(f"{'ID':>4s}  {'TYPE':<25s} {'AGENT':<22s} EXTRA")
        print("-" * 100)
        for e in events:
            print(f"{e.id:>4d}  {e.type:<25s} {e.agent or '-':<22s} {_audit_extra(e, app_name=app_name)}")

    print(f"\nharness_trail ({len(h_events)})")
    if show_content:
        for h in h_events:
            print(
                f"\n[{h.id}] harness={h.harness} type={h.type} "
                f"verdict={h.verdict} related_event_id={h.related_event_id or '-'}"
            )
            print(_format_content(h.content))
    else:
        print(f"{'ID':>4s}  {'HARNESS':<10s} {'TYPE':<28s} {'VERDICT':<10s} REL_ID")
        print("-" * 80)
        for h in h_events:
            print(
                f"{h.id:>4d}  {h.harness:<10s} {h.type:<28s} "
                f"{h.verdict:<10s} {h.related_event_id or '-'}"
            )

    if rejs:
        print(f"\nrejected tool calls ({len(rejs)}):")
        for r in rejs:
            c = r.content
            print(
                f"  {c.get('agent')} -> {c.get('tool_name')}: "
                f"{c.get('rejection_reason')}"
            )
    return 0


# ============================================================
# trace — structured view (decisions / evidence)
# ============================================================
_DECISION_TYPES = {
    "cycle_started", "cycle_completed", "review_request",
    "system_mapper_output", "supervisor_decision",
    "specialist_finding", "evaluator_record",
    "recommendation", "hitl_decision",
}
_EVIDENCE_TYPES = {
    "tool_call", "observation", "correlation_observation", "infrastructure_fact",
}


def _cmd_trace(
    store: AuditStore,
    cycle_id: str,
    *,
    types: set[str],
    as_json: bool,
    show_content: bool,
) -> int:
    events = get_cycle_events(store, cycle_id)
    h_events = get_harness_events_for_cycle(store, cycle_id)
    app_name = _extract_app_name(events)

    decisions = [e for e in events if e.type in _DECISION_TYPES]
    evidence = [e for e in events if e.type in _EVIDENCE_TYPES]

    if as_json:
        out: dict[str, Any] = {"cycle_id": cycle_id, "app": app_name}
        if "decisions" in types:
            out["decisions"] = [_audit_to_dict(e) for e in decisions]
        if "evidence" in types:
            out["evidence"] = [_audit_to_dict(e) for e in evidence]
        if "decisions" in types:
            out["harness_verdicts"] = [
                {"id": h.id, "harness": h.harness, "type": h.type,
                  "verdict": h.verdict}
                for h in h_events
            ]
        print(json.dumps(out, indent=2, default=str))
        return 0

    label = ",".join(sorted(types))
    header = f"=== cycle {cycle_id}"
    if app_name:
        header += f"  app={app_name}"
    header += f" — trace ({label}) ==="
    print(header)

    if "decisions" in types:
        print("\nDECISIONS (the spine of the trail)")
        if show_content:
            for e in decisions:
                print(
                    f"\n[{e.id}] type={e.type} agent={e.agent or '-'} "
                    f"parent_id={e.parent_id or '-'}"
                )
                print(_format_content(e.content))
        else:
            print(f"{'ID':>4s}  {'TYPE':<25s} {'AGENT':<22s} SUMMARY")
            print("-" * 100)
            for e in decisions:
                print(
                    f"{e.id:>4d}  {e.type:<25s} {e.agent or '-':<22s} "
                    f"{_decision_summary(e, app_name=app_name)}"
                )
        print("\nHARNESS VERDICTS")
        if show_content:
            for h in h_events:
                print(
                    f"\n[{h.id}] harness={h.harness} type={h.type} "
                    f"verdict={h.verdict} related_event_id={h.related_event_id or '-'}"
                )
                print(_format_content(h.content))
        else:
            print(f"{'ID':>4s}  {'HARNESS':<10s} {'TYPE':<28s} {'VERDICT':<10s} REL_ID")
            print("-" * 80)
            for h in h_events:
                print(
                    f"{h.id:>4d}  {h.harness:<10s} {h.type:<28s} "
                    f"{h.verdict:<10s} {h.related_event_id or '-'}"
                )

    if "evidence" in types:
        print("\nEVIDENCE (the leaves the decisions cite)")
        if show_content:
            for e in evidence:
                print(
                    f"\n[{e.id}] type={e.type} agent={e.agent or '-'} "
                    f"parent_id={e.parent_id or '-'}"
                )
                print(_format_content(e.content))
        else:
            print(f"{'ID':>4s}  {'TYPE':<25s} {'AGENT':<22s} SUMMARY")
            print("-" * 100)
            for e in evidence:
                print(
                    f"{e.id:>4d}  {e.type:<25s} {e.agent or '-':<22s} "
                    f"{_evidence_summary(e)}"
                )

    return 0


# ============================================================
# verify — invariant checks against the live audit DB
# ============================================================
def _cmd_verify(
    store: AuditStore,
    *,
    app_filter: str | None,
    as_json: bool,
) -> int:
    """Walk the audit DB and flag invariant violations.

    Current invariants:

      I1. **Every `cycle_started` has a matching `cycle_completed`.**
          The partial unique indexes guarantee at most one of each per
          cycle; this check catches the *missing* case — a `cycle_started`
          with no `cycle_completed` is either still running (acceptable
          right now if the process is live) or the process died mid-cycle.
          The verifier cannot distinguish those two from the DB alone,
          so it flags any unmatched start as a *potential* invariant
          violation. A renderer reading a flagged cycle can show
          "incomplete/crashed" instead of mistaking it for a normal run.

    Exit code:
      0 — no violations
      1 — at least one violation in the (optionally app-filtered) trail
    """
    sql = text(
        """
        SELECT
            r.cycle_id,
            r.timestamp AS started_at,
            r.content    AS started_content,
            (SELECT 1 FROM audit_records
              WHERE cycle_id = r.cycle_id
                AND type = 'cycle_completed'
              LIMIT 1) AS has_completed
        FROM audit_records r
        WHERE r.type = 'cycle_started'
        ORDER BY r.id ASC
        """
    )
    with store.engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()

    violations: list[dict[str, Any]] = []
    total = 0
    for r in rows:
        started_content = _json_loads(r["started_content"])
        app = started_content.get("application_id")
        if app_filter and app != app_filter:
            continue
        total += 1
        if not r["has_completed"]:
            violations.append({
                "cycle_id": r["cycle_id"],
                "app": app,
                "started_at": str(r["started_at"]),
                "violation": "cycle_started_without_completed",
                "note": (
                    "No cycle_completed row found for this cycle_id. "
                    "Either the process is still running, or it died "
                    "mid-cycle (truncated trail)."
                ),
            })

    if as_json:
        print(json.dumps({
            "checked_cycles": total,
            "app_filter": app_filter,
            "violations": violations,
        }, indent=2, default=str))
        return 0 if not violations else 1

    scope = f"app={app_filter}" if app_filter else "all apps"
    print(f"verify ({scope}): checked {total} cycle(s)")
    if not violations:
        print("  ✓ all cycles bracket cleanly (cycle_started → cycle_completed)")
        return 0

    print(f"  ✗ {len(violations)} invariant violation(s):")
    for v in violations:
        print(f"    [{v['app'] or '-'}] {v['cycle_id']}  started_at={v['started_at']}")
        print(f"        → {v['violation']}: {v['note']}")
    return 1


# ============================================================
# Formatting helpers
# ============================================================
def _audit_to_dict(e: AuditRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "parent_id": e.parent_id,
        "category": e.category,
        "type": e.type,
        "agent": e.agent,
        "content": e.content,
        "timestamp": str(e.timestamp) if e.timestamp else None,
    }


def _audit_extra(e: AuditRecord, app_name: str | None = None) -> str:
    """Render the SUMMARY column for one audit_records row.

    `app_name` (optional) is folded into the bookend rows — cycle_started
    and cycle_completed — so the trace is self-describing at a glance:
    a reader looking at just the first or last row can see which
    application the cycle is for, without having to scroll up to the
    trace header. The caller passes the value once, extracted from the
    cycle_started row's content.
    """
    c = e.content
    if e.type == "cycle_started":
        bits = [f"app={app_name}"] if app_name else []
        trigger = c.get("trigger_type")
        if trigger:
            bits.append(f"trigger={trigger}")
        return " ".join(bits)
    if e.type == "tool_call":
        return f"tool={c.get('tool_name')}"
    if e.type == "observation":
        err = c.get("error")
        return f"tool={c.get('tool_name')}" + (f" ERROR={err}" if err else "")
    if e.type == "cycle_completed":
        bits = []
        if app_name:
            bits.append(f"app={app_name}")
        bits.append(f"status={c.get('final_status')}")
        if c.get("failed_at_stage"):
            bits.append(f"stage={c.get('failed_at_stage')}")
        if c.get("failure_reason"):
            bits.append(f"reason={c.get('failure_reason')}")
        return " ".join(bits)
    if e.type == "system_mapper_output":
        return (
            f"tiers={c.get('tiers_detected')} -> "
            f"specialists={c.get('specialists_to_invoke')}"
        )
    if e.type == "supervisor_decision":
        return f"decision={c.get('decision_type')}"
    return ""


def _decision_summary(e: AuditRecord, app_name: str | None = None) -> str:
    return _audit_extra(e, app_name=app_name)


def _extract_app_name(events: list[AuditRecord]) -> str | None:
    """Pull the application_id off the cycle_started row, if present."""
    for ev in events:
        if ev.type == "cycle_started":
            return ev.content.get("application_id")
    return None


def _evidence_summary(e: AuditRecord) -> str:
    c = e.content
    if e.type == "tool_call":
        return f"{c.get('tool_name')}({c.get('arguments') or {}})"
    if e.type == "observation":
        if c.get("error"):
            return f"{c.get('tool_name')} → ERROR {c.get('error')}"
        result = c.get("result") or {}
        if isinstance(result, dict) and result:
            keys = list(result.keys())[:5]
            return f"{c.get('tool_name')} → {{{', '.join(keys)}, ...}}"
        return f"{c.get('tool_name')} → (empty)"
    return ""


_VALID_TRACE_TYPES: frozenset[str] = frozenset({"decisions", "evidence"})


def _parse_trace_types(raw: str) -> set[str]:
    """argparse `type=` converter for `--type`. Accepts a comma-separated
    list of trace kinds and returns the deduplicated set.

    The Literal-style "both" sentinel was removed in favor of explicit
    `decisions,evidence`. Invalid values raise argparse.ArgumentTypeError
    so the CLI surface stays strict; typos get caught at parse time, not
    silently produce empty output.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError(
            "--type cannot be empty. Use 'decisions', 'evidence', or both "
            "comma-separated (e.g. --type decisions,evidence)."
        )
    invalid = sorted(set(parts) - _VALID_TRACE_TYPES)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"--type got invalid value(s): {invalid}. "
            f"Valid values are {sorted(_VALID_TRACE_TYPES)}."
        )
    return set(parts)


def _format_content(content: Any) -> str:
    """Pretty-print a content payload as indented JSON for --content mode."""
    try:
        return json.dumps(content, indent=2, default=str, sort_keys=False)
    except (TypeError, ValueError):
        return repr(content)


def _json_loads(raw: Any) -> dict[str, Any]:
    """SQLite JSON columns come back as str on raw text() queries; as
    dict on Table-typed selects. This handles both."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


# ============================================================
# Argparse + dispatch
# ============================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.audit.inspect",
        description="Inspect the audit trail.",
        epilog=(
            "Resolution: no args = most recent cycle; APP only = most "
            "recent cycle for that app; APP CYCLE = that specific cycle, "
            "after confirming it belongs to APP."
        ),
    )
    parser.add_argument(
        "--db-path",
        help="Audit DB file path. Defaults to AUDIT_DB_PATH env or .audit_db/audit.db.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of plain text.",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser(
        "list",
        help="Catalog of (app, cycle_id) pairs in the audit DB.",
    )
    p_list.add_argument(
        "app", nargs="?", default=None,
        help="Optional app-NN filter; without it, list every app.",
    )
    p_list.add_argument("--limit", type=int, default=20)

    p_show = sub.add_parser(
        "show",
        help="Raw dump of one cycle's audit + harness rows.",
    )
    p_show.add_argument(
        "app", nargs="?", default=None,
        help="Optional app-NN. Omit to use the most recent cycle "
             "across all apps.",
    )
    p_show.add_argument(
        "cycle", nargs="?", default=None,
        help="Optional cycle_id. With APP, must belong to APP.",
    )
    p_show.add_argument(
        "--list", action="store_true",
        help="Skip the dump and print the cycle catalog instead.",
    )
    p_show.add_argument(
        "--content", action="store_true",
        help="Dump the JSON content column for each row instead of the summary.",
    )

    p_trace = sub.add_parser(
        "trace",
        help="Structured trace (decisions / evidence).",
    )
    p_trace.add_argument(
        "app", nargs="?", default=None,
        help="Optional app-NN. Omit for the most recent cycle.",
    )
    p_trace.add_argument(
        "cycle", nargs="?", default=None,
        help="Optional cycle_id. With APP, must belong to APP.",
    )
    p_trace.add_argument(
        "--list", action="store_true",
        help="Skip the trace and print the cycle catalog instead.",
    )
    p_trace.add_argument(
        "--type",
        dest="types",
        type=_parse_trace_types,
        default={"decisions", "evidence"},
        metavar="TYPE[,TYPE]",
        help=(
            "Which trace(s) to show, comma-separated. Values: "
            "'decisions', 'evidence' (matches audit_records.category). "
            "Default: 'decisions,evidence' (both)."
        ),
    )
    p_trace.add_argument(
        "--content", action="store_true",
        help="Dump the JSON content column for each row instead of the summary.",
    )

    p_verify = sub.add_parser(
        "verify",
        help="Audit-DB invariant checks. Today: every cycle_started has a "
             "matching cycle_completed (no truncated/crashed cycles).",
    )
    p_verify.add_argument(
        "app", nargs="?", default=None,
        help="Optional app-NN filter; without it, verify every app.",
    )

    args = parser.parse_args(argv)

    # Single bootstrap point — schema is CREATE TABLE IF NOT EXISTS, so a
    # fresh checkout produces a usable empty DB instead of an Operational-
    # Error when inspect is run before any cycle has been recorded.
    store = get_audit_store(db_path=args.db_path)

    _validate_app_name(args.app)

    # --list alias on show / trace: route to the list command instead.
    if args.cmd in ("show", "trace") and getattr(args, "list", False):
        return _cmd_list(
            store, app_filter=args.app, limit=20, as_json=args.json,
        )

    if args.cmd == "list":
        return _cmd_list(
            store, app_filter=args.app, limit=args.limit, as_json=args.json,
        )

    if args.cmd == "show":
        cid = _resolve_target(store, app=args.app, cycle=args.cycle)
        return _cmd_show(
            store, cid, as_json=args.json, show_content=args.content,
        )

    if args.cmd == "trace":
        cid = _resolve_target(store, app=args.app, cycle=args.cycle)
        return _cmd_trace(
            store, cid, types=args.types, as_json=args.json,
            show_content=args.content,
        )

    if args.cmd == "verify":
        return _cmd_verify(store, app_filter=args.app, as_json=args.json)

    parser.error(f"unknown command: {args.cmd}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
