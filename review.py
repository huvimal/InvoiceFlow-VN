"""Session-local queue: raw predictions, revision-bound approval and batch exports."""
import csv
import hashlib
import io
import json
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from invoice import Invoice, export_csv, issues
from scoring import flatten
from vision import EXTRACTION_VERSION, read_image

EMPTY = {"seller": None, "invoice_no": None, "invoice_date": None, "lines": [],
         "subtotal": None, "tax": None, "total": None}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'"+value
    return value


def csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return output.getvalue().encode("utf-8-sig")


@dataclass
class Document:
    id: str
    name: str
    data: bytes
    draft: dict = field(default_factory=lambda: json.loads(json.dumps(EMPTY)))
    origin: str = "manual"
    revision: int = 0
    extractions: list = field(default_factory=list)
    approval: dict | None = None
    last_error: str | None = None

    @property
    def draft_hash(self):
        return fingerprint(self.draft)

    @property
    def approved(self):
        return bool(self.approval and self.approval["draft_sha256"] == self.draft_hash)


class ReviewQueue:
    def __init__(self):
        self.documents = {}

    def add(self, data, name):
        did = hashlib.sha256(data).hexdigest()
        if did in self.documents:
            return did, False
        if len(self.documents) >= 20:
            raise ValueError("Mỗi phiên hỗ trợ tối đa 20 ảnh khác nhau.")
        read_image(data)
        self.documents[did] = Document(did, str(name)[:200], data)
        return did, True

    def save_draft(self, did, payload, origin=None):
        invoice = Invoice.model_validate(payload)
        doc = self.documents[did]
        value = invoice.model_dump(mode="json")
        if value != doc.draft or origin is not None:
            doc.approval = None
            doc.revision += 1
        doc.draft = value
        if origin is not None:
            doc.origin = origin
        return invoice

    def set_extraction(self, did, invoice, model):
        doc = self.documents[did]
        value = invoice.model_dump(mode="json")
        doc.extractions.append({"received_at": now(), "model": model, "extraction_version": EXTRACTION_VERSION,
                                "invoice": value, "prediction_sha256": fingerprint(value)})
        doc.last_error = None
        self.save_draft(did, value, "groq")

    def record_failure(self, did, message):
        doc = self.documents[did]
        doc.last_error = message
        doc.approval = None

    def revoke(self, did):
        doc = self.documents[did]
        doc.approval = None
        doc.revision += 1

    def approve(self, did, reviewer, acknowledged, expected_hash):
        doc = self.documents[did]
        if expected_hash != doc.draft_hash:
            raise ValueError("Dữ liệu đã thay đổi. Hãy đối chiếu và xác nhận lại.")
        if not acknowledged or not reviewer.strip():
            raise ValueError("Cần tên người duyệt và xác nhận đối chiếu với ảnh.")
        invoice = Invoice.model_validate(doc.draft)
        if issues(invoice):
            raise ValueError("Chưa thể duyệt: còn trường thiếu hoặc số tiền chưa khớp.")
        doc.approval = {"reviewed_by": reviewer.strip()[:80], "reviewed_at": now(),
                        "source_sha256": doc.id, "draft_sha256": doc.draft_hash, "revision": doc.revision}
        return doc.approval

    def changes(self, did):
        doc = self.documents[did]
        if not doc.extractions:
            return []
        original = flatten(Invoice.model_validate(doc.extractions[-1]["invoice"]))
        current = flatten(Invoice.model_validate(doc.draft))
        return [{"field": key, "raw": original.get(key), "reviewed": current.get(key)} for key in sorted(original.keys() | current.keys())
                if key not in original or key not in current or original[key] != current[key]]

    def bundle(self):
        approved = [d for d in self.documents.values() if d.approved]
        if not approved:
            raise ValueError("Chưa có hóa đơn được duyệt.")
        invoice_rows, line_rows, audit, business_keys = [], [], [], set()
        for doc in approved:
            invoice = Invoice.model_validate(doc.draft)
            if (issues(invoice) or doc.approval["source_sha256"] != doc.id
                    or hashlib.sha256(doc.data).hexdigest() != doc.id):
                raise ValueError("Bản duyệt không còn hợp lệ.")
            seller = " ".join(unicodedata.normalize("NFC", invoice.seller).casefold().split())
            key = (seller, invoice.invoice_no.casefold(), invoice.invoice_date.isoformat())
            if key in business_keys:
                raise ValueError("Có hóa đơn trùng người bán, số hóa đơn và ngày. Chưa thể xuất gói.")
            business_keys.add(key)
            invoice_rows.append([doc.id, invoice.seller, invoice.invoice_no, invoice.invoice_date.isoformat(),
                                 invoice.subtotal, invoice.tax, invoice.total, len(invoice.lines)])
            for i, line in enumerate(invoice.lines, 1):
                line_rows.append([doc.id, i, line.description, line.quantity, line.unit_price, line.amount])
            audit.append({"document_id": doc.id, "filename": doc.name, "origin": doc.origin,
                          "approval": doc.approval, "reviewed_invoice": doc.draft,
                          "extractions": doc.extractions, "changes_from_latest_raw": self.changes(doc.id)})
        invoices = csv_bytes(["document_id", "seller", "invoice_no", "invoice_date", "subtotal_vnd", "tax_vnd", "total_vnd", "line_count"], invoice_rows)
        lines = csv_bytes(["document_id", "line_number", "description", "quantity", "unit_price_vnd", "amount_vnd"], line_rows)
        audit_json = json.dumps({"version": 1, "created_at": now(), "approved_count": len(approved),
                                 "not_exported": [{"document_id": d.id, "filename": d.name} for d in self.documents.values() if not d.approved],
                                 "documents": audit,
                                 "limits": "Session-local attestations; reviewer aliases are not authenticated and records are not signed."},
                                ensure_ascii=False, indent=2).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("invoices.csv", invoices)
            bundle.writestr("line_items.csv", lines)
            bundle.writestr("review_audit.json", audit_json)
        return output.getvalue()

    def single_csv(self, did):
        doc = self.documents[did]
        if not doc.approved:
            raise ValueError("Hóa đơn chưa được duyệt hoặc dữ liệu đã thay đổi.")
        return export_csv(Invoice.model_validate(doc.draft))
