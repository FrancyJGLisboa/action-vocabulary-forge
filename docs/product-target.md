<!-- product-contract:v1 -->

# Decision System Forge: final product target

## Product promise

A user points Decision System Forge at a decision-bearing environment and gets
an evidence-backed answer to one question:

> Which repeated decisions should remain with System 2, move to deterministic
> code, or be evaluated as bounded JEV judgments?

The environment may contain source code, prompts, skills, workflow definitions,
documents, SOPs, schemas, traces, e-mails, transcripts, resolved cases, or any
combination of them. Language support is an adapter concern, not the product
boundary. When the Forge cannot structurally interpret a source, System 2 may
propose source-cited hypotheses; those hypotheses receive no runtime authority.

The product is successful when it turns an unfamiliar environment into a
reviewable Decision Opportunity Map and can carry one suitable opportunity,
without an authority shortcut, through observation and measured shadow
evaluation.

## Primary user

The primary user owns or maintains an operational or agentic system but does not
need to know in advance which decision surface exists. They may also have no
resolved decision history. They should not need to author Forge bundle YAML or
coordinate compiler scripts.

The installed skill is the primary interface. The CLI is its deterministic
engine and remains available for inspection, automation, and testing.
The user supplies repositories, URLs, or paths; the skill, not the user, owns
evidence-manifest and reconstruction-proposal authoring.

## Canonical lifecycle

The machine-readable source of truth is `product_lifecycle.yaml`. It declares
the real persisted stage names, allowed `next_action` values, legal transitions,
required inputs, and non-authorizing guidance used by the public CLI. Tests must
fail if a compiler emits a stage or next action absent from that contract.

Every input follows the same lifecycle, even when some stages must wait for more
evidence:

```text
environment
  -> inventory and provenance
  -> work-system reconstruction
       actors | artifacts | states | activities | decisions | outcomes | gaps
  -> integration package
       observation | legal actions | binding stubs | controller | loop | telemetry
  -> binding verification
       typed proposal | controlled-host success + negative traces | shadow-only binding
  -> shadow controller
       observable state | deterministic legality | JEV choice | finite fail-closed loop
  -> decision opportunity map
       code rule | bounded semantic decision | open generation | unknown
  -> select one bounded hypothesis
  -> reviewable observation contract
  -> observed decision events and paths
  -> domain review and trusted labels
  -> JEV shadow measurement
  -> release gate for a separately verified runtime
```

The lifecycle has two legitimate entry paths:

1. **Cold start:** source material exists but resolved cases do not. The maximum
   first output is a source-backed hypothesis plus an observation contract.
2. **Observed history:** resolved cases and later outcomes exist. The Forge may
   rank a bounded surface and compile a non-executing shadow candidate.

Neither entry path grants production authority.

## Required product outputs

For every accepted input, the Forge must produce or explicitly decline each of
these outputs:

1. a hashed source inventory with provenance and authority grade;
2. a Work System Map separating declared, observed, inferred, unknown, and
   conflicting actors, artifacts, states, activities, decisions, and outcomes;
3. a Decision Opportunity Map separating exact rules, bounded semantic choices,
   open generation, and insufficient evidence;
4. exact evidence locators for every candidate;
5. a single declared project stage and next safe action;
6. a non-executing Integration Package naming every missing adapter, legality,
   binding, controller, loop, verification, and telemetry obligation;
7. a typed binding proposal and privacy-minimized runtime-evidence contract when
   integration implementation is authorized;
8. a finite non-executing controller that exposes only legal action IDs to a
   provider-neutral JEV callback;
9. an observation schema for a selected bounded hypothesis;
10. a privacy-safe Decision System Map when events are available;
11. a frozen shadow evaluation when reviewed labels are available;
12. an explicit refusal when evidence cannot support the next transition.

The product must never silently omit an unsupported input. It records coverage,
limitations, and the evidence needed to continue.

## Decision migration contract

The Forge does not try to replace every LLM call. It assigns each opportunity to
one of four destinations:

| Decision shape | Destination |
| --- | --- |
| Exact calculation, threshold, permission, or invariant | Deterministic code |
| Stable finite answers requiring semantic interpretation | Observe, then evaluate with JEV |
| Open-ended writing, planning, synthesis, or tool composition | System 2 |
| Missing vocabulary, state, provenance, or outcomes | Collect evidence |

A JEV candidate must have a bounded vocabulary, observable state available at
decision time, a safe fallback, and reviewable outcomes. JEV supplies a typed
judgment and confidence. Deterministic code owns eligibility, permissions,
threshold policy, state transition, effects, and audit logging.

## Evidence and authority invariants

These invariants are permanent product constraints:

- a source scan creates hypotheses, never executable bindings;
- a System 2 proposal must cite exact inventoried source lines;
- observed behavior does not prove correctness, legality, or current policy;
- raw state, private reasoning, event IDs, and case IDs are not copied into
  aggregate Forge maps;
- JEV is not called before a bounded vocabulary and reviewed evaluation path
  exist;
- shadow mode executes no discovered action;
- a semantic judgment never authorizes an action;
- an Integration Package never invents a safe fallback, activation mapping, or
  executable binding from reconstruction evidence;
- production release requires independently verified bindings, policy,
  calibration, and named human approval.

## Acceptance corpus

Product confidence comes from a versioned corpus, not from one successful demo.
The initial corpus contains six materially different archetypes:

