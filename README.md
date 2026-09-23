# decision-system-forge

[![Decision System Forge, explained in one page](docs/img/executive-comic.jpg)](docs/executive-comic.pdf)

*One-page explainer ([PDF](docs/executive-comic.pdf), [editable SVG](docs/executive-comic.svg)).
The Forge compiles source material into evidence-backed semantic judgments, legal action
surfaces, and an evaluated runtime. Semantic judgments inform action selection but never
authorize actions; deterministic code owns legality, policy, and execution.*

Point it at source material and a decision outcome. The Forge now compiles a
**Semantic Decision Bundle**: provenance-tracked material, semantic concepts,
reusable Choice/Noul/Score judgments, observable states, legal actions,
decision surfaces, and a generated adapter that lets TypeSafe's JEV decide
inside deterministic guard rails. The original eight-file **Action Bundle**
remains supported for focused decision-point audits.

If the decision point is not known yet, give Forge resolved cases plus the
repository or operating material. The offline discovery compiler profiles the
case history, ranks bounded surfaces by evidence readiness and optional workload
value, measures a disjoint local baseline, and scaffolds the safest candidate.
It never promotes historical behavior into production authority.

```text
material
  -> discovery writes a manifest, semantic IR, judgment registry and evidence
  -> legal actions and local decision surfaces are compiled from that structure
  -> validate_semantic_bundle.py refuses unsupported or unsafe links
  -> semantic_index.py makes the vocabulary searchable
  -> semantic_runtime.py evaluates active judgments, then exposes only legal actions
  -> generate_adapter.py renders the adapter: JEV call, policy, gates, handlers, log
  -> the adapter runs; every decision lands in decision_log.jsonl
  -> calibrate_thresholds.py turns labeled history into per-action thresholds
  -> evaluate_decisions.py prints the metrics and RELEASE GATE: APPROVE | HOLD
  -> a human grants credentials, answers the gate, resolves abstentions
```

JEV answers bounded questions. Supporting judgments interpret semantic properties;
the final action Choice sees only actions already legal in the observable state.
Code decides legality, applies thresholds and abstention, re-checks preconditions,
and executes through a handler generated from an observed binding, never a guess.

## Semantic compiler

Start from company material and resolved cases:

```bash
python3 scripts/discover_decision_system.py \
  --source examples/discovery-input/sop.md \
  --cases examples/discovery-input/cases.jsonl \
  --surface-field decision_type \
  --system-id shipment_triage \
  --review-minutes 2 --hourly-cost 45 --monthly-volume 3000 \
  --output /tmp/forge-discovery
```

This writes a source manifest, ranked candidates, privacy-safe train/holdout
references, a measured offline baseline, and a validator-clean but non-production
`candidate_bundle/`. See [decision discovery](references/decision-discovery.md).

After reviewing that vocabulary, measure the actual candidate Choice without
executing anything:

```bash
TYPESAFE_API_KEY=... python3 scripts/evaluate_discovered_system.py /tmp/forge-discovery
```

The evaluator reloads the unchanged original cases, uses only the frozen
holdout, rejects illegal answers, and writes a privacy-safe `jev_gate.py` log.

Continue with a reviewed bundle:

```bash
python3 scripts/init_semantic_bundle.py ./semantic-bundle --example
python3 scripts/validate_semantic_bundle.py ./semantic-bundle
python3 scripts/semantic_index.py build ./semantic-bundle ./semantic-bundle/semantic.sqlite
python3 scripts/semantic_runtime.py ./semantic-bundle --state-file state.json --context-file context.json
python3 scripts/generate_adapter.py ./semantic-bundle --output ./semantic-bundle/generated_adapter.py
```

The runtime is two-stage: active reviewed judgments are evaluated first; their
typed answers enrich a final Choice compiled from the currently legal actions.
One legal action is selected deterministically. No legal fallback stops the run.
`JudgmentLog` produces `jev_gate.py`-compatible records so supporting judgments,
not only final actions, can be calibrated against trusted labels.

## The mental model

Two analogies get you most of the way, and it is worth knowing what each one leaves out.

**A scoped action graph for a classifier.** A state machine where each state carries the small
set of actions legal from there, and the model is only ever asked to pick within that set. The
"scoped" part is load-bearing: a decision surface is local to one state, never a global menu.

**An action space with verifiable preconditions**, if you come from reinforcement learning — but
the policy is not learned. It is calibrated from your own labelled decisions, and abstention is a
first-class outcome rather than one action among many.

What both metaphors miss is what makes it usable: every node and edge carries its **provenance**
(where it was observed, at what evidence grade, refused by the validator if only inferred); every
action carries a **binding**, so the graph knows how to execute itself, and only when the
invocation was actually observed; and a **policy layer** sits over the graph with calibrated
thresholds, a mandatory abstention path, and a release gate a human answers.

A scoped action graph, plus provenance, plus executability, plus a gate.

## Status

The semantic compiler is new and covered by a validated example plus offline
two-stage runtime tests. The action-runtime core has one real system wired end to
end (a question-routing surface in an internal
ag-commodity tool: 5 generated handlers, JEV 53/54 vs 33/54 for the keyword
resolver it replaced, release gate approved). A second system (an internal-control test) ran 110 labelled decisions through
both a hosted and a fully local classifier: 97.3% and 93.6% raw agreement,
**zero wrong conclusions after thresholds on either**, with the local one
concluding 62 decisions where the hosted one concluded 105.

