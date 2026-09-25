# Gates: Integration Package V1

Scope: Compile a validated Work System Map into a reviewable, non-executing integration package that makes the missing adapter, legal-action, binding, controller, loop, verification, and telemetry work explicit without inventing runtime authority.

- [x] G1: The product CLI advances only a `work_system_mapped` project through an explicit integration-preparation command and records a durable `integration_planned` stage.
  CHECK: python3 -m unittest tests.test_integration_package.IntegrationPackageLifecycleTests -v
  EXPECT: /OK/
  EVIDENCE: Four lifecycle tests passed: mapped transition, CLI decision selection, unmapped refusal, and unknown-decision refusal without stage advancement.

- [x] G2: Each selected bounded decision compiles to an action registry, observable-state adapter contract, legal-action policy, controller plan, loop plan, and telemetry contract with stable IDs.
  CHECK: python3 -m unittest tests.test_integration_package.IntegrationPackageContractTests -v
  EXPECT: /OK/
  EVIDENCE: Three contract tests passed; stable IDs, all seven artifacts, Work System Map hash, and source inventory retention were verified.

- [x] G3: No binding is executable unless supported by verified runtime or observed-trace evidence; reconstruction-only actions become explicit stubs with named verification work.
  CHECK: python3 -m unittest tests.test_integration_package.IntegrationPackageSafetyTests -v
  EXPECT: /OK/
  EVIDENCE: Three safety tests passed; bindings are stubs, activation and fallback are not invented, and the loop remains blocked and non-executing.

- [x] G4: The package contains runnable contract checks that validate observation shape, legal-action filtering, binding coverage, loop stop conditions, and telemetry fields without executing the target system.
  CHECK: python3 -m unittest tests.test_integration_package.IntegrationPackageVerificationTests -v
  EXPECT: /OK/
  EVIDENCE: Seven verifier tests passed, including adversarial observation, action coverage, binding, loop, telemetry, and artifact-hash corruption; no target code is imported or called.

- [x] G5: At least one pinned public work-system fixture compiles end to end from reconstruction to an integration package and retains its source provenance and descriptive-only boundary.
  CHECK: python3 scripts/integration_package_benchmark.py
  EXPECT: INTEGRATION PACKAGE BENCHMARK: PASS
  EVIDENCE: Pinned OpenAI Agents reconstruction produced 3 decisions and 9 stub actions with retained provenance and zero executable bindings; benchmark PASS.

- [x] G6: The installed skill and product documentation explain that the package is an implementation contract, not an autonomous runtime or permission to execute.
  CHECK: python3 -m unittest tests.test_skill_install -v && python3 scripts/evaluate_product_contract.py --check-target
  EXPECT: TARGET CONTRACT: PASS
  EVIDENCE: Four skill-package tests and TARGET CONTRACT passed; README, SKILL, product specs, reference, metadata, and changelog describe the non-executing boundary.

- [x] G7: Existing acquisition, reconstruction, discovery, and observed-runtime benchmarks remain green.
  CHECK: python3 scripts/direct_acquisition_benchmark.py && python3 scripts/work_system_benchmark.py && python3 scripts/real_world_benchmark.py && python3 scripts/observed_runtime_benchmark.py
  EXPECT: /DIRECT ACQUISITION BENCHMARK: PASS.*WORK SYSTEM RECONSTRUCTION: PASS.*PRODUCT READINESS: PASS.*OBSERVED RUNTIME BENCHMARK: PASS/s
  EVIDENCE: Direct acquisition, work-system reconstruction, real-world product-readiness, and observed-runtime benchmarks all passed.

- [x] G8: The full repository passes tests, Python syntax checks, and whitespace validation.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: 234 tests passed with 1 skipped; compileall and `git diff --check` passed.
