import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_action_bundle as v  # noqa: E402

EXAMPLE = ROOT / "examples" / "validation-bundle"


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.bundle = Path(self.directory) / "bundle"
        shutil.copytree(EXAMPLE, self.bundle)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def edit(self, name, mutate):
        path = self.bundle / name
        doc = yaml.safe_load(path.read_text())
        mutate(doc)
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    def errors(self):
        errors, warnings, counts = v.validate(self.bundle)
        return errors

    def assert_error(self, fragment):
        errors = self.errors()
        self.assertTrue(any(fragment in e for e in errors), f"{fragment!r} not in {errors}")

    def test_example_is_valid_with_stub_warning(self):
        errors, warnings, counts = v.validate(EXAMPLE)
        self.assertEqual(errors, [])
        self.assertTrue(any("human_review binding" in w and "stub" in w for w in warnings))
        self.assertEqual(counts["actions"], 2)

    def test_binding_without_evidence(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].pop("evidence_refs"))
        self.assert_error("binding without evidence")

    def test_binding_unknown_evidence(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].__setitem__("evidence_refs", ["nope"]))
        self.assert_error("unknown evidence reference")

    def test_binding_secret_value(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].__setitem__("headers", {"Authorization": "Bearer abc"}))
        self.assert_error("looks like a secret")

    def test_binding_auth_env_is_allowed(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].__setitem__("auth_env", "MY_API_TOKEN"))
        self.assertEqual(self.errors(), [])

    def test_binding_kind_specific(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].update({"kind": "http", "locator": "http://x"}))
        self.assert_error("https://")
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].update({"kind": "cli", "locator": "x"}))
        self.assert_error("argv")
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].update({"kind": "python_callable", "locator": "no-colon"}))
        self.assert_error("module:callable")
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].update({"kind": "bogus"}))
        self.assert_error("invalid kind")

    def test_arg_mapping_must_use_declared_parameters(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0]["binding"].__setitem__("arg_mapping", {"ghost": "x"}))
        self.assert_error("not a declared parameter")

    def test_unparseable_predicate(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][0].__setitem__("preconditions", ["retry_budget >"]))
        self.assert_error("unparseable predicate")
        self.edit("state_registry.yaml", lambda d: d["states"][0].__setitem__("observable_predicate", "status = pending"))
        self.assert_error("state validation_pending observable_predicate: unparseable")

    def test_fallback_must_be_safe(self):
        self.edit("action_registry.yaml", lambda d: d["actions"][1].update({"risk": "critical"}))
        self.assert_error("must be reversible and not critical")

    def test_policy_checks(self):
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {"min_confidence": 1.5}))
        self.assert_error("between 0 and 1")
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {"default_when_uncalibrated": "maybe"}))
        self.assert_error("abstain or allow")
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {"questions": {"ghost": {}}}))
        self.assert_error("unknown question")
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {"questions": {"next_validation_action": {"actions": {"ghost": 0.5}}}}))
        self.assert_error("not an executor of that question")
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {"questions": {"next_validation_action": {"actions": {"retry": 0.9}}}}))
        self.assertEqual(self.errors(), [])

    def test_empty_policy_warns(self):
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("policy", {}))
        errors, warnings, _ = v.validate(self.bundle)
        self.assertEqual(errors, [])
        self.assertTrue(any("policy is empty" in w for w in warnings))

    def test_question_types(self):
        def to_noul(d):
            q = d["classifier_questions"][0]
            q.update({"type": "noul", "yes_action_id": "retry", "no_action_id": "human_review"})
            q.pop("choices"); q.pop("abstention_choice")
        self.edit("jev_adapter_spec.yaml", to_noul)
        self.assertEqual(self.errors(), [])

        def to_score(d):
            q = d["classifier_questions"][0]
            q.update({"type": "score", "levels": [
                {"id": "low", "criterion": "a", "executor_action_id": "human_review"},
                {"id": "high", "criterion": "b", "executor_action_id": "retry"},
            ]})
            q.pop("yes_action_id"); q.pop("no_action_id")
        self.edit("jev_adapter_spec.yaml", to_score)
        self.assert_error("Score needs abstention_action_id")
        self.edit("jev_adapter_spec.yaml", lambda d: d["classifier_questions"][0].__setitem__("abstention_action_id", "human_review"))
        self.assertEqual(self.errors(), [])
        self.edit("jev_adapter_spec.yaml", lambda d: d["classifier_questions"][0].__setitem__("type", "essay"))
        self.assert_error("invalid type")

    def test_dynamic_criteria(self):
        self.edit("jev_adapter_spec.yaml", lambda d: d["classifier_questions"][0].__setitem__("criteria_source", "dynamic"))
        self.assert_error("dynamic_executor_action_id")
        self.edit("jev_adapter_spec.yaml", lambda d: d["classifier_questions"][0].__setitem__("dynamic_executor_action_id", "retry"))
        self.assertEqual(self.errors(), [])
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("dynamic_criteria_source", "legacy"))
        errors, warnings, _ = v.validate(self.bundle)
        self.assertTrue(any("deprecated" in w for w in warnings))

    def test_endpoint_must_be_https(self):
        self.edit("jev_adapter_spec.yaml", lambda d: d.__setitem__("endpoint", "http://insecure"))
        self.assert_error("https://")

    def test_main_prints_and_exits(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys, "argv", ["validate", str(EXAMPLE)]):
            code = v.main()
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("VALID"))


if __name__ == "__main__":
    unittest.main()