`python_callable`, `http` and `cli` handlers have run for real; `ui` (Playwright)
and `mcp` (MCP SDK) renderers are unit-tested at the SDK boundary but not yet
exercised against a live browser or server. Runs against TypeSafe JEV or a
self-hosted Laya checkpoint (`provider: laya`); the bundle, thresholds and gate do
not change. Expect the bundle schema to move; the validator is the contract.

## Install as a skill

```bash
git clone https://github.com/FrancyJGLisboa/decision-system-forge ~/projects/decision-system-forge
~/projects/decision-system-forge/scripts/install.sh      # symlinks into Claude, Codex, Copilot, Gemini
```

Requires Python 3.11+ and PyYAML. `ui` handlers need Playwright and `mcp` handlers the `mcp` package, only on the host that runs them. `TYPESAFE_API_KEY` is read from the
environment at call time and never written into a bundle.

Long-form explainer for people and agents: [docs/what-the-forge-is.md](docs/what-the-forge-is.md).

## Layout

```text
SKILL.md                     material -> judgments -> legal actions -> evaluated runtime
references/semantic-decision-schema.md   semantic manifest, IR, judgments, links and runtime plan
references/decision-discovery.md   resolved-case contract, ranking, baseline and promotion path
references/bundle-schema.md  every bundle field, including binding, policy, criteria_source, predicates
references/adapter-generation.md   what the generated module contains and how a host uses it
references/evaluation.md     metrics, calibration rule, release gate
references/source-playbook.md   mixed-source discovery guidance
scripts/init_action_bundle.py       scaffold an empty or example bundle
scripts/init_semantic_bundle.py     semantic front end plus the compatible Action Bundle
scripts/discover_decision_system.py material + resolved cases -> ranked candidates + safe bundle
scripts/evaluate_discovered_system.py candidate Choice -> measured shadow holdout, no execution
scripts/scan_llm_opportunities.py   heuristic scan for classifier-replaceable generative calls
scripts/validate_action_bundle.py   structural and safety checks
scripts/validate_semantic_bundle.py semantic provenance, judgment and cross-link checks
scripts/semantic_index.py           SQLite FTS index over the compiled vocabulary
scripts/semantic_runtime.py         active judgments -> filtered legal Choice -> adapter execution
scripts/compile_jev_surface.py      one surface, one state -> compiled choice set
scripts/generate_adapter.py         bundle -> Python adapter (embeds scripts/predicates.py)
scripts/calibrate_thresholds.py     decision log + labels -> policy thresholds
scripts/evaluate_decisions.py       held-out metrics + release gate
scripts/run_jev_choice.py           minimal manual JEV call for a compiled surface
scripts/jev_gate.py                 calibrate + release gate for any JEV decision log, no bundle
scripts/decision_history.py         shared core: labels, split, calibration, metrics, gate checks
examples/validation-bundle/         the reference bundle used by the tests
examples/semantic-validation-bundle/ full material-to-runtime reference bundle
examples/discovery-input/           representative SOP and resolved-case history
tests/                              unit tests per feature plus test_end_to_end.py
```

## Calibration and gate without a bundle

Using JEV (or Laya) for routing, scoring or classification with no action to execute? You
still need thresholds from data and a held-out check on human labels. `jev_gate.py` does only
that: one JSONL record per decision (`case_id, question_id, answer, confidence, truth`, or the
raw JEV answer as `jev`), no bundle, no PyYAML.

```bash
python3 scripts/jev_gate.py calibrate decisions.jsonl --abstain human --out thresholds.json
python3 scripts/jev_gate.py evaluate  decisions.jsonl --abstain human --thresholds thresholds.json
```

It shares its core (`decision_history.py`) with the bundle scripts, so the calibration rule,
the 0.5 floor and the human-labels-only gate are the same code.

## Try it

```bash
cd ~/projects/decision-system-forge
python3 -m unittest discover -s tests -v
python3 scripts/validate_action_bundle.py examples/validation-bundle
python3 scripts/generate_adapter.py examples/validation-bundle --output /tmp/forge/adapter.py
python3 -c "import sys; sys.path.insert(0,'tests'); from test_end_to_end import write_fixture; write_fixture('/tmp/forge')"
python3 scripts/calibrate_thresholds.py /tmp/forge/bundle --log /tmp/forge/decision_log.jsonl --labels /tmp/forge/labels.jsonl --write
python3 scripts/evaluate_decisions.py /tmp/forge/bundle --log /tmp/forge/decision_log.jsonl --labels /tmp/forge/labels.jsonl
```

## Editing

`examples/validation-bundle/` and `examples/semantic-validation-bundle/` are
generated from their matching `init_*_bundle.py --example` scripts. Edit the
initializer and regenerate with `--force`. The pre-commit hook runs the tests,
validates both examples, and refuses drift. The references are hand-written.
# Optional JEV opportunity triage

Set `TYPESAFE_API_KEY` and opt in explicitly:

```sh
TYPESAFE_API_KEY=... python3 scripts/scan_llm_opportunities.py src/ --jev-triage --output opportunities.yml
```

Only this opt-in sends source windows to TypeSafe; windows are not persisted in the YAML output. Every triaged result remains a review-only `candidate`.

The scanner filters before any JEV request: Python candidates must be AST-confirmed
calls, other supported code files are scanned with comments and strings masked, and
documentation/data files are ignored. Raw HTTP calls qualify only when the same file
contains a recognized AI-provider signal.
