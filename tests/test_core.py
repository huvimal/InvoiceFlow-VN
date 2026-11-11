import copy
import csv
import io
import itertools
import json
import random
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

from invoice import Invoice, export_csv, issues
from review import ReviewQueue
from scoring import assignment, compare
from vision import ExtractionError, extract, read_image

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads((ROOT/"data/reference.json").read_text(encoding="utf-8"))


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (10, 12), color).save(output, format="PNG")
    return output.getvalue()


class SchemaAndScoringTests(unittest.TestCase):
    def test_integer_money_and_quantity_are_strict(self):
        for value in [True, "605000", 605000.0, -1]:
            with self.assertRaises(ValidationError):
                Invoice.model_validate({**SAMPLE, "total": value})

    def test_numeric_and_ambiguous_dates_rejected(self):
        for value in [0, True, "09/11/2026", "2026-02-30"]:
            with self.assertRaises(ValidationError):
                Invoice.model_validate({**SAMPLE, "invoice_date": value})

    def test_absent_tax_does_not_become_zero(self):
        invoice = Invoice.model_validate({**SAMPLE, "tax": None})
        self.assertIsNone(invoice.tax)
        self.assertTrue(issues(invoice))
        with self.assertRaises(ValueError):
            export_csv(invoice)

    def test_wrong_printed_total_preserved(self):
        invoice = Invoice.model_validate({**SAMPLE, "total": 600000})
        self.assertEqual(invoice.total, 600000)
        self.assertTrue(issues(invoice))

    def test_arithmetic_does_not_prove_extraction_correct(self):
        invoice = Invoice.model_validate({**SAMPLE, "seller": "Wrong seller"})
        self.assertFalse(issues(invoice))
        self.assertFalse(compare(Invoice.model_validate(SAMPLE), invoice)["exact_invoice"])

    def test_exact_and_reordered_lines(self):
        invoice = Invoice.model_validate(SAMPLE)
        changed = Invoice.model_validate({**SAMPLE, "lines": list(reversed(SAMPLE["lines"]))})
        score = compare(invoice, changed)
        self.assertTrue(score["exact_invoice"])
        self.assertTrue(score["line_order_changed"])

    def test_extra_duplicate_and_null_lines_are_penalized(self):
        ref = Invoice.model_validate(SAMPLE)
        for extra in [SAMPLE["lines"][0], {k: None for k in SAMPLE["lines"][0]}]:
            actual = Invoice.model_validate({**SAMPLE, "lines": SAMPLE["lines"]+[extra]})
            score = compare(ref, actual)
            self.assertEqual(len(score["extra_lines"]), 1)
            self.assertFalse(score["exact_invoice"])
            self.assertLess(score["field_accuracy"], 1)

    def test_missing_line_and_false_fill(self):
        ref = Invoice.model_validate({**SAMPLE, "invoice_date": None})
        actual = Invoice.model_validate({**SAMPLE, "lines": SAMPLE["lines"][:1]})
        score = compare(ref, actual)
        self.assertEqual(len(score["missing_lines"]), 1)
        self.assertEqual(score["false_fills"], 1)

    def test_line_assignment_is_one_to_one(self):
        ref = Invoice.model_validate(SAMPLE)
        actual = Invoice.model_validate({**SAMPLE, "lines": [SAMPLE["lines"][0], SAMPLE["lines"][0]]})
        score = compare(ref, actual)
        self.assertFalse(score["exact_invoice"])
        self.assertEqual(len(set(score["line_mapping"].values())), 2)

    def test_assignment_matches_bruteforce_optimum(self):
        rng = random.Random(42)
        for n in range(1, 6):
            costs = [[rng.randint(0, 4) for _ in range(n)] for _ in range(n)]
            mapping = assignment(costs)
            total = sum(costs[i][j] for i, j in mapping.items())
            optimum = min(sum(costs[i][j] for i, j in enumerate(order)) for order in itertools.permutations(range(n)))
            self.assertEqual(total, optimum)

    def test_text_normalization_retains_accents_and_id_case(self):
        ref = Invoice.model_validate(SAMPLE)
        changed = Invoice.model_validate({**SAMPLE, "seller": SAMPLE["seller"].upper()})
        self.assertTrue(compare(ref, changed)["exact_invoice"])
        changed = Invoice.model_validate({**SAMPLE, "invoice_no": SAMPLE["invoice_no"].lower()})
        self.assertFalse(compare(ref, changed)["exact_invoice"])

    def test_csv_formula_protection(self):
        invoice = Invoice.model_validate({**SAMPLE, "seller": "=SUM(1,2)"})
        rows = list(csv.DictReader(io.StringIO(export_csv(invoice).decode("utf-8-sig"))))
        self.assertEqual(rows[0]["seller"], "'=SUM(1,2)")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.queue = ReviewQueue()
        self.did = self.queue.add(png("white"), "invoice.png")[0]
        self.queue.save_draft(self.did, SAMPLE)

    def approve(self, did=None):
        did = did or self.did
        self.queue.approve(did, "tester", True, self.queue.documents[did].draft_hash)

    def test_same_bytes_different_filename_are_deduplicated(self):
        did, added = self.queue.add(png("white"), "renamed.png")
        self.assertFalse(added)
        self.assertEqual(did, self.did)
        self.assertEqual(len(self.queue.documents), 1)

    def test_ack_and_current_hash_required(self):
        for ack, digest in [(False, self.queue.documents[self.did].draft_hash), (True, "stale")]:
            with self.assertRaises(ValueError):
                self.queue.approve(self.did, "tester", ack, digest)

    def test_edit_invalidates_approval(self):
        self.approve()
        self.queue.save_draft(self.did, {**SAMPLE, "invoice_no": "CHANGED"})
        self.assertFalse(self.queue.documents[self.did].approved)
        with self.assertRaises(ValueError):
            self.queue.single_csv(self.did)

    def test_missing_and_unbalanced_values_cannot_be_approved(self):
        for value in [None, 600000]:
            self.queue.save_draft(self.did, {**SAMPLE, "total": value})
            with self.assertRaises(ValueError):
                self.approve()

    def test_new_extraction_invalidates_even_if_values_same(self):
        self.approve()
        self.queue.set_extraction(self.did, Invoice.model_validate(SAMPLE), "mock-model")
        self.assertFalse(self.queue.documents[self.did].approved)

    def test_raw_prediction_preserved_after_manual_fix(self):
        self.queue.set_extraction(self.did, Invoice.model_validate({**SAMPLE, "total": 600000}), "mock-model")
        self.queue.save_draft(self.did, SAMPLE)
        doc = self.queue.documents[self.did]
        self.assertEqual(doc.extractions[0]["invoice"]["total"], 600000)
        self.assertEqual(self.queue.changes(self.did)[0]["field"], "total")

    def test_failed_retry_clears_approval_without_overwriting_draft(self):
        self.approve()
        before = copy.deepcopy(self.queue.documents[self.did].draft)
        self.queue.record_failure(self.did, "Groq HTTP 429")
        self.assertFalse(self.queue.documents[self.did].approved)
        self.assertEqual(self.queue.documents[self.did].draft, before)

    def test_duplicate_business_invoice_blocks_combined_export(self):
        other = self.queue.add(png("blue"), "another-photo.png")[0]
        self.queue.save_draft(other, SAMPLE)
        self.approve()
        self.approve(other)
        with self.assertRaisesRegex(ValueError, "trùng"):
            self.queue.bundle()
        self.queue.revoke(other)
        self.assertTrue(self.queue.bundle())

    def test_bundle_has_one_total_per_invoice_and_excludes_unapproved(self):
        other = self.queue.add(png("blue"), "pending.png")[0]
        self.approve()
        with zipfile.ZipFile(io.BytesIO(self.queue.bundle())) as archive:
            invoices = list(csv.DictReader(io.StringIO(archive.read("invoices.csv").decode("utf-8-sig"))))
            lines = list(csv.DictReader(io.StringIO(archive.read("line_items.csv").decode("utf-8-sig"))))
            audit = json.loads(archive.read("review_audit.json"))
        self.assertEqual(len(invoices), 1)
        self.assertEqual(sum(int(i["total_vnd"]) for i in invoices), 605000)
        self.assertEqual(len(lines), 2)
        self.assertNotIn("total_vnd", lines[0])
        self.assertEqual(audit["not_exported"][0]["document_id"], other)

    def test_replaced_source_bytes_are_detected(self):
        self.approve()
        self.queue.documents[self.did].data = png("blue")
        with self.assertRaises(ValueError):
            self.queue.bundle()


