import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark import load_dataset, run
from invoice import Invoice
from vision import ExtractionError

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT/"data"/"benchmark"


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_twenty_distinct_invoices_with_layout_holdout(self):
        manifest = load_dataset(DATASET)
        self.assertEqual(len(manifest["cases"]), 20)
        dev = {c["layout"] for c in manifest["cases"] if c["split"] == "dev"}
        test = {c["layout"] for c in manifest["cases"] if c["split"] == "test"}
        self.assertFalse(dev & test)

    def test_changed_image_and_leaking_layout_rejected(self):
        dataset = self.folder/"dataset"
        shutil.copytree(DATASET, dataset)
        path = dataset/"manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["cases"][1]["split"] = "test"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "leakage"):
            load_dataset(dataset)
        manifest["cases"][1]["split"] = "dev"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        (dataset/manifest["cases"][0]["image"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            load_dataset(dataset)

    def test_replay_never_calls_model_and_classifies_failures(self):
        with patch("benchmark.extract", side_effect=AssertionError("Network must not be used")):
            report = run(DATASET, "replay", "all", "ignored", self.folder/"replay.json")
        self.assertEqual(report["summary"]["status_counts"], {"success": 18, "request_error": 1, "schema_error": 1})
        self.assertEqual(report["summary"]["extra_lines"], 1)
        self.assertEqual(report["summary"]["missing_lines"], 1)
        by_id = {r["case_id"]: r for r in report["rows"]}
        self.assertTrue(by_id["INV-003"]["score"]["exact_invoice"])
        self.assertTrue(by_id["INV-003"]["score"]["line_order_changed"])
        self.assertTrue(by_id["INV-011"]["score"]["exact_invoice"])
        self.assertTrue(by_id["INV-011"]["review_issues"])
        self.assertEqual(by_id["INV-007"]["score"]["false_fills"], 1)
        self.assertTrue((self.folder/"replay.html").exists())

    def test_live_budget_resume_and_no_recall_of_successes(self):
        cases = [c for c in load_dataset(DATASET)["cases"] if c["split"] == "dev"]
        calls = []
        def fake(image, key, model):
            case = cases[len(calls)]
            calls.append(case["id"])
            return Invoice.model_validate_json((DATASET/case["reference"]).read_text(encoding="utf-8"))
        path = self.folder/"live.json"
        with patch.dict(os.environ, {"GROQ_API_KEY": "mock-key"}):
            first = run(DATASET, "live", "dev", "mock-model", path, max_requests=1, extractor=fake)
            second = run(DATASET, "live", "dev", "mock-model", path, max_requests=2, resume=True, extractor=fake)
        self.assertEqual(first["summary"]["processed_cases"], 1)
        self.assertEqual(first["summary"]["not_run"], 11)
        self.assertFalse(first["summary"]["completed"])
        self.assertEqual(second["summary"]["processed_cases"], 3)
        self.assertEqual(calls, ["INV-001", "INV-002", "INV-003"])

    def test_retry_requires_explicit_flag_and_preserves_history(self):
        path = self.folder/"retry.json"
        calls = []
        def fail(image, key, model):
            calls.append(1)
            raise ExtractionError("Simulated HTTP 429", "request_error", 429)
        sample = Invoice.model_validate_json((DATASET/"references/INV-001.json").read_text(encoding="utf-8"))
        with patch.dict(os.environ, {"GROQ_API_KEY": "mock-key"}):
            run(DATASET, "live", "dev", "mock", path, limit=1, extractor=fail)
            run(DATASET, "live", "dev", "mock", path, limit=1, resume=True, extractor=fail)
            self.assertEqual(len(calls), 1)
            report = run(DATASET, "live", "dev", "mock", path, limit=1, resume=True, retry_errors=True,
                         extractor=lambda *args: sample)
        self.assertEqual(report["rows"][0]["status"], "success")
        self.assertEqual(report["rows"][0]["attempt_history"][0]["status"], "request_error")

    def test_existing_report_and_changed_resume_selection_rejected(self):
        path = self.folder/"report.json"
        run(DATASET, "replay", "dev", "ignored", path, limit=1)
        with self.assertRaises(ValueError):
            run(DATASET, "replay", "dev", "ignored", path, limit=1)
        with self.assertRaisesRegex(ValueError, "Cannot resume"):
            run(DATASET, "replay", "test", "ignored", path, limit=1, resume=True)

    def test_rate_limit_stops_remaining_live_requests(self):
        calls = []
        def fail(image, key, model):
            calls.append(1)
            raise ExtractionError("Simulated rate limit", "request_error", 429)
        with patch.dict(os.environ, {"GROQ_API_KEY": "mock-key"}):
            report = run(DATASET, "live", "dev", "mock", self.folder/"limited.json", max_requests=5, extractor=fail)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["summary"]["not_run"], 11)

    def test_live_missing_key_does_not_write_report(self):
        path = self.folder/"missing.json"
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                run(DATASET, "live", "dev", "mock", path)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
