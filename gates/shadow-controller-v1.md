# Gates: Shadow Controller V1

Scope: Compile verified shadow bindings plus reviewed state/legal-action policy
into a finite, non-executing controller that can exercise a JEV-compatible
decider over observable states without granting action authority.

- [x] G1: A `bindings_verified_for_shadow` project accepts a source-cited controller proposal and advances through an explicit CLI command without invoking JEV or any target binding.
  CHECK: python3 -m unittest tests.test_shadow_controller.ShadowControllerProposalTests -v
  EXPECT: /OK/
  EVIDENCE: Three proposal lifecycle tests passed; CLI advanced to `shadow_controller_ready` with compilation JEV calls, binding invocation, action execution, and production authority all false.

- [x] G2: The proposal validates observable state predicates, surface activation, per-action legality, an explicit fallback/abstention action, confidence thresholds, terminal states, and a positive finite iteration limit.
  CHECK: python3 -m unittest tests.test_shadow_controller.ShadowControllerContractTests -v
  EXPECT: /OK/
  EVIDENCE: Five contract tests passed for parsed predicates, terminal/active states, complete action coverage, unconditional fallback, provisional threshold, exact evidence, full stop set, and finite loop limit.

- [x] G3: The generated controller resolves exactly one state, filters legal actions deterministically before calling the decider, rejects illegal answers, applies confidence policy, and never invokes an action binding.
  CHECK: python3 -m unittest tests.test_shadow_controller.ShadowControllerRuntimeTests -v
  EXPECT: /OK/
  EVIDENCE: Five runtime tests passed; action preconditions changed the offered set, illegal answers raised before side effects, low confidence and explicit fallback stopped safely, and binding invocations remained zero.

- [x] G4: The bounded loop stops on terminal state, unknown or ambiguous state, no legal actions, low confidence/human review, adapter or decider error, and max iterations; all records are privacy-minimized and content-hashed.
  CHECK: python3 -m unittest tests.test_shadow_controller.ShadowControllerStopAndPrivacyTests -v
  EXPECT: /OK/
  EVIDENCE: Seven stop/privacy tests passed, including terminal, unknown, ambiguous, no-legal-action, decider error, unchanged state, finite iteration, observation exhaustion, state hashing, and private-reasoning/secret refusal.

- [x] G5: Unknown IDs, unsupported predicates, missing fallback, unsafe thresholds, unverified action-policy evidence, artifact tampering, and attempts to enable execution or production authority fail closed.
  CHECK: python3 -m unittest tests.test_shadow_controller.ShadowControllerAdversarialTests -v
  EXPECT: /OK/
  EVIDENCE: Six adversarial tests plus contract negatives passed for unknown actions, authority fields, secrets, plan and integration drift, unsafe runtime plans, bad predicates, hashes, fallback, thresholds, and loop bounds.

- [x] G6: A controlled public-code-derived benchmark runs the generated shadow controller through multiple states with a JEV-shaped decider and proves zero binding invocations, finite termination, and only legal choices.
  CHECK: python3 scripts/shadow_controller_benchmark.py
  EXPECT: /"status": "PASS"/
  EVIDENCE: PASS against OpenAI Agents commit `74636ac9b9c5b7fe9e2e4ca62daff59248769c02`; 2 legal actions, 0 illegal actions, terminal and low-confidence stops, 0 binding invocations, no authority.

- [x] G7: The skill and product documentation describe how an agent supplies the reviewed controller proposal, how a host injects JEV, and why shadow decisions still cannot execute actions.
  CHECK: python3 -m unittest tests.test_skill_install -v && bash scripts/install.sh --check
  EXPECT: /OK.*Codex CLI.*ready/s
  EVIDENCE: `SKILL.md`, `references/shadow-controller.md`, README, product V1/target, metadata, and changelog describe the proposal, callback, provisional threshold, and non-execution boundary; all CLI installation checks passed.

- [x] G8: The full suite, all existing product benchmarks, syntax checks, whitespace checks, and gate ledger pass without weakening prior boundaries.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: 280 tests passed with 1 skipped; product contract 6/6 and all reconstruction, acquisition, integration, binding, controller, real-world, and observed-runtime benchmarks passed; compileall, skill checks, and `git diff --check` passed.
