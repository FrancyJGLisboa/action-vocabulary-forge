import importlib.util
from pathlib import Path
import unittest
import math
import os
import tempfile
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO


PATH = Path(__file__).parents[1] / "scripts" / "scan_llm_opportunities.py"
spec = importlib.util.spec_from_file_location("scanner", PATH)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class ScannerTriageTests(unittest.TestCase):
    def candidates(self):
        return [
            {"candidate_id": "b", "source_locator": "x.py:2", "review_status": "candidate"},
            {"candidate_id": "a", "source_locator": "x.py:1", "review_status": "candidate"},
        ]

    def response(self, score="shadow_ready"):
        return {"model": "jev-latest", "answers": {"decision_shape": {"type": "choice", "choice": "bounded_semantic_decision", "probabilities": {"bounded_semantic_decision": 1, "generative_task": 0, "deterministic_rule": 0, "insufficient_evidence": 0}, "confidence": 1}, "stable_answer_set": {"type": "noul", "noul": 0.9},
                "semantic_interpretation_required": {"type": "noul", "noul": 0.8}, "replacement_readiness": {"type": "score", "score": 2 if score == "shadow_ready" else 1, "legend": {"0": "open-ended or insufficiently evidenced; not suitable", "1": "plausible bounded decision but needs review", "2": "clear finite decision suitable for shadow evaluation"}, "probabilities": {"0": 0, "1": 0, "2": 1}, "confidence": 1}},
                "usage": {"input_tokens": 2}}

    def test_payload_and_ranking_are_deterministic(self):
        payloads = []
        def transport(payload):
            payloads.append(payload)
            return self.response("investigate")
        result = scanner.triage_candidates(self.candidates(), ["secret excerpt", "other"], transport)
        self.assertEqual([c["candidate_id"] for c in result], ["a", "b"])
        self.assertEqual(result[0]["jev_triage"]["rank"], 1)
        self.assertEqual(payloads[0]["state"]["source_window"], "secret excerpt")
        self.assertIsInstance(payloads[0]["questions"], dict)
        self.assertEqual(payloads[0]["questions"]["decision_shape"]["type"], "choice")
        self.assertEqual(payloads[0]["questions"]["stable_answer_set"]["type"], "noul")
        self.assertEqual(len(payloads[0]["questions"]["replacement_readiness"]["criteria"]), 3)
        self.assertEqual(result[0]["review_status"], "candidate")

    def test_malformed_and_illegal_responses_rejected(self):
        with self.assertRaises(ValueError):
            scanner.parse_jev_response({"answers": {"decision_shape": {"choice": "nope"}, "stable_answer_set": {"noul": 1}, "semantic_interpretation_required": {"noul": 0}, "replacement_readiness": {"score": 0}}})
        with self.assertRaises(ValueError):
            scanner.parse_jev_response({"answers": {"decision_shape": {"choice": "generative_task", "probabilities": {"generative_task": 1}, "confidence": 1}, "stable_answer_set": {"noul": 2}, "semantic_interpretation_required": {"noul": 0}, "replacement_readiness": {"score": 0, "legend": {"0": "poor_fit", "1": "investigate", "2": "shadow_ready"}, "probabilities": {"0": 1, "1": 0, "2": 0}, "confidence": 1}}})

    def test_default_scan_has_no_triage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("from openai import OpenAI\nresult = client.responses.create(model='example')\n")
            result = scanner.scan_file(path, path.parent)
        self.assertEqual(len(result), 1)
        self.assertNotIn("jev_triage", result[0])

    def test_valid_official_response_parses_and_scores(self):
        parsed = scanner.parse_jev_response(self.response())
        self.assertEqual(parsed["decision_shape"], "bounded_semantic_decision")
        self.assertEqual(parsed["replacement_readiness"], "shadow_ready")
        self.assertEqual(parsed["priority_score"], 0.93)

    def test_payload_has_mapping_rubrics_and_privacy_safe_heuristics(self):
        seen = []
        c = self.candidates()[:1]
        c[0].update(observed_options=["yes"], bounded_output=True, semantic_interpretation_required=True, replacement_strength="strong")
        scanner.triage_candidates(c, ["PRIVATE WINDOW"], lambda p: seen.append(p) or self.response())
        payload = seen[0]
        self.assertIsInstance(payload["questions"], dict)
        self.assertEqual(payload["questions"]["decision_shape"]["type"], "choice")
        self.assertEqual(payload["questions"]["replacement_readiness"]["type"], "score")
        self.assertEqual(payload["state"]["heuristics"]["replacement_strength"], "strong")
        self.assertNotIn("TYPESAFE_API_KEY", str(payload))

    def test_equal_scores_rank_by_candidate_id(self):
        result = scanner.triage_candidates(self.candidates(), ["a", "b"], lambda _: self.response("investigate"))
        self.assertEqual([x["candidate_id"] for x in result], ["a", "b"])

    def test_response_model_is_used_and_requested_model_is_fallback(self):
        c = self.candidates()[:1]
        self.assertEqual(scanner.triage_candidates(c, ["x"], lambda _: self.response())[0]["jev_triage"]["model"], "jev-latest")
        c = self.candidates()[:1]
        raw = self.response(); raw.pop("model")
        self.assertEqual(scanner.triage_candidates(c, ["x"], lambda _: raw, model="custom")[0]["jev_triage"]["model"], "custom")

    def test_review_status_cannot_be_promoted(self):
        c = self.candidates()[:1]; c[0]["review_status"] = "promoted"
        self.assertEqual(scanner.triage_candidates(c, ["x"], lambda _: self.response())[0]["review_status"], "candidate")

    def test_illegal_choice_and_wrong_answer_types_rejected(self):
        raw = self.response(); raw["answers"]["decision_shape"]["choice"] = "bad"
        with self.assertRaises(ValueError): scanner.parse_jev_response(raw)
        raw = self.response(); raw["answers"]["stable_answer_set"]["type"] = "score"
        with self.assertRaises(ValueError): scanner.parse_jev_response(raw)

    def test_noul_nan_inf_and_out_of_range_rejected(self):
        for value in (math.nan, math.inf, -1, 2):
            raw = self.response(); raw["answers"]["stable_answer_set"]["noul"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): scanner.parse_jev_response(raw)

    def test_score_nan_inf_out_of_range_bad_legend_rejected(self):
        for value in (math.nan, math.inf, -1, 3):
            raw = self.response(); raw["answers"]["replacement_readiness"]["score"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): scanner.parse_jev_response(raw)
        raw = self.response(); raw["answers"]["replacement_readiness"]["legend"]["0"] = "wrong"
        with self.assertRaises(ValueError): scanner.parse_jev_response(raw)

    def test_incomplete_and_non_summing_choice_probabilities_rejected(self):
        for probs in ({"bounded_semantic_decision": 1}, {x: .2 for x in scanner.DECISION_SHAPES}):
            raw = self.response(); raw["answers"]["decision_shape"]["probabilities"] = probs
            with self.assertRaises(ValueError): scanner.parse_jev_response(raw)

    def test_incomplete_and_non_summing_score_probabilities_rejected(self):
        for probs in ({"0": 1}, {"0": .2, "1": .2, "2": .2}):
            raw = self.response(); raw["answers"]["replacement_readiness"]["probabilities"] = probs
            with self.assertRaises(ValueError): scanner.parse_jev_response(raw)

    def test_serialized_output_excludes_source_windows_and_api_key(self):
        c = self.candidates()[:1]; result = scanner.triage_candidates(c, ["SECRET WINDOW"], lambda _: self.response())
        import yaml
        output = yaml.safe_dump({"surface_candidates": result})
        self.assertNotIn("SECRET WINDOW", output); self.assertNotIn("TYPESAFE_API_KEY", output)

    def test_default_scan_is_offline_and_has_no_triage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("result = llm.invoke('classify this')\n")
            with patch.object(scanner, "_http_transport", side_effect=AssertionError("network")):
                self.assertNotIn("jev_triage", scanner.scan_file(path, path.parent)[0])

    def test_cli_opt_in_without_key_exits_before_http_transport(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(scanner, "_http_transport", side_effect=AssertionError("network")), patch("sys.argv", ["scan", str(PATH), "--jev-triage"]):
            with self.assertRaises(SystemExit): scanner.main()


class ScannerCallSiteTests(unittest.TestCase):
    def scan_text(self, text, suffix=".py"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"sample{suffix}"
            path.write_text(text)
            return scanner._scan_file(path, path.parent)

    def test_python_scan_keeps_only_executable_model_calls(self):
        scanned = self.scan_text(
            "DEFAULT_ENDPOINT = 'https://api.typesafe.ai/v1/systemone'\n"
            "LLM_PATTERN = re.compile(r'responses[.]create')\n"
            "# client.responses.create(model='comment')\n"
            "documentation = \"client.responses.create(model='string')\"\n"
            "PROVIDERS = {'typesafe', 'anthropic'}\n"
            "result = client.responses.create(model='example')\n"
            "answer = llm.invoke('classify this')\n"
        )
        self.assertEqual(
            [candidate["source_locator"] for candidate, _ in scanned],
            ["sample.py:6", "sample.py:7"],
        )

    def test_javascript_scan_ignores_comments_and_strings(self):
        scanned = self.scan_text(
            "import OpenAI from 'openai';\n"
            "const endpoint = 'https://api.openai.com/v1';\n"
            "// await generateText({ prompt: 'comment' });\n"
            "const documentation = 'client.chat.completions.create()';\n"
            "const result = await generateText({ prompt });\n"
            "const other = client.chat.completions.create({ messages });\n",
            suffix=".js",
        )
        self.assertEqual(
            [candidate["source_locator"] for candidate, _ in scanned],
            ["sample.js:5", "sample.js:6"],
        )

    def test_non_executable_material_is_not_scanned_for_calls(self):
        self.assertEqual(
            self.scan_text("Use `client.responses.create()` here.\n", suffix=".md"),
            [],
        )

    def test_only_filtered_call_sites_are_sent_to_jev(self):
        scanned = self.scan_text(
            "from openai import OpenAI\n"
            "DEFAULT_ENDPOINT = 'https://api.openai.com/v1'\n"
            "# client.responses.create(model='comment')\n"
            "result = client.responses.create(model='example')\n"
        )
        candidates = [candidate for candidate, _ in scanned]
        windows = [window for _, window in scanned]
        payloads = []
        response = ScannerTriageTests().response()
        scanner.triage_candidates(candidates, windows, lambda payload: payloads.append(payload) or response)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["state"]["source_locator"], "sample.py:4")

    def test_raw_http_transport_requires_provider_context(self):
        with_provider = self.scan_text(
            "ENDPOINT = 'https://api.openai.com/v1/responses'\n"
            "request = urllib.request.Request(ENDPOINT, data=payload)\n"
            "response = urllib.request.urlopen(request)\n"
        )
        without_provider = self.scan_text(
            "request = urllib.request.Request('https://example.com', data=payload)\n"
            "response = urllib.request.urlopen(request)\n"
        )
        self.assertEqual(
            [candidate["source_locator"] for candidate, _ in with_provider],
            ["sample.py:3"],
        )
        self.assertEqual(without_provider, [])


if __name__ == "__main__":
    unittest.main()
