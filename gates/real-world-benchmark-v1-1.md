# Gate: Real-world benchmark V1.1

Scope: measure conservative recovery of bounded decisions while adding pinned
negative controls for open generation, incidental lists, and deterministic code.

- [x] G1: Every positive and negative entry has full public provenance, minimal reviewed excerpt, expected shape, and rationale; synthetic excerpts are excluded from external readiness.
  CHECK: python3 scripts/real_world_benchmark.py --validate-only
  EXPECT: /CORPUS: PASS/
  EVIDENCE: PROVENANCE: PASS; 4 external_exact entries verified against pinned raw URLs; 8 synthetic regression entries excluded

- [x] G2: The evaluator reports separate external-only and synthetic confusion matrices.
  CHECK: python3 scripts/real_world_benchmark.py
  EXPECT: /BENCHMARK: (PASS|HOLD)/
  EVIDENCE: external TP=2 FP=0 FN=0 TN=2; synthetic TP=5 FP=0 FN=0 TN=3

- [x] G3: Recovery of bounded decisions does not create false positives on available negative controls.
  CHECK: python3 -m unittest tests.test_real_world_benchmark -v
  EXPECT: /OK/
  EVIDENCE: benchmark tests passed; external FP=0 on 2 negative controls; synthetic FP=0 on 3 negative controls

- [x] G4: All discovered outputs remain hypothesis-only with no bindings, execution, or JEV calls.
  CHECK: python3 -m unittest tests.test_real_world_benchmark -v
  EXPECT: /OK/
  EVIDENCE: scanner output retains maturity=hypothesis and shadow_eligible=false

- [x] G5: Full repository verification and the unlazy gate checker pass without network access.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: 185 tests passed; compileall, diff check, and unlazy ALL MET (15 met)
