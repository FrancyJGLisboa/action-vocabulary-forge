# Changelog

## 0.4.0 (2026-09-22)

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
