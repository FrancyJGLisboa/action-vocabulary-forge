# Binding Verification V1 — Completion Gates

This increment turns untrusted, agent-authored binding proposals into reviewable
shadow candidates only after runtime evidence demonstrates the proposed
pre-state → invocation → post-state transition. The Forge never executes an
arbitrary target and never grants production authority in this increment.

- [x] G1 — A project at `integration_planned` can ingest a binding proposal and advance to a distinct binding-candidate stage without invoking the target environment.
  CHECK: python3 -m unittest tests.test_binding_verification.BindingProposalTests.test_proposal_advances_stage_without_executing_target -v
  EXPECT: /OK/
  EVIDENCE: `forge.py propose-bindings` advanced to `binding_candidates_ready` with executes_actions and production_authority false.
- [x] G2 — Binding proposals have stable IDs, refer to known actions, use kind-specific typed locators, cite exact acquired evidence, declare preconditions/postconditions, and cannot embed credentials or shell command strings.
  CHECK: python3 -m unittest tests.test_binding_verification.BindingProposalTests -v
  EXPECT: /OK/
  EVIDENCE: Six proposal tests passed for stable digests, typed locators, action identity, exact quote/hash evidence, shell refusal, and secret refusal.
- [x] G3 — Externally produced runtime observations are validated, content-hashed, replay-resistant, privacy-minimized, and prove pre-state → invocation → post-state without storing raw secrets or private reasoning.
  CHECK: python3 -m unittest tests.test_binding_verification.BindingObservationTests.test_archived_evidence_contains_fingerprints_not_raw_state tests.test_binding_verification.BindingObservationTests.test_duplicate_observation_is_rejected tests.test_binding_verification.BindingObservationTests.test_candidate_tampering_is_detected -v
  EXPECT: /OK/
  EVIDENCE: Generated schema, candidate digest, unique IDs, archived fingerprint-only JSONL, and artifact hashes passed three focused tests.
- [x] G4 — A binding is promoted only to `verified_for_shadow` after both a successful case and a safe negative/failure case; it remains `executable: false` with `production_authority: false`.
  CHECK: python3 -m unittest tests.test_binding_verification.BindingObservationTests.test_success_and_negative_observations_promote_shadow_only tests.test_binding_verification.BindingObservationTests.test_negative_case_must_leave_state_unchanged tests.test_binding_verification.BindingObservationTests.test_success_and_negative_must_use_same_harness_digest -v
  EXPECT: /OK/
  EVIDENCE: Shadow promotion, same-harness digest, and unchanged-negative-state tests passed; the promoted registry retained zero authority.
- [x] G5 — Missing evidence, unknown IDs, altered hashes, duplicate observations, malformed transitions, unsafe locators, success-only evidence, and secret/private-reasoning fields fail closed with actionable diagnostics.
  CHECK: python3 -m unittest tests.test_binding_verification -v
  EXPECT: /OK/
  EVIDENCE: Eighteen lifecycle and adversarial tests passed, including cross-file promoted-registry drift detection.
- [x] G6 — A controlled benchmark demonstrates proposal → external execution evidence → shadow verification against a pinned public-code-derived target or fixture.
  CHECK: python3 scripts/binding_verification_benchmark.py
  EXPECT: /"status": "PASS"/
  EVIDENCE: PASS against OpenAI Agents commit `74636ac9b9c5b7fe9e2e4ca62daff59248769c02`; 1 success, 1 negative, 1 shadow binding, 0 executable.
- [x] G7 — The skill and product documentation explain the trust boundary: an agent may implement a candidate and a controlled host may execute tests, but the Forge only validates evidence and code retains authority.
  CHECK: python3 -m unittest tests.test_skill_install -v && bash scripts/install.sh --check
  EXPECT: /OK.*Codex CLI.*ready/s
  EVIDENCE: Skill package tests and Codex/Claude/Copilot/Gemini installation checks passed; the skill and product references document the trust boundary.
- [x] G8 — Targeted tests, verifier checks, existing benchmarks, and the full regression suite pass without weakening earlier release gates.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: 253 tests passed with 1 skipped; product contract 6/6; all reconstruction, acquisition, integration, binding, real-world, and observed-runtime benchmarks passed; compileall and whitespace checks passed.
