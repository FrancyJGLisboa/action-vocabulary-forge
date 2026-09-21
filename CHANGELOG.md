# Changelog

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
