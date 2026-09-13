import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from lab.engine import Config
from inferenceops.audit import audit_traces
from inferenceops.connectors import GitHubConnector, LocalOutboxNotifier
from inferenceops.experiments.budget import BudgetLedger, preflight
from inferenceops.experiments.fallback import FallbackConfig, evaluate_response_bank
from inferenceops.experiments.task import SUPPORT_INTENT_TASK
from inferenceops.storage import Repository
from inferenceops.traces import generate_demo_traces, normalize_records
from inferenceops.traces.bitext import stratified_manifests
from inferenceops.workflows import WorkflowError, WorkflowService


class TraceAndAuditTests(unittest.TestCase):
    def test_trace_validation_and_duplicate_rejection(self):
        rows = generate_demo_traces(90)
        self.assertEqual(len(normalize_records(rows)), 90)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalize_records([rows[0], rows[0]])
        broken = {**rows[0], "cost": None}
        with self.assertRaisesRegex(ValueError, "cost"):
            normalize_records([broken])

    def test_three_detector_types_are_explainable(self):
        findings = audit_traces(normalize_records(generate_demo_traces(900)))
        self.assertEqual({row["type"] for row in findings},
                         {"oversized_model", "repeated_prompt", "excessive_rag"})
        routing = next(row for row in findings if row["type"] == "oversized_model")
        self.assertTrue(routing["eligible_for_experiment"])
        self.assertTrue(routing["evidence"])
        self.assertTrue(routing["limitations"])


class FallbackAndBudgetTests(unittest.TestCase):
    @staticmethod
    def observation(output, tier):
        return {"output": output, "service_ms": 100 if tier == "strong" else 10,
                "cost": .01 if tier == "strong" else .001, "cost_known": True,
                "input_tokens": 100, "output_tokens": 10, "error": None}

    def test_task_ignores_confidence_when_scoring(self):
        expected = {"category": "ORDER", "intent": "cancel_order"}
        output = {**expected, "confidence": .91}
        self.assertTrue(SUPPORT_INTENT_TASK.score(output, expected)["correct"])
        self.assertFalse(SUPPORT_INTENT_TASK.score({**output, "intent": "change_order"}, expected)["correct"])

    def test_validation_selects_fallback_threshold_and_counts_both_calls(self):
        rows, bank = [], {}
        expected = {"category": "ORDER", "intent": "cancel_order"}
        for index, confidence in enumerate((.95, .90, .70, .60)):
            row = {"id": f"row-{index}", "text": "cancel order", "expected": expected}
            rows.append(row)
            economy_output = ({**expected, "confidence": confidence} if index < 2
                              else {"category": "ORDER", "intent": "change_order", "confidence": confidence})
            bank[row["id"]] = {"economy": self.observation(economy_output, "economy"),
                               "strong": self.observation(expected, "strong")}
        result = evaluate_response_bank(rows, rows, bank, bank, SUPPORT_INTENT_TASK,
                                        FallbackConfig(quality_min=1, latency_slo_ms=500))
        self.assertEqual(result["threshold"], .80)
        self.assertTrue(result["accepted"])
        adaptive = next(row for row in result["results"] if row["id"] == "adaptive")
        self.assertEqual(adaptive["metrics"]["fallback_rate"], .5)
        self.assertAlmostEqual(adaptive["metrics"]["total_cost"], .024)

    def test_budget_preflight_and_reservation(self):
        result = preflight(30, 20, 2000, 150,
                           {"economy": {"input_price": .2, "output_price": 1.2},
                            "strong": {"input_price": 2, "output_price": 12}}, 5)
        self.assertTrue(result["within_cap"])
        ledger = BudgetLedger(.05)
        ledger.reserve(.04)
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            ledger.reserve(.02)
        ledger.settle(.04, .03)
        self.assertAlmostEqual(ledger.remaining, .02)

    def test_paid_provider_requires_confirmation_and_fitting_budget(self):
        environment = {
            "ECONOMY_BASE_URL": "https://provider.test/v1",
            "ECONOMY_MODEL": "economy",
            "ECONOMY_INPUT_PER_MILLION": "0.2",
            "ECONOMY_OUTPUT_PER_MILLION": "1.2",
            "STRONG_BASE_URL": "https://provider.test/v1",
            "STRONG_MODEL": "strong",
            "STRONG_INPUT_PER_MILLION": "2",
            "STRONG_OUTPUT_PER_MILLION": "12",
        }
        payload = {"mode": "live", "requests": 20, "validation_requests": 20,
                   "budget_cap": .50, "budget_currency": "USD"}
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "requires confirmation"):
                Config.parse(payload)
            accepted = Config.parse({**payload, "paid_confirmation": "RUN 0.50 USD"})
            self.assertEqual(accepted.budget_cap, .50)
            with self.assertRaisesRegex(ValueError, "exceeds"):
                Config.parse({**payload, "budget_cap": .01,
                              "paid_confirmation": "RUN 0.01 USD"})


