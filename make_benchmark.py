"""Render original synthetic invoices with known printed values; never uses an OCR model."""
import argparse
import csv
import hashlib
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from invoice import Invoice, issues

ROOT = Path(__file__).resolve().parent
LAYOUTS = ["classic", "receipt", "minimal", "landscape", "split"]
CONDITIONS = ["clean", "tilted", "soft_blur", "low_contrast"]
ITEMS = ["Bàn phím cơ", "Chuột không dây", "Tai nghe", "Sổ tay", "Bút mực", "Đèn bàn", "Cáp sạc", "Giá đỡ laptop"]
SELLERS = ["Cửa hàng An Bình", "Văn phòng phẩm Mai", "Thiết bị Minh Anh", "Huni Office", "Cửa hàng Sao Việt"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def find_font(explicit=None):
    paths = [explicit] if explicit else ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for value in paths:
        if value and Path(value).exists():
            return str(value)
    raise ValueError("Provide --font with a Unicode TrueType font supporting Vietnamese.")


def money(value):
    return "—" if value is None else f"{value:,}".replace(",", ".")


def render(record, layout, font_path):
    width = 780 if layout == "receipt" else 1500 if layout == "landscape" else 1200
    height = 1350 if layout == "receipt" else 920 if layout == "landscape" else 1100
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    margin = 45
    dark, muted = "#14364a", "#526675"
    def text(x, y, value, size=25, color=dark, max_width=None):
        while True:
            font = ImageFont.truetype(font_path, size)
            if max_width is None or draw.textbbox((0, 0), value, font=font)[2] <= max_width or size <= 14:
                break
            size -= 1
        draw.text((x, y), value, font=font, fill=color)
    def rule(y):
        draw.line((margin, y, width-margin, y), fill="#c0ccd4", width=2)
    def meta(y, seller_x=45):
        text(seller_x, y, record.seller or "", 29, max_width=width-seller_x-margin)
        text(margin, y+55, f"Số hóa đơn: {record.invoice_no or '—'}", 25)
        text(margin, y+100, f"Ngày: {record.invoice_date.strftime('%d/%m/%Y') if record.invoice_date else '—'}", 25)
    if layout == "classic":
        draw.rectangle((0, 0, width, 130), fill=dark)
        text(margin, 32, "HÓA ĐƠN BÁN HÀNG", 43, "white")
        meta(165)
        y = 340
    elif layout == "receipt":
        text(margin, 35, "PHIẾU BÁN HÀNG", 39)
        rule(110)
        meta(145)
        y = 325
    elif layout == "minimal":
        text(margin, 35, "HÓA ĐƠN / INVOICE", 42, "#111111")
        rule(110)
        meta(150)
        y = 330
    elif layout == "landscape":
        text(margin, 30, "HÓA ĐƠN THANH TOÁN", 42)
        text(850, 35, record.seller or "", 27, max_width=600)
        text(margin, 115, f"Số: {record.invoice_no or '—'}", 25)
        text(650, 115, f"Ngày: {record.invoice_date.strftime('%d/%m/%Y') if record.invoice_date else '—'}", 25)
        y = 215
    else:
        draw.rectangle((width-380, 0, width, 160), fill="#e7f0ec")
        text(margin, 40, "CHI TIẾT HÓA ĐƠN", 40)
        text(width-350, 50, "VND", 35)
        meta(180)
        y = 350
    if layout == "receipt":
        for line in record.lines:
            text(margin, y, line.description or "", 27, max_width=width-2*margin)
            text(margin, y+43, f"{line.quantity} × {money(line.unit_price)}", 24, muted)
            text(width-235, y+43, money(line.amount), 25)
            rule(y+95)
            y += 118
    else:
        if layout == "landscape":
            columns, labels = [45, 680, 990, 1130], ["Mặt hàng", "Thành tiền", "SL", "Đơn giá"]
        else:
            columns, labels = [45, 580, 730, 940], ["Mặt hàng", "SL", "Đơn giá", "Thành tiền"]
        draw.rectangle((margin-10, y-10, width-margin+10, y+48), fill="#e9eff3")
        for x, label in zip(columns, labels):
            text(x, y, label, 25)
        y += 75
        for line in record.lines:
            values = [line.description or "", money(line.amount), str(line.quantity), money(line.unit_price)] if layout == "landscape" else [line.description or "", str(line.quantity), money(line.unit_price), money(line.amount)]
            for i, (x, value) in enumerate(zip(columns, values)):
                text(x, y, value, 24, max_width=(columns[1]-columns[0]-25) if i == 0 else None)
            rule(y+46)
            y += 65
    y += 25
    totals_x = margin if layout in {"receipt", "split"} else width-540
    for label, value in [("Tạm tính", record.subtotal), ("Thuế", record.tax), ("Tổng thanh toán", record.total)]:
        text(totals_x, y, label, 25)
        text(totals_x+300, y, money(value), 27)
        y += 52
    if y > height-130:
        raise ValueError("Layout overflow; adjust the renderer before publishing the image.")
    text(margin, height-100, "DỮ LIỆU GIẢ LẬP • Đơn vị tiền: VND", 21, muted)
    text(margin, height-60, "Không phải hóa đơn thuế hay chứng từ giao dịch thật.", 18, muted)
    return image


def generate(root, font_path):
    root = Path(root)
    (root/"images").mkdir(parents=True, exist_ok=True)
    (root/"references").mkdir(exist_ok=True)
    rng, cases, replay = random.Random(42), [], {}
    for i in range(1, 21):
        layout = LAYOUTS[(i-1)//4]
        condition = CONDITIONS[(i-1) % 4]
        lines = []
        for name in rng.sample(ITEMS, 1+(i % 4)):
            quantity, price = rng.randint(1, 4), rng.choice([15000, 35000, 75000, 125000, 240000])
            lines.append({"description": name, "quantity": quantity, "unit_price": price, "amount": quantity*price})
        if i == 5:
            lines[0]["unit_price"] = lines[0]["amount"] = 0
        subtotal = sum(line["amount"] for line in lines)
        tax = 0 if i % 3 == 0 else subtotal//10
        value = {"seller": SELLERS[(i-1)//4], "invoice_no": f"BENCH-{i:03d}" if i != 18 else None,
                 "invoice_date": f"2026-10-{i:02d}" if i != 7 else None, "lines": lines,
                 "subtotal": subtotal, "tax": tax, "total": subtotal+tax+(5000 if i == 11 else 0)}
        reference = Invoice.model_validate(value)
        image = render(reference, layout, font_path)
        if condition == "tilted":
            image = image.rotate(2.5, expand=True, fillcolor="white")
        elif condition == "soft_blur":
            image = image.filter(ImageFilter.GaussianBlur(.65))
        elif condition == "low_contrast":
            image = ImageEnhance.Contrast(image).enhance(.65)
        cid = f"INV-{i:03d}"
        image_path, ref_path = root/"images"/f"{cid}.png", root/"references"/f"{cid}.json"
        image.save(image_path)
        ref_path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
        cases.append({"id": cid, "layout": layout, "condition": condition,
                      "split": "dev" if layout in LAYOUTS[:3] else "test",
                      "image": f"images/{cid}.png", "image_sha256": digest(image_path),
                      "reference": f"references/{cid}.json", "reference_sha256": digest(ref_path),
                      "expected_review_issues": issues(reference), "reference_origin": "renderer source values; not independent human annotation"})
        replay[cid] = {"invoice": json.loads(json.dumps(value))}
    replay["INV-002"]["invoice"]["total"] += 1000
    replay["INV-004"]["invoice"]["lines"].append(dict(replay["INV-004"]["invoice"]["lines"][0]))
    replay["INV-006"]["invoice"]["seller"] = None
    replay["INV-007"]["invoice"]["invoice_date"] = "2026-10-07"
    replay["INV-003"]["invoice"]["lines"].reverse()
    replay["INV-009"] = {"error": {"kind": "request_error", "status": 429, "message": "Simulated rate limit; no API request made."}}
    replay["INV-010"]["invoice"]["total"] = "not-an-integer"
    replay["INV-016"]["invoice"]["lines"].pop()
    replay["INV-018"]["invoice"]["invoice_no"] = "GUESSED-018"
    replay["INV-020"]["invoice"]["lines"][0]["quantity"] += 1
    manifest = {"version": "synthetic-layouts-v1", "synthetic": True, "seed": 42,
                "rights": "Original synthetic invoices created for InvoiceFlow-VN; no customer data.",
                "reference_policy": "Printed values are ground truth, including deliberately wrong totals and absent fields.",
                "split_policy": "12 dev / 8 test; layout families are disjoint. Do not tune on test results.",
                "cases": cases}
    (root/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (root/"replay.json").write_text(json.dumps({"mode": "scripted_replay_not_model_output", "cases": replay}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    with (root/"ground_truth.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "split", "layout", "condition", "image", "seller", "invoice_no", "invoice_date", "lines_json", "subtotal_vnd", "tax_vnd", "total_vnd", "expected_review_issues"])
        for case in cases:
            value = json.loads((root/case["reference"]).read_text(encoding="utf-8"))
            writer.writerow([case["id"], case["split"], case["layout"], case["condition"], case["image"], value["seller"], value["invoice_no"], value["invoice_date"], json.dumps(value["lines"], ensure_ascii=False), value["subtotal"], value["tax"], value["total"], "; ".join(case["expected_review_issues"])])
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"data"/"benchmark")
    parser.add_argument("--font")
    args = parser.parse_args()
    result = generate(args.output, find_font(args.font))
    print(f"Generated {len(result['cases'])} original synthetic invoices with five layouts.")
