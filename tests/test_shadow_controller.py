from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import binding_verification  # noqa: E402
import forge  # noqa: E402
import shadow_controller  # noqa: E402
from tests.test_binding_verification import BindingVerificationMixin  # noqa: E402


PUBLIC_MANIFEST = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"


class ShadowControllerMixin(BindingVerificationMixin):
    def verified_project(self) -> Path:
        project = self.project()
        self.propose(project)
        binding_verification.verify_bindings(project=project, observations=self.observations(project))
        return project

    def ids(self, project: Path) -> tuple[dict[str, str], str]:
        registry = yaml.safe_load((project / "integration" / "action_registry.yaml").read_text())
        actions = {item["label"]: item["action_id"] for item in registry["actions"]}
        policy = yaml.safe_load((project / "integration" / "legal_action_policy.yaml").read_text())
        return actions, policy["surfaces"][0]["surface_id"]

    def source(self, source_id: str = "issues_workflow") -> dict:
        manifest = yaml.safe_load(PUBLIC_MANIFEST.read_text())
        system = next(item for item in manifest["systems"] if item["system_id"] == "openai_agents_contribution_work")
        return next(item for item in system["sources"] if item["source_id"] == source_id)

    def controller_proposal(self, project: Path, *, mutate=None) -> Path:
        actions, surface_id = self.ids(project)
        source = self.source()

        def evidence(quote: str) -> dict:
            return {
                "source_id": "issues_workflow",
                "source_sha256": source["sha256"],
                "quote": quote,
            }

        proposal = {
            "schema_version": "1.0",
            "system_id": "openai_agents_contribution_work",
            "states": [
                {
                    "state_id": "inactive_eligible",
                    "label": "Inactive issue eligible for policy",
                    "predicate": "inactive_days >= 7 and closed == false",
                    "terminal": False,
                    "evidence": evidence("days-before-issue-stale: 7"),
                },
                {
                    "state_id": "terminal_closed",
                    "label": "Issue already closed",
                    "predicate": "closed == true",
                    "terminal": True,
                    "evidence": evidence("days-before-issue-close: 3"),
                },
            ],
            "surface": {
                "surface_id": surface_id,
                "question_id": "inactive_item_action_v1",
                "question": "Which reviewed inactive-item action best fits the observable issue state?",
                "active_state_ids": ["inactive_eligible"],
                "fallback_action_id": actions["skip_stale"],
                "min_confidence": 0.8,
                "actions": [
                    {
                        "action_id": actions["mark_stale"],
                        "allowed_state_ids": ["inactive_eligible"],
                        "preconditions": ["inactive_days >= 7", "closed == false"],
                        "evidence": evidence('stale-issue-label: "stale"'),
                    },
                    {
                        "action_id": actions["close"],
                        "allowed_state_ids": ["inactive_eligible"],
                        "preconditions": ["days_since_stale >= 3", "closed == false"],
                        "evidence": evidence("days-before-issue-close: 3"),
                    },
                    {
                        "action_id": actions["skip_stale"],
                        "allowed_state_ids": ["inactive_eligible"],
                        "preconditions": [],
                        "evidence": evidence('exempt-issue-labels: "skip-stale"'),
                    },
                ],
            },
            "loop": {
                "max_iterations": 3,
                "stop_conditions": [
                    "terminal_state",
                    "unknown_state",
                    "ambiguous_state",
                    "no_legal_action",
                    "low_confidence",
                    "human_review",
                    "adapter_error",
                    "decider_error",
                    "illegal_answer",
                    "unchanged_state",
                    "max_iterations",
                    "observation_exhausted",
                ],
            },
        }
        if mutate:
            mutate(proposal)
        path = project / "controller_proposal.yaml"
        path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
        return path

    def prepare_controller(self, project: Path, *, mutate=None) -> dict:
        return shadow_controller.prepare_controller(
            project=project,
            proposal=self.controller_proposal(project, mutate=mutate),
        )

    @staticmethod
    def active_state(**changes) -> dict:
        state = {"inactive_days": 8, "days_since_stale": 0, "closed": False}
        state.update(changes)
        return state