1. Python source with a bounded choice, exact rule, and open generation;
2. a TypeScript agent plus DOT workflow transitions;
3. documents only, with no historical cases;
4. mixed code, documents, and schemas;
5. an expensive generative LLM call that appears to implement a bounded router;
6. resolved cases that can reach reviewed shadow preparation.

Each scenario declares expected stages, opportunity types, vocabularies, and
safety properties in `tests/fixtures/product-contract/scenarios.yaml`. The
acceptance evaluator runs the real product functions and fails on missing
artifacts, unsafe promotion, or stage drift.

The corpus expands whenever a real repository, workflow, or document set exposes
a false positive, false negative, unsupported source form, privacy failure, or
ambiguous product state. Fixes require a pinned regression scenario.

The first frozen external corpus is `tests/fixtures/real-world-benchmark/` and
is evaluated by `scripts/real_world_benchmark.py`. Its initial V1 thresholds are
75% overall precision, 75% overall recall, and 50% recall per archetype. A
benchmark `HOLD` is expected while known source-form gaps remain; benchmark
health is not product readiness.

V1.1 adds negative controls and requires specificity to be defined before the
benchmark can report product readiness. Bounded-choice recovery requires
explicit choice language and two to twelve answer labels; incidental lists and
ordinary open generation remain unclassified or open generation.

V1.1 retains eight synthetic minimal excerpts as scanner regressions and adds
four mechanically extracted excerpts from pinned raw GitHub commits. Synthetic
and external metrics are reported separately; product readiness requires at
least four external cases, including at least two bounded positives and two
negative controls.

V1.2 raises that external bar to ten cases, four positives, four negatives,
three repositories, and two declared SDK/provider ecosystems. Until that corpus
exists, the benchmark remains HOLD even when current measured classifications
are perfect.

The V1.2 corpus includes deterministic notebook-cell extraction for pinned
OpenAI Cookbook `.ipynb` cells: raw notebook hash, stable cell id, joined source,
and extracted-source hash are all verified before an external case is counted.

The public work-system corpus is `tests/fixtures/work-system-benchmark/` and is
evaluated by `scripts/work_system_benchmark.py`. Its first two systems combine
commit-pinned policy documents, deterministic or agentic workflow definitions,
interface templates, and captured public issue/PR histories from Pydantic AI and
OpenAI Agents SDK. A System 2 proposal may connect those fragments only through
quotes that exist in the hashed source. The compiler preserves whether each
claim is declared or observed, requires explicit evidence gaps, and emits no
shadow eligibility or execution authority.

`scripts/direct_acquisition_benchmark.py` runs those two public systems through
the direct repository input boundary twice. It requires deterministic source
identities, commit-pinned origins, heterogeneous source forms, and byte-for-byte
agreement with the frozen public evidence hashes. This is the acceptance gate
for the no-user-YAML acquisition experience; it does not substitute for semantic
or runtime validation.

The observed-runtime corpus is
`tests/fixtures/observed-runtime-benchmark/` and is evaluated by
`scripts/observed_runtime_benchmark.py`. It pins two public repositories and
their licenses, records passing offline execution commands, freezes the capture
adapter and event-log hashes, and runs both logs through the same observation
compiler used by `forge.py observe`. The first corpus covers OpenAI Agents SDK
handoffs and Vajra plan-review routing. It proves that real agentic decisions can
be normalized and compiled, while preserving an important negative result:
Vajra's four events do not contain a recurring path across independent cases and
therefore remain descriptive rather than workflow-ready.

This runtime benchmark establishes workflow recurrence, not semantic validity or
predictive validity. The next evidence gate is a frozen trusted-label shadow
comparison for an observed, repeated surface. It must measure whether JEV can
reproduce reviewed decisions without executing their downstream actions.

## Confidence ladder

Confidence is earned separately at each layer:

1. **Source coverage:** the relevant material was inventoried and hashed.
2. **Structural precision:** executable signals are distinguished from comments,
   examples, strings, and unrelated constants.
3. **Semantic validity:** candidate actions and questions are supported by cited
   material and reviewed by a domain owner.
4. **Workflow recurrence:** events show that the branch and its state recur.
5. **Predictive validity:** a frozen holdout measures JEV against trusted labels.
6. **Operational safety:** bindings, preconditions, fallbacks, thresholds, and
   outcomes are verified independently.

Passing one level does not imply the next.

## Release criteria

The discovery-to-shadow product is considered reliable only while all of these
conditions hold:

- every declared acceptance archetype passes its executable scenario;
- direct repository, URL, and path acquisition preserves hashes and origin;
- structural scanners meet their pinned precision and recall gates;
- every opportunity has provenance and a declared maturity;
- no cold-start output contains an executable binding or shadow approval;
- illegal actions and private-reasoning fields are rejected;
- weak or ambiguous evidence produces a refusal with a next action;
- full repository tests pass on supported Python versions;
- every real-world defect becomes a regression fixture before it is fixed.

Supporting another language means adding or reusing a structural adapter and
passing relevant corpus scenarios. It does not change the lifecycle or safety
contract.

## Deliberate boundaries

The target is not a universal static analyzer, autonomous process miner, or
automatic production deployer. It does not promise to discover behavior hidden
behind unavailable services, generated binaries, runtime reflection, or missing
traces. It does promise to show what was inspected, what was inferred, what was
observed, and why the next transition is allowed or refused.

The final product may eventually generate reviewed integration patches and
release reversible actions. Those capabilities must sit after the same evidence,
evaluation, and authority gates; they do not weaken them.
