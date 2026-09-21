# Action Bundle schema

The bundle uses YAML for human review and JSONL for append-only evidence. IDs are stable snake_case strings. Empty lists are valid, but production decision surfaces should not be empty.

## surface_candidates.yaml

This is the discovery inventory, before promotion into a production decision surface:

    schema_version: "1.0"
    discovery_mode: heuristic_static_scan
    source_root: ./src
    review_required: true
    surface_candidates:
      - candidate_id: customer_email_route
        input_description: Customer email and account context.
        observed_options: [refund, escalate, request_information, close]
        bounded_output: true
        semantic_interpretation_required: true
        current_implementation: generative_call
        replacement_strength: strong
        source_locator: src/support/router.py:42
        review_status: candidate

The scanner is deliberately conservative and heuristic. A candidate is not executable evidence. Promote it only after the source, output labels, boundary examples, and abstention path have been reviewed.

## action_registry.yaml

Example:

    schema_version: "1.0"
    system_id: example_system
    actions:
      - action_id: human_review
        description: Pause automation and ask a person to decide.
        aliases: ["manual review", "escalate"]
        choose_when: Evidence is insufficient or interpretations remain ambiguous.
        do_not_choose_when: A deterministic rule resolves the case.
        preconditions:
          - unresolved_semantic_ambiguity == true
        parameters:
          - name: evidence
            type: object
            required: true
        outputs:
          - human_decision
        side_effects:
          - pauses_automation
        risk: low
        reversible: true
        requires_confirmation: false
        allowed_from_states: [candidate_ambiguous]
        destination_states: [awaiting_human_review]
        success_condition: human_decision_recorded
        evidence_refs: [doc_review_policy]
        examples: []
        counterexamples: []

Required action fields: action_id, description, choose_when, do_not_choose_when, preconditions, parameters, outputs, side_effects, risk, reversible, allowed_from_states, destination_states, success_condition, and evidence_refs.

### binding (optional, per action)

The binding records how the action is invoked, so the generated adapter can execute it:

    binding:
      kind: python_callable          # python_callable | http | cli | mcp | ui
      locator: validation_client:retry
      arg_mapping:                   # parameter name -> dotted path into the call state
        record_id: record_id
      evidence_refs: [binding_retry_call]   # REQUIRED; ledger entries that show the invocation
      timeout_seconds: 30

Kind-specific fields:

- `python_callable`: `locator` is `module.path:callable`.
- `http`: `locator` is an `https://` URL template (`{record_id}`), plus `method`, `headers` (non-secret), `body` (`field: dotted.path`), and `auth_env` (environment variable whose value becomes `Authorization: Bearer ...`).
- `cli`: `argv` (list of templates), `cwd`, `env_allowlist`.
- `mcp`: `server` and `tool`. `ui`: `locator` is the page/selector. Both render as stubs the host overrides.

Grade rule: the adapter generates a real handler only when at least one `binding.evidence_refs` entry has grade `verified_runtime` or `observed_trace` (the invocation was seen to run). `documented` or `verified_schema` prove existence, not invocation, and render a stub that names the missing evidence. The validator rejects a binding without evidence, an `arg_mapping` key that is not a declared parameter, and any binding value that looks like a secret.

### Predicates

`preconditions[]`, `observable_predicate`, and transition `guard[]` share one grammar, evaluated by the generated adapter:

    predicate := clause ( 'and' clause )*
    clause    := IDENT OP value              # IDENT may be dotted: decision.confidence
    OP        := '==' | '!=' | '>=' | '<=' | '>' | '<'
    value     := NUMBER | true | false | null | 'string' | "string" | IDENT

A bare identifier on the right resolves to the state key of that name when present, otherwise to the literal string (`choice == none_of_candidates`). A missing left-hand key makes the clause false. The validator rejects predicates that do not parse.

## state_registry.yaml

Example:

    schema_version: "1.0"
    system_id: example_system
    states:
      - state_id: candidate_ambiguous
        description: More than one candidate remains plausible.
        observable_predicate: "eligible_candidate_count > 1"
        entry_evidence_refs: [trace_ambiguous_01]
        terminal: false

A state must have an observable predicate or a named runtime field. Do not use labels such as “looks hard” without an observable rule.

## transition_graph.yaml

Example:

    schema_version: "1.0"
    system_id: example_system
    transitions:
      - transition_id: ambiguous_to_review
        source_state: candidate_ambiguous
        action_id: human_review
        destination_state: awaiting_human_review
        guard:
          - unresolved_semantic_ambiguity == true
        outcome: review_ticket_created
        evidence_refs: [trace_ambiguous_01]

