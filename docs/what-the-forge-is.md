# What the action-vocabulary-forge is, what it produces, and how to use what it produces

Use this text as context for any person or agent who needs to understand or use the Forge.
It answers the most common confusion first: **does the Forge inject the JEV into any system?**
Short answer: **it does not inject; it fabricates the piece that fits.** The Forge produces a
package (bundle + generated adapter) that lets the JEV decide safely inside a system. The final
fit is one call in the system, made once, at a single decision point. Everything else is generated.

---

## 1. Definition in three sentences

The action-vocabulary-forge is a decision compiler. It reads a system (code, API, UI, SOPs,
traces), finds where that system reduces complex context to a choice among a few actions, and
compiles those choices into an **Action Bundle**: states, actions, transitions, preconditions,
decision surfaces, and evidence for every claim. From the bundle it generates an **adapter** that
calls the JEV (TypeSafe System One) for the semantic part of the choice and keeps everything that
is a rule in deterministic code: what is legal now, thresholds, abstention, confirmation,
execution, and logging.

## 2. What the Forge is NOT

- Not an autonomous agent. The JEV never plans, never calls tools, never widens the option set.
  It answers one closed question at a time.
- Not a framework that wraps the system. The system stays the system; the Forge delivers a Python
  module the system calls at a decision point.
- Not an inventor of how to execute an action. A handler is generated only when discovery
  recorded, with observed evidence, how the action is invoked (function, HTTP request, CLI
  command, MCP tool, UI operation).
- Not a replacement for the human on decisions of authority: credentials, approving the release
  gate, reviewing labels, resolving abstentions.

## 3. The two LLMs and their roles (do not confuse them)

| Who | When | Role |
|---|---|---|
| Frontier agent (Claude Code, Codex, Gemini…) following `SKILL.md` | discovery, once per system, outside the production path | reads the system, writes the bundle, records evidence and bindings, writes the criteria, labels cases for review |
| JEV (`jev-latest`, TypeSafe) | runtime, every decision | chooses one option among the legal ones, with confidence and probabilities; nothing else |
| Code (Forge + generated adapter) | compilation and runtime | validates the bundle, generates the adapter, filters illegal actions, applies policy, executes, logs, calibrates, evaluates |
| Human | before and after | approves the gate, reviews labels, resolves abstentions |

Open intelligence works outside the production path and leaves reviewable artifacts.
Only closed intelligence enters the production path, surrounded by code.

## 4. Input

