"""Streamlit review workspace for a single invoice at a time."""
import hashlib
import os
from pathlib import Path

import streamlit as st
import pandas as pd
from pydantic import ValidationError

from invoice import Invoice, export_csv, issues
from vision import MODEL, extract, read_image

ROOT = Path(__file__).resolve().parent
EMPTY = {"seller": None, "invoice_no": None, "invoice_date": None,
         "subtotal": None, "tax": None, "total": None,
         "lines": [{"description": None, "quantity": None, "unit_price": None, "amount": None}]}

st.set_page_config(page_title="InvoiceFlow VN", layout="wide")
st.title("InvoiceFlow VN")
st.caption("Đọc ảnh hóa đơn · Soát số tiền · Xuất CSV")
key = st.sidebar.text_input("Groq API key", type="password", value=os.environ.get("GROQ_API_KEY", ""))
model = st.sidebar.text_input("Model hỗ trợ ảnh", value=MODEL)
st.sidebar.caption("Nút đọc ảnh gửi ảnh tới Groq. Chỉ dùng dữ liệu giả lập hoặc tài liệu được phép chia sẻ.")
upload = st.file_uploader("Ảnh hóa đơn JPG hoặc PNG (tối đa 5 MB)", type=["jpg", "jpeg", "png"])
data = upload.getvalue() if upload else (ROOT / "data/sample.png").read_bytes()
identity = ("upload" if upload else "demo", hashlib.sha256(data).hexdigest())
try:
    image = read_image(data)
except ValueError as error:
    st.error(str(error))
    st.stop()


def start_review(invoice, origin):
    st.session_state["draft"] = invoice
    st.session_state["origin"] = origin
    st.session_state["revision"] = st.session_state.get("revision", 0) + 1


if st.session_state.get("identity") != identity:
    st.session_state["identity"] = identity
    start_review(EMPTY, "Nhập tay")

left, right = st.columns(2)
with left:
    st.subheader("Ảnh nguồn")
    st.image(image, caption=upload.name if upload else "Hóa đơn giả lập HD-0006")
with right:
    st.subheader("Dữ liệu đối soát")
    demo_col, ai_col = st.columns(2)
    if not upload and demo_col.button("Nạp dữ liệu demo"):
        sample = Invoice.model_validate_json((ROOT / "data/reference.json").read_text(encoding="utf-8"))
        start_review(sample.model_dump(mode="json"), "Dữ liệu demo chuẩn bị sẵn")
    if ai_col.button("Đọc ảnh bằng Groq", disabled=not key.strip() or not model.strip()):
        try:
            with st.spinner("Đang đọc ảnh..."):
                invoice = extract(image, key.strip(), model.strip())
            start_review(invoice.model_dump(mode="json"), "Trích xuất bằng " + model.strip())
        except RuntimeError as error:
            st.error(str(error))
    st.caption(st.session_state["origin"])

    draft = st.session_state["draft"]
    revision = st.session_state["revision"]
    reviewed = {
        name: st.text_input(label, value=draft[name] or "", key=f"{revision}-{name}").strip() or None
        for name, label in [("seller", "Người bán"), ("invoice_no", "Số hóa đơn"), ("invoice_date", "Ngày (YYYY-MM-DD)")]
    }
    rows = draft["lines"] or EMPTY["lines"]
    frame = pd.DataFrame(rows).astype({"description": "string", "quantity": "Int64",
                                      "unit_price": "Int64", "amount": "Int64"})
    edited = st.data_editor(
        frame, num_rows="dynamic", hide_index=True, key=f"{revision}-lines",
        column_config={
            "description": st.column_config.TextColumn("Mặt hàng"),
            "quantity": st.column_config.NumberColumn("SL", min_value=1, step=1),
            "unit_price": st.column_config.NumberColumn("Đơn giá VND", min_value=0, step=1),
            "amount": st.column_config.NumberColumn("Thành tiền VND", min_value=0, step=1),
        },
    )
    reviewed["lines"] = [
        {field: row.get(field) for field in ("description", "quantity", "unit_price", "amount")}
        for row in edited.astype(object).where(pd.notna(edited), None).to_dict("records")
    ]
    # Streamlit may represent editable integer columns as floats when some cells are empty.
    for row in reviewed["lines"]:
        for field in ("quantity", "unit_price", "amount"):
            value = row[field]
            if isinstance(value, float):
                row[field] = None if value != value else int(value) if value.is_integer() else value
    for field, label in [("subtotal", "Tạm tính"), ("tax", "Thuế"), ("total", "Tổng thanh toán")]:
        reviewed[field] = st.number_input(label + " (VND)", min_value=0, max_value=1_000_000_000_000,
                                          value=draft[field], step=1, key=f"{revision}-{field}")
    try:
        result = Invoice.model_validate(reviewed)
    except ValidationError as error:
        for detail in error.errors(include_input=False, include_url=False):
            st.error(f"{'.'.join(map(str, detail['loc']))}: {detail['msg']}")
    else:
        errors = issues(result)
        for error in errors:
            st.warning(error)
        if not errors:
            st.success("Các phép tính khớp. Hãy đối chiếu từng trường với ảnh.")
            review_id = hashlib.sha256(result.model_dump_json().encode()).hexdigest()
            if st.checkbox("Tôi đã đối chiếu dữ liệu với ảnh nguồn", key=f"review-{revision}-{review_id}"):
                st.download_button("Tải CSV đã duyệt", export_csv(result), "invoice-reviewed.csv", "text/csv")
