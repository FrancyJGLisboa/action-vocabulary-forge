import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_adapter  # noqa: E402


class GeneratedAdapterTests(unittest.TestCase):
    def test_generated_module_validates_choice_and_guards_execution(self):
        bundle = generate_adapter.load_bundle(ROOT / "examples" / "validation-bundle")
        source = generate_adapter.render(bundle, "generated_validation_adapter")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated_validation_adapter.py"
            path.write_text(source, encoding="utf-8")
            spec = importlib.util.spec_from_file_location("generated_validation_adapter", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            decision = module.parse_response(
                {
                    "answers": {
                        "next_validation_action": {
                            "choice": "retry",
                            "confidence": 0.96,
                            "probabilities": {"retry": 0.96, "human_review": 0.04},
                        }
                    }
                },
                "next_validation_action",
            )
            self.assertEqual(decision.action_id, "retry")
            self.assertEqual(
                module.execute(
                    decision,
                    {"state_id": "validation_failed", "retry_budget": 1, "record_exists": True},
                    handlers={"retry": lambda state: "started"},
                    guard=lambda action_id, state: action_id == "retry",
                ),
                "started",
            )
            with self.assertRaises(module.ExecutionBlocked):
                module.execute(
                    decision,
                    {"state_id": "awaiting_human_review", "retry_budget": 1, "record_exists": True},
                    handlers={"retry": lambda state: "started"},
                )

    def test_invalid_choice_is_rejected(self):
        bundle = generate_adapter.load_bundle(ROOT / "examples" / "validation-bundle")
        namespace = {}
        exec(compile(generate_adapter.render(bundle, "adapter"), "adapter.py", "exec"), namespace)
        with self.assertRaises(namespace["IllegalChoice"]):
            namespace["parse_response"](
                {"answers": {"next_validation_action": {"choice": "delete_everything"}}},
                "next_validation_action",
            )

    def test_noul_and_score_questions_map_to_actions(self):
        bundle = generate_adapter.load_bundle(ROOT / "examples" / "validation-bundle")
        bundle["questions"] = {
            "is_transient": {
                "question_id": "is_transient",
                "surface_id": "resolve_validation_failure",
                "type": "noul",
                "instruction": "Is the failure transient?",
                "yes_action_id": "retry",
                "no_action_id": "human_review",
            },
            "urgency": {
                "question_id": "urgency",
                "surface_id": "resolve_validation_failure",
                "type": "score",
                "instruction": "How urgent is the failure?",
                "levels": [
                    {"id": "low", "criterion": "Can wait", "executor_action_id": "human_review"},
                    {"id": "high", "criterion": "Act now", "executor_action_id": "retry"},
                ],
            },
        }
        namespace = {}
        exec(compile(generate_adapter.render(bundle, "adapter"), "adapter.py", "exec"), namespace)
        noul = namespace["parse_response"]({"answers": {"is_transient": {"noul": 0.9}}}, "is_transient")
        score = namespace["parse_response"]({"answers": {"urgency": {"score": 1.0}}}, "urgency")
        self.assertEqual(noul.action_id, "retry")
        self.assertEqual(score.action_id, "retry")


if __name__ == "__main__":
    unittest.main()
