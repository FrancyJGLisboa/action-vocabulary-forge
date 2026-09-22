# Generated adapter contract

`generate_adapter.py` produces a Python module from the reviewed Action Bundle.
It is a runtime boundary, not an autonomous agent.

## Generated responsibilities

The module contains:

- immutable action, state, surface, and question registries (`ACTIONS`, `STATES`, `SURFACES`, `QUESTIONS`, `POLICY`, `HANDLER_STATUS`);
- `build_payload()` exposing only the compiled criteria, plus runtime criteria for `criteria_source: dynamic` questions;
- raw HTTP JEV support using `TYPESAFE_API_KEY` and `TYPESAFE_API_URL`, with `model` and `endpoint` taken from `jev_adapter_spec.yaml`;
- response parsing for `Choice`, `Noul`, and `Score` questions, with `IllegalChoice` on anything outside the legal set;
- `threshold_for()` / `apply_policy()`: per-action, per-question, then global thresholds; an uncalibrated action abstains unless `policy.default_when_uncalibrated: allow`;
- the embedded predicate evaluator (`check_preconditions`, `infer_state`, `legal_actions`);
- `execute()` gates, in order: action exists, `allowed_from_states`, `requires_confirmation`, preconditions, host guard, abstention lands on a declared fallback (or was escalated, and then never onto an action with `requires_confirmation`), handler exists;
- `decide(..., escalate=fn)` / `run(..., escalate=fn)`: an abstained decision goes to `fn(context, decision, legal)`, which sees only the question's non-fallback actions legal from the state and returns one of them or `None` (keep the fallback); the result carries `decided_by: escalation`;
- `DecisionLog` and `decision_record()`: one JSONL record per decision or execution attempt;
- generated handlers, one function per action, and the `HANDLERS` table.

## Generated handlers

| `HANDLER_STATUS[action_id]` | Rendered as | Why |
|---|---|---|
| `generated:python_callable` | `importlib` the module named in `locator` (`pkg.module:callable`) and call it with the mapped arguments | invocation observed |
| `generated:http` | `urllib` request to the `locator` URL template with `method`, `headers`, JSON `body`, and `Authorization: Bearer $auth_env` | invocation observed |
| `generated:cli` | `subprocess.run(argv, shell=False)` with templated `argv`, `cwd`, `env_allowlist`, `timeout_seconds` | invocation observed |
| `stub:no_binding` | raises `HandlerUnavailable` | the action has no `binding` |
| `stub:unobserved_binding` | raises `HandlerUnavailable` naming the locator and current evidence refs | no `verified_runtime` / `observed_trace` evidence for the binding |
| `generated:mcp` | `tools/call` through the MCP Python SDK (stdio subprocess or streamable HTTP), or through the host's `set_mcp_caller(fn)` | invocation observed |
| `generated:ui` | Playwright: open `url`, then `operation` on `locator`; on the host's page via `set_ui_page(page)` or a headless browser launched per call | invocation observed |

Override any entry with `HANDLERS[action_id] = callable(state)`. A generated handler receives the call state `{**state, "decision": <decision record>}`, so `arg_mapping` paths may reference `record_id`, `context.row.id`, or `decision.selected_choice`. Without `arg_mapping`, arguments are the action's `parameters[].name` looked up in the call state; a missing required parameter raises `ExecutionBlocked` before any side effect. A handler that attempted the side effect and failed raises `HandlerError`.

`ui` and `mcp` handlers import their SDK only when nothing was injected; a missing SDK raises `ExecutionBlocked` naming the install command, never a silent no-op. Hosts that already own a browser page or an MCP session inject them once at startup.

Secrets never enter the bundle or the generated file: `auth_env` names an environment variable read at call time, and the validator rejects binding values that look like tokens.

## Host usage

```python
import generated_adapter as adapter

log = adapter.DecisionLog("decision_log.jsonl")
decision, result = adapter.run(
    {"error": "timeout", "retry_budget": 1},
    {"state_id": "validation_failed", "record_id": "r-123", "retry_budget": 1, "record_exists": True},
    case_id="r-123",
    log=log,
    guard=lambda action_id, state: state["retry_budget"] > 0,   # optional
)
```

`run()` = `decide()` (classify + policy) + `execute()` with one log record. Use `decide()` alone for shadow mode. Pass `transport=callable(payload) -> response` to replace the HTTP call in tests. Dynamic questions take `dynamic_choices={question_id: {choice_id: criterion}}`; any runtime choice maps to the question's `dynamic_executor_action_id` and its raw id is kept in `Decision.selected_choice`.

## Providers

`jev_adapter_spec.yaml: provider` selects the transport the generated `classify()` uses when the
host injects none: `typesafe_system_one_http` (default; `TYPESAFE_API_KEY`, `TYPESAFE_API_URL`)
or `laya` (in-process, self-hosted; `pip install laya "numpy<2"`; `model: laya:typed-decisions`).
`FORGE_PROVIDER=laya|typesafe` overrides at runtime. The Laya transport (embedded from
`scripts/transports.py`) sends one question per `predict`, compacts the context to
`max_state_chars`, and returns the same `answers` shape, stamped `model: laya:<checkpoint>`.
It reports the winning probability as `confidence` (keeping the provider's own value as
`native_confidence`), because providers scale confidence differently and the calibration bins are
fixed; a margin-scaled value would collapse into the lowest bin and never calibrate. The
bundle, thresholds and gate do not change between providers; `evaluate_decisions.py --by-model`
prints the metrics per model on the same log so the accuracy cost of going local is a number.

## Decision log record

`timestamp, system_id, question_id, surface_id, case_id, state_id, selected_choice, proposed_action_id` (before policy), `action_id` (after policy), `confidence, probabilities, threshold, abstained, decided_by (jev|fallback|escalation), reason, legal_actions, executed, outcome (ok|blocked|error|null), blocked_reason, result_summary, handler_status, model, latency_ms, usage, ground_truth_action_id, label_source`. `calibrate_thresholds.py` and `evaluate_decisions.py` read this file; labels may be inline (`ground_truth_action_id`) or in a separate JSONL/CSV keyed by `case_id, question_id`, with an optional `label_source` (missing means `human`).

## Question shapes

`Choice` is the default and maps each choice ID to an executor action. A `Noul`
question declares `yes_action_id` and `no_action_id`; a `Score` question declares
ordered `levels`, each with an `executor_action_id`. Use `abstention_action_id`
for non-Choice fallbacks. Confidence is never a substitute for a deterministic
guard: the adapter re-evaluates preconditions regardless of the threshold.

## What still requires review

The generated file does not prove that a surface is semantically correct, that a
handler is idempotent, or that a permission is sufficient. Validate the bundle,
run `evaluate_decisions.py` on a held-out split, and answer the release gate
before enabling mutating operations.
