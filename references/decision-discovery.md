# Decision discovery from resolved cases

`scripts/discover_decision_system.py` implements the offline entry point for a
company builder who has source material and historical decisions but has not yet
defined a JEV surface.

## Input contract

The cases file is JSONL, NDJSON, or CSV. It must contain:

| Field | Default | Meaning |
| --- | --- | --- |
| case ID | `case_id` | Stable and unique decision-event identifier |
| context | `message` | Text or JSON that was available at decision time |
| action | `resolved_action` | Action observed in the resolved case |
| outcome | `outcome` | Optional later result; presence is measured, meaning is not invented |
| surface | none | Optional field separating different decision families |

Use dotted field paths for nested JSON. When no surface field is supplied, all
records form `default_surface`.

Historical actions are observations. They do not establish that an action was
correct, legal, authorized, or still available. The discovery bundle therefore
has no bindings, is non-production, and requires human approval for every
non-fallback action. Per-action contract evidence is graded `inferred`, so merely
changing `production: false` to `true` fails validation.

## Command

```bash
python3 scripts/discover_decision_system.py \
  --source ./src \
  --source ./SOPs \
  --cases ./resolved-cases.jsonl \
  --surface-field decision_type \
  --system-id supplier_operations \
  --scope "Choose the next safe step for a supplier exception." \
  --review-minutes 2 \
  --hourly-cost 45 \
  --monthly-volume 10000 \
  --output ./forge-discovery
```

The command is deterministic and makes no network call. Source files are
inventoried by locator, type, size, and SHA-256. Raw case context and raw case
IDs are processed in memory but not copied into the output.

## Outputs

```text
forge-discovery/
├── discovery_manifest.yaml    source inventory, hashes, input scope
├── decision_candidates.yaml   ranking, evidence gaps, value and baseline metrics
├── evaluation_cases.jsonl     hashed case refs and deterministic train/holdout split
├── discovery_report.md        human-readable result and next gate
├── bundle_validation.json     validator evidence for the candidate bundle
├── candidate_bundle/          highest-ranked eligible Semantic Decision Bundle
├── jev_holdout_log.jsonl      generated after explicit shadow evaluation
└── jev_performance.json       measured JEV/Laya holdout result, never a release by itself
```

No bundle is generated when every surface fails the minimum evidence gate.
Defaults are 20 cases, 3 cases per action, 2–12 actions, 80% context coverage,
and a stratified 20% holdout. These are discovery defaults, not universal release
criteria.

## Ranking

Each surface receives two distinct measurements:

1. `readiness_score` (0–100) combines sample support, bounded action count,
   minimum support per action, context coverage, outcome coverage, and the
   availability of a disjoint holdout baseline.
2. `priority_score` combines readiness with optional workload signals. When
   monthly volume is supplied, monthly review hours contribute 20%; when hourly
   cost is also supplied, monthly labor value contributes 10%. Available weights
   are normalized, so missing economics do not silently count as zero value.

Workload components saturate at 100 review hours/month and 5,000 supplied cost
units/month. These transparent defaults rank discovery work; they do not promise
savings. `priority_components` records every score and weight.

## Baseline

The command trains a small multinomial Naive Bayes classifier on only the
training split and evaluates it on the disjoint holdout. It reports:

- majority-class accuracy;
- local text accuracy;
- coverage and accuracy above the selected confidence threshold;
- illegal predictions (always zero because the classifier is restricted to the
  observed action vocabulary).

This is a signal that text contains repeatable structure. It is explicitly
marked `not_jev_performance: true`. It is not a release gate, production claim,
or substitute for shadow-mode JEV evaluation on trusted labels.

After a domain owner reviews the candidate vocabulary, run the actual candidate
Choice against the frozen holdout without executing any action:

```bash
TYPESAFE_API_KEY=... python3 scripts/evaluate_discovered_system.py ./forge-discovery
# or, with Laya installed:
python3 scripts/evaluate_discovered_system.py ./forge-discovery --provider laya
```

The evaluator verifies that the original case file still matches its discovery
hash, reconstructs the same holdout, sends one bounded Choice per case, rejects
out-of-vocabulary answers, and writes only hashed case references. Its default
`label_source` is `historical`, which is not release-gate eligible. Use
`--label-source human` only when a responsible human actually verified the
labels, then calibrate and evaluate the resulting log with `jev_gate.py`.

## Promotion path

Before changing a discovered bundle from candidate to production:

1. A domain owner reviews actions, labels, vocabulary, and judgment boundaries.
2. Authoritative material establishes deterministic policy and legal
   preconditions.
3. Runtime inspection supplies real bindings and permission checks.
4. Run `evaluate_discovered_system.py`; candidate judgments are evaluated and
   promoted to reviewed or calibrated.
5. The adapter runs in shadow, then passes the held-out release gate.

Do not treat discovery confidence, historical frequency, or the local baseline
as permission to execute.
