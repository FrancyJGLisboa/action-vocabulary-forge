# Semantic Decision Bundle schema

The semantic layer sits before the Action Bundle. It turns heterogeneous material
into reviewable semantic structure and supporting JEV judgments. It never grants
permission or executes an action.

## `material_manifest.yaml`

```yaml
schema_version: "2.0"
system_id: support_system
scope: Route refund requests under the owned refund policy.
sources:
  - source_id: refund_policy_v3
    kind: sop
    locator: docs/refunds.md#duplicate-charge
    authority: authoritative
    included: true
```

`kind` is `code`, `api`, `ui`, `sop`, `document`, `log`, `trace`, `dataset`,
`schema`, `analysis`, or `other`. `authority` is:

- `authoritative`: defines an owned rule or interface;
- `operational`: records what the running system or operators did;
- `contextual`: useful context that cannot establish production behavior.

Every `evidence_ledger.jsonl.source_id` in a Semantic Decision Bundle must appear
in this manifest. A locator identifies the material; it is not permission to read
outside the task scope.

## `semantic_ir.yaml`

```yaml
schema_version: "2.0"
system_id: support_system
concepts:
  - concept_id: duplicate_charge
    kind: condition
    description: Two captured charges for the same order and amount.
    observable: true
    value_type: boolean
    evidence_refs: [policy_duplicate_charge, schema_charge]
relations:
  - relation_id: duplicate_charge_supports_refund
    subject_id: duplicate_charge
    predicate: supports
    object_id: refund_eligibility
    evidence_refs: [policy_duplicate_charge]
```

Concept `kind` is `entity`, `state_variable`, `fact`, `condition`, `policy`,
`outcome`, `semantic_property`, `action_concept`, or `other`. Relations connect
concepts without silently turning correlation or examples into rules.

## `judgment_registry.yaml`

Families describe reusable question shapes; judgments are concrete, reviewable
instruments used by runtime surfaces.

```yaml
schema_version: "2.0"
system_id: support_system
families:
  - family_id: policy_supports_action
    type: noul
    instructions_template: Does {policy} support {action} for `context.request`?
    parameters: [policy, action]
    purpose: Create policy-to-action evidence features.
    maturity: candidate
    evidence_refs: [policy_duplicate_charge]
judgments:
  - judgment_id: refund_policy_supports_request
    family_id: policy_supports_action
    type: noul
    instructions: Does `context.refund_policy` support the refund requested in `context.ticket`?
    criteria:
      true: The policy explicitly covers this request.
      false: The policy excludes it or the supplied evidence is insufficient.
    state_paths: [context.ticket, context.refund_policy]
    activate_when: [request_type == 'refund']
    surface_ids: [resolve_refund]
    purpose: Give the final action choice an inspectable policy-support judgment.
    maturity: reviewed
    evidence_refs: [policy_duplicate_charge]
```

Types are `choice`, `noul`, and `score`. Choice criteria are an option mapping;
Score criteria are an ordered list; Noul criteria are optional `true`/`false`
clarifications. Each question makes one narrow judgment and names every state path
needed to answer it.

Maturity is:

- `candidate`: generated or plausible; never compiled into runtime;
- `reviewed`: source-supported and eligible for shadow/runtime requests;
- `calibrated`: reviewed plus evaluation metadata from trusted labels;
- `retired`: retained for lineage, excluded from runtime.

A `calibrated` judgment includes an `evaluation` mapping naming its label source,
sample size, date, and relevant metric. Reviewed and calibrated judgments require
at least one production-grade evidence reference.

Judgments must not contain `authorizes_actions`, `legal_actions`, `legal_when`, or
`preconditions`. Legality belongs to `action_registry.yaml` and runtime code.

## `semantic_links.yaml`

```yaml
schema_version: "2.0"
system_id: support_system
links:
  - link_id: policy_to_refund_judgment
    from: {kind: source, id: refund_policy_v3}
    to: {kind: judgment, id: refund_policy_supports_request}
    relation: supports
    evidence_refs: [policy_duplicate_charge]
  - link_id: judgment_to_refund_surface
    from: {kind: judgment, id: refund_policy_supports_request}
    to: {kind: surface, id: resolve_refund}
    relation: informs
    evidence_refs: [policy_duplicate_charge]
```

Endpoint kinds are `source`, `concept`, `family`, `judgment`, `state`, `surface`,
and `action`. Links make the compiled path traversable without claiming that one
semantic edge authorizes the next operational step.

## Surface extension

`decision_surfaces.yaml` may declare:

```yaml
supporting_judgments:
  - refund_policy_supports_request
```

The judgment must link back to the surface through `surface_ids`. Candidate and
retired judgments are ignored by the runtime compiler.

## Two-stage runtime plan

`semantic_runtime.py` emits:

1. `judgment_request`: active reviewed/calibrated judgments sharing the current state;
2. `action_request`: a final Choice whose criteria contain only actions legal from
   the observable state and whose deterministic preconditions pass.

The action request receives the first stage answers under `semantic_judgments`.
Those answers are context, not authorization. One legal action is selected in code;
zero legal actions or an illegal fallback stops compilation.

The first semantic runtime version requires the final action classifier to be a
static `Choice`. Supporting judgments may use all three primitive types. Existing
Action Bundles retain Choice/Noul/Score final classifiers through the generated
adapter.

## Search index

`semantic_index.py build` creates a generated SQLite FTS5 index across sources,
concepts, relations, families, judgments, states, actions, surfaces, and evidence.
The database is derived output: rebuild it when the bundle changes; do not treat it
as evidence or commit it unless the host workflow explicitly requires that.

## Evaluation

`JudgmentLog` writes one raw answer per line using fields accepted by
`scripts/jev_gate.py`: `case_id`, `question_id`, raw `jev` answer, model, and usage.
Trusted labels evaluate supporting judgments. The generated action adapter continues
to write `decision_log.jsonl`, which drives per-action calibration and the final
release gate.
