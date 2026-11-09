"""Compare live image extraction against known synthetic invoice fields."""
import argparse
import json
import os
from pathlib import Path

from PIL import ImageFilter

from invoice import Invoice, issues
from vision import MODEL, extract, read_image


def fields(invoice):
    data = invoice.model_dump(mode="json")
    lines = data.pop("lines")
    data["line_count"] = len(lines)
    for index, line in enumerate(lines, 1):
        for name, value in line.items():
            data[f"line_{index}.{name}"] = value
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("original", "blurred", "tilted"),
                        help="Evaluate just one image condition.")
    parser.add_argument("--output", type=Path, help="Write the results to a separate JSON report.")
    args = parser.parse_args()
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set GROQ_API_KEY before evaluating live extraction.")
    model = os.environ.get("INVOICE_VISION_MODEL", MODEL)
    data = Path(__file__).with_name("data")
    reference = Invoice.model_validate_json((data / "reference.json").read_text(encoding="utf-8"))
    original = read_image((data / "sample.png").read_bytes())
    variants = {"original": original, "blurred": original.filter(ImageFilter.GaussianBlur(2.5)),
                "tilted": original.rotate(7, expand=True, fillcolor="white")}
    expected = fields(reference)
    rows = []
    for name, image in variants.items():
        if args.variant and args.variant != name:
            continue
        try:
            invoice = extract(image, key, model)
        except RuntimeError as error:
            rows.append({"variant": name, "error": str(error)})
            continue
        actual = fields(invoice)
        differences = {field: {"expected": value, "actual": actual.get(field)}
                       for field, value in expected.items() if actual.get(field) != value}
        rows.append({"variant": name, "matched": len(expected) - len(differences), "total_fields": len(expected),
                     "differences": differences, "arithmetic_issues": issues(invoice)})
    filename = f"live-{args.variant}.json" if args.variant else "live.json"
    target = args.output or Path("reports") / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"model": model, "invoices": 1, "variants": rows},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {target}")
    return int(any("error" in row for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
