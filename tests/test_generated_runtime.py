import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import EXAMPLE, GOOD_STATE, ROOT, choice_response, clone, import_bundle, load_example  # noqa: E402

import init_action_bundle  # noqa: E402

Q = "next_validation_action"


class ModelAndEndpointTests(unittest.TestCase):
    def test_spec_model_and_endpoint_are_carried(self):
        bundle = load_example()
        bundle["model"] = "jev-1.13.0"
        bundle["endpoint"] = "https://example.invalid/systemone"
        module = import_bundle(bundle)
        self.assertEqual(module.MODEL, "jev-1.13.0")
        self.assertEqual(module.DEFAULT_ENDPOINT, "https://example.invalid/systemone")
        self.assertEqual(module.build_payload({"x": 1})["model"], "jev-1.13.0")

    def test_defaults_when_spec_is_silent(self):
        bundle = load_example()
        self.assertEqual(bundle["model"], "jev-latest")
        self.assertTrue(bundle["endpoint"].startswith("https://"))


class PreconditionTests(unittest.TestCase):
    def setUp(self):
        self.module = import_bundle(load_example())

    def test_precondition_failure_blocks(self):
        m = self.module
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with self.assertRaises(m.ExecutionBlocked) as ctx:
            m.execute(decision, {"state_id": "validation_failed", "retry_budget": 0, "record_exists": True}, handlers={"retry": lambda s: "x"})
        self.assertIn("preconditions failed", str(ctx.exception))
        self.assertEqual(m.check_preconditions("retry", {"retry_budget": 0, "record_exists": True}), ["retry_budget > 0"])

    def test_preconditions_can_be_skipped_explicitly(self):
        m = self.module
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        result = m.execute(decision, {"state_id": "validation_failed"}, handlers={"retry": lambda s: "x"}, check_preconditions=False)
        self.assertEqual(result, "x")

    def test_infer_state_and_legal_actions(self):
        m = self.module
        self.assertEqual(m.infer_state({"validation_status": "failed"}), "validation_failed")
        self.assertIsNone(m.infer_state({}))
        self.assertEqual(m.legal_actions({"validation_status": "failed", "retry_budget": 1, "record_exists": True, "unresolved_failure": False}), ["retry"])
        self.assertEqual(m.legal_actions({"state_id": "awaiting_human_review", "retry_budget": 1, "record_exists": True}), [])


class DecisionLogTests(unittest.TestCase):
    def test_ok_and_blocked_paths_each_write_one_record(self):
        m = import_bundle(load_example())
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with tempfile.TemporaryDirectory() as directory:
            log = m.DecisionLog(Path(directory) / "log.jsonl")
            m.execute(decision, GOOD_STATE, handlers={"retry": lambda s: {"run": 1}}, log=log, case_id="c1")
            with self.assertRaises(m.ExecutionBlocked):
                m.execute(decision, {**GOOD_STATE, "retry_budget": 0}, handlers={"retry": lambda s: 1}, log=log, case_id="c2")
            lines = [json.loads(line) for line in Path(log.path).read_text().splitlines()]
        self.assertEqual(len(lines), 2)
        ok, blocked = lines
        for record in lines:
            for field in (
                "timestamp", "system_id", "question_id", "surface_id", "case_id", "state_id", "selected_choice",
                "proposed_action_id", "action_id", "confidence", "probabilities", "threshold", "abstained", "reason",
                "legal_actions", "executed", "outcome", "blocked_reason", "result_summary", "handler_status", "model",
                "latency_ms", "usage", "ground_truth_action_id", "label_source",
            ):
                self.assertIn(field, record)
        self.assertEqual((ok["case_id"], ok["executed"], ok["outcome"]), ("c1", True, "ok"))
        self.assertEqual(ok["result_summary"], "{'run': 1}")
        self.assertEqual(ok["legal_actions"], ["retry", "human_review"])
        self.assertEqual((blocked["executed"], blocked["outcome"]), (False, "blocked"))
        self.assertIn("preconditions failed", blocked["blocked_reason"])
        self.assertEqual(ok["model"], "jev-test")
        self.assertEqual(ok["usage"], {"input_tokens": 10, "output_tokens": 1})

    def test_default_log_only_when_env_is_set(self):
        m = import_bundle(load_example())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_DECISION_LOG", None)
            self.assertIsNone(m.default_log())
            os.environ["FORGE_DECISION_LOG"] = "/tmp/x.jsonl"
            self.assertEqual(m.default_log().path, "/tmp/x.jsonl")


