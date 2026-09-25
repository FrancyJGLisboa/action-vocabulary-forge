# Gates: JEV shadow transport V1

Scope: Connect a Forge shadow controller to TypeSafe JEV through `TYPESAFE_API_KEY`, while preserving deterministic legality checks, non-execution, and privacy-safe receipts.

- [x] G1: The TypeSafe transport reads credentials only from the environment, requires HTTPS, sends only the bounded Choice payload, and returns validated answer metadata without exposing the key.
  CHECK: python3 -m unittest tests.test_jev_shadow_transport.JevTransportTests -v
  EXPECT: /OK/
  EVIDENCE: JevTransportTests ran 4 tests; OK, including non-finite response rejection.

- [x] G2: A Forge command runs observations through JEV in shadow mode, persists sanitized receipts, and reports zero binding invocations.
  CHECK: python3 -m unittest tests.test_jev_shadow_transport.ShadowRunTests -v
  EXPECT: /OK/
  EVIDENCE: ShadowRunTests ran 2 tests; OK. Live integrated run recorded binding_invocations=0 and production_authority=false.

- [x] G3: The CLI wiring and fail-closed error paths are covered without requiring network access or a real key.
  CHECK: python3 -m unittest tests.test_jev_shadow_transport.CliTests -v
  EXPECT: /OK/
  EVIDENCE: CliTests ran 2 tests; OK, including missing-key refusal.

- [x] G4: One harmless synthetic Choice succeeds against the live TypeSafe API using the key loaded by zsh; no repository or user content is sent.
  EVIDENCE: Live synthetic call returned connected=true, model=jev-1.13.0, answer=route_human_review, confidence=1.0; integrated public-fixture shadow returned confidence=0.86, zero binding invocations, and production_authority=false.

- [x] G5: Product and skill documentation explain setup, the shadow-only boundary, the artifact contract, and the next promotion gate.
  CHECK: python3 -m unittest tests.test_jev_shadow_transport.DocumentationTests -v
  EXPECT: /OK/
  EVIDENCE: DocumentationTests ran 1 test; OK.

- [x] G6: The full repository tests, product benchmarks, and whitespace checks remain green.
  CHECK: python3 -m unittest discover -s tests && python3 scripts/evaluate_product_contract.py && git diff --check
  EXPECT: /PRODUCT CONTRACT: PASS/
  EVIDENCE: Ran 289 tests in 49.180s; OK (skipped=1). Product contract PASS 6/6. Five public/product benchmarks PASS. git diff --check passed.

Ledger note: the optional Node gate-check wrapper could not start because the local Node binary is missing `libsimdjson.29.dylib`; every declared CHECK was run directly and its deciding output is recorded above.
