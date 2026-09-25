# Shadow Controller

Shadow Controller V1 turns a verified binding project into a finite,
non-executing semantic control loop. It is the first product stage where the
complete runtime order is exercised:

```text
observable state
  -> deterministic state adapter
  -> deterministic legal-action filter
  -> JEV-compatible decider sees only legal action IDs
  -> confidence/fallback policy
  -> privacy-minimized record
  -> next external observation or fail-closed stop
```

No step invokes an action binding. `verified_for_shadow` says a binding has
controlled success and safe-negative evidence; it does not give the controller
permission to call it.

## Prepare the controller

After `verify-bindings`, an implementation agent prepares a
`controller_proposal.yaml` using exact quotes from the acquired evidence:

```bash
python3 scripts/forge.py prepare-controller ./forge-project \
  --proposal ./controller_proposal.yaml
```

The proposal defines:

- mutually exclusive observable states using the Forge's closed predicate
  grammar;
- at least one non-terminal state and one terminal state;
- the existing decision surface to activate;
- every candidate action's allowed states and deterministic preconditions;
- one unconditional fallback/abstention action legal in every active state;
- a confidence threshold greater than zero and at most one;
- a finite `max_iterations` from 1 to 100;
- the complete fail-closed stop-condition set;
- a stable question ID; semantic changes require a new versioned ID;
- exact source quote and source SHA-256 for every state and action rule.

The compiler rejects unknown actions and surfaces, unsupported predicate syntax,
partial action coverage, ambiguous authority fields, missing fallback behavior,
unbounded loops, altered sources, and attempts to enable execution.

## Built-in TypeSafe JEV connection

Export the key in the shell environment, prepare an observation-only JSONL file,
and run the reviewed controller:

```bash
export TYPESAFE_API_KEY="..."
python3 scripts/forge.py run-controller-shadow ./forge-project \
  --observations ./observable-states.jsonl
```

Every non-empty JSONL line is one observable-state object. Before the API call,
deterministic predicates identify the state and filter the legal action set. The
request contains one Choice whose criteria are exactly those legal action IDs.
If only one action remains, it is selected deterministically without calling
JEV. Unknown, ambiguous, repeated, invalid, or secret-bearing observations stop
closed.

The key is read only from `TYPESAFE_API_KEY` and used only in the HTTPS
Authorization header. It is absent from errors, return values, and artifacts.
Artifacts under `integration/shadow_runs/` contain state fingerprints and
bounded decision metadata plus a manifest that pins the controller, input, and
record hashes. Raw observations are not copied. Every run records zero binding
invocations, no action execution, and no production authority.

## Custom or host-supplied decider

The generated plan is loaded through the packaged runtime:

```python
from shadow_controller import load_runtime

controller = load_runtime("./forge-project")

def decide(question, state, legal_action_ids):
    # Call JEV here with a Choice whose choices are exactly legal_action_ids.
    return {"answer": "action_id_returned_by_jev", "confidence": 0.91}

result = controller.run(observations, decide)
```

The callback is the host boundary. It receives the reviewed question, current
observable state, and only the actions whose state and preconditions passed.
It returns a bounded action ID and confidence. An answer outside that list is
rejected before any side effect.

The TypeSafe or Laya transport can still be placed behind this callback. The
controller contract does not depend on one provider; `run-controller-shadow`
is the supported TypeSafe convenience path.

## Confidence and stopping

If confidence is below the reviewed threshold, the controller records the
proposed action, selects the fallback, marks the record as abstained, and stops
with `low_confidence`. Selecting the fallback directly stops with
`human_review`.

This threshold is marked `provisional_for_shadow`. It controls measurement and
review routing only. It is not a production threshold; calibration must come
from held-out trusted labels for the exact question version, state builder, and
model.

The loop also stops on:

- terminal, unknown, or ambiguous state;
- no legal action;
- adapter or decider error;
- illegal answer;
- an unchanged observation, so the same question is never re-asked on the same state;
- maximum iterations;
- exhaustion of the external observation stream.

Records contain a SHA-256 fingerprint of the observation, state ID, offered
action IDs, proposed and selected action, confidence, threshold, policy outcome,
and stop reason. Raw state and private reasoning are not persisted.

## Maximum authority

At `shadow_controller_ready`:

- the state adapter and legal-action policy are reviewed for shadow;
- the bounded controller and finite loop are runnable;
- JEV may be called by the host for non-executing decisions;
- action bindings remain non-executable;
- production authority remains false;
- trusted labels, calibration, release evaluation, and named approval are still
  required before any side effect can be released.