Every transition references known states and an action allowed from the source state. If a workflow has a failure branch, represent it explicitly.

## decision_surfaces.yaml

Example:

    schema_version: "1.0"
    system_id: example_system
    decision_surfaces:
      - surface_id: resolve_validation_failure
        description: Choose the next step after a validation failure.
        activation:
          state_id: validation_failed
        candidate_actions:
          - action_id: retry
            criterion: Failure appears transient and retry budget remains.
            evidence_refs: [runbook_retry, trace_retry_01]
          - action_id: human_review
            criterion: The failure is ambiguous or retry budget is exhausted.
            evidence_refs: [runbook_review, trace_review_01]
        fallback_action: human_review
        abstention_choice: human_review
        production: true
        evidence_refs: [runbook_retry, runbook_review]

A surface is not a global action list. It is a local choice set activated by an observable state or predicate. fallback_action and abstention_choice must be safe, known candidate actions.

## evidence_ledger.jsonl

One JSON object per line:

    {"evidence_id":"runbook_retry","source_id":"docs/validation.md#retry","source_type":"sop","locator":"docs/validation.md#retry","claim":"Transient validation errors may be retried.","grade":"documented","observed_at":null,"notes":""}

Required keys: evidence_id, source_id, source_type, locator, claim, grade. Valid grades are verified_runtime, verified_schema, documented, observed_trace, inferred, and hypothetical.

## jev_adapter_spec.yaml

Example:

    schema_version: "1.0"
    system_id: example_system
    provider: vendor_neutral
    model: jev-latest                        # optional; default jev-latest
    endpoint: https://api.typesafe.ai/v1/systemone   # optional; must be https://
    policy:
      min_confidence: 0.85                   # global default
      default_when_uncalibrated: abstain     # abstain | allow
      min_accuracy: 0.97                     # target used by calibrate_thresholds.py
      calibrated_at: null                    # written by calibrate_thresholds.py --write
      calibration_source: null
      questions:
        next_validation_action:
          min_confidence: 0.90               # per question
          actions:
            retry: 0.92                      # per executor action within the question
    classifier_questions:
      - question_id: next_validation_action
        surface_id: resolve_validation_failure
        type: choice
        instruction: Select the action whose criterion best matches the evidence.
        choices:
          - id: retry
            criterion: Failure appears transient and retry budget remains.
            executor_action_id: retry
          - id: human_review
            criterion: Evidence is ambiguous or retry budget is exhausted.
            executor_action_id: human_review
        abstention_choice: human_review

Choice IDs must map to executor actions on the referenced surface. `type` defaults
to `choice`; a `noul` question uses `yes_action_id` and `no_action_id`, while a
`score` question uses ordered `levels` with `id`, `criterion`, and
`executor_action_id`. Non-Choice questions declare `abstention_action_id`.

Threshold resolution is `policy.questions[q].actions[a]`, then
`policy.questions[q].min_confidence`, then `policy.min_confidence`. An action
with no threshold at any level abstains unless `default_when_uncalibrated: allow`.
Fallback actions are never gated. `calibrate_thresholds.py --write` fills the
`questions` block from the decision log.

A question whose choice set is only known at runtime (retrieved candidates, DOM
affordances) declares `criteria_source: dynamic` and a
`dynamic_executor_action_id`; the static `choices` still carry the abstention
choice. The top-level `dynamic_criteria_source` field is deprecated. Provider
SDK fields belong in the generated adapter, not in the bundle.

Run `scripts/generate_adapter.py` to emit the adapter. The generated module
validates answers, re-checks preconditions and states, logs every decision, and
executes through handlers generated from the action bindings; see
[adapter-generation.md](adapter-generation.md).

## TypeSafe JEV adapter

The included run_jev_choice.py adapter sends:

    {
      "model": "jev-latest",
      "state": {"context": "...", "observable_state": "...", "allowed_actions": ["..."]},
      "questions": {
        "surface_choice": {
          "type": "choice",
          "instructions": "Choose exactly one allowed action.",
          "criteria": {"action_a": "criterion text", "action_b": "criterion text"}
        }
      }
    }

The API returns a flat answers object. The adapter checks the returned choice against the compiled IDs and exposes probabilities and confidence to policy code. A confidence value is not permission to execute; deterministic preconditions must run again.

## coverage_report.md

State what was inspected, what converged, what remains inferred, what states and surfaces are covered, and what the agent could not observe. Include the validator command and date. A coverage report is evidence about the discovery process, not a claim that the source system is complete.
