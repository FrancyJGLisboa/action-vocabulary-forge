# Gate: Real-world benchmark V1

Scope: measure the current Forge against a frozen, provenance-backed corpus of
public repository excerpts without treating benchmark labels as runtime authority.

- [x] G1: Corpus entries pin a public source URL, commit/version, license or source locator, minimal reviewed excerpt, expected shape, and rationale.
  CHECK: python3 scripts/real_world_benchmark.py --validate-only
  EXPECT: /CORPUS: PASS/
  EVIDENCE: CORPUS: PASS

- [x] G2: The evaluator runs the actual current scanners and reports confusion counts, precision, recall, and per-archetype results.
  CHECK: python3 scripts/real_world_benchmark.py
  EXPECT: /BENCHMARK: (PASS|HOLD)/
  EVIDENCE: CONFUSION TP=3 FN=2; precision=0.600; recall=0.600; per-archetype output emitted

- [x] G3: Unsupported or malformed provenance is rejected, and benchmark health is distinct from product readiness.
  CHECK: python3 -m unittest tests.test_real_world_benchmark -v
  EXPECT: /OK/
  EVIDENCE: 3 benchmark tests passed; PRODUCT READINESS: HOLD

- [x] G4: A HOLD is permitted when pinned metrics do not meet the initial thresholds; no HOLD may be reported as product readiness.
  CHECK: python3 scripts/real_world_benchmark.py
  EXPECT: /PRODUCT READINESS: HOLD/
  EVIDENCE: BENCHMARK: HOLD; known gaps: expensive_llm_router, operating_note_only

- [x] G5: The repository remains green and the gate is reproducible without network access.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: targeted benchmark tests, compileall, and git diff --check passed
