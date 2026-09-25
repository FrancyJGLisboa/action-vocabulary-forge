# Gates: Observed Runtime Benchmark V1

Scope: Prove that source-discovered bounded decisions can be captured from real public-repository executions and compiled into privacy-safe observed workflow maps.

- [x] G1: At least two pinned public runtimes have executable decision paths, verified licenses, and baseline tests that run locally without paid model calls.
  CHECK: python3 scripts/observed_runtime_benchmark.py --list-runtimes
  EXPECT: RUNTIME COVERAGE: PASS
  EVIDENCE: OpenAI Agents SDK and Vajra are pinned to full commits with MIT license URLs; their targeted offline baselines passed 2/2 and 3/3 tests respectively. RUNTIME COVERAGE: PASS.

- [x] G2: Every runtime event set is produced by an actual repository test/execution path and carries pinned source, commit, capture-point, and command provenance.
  CHECK: python3 scripts/observed_runtime_benchmark.py --verify-provenance
  EXPECT: RUNTIME PROVENANCE: PASS
  EVIDENCE: Eight frozen events were captured from actual Runner/Bun executions; event logs, adapters, and the Vajra integration patch have verified SHA-256 hashes. RUNTIME PROVENANCE: PASS.

- [x] G3: Normalized events validate without hidden reasoning, illegal actions, source edits in the committed upstream snapshot, or action execution by the Forge.
  CHECK: python3 scripts/observed_runtime_benchmark.py --verify-safety
  EXPECT: OBSERVATION SAFETY: PASS
  EVIDENCE: Both pinned snapshots remain unmodified; Vajra instrumentation ran in a separate worktree. OBSERVATION SAFETY: PASS.

- [x] G4: The observed event sets compile through the real Forge observation path into descriptive Decision System Maps for every runtime.
  CHECK: python3 scripts/observed_runtime_benchmark.py
  EXPECT: OBSERVED RUNTIME BENCHMARK: PASS
  EVIDENCE: OpenAI 4 events/2 cases/1 repeated path; Vajra 4 events/3 cases/0 repeated paths. OBSERVED RUNTIME BENCHMARK: PASS.

- [x] G5: At least one runtime demonstrates a repeated multi-step decision path across independent cases, rather than only isolated labels.
  CHECK: python3 scripts/observed_runtime_benchmark.py --require-repeated-path
  EXPECT: REPEATED PATH EVIDENCE: PASS
  EVIDENCE: OpenAI repeats refund -> closer across two independent cases. REPEATED PATH EVIDENCE: PASS.

- [x] G6: Offline regression tests reject fabricated provenance, private reasoning, illegal vocabularies, and insufficient runtime coverage.
  CHECK: python3 -m unittest tests.test_observed_runtime_benchmark -v
  EXPECT: /OK/
  EVIDENCE: Ran 6 tests in 0.059s | OK.

- [x] G7: The static external benchmark remains green after runtime-observation work.
  CHECK: python3 scripts/real_world_benchmark.py
  EXPECT: PRODUCT READINESS: PASS
  EVIDENCE: 11 external cases; precision=1.000, recall=1.000, specificity=1.000. PRODUCT READINESS: PASS.

- [x] G8: The complete repository remains green on Python 3.11-compatible syntax with no whitespace errors.
  CHECK: python3 -m unittest discover -s tests && python3 -m compileall -q scripts tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: Ran 191 tests in 8.551s | OK (skipped=1); compileall and git diff --check passed.
