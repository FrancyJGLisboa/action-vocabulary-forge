# Source playbook

Use this guide while discovering candidates. It is a search strategy, not permission to infer unsupported behavior.

## Generative-call opportunity

Search prompts and model-call wrappers for verbs such as classify, choose, route, approve, reject, retry, escalate, and select. Look for bullets, enum values, JSON schemas, or explicit “one of” instructions near the call. Record the source locator and labels without copying the prompt or payload. Mark the result as a candidate until examples, guards, and abstention behavior are verified.

## Codebase

Search for routes, commands, public methods, event handlers, enums, status constants, workflow nodes, permission guards, retries, error branches, tests, and audit events. For each candidate, capture the callable name, guard, side effect, destination status, and a source locator. A function name alone is weak evidence; pair it with an implementation or test.

## API, OpenAPI, MCP, and tool schemas

Extract operation IDs, HTTP methods, parameters, scopes, response states, and documented side effects. Treat DELETE, POST, merge, publish, send, and payment operations as mutating until proven otherwise. Schemas prove availability and shape; they do not prove that a call is legal in every state.

## Website or UI

Inspect the accessibility tree and visible controls before relying on DOM internals. Capture labels, roles, enabled/disabled state, navigation destination, and the network operation triggered by a control when available. Distinguish “button is rendered” from “button is enabled and executable.” A screen can expose actions that are illegal until a guard is satisfied.

## SOPs and documentation

Extract explicit verbs, modal language, conditional rules, exceptions, and escalation paths. Preserve the document locator and quote only a short claim in the evidence ledger. Convert IF X THEN Y into a state predicate plus a transition candidate; do not turn examples into universal rules.

## Logs, traces, and human decisions

Prefer structured records with before/after state. Cluster action names only after checking side effects and outcomes. Repeated observations can support observed_trace; one unexplained event should remain unresolved. Redact identifiers and payloads in the bundle.

## Cross-source convergence

When code, UI, API, SOP, and logs describe the same operation, create one canonical action with aliases and multiple evidence references. If they disagree about destination state, retain both claims and create a human_review surface. Do not use similarity alone as proof of equivalence.

## Common JEV surfaces

Typical local choice sets include:

- classify a ticket or document into a finite queue;
- choose among candidate ontology mappings;
- route to deterministic tool, cheap model, frontier model, or human;
- choose retry, quarantine, publish, or review after validation;
- select the next enabled UI action;
- triage a large corpus into a shortlist for expensive processing.

In each case, filter legality first, then let JEV choose semantics, then execute and record the next state.
