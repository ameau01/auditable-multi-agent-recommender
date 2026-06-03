"""Load and validate per-scenario scoring rules.

The scoring-rules dict (the `scoring_metadata` block inside a composite,
or the legacy flat `scoring_rules/NN/rules.json` shape) defines:
  - finding_type_allowed, primary_tier_allowed, secondary_tier_allowed,
    action_category_allowed: per-scenario enum allowed lists (each value
    must be in the corresponding universe defined in enums.py)
  - must_cite_fixture: scenario metadata fixture the prose must cite (Rich check)
  - short_circuit: marker that no-action scenarios bypass Mid + Rich
  - description, *_rationale: human-readable documentation

This module loads rules from disk and validates them against the enum
universes. Two load modes:

  load_rules_file(path)         single rules.json (ad-hoc scenarios)
  load_rules_dir(dir)           whole NN/rules.json directory tree

Validation runs at load time. Drift between rules files and enums.py
fails loud (raises ValueError) rather than silently producing wrong scores.

The Scorer's primary loader is now `Scorer.from_eval_set_dir()`,
which reads composites and calls `validate_rules()` on each one's
`to_rules_dict()` output. The `load_rules_file` / `load_rules_dir`
functions remain available for tests and ad-hoc rules dicts.
"""

from __future__ import annotations

import json
from pathlib import Path

from .enums import universe_for


# ============================================================
# Public API: load and validate
# ============================================================
def load_rules_file(path: Path | str) -> dict:
    """Load and validate a single rules.json file.

    Returns the parsed dict. Raises ValueError if any *_allowed value is
    not in the corresponding enum universe (per enums.py).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"rules file not found: {path}")
    rules = json.loads(path.read_text())
    validate_rules(rules, source=str(path))
    return rules


def load_rules_dir(rules_dir: Path | str) -> dict[str, dict]:
    """Load and validate every NN/rules.json under the given directory.

    Returns a dict keyed by scenario id (NN, two-digit string), where each
    value is the parsed and validated rules dict.

    Used by the Scorer class to preload all per-scenario rules at init
    so subsequent scoring calls don't re-read JSON from disk.
    """
    rules_dir = Path(rules_dir)
    if not rules_dir.exists():
        raise FileNotFoundError(f"rules directory not found: {rules_dir}")
    out: dict[str, dict] = {}
    for sid_dir in sorted(rules_dir.iterdir()):
        if not sid_dir.is_dir():
            continue
        sid = sid_dir.name
        rules_path = sid_dir / "rules.json"
        if not rules_path.exists():
            continue
        out[sid] = load_rules_file(rules_path)
    if not out:
        raise FileNotFoundError(
            f"no NN/rules.json files found under {rules_dir}"
        )
    return out


def validate_rules(rules: dict, source: str | None = None) -> None:
    """Confirm every *_allowed value is in the corresponding enum universe.

    Raises ValueError on the first mismatch, naming the field and the bad
    value. `source` is included in error messages for traceability.

    Does not validate keyword groups (those are scenario-specific strings,
    no universe to check against) or short_circuit (it's a metadata flag).
    """
    src_tag = f" [from {source}]" if source else ""
    for field_name in ("finding_type", "primary_tier",
                       "secondary_tier", "action_category"):
        allowed_key = f"{field_name}_allowed"
        if allowed_key not in rules:
            continue
        allowed_list = rules[allowed_key]
        if not isinstance(allowed_list, list):
            raise ValueError(
                f"{allowed_key}{src_tag} must be a list, "
                f"got {type(allowed_list).__name__}"
            )
        universe = universe_for(field_name)
        for value in allowed_list:
            if value not in universe:
                raise ValueError(
                    f"{allowed_key}{src_tag} contains {value!r} which is not "
                    f"in the {field_name} enum universe. "
                    f"Known values: {sorted(str(v) for v in universe)}"
                )


# ============================================================
# Convenience predicates
# ============================================================
def is_short_circuit_scenario(rules: dict) -> bool:
    """True if this scenario bypasses Mid + Rich under the short-circuit rule.

    Short-circuited scenarios have a `short_circuit: {applies: true, ...}`
    block in their rules. See richness.py for what the bypass does.
    """
    sc = rules.get("short_circuit", {})
    return isinstance(sc, dict) and bool(sc.get("applies", False))
