import os
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from vision import ExtractionError

ROOT = Path(__file__).resolve().parents[1]


def button(app, label):
    return next(b for b in app.button if b.label == label)


def selector(app, label):
    return next(s for s in app.selectbox if s.label == label)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GROQ_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def start(self):
        app = AppTest.from_file(str(ROOT/"app.py"), default_timeout=15).run()
        self.assertFalse(app.exception)
        return app

    def load_sample(self, app):
        button(app, "Nạp đáp án mẫu").click().run()
        self.assertFalse(app.exception)

    def approve(self, app):
        app.checkbox[0].check().run()
        button(app, "Xác nhận duyệt hóa đơn").click().run()
        self.assertFalse(app.exception)

    def test_app_never_calls_api_on_load_or_prepared_demo(self):
        with patch("vision.extract", side_effect=AssertionError("No API call expected")) as fake:
            app = self.start()
            self.assertTrue(button(app, "Đọc ảnh bằng Groq").disabled)
            self.load_sample(app)
            self.approve(app)
            self.assertEqual(fake.call_count, 0)
            self.assertTrue(next(iter(app.session_state["review_queue_v2"].documents.values())).approved)

    def test_saved_bad_total_blocks_approval_and_repair_requires_new_ack(self):
        app = self.start()
        self.load_sample(app)
        self.approve(app)
        total = next(n for n in app.number_input if n.label == "Tổng thanh toán (VND)")
        total.set_value(600000)
        button(app, "Lưu thay đổi").click().run()
        self.assertFalse(app.exception)
        doc = next(iter(app.session_state["review_queue_v2"].documents.values()))
        self.assertFalse(doc.approved)
        self.assertEqual(doc.draft["total"], 600000)
        self.assertTrue(any("Tạm tính + thuế" in w.value for w in app.warning))
        self.assertFalse(any(b.label == "Xác nhận duyệt hóa đơn" for b in app.button))
        next(n for n in app.number_input if n.label == "Tổng thanh toán (VND)").set_value(605000)
        button(app, "Lưu thay đổi").click().run()
        self.assertFalse(app.checkbox[0].value)
        self.assertFalse(doc.approved)

    def test_switching_documents_keeps_saved_draft_and_separate_approvals(self):
        app = self.start()
        self.load_sample(app)
        did = next(iter(app.session_state["review_queue_v2"].documents))
        next(t for t in app.text_input if t.label == "Người bán").set_value("Saved seller")
        button(app, "Lưu thay đổi").click().run()
        sample_selector = selector(app, "Mẫu hóa đơn giả lập")
        sample_selector.set_value(next(s for s in sample_selector.options if s.startswith("INV-005")))
        button(app, "Thêm mẫu vào hàng đợi").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["review_queue_v2"].documents), 2)
        selector(app, "Hóa đơn đang đối soát").set_value(did).run()
        self.assertEqual(next(t for t in app.text_input if t.label == "Người bán").value, "Saved seller")

    def test_failed_extraction_removes_approval_and_preserves_saved_draft(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "mock-key"}), patch("vision.extract", side_effect=ExtractionError("Groq HTTP 429", status=429)):
            app = self.start()
            self.load_sample(app)
            self.approve(app)
            button(app, "Đọc ảnh bằng Groq").click().run()
            self.assertFalse(app.exception)
            doc = next(iter(app.session_state["review_queue_v2"].documents.values()))
            self.assertFalse(doc.approved)
            self.assertEqual(doc.draft["total"], 605000)
            self.assertTrue(any("429" in e.value for e in app.error))


if __name__ == "__main__":
    unittest.main()
