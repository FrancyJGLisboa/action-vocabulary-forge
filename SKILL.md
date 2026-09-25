---
name: decision-system-forge
description: Take repositories, URLs, paths, code, documents, SOPs, logs, traces, or resolved cases directly; reconstruct hidden work systems and discover repeated human, agentic, and software decisions without asking the user to author Forge YAML. Without history, create source-backed maps and an observation plan; with cases, compile the safest bounded candidate into a measured JEV shadow workflow. Use for hidden workflow discovery, bounded decision software, or classifier-like LLM audits; not generic summarization or unbounded autonomy.
---

# Semantic Decision Compilation

## Explicit invocation

- Codex: `$decision-system-forge <goal>`
- Claude Code: `/decision-system-forge <goal>`

Treat the directory containing this `SKILL.md` as `SKILL_ROOT`. Resolve it from
the loaded skill path and invoke packaged scripts with absolute paths such as
`python3 "$SKILL_ROOT/scripts/forge.py"`. Never assume the user's working
directory is the skill repository.

Unless the user supplies another location, persist the product project under
`<current-workspace>/.forge/<system-id>/`. Do not write generated project data
inside `SKILL_ROOT` when the skill is being used from another workspace.

## Public product journey

Use the thin product-facing lifecycle first. `start` accepts repositories,
URLs, paths, documents, or resolved cases and routes to the proven compiler;
`inspect` shows the current stage; `continue` reports the next safe action and
stops when required human input is missing. The canonical transition and safety
contract is [`product_lifecycle.yaml`](product_lifecycle.yaml). Commands such as
`reconstruct`, `prepare-integration`, and `run-controller-shadow` remain
advanced implementation and audit entry points.

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

## Default product workflow

When the user asks the Forge to discover decisions, find automation
opportunities, or build a decision system, use the product lifecycle in
`scripts/forge.py`. Do not expose bundle files or coordinate low-level compiler
scripts unless the user asks to inspect or debug them.

Choose the entry point from the available evidence:

1. **Heterogeneous evidence describing a larger system:** pass every accessible
   repository, HTTPS URL, file, or directory directly to `forge.py reconstruct`.
   Do not ask the user to build a manifest. The deterministic first phase hashes
   and snapshots bounded textual evidence and stops at `evidence_inventoried`.
   Read `reconstruction_request.md` and every inventoried snapshot, complete the
   generated `work_system_proposal.yaml` as System 2, then run `reconstruct`
   again with `--proposal`. Every claim must quote an inventoried source and
   retain `declared`, `observed`, `inferred`, `unknown`, or `conflict` status.
   Read [work-system reconstruction](references/work-system-reconstruction.md).
   After the project reaches `work_system_mapped`, run
   `forge.py prepare-integration <project>` to compile a non-executing
   Integration Package. It makes the adapter, legal-action, controller, loop,
   verification, telemetry, and binding contracts explicit. Bindings derived
   only from reconstruction evidence remain stubs with no authority. Read
   [integration packages](references/integration-package.md). Do not implement,
   promote, or execute those stubs unless the user separately asks for the
   integration work and the required runtime evidence can actually be produced.
   When that work is authorized, have the implementation agent author a binding
   proposal and run `forge.py propose-bindings`. The Forge validates exact
   source evidence and typed locators without importing the target. A controlled
   host—not the Forge—may then exercise each candidate and emit observations for
   `forge.py verify-bindings`. Require both a successful transition and a safe
   negative/failure case. The maximum promotion is `verified_for_shadow` with
   `executable: false` and no production authority. Read
   [binding verification](references/binding-verification.md).
   After bindings reach `bindings_verified_for_shadow`, complete a source-cited
   `controller_proposal.yaml` and run `forge.py prepare-controller`. Require
   mutually exclusive observable states, exact deterministic legality for every
   action, one unconditional fallback, a confidence threshold, terminal state,
   full stop conditions, and a finite iteration limit. The host may inject JEV
   through the generated callback contract, but the controller must offer only
   legal action IDs and must never invoke bindings. Read
   [shadow controller](references/shadow-controller.md).
   When `TYPESAFE_API_KEY` is available, run that reviewed controller with
   `forge.py run-controller-shadow --observations <observable-state.jsonl>`.
   Inspect `integration/shadow_runs/`, require zero binding invocations, and
   never describe a shadow receipt as execution authority.
