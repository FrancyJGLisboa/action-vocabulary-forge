import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from predicates import PredicateError, evaluate_all, evaluate_predicate, parse_predicate  # noqa: E402

# Optional: a second bundle placed next to the repo (any name) exercises real-world predicates.
EXTRA_BUNDLE = next((p for p in sorted(ROOT.parent.glob("*-validation")) if (p / "state_registry.yaml").is_file()), ROOT.parent / "extra-validation")


def bundle_predicates(bundle: Path) -> list[str]:
    found: list[str] = []
    for name, key, field in (
        ("state_registry.yaml", "states", "observable_predicate"),
        ("action_registry.yaml", "actions", "preconditions"),
        ("transition_graph.yaml", "transitions", "guard"),
    ):
        doc = yaml.safe_load((bundle / name).read_text(encoding="utf-8"))
        for item in doc.get(key, []):
            value = item.get(field)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
    return found


class PredicateTests(unittest.TestCase):
    def test_example_bundle_predicates_parse(self):
        preds = bundle_predicates(ROOT / "examples" / "validation-bundle")
        self.assertTrue(preds)
        for text in preds:
            parse_predicate(text)

    @unittest.skipUnless(EXTRA_BUNDLE.is_dir(), "no extra *-validation bundle next to the repo")
    def test_extra_bundle_predicates_parse(self):
        for text in bundle_predicates(EXTRA_BUNDLE):
            parse_predicate(text)

    def test_operators(self):
        state = {"n": 3, "s": "failed", "flag": True}
        self.assertTrue(evaluate_predicate("n == 3", state))
        self.assertTrue(evaluate_predicate("n != 4", state))
        self.assertTrue(evaluate_predicate("n > 2", state))
        self.assertTrue(evaluate_predicate("n >= 3", state))
        self.assertTrue(evaluate_predicate("n < 4", state))
        self.assertTrue(evaluate_predicate("n <= 3", state))
        self.assertFalse(evaluate_predicate("n > 3", state))
        self.assertTrue(evaluate_predicate("s == 'failed'", state))
        self.assertTrue(evaluate_predicate('s == "failed"', state))
        self.assertTrue(evaluate_predicate("flag == true", state))
        self.assertFalse(evaluate_predicate("flag == false", state))

    def test_and_and_bare_words(self):
        state = {"choice": "none_of_candidates", "p_new": 0.7, "target_auto_threshold": 0.9, "conf": 0.95}
        self.assertTrue(evaluate_predicate("choice == none_of_candidates and p_new >= 0.5", state))
        self.assertFalse(evaluate_predicate("choice == none_of_candidates and p_new >= 0.8", state))
        # RHS identifier resolves to a state key when present.
        self.assertTrue(evaluate_predicate("conf >= target_auto_threshold", state))
        self.assertFalse(evaluate_predicate("p_new >= target_auto_threshold", state))

    def test_missing_key_is_false_and_ordering_on_strings_is_false(self):
        self.assertFalse(evaluate_predicate("absent == 1", {}))
        self.assertFalse(evaluate_predicate("s > 1", {"s": "abc"}))
        self.assertEqual(evaluate_all(["a == 1", "b == 2"], {"a": 1}), ["b == 2"])

    def test_dotted_paths(self):
        self.assertTrue(evaluate_predicate("decision.confidence >= 0.9", {"decision": {"confidence": 0.95}}))

    def test_malformed(self):
        for text in ("", "n", "n === 1", "n = 1", "n > ", "n > !!"):
            with self.assertRaises(PredicateError):
                parse_predicate(text)


if __name__ == "__main__":
    unittest.main()
