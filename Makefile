# Common workflows wrapped behind one-word targets.
# Run `make help` to see them.
#
# Target invocations use `./scripts/foo.sh` (not `bash scripts/foo.sh`) so
# Make doesn't pick the shell — each script's own `#!/usr/bin/env bash`
# shebang does. The scripts themselves require bash (they use [[ ]],
# arrays, etc.), but Make doesn't have to know that.

.PHONY: help demo demo-docker test test-ci eval scenario baseline-haiku baseline-sonnet baseline-opus integration mock-demo langgraph clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

demo: ## Score app-08 gold against deterministic gates (no API key, no LLM).
	./scripts/run_demo.sh

mock-demo: ## Replay app-08 cycle + render report locally (no LLM, no docker, ~10s).
	./scripts/run_mock_demo.sh

demo-docker: ## Same as mock-demo, but hermetic in a Docker container (recruiter path).
	docker compose up demo

test: ## Run the full pytest sweep (unit + integration).
	./scripts/run_pytest.sh

test-ci: ## Run the CI suite (ruff + mypy + pytest).
	./scripts/run_ci_locally.sh

eval: ## Score one app's gold answer with the LLM judge. Override APP=app-NN.
	./scripts/run_demo.sh --with-judge

scenario: ## Run agents + render report + score against gold for one scenario. Usage: make scenario APP=app-08
	@# Coalesce APP across common case variants — make variables are
	@# case-sensitive, so without this, `make scenario App=app-08`
	@# silently fails with "APP is required."
	$(eval APP := $(or $(APP),$(App),$(app)))
	@if [ -z "$(APP)" ]; then \
		echo "ERROR: APP is required. Usage: make scenario APP=app-08" >&2; \
		echo "       (variable names are case-sensitive: APP, not App or app)" >&2; \
		exit 2; \
	fi
	@mkdir -p ./demo-output
	./scripts/run_agents.sh $(APP)
	./scripts/render_recommendation.sh $(APP) --out ./demo-output/report.md
	@echo ""
	@echo "Wrote ./demo-output/report.md"
	@echo ""
	./scripts/score_recommendation.sh $(APP)

integration: ## Full 18-scenario integration test (orchestrated, ~30-50 min, real LLM).
	./scripts/integration_test_all.sh

langgraph: ## Boot LangGraph dev + open Studio to visualize the agent graph.
	./scripts/run_langgraph_dev.sh

baseline-haiku: ## Single-shot Haiku baseline, all 18 apps (~$0.05, ~3 min).
	./scripts/baseline_single_shot.sh --model haiku

baseline-sonnet: ## Single-shot Sonnet baseline (~$0.50, ~5-8 min).
	./scripts/baseline_single_shot.sh --model sonnet

baseline-opus: ## Single-shot Opus baseline (~$3, ~8-15 min).
	./scripts/baseline_single_shot.sh --model opus

clean: ## Wipe the audit DB, the HF dataset cache, and ./demo-output/.
	./scripts/clean.sh --all
	rm -rf ./demo-output
	@echo "Wiped ./demo-output/"
