# Gates: Product target and acceptance harness

Scope: Fix the final product contract and add a repeatable acceptance harness across materially different input archetypes.

- [x] G1: A durable product-target document defines one user outcome, the end-to-end lifecycle, evidence boundaries, and explicit release criteria.
  CHECK: python3 scripts/evaluate_product_contract.py --check-target
  EXPECT: /TARGET CONTRACT: PASS/
  EVIDENCE: TARGET CONTRACT: PASS

- [x] G2: The acceptance corpus covers source code, agentic workflows, documents-only cold starts, mixed material, and resolved histories without silently treating hypotheses as automation authority.
  CHECK: python3 scripts/evaluate_product_contract.py --list-scenarios
  EXPECT: SCENARIO COVERAGE: PASS 6/6
  EVIDENCE: resolved_shipment_history: resolved_history | SCENARIO COVERAGE: PASS 6/6

- [x] G3: A deterministic evaluator runs the declared product scenarios and rejects missing, unsafe, or stage-inconsistent outputs.
  CHECK: python3 scripts/evaluate_product_contract.py
  EXPECT: /PRODUCT CONTRACT: PASS/
  EVIDENCE: resolved_shipment_history: PASS - contract met | PRODUCT CONTRACT: PASS (6/6)

- [x] G4: Automated tests cover the product-target validator and the input-archetype acceptance matrix.
  CHECK: python3 -m unittest tests.test_product_contract -v
  EXPECT: /OK/
  EVIDENCE: Ran 4 tests in 0.229s | OK

- [x] G5: The complete repository remains green and contains no whitespace errors.
  CHECK: python3 -m unittest discover -s tests && git diff --check
  EXPECT: /OK/
  EVIDENCE: Ran 181 tests in 7.729s | OK (skipped=1)
