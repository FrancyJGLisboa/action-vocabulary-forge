# Changelog

## Unreleased

- `discover_decision_system.py`: offline product entry point for a repository/SOP set plus resolved JSONL/CSV cases. It validates and groups histories, ranks bounded decision surfaces by evidence readiness and optional workload value, creates a privacy-safe stratified holdout, reports a clearly labeled local text baseline, and scaffolds the highest-ranked eligible surface as a non-production Semantic Decision Bundle.
- Candidate discovery never copies raw case context into its outputs, never invents bindings, requires human approval for non-fallback actions, and suppresses bundle generation when evidence or boundedness gates fail. `references/decision-discovery.md` defines the input contract, scoring, outputs, and promotion path; `examples/discovery-input/` provides the tested vertical slice.
- `evaluate_discovered_system.py`: shadow-only JEV/Laya evaluation on the frozen deterministic holdout. It verifies the original source hash, rejects illegal model answers, writes only hashed case references, distinguishes historical from human-verified labels, and produces a `jev_gate.py`-compatible log plus measured performance JSON.
- Refresh the executive comic for the semantic compiler flow and add an editable SVG source.
- Semantic Decision Bundle: `material_manifest.yaml`, `semantic_ir.yaml`, `judgment_registry.yaml`, and `semantic_links.yaml` extend the compatible Action Bundle from arbitrary source material through semantic judgments to legal actions.
- `init_semantic_bundle.py` and `validate_semantic_bundle.py`: scaffold and enforce provenance, maturity, question shapes, cross-file links, and the invariant that a judgment cannot authorize an action.
- `semantic_runtime.py`: two-stage execution. It evaluates active reviewed Choice/Noul/Score judgments, enriches the final action context, filters the Choice to actions whose deterministic preconditions pass, selects a single legal action without a second model call, and refuses a missing legal fallback.
- `semantic_index.py`: generated SQLite FTS5 search across sources, concepts, relations, question families, judgments, states, actions, surfaces, and evidence.
- `JudgmentLog`: raw supporting answers in the bundle-free `jev_gate.py` format, so question instruments and final actions can be evaluated independently.
- `scripts/jev_gate.py calibrate|evaluate`: thresholds and the release gate for any JEV or Laya decision log, with no bundle and no PyYAML. Accepts plain `answer/confidence` or the raw System One answer (`choice`, `noul`, `score`), per-question abstain values, human-labels-only gate, and replays the thresholds on the held-out split to report the automation rate.
- The calibration rule, metrics and bundle-free gate checks moved into `decision_history.py` (`calibrate_groups`, `log_metrics`, `gate_failures`, `threshold_failures`); `calibrate_thresholds.py` and `evaluate_decisions.py` now call them, with unchanged output.
- Fix: calibration and the release gate treated a Noul's `no_action_id` as a free fallback even when the question declares a distinct `abstention_action_id`, so "no" was never calibrated and the gate never required a threshold for it. They now use the adapter's rule; a test pins the two implementations together.
- Escalation cascade: `decide()` / `run()` take `escalate=callable(context, decision, legal) -> action_id | None`. An abstained decision goes to the second decider (an LLM, a rule) before the fallback runs. It is offered only the question's non-fallback actions legal from the current state; `None` keeps the fallback; anything else raises `IllegalChoice`. Dynamic-candidate questions are not escalated.
- `Decision.decided_by` and the log field `decided_by`: `jev | fallback | escalation`. An escalated decision keeps `abstained: true` (JEV did abstain) and still passes every execution check; an escalated action with `requires_confirmation` is always blocked, even with `confirmed=True`.
- `label_source` on labels (JSONL/CSV column or inline in the log; missing means `human`). `evaluate_decisions.py` judges the release gate on human labels only and prints `excluded_machine_labeled`: an LLM that labels and escalates would otherwise grade itself. Calibration accepts every source and prints the counts.
- Evaluation: escalated records count as decisions in boundary accuracy, an escalation that acted where the truth was a fallback fails abstention accuracy, and a new `escalation_accuracy` line reports the second decider alone.

## 0.4.0 (2026-09-22)

