---
name: decision-system-forge
description: Compile code, APIs, UIs, documents, SOPs, logs, traces, datasets, or mixed material into an evidence-backed and searchable vocabulary of semantic Choice/Noul/Score judgments, observable states, legal actions, decision surfaces, and evaluated JEV runtime adapters. Use when an agent must turn source material into bounded JEV decision software, discover action vocabularies, or audit a generative classifier for replacement. Do not use for generic summarization or unbounded autonomous execution.
---

# Semantic Decision Compilation

Compile the complete path from source material to an evaluated JEV runtime. The
Forge supports two scopes:

- **Semantic bundle:** material → semantic IR → judgments → legal actions → runtime.
- **Action-only bundle:** an existing bounded decision point → legal actions → runtime.

Prefer a Semantic Decision Bundle for new systems. Keep Action Bundles compatible
for focused audits and existing integrations.

## Non-negotiable model

Keep these questions separate:

1. **What does the material support?** Record sources, authority, concepts, relations, and evidence.
2. **What can be judged semantically?** Define narrow Choice, Noul, or Score questions with state inputs and activation rules.
3. **What actions exist and are legal now?** Deterministic predicates and permissions filter the action registry.
4. **Which legal action fits?** JEV selects only from the filtered set; supporting judgments may enrich its state.
5. **What executes and did it work?** A verified adapter performs the action, logs it, and is evaluated against trusted labels.

Never let a semantic judgment authorize an action. Never invent an executable
binding from prose. `inferred` and `hypothetical` evidence may support candidates,
but never reviewed production judgments, actions, or surfaces.

“Arbitrary material” means the Forge can inspect heterogeneous inputs. It does not
mean every input contains enough authority or runtime evidence for automation:

| Available evidence | Maximum valid output |
| --- | --- |
| Descriptive or contextual material | Candidate concepts and judgments |
| Authoritative rules and action definitions | Reviewable decision surfaces |
| Verified bindings, traces, and trusted labels | Calibrated executable runtime |

## Semantic Decision Bundle

Initialize a new bundle:

```bash
python3 scripts/init_semantic_bundle.py ./semantic-bundle --example
```

The semantic front end adds four artifacts to the existing Action Bundle:

```text
semantic-bundle/
├── material_manifest.yaml
├── semantic_ir.yaml
├── judgment_registry.yaml
├── semantic_links.yaml
├── surface_candidates.yaml
├── action_registry.yaml
├── state_registry.yaml
├── transition_graph.yaml
├── decision_surfaces.yaml
├── evidence_ledger.jsonl
├── jev_adapter_spec.yaml
└── coverage_report.md
```

Read [references/semantic-decision-schema.md](references/semantic-decision-schema.md)
when creating or reviewing the semantic files. Read
[references/bundle-schema.md](references/bundle-schema.md) for the action and
adapter files.

## Workflow

### 0. Discover and rank decision surfaces from resolved work

When the user has source material and resolved cases but has not named the
decision surface, start with the offline discovery compiler:

```bash
python3 scripts/discover_decision_system.py \
  --source ./src --source ./SOPs \
  --cases ./resolved-cases.jsonl \
  --surface-field decision_type \
  --system-id company_workflow \
  --output ./forge-discovery
```

Read [references/decision-discovery.md](references/decision-discovery.md) for the
case contract, ranking formula, privacy behavior, and promotion gates. Inspect
`decision_candidates.yaml` and `discovery_report.md`, then review the generated
`candidate_bundle/` with the domain owner.

The local holdout classifier is a discovery baseline, not JEV performance. The
candidate bundle is deliberately non-production, contains no invented bindings,
and requires human approval for non-fallback actions. If evidence is weak or the
actions are unbounded, no bundle is generated.

After that review, measure the real candidate Choice in shadow mode:

```bash
python3 scripts/evaluate_discovered_system.py ./forge-discovery
```

The evaluator refuses a changed source file and illegal model answers, executes
no action, and emits a `jev_gate.py`-compatible log. Historical labels are not
release evidence unless a responsible human explicitly verifies them.

### 1. Scope the decision outcome and inventory material

Start from behavior the resulting software should select, change, show, or hand
off. Record every inspected source in `material_manifest.yaml`, including its
locator and authority. Record each supported claim separately in
`evidence_ledger.jsonl`.

When the input mixes code, UI, APIs, SOPs, and traces, read
[references/source-playbook.md](references/source-playbook.md). Preserve
disagreements instead of resolving them through similarity.

