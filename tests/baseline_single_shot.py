"""Single-shot LLM baseline.

For each app: pre-fetch the same telemetry observations the orchestrated
system would gather via MCP tools, bundle them into ONE prompt, ask one
LLM call to produce a complete Recommendation in the project's Pydantic
shape. No specialists, no harnesses, no reconciliation — just "here is
all the data, give me an answer."

This is the "fair contrast" baseline for the README scorecard: same
inputs, same evaluator, fewer agents. If the orchestrated system's
scores aren't materially better than this baseline's, the multi-agent
architecture isn't earning its complexity.

Usage:
    python tests/baseline_single_shot.py \\
        --model haiku \\
        --apps app-08 \\
        --out-dir baseline-runs/haiku-20260605

Or via the bash wrapper that adds Stage 2 (scoring) + Stage 3 (summary):
    bash scripts/baseline_single_shot.sh --model haiku

CLI args:
    --model {haiku|sonnet|opus}   Maps to claude model strings.
    --apps APP[,APP,...]          Default: all 18 (app-01 through app-18).
    --out-dir DIR                 Default: baseline-runs/<model>-<ts>.
    --provider {anthropic|openai} Default: anthropic.

Output layout (mirrors integration_test_all.sh step1 structure):
    <out-dir>/
      00_generation.log         run config + timing + per-app pass/fail
                                (verbose; the polished single-file
                                 summary lives at 00_summary.txt and is
                                 written by tests/baseline_summarize.py
                                 after Stage 2 scoring completes)
      app-NN/
        recommendation.json     the LLM's Recommendation (Pydantic-valid)
        prompt.txt              the full prompt sent (for reproducibility)
        usage.json              token + cost metadata
        baseline.log            stdout/stderr from this scenario's call
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root on sys.path so `import src.*` resolves whether
# script is invoked as `python tests/baseline_single_shot.py` from
# project root or via `uv run python -m tests.baseline_single_shot`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.llm_client import make_llm_client  # noqa: E402
from src.agents.mcp_adapter import call_tool  # noqa: E402
from src.common.init import ensure_env_loaded  # noqa: E402
from src.models.composite import Recommendation  # noqa: E402


# Model short-name -> claude model string. Mirrors the project-wide
# convention used in docs/decisions.md.
MODEL_MAP = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}

# Default app set — all 18 in the dataset.
ALL_APPS = [f"app-{i:02d}" for i in range(1, 19)]


# ============================================================
# Telemetry pre-fetch
# ============================================================
def _safe_call(tool_name: str, **arguments: Any) -> dict[str, Any] | None:
    """Wrap call_tool so a single tool failure doesn't kill the whole run.

    Returns None on failure (so the prompt-builder can omit the section
    cleanly) and prints the error to stderr for diagnostic visibility.

    Suppresses the "WARN:" prefix for *expected* tool errors — specifically
    `unknown_tier`, which is how the agent legitimately discovers "this tier
    doesn't exist in this scenario." Single-shot has no System Mapper to
    tell it the topology upfront, so it gropes across all four tiers and
    expects ~3 unknown_tier responses per app. Surfacing those as WARN
    overstated the noise; they're informational, not failures.

    Unexpected errors (schema mismatches, network failures, real bugs) still
    log as WARN — the suppression is keyword-specific.
    """
    try:
        return call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # Expected tool error: single-shot agent groping for absent tiers.
        # Don't surface as WARN — it's information the agent uses, not a fault.
        if "unknown_tier" in msg:
            return None
        print(
            f"  WARN: {tool_name}({arguments}) failed: {exc}",
            file=sys.stderr,
        )
        return None


def fetch_app_context(app: str) -> dict[str, Any]:
    """Pull the bundle of telemetry the orchestrated system would gather.

    Order matters only for readability of the prompt; the bundle is
    rendered as labeled sections. Each call uses the same MCP tools the
    orchestrated agents use, so the baseline sees the same inputs.
    """
    ctx: dict[str, Any] = {}

    # Shared context (every app)
    ctx["business_context"] = _safe_call(
        "get_business_context", app_name=app,
    )
    ctx["sla_target"] = _safe_call("get_sla_target", app_name=app)
    ctx["monthly_cost"] = _safe_call("get_monthly_cost", app_name=app)
    ctx["before_after_evidence"] = _safe_call(
        "get_before_after_evidence", app_name=app,
    )
    ctx["correlation_evidence"] = _safe_call(
        "get_correlation_evidence", app_name=app,
    )

    # Per-tier summary stats on the canonical "is this tier loaded?" metric.
    # The Specialists call many more metrics — we give the baseline the
    # most-cited ones, same as a generalist analyst would pull.
    ctx["compute_cpu_p95"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="compute",
        metric="cpu_p95",
    )
    ctx["compute_memory_p95"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="compute",
        metric="memory_p95",
    )
    ctx["compute_latency_p95"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="compute",
        metric="application_p95_latency_ms",
    )
    ctx["compute_config"] = _safe_call(
        "get_configuration", app_name=app, tier="compute",
    )

    ctx["database_query_latency"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="database",
        metric="db_query_p95_latency_ms",
    )
    ctx["database_cache_hit_ratio"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="database",
        metric="db_cache_hit_ratio",
    )
    ctx["database_config"] = _safe_call(
        "get_configuration", app_name=app, tier="database",
    )

    ctx["cache_hit_ratio"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="cache",
        metric="cache_hit_ratio",
    )
    ctx["cache_config"] = _safe_call(
        "get_configuration", app_name=app, tier="cache",
    )

    ctx["network_throughput"] = _safe_call(
        "get_summary_statistics", app_name=app, tier="network",
        metric="network_throughput_p95",
    )
    ctx["network_config"] = _safe_call(
        "get_configuration", app_name=app, tier="network",
    )

    return ctx


# ============================================================
# Prompt construction
# ============================================================
RECOMMENDATION_SCHEMA_HINT = """
Produce a SINGLE JSON object with this shape (extra fields allowed,
but populate as many of these as the telemetry supports):