class ShadowControllerProposalTests(ShadowControllerMixin, unittest.TestCase):
    def test_verified_binding_project_advances_without_target_or_jev_call(self):
        project = self.verified_project()
        result = self.prepare_controller(project)
        self.assertEqual(result["stage"], "shadow_controller_ready")
        self.assertFalse(result["safety"]["calls_jev_during_compilation"])
        self.assertFalse(result["safety"]["invokes_bindings"])
        self.assertEqual(forge.load_project(project)["stage"], "shadow_controller_ready")

    def test_cli_prepares_controller(self):
        project = self.verified_project()
        status = forge.main(
            ["prepare-controller", str(project), "--proposal", str(self.controller_proposal(project))]
        )
        self.assertEqual(status, 0)
        self.assertEqual(forge.load_project(project)["stage"], "shadow_controller_ready")

    def test_wrong_stage_is_rejected(self):
        project = self.project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "bindings_verified_for_shadow"):
            self.prepare_controller(project)


class ShadowControllerContractTests(ShadowControllerMixin, unittest.TestCase):
    def test_compiled_plan_covers_state_legality_fallback_confidence_and_loop(self):
        project = self.verified_project()
        result = self.prepare_controller(project)
        plan = yaml.safe_load((project / "integration" / "shadow_controller_plan.yaml").read_text())
        self.assertEqual(len(plan["states"]), 2)
        self.assertTrue(any(item["terminal"] for item in plan["states"]))
        self.assertEqual(plan["surface"]["min_confidence"], 0.8)
        self.assertIn(plan["surface"]["fallback_action_id"], plan["surface"]["candidate_action_ids"])
        self.assertEqual(plan["loop"]["max_iterations"], 3)
        self.assertEqual(result["artifacts"]["shadow_controller_plan"]["sha256"], hashlib.sha256(
            (project / "integration" / "shadow_controller_plan.yaml").read_bytes()
        ).hexdigest())

    def test_predicates_are_parsed_before_artifacts_are_written(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "predicate"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["states"][0].update(predicate="inactive_days in [7, 8]"),
            )
        self.assertFalse((project / "integration" / "shadow_controller_plan.yaml").exists())

    def test_fallback_must_be_known_legal_and_unconditional_in_every_active_state(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "fallback"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["surface"].update(fallback_action_id="invented"),
            )
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "fallback"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["surface"]["actions"][2].update(
                    preconditions=["exempt == true"]
                ),
            )

    def test_loop_limit_and_threshold_are_bounded(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "max_iterations"):
            self.prepare_controller(project, mutate=lambda value: value["loop"].update(max_iterations=0))
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "min_confidence"):
            self.prepare_controller(
                project, mutate=lambda value: value["surface"].update(min_confidence=1.2)
            )

    def test_all_state_and_action_rules_require_exact_hashed_evidence(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "quote is not present"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["surface"]["actions"][0]["evidence"].update(
                    quote="invented authority"
                ),
            )


