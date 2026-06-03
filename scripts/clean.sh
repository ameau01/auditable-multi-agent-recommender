#!/usr/bin/env bash
# Wipe local cached state. Destructive — requires a flag.
#
# Usage:
#   scripts/clean.sh [--audit] [--hf] [--all]
#
# Flags:
#   --audit             Delete .audit_db/audit.db (and the dir if empty).
#   --hf                Delete .hf_cache/ (next run re-downloads ~12 MB).
#   --all               Both of the above.
#   -h, --help          Show this help message and exit.
#
# Example:
#   scripts/clean.sh --audit

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

if [[ $# -eq 0 ]]; then
  cat <<EOF >&2
scripts/clean.sh — wipe local cached state.

Usage:
  scripts/clean.sh --audit    # nuke the audit DB
  scripts/clean.sh --hf       # nuke the HF dataset cache
  scripts/clean.sh --all      # both

Pass at least one flag — this script does nothing without one.
EOF
  exit 2
fi

DO_AUDIT=false
DO_HF=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --audit) DO_AUDIT=true; shift ;;
    --hf)    DO_HF=true;    shift ;;
    --all)   DO_AUDIT=true; DO_HF=true; shift ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

PY_SNIPPET='
import sys
from src.common.cleanup import wipe_audit_db, wipe_hf_cache
removed = []
if "audit" in sys.argv:
    removed += wipe_audit_db()
if "hf" in sys.argv:
    removed += wipe_hf_cache()
if not removed:
    print("(nothing to wipe — paths already absent)")
else:
    for p in removed:
        print(f"removed: {p}")
'

ARGS=()
$DO_AUDIT && ARGS+=("audit")
$DO_HF    && ARGS+=("hf")

exec uv run python -c "$PY_SNIPPET" "${ARGS[@]}"
