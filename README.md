# InvoiceFlow VN

A Vietnamese invoice review application with a session-local queue of up to 20
JPG/PNG images, optional Groq vision extraction, raw-versus-reviewed traceability,
revision-bound human approvals, and separate invoice/line-item CSV exports.
Amounts are integer Vietnamese dong. This is a local prototype, not a fiscal
validation service or an authenticated multi-user accounting system.

## Upgrade v0.2

- 20 original synthetic invoices across five layout families, with a ground-truth CSV.
- Layout-disjoint dev/test split: 12 development invoices and 8 test invoices.
- Exact numeric/date scoring, one-to-one line alignment, extra/missing-line penalties,
  null-field hallucination counts, and per-layout error reports.
- Request/schema/response failures separate from field-value errors.
- Checkpoint/resume, an explicit live-request budget, and early stop on HTTP 429.
- A multi-image review queue, saved drafts, immutable copies of successful raw API
  responses, and approvals invalidated by changed saved data or a new extraction.
- Batch ZIP with one invoice total per invoice, line items, and review provenance.
- 40 automated tests, including Streamlit AppTest flows, plus offline GitHub Actions.

Read [HUONG_DAN_NANG_CAP.md](HUONG_DAN_NANG_CAP.md) for a Vietnamese walkthrough.

## Run on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8504
```

Open http://127.0.0.1:8504 and choose **Nạp đáp án mẫu** on the original sample.
This loads prepared values, not an AI prediction. Change the total from 605000 to
600000, click **Lưu thay đổi**, and verify approval/export is blocked. Restore the
amount, save, compare the saved values with the image, check the acknowledgement,
and click **Xác nhận duyệt hóa đơn**.

Upload multiple images or add synthetic cases from the sidebar. Each image has its
own draft, extraction history and approval. Save before switching documents. The
same image bytes under another filename are deduplicated, not reprocessed.
The app only sends the selected image to Groq when its extraction button is clicked.

Download the reviewed ZIP to retain the session's results:
- `invoices.csv`: one row per approved invoice; sum totals here.
- `line_items.csv`: one row per item linked by image SHA-256; no repeated invoice totals.
- `review_audit.json`: approved payloads, source/image hash, raw successful extractions,
  changes, reviewer alias/time, and filenames not exported because they are unapproved.

Only approved saved drafts are exported. Changed drafts require approval again.
A failed API retry preserves the earlier draft, displays the failure, and revokes
approval; it does not claim a fresh successful prediction. Duplicate approved
seller/invoice-number/date keys block the combined export. Select the duplicate and
use **Hủy duyệt hóa đơn này** to exclude it from that ZIP.

The per-invoice legacy CSV still repeats invoice-level totals on each line. Use the
new ZIP's `invoices.csv` for aggregation. CSV text cells are formula-escaped.
Reviewer aliases are not authenticated, audit data is not signed, and drafts survive
only within the Streamlit session. Raw extraction records remain separate from edits;
this is application-level traceability, not tamper-proof regulatory auditing.

## Multi-layout benchmark

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe benchmark.py validate
.\.venv\Scripts\python.exe benchmark.py run --mode replay --split all
Start-Process reports/benchmark-replay-all.html
```

Replay uses deliberately flawed scripted predictions and **never calls a model**.
Its command returns 1 because request and schema failures are intentionally included.
It verifies evaluator behavior, not OCR quality. All 20 examples are original
synthetic invoices, not independently collected supplier documents. References come
from renderer source values, not independent human annotation. Inspect the images
and `data/benchmark/ground_truth.csv` before external claims.

Layouts: classic table, receipt, minimal, landscape with changed column order, and
split summary. Conditions include clean, slight tilt, soft blur and lower contrast;
each case is a different invoice, not three counted copies of one invoice. Edge cases
include zero tax, a zero-price line, absent date/number and a deliberately wrong
printed total. The reference preserves what is printed, even when arithmetic is wrong.
An exact extraction can therefore still require review and be blocked from export.

The first three layout families are dev; the remaining two are held-out test layouts.
All images still share a generator, limited language and schema; this does not prove
generalization to real invoices. Use dev while improving prompts/code, freeze a
version, then evaluate test without tuning against it. Only JPG/PNG are implemented.

Live benchmark (sends images to Groq and uses quota):

```powershell
$invoiceKey = Read-Host "Groq API key" -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $invoiceKey).Password
.\.venv\Scripts\python.exe benchmark.py run --mode live --split dev --max-requests 3 --output reports/live-dev-v2.json
.\.venv\Scripts\python.exe benchmark.py run --mode live --split dev --max-requests 3 --output reports/live-dev-v2.json --resume
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
Remove-Variable invoiceKey -ErrorAction SilentlyContinue
```

