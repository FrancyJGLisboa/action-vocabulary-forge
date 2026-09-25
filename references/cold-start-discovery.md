# Cold-start decision discovery

Use this path when the user has operating material but no resolved cases and
may not know which decisions exist. The host LLM is System 2: it reads the
setting once and proposes bounded decision hypotheses. The Forge is the
compiler and verifier: it accepts only source-cited structure, emits an
observation contract, and grants no runtime authority.

## Product sequence

1. Run `forge.py scan` without a proposal. This inventories supported material,
   hashes every source, and extracts conservative code signals.
2. Read `scan_review.md`, `decision_opportunity_map.yaml`, and
   `evidence/source_inventory.yaml`.
3. Inspect only material relevant to missing human or agentic branch points.
   Look for recurring triage, gates, handoffs, retry/stop choices, tool routing,
   acceptance/revision decisions, or rubric judgments. Do not convert open-ended
   writing, summarization, or planning into a bounded choice merely to use JEV.
4. Write `system2_proposals.yaml` inside the Forge project. Cite exact source IDs
   and line ranges. Every bounded action must appear in the cited text.
5. Re-run `forge.py scan --force --proposal ...` with the same sources.
6. Explain hypotheses and select at most one bounded semantic candidate with
   `forge.py select <project> --opportunity <id>`. This emits
   `instrumentation_spec.yaml` and `event_schema.json`; it does not modify the
   source application.
7. Collect cumulative JSONL observations outside the Forge project and run
   `forge.py observe <project> --events <events.jsonl>`. Review the aggregated
   `decision_system_map.yaml`. Do not compile a bundle or call JEV yet.

The proposing LLM does not score or approve its own candidate. Deterministic
validation checks source support; later observed events, domain review, and an
independent JEV shadow measurement evaluate the candidate.

Generated project directories under `.forge/` are excluded from source
inventory so the Forge never treats its own proposal as operating evidence.

## Proposal contract

```yaml
opportunities:
  - opportunity_id: review_research_state
    type: bounded_semantic_decision
    actor_type: human
    candidate_actions:
      - search_more
      - revise
      - accept
      - escalate
    evidence:
      - source_id: material:0123456789abcdef
        line_start: 12
        line_end: 16
```

Allowed types:

- `bounded_semantic_decision`: meaning determines one of at least two explicit
  actions; recommended runtime is `observe_then_jev`;
- `deterministic_rule`: exact data or policy can decide; recommended runtime is
  ordinary code;
- `open_generation`: the work must produce unbounded content; recommended
  runtime remains an LLM.

The proposal must not contain `binding`, `executable`, `handler`, or
`production` fields. These are deliberately outside cold-start authority.

## Observation contract

For each selected bounded hypothesis, collect only operational state and
outcomes:

- `event_id`, `case_id`, `opportunity_id`, `occurred_at`, and `actor_type`;
- `state_before` and the `available_actions` actually shown;
- `selected_action`, `state_after`, and `source_ref`;
- optionally model confidence, eventual outcome, and outcome time.

Do not collect hidden reasoning or chain-of-thought. Human and agent decisions
share the same event shape; `actor_type` distinguishes them. Software rules may
be recorded for path reconstruction but should remain code, not JEV questions.
The event file remains external. The Forge stores its locator and hash plus
aggregate action paths; it does not copy raw state, event IDs, or case IDs into
the project.

## Promotion boundary

A source-backed hypothesis is not an observed workflow. Promote it only after
events show that the branch recurs, the state available at decision time is
known, the action vocabulary is bounded, and labels or outcomes can be reviewed.
Before compiling a JEV Choice, add and review an explicit safe no-match or
fallback action when the observed vocabulary does not already contain one.
At that point the historical `discover` path may rank and measure the local
surface. Multi-step Decision System Maps require repeated event paths and are a
later compilation stage.
