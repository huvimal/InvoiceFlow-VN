# InvoiceFlow VN

A Vietnamese invoice review prototype. Load a JPG or PNG, extract fields with
an optional Groq vision model, compare them with the image, and export reviewed
line items to a UTF-8 CSV. Amounts are integer Vietnamese dong.

## Run on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8504
```

Open http://127.0.0.1:8504 and choose **Nạp dữ liệu demo**. The bundled image and
reference JSON were created for this project. Demo loads the prepared values.
Change the total from 605000 to 600000 and verify that export is blocked.
Restore 605000, compare the fields with the image, mark the review checkbox,
and download the CSV. Invoice-level totals repeat on each line for context.
Deduplicate invoice totals before aggregating them.

## Live extraction

Use a Groq project with access to a model that supports images and JSON mode.
The default is qwen/qwen3.8-27b. Enter the API key in the sidebar, then choose
**Đọc ảnh bằng Groq**. This sends the prepared image to Groq. Use synthetic or
authorized documents. API access and usage are subject to provider limits.

The model is instructed to preserve printed values and mark unreadable fields
as null. Python validates field types and checks quantity × unit price,
subtotal, and total. A balanced invoice can still contain extraction errors,
so review each field against the image before exporting.

## Compare clean, blurred and tilted images

```powershell
$invoiceKey = Read-Host "Groq API key" -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $invoiceKey).Password
.\.venv\Scripts\python.exe evaluate.py
Get-Content reports/live.json -Encoding utf8
Remove-Item Env:GROQ_API_KEY
```

The script sends three requests, one per image variant of the same synthetic
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

The prototype supports a single invoice at a time, whole-number quantities,
nonnegative VND amounts, one aggregate tax amount and no discounts. It handles
JPG/PNG up to 5 MB and 16 million pixels. PDFs, handwritten forms and fiscal
validation are outside the current scope. Review drafts are kept in the browser
session. Saving the CSV is the export step.

## Code

- app.py presents the image and editable review form.
- invoice.py defines the schema, arithmetic checks and CSV export.
- vision.py validates the image and calls Groq.
- evaluate.py measures field matches under three image conditions.
