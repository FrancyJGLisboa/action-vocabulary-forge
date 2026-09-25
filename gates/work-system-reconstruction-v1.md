# Gates: Public Work System Reconstruction V1

Scope: Reconstruct evidence-backed work systems from heterogeneous public repository material, without collapsing declared, observed, inferred, unknown, or conflicting evidence.

- [x] G1: The frozen benchmark contains at least two pinned public repositories and at least three materially different evidence forms per repository, including public operational history.
  CHECK: python3 scripts/work_system_benchmark.py --list-systems
  EXPECT: PUBLIC SYSTEM COVERAGE: PASS
  EVIDENCE: Pydantic AI has 3 source forms; OpenAI Agents SDK has 4. Both include captured public issue/PR history. Commit-pinned excerpts were re-extracted byte-for-byte from GitHub: PUBLIC SOURCE PROVENANCE: PASS.

- [x] G2: A normalized evidence contract represents actors, artifacts, activities, states, decisions, actions, transitions, and outcomes with exact source provenance and evidence status.
  CHECK: python3 scripts/work_system_benchmark.py --verify-contract
  EXPECT: EVIDENCE CONTRACT: PASS
  EVIDENCE: Both maps contain every required node kind; all decisions have bounded actions and all claims retain source IDs, quotes, and declared/observed status. EVIDENCE CONTRACT: PASS.

- [x] G3: The reconstruction compiler produces one reviewable Work System Map per public system and never upgrades inferred or declared behavior into observed behavior.
  CHECK: python3 scripts/work_system_benchmark.py
  EXPECT: WORK SYSTEM RECONSTRUCTION: PASS
  EVIDENCE: Pydantic AI: 12 nodes/7 links/2 decisions; OpenAI Agents: 13 nodes/7 links/3 decisions. WORK SYSTEM RECONSTRUCTION: PASS.

- [x] G4: Every reconstructed workflow links evidence from at least two source forms and contains actors, artifacts, ordered steps, a bounded decision candidate, and an explicit evidence gap.
  CHECK: python3 scripts/work_system_benchmark.py --verify-cross-source
  EXPECT: CROSS-SOURCE WORKFLOWS: PASS
  EVIDENCE: Each map contains one workflow repeated across 3 public cases, 2+ source forms, named actors/artifacts/decisions, and 2 explicit gaps. CROSS-SOURCE WORKFLOWS: PASS.

- [x] G5: Adversarial tests reject unsupported links, unknown source references, invalid evidence promotion, missing gaps, and accidental execution authority.
  CHECK: python3 -m unittest tests.test_work_system_reconstruction -v
  EXPECT: /OK/
  EVIDENCE: Ran 9 focused tests; all passed, including Forge product-stage persistence.

- [x] G6: The product target and CLI expose reconstruction as a pre-decision stage that can feed later observation, JEV shadow evaluation, or System 2 work without claiming automation readiness.
  CHECK: python3 scripts/evaluate_product_contract.py --check-target
  EXPECT: TARGET CONTRACT: PASS
  EVIDENCE: `forge.py reconstruct` persists stage `work_system_mapped`, next action `review_decision_opportunities`, and no shadow eligibility. TARGET CONTRACT: PASS.

- [x] G7: Existing external and observed-runtime benchmarks remain green.
  CHECK: python3 scripts/real_world_benchmark.py && python3 scripts/observed_runtime_benchmark.py
  EXPECT: /PRODUCT READINESS: PASS.*OBSERVED RUNTIME BENCHMARK: PASS/s
  EVIDENCE: Static external PRODUCT READINESS: PASS; observed-runtime OBSERVED RUNTIME BENCHMARK: PASS.

- [x] G8: The full repository passes tests, Python 3.11 syntax checks, and whitespace validation.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: Ran 200 tests in 8.485s | OK (skipped=1); compileall and git diff --check passed.
