# Gates: Product Coherence V1

Scope: Make one executable lifecycle the public specification for every existing Forge entry path, without duplicating compilers or weakening authority gates.

- [x] G1: The canonical lifecycle validates its own schema, declares every real persisted stage and next-action value, and contains no dangling transition.
  CHECK: python3 -m unittest tests.test_product_coherence.LifecycleContractTests -v
  EXPECT: /OK/
  EVIDENCE: LifecycleContractTests ran 5 tests; OK. Forbidden transition writes and unknown stages fail closed.

- [x] G2: `start` executes real repository/code, document/SOP, agentic-runtime, and resolved-case entry journeys; it never silently drops a supplied input.
  CHECK: python3 -m unittest tests.test_product_coherence.StartJourneyTests -v
  EXPECT: /OK/
  EVIDENCE: StartJourneyTests ran 6 tests against real fixtures; OK. Repository/URL plus cases is explicitly rejected.

- [x] G3: `inspect` combines the persisted project with canonical guidance, and `continue` gives an actionable but non-mutating handoff at human/evidence gates.
  CHECK: python3 -m unittest tests.test_product_coherence.GuidanceTests -v
  EXPECT: /OK/
  EVIDENCE: GuidanceTests ran 4 tests; OK. CLI smoke reached evidence_inventoried and returned combined JSON plus an exact non-mutating command.

- [x] G4: The public documentation and installed skill describe `start → inspect → continue` once and classify low-level commands as advanced implementation interfaces.
  CHECK: python3 -m unittest tests.test_product_coherence.DocumentationTests -v
  EXPECT: /OK/
  EVIDENCE: DocumentationTests ran 1 test; OK. Skill installation check passed for agents, Claude, Codex, Copilot, and Gemini.

- [x] G5: The existing six-archetype product acceptance corpus remains green.
  CHECK: python3 scripts/evaluate_product_contract.py
  EXPECT: /PRODUCT CONTRACT: PASS \(6\/6\)/
  EVIDENCE: PRODUCT CONTRACT: PASS (6/6).

- [x] G6: The full repository, public benchmarks, compilation, and whitespace checks remain green.
  CHECK: python3 -m unittest discover -s tests && python3 scripts/work_system_benchmark.py && python3 scripts/direct_acquisition_benchmark.py && python3 scripts/integration_package_benchmark.py && python3 scripts/binding_verification_benchmark.py && python3 scripts/shadow_controller_benchmark.py && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: Ran 305 tests in 46.266s; OK (skipped=1). Work-system, direct-acquisition, integration-package, binding-verification, and shadow-controller benchmarks PASS. compileall and git diff --check PASS.

Ledger note: the optional Node gate-check wrapper is unavailable because the local Node binary cannot load `libsimdjson.29.dylib`; every declared CHECK was executed directly and its measured evidence is recorded above.