class ShadowControllerRuntimeTests(ShadowControllerMixin, unittest.TestCase):
    def runtime(self):
        project = self.verified_project()
        self.prepare_controller(project)
        return project, shadow_controller.load_runtime(project)

    def test_decider_receives_only_deterministically_legal_actions(self):
        project, runtime = self.runtime()
        actions, _surface = self.ids(project)
        seen = []

        def decide(question, state, legal_actions):
            seen.append((question, dict(state), list(legal_actions)))
            return {"answer": actions["mark_stale"], "confidence": 0.93}

        result = runtime.run([self.active_state(), {"closed": True}], decide)
        self.assertEqual(seen[0][2], [actions["mark_stale"], actions["skip_stale"]])
        self.assertEqual(result["records"][0]["selected_action_id"], actions["mark_stale"])
        self.assertEqual(result["stop_reason"], "terminal_state")
        self.assertEqual(result["binding_invocations"], 0)

    def test_close_is_legal_only_after_deterministic_precondition(self):
        project, runtime = self.runtime()
        actions, _surface = self.ids(project)
        captured = []

        def decide(_question, _state, legal_actions):
            captured.extend(legal_actions)
            return {"answer": actions["close"], "confidence": 0.9}

        result = runtime.run([self.active_state(days_since_stale=4)], decide)
        self.assertIn(actions["close"], captured)
        self.assertEqual(result["records"][0]["selected_action_id"], actions["close"])

    def test_low_confidence_routes_to_fallback_and_stops(self):
        project, runtime = self.runtime()
        actions, _surface = self.ids(project)
        result = runtime.run(
            [self.active_state()],
            lambda *_args: {"answer": actions["mark_stale"], "confidence": 0.3},
        )
        self.assertEqual(result["stop_reason"], "low_confidence")
        self.assertEqual(result["records"][0]["proposed_action_id"], actions["mark_stale"])
        self.assertEqual(result["records"][0]["selected_action_id"], actions["skip_stale"])
        self.assertTrue(result["records"][0]["abstained"])

    def test_explicit_fallback_routes_to_human_review(self):
        project, runtime = self.runtime()
        actions, _surface = self.ids(project)
        result = runtime.run(
            [self.active_state()],
            lambda *_args: {"answer": actions["skip_stale"], "confidence": 0.95},
        )
        self.assertEqual(result["stop_reason"], "human_review")
        self.assertEqual(result["binding_invocations"], 0)

    def test_illegal_answer_is_rejected_before_any_binding(self):
        _project, runtime = self.runtime()
        with self.assertRaisesRegex(shadow_controller.IllegalShadowChoice, "illegal"):
            runtime.run(
                [self.active_state()],
                lambda *_args: {"answer": "delete_everything", "confidence": 0.99},
            )
        self.assertEqual(runtime.binding_invocations, 0)