### 2. Compile semantic structure and judgment families

Write concepts and relations into `semantic_ir.yaml`. Create one judgment for one
narrow, independently useful semantic property:

- `choice`: one option from a defined set;
- `noul`: probability that a condition holds;
- `score`: position on ordered, concrete levels.

Every judgment declares its required `state_paths`, `activate_when` predicates,
linked surfaces, maturity, and evidence. Use families for repeated parameterized
questions. Keep large vocabularies virtual: store canonical families and indexed
records, then retrieve and instantiate a small relevant subset at runtime. Do not
send a million questions in one request.

Candidate questions may be generated from material. Promote them to `reviewed`
only after checking source support, candidate coverage, neighboring boundaries,
examples, counterexamples, and the downstream behavior they affect.

### 3. Compile legal action surfaces

Discover and canonicalize observable states, actions, transitions, bindings, and
small state-specific decision surfaces. Every action still needs:

- `choose_when`, `do_not_choose_when`, parameters, outputs, and side effects;
- deterministic preconditions and allowed states;
- risk, reversibility, destination states, and success condition;
- evidence and an observed binding for generated execution.

Add `supporting_judgments` to a surface when semantic features should be evaluated
before its final action Choice. Include a safe fallback or abstention path.

### 4. Validate, index, and compile the runtime plan

```bash
python3 scripts/validate_semantic_bundle.py ./semantic-bundle
python3 scripts/semantic_index.py build ./semantic-bundle ./semantic-bundle/semantic.sqlite
python3 scripts/semantic_index.py search ./semantic-bundle/semantic.sqlite "refund policy"
python3 scripts/semantic_runtime.py ./semantic-bundle \
  --state-file state.json --context-file context.json
```

`semantic_runtime.py` compiles two stages:

1. reviewed supporting judgments active for the current observable state;
2. a final Choice containing only actions whose allowed state and preconditions pass.

The semantic answers enrich stage two but never widen its legal choices. If only
one action is legal, the runtime selects it deterministically without a second JEV
call. If no safe fallback is legal, compilation stops.

### 5. Generate, run in shadow, calibrate, and release

```bash
python3 scripts/generate_adapter.py ./semantic-bundle \
  --output ./semantic-bundle/generated_adapter.py
python3 scripts/calibrate_thresholds.py ./semantic-bundle \
  --log decision_log.jsonl --labels labels.jsonl --write
python3 scripts/evaluate_decisions.py ./semantic-bundle \
  --log decision_log.jsonl --labels labels.jsonl
```

Use `semantic_runtime.run(...)` with the generated adapter to execute the two-stage
path. It writes supporting answers through `JudgmentLog` in the raw format accepted
by `jev_gate.py`; evaluate those judgments separately when labels exist. The action
adapter re-checks preconditions, applies calibrated thresholds, executes only an
observed handler, and writes the final decision log.

Do not enable a mutating surface before the held-out evaluation prints
`RELEASE GATE: APPROVE` and the responsible human approves it. Read
[references/evaluation.md](references/evaluation.md) for metrics and
[references/adapter-generation.md](references/adapter-generation.md) for host use.

## Action-only compatibility

For a known bounded decision point with no semantic front-end work:

```bash
python3 scripts/init_action_bundle.py ./action-bundle --example
python3 scripts/validate_action_bundle.py ./action-bundle
python3 scripts/compile_jev_surface.py ./action-bundle \
  --state validation_failed --surface resolve_validation_failure
```

The original eight-file bundle, validator, adapter generator, calibration, and
evaluation workflow remain supported.

## Evidence and runtime boundaries

Use exactly these evidence grades:

| Grade | Production use |
| --- | --- |
| `verified_runtime` | judgment, surface, action, and executable binding |
| `verified_schema` | judgment, surface, and action; not binding execution by itself |
| `documented` | judgment, surface, and action; not binding execution by itself |
| `observed_trace` | judgment, surface, action, and executable binding |
| `inferred` | candidate only |
| `hypothetical` | candidate only |

The generated runtime is a boundary, not an autonomous agent. Code owns control
flow, permissions, exact calculations, deterministic rules, side effects, and
secrets. JEV supplies typed semantic judgments and probabilities. Humans own
credentials, release approval, disputed labels, and high-risk confirmation.

## Output language

Use the user's language for descriptions and criteria when practical. Keep IDs
stable, lowercase, and `snake_case`. Never copy secrets, credentials, personal
data, or proprietary payloads into the bundle; use redacted source locators and
environment-variable names.
