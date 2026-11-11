"""Local multi-invoice workspace. API calls require an explicit click."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from invoice import Invoice, issues
from review import ReviewQueue
from vision import MODEL, extract, read_image

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="InvoiceFlow VN", page_icon="🧾", layout="wide")
st.title("InvoiceFlow VN")
st.caption("Nhập ảnh → Trích xuất → Đối chiếu → Duyệt → Xuất dữ liệu")
if "review_queue_v2" not in st.session_state:
    st.session_state["review_queue_v2"] = ReviewQueue()
queue = st.session_state["review_queue_v2"]
key = st.sidebar.text_input("Groq API key", type="password", value=os.environ.get("GROQ_API_KEY", ""))
model = st.sidebar.text_input("Model hỗ trợ ảnh", value=os.environ.get("INVOICE_VISION_MODEL", MODEL))
reviewer = st.sidebar.text_input("Tên người duyệt", value="Huni")
st.sidebar.caption("Đọc ảnh gửi ảnh tới Groq và dùng hạn mức tài khoản. Bản nháp chỉ lưu trong phiên; tải gói đã duyệt để giữ kết quả.")
uploads = st.file_uploader("Thêm ảnh JPG/PNG — tối đa 20 ảnh, mỗi ảnh 5 MB", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
for upload in uploads or []:
    try:
        queue.add(upload.getvalue(), upload.name)
    except ValueError as error:
        st.error(f"{upload.name}: {error}")

samples = {"Mẫu ban đầu — HD-0006": (ROOT/"data/sample.png", ROOT/"data/reference.json")}
manifest_path = ROOT/"data/benchmark/manifest.json"
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        samples[f"{case['id']} · {case['layout']} · {case['condition']}"] = (ROOT/"data/benchmark"/case["image"], ROOT/"data/benchmark"/case["reference"])
chosen_sample = st.sidebar.selectbox("Mẫu hóa đơn giả lập", list(samples))
if st.sidebar.button("Thêm mẫu vào hàng đợi"):
    try:
        did, _ = queue.add(samples[chosen_sample][0].read_bytes(), samples[chosen_sample][0].name)
        st.session_state["active_document"] = did
    except ValueError as error:
        st.error(str(error))
if not queue.documents:
    queue.add((ROOT/"data/sample.png").read_bytes(), "sample.png")

approved_count = sum(d.approved for d in queue.documents.values())
cols = st.columns(3)
cols[0].metric("Ảnh trong phiên", len(queue.documents))
cols[1].metric("Đã duyệt", approved_count)
cols[2].metric("Cần xử lý", len(queue.documents)-approved_count)
requested = st.session_state.pop("active_document", None)
if requested:
    st.session_state["document_selector"] = requested
did = st.selectbox("Hóa đơn đang đối soát", list(queue.documents), key="document_selector",
                   format_func=lambda value: ("✓ " if queue.documents[value].approved else "○ ")+queue.documents[value].name+" · "+value[:8])
doc = queue.documents[did]
image = read_image(doc.data)
left, right = st.columns([1, 1.1])
with left:
    st.subheader("Ảnh nguồn")
    st.image(image, caption=doc.name)
    st.caption("Các phép tính khớp chưa bảo đảm AI đọc đúng. Đối chiếu cả tên, ngày, mã và từng dòng hàng.")
with right:
    st.subheader("Trích xuất và chỉnh sửa")
    demo_col, ai_col = st.columns(2)
    reference_path = next((reference for source, reference in samples.values() if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == did), None)
    if reference_path and demo_col.button("Nạp đáp án mẫu", key=f"demo-{did}"):
        prepared = Invoice.model_validate_json(reference_path.read_text(encoding="utf-8"))
        queue.save_draft(did, prepared.model_dump(mode="json"), "demo_reference")
        st.rerun()
    if ai_col.button("Đọc ảnh bằng Groq", disabled=not key.strip() or not model.strip(), key=f"extract-{did}"):
        try:
            with st.spinner("Đang đọc hóa đơn đã chọn..."):
                invoice = extract(image, key.strip(), model.strip())
            queue.set_extraction(did, invoice, model.strip())
            st.rerun()
        except RuntimeError as error:
            queue.record_failure(did, str(error))
            st.rerun()
    if doc.last_error:
        st.error(doc.last_error+" Dữ liệu trước đó được giữ lại; lần gọi này chưa tạo kết quả mới.")
    if doc.origin == "demo_reference":
        st.info("Đang dùng đáp án chuẩn bị sẵn để thử giao diện. Đây chưa phải kết quả AI đọc ảnh.")
    elif doc.origin == "groq":
        st.caption("Nguồn: Groq · "+doc.extractions[-1]["model"])
    else:
        st.caption("Nguồn: nhập tay")
    draft, revision = doc.draft, doc.revision
    prefix = f"{did}-{revision}"
    with st.form(f"edit-{prefix}"):
        edited_invoice = {}
        for name, label in [("seller", "Người bán"), ("invoice_no", "Số hóa đơn"), ("invoice_date", "Ngày (YYYY-MM-DD)")]:
            edited_invoice[name] = st.text_input(label, value=draft[name] or "", key=f"{prefix}-{name}").strip() or None
        rows = draft["lines"] or [{"description": None, "quantity": None, "unit_price": None, "amount": None}]
        frame = pd.DataFrame(rows).astype({"description": "string", "quantity": "Int64", "unit_price": "Int64", "amount": "Int64"})
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, key=f"{prefix}-lines", column_config={
            "description": st.column_config.TextColumn("Mặt hàng"),
            "quantity": st.column_config.NumberColumn("SL", min_value=1, step=1),
            "unit_price": st.column_config.NumberColumn("Đơn giá VND", min_value=0, step=1),
            "amount": st.column_config.NumberColumn("Thành tiền VND", min_value=0, step=1)})
        edited_invoice["lines"] = edited.astype(object).where(pd.notna(edited), None).to_dict("records")
        for row in edited_invoice["lines"]:
            for field in ["quantity", "unit_price", "amount"]:
                value = row[field]
                if isinstance(value, float) and value.is_integer():
                    row[field] = int(value)
        for name, label in [("subtotal", "Tạm tính"), ("tax", "Thuế"), ("total", "Tổng thanh toán")]:
            edited_invoice[name] = st.number_input(label+" (VND)", min_value=0, max_value=1_000_000_000_000,
                                                   value=draft[name], step=1, key=f"{prefix}-{name}")
        if st.form_submit_button("Lưu thay đổi"):
            try:
                queue.save_draft(did, edited_invoice)
                st.rerun()
            except ValidationError as error:
                for detail in error.errors(include_input=False, include_url=False):
                    st.error(f"{'.'.join(map(str, detail['loc']))}: {detail['msg']}")
    st.caption("Sau khi chỉnh sửa, bấm Lưu thay đổi trước khi duyệt. Chỉ dữ liệu đã lưu được xuất.")

st.subheader("Kiểm tra và duyệt bản đã lưu")
result = Invoice.model_validate(doc.draft)
st.caption(f"Bản đã lưu: {result.invoice_no or 'chưa có số'} · Tổng thanh toán: {result.total if result.total is not None else 'chưa có'} VND")
errors = issues(result)
for message in errors:
    st.warning(message)
if not errors:
    st.success("Các trường bắt buộc và phép tính hợp lệ. Hãy kiểm tra lại với ảnh nguồn.")
    acknowledged = st.checkbox("Tôi đã đối chiếu bản đã lưu với ảnh nguồn", key=f"ack-{did}-{doc.revision}-{doc.draft_hash}")
    if st.button("Xác nhận duyệt hóa đơn", disabled=not acknowledged, key=f"approve-{did}-{doc.revision}-{doc.draft_hash}"):
        try:
            queue.approve(did, reviewer, acknowledged, doc.draft_hash)
            st.rerun()
        except ValueError as error:
            st.error(str(error))
if doc.approved:
    st.success("Đã duyệt bởi "+doc.approval["reviewed_by"])
    st.download_button("Tải CSV hóa đơn này", queue.single_csv(did), f"invoice-{did[:8]}.csv", "text/csv")
    if st.button("Hủy duyệt hóa đơn này", key=f"revoke-{did}"):
        queue.revoke(did)
        st.rerun()
if doc.extractions:
    with st.expander("Bản AI gốc và những trường đã sửa"):
        raw = doc.extractions[-1]
        st.json(raw["invoice"])
        st.download_button("Tải JSON trích xuất gốc", json.dumps(raw, ensure_ascii=False, indent=2), f"raw-{did[:8]}.json", "application/json")
        changes = queue.changes(did)
        if changes:
            st.dataframe(pd.DataFrame(changes), hide_index=True)
        else:
            st.caption("Bản đã lưu chưa khác bản AI gốc.")

st.subheader("Xuất các hóa đơn đã duyệt")
st.caption(f"Gói xuất gồm {approved_count} hóa đơn đã duyệt; {len(queue.documents)-approved_count} hóa đơn còn lại chưa được xuất.")
if approved_count:
    try:
        bundle = queue.bundle()
        st.download_button("Tải gói đã duyệt (ZIP)", bundle, "invoiceflow-reviewed.zip", "application/zip")
        st.caption("invoices.csv: một dòng mỗi hóa đơn. line_items.csv: dòng hàng. review_audit.json: nguồn và lịch sử chỉnh sửa.")
    except ValueError as error:
        st.error(str(error))
