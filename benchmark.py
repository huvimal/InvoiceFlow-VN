"""Multi-layout benchmark. Replay checks the evaluator; live mode makes paid/quota API calls."""
import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from invoice import Invoice, issues
from scoring import compare
from vision import EXTRACTION_VERSION, MODEL, ExtractionError, extract, read_image

ROOT = Path(__file__).resolve().parent


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(root, name):
    result = (root/name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Dataset paths must stay inside the dataset directory.")
    return result


def load_dataset(root):
    root = Path(root)
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    ids, groups, images = set(), {}, set()
    for case in manifest["cases"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case["id"]) or case["id"] in ids:
            raise ValueError("Case IDs must be unique and filename-safe.")
        ids.add(case["id"])
        if case["split"] not in {"dev", "test"}:
            raise ValueError("Unknown benchmark split.")
        if case["layout"] in groups and groups[case["layout"]] != case["split"]:
            raise ValueError("Layout leakage across dev/test.")
        groups[case["layout"]] = case["split"]
        for kind in ["image", "reference"]:
            path = within(root, case[kind])
            if file_sha(path) != case[f"{kind}_sha256"]:
                raise ValueError(f"Dataset fingerprint mismatch for {case['id']} {kind}.")
        if case["image_sha256"] in images:
            raise ValueError("Duplicate image content; variants must not be counted as independent invoices.")
        images.add(case["image_sha256"])
        reference = Invoice.model_validate_json(within(root, case["reference"]).read_text(encoding="utf-8"))
        if issues(reference) != case["expected_review_issues"]:
            raise ValueError("Reference issue expectations changed; inspect and version the dataset.")
        read_image(within(root, case["image"]).read_bytes())
    if not ids:
        raise ValueError("Dataset is empty.")
    return manifest


def summarize(rows, planned):
    successful = [r for r in rows if r["status"] == "success"]
    scores = [r["score"] for r in successful]
    fields = sum(s["total_fields"] for s in scores)
    present = sum(s["present_fields"] for s in scores)
    absent = sum(s["absent_fields"] for s in scores)
    errors = Counter()
    for score in scores:
        for difference in score["differences"]:
            field = re.sub(r"\[\d+\]", "[]", difference["field"])
            errors[field] += 1
    exact = sum(s["exact_invoice"] for s in scores)
    status_counts = dict(Counter(r["status"] for r in rows))
    return {"selected_cases": planned, "processed_cases": len(rows), "not_run": planned-len(rows),
            "completed": len(rows) == planned, "coverage": len(rows)/planned if planned else 0,
            "status_counts": status_counts, "successful_extractions": len(successful), "exact_invoices": exact,
            "exact_invoice_rate_over_processed_including_failures": exact/len(rows) if rows else None,
            "field_accuracy_on_successes": sum(s["matched"] for s in scores)/fields if fields else None,
            "present_field_accuracy_on_successes": sum(s["present_matched"] for s in scores)/present if present else None,
            "absent_field_false_fill_rate_on_successes": sum(s["false_fills"] for s in scores)/absent if absent else None,
            "extra_lines": sum(len(s["extra_lines"]) for s in scores),
            "missing_lines": sum(len(s["missing_lines"]) for s in scores),
            "arithmetic_or_completeness_flags": sum(bool(r["review_issues"]) for r in successful),
            "field_errors": dict(errors.most_common())}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    os.replace(temp, path)
    esc = lambda value: html.escape(str(value))
    cases = []
    for row in report["rows"]:
        detail = row.get("score", {}).get("differences", [])
        cases.append(f"<tr><td>{esc(row['case_id'])}</td><td>{esc(row['layout'])}</td><td>{esc(row['status'])}</td>"
                     f"<td>{esc(row.get('score', {}).get('field_accuracy'))}</td><td>{esc(row.get('score', {}).get('exact_invoice'))}</td>"
                     f"<td><details><summary>Xem lỗi</summary><pre>{esc(json.dumps(detail or row.get('error') or [], ensure_ascii=False, indent=2))}</pre></details></td></tr>")
    page = f'''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>InvoiceFlow VN benchmark</title><style>body{{font:16px system-ui;margin:32px;color:#183347;line-height:1.6}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border:1px solid #c5d1db;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;background:#eef4f7;padding:14px}}.notice{{padding:16px;background:#fff0d0}}</style>
<h1>InvoiceFlow VN — Benchmark</h1><p class="notice">Mode: {esc(report['config']['mode'])}. Replay là phản hồi giả lập; không phải độ chính xác của model OCR.</p>
<pre>{esc(json.dumps(report['summary'], ensure_ascii=False, indent=2))}</pre>
<p>Điểm trường chỉ tính trên phản hồi hợp lệ. Tỷ lệ đúng toàn hóa đơn tính cả lần gọi lỗi; xem coverage trước khi kết luận.</p>
<table><tr><th>Case</th><th>Layout</th><th>Status</th><th>Field accuracy</th><th>Exact invoice</th><th>Details</th></tr>{''.join(cases)}</table>
<h2>By layout</h2><pre>{esc(json.dumps(report['by_layout'], ensure_ascii=False, indent=2))}</pre>
<h2>Limitations</h2><ul>{''.join('<li>'+esc(x)+'</li>' for x in report['limitations'])}</ul></html>'''
    path.with_suffix(".html").write_text(page, encoding="utf-8")
    with path.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "status", "field", "expected", "actual"])
        for row in report["rows"]:
            for diff in row.get("score", {}).get("differences", []):
                values = [row["case_id"], row["status"], diff["field"], diff["expected"], diff["actual"]]
                writer.writerow(["'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])


def run(root, mode, split, model, output, *, limit=None, max_requests=5, resume=False, retry_errors=False, extractor=None):
    root, output = Path(root), Path(output)
    manifest = load_dataset(root)
    selected = [c for c in manifest["cases"] if split == "all" or c["split"] == split]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive.")
        selected = selected[:limit]
    if not selected or max_requests < 1:
        raise ValueError("Selection must be nonempty and max_requests positive.")
    if mode not in {"replay", "live"}:
        raise ValueError("Use replay or live mode.")
    replay = json.loads((root/"replay.json").read_text(encoding="utf-8")) if mode == "replay" else None
    config = {"mode": mode, "model": model if mode == "live" else "scripted-fixtures",
              "split": split, "case_ids": [c["id"] for c in selected], "selection_limit": limit,
              "manifest_sha256": sha(manifest), "replay_sha256": sha(replay) if replay else None,
              "extraction_version": EXTRACTION_VERSION,
              "implementation_sha256": sha({n: (ROOT/n).read_text(encoding="utf-8") for n in ["invoice.py", "vision.py", "scoring.py", "benchmark.py"]})}
    if output.exists() and not resume:
        raise ValueError("Report exists. Use --resume or choose a new --output; reports are not silently replaced.")
    if retry_errors and not resume:
        raise ValueError("--retry-errors requires --resume.")
    old = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    if old and old["config"] != config:
        raise ValueError("Cannot resume: model, selection, source data, prompt or code changed. Use a new report.")
    saved = {r["case_id"]: r for r in old["rows"]} if old else {}
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if mode == "live" and not key:
        raise ValueError("Set GROQ_API_KEY in this terminal before live extraction.")
    calls, rows = 0, []
    def checkpoint():
        current = [saved[c["id"]] for c in selected if c["id"] in saved]
        report = {"config": config, "synthetic": manifest.get("synthetic", False),
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "summary": summarize(current, len(selected)), "rows": current,
                  "by_layout": {layout: summarize([r for r in current if r["layout"] == layout],
                                                    sum(c["layout"] == layout for c in selected)) for layout in sorted({c["layout"] for c in selected})},
                  "limitations": ["Synthetic original invoice layouts; not independent real supplier documents.",
                                  "References come from renderer source values and need independent human checking before external claims.",
                                  "Line items use minimum-mismatch one-to-one alignment; duplicate extras are penalized.",
                                  "Text seller/description ignores case and whitespace, retains accents; IDs/numbers/dates remain exact.",
                                  "Schema/request errors are not scored as field substitutions; they count against end-to-end invoice success.",
                                  "Latest attempts drive metrics; prior attempts remain in attempt_history when explicit retries occur."]}
        write_report(output, report)
        return report
    for case in selected:
        previous = saved.get(case["id"])
        if previous and (previous["status"] == "success" or not retry_errors):
            continue
        if mode == "live" and calls >= max_requests:
            break
        started = time.perf_counter()
        row = {"case_id": case["id"], "layout": case["layout"], "condition": case["condition"], "split": case["split"],
               "image_sha256": case["image_sha256"], "reference_sha256": case["reference_sha256"]}
        try:
            reference = Invoice.model_validate_json(within(root, case["reference"]).read_text(encoding="utf-8"))
            if mode == "replay":
                fixture = replay["cases"][case["id"]]
                if "error" in fixture:
                    err = fixture["error"]
                    raise ExtractionError(err["message"], err["kind"], err.get("status"))
                actual = Invoice.model_validate(fixture["invoice"])
            else:
                calls += 1
                actual = (extractor or extract)(read_image(within(root, case["image"]).read_bytes()), key, model)
            row.update(status="success", prediction=actual.model_dump(mode="json"), score=compare(reference, actual),
                       review_issues=issues(actual), expected_review_issues=case["expected_review_issues"])
        except ExtractionError as error:
            row.update(status=error.kind, error={"message": str(error), "http_status": error.status})
        except ValidationError:
            row.update(status="schema_error", error={"message": "Prediction does not match strict invoice schema."})
        except (ValueError, KeyError, OSError) as error:
            row.update(status="input_or_fixture_error", error={"message": str(error)})
        row["elapsed_seconds"] = time.perf_counter()-started
        if previous:
            row["attempt_history"] = previous.get("attempt_history", []) + [{k: v for k, v in previous.items() if k != "attempt_history"}]
        saved[case["id"]] = row
        checkpoint()
        if mode == "live" and row.get("error", {}).get("http_status") == 429:
            break  # Persist progress and avoid spending the rest of the request budget on a rate limit.
    return checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--dataset", type=Path, default=ROOT/"data"/"benchmark")
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--model", default=os.environ.get("INVOICE_VISION_MODEL", MODEL))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            manifest = load_dataset(args.dataset)
            print(f"Valid dataset: {len(manifest['cases'])} cases; synthetic={manifest.get('synthetic')}")
            return 0
        target = args.output or ROOT/"reports"/f"benchmark-{args.mode}-{args.split}.json"
        report = run(args.dataset, args.mode, args.split, args.model, target, limit=args.limit,
                     max_requests=args.max_requests, resume=args.resume, retry_errors=args.retry_errors)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"Report: {target.resolve()}\nHTML: {target.resolve().with_suffix('.html')}")
        return int(any(r["status"] != "success" for r in report["rows"]))
    except (ValueError, KeyError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