2. **No resolved cases or named decision surface:** run `forge.py scan` over the
   operating material. Treat every result as a source-backed hypothesis. Explain
   the Decision Opportunity Map and the proposed event contract. Do not create a
   JEV bundle, approve shadow mode, or infer an executable binding.
3. For a promising bounded hypothesis, run `forge.py select`. This creates an
   instrumentation specification and JSON Schema but does not edit the source
   application. After the user or host emits cumulative JSONL events, run
   `forge.py observe` to build a descriptive Decision System Map.
4. **Resolved cases available:** run `forge.py discover` to create a persistent
   project and `review.md`. Explain ranked candidates and blocking gaps.
5. Do not approve on the user's behalf. After a named domain owner confirms the
   vocabulary and historical labels, run `forge.py approve-shadow`.
6. Run `forge.py shadow` only after that recorded approval. Shadow mode executes
   no actions.
7. Report measured evidence and the project's declared next action. Never
   describe a scan hypothesis or shadow accuracy as production approval.

Cold start:

```bash
python3 "$SKILL_ROOT/scripts/forge.py" reconstruct \
  --project ./.forge/company-workflow \
  --repo https://github.com/example/operations \
  --url https://example.com/operations-policy.md \
  --source ./material \
  --system-id company_workflow

# After reading reconstruction_request.md and the acquired snapshots, replace
# the empty lists in work_system_proposal.yaml with concise, cited structure.
python3 "$SKILL_ROOT/scripts/forge.py" reconstruct \
  --project ./.forge/company-workflow \
  --proposal ./.forge/company-workflow/work_system_proposal.yaml

python3 "$SKILL_ROOT/scripts/forge.py" prepare-integration \
  ./.forge/company-workflow

# Only after the user authorizes integration implementation:
python3 "$SKILL_ROOT/scripts/forge.py" propose-bindings \
  ./.forge/company-workflow --proposal ./binding_proposal.yaml
python3 "$SKILL_ROOT/scripts/forge.py" verify-bindings \
  ./.forge/company-workflow --observations ./binding_observations.jsonl
python3 "$SKILL_ROOT/scripts/forge.py" prepare-controller \
  ./.forge/company-workflow --proposal ./controller_proposal.yaml
python3 "$SKILL_ROOT/scripts/forge.py" run-controller-shadow \
  ./.forge/company-workflow --observations ./observable-states.jsonl

python3 "$SKILL_ROOT/scripts/forge.py" scan \
  --project ./.forge/company-workflow \
  --source ./material \
  --system-id company_workflow
python3 "$SKILL_ROOT/scripts/forge.py" status ./.forge/company-workflow
python3 "$SKILL_ROOT/scripts/forge.py" select ./.forge/company-workflow \
  --opportunity candidate_id
python3 "$SKILL_ROOT/scripts/forge.py" observe ./.forge/company-workflow \
  --events ./decision-events.jsonl
```

For direct reconstruction, never invent quotes, silently omit a supplied source,
or edit `evidence_manifest.yaml`. If evidence cannot support a node, link, or
workflow, record the gap instead. A compiler failure is evidence that the
proposal must be corrected; it is not permission to weaken the validator. Keep
the whole acquire → System 2 proposal → compile cycle inside one skill invocation.

The cold-start scanner currently performs conservative Python AST discovery plus
explicit TypeScript/TSX vocabulary and DOT transition discovery. Other source
forms remain inventoried material. An LLM/System 2 may propose additional
candidates through `--proposal`, but each proposal must cite exact inventoried
source lines and every bounded action must appear in that evidence. Proposal
output remains hypothesis-only. Follow
[cold-start discovery](references/cold-start-discovery.md) for the two-pass
System 2 workflow and proposal contract.

`observe` treats the supplied JSONL as a cumulative immutable snapshot. It
validates the selected action vocabulary, actor, timestamps, and absence of
private reasoning. The Forge persists only source hashes and aggregate paths;
raw state, event IDs, and case IDs remain in the external event source.

Observed-history path:

```bash
python3 "$SKILL_ROOT/scripts/forge.py" discover \
  --project ./forge-project \
  --source ./material \
  --cases ./resolved-cases.jsonl \
  --surface-field decision_type \
  --system-id company_workflow
python3 "$SKILL_ROOT/scripts/forge.py" status ./forge-project
```

Use the lower-level workflow below for compiler development, custom bundle
audits, or when a product-stage diagnostic requires it.

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