class PolicyTests(unittest.TestCase):
    def decision(self, module, confidence, choice="retry"):
        return module.parse_response(choice_response(Q, choice, confidence), Q)

    def test_below_threshold_routes_to_fallback_executor(self):
        m = import_bundle(load_example())  # policy.min_confidence 0.85
        routed = m.apply_policy(self.decision(m, 0.5))
        self.assertTrue(routed.abstained)
        self.assertEqual(routed.action_id, "human_review")
        self.assertEqual(routed.proposed_action_id, "retry")
        self.assertEqual(routed.threshold, 0.85)
        self.assertEqual(routed.reason, "confidence_below_threshold:0.85")
        kept = m.apply_policy(self.decision(m, 0.9))
        self.assertFalse(kept.abstained)
        self.assertEqual(kept.threshold, 0.85)

    def test_precedence_action_over_question_over_global(self):
        bundle = load_example()
        bundle["policy"] = {"min_confidence": 0.5, "questions": {Q: {"min_confidence": 0.7, "actions": {"retry": 0.9}}}}
        m = import_bundle(bundle)
        self.assertEqual(m.threshold_for(Q, "retry"), 0.9)
        self.assertEqual(m.threshold_for(Q, "human_review"), 0.0)  # fallback never gated
        bundle["policy"] = {"min_confidence": 0.5, "questions": {Q: {"min_confidence": 0.7}}}
        self.assertEqual(import_bundle(bundle).threshold_for(Q, "retry"), 0.7)
        bundle["policy"] = {"min_confidence": 0.5}
        self.assertEqual(import_bundle(bundle).threshold_for(Q, "retry"), 0.5)

    def test_empty_policy_abstains_until_calibrated(self):
        bundle = load_example()
        bundle["policy"] = {}
        m = import_bundle(bundle)
        routed = m.apply_policy(self.decision(m, 0.99))
        self.assertTrue(routed.abstained)
        self.assertEqual(routed.reason, "uncalibrated:abstain")
        self.assertIsNone(routed.threshold)
        bundle["policy"] = {"default_when_uncalibrated": "allow"}
        self.assertFalse(import_bundle(bundle).apply_policy(self.decision(m, 0.1)).abstained)

    def test_null_confidence_counts_as_zero(self):
        m = import_bundle(load_example())
        decision = m.parse_response({"answers": {Q: {"choice": "retry"}}}, Q)
        self.assertTrue(m.apply_policy(decision).abstained)

    def test_a_noul_no_answer_is_gated_when_an_abstention_is_declared(self):
        """"no" is a conclusion; only the declared abstention escapes the threshold."""
        bundle = load_example()
        bundle["policy"] = {"min_confidence": 0.9}
        bundle["questions"] = {
            "is_transient": {"question_id": "is_transient", "surface_id": "resolve_validation_failure", "type": "noul",
                             "instruction": "?", "yes_action_id": "retry", "no_action_id": "human_review",
                             "abstention_action_id": "human_review"},
        }
        m = import_bundle(bundle)
        # with an abstention declared, "no" is not a free fallback...
        self.assertEqual(m.question_fallbacks("is_transient"), {"human_review"})
        bundle["questions"]["is_transient"].pop("abstention_action_id")
        bundle["questions"]["is_transient"]["no_action_id"] = "human_review"
        m2 = import_bundle(bundle)
        self.assertEqual(m2.question_fallbacks("is_transient"), {"human_review"})

    def test_a_noul_no_answer_is_thresholded_against_a_distinct_abstention(self):
        bundle = load_example()
        bundle["policy"] = {"min_confidence": 0.9}
        bundle["actions"]["defer"] = {**bundle["actions"]["human_review"], "action_id": "defer"}
        bundle["handler_status"]["defer"] = bundle["handler_status"]["human_review"]
        bundle["questions"] = {
            "is_transient": {"question_id": "is_transient", "surface_id": "resolve_validation_failure", "type": "noul",
                             "instruction": "?", "yes_action_id": "retry", "no_action_id": "human_review",
                             "abstention_action_id": "defer"},
        }
        m = import_bundle(bundle)
        self.assertEqual(m.question_fallbacks("is_transient"), {"defer"})
        self.assertEqual(m.threshold_for("is_transient", "human_review"), 0.9)   # gated
        self.assertEqual(m.threshold_for("is_transient", "defer"), 0.0)          # the abstention is free
        low = m.apply_policy(m.parse_response({"answers": {"is_transient": {"noul": 0.45}}}, "is_transient"))
        self.assertEqual((low.action_id, low.abstained), ("defer", True))
        high = m.apply_policy(m.parse_response({"answers": {"is_transient": {"noul": 0.02}}}, "is_transient"))
        self.assertEqual((high.action_id, high.abstained), ("human_review", False))

    def test_noul_and_score_fallbacks(self):
        bundle = load_example()
        bundle["policy"] = {"min_confidence": 0.95}
        bundle["questions"] = {
            "is_transient": {"question_id": "is_transient", "surface_id": "resolve_validation_failure", "type": "noul",
                             "instruction": "?", "yes_action_id": "retry", "no_action_id": "human_review"},
            "urgency": {"question_id": "urgency", "surface_id": "resolve_validation_failure", "type": "score",
                        "instruction": "?", "abstention_action_id": "human_review",
                        "levels": [{"id": "low", "criterion": "a", "executor_action_id": "human_review"},
                                   {"id": "high", "criterion": "b", "executor_action_id": "retry"}]},
            "no_fallback": {"question_id": "no_fallback", "surface_id": "resolve_validation_failure", "type": "score",
                            "instruction": "?", "levels": [{"id": "l", "criterion": "a", "executor_action_id": "retry"},
                                                           {"id": "h", "criterion": "b", "executor_action_id": "retry"}]},
        }
        m = import_bundle(bundle)
        noul = m.apply_policy(m.parse_response({"answers": {"is_transient": {"noul": 0.6}}}, "is_transient"))
        self.assertEqual((noul.abstained, noul.action_id), (True, "human_review"))
        score = m.apply_policy(m.parse_response({"answers": {"urgency": {"score": 1.0, "confidence": 0.5}}}, "urgency"))
        self.assertEqual((score.abstained, score.action_id, score.selected_choice), (True, "human_review", "high"))
        with self.assertRaises(m.ExecutionBlocked):
            m.apply_policy(m.parse_response({"answers": {"no_fallback": {"score": 1.0, "confidence": 0.5}}}, "no_fallback"))


