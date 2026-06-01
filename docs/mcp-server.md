# MCP Server: the read contract over the dataset

The system needs a bounded surface for reading telemetry. It exposes that surface as a deliberately thin MCP server: a read layer over the fixed dataset that mimics pulling telemetry from an AWS-like environment. The server holds no analysis logic. It reads the scenario's JSON and Terraform files and returns what it finds. The intelligence lives in the agents that call it.

Two consumers share one protocol:

- Agents inside this project call MCP tools as part of their ReAct loops. The Action Harness scopes each specialist to its own tier's tools; the Reasoning Harness binds the returned observations as the evidence a finding cites.
- External AI clients (Claude Desktop, Cursor, anything that speaks MCP) connect to the same server and explore the dataset interactively.

This is not two systems. It is one tool surface with two consumers.

## Why MCP

The agents need a constrained way to read. Without a protocol, that constraint lives as Python function signatures only this project understands. MCP gives the same surface a wire format, so the constraint becomes inspectable, replayable, and reusable.

Three reasons MCP was chosen over a bespoke tool layer:

- **Open protocol.** Any AI client that supports MCP can use the server. The agent project is one client among many.
- **Tool surface separation.** The server defines the read contract. The agents in this project consume it, and so does any other client. The Action Harness's narrow-tool-surface scoping becomes a protocol guarantee rather than an internal convention.
- **Portfolio surface.** MCP is a recognizable name. A reviewer who sees an MCP server knows the project speaks a modern protocol, not a one-off RPC.

## The dataset that the server wraps