class DatasetTests(unittest.TestCase):
    def test_stratified_splits_are_deterministic_and_disjoint(self):
        rows = []
        for intent in ("cancel_order", "track_order"):
            rows.extend({"id": f"{intent}-{index}", "text": f"request {index}",
                         "expected": {"category": "ORDER", "intent": intent}}
                        for index in range(12))
        validation, test = stratified_manifests(rows, seed=7,
                                                validation_per_intent=3,
                                                test_per_intent=4)
        again = stratified_manifests(rows, seed=7, validation_per_intent=3,
                                     test_per_intent=4)
        self.assertEqual((validation, test), again)
        self.assertFalse({row["id"] for row in validation} & {row["id"] for row in test})
        self.assertEqual(len(validation), 6)
        self.assertEqual(len(test), 8)


class PersistenceAndWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(Path(self.temp.name) / "ops.sqlite3")

    def test_schema_preserves_batches_opportunities_and_events(self):
        traces = normalize_records(generate_demo_traces(90))
        batch = self.repo.add_trace_batch("demo", traces, "hash", {"synthetic": True})
        findings = self.repo.replace_opportunities(batch, audit_traces(traces))
        self.assertEqual(self.repo.summary()["trace_count"], 90)
        opportunity = next(row for row in findings if row["type"] == "oversized_model")
        service = WorkflowService(self.repo)
        workflow = service.create_for_opportunity(opportunity)
        service.begin_experiment(workflow["id"])
        done = service.finish_experiment(workflow["id"], "experiment-1", True)
        self.assertEqual(done["state"], "VERIFIED")
        self.assertEqual(len(done["events"]), 4)
        with self.assertRaises(WorkflowError):
            service.record_notification(workflow["id"], {"delivery": "staged"})

    def test_local_notification_is_explicitly_staged(self):
        result = LocalOutboxNotifier().send({"id": "wf", "experiment_id": "run"},
                                            {"endpoint": "/x", "title": "finding"})
        self.assertEqual(result["delivery"], "staged")

    def test_version_one_trace_schema_migrates_without_data_loss(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE experiments (id TEXT PRIMARY KEY, created_at TEXT, config TEXT,
              status TEXT, progress REAL, result TEXT, error TEXT);
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (1);
            CREATE TABLE trace_batches (id TEXT PRIMARY KEY, created REAL NOT NULL,
              source TEXT NOT NULL, dataset_hash TEXT NOT NULL, record_count INTEGER NOT NULL,
              metadata TEXT NOT NULL);
            CREATE TABLE traces (trace_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
              endpoint TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL,
              payload TEXT NOT NULL);
            INSERT INTO trace_batches VALUES ('batch',1.0,'demo','hash',1,'{}');
            INSERT INTO traces VALUES ('trace','batch','/x','strong',1.0,
              '{"trace_id":"trace","cost":0.01,"currency":"USD"}');
        """)
        connection.commit()
        connection.close()
        migrated = Repository(path)
        self.assertEqual(migrated.list_batches()[0]["record_count"], 1)
        check = sqlite3.connect(path)
        try:
            self.assertEqual(check.execute("SELECT version FROM schema_version").fetchone()[0], 2)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM traces").fetchone()[0], 1)
        finally:
            check.close()


class GitHubConnectorTests(unittest.TestCase):
    def test_preview_and_existing_pr_are_idempotent(self):
        calls = []
        def runner(arguments):
            calls.append(arguments)
            if arguments[:2] == ["pr", "list"]:
                return json.dumps([{"url": "https://github.test/pr/1", "isDraft": True,
                                    "headRefName": "inferenceops/optimization-wf1"}])
            raise AssertionError(arguments)
        connector = GitHubConnector(runner=runner)
        experiment = {"threshold": .8, "accepted": True, "config": {"mode": "simulation"},
                      "results": [{"id": "adaptive", "metrics": {"quality": .95, "p95_ms": 400,
                                   "cost_per_1k": .2, "savings": .6}}]}
        preview = connector.preview({"id": "wf1", "experiment_id": "run1"},
                                    {"endpoint": "/support/classify"}, experiment)
        self.assertEqual(preview["files"], ["config/inference-policy.json", "evidence/wf1.json"])
        created = connector.create_draft(preview)
        self.assertTrue(created["idempotent"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
