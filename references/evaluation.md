# Decision surface evaluation

Do not judge the Forge by the number of YAML files it emits. Evaluate whether it finds real bounded decisions and preserves their safety boundary.

## Dataset

Sample one real system and freeze:

- source artifacts used for discovery;
- historical contexts and the action chosen by a trusted operator or deterministic system;
- legal actions for the state at decision time;
- cases where no safe action was available.

Keep a held-out set. Do not tune surface criteria on the held-out labels.

## Metrics

- **Action recall:** real actions represented by the registry divided by real actions observed.
- **Action precision:** supported canonical actions divided by all proposed actions.
- **Surface coverage:** real bounded decisions represented by a compiled surface divided by bounded decisions sampled.
- **Boundary accuracy:** correct choice among neighboring actions on held-out contexts.
- **Abstention accuracy:** correct abstentions divided by cases requiring more evidence or human review.
- **Illegal-action rate:** offered choices that violate state, permission, or precondition guards.
- **JEV accuracy:** correct choices when the compiled surface is provided to JEV.
- **Replacement rate:** eligible generative calls removed or reduced after review.
- **Cost and latency:** compare the old path with the JEV path, including retrieval, validation, and human escalations.

Report counts as well as percentages. A high replacement rate is not a win if boundary accuracy or abstention accuracy falls.

## Minimum release gate

Use a system-specific threshold, but do not release a surface with:

- any illegal action on the held-out set;
- no safe abstention path;
- unresolved disagreement between code, documentation, and traces;
- only inferred or hypothetical evidence;
- a claimed generative replacement without a stable finite output set.

Record failures in coverage_report.md and keep the surface out of production until reviewed.

## Decision log

The generated adapter writes one JSONL record per decision or execution attempt (`DecisionLog`, or set `FORGE_DECISION_LOG`). Fields: `timestamp, system_id, question_id, surface_id, case_id, state_id, selected_choice, proposed_action_id` (before policy), `action_id` (after policy), `confidence, probabilities, threshold, abstained, reason, legal_actions, executed, outcome, blocked_reason, result_summary, handler_status, model, latency_ms, usage, ground_truth_action_id, label_source`. Labels can be written inline as `ground_truth_action_id` or supplied separately as JSONL/CSV rows with `case_id, question_id, ground_truth_action_id`.

## Calibration

    python3 scripts/calibrate_thresholds.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl [--write]

Records are split by `case_id` (default 30% held out, seed 7) and only the training split is used. For every question, and for every proposed non-fallback action within it, confidences are binned (`0-0.5, 0.5-0.6, ..., 0.9-1`) and accuracy is `proposed_action_id == ground_truth_action_id`. The suggested threshold is the lowest bin floor whose cumulative accuracy from the top bin down stays at or above `--min-accuracy` (0.97). A group needs `--min-samples` (30) records; otherwise it stays uncalibrated, which the adapter treats as abstain. A suggestion below `--min-threshold` (0.5) is raised to that floor and marked `*` in the table: when every band looks perfect on a small sample the rule would otherwise return the lowest floor and leave the action ungated, which is overfitting rather than a licence to act on a weak answer. `--write` replaces `policy` in `jev_adapter_spec.yaml` and stamps `calibrated_at` and `calibration_source`.

## Running the eval

    python3 scripts/evaluate_decisions.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl [--json eval.json]

Log metrics use the held-out split (`--all` evaluates everything):

| Metric | Numerator / denominator |
|---|---|
| Action recall | distinct ground-truth actions present in the registry / distinct ground-truth actions |
| Action precision (bundle) | actions with production-grade evidence / all actions |
| Surface coverage (bundle) | promoted bounded candidates backed by a production surface with a question / bounded candidates |
| Boundary accuracy | non-abstained records with `action_id == ground_truth` / non-abstained records |
| Abstention accuracy | records whose truth is a fallback and that abstained or landed on a fallback / records whose truth is a fallback |
| Illegal-action rate | records whose `proposed_action_id` was not in `legal_actions`, or blocked by state/precondition / all records |
| JEV accuracy | `proposed_action_id == ground_truth` / all records |
| Replacement rate (bundle) | promoted `generative_call` candidates / `generative_call` candidates |
| Cost and latency | mean and p95 `latency_ms`; summed `usage` tokens |

Records decided by code (`reason` starting with `deterministic:`, no confidence) are excluded from
calibration bins and reported on their own line; any disagreement with labels is a host rule bug
and holds the release. `--by-model` prints the log metrics per `model` value (e.g. `jev-1.13.0`
vs `laya:typed-decisions`) on the same records.

The release gate prints `APPROVE` (exit 0) or `HOLD` (exit 2) with each failing line: any illegal action; a question without an abstention path; an exercised non-fallback action with no threshold at any policy level; boundary or abstention accuracy below `--min-boundary-accuracy` / `--min-abstention-accuracy` (0.9); fewer than `--min-samples` held-out records; a bundle that fails validation. The human answers the printed approval question; the scripts never enable a surface on their own.
