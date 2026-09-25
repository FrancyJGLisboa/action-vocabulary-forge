# Work-system reconstruction

Use this path when the user supplies repositories, URLs, documents, workflows,
templates, logs, traces, issues, pull requests, or examples that describe parts
of one operational system but do not name a local decision surface.

## Evidence model

Inventory every accessible source with a stable `source_id`, kind, locator,
SHA-256, public URL when applicable, and one source-level status:

- `declared`: policy, documentation, schema, interface, or workflow definition;
- `observed`: captured history, trace, event, resolved case, or runtime output.

System 2 may propose nodes and links using these claim-level statuses:
`declared`, `observed`, `inferred`, `unknown`, or `conflict`. Every supported
claim must contain a short exact quote from an inventoried source. A declared
source cannot prove observed behavior; observed history cannot establish policy.

## Proposal contract

The proposal contains:

- `nodes`: actors, artifacts, activities, states, decisions, and outcomes;
- `links`: directed semantic relationships between known nodes;
- `workflows`: ordered steps, involved actors and artifacts, bounded decisions,
  source forms, observed recurrence count, and explicit evidence gaps.

Decision nodes need two to twelve candidate actions. These are candidates only.
Reconstruction never makes them shadow eligible and never creates bindings.

The normal skill flow begins with direct inputs:

```bash
python3 scripts/forge.py reconstruct \
  --project ./.forge/<system-id> \
  --repo https://github.com/owner/repository \
  --url https://example.com/operating-policy.md \
  --source ./documents \
  --system-id <system-id>
```

The project first enters `evidence_inventoried`. It contains exact bounded
snapshots in `evidence/sources/`, a `source_catalog.yaml`, a generated
`reconstruction_request.md`, and `work_system_proposal.yaml`. Read all snapshots,
complete the proposal as System 2 using exact quotes, and finalize it:

```bash
python3 scripts/forge.py reconstruct \
  --project ./.forge/<system-id> \
  --proposal ./.forge/<system-id>/work_system_proposal.yaml
```

The user must not hand-author either the evidence manifest or the proposal; the
skill performs both phases within one invocation. The advanced
`--evidence-manifest` interface remains available for frozen corpora and custom
pipelines.

After validation the project enters `work_system_mapped` with next action
`review_decision_opportunities`. Select a bounded decision for later observation,
or collect evidence that closes a named gap. Do not describe the result as the
complete true system: it is the system supported by the available evidence.

## Public regression corpus

`tests/fixtures/work-system-benchmark/` combines policy documents, deterministic
and agentic workflows, templates, and public issue/PR histories from two pinned
repositories. Run `python3 scripts/work_system_benchmark.py` to validate source
hashes, cross-source reconstruction, evidence status boundaries, repeated paths,
explicit gaps, and the absence of execution authority.

Run `python3 scripts/direct_acquisition_benchmark.py` to verify that the direct
repository path reproduces the pinned hashes and stable source identities for
both public systems without a user-authored manifest.
