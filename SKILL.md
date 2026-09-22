---
name: action-vocabulary-forge
description: Discover and compile bounded decision surfaces in an application, website, API, codebase, SOP, documentation set, log stream, or mixed system, then produce JEV-ready states, legal action sets, boundaries, and evidence. Use when an agent must find complex-input-to-finite-choice decisions or audit a generative call for classifier replacement. Do not use for generic summarization or for executing actions in a live system.
---

# Decision Surface Discovery

Transform an environment into a reviewable **Decision Surface Bundle**. The primary output is a bounded decision surface: a real point where complex context is reduced to a small set of next actions. The action vocabulary, states, transitions, and evidence are the supporting control plane.

## Non-negotiable model

Keep these questions separate:

1. **What exists?** Discover candidate actions and states from evidence.
2. **What is legal now?** Deterministic predicates and permissions filter the registry.
3. **Which legal action fits the context?** JEV selects from the filtered set.
4. **What executes?** A normal adapter performs the selected action and records the resulting state.

Never ask JEV to authorize an action that code can reject deterministically. Never invent an executable action from an LLM guess. An action with `inferred` or `hypothetical` evidence may remain in the ledger, but cannot enter a production decision surface.

## Deliverable

Start with opportunity discovery. Look for a complex input, a finite output set, and semantic interpretation. Prioritize surfaces that are currently implemented as an LLM or other generative call. A function named approve() is only an action candidate; a prompt that asks a model to choose from {approve, reject, retry, escalate} is a decision-surface candidate.

Create these files in the requested output directory. Use the initializer when starting from nothing:

```bash
python3 scripts/init_action_bundle.py ./action-bundle --example
```

The eight files are:

```text
action-bundle/
├── surface_candidates.yaml
├── action_registry.yaml
├── state_registry.yaml
├── transition_graph.yaml
├── decision_surfaces.yaml
├── evidence_ledger.jsonl
├── jev_adapter_spec.yaml
└── coverage_report.md
```

Run the validator before presenting the bundle:

```bash
python3 scripts/validate_action_bundle.py ./action-bundle
```

For a concrete runtime choice set, compile one surface for one observable state:

```bash
python3 scripts/compile_jev_surface.py ./action-bundle \
  --state validation_failed --surface resolve_validation_failure
```

The compiler is deterministic. Its JSON is the input boundary for a small JEV adapter, not a JEV prediction.

Generate the first adapter implementation from the reviewed bundle:

    python3 scripts/generate_adapter.py ./action-bundle \
      --output ./action-bundle/generated_adapter.py

The generated Python module builds the JEV request, validates `Choice`, `Noul`,
or `Score` answers, applies the declared abstention and confidence policy,
re-checks `preconditions` and `allowed_from_states`, writes a decision log, and
renders one handler per action from the action's `binding`. A handler executes
only when the binding has `verified_runtime` or `observed_trace` evidence;
otherwise it is a stub that names the missing evidence. The generator never
invents HTTP calls, browser clicks, CLI commands, permissions, or side effects
from descriptions alone: it only executes what discovery recorded and observed.

Then close the loop with the decision log:

    python3 scripts/calibrate_thresholds.py ./action-bundle \
      --log decision_log.jsonl --labels labels.jsonl --write
    python3 scripts/evaluate_decisions.py ./action-bundle \
      --log decision_log.jsonl --labels labels.jsonl

Calibration writes per-question and per-action thresholds into
`jev_adapter_spec.yaml: policy`; an empty policy makes every non-fallback action
abstain. The evaluator prints the references/evaluation.md metrics on the
held-out split and a `RELEASE GATE: APPROVE | HOLD` line. The human's remaining
role is to grant credentials, answer the release gate, and resolve abstentions.
Abstentions on reversible actions can go to a second decider first
(`run(..., escalate=fn)`, logged as `decided_by: escalation`); the gate still
judges human labels only (`label_source`), so that decider never grades itself.

When TypeSafe credentials are available, run the compiled surface through JEV:

    python3 scripts/run_jev_choice.py /tmp/compiled-surface.json \
      --context "The validation failed with a transient timeout; retry budget remains."