class HandlerTests(unittest.TestCase):
    def test_handler_status_from_example(self):
        m = import_bundle(load_example())
        self.assertEqual(m.HANDLER_STATUS, {"retry": "generated:python_callable", "human_review": "stub:unobserved_binding"})
        self.assertEqual(set(m.HANDLERS), {"retry", "human_review"})

    def test_python_callable_handler_calls_target(self):
        m = import_bundle(load_example())
        calls = []
        sys.modules["validation_client"] = types.SimpleNamespace(retry=lambda record_id: (calls.append(record_id), f"started:{record_id}")[1])
        try:
            decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
            self.assertEqual(m.execute(decision, GOOD_STATE), "started:r-1")
            self.assertEqual(calls, ["r-1"])
            with self.assertRaises(m.ExecutionBlocked):
                m.execute(decision, {k: v for k, v in GOOD_STATE.items() if k != "record_id"})
        finally:
            del sys.modules["validation_client"]

    def test_python_callable_exception_becomes_handler_error_and_logs_error(self):
        m = import_bundle(load_example())
        sys.modules["validation_client"] = types.SimpleNamespace(retry=lambda record_id: 1 / 0)
        try:
            decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
            with tempfile.TemporaryDirectory() as directory:
                log = m.DecisionLog(Path(directory) / "log.jsonl")
                with self.assertRaises(m.HandlerError):
                    m.execute(decision, GOOD_STATE, log=log)
                record = json.loads(Path(log.path).read_text().splitlines()[0])
            self.assertEqual(record["outcome"], "error")
        finally:
            del sys.modules["validation_client"]

    def test_stub_names_missing_evidence(self):
        m = import_bundle(load_example())
        decision = m.parse_response(choice_response(Q, "human_review", 0.9), Q)
        with self.assertRaises(m.HandlerUnavailable) as ctx:
            m.execute(decision, GOOD_STATE)
        self.assertIn("binding_review_cli", str(ctx.exception))
        self.assertIn("stub:unobserved_binding", str(ctx.exception))
        m.HANDLERS["human_review"] = lambda state: "ticket-1"
        self.assertEqual(m.execute(decision, GOOD_STATE), "ticket-1")

    def observed(self, bundle, action_id, binding):
        bundle["actions"][action_id]["binding"] = binding
        bundle["handler_status"][action_id] = f"generated:{binding['kind']}"
        return bundle

    def test_http_handler(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "http", "method": "POST", "locator": "https://api.example.invalid/records/{record_id}/retry",
            "body": {"reason": "decision.reason", "id": "record_id"}, "headers": {"X-Trace": "1"},
            "auth_env": "EXAMPLE_TOKEN", "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXAMPLE_TOKEN", None)
            with self.assertRaises(m.ExecutionBlocked):
                m.execute(decision, GOOD_STATE)
            os.environ["EXAMPLE_TOKEN"] = "secret"
            response = mock.MagicMock()
            response.read.return_value = b'{"ok": true}'
            response.__enter__.return_value = response
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                self.assertEqual(m.execute(decision, GOOD_STATE), {"ok": True})
            request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.invalid/records/r-1/retry")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("X-trace"), "1")
        self.assertEqual(json.loads(request.data), {"reason": None, "id": "r-1"})

    def test_http_error_is_handler_error(self):
        import urllib.error
        bundle = self.observed(load_example(), "retry", {
            "kind": "http", "method": "POST", "locator": "https://api.example.invalid/retry", "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 500, "boom", {}, None)):
            with self.assertRaises(m.HandlerError):
                m.execute(decision, GOOD_STATE)

    def test_cli_handler(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "cli", "locator": sys.executable,
            "argv": [sys.executable, "-c", "import sys; print('retry', sys.argv[1])", "{record_id}"],
            "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        result = m.execute(decision, GOOD_STATE)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"].strip(), "retry r-1")
        bundle["actions"]["retry"]["binding"]["argv"] = [sys.executable, "-c", "raise SystemExit(3)"]
        failing = import_bundle(bundle)
        with self.assertRaises(failing.HandlerError):
            failing.execute(failing.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)

    def test_ui_handler_with_injected_page(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "ui", "operation": "fill", "url": "https://app.example.invalid/records/{record_id}",
            "locator": "#retry-reason", "arg_mapping": {"record_id": "record_id", "value": "note"},
            "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        self.assertEqual(m.HANDLER_STATUS["retry"], "generated:ui")
        calls = []

        class FakeLocator:
            def __init__(self, selector): self.selector = selector
            def fill(self, value, timeout=None): calls.append(("fill", self.selector, value))
            def click(self, timeout=None): calls.append(("click", self.selector))
            def inner_text(self, timeout=None): return "hello"

        class FakePage:
            url = None
            def goto(self, url, timeout=None): calls.append(("goto", url)); self.url = url
            def locator(self, selector): return FakeLocator(selector)

        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        decision = m.apply_policy(decision)
        m.set_ui_page(FakePage())
        try:
            result = m.execute(decision, {**GOOD_STATE, "note": "transient"})
        finally:
            m.set_ui_page(None)
        self.assertEqual(calls, [("goto", "https://app.example.invalid/records/r-1"), ("fill", "#retry-reason", "transient")])
        self.assertEqual(result["operation"], "fill")

        bundle["actions"]["retry"]["binding"].update({"operation": "read", "arg_mapping": {"record_id": "record_id"}})
        m2 = import_bundle(bundle)
        m2.set_ui_page(FakePage())
        try:
            self.assertEqual(m2.execute(m2.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)["text"], "hello")
        finally:
            m2.set_ui_page(None)

    def test_ui_handler_without_page_or_playwright_is_blocked(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "ui", "operation": "click", "locator": "text=Retry", "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with self.assertRaises(m.ExecutionBlocked) as ctx:
            m.execute(decision, GOOD_STATE)  # no page injected and no url to open
        self.assertIn("set_ui_page", str(ctx.exception))
        bundle["actions"]["retry"]["binding"]["url"] = "https://app.example.invalid/"
        m2 = import_bundle(bundle)
        with mock.patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            with self.assertRaises(m2.ExecutionBlocked) as ctx:
                m2.execute(m2.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)
        self.assertIn("playwright is not installed", str(ctx.exception))

    def test_ui_handler_launches_playwright_when_no_page_injected(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "ui", "operation": "click", "url": "https://app.example.invalid/", "locator": "text=Retry",
            "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        events = []
        page = mock.MagicMock(); page.url = "https://app.example.invalid/"
        page.locator.return_value.click.side_effect = lambda timeout=None: events.append("click")
        browser = mock.MagicMock(); browser.new_page.return_value = page
        pw = mock.MagicMock(); pw.chromium.launch.return_value = browser
        cm = mock.MagicMock(); cm.__enter__.return_value = pw
        fake_api = types.SimpleNamespace(sync_playwright=lambda: cm)
        with mock.patch.dict(sys.modules, {"playwright": types.SimpleNamespace(sync_api=fake_api), "playwright.sync_api": fake_api}):
            result = m.execute(m.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)
        self.assertEqual(events, ["click"])
        self.assertEqual(result["selector"], "text=Retry")
        browser.close.assert_called_once()

    def test_mcp_handler_with_injected_caller(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "mcp", "locator": "validation/retry", "server": "validation", "tool": "retry",
            "transport": "stdio", "command": "validation-mcp", "arg_mapping": {"record_id": "record_id"},
            "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        self.assertEqual(m.HANDLER_STATUS["retry"], "generated:mcp")
        seen = []
        m.set_mcp_caller(lambda server, tool, args, binding: seen.append((server, tool, dict(args))) or {"ok": True})
        try:
            result = m.execute(m.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)
        finally:
            m.set_mcp_caller(None)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen, [("validation", "retry", {"record_id": "r-1"})])
        m.set_mcp_caller(lambda *a: 1 / 0)
        try:
            with self.assertRaises(m.HandlerError):
                m.execute(m.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)
        finally:
            m.set_mcp_caller(None)

    def test_mcp_handler_without_sdk_is_blocked_and_auth_env_checked(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "mcp", "locator": "validation/retry", "server": "validation", "tool": "retry",
            "transport": "http", "url": "https://mcp.example.invalid/mcp", "auth_env": "MCP_TOKEN",
            "arg_mapping": {"record_id": "record_id"}, "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        decision = m.parse_response(choice_response(Q, "retry", 0.96), Q)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_TOKEN", None)
            with self.assertRaises(m.ExecutionBlocked) as ctx:
                m.execute(decision, GOOD_STATE)
            self.assertIn("MCP_TOKEN", str(ctx.exception))
            os.environ["MCP_TOKEN"] = "t"
            with mock.patch.dict(sys.modules, {"mcp": None}):
                with self.assertRaises(m.ExecutionBlocked) as ctx:
                    m.execute(decision, GOOD_STATE)
        self.assertIn("mcp package is not installed", str(ctx.exception))

    def test_mcp_handler_stdio_via_sdk(self):
        bundle = self.observed(load_example(), "retry", {
            "kind": "mcp", "locator": "validation/retry", "server": "validation", "tool": "retry",
            "transport": "stdio", "command": "validation-mcp", "args": ["--fast"],
            "arg_mapping": {"record_id": "record_id"}, "evidence_refs": ["binding_retry_call"],
        })
        m = import_bundle(bundle)
        calls = []

        class FakeSession:
            def __init__(self, read, write): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def initialize(self): calls.append("init")
            async def call_tool(self, tool, arguments):
                calls.append((tool, arguments))
                return types.SimpleNamespace(content=[types.SimpleNamespace(text="started:r-1")], isError=False)

        class FakeStdio:
            def __init__(self, params): self.params = params
            async def __aenter__(self): calls.append(("stdio", self.params.command, list(self.params.args))); return (None, None)
            async def __aexit__(self, *a): return False

        class Params:
            def __init__(self, command, args, env, cwd): self.command, self.args = command, args

        fake_mcp = types.SimpleNamespace(ClientSession=FakeSession, StdioServerParameters=Params)
        fake_stdio = types.SimpleNamespace(stdio_client=FakeStdio)
        with mock.patch.dict(sys.modules, {"mcp": fake_mcp, "mcp.client": types.SimpleNamespace(stdio=fake_stdio), "mcp.client.stdio": fake_stdio}):
            result = m.execute(m.parse_response(choice_response(Q, "retry", 0.96), Q), GOOD_STATE)
        self.assertEqual(result, "started:r-1")
        self.assertEqual(calls, [("stdio", "validation-mcp", ["--fast"]), "init", ("retry", {"record_id": "r-1"})])


class DynamicCriteriaTests(unittest.TestCase):
    def dynamic_bundle(self):
        bundle = load_example()
        question = bundle["questions"][Q]
        question["criteria_source"] = "dynamic"
        question["dynamic_executor_action_id"] = "retry"
        question["choices"] = [c for c in question["choices"] if c["id"] == "human_review"]
        return bundle

    def test_payload_and_parse_with_runtime_choices(self):
        m = import_bundle(self.dynamic_bundle())
        dynamic = {Q: {"cand_7": "Candidate seven", "cand_9": "Candidate nine"}}
        payload = m.build_payload({"row": 1}, question_id=Q, dynamic_choices=dynamic)
        self.assertEqual(set(payload["questions"][Q]["criteria"]), {"human_review", "cand_7", "cand_9"})
        decision = m.parse_response(choice_response(Q, "cand_7", 0.93), Q, dynamic_choices=dynamic)
        self.assertEqual((decision.action_id, decision.selected_choice), ("retry", "cand_7"))
        with self.assertRaises(m.IllegalChoice):
            m.parse_response(choice_response(Q, "cand_42", 0.93), Q, dynamic_choices=dynamic)
        with self.assertRaises(m.AdapterError):
            m.build_payload({}, question_id=Q, dynamic_choices={Q: {"human_review": "collides"}})
        with self.assertRaises(m.AdapterError):
            m.build_payload({}, question_id=Q)  # fewer than two criteria

    def test_static_question_rejects_dynamic_choices(self):
        m = import_bundle(load_example())
        with self.assertRaises(m.IllegalChoice):
            m.build_payload({}, question_id=Q, dynamic_choices={Q: {"x": "y"}})
        with self.assertRaises(m.IllegalChoice):
            m.parse_response(choice_response(Q, "x", 0.9), Q, dynamic_choices={Q: {"x": "y"}})

    def test_run_with_transport_logs_once(self):
        m = import_bundle(self.dynamic_bundle())
        dynamic = {Q: {"cand_7": "Candidate seven"}}
        seen = []

        def transport(payload):
            seen.append(payload)
            return choice_response(Q, "cand_7", 0.97)

        sys.modules["validation_client"] = types.SimpleNamespace(retry=lambda record_id: f"started:{record_id}")
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = m.DecisionLog(Path(directory) / "log.jsonl")
                decision, result = m.run({"row": 1}, GOOD_STATE, dynamic_choices=dynamic, case_id="c9", log=log, transport=transport)
                lines = Path(log.path).read_text().splitlines()
        finally:
            del sys.modules["validation_client"]
        self.assertEqual(result, "started:r-1")
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual((record["case_id"], record["selected_choice"], record["executed"]), ("c9", "cand_7", True))
        self.assertIsNotNone(decision.latency_ms)


class ExampleLockstepTests(unittest.TestCase):
    def test_init_example_matches_checked_in_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bundle"
            init_action_bundle.write_bundle(target, init_action_bundle.example_bundle(), force=False)
            for name in init_action_bundle.FILES:
                self.assertEqual((target / name).read_text(), (EXAMPLE / name).read_text(), name)


if __name__ == "__main__":
    unittest.main()
