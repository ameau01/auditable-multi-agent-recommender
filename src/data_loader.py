"""Loads the synthesized cloud-optimization dataset from Hugging Face Hub.

The dataset is published at
    https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations

This module fetches it at runtime via huggingface_hub.snapshot_download and
caches it inside the repo at <repo-root>/.hf_cache/. First run downloads
about 12 MB. Subsequent runs use the cache and do not hit the network.

The project-local cache (rather than the HF default at ~/.cache/huggingface/)
keeps the repo self-contained: a reviewer can clone, run, and inspect the
downloaded artifacts without anything escaping into their home directory,
and `rm -rf .hf_cache` is a clean reset. Power users who already maintain
a shared HF cache can override the location by setting the HF_HOME env var
in their shell before running anything in this project.

Pin DATASET_REVISION to a specific commit hash to lock the version for
reproducibility. Leave it as None to always pull the latest commit on the
main branch. Pin it once the eval results stabilize.

Public functions:

    get_dataset_path() -> Path
        Path to the local cache directory holding the dataset snapshot.
        Triggers a download on first call.

    list_scenario_ids() -> list[str]
        Returns ['01', '02', ..., '18'].

    load_scenario(scenario_id) -> dict
        Loads one scenario's eight files into a dict.

    load_all_scenarios() -> list[dict]
        Loads all 18 scenarios. Ordered by scenario_id.

    get_dataset_revision() -> str
        The commit hash currently cached. Useful for logging or pinning.

Quick smoke test:
    python -m src.data_loader
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from huggingface_hub import snapshot_download

# Pick up HF_HOME (and any other config) from .env if the user has copied
# .env.example -> .env. Idempotent; safe to call at import time.
load_dotenv()


DATASET_REPO = "ameau01/synthesized-cloud-optimization-recommendations"

# Set to a commit hash (e.g. "ae3b650f57eabf679cff98234c6d1ae3bbf1d242") to
# pin the dataset version. Leave as None to always fetch main. Once your
# baseline numbers stabilize, pin this so a future re-run reproduces the
# exact same dataset state.
DATASET_REVISION: str | None = None


# data_loader.py lives at <repo-root>/src/data_loader.py, so parent.parent
# is the repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_cache_dir() -> Path:
    """Return the cache directory for the HF dataset snapshot.

    Configuration model (kept simple on purpose for a clone-and-run
    reviewer experience):

      - Default: <repo-root>/.hf_cache/  (project-local; gitignored).
      - Override: set HF_HOME in .env (or in your shell) to any path you
        prefer. Relative paths resolve against the project root, so
        `HF_HOME=.hf_cache` in .env behaves the same regardless of which
        directory you invoke `uv run` from.
      - Absolute paths are used as-is, useful for power users who already
        keep a shared Hugging Face cache somewhere outside the repo.

    HF_HOME is treated as the direct cache directory for this project
    (no `/hub` suffix appended), so a reviewer who runs the demo sees
    `.hf_cache/datasets--ameau01--.../...` rather than a deeper
    `.hf_cache/hub/datasets--.../`.
    """
    raw = os.environ.get("HF_HOME", ".hf_cache")
    p = Path(raw)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


HF_CACHE_DIR: Path = _resolve_cache_dir()


@lru_cache(maxsize=1)
def get_dataset_path() -> Path:
    """Return the local path to the dataset snapshot.

    On first call, fetches the dataset from Hugging Face Hub and caches it
    at HF_CACHE_DIR (defaults to <repo-root>/.hf_cache/). On subsequent
    calls, returns the cached path immediately. The cache survives across
    sessions.
    """
    path = snapshot_download(
        repo_id=DATASET_REPO,
        repo_type="dataset",
        revision=DATASET_REVISION,
        cache_dir=str(HF_CACHE_DIR),
    )
    return Path(path)


def list_scenario_ids() -> list[str]:
    """Return the 18 scenario IDs as zero-padded strings: '01' through '18'."""
    scenarios_dir = get_dataset_path() / "scenarios"
    return sorted(p.name for p in scenarios_dir.iterdir() if p.is_dir())


def load_scenario(scenario_id: str) -> dict[str, Any]:
    """Load one scenario's eight files into a dict.

    Args:
        scenario_id: zero-padded scenario ID, for example '01' or '17'.

    Returns:
        A dict with keys:
            scenario_id, metadata, compute_telemetry, database_telemetry,
            cache_telemetry, network_telemetry, correlation_evidence,
            terraform, handcrafted_recommendation.

    Raises:
        FileNotFoundError: if the scenario directory does not exist.
    """
    root = get_dataset_path() / "scenarios" / scenario_id
    if not root.exists():
        raise FileNotFoundError(f"Scenario {scenario_id} not found at {root}")

    return {
        "scenario_id": scenario_id,
        "metadata": json.loads((root / "metadata.json").read_text()),
        "compute_telemetry": json.loads((root / "compute_telemetry.json").read_text()),
        "database_telemetry": json.loads((root / "database_telemetry.json").read_text()),
        "cache_telemetry": json.loads((root / "cache_telemetry.json").read_text()),
        "network_telemetry": json.loads((root / "network_telemetry.json").read_text()),
        "correlation_evidence": json.loads((root / "correlation_evidence.json").read_text()),
        "terraform": (root / "main.tf").read_text(),
        "handcrafted_recommendation": json.loads(
            (root / "handcrafted_recommendation.json").read_text()
        ),
    }


def load_all_scenarios() -> list[dict[str, Any]]:
    """Load all 18 scenarios. Returns a list ordered by scenario_id."""
    return [load_scenario(sid) for sid in list_scenario_ids()]


def get_dataset_revision() -> str:
    """Return the commit hash of the currently cached snapshot.

    The snapshot_download result is a path like
        .../snapshots/<commit-hash>/
    so the last directory name is the revision.
    """
    return get_dataset_path().name


if __name__ == "__main__":
    # Quick smoke test: download the dataset and report basic facts.
    path = get_dataset_path()
    sids = list_scenario_ids()
    sample = load_scenario(sids[0])

    print(f"Dataset repo:     {DATASET_REPO}")
    print(f"Cache directory:  {HF_CACHE_DIR}")
    print(f"Dataset path:     {path}")
    print(f"Commit revision:  {get_dataset_revision()}")
    print(f"Scenarios found:  {len(sids)} ({sids[0]} through {sids[-1]})")
    print(f"First scenario:   {sample['metadata'].get('scenario_name', '?')}")
    print(f"First gold action: "
          f"{sample['handcrafted_recommendation'].get('action_category', '?')}")
