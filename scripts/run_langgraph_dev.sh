#!/usr/bin/env bash
# Boot the LangGraph dev server + open Studio for the agent graph.
#
# Usage:
#   scripts/run_langgraph_dev.sh                    # default: port 2024, auto-open Studio
#   scripts/run_langgraph_dev.sh --port 3000        # override port
#   scripts/run_langgraph_dev.sh --no-browser       # don't auto-open Studio in a browser
#   scripts/run_langgraph_dev.sh --tunnel           # expose via public Cloudflare tunnel
#   scripts/run_langgraph_dev.sh --strict           # OPT-OUT: enable blockbuster sync I/O guard
#
# Flags (forwarded to `langgraph dev`):
#   --host HOST           Bind host (default 127.0.0.1).
#   --port PORT           Bind port (default 2024).
#   --no-reload           Disable code-change watchfiles auto-reload.
#   --no-browser          Don't auto-open Studio.
#   --tunnel              Public Cloudflare tunnel (useful for Safari / sharing).
#   --debug-port PORT     Wait for a debugger on this port before starting.
#   --strict              Opt OUT of the default --allow-blocking. LangSmith's
#                         autoinstrumentation does sync I/O on every trace
#                         flush, which the blockbuster middleware otherwise
#                         rejects. Only use --strict for hunting blocking calls.
#   -h, --help            Show this help message and exit.
#
# Reads langgraph.json at the repo root (graphs.agent ->
# src.agents.orchestrator:graph_factory). Studio opens at:
#   https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:<port>
#
# Stop with Ctrl-C. Reloads automatically on src/ changes unless --no-reload.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

# Fail fast if langgraph-cli isn't on the venv — clearer than the raw uv error.
if ! uv run python -c "import langgraph_cli" 2>/dev/null; then
  echo "ERROR: langgraph-cli not installed in this venv." >&2
  echo "  Run:  uv sync" >&2
  echo "  (it's in [dependency-groups].dev in pyproject.toml)" >&2
  exit 2
fi

# Fail fast if langgraph.json is missing.
if [[ ! -f langgraph.json ]]; then
  echo "ERROR: langgraph.json not found at $(pwd)." >&2
  echo "  Expected the manifest with graphs.agent -> src.agents.orchestrator:graph_factory" >&2
  exit 2
fi

# Default: --allow-blocking is ON, because LangSmith's autoinstrumentation
# does sync I/O on every trace flush and blockbuster would otherwise reject
# it. Strip the user-facing --strict flag and add --allow-blocking unless
# --strict was explicitly passed.
FORWARD_ARGS=()
ALLOW_BLOCKING=1
for arg in "$@"; do
  case "$arg" in
    --strict)
      ALLOW_BLOCKING=0
      ;;
    *)
      FORWARD_ARGS+=("$arg")
      ;;
  esac
done

if [[ "$ALLOW_BLOCKING" == "1" ]]; then
  FORWARD_ARGS+=("--allow-blocking")
fi

exec uv run langgraph dev "${FORWARD_ARGS[@]}"
