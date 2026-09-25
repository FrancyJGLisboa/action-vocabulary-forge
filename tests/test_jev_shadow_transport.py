from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402
import jev_shadow_transport  # noqa: E402
import shadow_controller  # noqa: E402
import shadow_controller_benchmark as benchmark  # noqa: E402


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _ready_project(workspace: Path) -> tuple[Path, dict[str, str]]:
    project = workspace / "project"
    actions, surface_id, source = benchmark._prepare_verified_binding(project, workspace)
    evidence = lambda quote: benchmark._evidence(source, quote)  # noqa: E731
    proposal = workspace / "controller_proposal.yaml"
    proposal.write_text(
        yaml.safe_dump(
            {
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
                    "stop_conditions": sorted(shadow_controller.REQUIRED_STOP_CONDITIONS),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    shadow_controller.prepare_controller(project=project, proposal=proposal)
    return project, actions


class JevTransportTests(unittest.TestCase):
    def test_environment_key_https_and_bounded_choice_payload(self):
        captured = {}
        result = {
            "model": "jev-1.13.0",
            "answers": {
                "route_v1": {
                    "type": "choice",
                    "choice": "review",
                    "confidence": 0.84,
                    "probabilities": {"accept": 0.08, "review": 0.92},
                }
            },
            "usage": {"input_tokens": 25, "output_tokens": 8},
        }

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response(result)

        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "unit-test-secret"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                send = jev_shadow_transport.typesafe_transport(timeout=4.0)
                response = send(
                    {
                        "model": "jev-latest",
                        "state": {"context": {"ready": False}},
                        "questions": {
                            "route_v1": {
                                "type": "choice",
                                "instructions": "Choose the legal action.",
                                "criteria": {"accept": "Accept", "review": "Review"},
                            }
                        },
                    }
                )

        request = captured["request"]
        self.assertEqual(request.full_url, jev_shadow_transport.DEFAULT_ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-test-secret")
        self.assertEqual(captured["timeout"], 4.0)
        self.assertEqual(json.loads(request.data), {
            "model": "jev-latest",
            "state": {"context": {"ready": False}},
            "questions": {
                "route_v1": {
                    "type": "choice",
                    "instructions": "Choose the legal action.",
                    "criteria": {"accept": "Accept", "review": "Review"},
                }
            },
        })
        self.assertEqual(response, result)
        self.assertNotIn("unit-test-secret", repr(send))

    def test_missing_key_and_non_https_endpoint_fail_before_network(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(jev_shadow_transport.JevAuthenticationError, "TYPESAFE_API_KEY"):
                jev_shadow_transport.typesafe_transport()
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret"}, clear=True):
            with self.assertRaisesRegex(jev_shadow_transport.JevTransportError, "HTTPS"):
                jev_shadow_transport.typesafe_transport(endpoint="http://example.test/systemone")

    def test_http_and_malformed_responses_fail_without_response_body_or_secret(self):
        error = urllib.error.HTTPError(
            jev_shadow_transport.DEFAULT_ENDPOINT,
            401,
            "Unauthorized",
            {},
            None,
        )
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "never-print-me"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=error):
                send = jev_shadow_transport.typesafe_transport()
                with self.assertRaises(jev_shadow_transport.JevAuthenticationError) as caught:
                    send({"model": "jev-latest", "state": "safe", "questions": {}})
                self.assertNotIn("never-print-me", str(caught.exception))
            error.close()
            with mock.patch("urllib.request.urlopen", return_value=_Response(["not", "a", "mapping"])):
                send = jev_shadow_transport.typesafe_transport()
                with self.assertRaisesRegex(jev_shadow_transport.JevResponseError, "mapping"):
                    send({"model": "jev-latest", "state": "safe", "questions": {}})

    def test_decider_rejects_non_finite_or_incomplete_choice_metadata(self):
        plan = {
            "system_id": "bounded",
            "surface": {
                "surface_id": "route",
                "question_id": "route_v1",
                "fallback_action_id": "review",
                "actions": [
                    {"action_id": "accept", "label": "Accept", "preconditions": []},
                    {"action_id": "review", "label": "Review", "preconditions": []},
                ],
            },
        }
        malformed = {
            "model": "jev-test",
            "answers": {
                "route_v1": {
                    "choice": "accept",
                    "confidence": float("nan"),
                    "probabilities": {"accept": 1.0},
                }
            },
        }
        decider = jev_shadow_transport.TypeSafeShadowDecider(
            plan, transport=lambda _payload: malformed, model="jev-test"
        )
        with self.assertRaisesRegex(jev_shadow_transport.JevResponseError, "confidence"):
            decider("What next?", {"ready": True}, ["accept", "review"])


class ShadowRunTests(unittest.TestCase):
    def test_shadow_run_offers_only_legal_actions_and_persists_sanitized_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project, actions = _ready_project(workspace)
            observations = workspace / "observations.jsonl"
            raw_observation = {
                "inactive_days": 8,
                "days_since_stale": 0,
                "closed": False,
                "customer_note": "synthetic note that must not be persisted",
            }
            observations.write_text(json.dumps(raw_observation) + "\n", encoding="utf-8")
            payloads = []

            def transport(payload):
                payloads.append(payload)
                question_id = next(iter(payload["questions"]))
                legal = list(payload["questions"][question_id]["criteria"])
                self.assertEqual(legal, [actions["mark_stale"], actions["skip_stale"]])
                return {
                    "model": "jev-test",
                    "answers": {
                        question_id: {
                            "type": "choice",
                            "choice": actions["mark_stale"],
                            "confidence": 0.9,
                            "probabilities": {
                                actions["mark_stale"]: 0.95,
                                actions["skip_stale"]: 0.05,
                            },
                        }
                    },
                    "usage": {"input_tokens": 19, "output_tokens": 4},
                }

            result = jev_shadow_transport.run_controller_shadow(
                project,
                observations=observations,
                transport=transport,
                model="jev-test",
                run_id="unit-shadow-001",
            )

            self.assertEqual(len(payloads), 1)
            self.assertEqual(result["stop_reason"], "observation_exhausted")
            self.assertEqual(result["binding_invocations"], 0)
            self.assertFalse(result["production_authority"])
            records_path = project / result["artifacts"]["records"]
            manifest_path = project / result["artifacts"]["manifest"]
            records_text = records_path.read_text(encoding="utf-8")
            self.assertNotIn("synthetic note", records_text)
            self.assertNotIn("customer_note", records_text)
            record = json.loads(records_text)
            self.assertEqual(record["model"], "jev-test")
            self.assertEqual(record["probabilities"][actions["mark_stale"]], 0.95)
            self.assertFalse(record["binding_invoked"])
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["safety"]["binding_invocations"], 0)
            self.assertFalse(manifest["safety"]["executes_actions"])
            self.assertEqual(manifest["source"]["observations_sha256"], jev_shadow_transport.sha256_file(observations))
            self.assertEqual(forge.load_project(project)["stage"], "shadow_controller_ready")

    def test_single_legal_action_is_selected_without_calling_jev(self):
        plan = {
            "schema_version": "1.0",
            "system_id": "single",
            "stage": "shadow_controller_ready",
            "states": [{"state_id": "active", "label": "Active", "predicate": "ready == true", "terminal": False}],
            "surface": {
                "surface_id": "single_surface",
                "question_id": "single_v1",
                "question": "What next?",
                "active_state_ids": ["active"],
                "candidate_action_ids": ["continue"],
                "fallback_action_id": "continue",
                "min_confidence": 0.8,
                "threshold_status": "provisional_for_shadow",
                "actions": [{"action_id": "continue", "label": "Continue", "allowed_state_ids": ["active"], "preconditions": [], "evidence": {}}],
            },
            "loop": {"max_iterations": 1, "stop_conditions": sorted(shadow_controller.REQUIRED_STOP_CONDITIONS)},
            "safety": {"calls_jev_during_compilation": False, "invokes_bindings": False, "executes_actions": False, "production_authority": False},
        }
        plan["plan_digest"] = shadow_controller._mapping_digest(plan)
        calls = []
        decider = jev_shadow_transport.TypeSafeShadowDecider(plan, transport=lambda payload: calls.append(payload))
        answer = decider("What next?", {"ready": True}, ["continue"])
        self.assertEqual(calls, [])
        self.assertEqual(answer["answer"], "continue")
        self.assertEqual(answer["model"], "deterministic-single-legal-action")


class CliTests(unittest.TestCase):
    def test_run_controller_shadow_cli_uses_typesafe_transport_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project, actions = _ready_project(workspace)
            observations = workspace / "observations.jsonl"
            observations.write_text(
                json.dumps({"inactive_days": 8, "days_since_stale": 0, "closed": False}) + "\n",
                encoding="utf-8",
            )

            def fake_transport(payload):
                qid = next(iter(payload["questions"]))
                return {
                    "model": "jev-cli-test",
                    "answers": {qid: {"choice": actions["skip_stale"], "confidence": 0.91, "probabilities": {
                        actions["mark_stale"]: 0.09, actions["skip_stale"]: 0.91,
                    }}},
                    "usage": {},
                }

            with mock.patch("jev_shadow_transport.typesafe_transport", return_value=fake_transport):
                exit_code = forge.main([
                    "run-controller-shadow",
                    str(project),
                    "--observations",
                    str(observations),
                    "--run-id",
                    "cli-shadow-001",
                ])
            self.assertEqual(exit_code, 0)
            self.assertTrue((project / "integration" / "shadow_runs" / "cli-shadow-001.yaml").is_file())

    def test_cli_fails_closed_when_key_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project, _actions = _ready_project(workspace)
            observations = workspace / "observations.jsonl"
            observations.write_text(
                json.dumps({"inactive_days": 8, "days_since_stale": 0, "closed": False}) + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                exit_code = forge.main([
                    "run-controller-shadow", str(project), "--observations", str(observations)
                ])
            self.assertEqual(exit_code, 2)


class DocumentationTests(unittest.TestCase):
    def test_user_facing_docs_cover_environment_shadow_and_receipts(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        reference = (ROOT / "references" / "shadow-controller.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for expected in (
            "run-controller-shadow",
            "TYPESAFE_API_KEY",
            "shadow_runs",
            "zero binding invocations",
        ):
            self.assertIn(expected, readme)
        self.assertIn("run-controller-shadow", reference)
        self.assertIn("run-controller-shadow", skill)


if __name__ == "__main__":
    unittest.main()