class ShadowControllerStopAndPrivacyTests(ShadowControllerMixin, unittest.TestCase):
    def runtime(self):
        project = self.verified_project()
        self.prepare_controller(project)
        return shadow_controller.load_runtime(project)

    def test_terminal_state_stops_without_calling_decider(self):
        runtime = self.runtime()
        calls = []
        result = runtime.run([{"closed": True}], lambda *_args: calls.append(True))
        self.assertEqual(calls, [])
        self.assertEqual(result["stop_reason"], "terminal_state")

    def test_unknown_and_ambiguous_states_stop(self):
        runtime = self.runtime()
        unknown = runtime.run([{"closed": False, "inactive_days": 2}], lambda *_args: None)
        self.assertEqual(unknown["stop_reason"], "unknown_state")
        plan = copy.deepcopy(runtime.plan)
        plan["states"].append(
            {
                "state_id": "duplicate_active",
                "label": "Duplicate",
                "predicate": "inactive_days >= 7 and closed == false",
                "terminal": False,
                "evidence": plan["states"][0]["evidence"],
            }
        )
        plan["plan_digest"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in plan.items() if key != "plan_digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        ambiguous = shadow_controller.ShadowController(plan).run(
            [self.active_state()], lambda *_args: None
        )
        self.assertEqual(ambiguous["stop_reason"], "ambiguous_state")

    def test_no_legal_action_stops_before_decider(self):
        runtime = self.runtime()
        plan = copy.deepcopy(runtime.plan)
        for action in plan["surface"]["actions"]:
            action["allowed_state_ids"] = []
        plan["plan_digest"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in plan.items() if key != "plan_digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        calls = []
        result = shadow_controller.ShadowController(plan).run(
            [self.active_state()], lambda *_args: calls.append(True)
        )
        self.assertEqual(result["stop_reason"], "no_legal_action")
        self.assertEqual(calls, [])

    def test_decider_error_and_max_iterations_stop(self):
        runtime = self.runtime()

        def broken(*_args):
            raise RuntimeError("provider unavailable")

        failed = runtime.run([self.active_state()], broken)
        self.assertEqual(failed["stop_reason"], "decider_error")
        actions = runtime.plan["surface"]["candidate_action_ids"]
        fallback = runtime.plan["surface"]["fallback_action_id"]
        selected = next(item for item in actions if item != fallback)
        states = [self.active_state(tick=index) for index in range(5)]
        limited = runtime.run(states, lambda *_args: {"answer": selected, "confidence": 0.95})
        self.assertEqual(limited["stop_reason"], "max_iterations")
        self.assertEqual(len(limited["records"]), 3)

    def test_unchanged_state_is_not_reasked(self):
        runtime = self.runtime()
        fallback = runtime.plan["surface"]["fallback_action_id"]
        calls = []

        def decide(*_args):
            calls.append(True)
            nonfallback = next(
                item for item in runtime.plan["surface"]["candidate_action_ids"] if item != fallback
            )
            return {"answer": nonfallback, "confidence": 0.95}

        state = self.active_state()
        result = runtime.run([state, dict(state)], decide)
        self.assertEqual(result["stop_reason"], "unchanged_state")
        self.assertEqual(len(calls), 1)

    def test_records_hash_state_and_forbid_private_reasoning(self):
        runtime = self.runtime()
        fallback = runtime.plan["surface"]["fallback_action_id"]
        result = runtime.run(
            [self.active_state()], lambda *_args: {"answer": fallback, "confidence": 0.9}
        )
        rendered = json.dumps(result["records"])
        self.assertNotIn("inactive_days", rendered)
        self.assertRegex(result["records"][0]["observation_sha256"], r"^[0-9a-f]{64}$")
        private = self.active_state(chain_of_thought="hidden")
        blocked = runtime.run([private], lambda *_args: None)
        self.assertEqual(blocked["stop_reason"], "adapter_error")
        self.assertNotIn("hidden", json.dumps(blocked))
        secret = runtime.run([self.active_state(api_key="must-not-pass")], lambda *_args: None)
        self.assertEqual(secret["stop_reason"], "adapter_error")
        self.assertNotIn("must-not-pass", json.dumps(secret))

    def test_observation_exhaustion_is_explicit(self):
        runtime = self.runtime()
        result = runtime.run([], lambda *_args: None)
        self.assertEqual(result["stop_reason"], "observation_exhausted")


class ShadowControllerAdversarialTests(ShadowControllerMixin, unittest.TestCase):
    def test_unknown_action_is_rejected(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "unknown action"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["surface"]["actions"][0].update(action_id="invented"),
            )

    def test_extra_authority_fields_are_rejected(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "unsupported fields"):
            self.prepare_controller(project, mutate=lambda value: value.update(executes_actions=True))

    def test_embedded_secret_is_rejected(self):
        project = self.verified_project()
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "secret"):
            self.prepare_controller(
                project,
                mutate=lambda value: value["surface"].update(question="Use Bearer secret-value"),
            )

    def test_plan_tampering_is_detected(self):
        project = self.verified_project()
        self.prepare_controller(project)
        plan = project / "integration" / "shadow_controller_plan.yaml"
        plan.write_text(plan.read_text() + "tampered: true\n", encoding="utf-8")
        self.assertIn("shadow controller plan hash mismatch", shadow_controller.verify_controller(project))

    def test_integration_artifact_drift_is_detected(self):
        project = self.verified_project()
        self.prepare_controller(project)
        policy = project / "integration" / "legal_action_policy.yaml"
        value = yaml.safe_load(policy.read_text())
        value["executes_actions"] = True
        policy.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        self.assertTrue(any("hash mismatch" in item for item in shadow_controller.verify_controller(project)))

    def test_runtime_refuses_plan_that_grants_authority(self):
        project = self.verified_project()
        self.prepare_controller(project)
        plan = yaml.safe_load((project / "integration" / "shadow_controller_plan.yaml").read_text())
        plan["safety"]["invokes_bindings"] = True
        with self.assertRaisesRegex(shadow_controller.ShadowControllerError, "safety"):
            shadow_controller.ShadowController(plan)


if __name__ == "__main__":
    unittest.main()