The adapter uses the current TypeSafe HTTP shape: model jev-latest, endpoint /v1/systemone, a Choice question, and criteria as an object. It validates that the returned choice is one of the compiled action IDs before exposing it to an executor. Keep TYPESAFE_API_KEY server-side and never write it into bundle artifacts.

For a codebase-first audit of generative calls, run:

    python3 scripts/scan_llm_opportunities.py ./path/to/source \
      --output ./action-bundle/surface_candidates.yaml

Treat that scan as candidate generation. Review each candidate against source evidence before promoting it into decision_surfaces.yaml.

## Discovery workflow

### 1. Find decision-surface candidates

Search for:

- model calls whose prompt contains a closed list, enum, JSON schema, or routing instruction;
- handlers, workflow nodes, approval gates, and triage queues where one of a few outcomes follows complex context;
- repeated human or agent traces with the same small action set;
- UI, API, or tool states where deterministic guards leave a semantic choice.

Record each candidate in surface_candidates.yaml with the input, observed options, current implementation, evidence, and a strength rating. A candidate is strong when it has a bounded output set, semantic interpretation is required, and the current implementation is generative.

### 2. Inventory the sources and their authority

Record every inspected source in `evidence_ledger.jsonl`. Prefer independent convergence:

- runtime/API schemas and tool definitions;
- code routes, commands, handlers, enums, guards, tests, and workflow nodes;
- UI controls, accessibility tree, navigation targets, and network operations;
- SOP/documentation rules (`MUST`, `MUST NOT`, `IF/THEN`, approve, reject, retry, escalate, publish);
- logs, traces, and human decisions with `before_state`, `context`, `chosen_action`, `result`, and `after_state`.

Use `source_id` values that are stable and point to a file, URL, route, log query, or trace slice. Do not silently merge two differently named operations.

### 3. Canonicalize candidates

Create one `action_id` for one semantic operation. Merge aliases only when at least two sources support the same meaning and side effects. Keep the aliases and all supporting evidence in the action record. Examples: a UI “Publish” button, `POST /reports/{id}/publish`, `publish_report()`, and `REPORT_PUBLISHED` can converge on `publish_report`.

Create observable states only when the state changes the legal action set. Prefer predicates over subjective labels:

```yaml
state_id: candidate_ambiguous
observable_predicate: "eligible_candidate_count > 1"
```

### 4. Write action contracts

Every action must state:

- `description`, `choose_when`, and `do_not_choose_when`;
- deterministic `preconditions` and `allowed_from_states`;
- `parameters`, `outputs`, and `side_effects`;
- `risk` (`low`, `medium`, `high`, or `critical`) and `reversible`;
- `destination_states` and `success_condition`;
- evidence references, examples, and counterexamples;
- a `binding` for every action you expect the adapter to execute: `kind`
  (`python_callable`, `http`, `cli`, `mcp`, `ui`), `locator`, `arg_mapping`, and
  `evidence_refs` pointing at ledger entries that show the invocation. Record
  the binding at discovery time from what you observed (the function that was
  called, the request that was sent, the command that ran). Never write a
  secret into a binding; name the environment variable in `auth_env`.

Actions that mutate data, publish, delete, send, merge, or otherwise have external side effects require explicit preconditions. Irreversible or critical actions also require a human confirmation or policy gate in the contract and must not be a fallback action unless the user explicitly requests that policy.

### 5. Compile local decision surfaces

A decision surface is a small, state-specific choice set. It must include:

- an observable activation state or predicate;
- two or more candidate actions when a semantic choice exists;
- the criterion that distinguishes each candidate;
- a safe `fallback_action` and/or `abstention_choice`;
- evidence references for the surface and each candidate.

Include `no_valid_action`, `insufficient_information`, or `human_review` when the environment can produce an out-of-set case. Do not force a choice merely to keep the classifier exhaustive.

For large vocabularies, compose hierarchical surfaces (domain → operation family → concrete action) or generate the surface from the current state. Keep each JEV call small and auditable.