{
  "scenario_id": "<the NN digits from the app id, e.g. '08' for app-08>",
  "finding_type": "issue_found" | "no_issue_found" | "diagnostic_deferral",
  "primary_tier": "compute" | "database" | "cache" | "network" | "deferred",
  "secondary_tier": "<same enum as primary, or null>",
  "action_category": "<one of: scaling_policy_change, instance_rightsize,
                       query_cache_optimization, cache_capacity_adjustment,
                       schema_or_index_change, network_capacity_change,
                       no_action, defer_until_more_data, ...>",
  "headline": "<one-line summary of the recommendation>",
  "specific_change": "<the concrete operational change to make,
                       with magnitudes (e.g. 'reduce db.r5.4xlarge to
                       db.r5.2xlarge', '+2 cache nodes', etc.)>",
  "evidence": {
    "telemetry_observations": ["<bullet citing concrete numbers>", ...],
    "infrastructure_context": ["<bullet citing tier/config/cost>", ...],
    "correlation_observations": ["<bullet on cross-tier links, or []>"]
  },
  "reasoning": "<2-4 sentence chain from telemetry to action>",
  "projected_state": {
    "cpu_p95_pct_estimate": "<range, e.g. '40-60%'>",
    "memory_p95_pct_estimate": "<range>",
    "latency_p95_ms_estimate": "<range>",
    "sla_availability_preserved": true | false,
    "notes": "<one line>"
  },
  "cost_impact": {
    "current_monthly_usd": <float>,
    "projected_monthly_usd": <float>,
    "savings_monthly_usd": <float>,
    "savings_pct": <float>,
    "notes": "<one line>"
  },
  "risk_assessment": {
    "primary_risk": "<one line>",
    "mitigation": "<one line>",
    "rollback": "<one line>"
  }
}

If the telemetry shows no real issue (utilizations within SLA, no
correlation signals), return finding_type='no_issue_found' with
action_category='no_action' and explain in reasoning. If data is
ambiguous, use 'diagnostic_deferral' + 'defer_until_more_data'.
"""


def build_prompt(app: str, ctx: dict[str, Any]) -> str:
    """Render the telemetry bundle + ask for one Recommendation."""
    sections = [
        f"# Single-shot recommendation task for {app}",
        "",
        "You are a senior reliability engineer. Below is the full",
        "telemetry bundle for one application across its four tiers",
        "(compute, database, cache, network). Your job: read the data",
        "and produce ONE optimization recommendation (or restraint, or",
        "deferral) in the JSON shape specified at the end.",
        "",
        "Do not invent telemetry. Cite only numbers present in the",
        "bundle below. If a section is null/empty, treat that tier as",
        "either healthy on the dimensions you couldn't measure or as",
        "deferral-worthy if the missing data is load-bearing.",
        "",
        "## Telemetry bundle",
        "",
    ]
    for label, body in ctx.items():
        sections.append(f"### {label}")
        if body is None:
            sections.append("(unavailable)")
        else:
            sections.append("```json")
            sections.append(json.dumps(body, indent=2, default=str))
            sections.append("```")
        sections.append("")

    sections.append("## Output format")
    sections.append(RECOMMENDATION_SCHEMA_HINT)
    sections.append("")
    sections.append("Reply with ONLY the JSON object. No prose, no fences.")
    return "\n".join(sections)


# ============================================================
# LLM call + parsing
# ============================================================
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first balanced JSON object out of the LLM response.

    The prompt asks for JSON only, but models sometimes wrap in fences
    or prefix with prose. This helper survives both.
    """
    if isinstance(text, list):
        # Some langchain providers return content as a list of segments.
        text = "".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg)
            for seg in text
        )
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json or ``` fence and its closing ```.
        fence_end = text.find("\n")
        text = text[fence_end + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Try direct parse first; fall back to greedy-match.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(
            f"could not find JSON object in response:\n{text[:500]}"
        )
    return json.loads(match.group(0))


def run_one(app: str, model_str: str, provider: str) -> dict[str, Any]:
    """Pull telemetry → build prompt → call LLM → parse → validate.

    Returns a dict with keys: recommendation, prompt, usage, elapsed_s.
    Raises on any failure so the per-app loop can record it.
    """
    t0 = time.time()

    ctx = fetch_app_context(app)
    prompt = build_prompt(app, ctx)

    client = make_llm_client(provider, model_str)
    response = client.complete(
        [{"role": "user", "content": prompt}],
        model=model_str,
    )

    raw_text = response.get("content", "")
    rec_dict = extract_json(raw_text)

    # Pydantic validation: Recommendation is lenient (only scenario_id +
    # specific_change required). Validate so the scorer doesn't choke
    # on a structurally-broken response.
    Recommendation.model_validate(rec_dict)

    return {
        "recommendation": rec_dict,
        "prompt": prompt,
        "usage": response.get("usage"),
        "elapsed_s": round(time.time() - t0, 2),
        "raw_content_excerpt": (
            raw_text[:500] if isinstance(raw_text, str) else str(raw_text)[:500]
        ),
    }


# ============================================================
# Driver
# ============================================================
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single-shot LLM baseline for the 18-scenario eval set.",
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=sorted(MODEL_MAP.keys()),
        help="Model tier. Maps to claude model string via MODEL_MAP.",
    )
    parser.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "openai"],
        help="LLM provider. Default: anthropic.",
    )
    parser.add_argument(
        "--apps",
        default=",".join(ALL_APPS),
        help="Comma-separated app-NN list. Default: all 18.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Default: baseline-runs/<model>-<UTC ts>.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_env_loaded()

    model_str = MODEL_MAP[args.model]
    apps = [a.strip() for a in args.apps.split(",") if a.strip()]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or PROJECT_ROOT / f"baseline-runs/{args.model}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Single-shot baseline | model={args.model} ({model_str}) "
          f"| provider={args.provider} | apps={len(apps)} | out={out_dir}")
    print("=" * 60)

    summary_lines = [
        "# Generation execution log (Stage 1)",
        f"started_at: {ts}Z",
        f"model: {args.model}  ({model_str})",
        f"provider: {args.provider}",
        f"apps: {apps}",
        f"out_dir: {out_dir}",
        "",
        "per_app:",
    ]
    n_pass = 0
    n_fail = 0
    total_elapsed = 0.0

    for app in apps:
        print(f"\n[{app}] running...")
        app_dir = out_dir / app
        app_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = run_one(app, model_str, args.provider)
            (app_dir / "recommendation.json").write_text(
                json.dumps(result["recommendation"], indent=2)
            )
            (app_dir / "prompt.txt").write_text(result["prompt"])
            (app_dir / "usage.json").write_text(
                json.dumps(
                    {
                        "elapsed_s": result["elapsed_s"],
                        "usage": result["usage"],
                    },
                    indent=2,
                    default=str,
                )
            )
            print(f"  ✓ {app} done in {result['elapsed_s']}s "
                  f"(usage={result['usage']})")
            summary_lines.append(
                f"  - {app}: PASS  elapsed={result['elapsed_s']}s"
            )
            n_pass += 1
            total_elapsed += result["elapsed_s"]
        except Exception as exc:  # noqa: BLE001
            err_text = traceback.format_exc()
            (app_dir / "baseline.log").write_text(err_text)
            print(f"  ✗ {app} FAILED: {exc}")
            summary_lines.append(
                f"  - {app}: FAIL  reason={type(exc).__name__}: {exc}"
            )
            n_fail += 1

    summary_lines += [
        "",
        "totals:",
        f"  pass: {n_pass}",
        f"  fail: {n_fail}",
        f"  elapsed_s: {round(total_elapsed, 2)}",
    ]
    (out_dir / "00_generation.log").write_text("\n".join(summary_lines))

    print("\n" + "=" * 60)
    print(f"Done. pass={n_pass} fail={n_fail} "
          f"elapsed={round(total_elapsed, 1)}s")
    print(f"Output: {out_dir}")
    print(f"Generation log: {out_dir}/00_generation.log")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
