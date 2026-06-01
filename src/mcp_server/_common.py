"""Shared helpers across the tool modules.

Two responsibilities:

1. **app_name -> scenario_id mapping.** External agents address scenarios
   as `app-NN`. The dataset uses bare two-digit ids `NN`. The mapping is
   the only indirection the MCP server adds (per docs/mcp-server.md).
2. **Tier validation.** A small helper that turns a `tier` parameter
   into the right telemetry key on the loaded scenario dict, raising a
   `ToolError` with a stable error code when the tier is unknown.

Tools call these helpers so error messages are consistent across the
catalog and the stable error codes (`unknown_app`, `unknown_metric`,
`unknown_tier`, `invalid_input`) are documented in one place.

Error model (per the FastMCP convention):

- `ToolError` is **agent-visible**. The string and code travel to the
  caller. Use this for malformed inputs.
- Plain Python exceptions raised inside a tool are masked by FastMCP
  with a generic internal-error message. Use plain exceptions for
  unexpected failures (file missing, schema mismatch in the dataset
  itself, etc.).
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from ..data_loader import list_scenario_ids, load_scenario


# Map a tier name to the key on the scenario dict that holds its telemetry.
_TIER_TO_TELEMETRY_KEY: dict[str, str] = {
    "compute":  "compute_telemetry",
    "database": "database_telemetry",
    "cache":    "cache_telemetry",
    "network":  "network_telemetry",
}

ALLOWED_TIERS: tuple[str, ...] = tuple(_TIER_TO_TELEMETRY_KEY.keys())


_APP_NAME_RE = re.compile(r"^app-(\d{2})$")


def app_name_to_scenario_id(app_name: str) -> str:
    """Validate and convert 'app-NN' -> 'NN'.

    Raises ToolError(code='invalid_input') if the format is wrong, or
    ToolError(code='unknown_app') if the scenario id doesn't exist in
    the dataset.
    """
    if not isinstance(app_name, str):
        raise ToolError(
            f"invalid_input: app_name must be a string like 'app-08', "
            f"got {type(app_name).__name__}"
        )
    m = _APP_NAME_RE.match(app_name)
    if not m:
        raise ToolError(
            f"invalid_input: app_name must match the pattern 'app-NN' "
            f"(two digits). Got: {app_name!r}"
        )
    sid = m.group(1)
    known = set(list_scenario_ids())
    if sid not in known:
        raise ToolError(
            f"unknown_app: {app_name!r} is not in the dataset. "
            f"Known apps: {', '.join('app-' + s for s in sorted(known))}"
        )
    return sid


def load_for_app(app_name: str) -> dict[str, Any]:
    """Resolve app_name to its scenario dict, raising ToolError on bad input."""
    return load_scenario(app_name_to_scenario_id(app_name))


def telemetry_records(scenario: dict[str, Any], tier: str) -> list[dict]:
    """Return the telemetry record list for a given tier of a loaded scenario.

    Raises ToolError(code='unknown_tier') for an unknown tier name or for
    a tier that exists in the catalog but is not present in this scenario
    (e.g. asking for cache telemetry on a scenario without a cache tier).
    """
    if tier not in _TIER_TO_TELEMETRY_KEY:
        raise ToolError(
            f"unknown_tier: tier must be one of {list(ALLOWED_TIERS)}, "
            f"got {tier!r}"
        )
    records = scenario.get(_TIER_TO_TELEMETRY_KEY[tier])
    if not records:
        raise ToolError(
            f"unknown_tier: tier {tier!r} is not present in this scenario "
            f"(the {tier}_telemetry array is empty or absent)"
        )
    return records
