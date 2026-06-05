# Evaluation: proving the architecture

The industry is saturated with multi-agent systems that a single LLM prompt could have solved. This evaluation exists to prove, using a falsifiable baseline comparison rather than assertions, that this project is not one of them.

The system is scored against an 18-scenario synthetic dataset ([`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations)), each scenario carrying one hand-crafted gold recommendation. Every agent output is graded through four layers: two deterministic, two LLM-judged. The split is deliberate, and the reasoning behind it is the most important thing in this document.

> **Implementation status.** All four layers are implemented in `src/evaluator/`. Shape and Correctness run as deterministic gates. Mid and Rich score the prediction's `specific_change` prose against the gold via an LLM judge (OpenAI or Anthropic, picked from the environment; see `src/evaluator/judge_client.py`). When no API key is available, Mid and Rich return graceful `(skipped)` markers and only the deterministic layers run.

## Two modes, one principle

A cloud-optimization recommendation is natural language. It does not fit a rigid schema, and its quality cannot be captured by string rules. But the *decision underneath* it — which tier, which action category, issue or no issue — is enumerable. The evaluator grades each part with the tool that fits it:

- **The decision is objective, so it is graded deterministically.** Shape and Correctness are pure-Python checks. Same prediction plus same rules equals the same verdict, on any machine, with no API key. This is the reproducible spine.
- **The prose is opinionated, so it is graded by an LLM judge.** The one field that genuinely requires semantic understanding is `specific_change`, the prose recommendation. The judge scores that field; rules check everything else. Mid and Rich pass or fail based on the judge's score and on the structural completeness of the prediction's supporting fields.

This split is the production-honest position. A purely deterministic scorer for free-form recommendations would be a treadmill of keyword patches that still could not tell "scale from 4 to 8" apart from "scale from 8 to 4." A purely LLM-judged scorer would make even the objective decision non-reproducible and invite the obvious circularity (an LLM grading an LLM). Splitting on the objective/opinionated line avoids both.

**The non-override invariant.** The LLM judge can flag, annotate, and guide, but it can never change a Shape or Correctness verdict. If a recommendation passes Correctness but the judge scores its prose poorly, that disagreement is surfaced, not resolved by override: it tells the human reviewer where to look, and it tells the system's author that the Correctness rule for that scenario may be too coarse and worth refining. The divergence is a signal, not a conflict.

## Why this design (the path here)

The two-mode split was not the first attempt. It is where two failed attempts led.

- **Attempt 1: strict structured output.** The agent was asked to produce a rigid recommendation that an AWS environment could consume directly. Real recommendations vary too much; the model could not reliably fit high-variance advice into a fixed schema. Conclusion: the output is inherently natural language.
- **Attempt 2: fully deterministic scoring.** With NLP output, Correctness could be enumerated, but quality was scored by keyword groups. That became a brittle rule-maintenance problem — every new phrasing the model produced needed another keyword or enum, and direction and magnitude still slipped through. Conclusion: rules cannot grade rich NLP.
- **Attempt 3 (current): split the modes.** Deterministic checks for the enumerable decision spine; an audited LLM judge for the one semantically rich field; the judge informs the human-in-the-loop but never overturns the objective layers.

Documenting the path matters because it shows the disciplined approaches were tried first and abandoned for concrete reasons, not skipped.

## The architectural justification

The evaluator is calibrated to separate careless single-shot models from deep, orchestrated synthesis. Measured scores across six baseline runs (three single-shot, three orchestrated tiers) live in [`../measurements/`](../measurements/); the headline table is in [`../README.md`](../README.md). The gap between single-shot and orchestrated on Correctness and Rich is what justifies the architecture: single-shot LLMs at every capability tier struggle to consistently match the project's enum vocabulary and to produce rich-text recommendations the judge will pass.

## The four layers

For each scenario, the agent produces a recommendation in a fixed JSON shape: a finding type, a primary tier (which part of the cloud stack), an optional secondary tier (when the issue cuts across tiers), an action category, a prose change description, supporting evidence, a projected post-change state, and a cost impact.

The evaluator scores that recommendation against the gold answer through four layers in order.

| Layer       | Mode          | Question                                                  | What passing means                                                          |
| ----------- | ------------- | --------------------------------------------------------- | --------------------------------------------------------------------------- |
| Shape       | Deterministic | Is the output well-formed JSON with the required fields?  | The recommendation parses and has the expected top-level fields.            |
| Correctness | Deterministic | Is this the right answer for this scenario?               | The finding type, primary tier, secondary tier, and action category match the gold (strict enum equality). |
| Mid         | LLM judge     | Did the agent engage with the right evidence?             | The judge scores the prediction's `specific_change` prose against the gold at 30 or above on a 0-100 scale. |
| Rich        | LLM judge + rules | Did the agent show orchestrated synthesis?            | The judge scores at 60 or above AND the prediction's supporting fields pass deterministic completeness checks. |

The layers are stacked. Shape is a precondition for everything else. Correctness is a **hard gate**: if the recommendation is the wrong answer (one or more enum fields disagree with the gold), Mid and Rich are skipped (not failed), so a reader can tell apart "wrong answer" from "right answer but thin."

### How Correctness works (deterministic)

Correctness is a strict-equality check on the four enum decision fields. For each scenario, the per-scenario `scoring_metadata` block inside `expectations/NN/raw_recommendation.json` lists exactly one allowed value per field, matching the gold answer. A prediction passes Correctness only when all four fields equal the gold's values exactly.

There is no partial credit at this layer. Two of the 18 scenarios (15 and 17, both diagnostic deferrals) allow `null` as an alternative for `secondary_tier_allowed`, documented in the rule file with an explicit rationale.

The enum encodes the semantic direction the prose alone could not be trusted to carry. "Scale up" and "scale down" are different `action_category` values, not phrasings of one; recommending the wrong direction fails Correctness deterministically and never reaches the judge. This is why the objective spine is enumerable: the decisions that most matter are encoded as distinct enum values, not left to prose.

### How Mid and Rich work (LLM judge + threshold gating)

`specific_change` is the only field in a recommendation that requires real semantic understanding to evaluate. The other fields are either enums (graded by Correctness) or structured records like `evidence`, `cost_impact`, `projected_state` (whose completeness can be checked by rules without semantic interpretation). Mid and Rich therefore share a single LLM judge call that scores only `specific_change`; thresholds on that score gate the two layers.

**One judge call per scenario.** For each scenario that is correct and not short-circuited, the evaluator makes one call to a pinned LLM judge. The judge sees the gold's `specific_change` prose, the prediction's `specific_change` prose, and a global scoring prompt. It returns a structured response with a 0-100 richness score and a one-paragraph rationale.

**Richness is orthogonal to Correctness.** A correct prediction passes through the Correctness gate and into the judge, where it can land at any richness level. Low richness (a correct prediction with weak prose) is a distinct outcome from incorrect (caught at Correctness, never reaches the judge). The eval treats "wrong answer," "correct but low richness," "correct and mid richness," and "correct and high richness" as four separate diagnostic outcomes because they tell the reviewer different things about the agent.

**Score interpretation.**

| Score range | Mid | Rich | What it means |
| :--- | :--- | :--- | :--- |
| 0 to 29 (low richness) | Fail | Fail | Correct enums but prose is generic, off-target, or trivially short. The agent picked the right action category by luck or process of elimination, not by genuine engagement with the data. |
| 30 to 59 (mid richness) | Pass | Fail | Prose engages with the right direction and references the relevant tiers, but lacks the depth Rich requires (no specific entities cited, no quantified projections, generic phrasing). |
| 60 to 100 (high richness) | Pass | Run additional structural checks below | Prose demonstrates the kind of specific, evidence-bound reasoning orchestration is meant to produce. Worth subjecting to the deterministic completeness checks. |

Above 60, Rich proceeds to four deterministic completeness checks on the supporting fields:

- **`fixture_citation`** — if the scenario flags a named fixture (`top_queries`, `top_cache_keys`, `per_instance_breakdown`), at least one identifier from that fixture must appear as a case-insensitive substring in the prose.
- **`cost_impact_quantified`** — the `cost_impact` field has at least one non-zero numeric entry (skipped for `sla_review` and null action categories).
- **`projected_state_quantified`** — the `projected_state` field has at least one numeric entry (skipped on the same condition).
- **`evidence_structured`** — the `evidence` field totals at least three bullets across `telemetry_observations`, `infrastructure_context`, `correlation_observations`.

All four must pass for Rich to pass. The LLM gate above 60 is the necessary condition; the structural checks are additionally required.

**Why this design.** The judge does the work only LLMs are good at: comparing two pieces of prose for semantic richness. Rules do the work only rules are good at: confirming structural fields exist with the right shape. Neither does the other's job, and neither needs per-scenario configuration: the judge prompt is global, the structural checks are global. Per-scenario specifics (which fixture to cite, what gold to compare against) come from each scenario's composite at `expectations/NN/raw_recommendation.json`, which carries both the gold prediction (top-level) and the rubric (`scoring_metadata` block).

The judge is made auditable to answer the usual objections to LLM-as-judge:

- **API key required.** The judge calls an LLM provider (Anthropic or OpenAI). Set either `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the environment, typically loaded from a `.env` file. When neither key is set, Mid and Rich return `(skipped)` and the deterministic layers (Shape + Correctness) still run normally. This degradation keeps the report format identical whether or not the judge runs.
- **Provider and model configurable.** `LLM_JUDGE_PROVIDER` (`openai` or `anthropic`) picks the provider explicitly; auto-detect from which key is present otherwise, preferring OpenAI if both are set. `LLM_JUDGE_MODEL` overrides the default model for the chosen provider. Defaults: `gpt-4o-mini` (OpenAI) and `claude-haiku-4-5-20251001` (Anthropic). The default thresholds (Mid >= 30, Rich >= 60) were originally calibrated against Anthropic Haiku and verified against OpenAI gpt-4o-mini; re-run `tests/judge_live/` after any provider or model change to confirm gold-vs-gold scores still clear the high-richness band.
- **Pinned model + temperature 0.** The model used per session is logged on every score response. Temperature 0 keeps verdicts reproducible in practice, though not guaranteed bit-identical across model versions.
- **Published prompt.** The exact scoring prompt lives at `src/evaluator/prompts/judge_richness.md` and is version-controlled with the code. No hidden prompt; a reviewer can read what the judge was asked to do.
- **Logged rationale.** The judge returns a structured rationale alongside the score. It surfaces at evaluation time; when the audit-trail substrate lands, the rationale will be persisted there as part of every review's evaluator record.
- **Threshold transparency.** The 30 and 60 cutoffs are calibration knobs, documented above. They are picked so that a random or trivial agent lands below 30, a single-shot frontier model lands 30 to 70, and an orchestrated multi-agent system lands above 60 on most scenarios. Tuning is a one-line change.

The judge never decides whether the answer is correct; Correctness does that deterministically. The judge only assesses richness on an already-correct answer. That boundary is what keeps the eval out of the circular trap of an LLM certifying an LLM.

**Note on borderline scores.** At temperature 0 the judge is mostly but not bit-identically reproducible. A score near a threshold (29 vs 31, 59 vs 61) can occasionally flip a layer's verdict between runs. The rationale field shows why, so borderline cases are diagnosable. No tie-break band is applied; "reproducible in practice, not in principle" is the honest description.

## Design philosophy

### Short-circuit rule for no-action findings

When the gold's `finding_type` is a no-action value, Mid and Rich are bypassed entirely — the judge is not called. The scorer produces a single `short_circuit` marker for each of those layers and runs none of the richness checks.

Two no-action finding types are used by current golds:

| `finding_type`        | What it means                                                                            | Why no Mid/Rich                                              |
| --------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `no_issue_found`      | Every tier is healthy; the right answer is "do nothing"                                  | There is no action to describe or quantify                  |
| `diagnostic_deferral` | Telemetry signals are ambiguous; the right answer is "deploy more instrumentation first" | There is no infrastructure change to describe or quantify   |

The `NO_ACTION_FINDINGS` sentinel set in `src/evaluator/enums.py` also includes a third value, `insufficient_data`, reserved for future scenarios where a specialist lacks signal within its tier. The short-circuit logic treats it the same way: no action to describe means no Mid or Rich check to run. No current gold uses it; it lives in the code only.

**Why short-circuit instead of judging no-action richness.** On a healthy scenario the right answer is "no change," and asking the judge to assess action-richness would pressure the agent toward inventing plausible-sounding action language ("we may want to consider rightsizing in the future") just to look rich, when restraint is the correct behavior. Short-circuiting removes that pressure. Correctness alone — the right finding type and tier, deterministically checked — is sufficient proof that the agent correctly chose to do nothing. Scenarios 06, 15, and 17 are the currently short-circuited scenarios; their composites carry a `short_circuit` field inside `scoring_metadata` documenting the bypass.

### Limitations

The objective layers are bit-for-bit reproducible. The judged layers are not, by design, and both modes have honest limits:

- **The judge is the runtime path for Mid and Rich.** When an API key is available, Mid and Rich score the prediction's prose against the gold via the LLM judge. When no key is available, both layers return `(skipped)` markers and only the deterministic gates (Shape, Correctness) report verdicts. CI runs the deterministic gates unconditionally; live-judge tests under `tests/judge_live/` exercise the judge path explicitly and are opt-in because they cost API calls.
- **Graceful degradation when no API key.** When neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY` is in the environment, the judge cannot run. Mid and Rich return `(skipped)` (the same form as the short-circuit marker for no-action scenarios). Shape and Correctness always run and report normally. The exit code and printout format are unchanged; the `(skipped)` markers tell a reader which layers were unable to run vs. which ran and failed. This is the same pattern used for short-circuit scenarios, so downstream tooling does not need a special case.
- **Correctness is only as granular as the enums.** It catches the wrong tier, wrong action category, and wrong direction, because those are encoded as distinct values. It does not catch a correct-category recommendation with the wrong magnitude (scaling to 8 when the gold says 12) — that is left to the judge and the human.
- **The judge is reproducible in practice, not in principle.** Temperature 0 plus a pinned model gives stable verdicts across runs, but a model-version change can shift a borderline Mid/Rich call. Borderline scores near the 30 or 60 cutoffs can occasionally flip across runs even at temperature 0. This is acceptable because the judged layers are advisory input to a human reviewer, not an automated gate.
- **The judge is opinionated.** Richness is a matter of degree, and reasonable judges disagree at the margin. The published prompt narrows this, and the rationale lets a human overrule any verdict. The non-override invariant guarantees a judge opinion can never flip the objective result.
- **The Rich structural checks are existence checks, not quality checks.** `cost_impact_quantified` passes when any numeric field is non-zero; a prediction with `savings: $1` passes. The LLM gate above 60 is the main guard against trivially-quantified output; the structural checks confirm only that the supporting fields are present in the right shape.

### The decision space an agent navigates

Correctness is scored by strict equality on four enum fields: `finding_type`, `primary_tier`, `secondary_tier`, and `action_category`. Each scenario's gold defines the single valid combination; see `eval-set/expectations/NN/raw_recommendation.json` (the `scoring_metadata.*_allowed` arrays) for per-scenario allowed values and `src/evaluator/enums.py` for the full value universes. Special sentinels (`no_issue_found`, `diagnostic_deferral`, `deferred`) let the agent explicitly signal "do nothing" or "need more data," and are handled by the short-circuit rule above.

Each `rules.json` file carries the four enum allow-lists, a `description`, optional `*_rationale` prose, an optional `short_circuit` block (no-action scenarios only), and an optional `must_cite_fixture` pointer (used by Rich's `fixture_citation` structural check for the two scenarios that have a named fixture). That is the full schema; the placeholder Mid fields were removed when Mid moved to the LLM judge.

### Why four layers

Each layer catches a different failure mode. Collapsing them into a single pass/fail score would hide the diagnostic signal that makes the eval useful, and would blur the deterministic verdicts into the opinionated ones. The four layers map to the discrimination story the architecture is meant to tell:

- **Shape** is meant to be trivial — any agent that produces well-formed JSON passes; it catches malformed output, not quality.
- **Correctness** is the main objective gate — a random agent picks the wrong finding type most of the time, and a single-shot model usually gets the easy scenarios right but misses on cross-tier and deferral cases.
- **Mid** separates careless from careful among agents that already passed Correctness — the LLM judge scores how well the prose engages with the right evidence.
- **Rich** is meant to be hard — passing requires both a high judge score and structurally complete supporting fields; a single-shot agent rarely clears both bars in one pass; an orchestrated specialist, scoped narrowly, can.

## Scope

- **Does not measure latency or throughput.** Operational performance is a separate concern.
- **Does not re-validate the audit trail.** That is enforced structurally by the Reasoning Harness's evidence-binding. The evaluator checks recommendation content, not chain integrity.
- **Does not compare agent runs to each other.** The methodology compares agent output to the hand-crafted gold, not to prior agent runs.
- **Does not gate releases.** This is a portfolio project, and the judged layers are advisory to a human reviewer rather than an automated gate. In a production deployment the methodology would inform a gate, but that is out of scope.

## How to use the evaluator

| You want to                                              | Read or run                                                             |
| -------------------------------------------------------- | ----------------------------------------------------------------------- |
| See a sample recommendation report                       | [`sample_runs/reports/`](../sample_runs/)                               |
| See the deterministic layers discriminate live           | Run `python eval-set/demo_scoring.py` (Shape + Correctness, no API key) |
| Run the full four-layer score (judge needs an API key)   | Run `python -m src.evaluator.eval --app-name app-NN --prediction your_file.json` |
| Verify every gold passes the deterministic layers        | Run `pytest tests/integration/test_golden_answers.py -v`                |
| Read the judge prompt                                    | [`src/evaluator/prompts/judge_richness.md`](../src/evaluator/prompts/)  |
| Inspect a gold answer + its rubric (one composite file)  | [`eval-set/expectations/NN/raw_recommendation.json`](../eval-set/)      |
| Read the composite Pydantic schema                       | [`src/models/composite.py`](../src/models/composite.py)                 |
| See what the input telemetry looks like                  | [`dataset-examples/scenario_NN/`](../dataset-examples/)                 |
| Understand the architecture this feeds into              | [`README.md`](../README.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md)    |

The commands and paths above describe the current evaluator layout. The judge prompt lives at `src/evaluator/prompts/judge_richness.md`. `app-NN` matches the `mcp-server.md` convention.