- A Noul's `no_action_id` is a free fallback only when the question declares no `abstention_action_id`. With one declared, "no" is a conclusion and must clear its threshold: a control test that can record an exception ungated is not a control test.
- `calibrate_thresholds.py --min-threshold` (default 0.5): a calibrated threshold can no longer fall to the bottom floor and silently ungate an action when a small sample makes every confidence band look perfect. Clamped values are marked `*` and the floor is recorded in the policy. Found when a local model produced its only wrong conclusion on an action calibrated to 0.00.
- `criterion_compact` / `instruction_compact`: a question may carry a short wording used by providers with a small context window (`COMPACT_PROVIDERS`, currently `laya`); `FORGE_COMPACT_CRITERIA=1|0` forces it. The validator requires compact criteria on every choice or none.
- `provider: laya`: in-process local transport (Laya, Apache-2.0) with the same answer shape as TypeSafe System One, embedded into generated adapters; `FORGE_PROVIDER` env override; `default_transport()`.
- Calibration skips records without a confidence (deterministic decisions); `evaluate_decisions.py` reports deterministic agreement separately (a disagreement holds the release) and gains `--by-model`.
- The Laya transport reports the winning probability as `confidence` and keeps the provider's own value as `native_confidence`: Laya's native confidence is a margin topping out near 0.3, which collapsed every record into the lowest calibration bin so no group could ever calibrate.
- Python 3.11 compatibility: nested same-quote f-strings (PEP 701, 3.12+) removed; a test parses every script at `feature_version=(3, 11)`. Found running a generated adapter on Intel macOS, where torch caps at 2.2.2 and the venv must be 3.11.
- First measured provider comparison on one bundle (60 labeled samples, internal-control testing): hosted `jev-1.13.0` 97.3% raw agreement and 95% of decisions executed; local `laya:typed-decisions` 90.0% and 31% executed; **zero wrong conclusions after thresholds on either**, and the gate held the local one by naming the attribute that never reached the accuracy target.

## 0.3.0 (2026-09-22)

- `ui` bindings render to Playwright handlers (`goto/click/fill/select/press/check/uncheck/read` on a locator template, optional `url`), on a host-injected page (`set_ui_page`) or a headless browser per call.
- `mcp` bindings render to MCP `tools/call` handlers over stdio or streamable HTTP via the MCP Python SDK, or through a host-injected caller (`set_mcp_caller`).
- Missing SDKs raise `ExecutionBlocked` with the install command; nothing is silently skipped.
- Validator: kind-specific checks for `ui` (operation, driver, url scheme, `value` for fill/select/press) and `mcp` (transport, command/url).
- No binding kind renders as `stub:unsupported_kind` any more; stubs remain only for missing or unobserved evidence.

## 0.2.0 (2026-09-21)

- `generate_adapter.py` renders handlers from evidence-graded `binding`s (`python_callable`, `http`, `cli`); `mcp`/`ui` and unobserved bindings become stubs that name the missing evidence.
- Generated adapter re-checks `preconditions[]` and `allowed_from_states`, infers the state from `observable_predicate`, and exposes `legal_actions()`.
- Decision log (`DecisionLog`, `decision_record()`), `decide()` and `run()` entry points, `transport=` for tests.
- Policy per question and per action (`policy.questions`), `default_when_uncalibrated: abstain`.
- `criteria_source: dynamic` questions with runtime choices mapped to `dynamic_executor_action_id`.
- `model` and `endpoint` from `jev_adapter_spec.yaml` carried into the adapter.
- `scripts/predicates.py`: one grammar for preconditions, observable predicates and guards, embedded into the adapter.
- `scripts/calibrate_thresholds.py` and `scripts/evaluate_decisions.py` with the release gate; `scripts/decision_history.py` shared helpers.
- Validator: `validate()` API, binding/policy/question-type/predicate/fallback-safety checks, secret detection.
- Example bundle gains bindings and is regenerated from `init_action_bundle.py --example` (drift is a pre-commit failure).
- Repo layout: README, MIT license, `scripts/install.sh`, `.githooks/pre-commit`.

## 0.1.0 (2026-09-21)

- Discovery skill, bundle schema, initializer, scanner, validator, surface compiler, minimal JEV runner, first `generate_adapter.py`.