### 6. Define the vendor-neutral adapter

`jev_adapter_spec.yaml` should map each classifier question to exactly one surface and map every choice to an executor `action_id`. Keep provider-specific SDK fields out of the bundle. A JEV adapter may add model configuration later, but it must preserve choice IDs, abstention behavior, and the evidence boundary.

The generated adapter is the mechanical implementation of this boundary. Its
handlers come from the bindings: `python_callable`, `http`, `cli`, `mcp`
(MCP SDK) and `ui` (Playwright) bindings with observed evidence become
executable code; any binding without observed evidence becomes a stub that the
host overrides with `HANDLERS[action_id] = callable`. The host may still pass a deterministic
`guard`, and the adapter re-evaluates every action precondition before the
side effect. Read [references/adapter-generation.md](references/adapter-generation.md).

### 7. Validate and report coverage

The validator must fail on unknown references, duplicate IDs, illegal transitions, invalid evidence grades, production surfaces using weak evidence, missing fallbacks, and risky actions without guards. `coverage_report.md` should state inspected source types, supported states, actions with independent evidence, unresolved candidates, and explicit blind spots. A clean validator result means the bundle is structurally coherent; it does not prove semantic correctness, so retain human review for the registry and high-risk surfaces. The validator also rejects bindings without evidence, binding values that look like secrets, unparseable predicates, fallbacks that are irreversible or critical, and policy entries that reference unknown questions or actions.

### 8. Calibrate and evaluate

Run the generated adapter with `log=DecisionLog(path)` (or set `FORGE_DECISION_LOG`). Once trusted labels exist for the logged cases, calibrate the thresholds and evaluate the held-out split:

    python3 scripts/calibrate_thresholds.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl --write
    python3 scripts/evaluate_decisions.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl

Calibration picks, per question and per proposed action, the lowest confidence bin whose cumulative accuracy stays above `--min-accuracy` (default 0.97) and needs `--min-samples` (default 30) records per group; groups without enough evidence stay uncalibrated and abstain. The evaluator holds the release until the held-out set shows zero illegal actions, every question has an abstention path, every exercised action has a threshold, and boundary and abstention accuracy meet their minimums. Present the `RELEASE GATE` block to the human; do not enable a surface in production before they answer it.

## Opportunity audit

For every promoted surface, record whether it can replace or reduce a generative call:

    bounded_output: true
    semantic_interpretation_required: true
    current_implementation: generative_call
    replacement_strength: strong

Do not claim replacement from syntax alone. Confirm that the finite options are stable, the semantic boundary is documented by examples or traces, and a safe abstention exists. Leave open-ended investigation and novel reasoning with the frontier model.

Evaluate the bundle on action recall, action precision, surface coverage, boundary accuracy, abstention accuracy, illegal-action rate, JEV accuracy, replacement rate, cost, and latency. Read references/evaluation.md for the benchmark protocol.

## Evidence grades

Use exactly these grades:

| Grade | Meaning | Production decision surface |
|---|---|---|
| `verified_runtime` | Observed in a running system or test | allowed |
| `verified_schema` | Present in an authoritative API/tool/schema | allowed |
| `documented` | Explicitly specified by an owned SOP or document | allowed |
| `observed_trace` | Repeatedly observed in a trace or human decision log | allowed |
| `inferred` | Plausible synthesis without direct support | forbidden |
| `hypothetical` | Proposed future capability | forbidden |

When sources disagree, preserve both records, mark the action as unresolved, and route the surface to `human_review` instead of silently choosing one interpretation.

## Output language

Use the user's language for descriptions and criteria when practical, but keep IDs stable, lowercase, and `snake_case`. Do not include secrets, credentials, personal data, or copied proprietary logs in the bundle; reference them by a redacted source locator.

## References

- Read [references/bundle-schema.md](references/bundle-schema.md) when creating or reviewing artifact fields.
- Read [references/source-playbook.md](references/source-playbook.md) when the input mixes code, UI, APIs, SOPs, and traces.