The published dataset lives at [`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations) on Hugging Face. The server pulls it on first run via `huggingface_hub.snapshot_download` and caches it at `<repo-root>/.hf_cache/`. The first call costs about 12 MB of download. Every later call hits the cache and stays offline.

The project-local cache keeps the repo self-contained: a reviewer who clones the project sees the downloaded artifacts in one folder and can reset with `rm -rf .hf_cache`. The default cache directory is set by `HF_HOME=.hf_cache` in `.env.example`; copy that to `.env` and edit `HF_HOME` if you want the cache somewhere else (relative paths resolve against the project root). The `.hf_cache/` directory is gitignored.

Pin a specific commit hash in `src/data_loader.py` to lock the dataset version. Leave the pin as `None` during development, and pin it once you start producing baseline numbers.

The agents and the MCP server share `src/data_loader.py`. The loader is small and stable, and it is copy-pasted between consumers if the MCP server ever moves to its own repo.

Every tool is keyed on `app_name`, mimicking an alert that fires against a named application. Internally the server maps `app_name` to its scenario in the dataset (`app-01` through `app-18`). That mapping is the only indirection the server adds.

## Tool catalog

The server is thin in logic and rich in named operations. Each tool is a trivial read against the fixed dataset files, computing nothing an agent could not compute for itself. The richness is in the names, not the behavior. Two things follow from naming each read operation separately rather than collapsing them into a few generic calls. Each call logs as its own auditable step, so a reviewer reading the trail sees `detect_threshold_breaches` rather than a generic read whose meaning is buried in its parameters. And the per-tier partitioning lets the Action Harness scope a specialist structurally: a Compute Analyst's toolset contains only compute operations, so an out-of-tier read is impossible at the tool layer, not merely refused after the fact.

The names are identical internally and over the wire.

### Per-tier telemetry surface

The Tier Specialists' vocabulary. Each operation is parameterized by `app_name`, `tier`, and the metric it reads. The per-tier composition (which tier each specialist is allowed to pass) is defined in [`agents.md`](agents.md).

| Operation                                           | What it returns                                      |
| --------------------------------------------------- | ---------------------------------------------------- |
| `get_time_series(app_name, tier, metric)`           | per-window values for one metric                     |
| `get_summary_statistics(app_name, tier, metric)`    | p50, p90, p95, and mean for one metric               |
| `get_time_pattern(app_name, tier, metric)`          | the temporal pattern (hourly, daily) of a metric     |
| `detect_threshold_breaches(app_name, tier, metric)` | the windows where a metric breaches its band         |
| `get_metric_distribution(app_name, tier, metric)`   | the metric's distribution across the window          |
| `get_configuration(app_name, tier)`                 | the tier's instance class, count, and scaling config |

### Shared context operations

Available to every specialist, independent of tier.

| Operation                             | What it returns                               |
| ------------------------------------- | --------------------------------------------- |
| `get_business_context(app_name)`      | the application's criticality and description |
| `get_sla_target(app_name)`            | the SLA target (availability and p95)         |
| `get_monthly_cost(app_name)`          | the per-tier and total monthly cost baseline  |
| `get_before_after_evidence(app_name)` | the recorded outcome of a prior config change |

### Per-tier specials

Each appears only on the surface of the specialist that needs it.

| Operation                             | Available to       |
| ------------------------------------- | ------------------ |
| `get_per_instance_breakout(app_name)` | Compute Analyst    |
| `get_top_queries(app_name)`           | Data Layer Analyst |
| `get_top_cache_keys(app_name)`        | Data Layer Analyst |

### Scenario and dataset surface

A separate surface, deliberately kept off every specialist's toolset.

| Operation                                  | Consumer                                   |
| ------------------------------------------ | ------------------------------------------ |
| `list_scenarios()`                         | navigation and external browsing           |
| `get_scenario_metadata(app_name)`          | navigation; narrative and business context |
| `get_terraform(app_name)`                  | the System Mapper, as its parsing input    |
| `get_correlation_evidence(app_name)`       | the Cross-Tier Evaluator                   |
| `get_handcrafted_recommendation(app_name)` | the evaluator harness only                 |

Keeping the gold answer off every specialist surface is a scope guarantee, not a convenience. A specialist that could read `get_handcrafted_recommendation` could reason backward from the target, and the evaluation would no longer mean anything. For the same reason `get_terraform` belongs to the System Mapper, whose job is to parse it, and `get_correlation_evidence` belongs to the Evaluator, the only agent that reasons across tiers.

Tools return JSON. Inputs are simple types, strings and optional integers. There is no streaming, no callbacks, and no agent state on the server.

## How agents inside this project call it

Tier Specialists run ReAct loops. Each step is a thought, then an action, then an observation. The action is an MCP tool call.

```
Thought:  Is CPU utilization below healthy ranges?
Action:   get_summary_statistics(app_name='app-01', tier='compute', metric='cpu_p95')
Observation: {p50: 18.4, p90: 24.7, p95: 27.1, mean: 19.2}
```

Two harnesses sit between the specialist and the server. The Action Harness checks that the tool is on the contract and that the tier matches the specialist's bounded scope; an out-of-scope call is rejected here. The Reasoning Harness logs the observation to the audit trail with a reference the specialist can later cite as evidence.

The MCP server itself does none of this. It answers tool calls and nothing more. The harnesses sit between the specialists and the server.

## How external AI clients connect

Claude Desktop and other MCP clients connect through a small config block. For Claude Desktop:

```json
{
  "mcpServers": {
    "cloud-optimization": {
      "command": "python",
      "args": ["-m", "src.mcp_server.server"]
    }
  }
}
```

Once the connection is live, Claude Desktop sees the tool surface under `cloud-optimization` and can call any of it. A reviewer who clones this project and adds that config block can browse all eighteen scenarios from inside Claude Desktop without writing code.

This is the strongest demo the project has.

## Implementation status

Implemented at `src/mcp_server/`. One file per tool family
under `src/mcp_server/tools/`, statistical helpers in `_stats.py`,
per-specialist allow-list in `scope.py`, FastMCP wiring in `server.py`,
stdio entry point at `python -m src.mcp_server`. The `mcp` Python SDK
is pinned exactly in `pyproject.toml` because the FastMCP submodule
changes contract between releases; bump the pin alongside a fresh
`uv.lock`.

### Error model

Tools raise `mcp.server.fastmcp.exceptions.ToolError` for agent-visible
errors with a stable code prefix on the message:

  - `invalid_input`: malformed argument (bad `app_name` format, wrong
    comparator, negative `n_bins`, etc.).
  - `unknown_app`: well-formed `app-NN` but no such scenario in the
    dataset.
  - `unknown_tier`: tier name typo, or a valid tier that this scenario
    does not include (e.g. asking for cache telemetry on a compute-only
    scenario).
  - `unknown_metric`: metric field not present in the tier's records.

Plain Python exceptions raised inside a tool (file missing, dataset
malformed, etc.) are masked by FastMCP as a generic internal error so
implementation paths don't leak to the wire.

### Verifying the server

1. **Unit tests for the helpers + scope contract.**
   ```bash
   uv run python -m pytest tests/unit/mcp_server/ -v
   ```
   34 tests cover the statistical helpers and the scope-allowlist
   consistency.

2. **Wire-layer integration test.**
   ```bash
   uv run python -m pytest tests/integration/test_mcp_server.py -v
   ```
   Spawns the server as a subprocess, exercises tools/list and one
   tools/call per family. Auto-skips in environments without network
   access to Hugging Face (the dataset needs to be fetched on first run).

3. **MCP Inspector (manual).** Before wiring Claude Desktop, run the
   server through Anthropic's Inspector to validate the tool surface
   in isolation. From the project root:
   ```bash
   npx @modelcontextprotocol/inspector uv run python -m src.mcp_server
   ```
   The Inspector UI lists every tool with its auto-generated JSON
   schema, lets you call each one interactively, and surfaces errors
   without the GUI-client overhead.

4. **Claude Desktop (manual).** Add the config block from the section
   above to `claude_desktop_config.json` and restart Claude Desktop.
   The server appears under `cloud-optimization` with all 18 tools.

## Loading the dataset without the MCP server

For local development, batch scripts, and tests that do not need the protocol layer, the project ships `src/data_loader.py` as a direct Python interface to the dataset. Same cache, same revision pinning, no MCP server required.

```python
from src.data_loader import load_scenario, list_scenario_ids

ids = list_scenario_ids()                # ['01', '02', ..., '18']
scenario = load_scenario('07')           # dict with 9 keys
print(scenario['metadata']['scenario_name'])
print(scenario['handcrafted_recommendation']['specific_change'])
```

The MCP server and the direct loader return the same data. They differ only in transport: the loader is in-process, the server speaks MCP over stdio.

## Where this fits in the architecture

The MCP server is the implementation of the read contract that the Action Harness scopes. The full picture:

- `harnesses.md` defines what the read contract is and why it is bounded.
- This doc defines how the contract is exposed over MCP and what operations it offers.
- `agents.md` describes how each specialist composes its slice of the contract during its ReAct loop.

If MCP later proves too heavy or too light, the architectural concern, a bounded read contract scoped by the Action Harness, does not change. Only the transport does.
