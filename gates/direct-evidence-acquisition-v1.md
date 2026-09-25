# Gates: Direct Evidence Acquisition V1

Scope: Let a user point the Forge at repositories, URLs, and local paths without first authoring an evidence manifest or reconstruction proposal, while preserving the evidence boundary between deterministic acquisition, System 2 interpretation, and compiler validation.

- [x] G1: `forge.py reconstruct` accepts one or more `--repo`, `--url`, and `--source` inputs, preserves the existing manifest workflow, and explains the two-phase result without requiring user-authored YAML.
  CHECK: python3 -m unittest tests.test_direct_evidence_acquisition.DirectEvidenceAcquisitionCLITests -v && python3 scripts/forge.py reconstruct --help
  EXPECT: --source SOURCE
  EVIDENCE: Ran 3 CLI tests; direct sources reached `evidence_inventoried`, legacy manifests reached `work_system_mapped`, and all passed.

- [x] G2: Acquisition creates a bounded, hashed, reviewable evidence package with stable source IDs, origin locators, source kinds, evidence status, and repository commit provenance where available.
  CHECK: python3 -m unittest tests.test_direct_evidence_acquisition.DirectEvidenceAcquisitionContractTests -v
  EXPECT: /OK/
  EVIDENCE: Ran 3 tests in 0.053s | OK

- [x] G3: The first phase writes a System 2 reconstruction request and proposal template, persists stage `evidence_inventoried`, and grants no action authority or shadow eligibility.
  CHECK: python3 -m unittest tests.test_direct_evidence_acquisition.DirectEvidenceAcquisitionLifecycleTests -v
  EXPECT: /OK/
  EVIDENCE: Ran 1 test in 0.020s | OK

- [x] G4: A validated System 2 proposal can finalize the same acquired project into a Work System Map without editing the generated evidence manifest by hand.
  CHECK: python3 -m unittest tests.test_direct_evidence_acquisition.DirectEvidenceAcquisitionFinalizeTests -v
  EXPECT: /OK/
  EVIDENCE: Ran 2 tests in 0.092s | OK

- [x] G5: Unsafe or misleading acquisition is rejected or skipped explicitly: non-HTTPS URLs, unsupported repository locators, binary files, oversized artifacts, symlink escapes, duplicate inputs, and empty evidence sets.
  CHECK: python3 -m unittest tests.test_direct_evidence_acquisition.DirectEvidenceAcquisitionSafetyTests -v
  EXPECT: /OK/
  EVIDENCE: Ran 5 tests in 0.032s | OK

- [x] G6: A reproducible benchmark exercises direct acquisition over heterogeneous evidence derived from at least two public systems and verifies source provenance and deterministic output.
  CHECK: python3 scripts/direct_acquisition_benchmark.py
  EXPECT: DIRECT ACQUISITION BENCHMARK: PASS
  EVIDENCE: openai_agents_contribution_work: 4 sources, 4 kinds, deterministic=True, provenance=True [PASS] | DIRECT ACQUISITION BENCHMARK: PASS

- [x] G7: The installed skill and product documentation describe the one-invocation user experience and the deterministic/System 2/compiler boundary accurately.
  CHECK: python3 -m unittest tests.test_skill_install -v && python3 scripts/evaluate_product_contract.py --check-target
  EXPECT: TARGET CONTRACT: PASS
  EVIDENCE: Ran 3 tests in 0.134s | OK

- [x] G8: Existing benchmarks and the full repository remain green under syntax, test, and whitespace validation.
  CHECK: python3 scripts/work_system_benchmark.py && python3 scripts/real_world_benchmark.py && python3 scripts/observed_runtime_benchmark.py && python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /WORK SYSTEM RECONSTRUCTION: PASS.*PRODUCT READINESS: PASS.*OBSERVED RUNTIME BENCHMARK: PASS.*OK/s
  EVIDENCE: Existing work-system, real-world, and observed-runtime benchmarks passed; 216 tests passed (1 skipped); compileall and git diff checks passed.
