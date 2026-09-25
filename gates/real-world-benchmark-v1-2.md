# Gate: Real-world benchmark V1.2

Scope: external exact bounded LLM routers across multiple SDK/provider ecosystems.

- [x] G1: At least 10 external exact cases cover 4 positives, 4 negatives, 3 repositories, and 2 SDK/provider ecosystems.
  CHECK: python3 scripts/real_world_benchmark.py --validate-only
  EXPECT: /CORPUS: PASS/
  EVIDENCE: 11 external cases, 5 positives, 6 negatives, 5 repositories, 6 declared ecosystems, 2 positive LLM ecosystems (OpenAI SDK and Vercel AI/OpenAI)
- [x] G2: Every external case has pinned raw URL, line span, upstream/local hashes, license, shape, actions, and rationale.
  CHECK: python3 scripts/real_world_benchmark.py --verify-network
  EXPECT: /PROVENANCE: PASS/
  EVIDENCE: PROVENANCE: PASS for all 11 external exact entries, including notebook-cell and exact multi-tool extraction
- [x] G3: External precision, recall, and specificity are each at least 0.90, with complete confusion matrices by ecosystem and shape.
  CHECK: python3 scripts/real_world_benchmark.py
  EXPECT: /BENCHMARK: (PASS|HOLD)/
  EVIDENCE: external TP=5 FP=0 FN=0 TN=6; precision=1.000 recall=1.000 specificity=1.000
- [x] G4: Cold-start outputs remain hypothesis-only with no bindings, execution, JEV calls, or source edits.
  CHECK: python3 -m unittest tests.test_real_world_benchmark -v
  EXPECT: /OK/
  EVIDENCE: Ran 4 tests in 0.091s | OK
- [x] G5: Full tests, compile, diff, and unlazy checks pass.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: 185 tests passed; compileall, diff check, network provenance, and unlazy ALL MET (20 met)
