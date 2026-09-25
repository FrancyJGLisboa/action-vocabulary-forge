# Binding Verification

Binding Verification closes one specific gap between a reconstructed Work
System and runnable decision software: it determines whether a proposed target
operation has actually demonstrated its declared transition in a controlled
host.

It deliberately does not make the operation executable.

## Trust boundary

Three roles remain separate:

1. An implementation agent or engineer proposes a typed target locator,
   deterministic preconditions, success/failure postconditions, and an exact
   quote from inventoried evidence.
2. A controlled host imports or calls that target and emits privacy-minimized
   observations. The host—not the Forge—owns sandboxing, credentials, and test
   isolation.
3. The Forge validates those observations and may mark the binding
   `verified_for_shadow`. It never imports or invokes the target and never turns
   observed behavior into production authority.

## Lifecycle

After `prepare-integration`:

```bash
python3 scripts/forge.py propose-bindings ./forge-project \
  --proposal ./binding_proposal.yaml

# A controlled host now exercises every candidate and writes JSONL observations.

python3 scripts/forge.py verify-bindings ./forge-project \
  --observations ./binding_observations.jsonl
```

`propose-bindings` accepts `python_callable`, `http`, `cli`, `mcp`, and `ui`
locators. Each kind has a closed locator shape. CLI bindings use an `argv` list,
never a shell string. HTTP and UI navigation require HTTPS. Credential-like
fields and values are rejected.

Every proposal must reference a known action and cite an exact quote plus the
SHA-256 digest of an inventoried source. A proposal is still only a candidate:

```yaml
schema_version: "1.0"
system_id: issue_maintenance
bindings:
  - binding_id: binding_mark_stale
    action_id: action_mark_stale_abc123
    kind: python_callable
    locator: {target: "issue_runtime:mark_stale"}
    preconditions:
      - issue has been inactive for at least seven days
    success_postconditions:
      - the issue contains the stale label
    failure_postconditions:
      - an ineligible issue is rejected without a state change
    evidence:
      source_id: issues_workflow
      source_sha256: "<64 lowercase hexadecimal characters>"
      quote: 'stale-issue-label: "stale"'
```

The generated `binding_observation_schema.json` is the host contract. It does
not accept raw state, model reasoning, prompts, credentials, or invocation
payloads. State, invocation, and harness content are represented by SHA-256
fingerprints. Each candidate must have at least:

- one succeeded case whose observable state fingerprint changes and whose
  declared success postcondition holds;
- one rejected or failed-safe negative case whose declared failure
  postcondition holds and whose observable state fingerprint is unchanged.

The success and negative case must be emitted by the same harness ID and
SHA-256 digest; results from different harness versions cannot be combined to
cross the gate.

Observation IDs must be unique. Every observation carries the candidate digest,
so evidence from an earlier or altered proposal cannot be replayed against a new
candidate. The validated, privacy-minimized JSONL is archived and hashed inside
the project.

## Maximum authority of this stage

A successful verification updates the action binding to:

```yaml
status: verified_for_shadow
evidence_grade: observed_trace
executable: false
production_authority: false
authority: none
```

This proves only that a controlled host observed the specified success and
failure behavior for the exact candidate. The observation adapter, legal-action
policy, fallback, bounded controller loop, JEV calibration, and named release
decision remain separate blocking gates.

The next product transition is `prepare-controller`, which compiles the state,
legal-action, fallback, confidence, and finite-loop contracts without enabling
the verified binding. See [shadow controller](shadow-controller.md).
