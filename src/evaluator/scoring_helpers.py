"""Shared helpers used by the scoring layers.

Lives outside the per-layer measure modules because more than one of them
uses these functions. Following the package convention that everything
under src/evaluator/ is implementation detail by location, this module
does not use the `_helpers.py` leading-underscore prefix.

Reserve the `_xxx.py` filename pattern for files that are MORE private
than their neighbors (rare). For this project, locating a file under
src/evaluator/ is sufficient signal that external callers should reach
it through the Scorer class or the tiers.py facade, not directly.
"""

from __future__ import annotations


def prediction_text(prediction: dict) -> str:
    """Concatenate the prediction's prose fields into one lowercase string.

    Used by the scoring layers:
      - mid_measure.score_mid (action_keywords + multi_tier_evidence checks)
      - richness_measure.score_rich (fixture_citation substring search)

    Includes specific_change, reasoning, and every string bullet across
    the three evidence categories. Returns the joined text lowercased so
    keyword/identifier matching is case-insensitive.
    """
    parts = [
        prediction.get("specific_change") or "",
        prediction.get("reasoning") or "",
    ]
    ev = prediction.get("evidence") or {}
    if isinstance(ev, dict):
        for cat in ("telemetry_observations", "infrastructure_context",
                    "correlation_observations"):
            bullets = ev.get(cat) or []
            if isinstance(bullets, list):
                for b in bullets:
                    if isinstance(b, str):
                        parts.append(b)
    return " ".join(parts).lower()