`--max-requests` limits **new** calls in that invocation. The default is five. HTTP
429 stops the current invocation after saving progress. There are no automatic API
retries or paid-tier changes. After checking provider quota, `--resume --retry-errors`
explicitly retries failed cases and preserves prior attempts. Successful cases are
not rerun to chase higher scores. Without `--retry-errors`, existing failed rows are
also skipped. Skipped rows do not spend request budget.

Resume refuses a changed model, selection, prompt version, code fingerprint, image,
reference or replay fixture. Use a new output path for a new experiment. One process
should write a given report path. JSON is checkpointed atomically; HTML and error CSV
are refreshed beside it. The report stores raw validated predictions **before human
correction**, case hashes, coverage, full-invoice accuracy, field accuracy, absent
field false-fill counts and per-layout breakdowns.

Field scoring includes all extra/missing item cells and uses minimum-mismatch
one-to-one assignment, so reordering alone does not reduce the score and duplicate
hallucinated rows cannot match the same reference twice. Seller and description
ignore case/whitespace under Unicode NFC; accents stay meaningful. Invoice IDs are
case-sensitive after whitespace normalization. Numbers and ISO dates must match
exactly; there is no numerical tolerance or fuzzy entity matching.

Request/schema failures are not mislabeled as transcription substitutions. They
count against full-invoice success over processed cases. Field metrics condition on
successful schema-valid extractions; report those with coverage and request success,
not alone. Partial runs explicitly show unprocessed cases. Dataset-level conclusions
require completing the chosen selection. Test is tiny, so publish case counts and
failures rather than claiming a general accuracy rate.

CLI exit codes: 0 completed processing without provider/schema errors (field mismatches
can still exist, and a budget-limited run can still be partial); 1 at least one selected
processed case has an extraction failure; 2 command/input/configuration error.

## Live extraction

Use a Groq project with access to a model that supports images and JSON mode.
The default is qwen/qwen3.8-27b. Enter the API key in the sidebar, then choose
**Đọc ảnh bằng Groq**. This sends the prepared image to Groq. Use synthetic or
authorized documents. API access and usage are subject to provider limits.

The model is instructed to preserve printed values and mark unreadable fields
as null. Python validates field types and checks quantity × unit price,
subtotal, and total. A balanced invoice can still contain extraction errors,
so review each field against the image before exporting.

## Legacy single-invoice comparison

```powershell
$invoiceKey = Read-Host "Groq API key" -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $invoiceKey).Password
.\.venv\Scripts\python.exe evaluate.py
Get-Content reports/live.json -Encoding utf8
Remove-Item Env:GROQ_API_KEY
```

The legacy script sends three requests, one per image variant of the same synthetic
invoice. It compares each field and line position with the reference JSON and
lists arithmetic issues separately. Provider errors are reported separately
from extraction errors. Set INVOICE_VISION_MODEL to compare another supported
vision model. Expand the dataset with independent layouts before reporting
general extraction quality.

If one request hits a provider limit, rerun only that variant. The original
report stays in `reports/live.json`.

```powershell
.\.venv\Scripts\python.exe evaluate.py --variant blurred
Get-Content reports/live-blurred.json -Encoding utf8
```

## Scope

### Pilot results

In a live pilot using `qwen/qwen3.8-27b`, the original, blurred, and tilted
versions of one synthetic invoice each matched all 15 reference fields.
The arithmetic checks flagged no discrepancies in those successful runs.
The blurred request initially returned HTTP 429 and was rerun on its own.
The three images share the same invoice layout and values.

### Supported inputs

The prototype queues up to 20 different images and reviews one invoice at a time, with whole-number quantities,
nonnegative VND amounts, one aggregate tax amount and no discounts. It handles
JPG/PNG up to 5 MB and 16 million pixels. PDFs, handwritten forms and fiscal
validation are outside the current scope. Drafts are held in the Streamlit
session rather than a persistent database. Download the reviewed ZIP to retain them.

## Code

- app.py presents the image and editable review form.
- invoice.py defines the schema, arithmetic checks and CSV export.
- vision.py validates the image and calls Groq.
- evaluate.py measures field matches under three image conditions.
- review.py manages saved drafts, approvals, provenance and batch ZIP exports.
- scoring.py aligns rows and accounts for missing, extra and hallucinated fields.
- benchmark.py validates the dataset and evaluates replay/live predictions with resume.
- make_benchmark.py renders the original synthetic benchmark. Regeneration needs a
  Unicode TrueType font; Windows Arial and Linux DejaVu Sans are detected. Rendered
  image hashes can change with font/Pillow versions, so version and inspect regenerated data.
- tests/ exercises the schema, scorer, API error handling, review queue, benchmark and UI.

No new 20-invoice live accuracy result is claimed by this upgrade. Offline tests use
scripted predictions and mocked HTTP. The historical one-invoice pilot above remains
an earlier, narrow result, not validation of the new layout benchmark.