A real system and a scope. Any combination of: a code repository, an API or MCP spec,
screens/DOM, SOPs and documentation, logs and traces of human decisions, test cases.
The discovery agent receives the path and a scope ("the question routing in
`decision_contracts.resolve`") and produces the `action-bundle/` folder.

## 5. Outputs: what each file is and what it is for

```
action-bundle/
├── surface_candidates.yaml   inventory of the decision points found (promoted or not)
├── action_registry.yaml      the vocabulary: one entry per action, with contract and binding
├── state_registry.yaml       observable states, each with a predicate code can evaluate
├── transition_graph.yaml     from which state, which action, to which state, with guards
├── decision_surfaces.yaml    where the JEV is called: activation state, candidates, fallback
├── evidence_ledger.jsonl     proof for every claim, with a grade (verified_runtime, documented…)
├── jev_adapter_spec.yaml     the questions to the JEV (Choice/Noul/Score) + the policy (thresholds)
├── coverage_report.md        what was inspected, what was left out, blind spots
├── generated_adapter.py      (generated) the module the system calls
├── decision_log.jsonl        (generated in use) one record per decision
└── labels.jsonl              (human/agent) ground truth per case, for calibration and eval
```

### 5.1 `action_registry.yaml` — one action

```yaml
- action_id: route_hedge_coverage_corn
  description: Route the question to contract us-crop-hedge-coverage for corn.
  choose_when: The user asks whether to change, hold, add to, or reduce hedge coverage for US corn.
  do_not_choose_when: Another crop, both crops, a price/basis/P&L question, or no crop at all.
  preconditions:                 # evaluated by code before executing; never by the JEV
    - registry_issue_count == 0
    - contract_status == 'active'
    - historical_market_year == false
  parameters: [{name: contract, required: true}, {name: crop, required: true}, {name: question, required: true}]
  risk: low
  reversible: true
  requires_confirmation: false
  allowed_from_states: [registry_ok]
  destination_states: [contract_routed]
  evidence_refs: [code_resolve, code_render, contract_hedge, test_resolver]
  binding:                       # HOW the action is invoked; becomes a handler only with observed evidence
    kind: python_callable        # python_callable | http | cli | mcp | ui
    locator: app.decision_contracts:_render
    arg_mapping: {contract: contract, crop: crops.corn, question: question}
    evidence_refs: [binding_render_observed]
```

Fields that matter to a user: `preconditions` (what code re-checks), `risk` / `reversible` /
`requires_confirmation` (gates), `binding` (what the adapter will execute).

### 5.2 `decision_surfaces.yaml` — where the JEV comes in

```yaml
- surface_id: route_decision_question
  activation: {state_id: registry_ok}          # the JEV is consulted only in this state
  candidate_actions:
    - {action_id: route_hedge_coverage_corn,     criterion: "…hedge coverage for US corn, and only corn."}
    - {action_id: route_hedge_coverage_soybeans, criterion: "…for US soybeans, and only soybeans."}
    - {action_id: decline_unsupported,           criterion: "Not a hedge-coverage decision for exactly one of corn or soybeans…"}
  fallback_action: decline_unsupported
  abstention_choice: decline_unsupported
  production: true
```

A surface is local: one state, few options, one safe fallback. The `criterion` is the only text
the JEV reads for each option.

### 5.3 `jev_adapter_spec.yaml` — the question and the policy

```yaml
model: jev-latest
endpoint: https://api.typesafe.ai/v1/systemone
policy:
  default_when_uncalibrated: abstain     # without a calibrated threshold, the action abstains
  min_accuracy: 0.97
  questions:
    route_decision_question:
      min_confidence: 0.8                # came from history, not opinion
      actions: {route_hedge_coverage_soybeans: 0.9}
classifier_questions:
  - question_id: route_decision_question
    surface_id: route_decision_question
    type: choice                         # choice | noul | score
    criteria_source: static              # static | dynamic (choices only known at runtime)
    instruction: "This is the user's decision question… choose decline_unsupported when unsure."
    choices:
      - {id: corn,        criterion: "…", executor_action_id: route_hedge_coverage_corn}
      - {id: soybeans,    criterion: "…", executor_action_id: route_hedge_coverage_soybeans}
      - {id: unsupported, criterion: "…", executor_action_id: decline_unsupported}
    abstention_choice: unsupported
```

### 5.4 `evidence_ledger.jsonl` — the proof

```json
{"evidence_id":"binding_render_observed","source_type":"test","locator":"tests/test_decision_contracts.py:12-44",
 "claim":"_render was observed running (via resolve) producing the three selectors…","grade":"verified_runtime"}
```

Grades: `verified_runtime`, `verified_schema`, `documented`, `observed_trace` (accepted in
production); `inferred`, `hypothetical` (never in production). A real handler requires
`verified_runtime` or `observed_trace` on the binding.

### 5.5 `generated_adapter.py` — the module the system calls

Public API (all generated from the bundle; nothing is hand-written):

```python
legal_actions(state) -> [action_id]         # allowed_from_states + preconditions
infer_state(state) -> state_id | None       # from observable_predicate
check_preconditions(action_id, state) -> [predicates that failed]

build_payload(context, question_id=, dynamic_choices=)   # the question to the JEV
parse_response(response, question_id)      # IllegalChoice if the answer leaves the set
apply_policy(decision)                      # threshold per action/question/global; abstains if uncalibrated
threshold_for(question_id, action_id)

execute(decision, state, handlers=, guard=, confirmed=, log=, case_id=)
    # gate order: action exists → state allowed → requires_confirmation → preconditions
    # → host guard → abstention lands on a declared fallback → handler exists → execute → log
classify(context, ...)                      # JEV + policy
decide(context, state, ...)                 # classify + log, no execution (shadow mode)
run(context, state, ...) -> (Decision, result)   # decide + execute, one log record

HANDLERS        # {action_id: callable(state)} generated from bindings; override to replace a stub
HANDLER_STATUS  # {action_id: "generated:python_callable" | "stub:no_binding" | "stub:unobserved_binding" | …}
DecisionLog(path)
set_ui_page(page)        # reuse the host's Playwright page for ui handlers
set_mcp_caller(fn)       # route mcp handlers through the host's MCP session
```

### 5.6 `decision_log.jsonl` — one record per decision

`case_id, state_id, selected_choice, proposed_action_id` (what the JEV proposed), `action_id`
(what code executed), `confidence, threshold, abstained, reason, legal_actions, executed,
outcome, handler_status, latency_ms, usage, ground_truth_action_id`. It is the input to
calibration and evaluation.

## 6. How to use the outputs, by role

### 6.1 Developer of the system (the fit, done once)

1. Build the **observable state** the bundle's predicates read. It is a dict with the fields the
   preconditions mention (`registry_issue_count`, `contract_status`…) plus the objects the
   bindings need (`contract`, `crops`). This is a small, deterministic shim (about 30 lines in
   the first real integration).
2. At the system's decision point, replace the current logic with one call:
   ```python
   decision, result = adapter.run(context, state, case_id=..., log=DecisionLog(path))
   ```
   If `len(adapter.legal_actions(state)) == 1`, execute directly without calling the JEV.
3. Keep the old logic as **fallback and oracle**: any adapter error falls back to it, and its
   result goes into the log next to the JEV decision (`oracle_action_id`).
4. Enable behind a flag. Flag off = the old behavior, byte for byte.
5. If a handler is `stub:*`, either add the missing evidence to the ledger and regenerate, or
   override `HANDLERS[action_id] = your_function`.

Typical time: 1 to 3 hours for the shim, the call swap, and tests with a fake transport.

### 6.2 Operator / owner of the system (the lifecycle)

```
1. run in shadow:        adapter.decide(...) logs without executing
2. label the cases:      labels.jsonl  (case_id, question_id, ground_truth_action_id)
3. calibrate:            calibrate_thresholds.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl --write
4. regenerate:           generate_adapter.py ./action-bundle --output ./action-bundle/generated_adapter.py
5. evaluate:             evaluate_decisions.py ./action-bundle --log decision_log.jsonl --labels labels.jsonl
                         → metrics + RELEASE GATE: APPROVE | HOLD
6. answer the gate.      only the human releases; the script never enables anything
7. in production:        resolve abstentions; they become labels; go back to step 3
```

The gate holds when: there is an illegal action on the held-out set; an abstention path is
missing; an exercised action has no threshold; boundary or abstention accuracy is below the
minimum; the sample is small; the bundle is invalid.

### 6.3 Agent creating a bundle for another system

Invoke the `action-vocabulary-forge` skill with the path and the scope. Rules it enforces:
- every item needs evidence with a locator; `inferred` never reaches production;
- every executable action needs a `binding` with **observed** evidence;
- what is a rule becomes a predicate (precondition), never a JEV criterion;
- every surface has a safe fallback (reversible, not critical);
- secrets never enter the bundle; use `auth_env`.
Validate with `validate_action_bundle.py` before delivering; it refuses what it cannot prove.

## 7. A complete example (first real system, 2026-09-21)

- System: an internal ag-commodity tool, decision point `decision_contracts.resolve` (natural
  language question → which decision contract and crop, or refuse). Previous implementation:
  keyword matching.
- Bundle: 5 actions, 5 states, 1 surface (`corn | soybeans | unsupported`), 18 evidence entries.
  Deterministic gates (registry health, expired contract, historical marketing year) became
  preconditions.
- Generated adapter: 5 real handlers (`_render`, `_limitation`, `audit`), 0 stubs.
- Fit: one router module (150 lines: state shim + fallback + oracle) and one changed line at the
  call site. Enabled behind an environment flag.
- Result on 55 labeled questions: keywords 33/55; JEV 53/54 (the one miss at confidence 0.37,
  abstained by policy). Held-out: 0 illegal actions, boundary 11/11, abstention 4/4.
  `RELEASE GATE: APPROVE`, approved by the owner.

## 8. FAQ

**Does the Forge inject the JEV into any system?**
Not by itself. It produces, for any system, the bundle and the adapter; the system has to call
the adapter at one point and supply the observable state. That fit is small, deterministic, and
the same for every system, but it is a change in the system. "Any system" holds for what has:
finite, nameable outputs, a semantic choice, state observable by code, observable execution.
Websites and MCP tools fit the model: `ui` bindings become Playwright handlers (open URL, click,
fill, read a selector) and `mcp` bindings become `tools/call` through the MCP SDK (stdio or
HTTP), provided the invocation was observed during discovery.

**Can the JEV execute something wrong?**
It can choose wrong; it cannot execute wrong. The choice passes through: the legal set, a
calibrated threshold, re-checked preconditions, confirmation for irreversible actions, a handler
that only exists with evidence. A JEV error becomes an abstention or a fallback, and stays in
the log.

**What about options that only exist at runtime (search candidates, buttons on a page)?**
`criteria_source: dynamic`: the bundle fixes the action type and the abstention; the host passes
the concrete options in `dynamic_choices`; any dynamic choice maps to
`dynamic_executor_action_id`.

**Where do thresholds come from?**
From the labeled log: the lowest confidence bin whose cumulative accuracy stays ≥ 0.97, per
question and per action, with a minimum sample. Without history, everything abstains.

**What is left for the human?**
Credentials, approving the gate, reviewing labels, resolving abstentions. No stubs, thresholds,
or eval scripts.

---

One-line summary: the Forge does not put the JEV inside the system; it produces a generated,
verified, calibrated adapter the system calls at a single point, and from there the JEV decides
only what is semantic, code decides everything else, and the human decides whether to release.