class VisionTests(unittest.TestCase):
    def test_invalid_and_oversized_images(self):
        for data in [b"not an image", b"x"*5_000_001]:
            with self.assertRaises(ValueError):
                read_image(data)

    def test_exif_orientation(self):
        image = Image.new("RGB", (20, 10))
        exif = image.getexif()
        exif[274] = 6
        output = io.BytesIO()
        image.save(output, "JPEG", exif=exif)
        self.assertEqual(read_image(output.getvalue()).size, (10, 20))

    def test_successful_mock_response_and_preserved_wrong_total(self):
        value = {**SAMPLE, "total": 600000}
        def opener(request, timeout):
            self.assertNotIn("secret-test", request.data.decode())
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}).encode())
        result = extract(read_image(png("white")), "secret-test", opener=opener)
        self.assertEqual(result.total, 600000)
        self.assertTrue(issues(result))

    def test_http_error_kind_and_secret_redaction(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "secret-test", {}, io.BytesIO(b"private response"))
        with self.assertRaises(ExtractionError) as caught:
            extract(read_image(png("white")), "secret-test", opener=opener)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.kind, "request_error")
        self.assertNotIn("secret-test", str(caught.exception))

    def test_schema_error_distinguished_from_api_error(self):
        def opener(request, timeout):
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        with self.assertRaises(ExtractionError) as caught:
            extract(read_image(png("white")), "test", opener=opener)
        self.assertEqual(caught.exception.kind, "schema_error")

    def test_truncated_output_is_not_accepted(self):
        def opener(request, timeout):
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "length", "message": {"content": json.dumps(SAMPLE)}}]}).encode())
        with self.assertRaises(ExtractionError) as caught:
            extract(read_image(png("white")), "test", opener=opener)
        self.assertEqual(caught.exception.kind, "response_error")


if __name__ == "__main__":
    unittest.main()
