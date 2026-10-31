# MED FINDER

## Medicine matching and price comparisons

Medicine alternatives are only returned when the database can verify the same
active ingredients and ingredient strengths, product strength, dosage form,
release type, and package size. The app compares listed prices only for matching
package sizes. Missing medicine data results in no verified alternative.

RxNorm is queried only when a medicine has no local search result. API records
are stored locally. Successful search results are cached for 30 days; empty
results are cached for one day so a temporary API failure does not block retries
for long. RxNorm names alone are not treated as verified ingredient compositions,
and external search prices are not fetched by default.

## Existing database setup

The current `database/medfinder.db` has been migrated without dropping medicine
records. For a fresh checkout or after restoring an older database, run:

```powershell
python migrate_medicine_quality.py
```

The migration adds structured ingredient, release, package, and price provenance
fields. It only marks composition complete when every ingredient and its
strength can be parsed from the source record.

## Prescription upload

The scanner reads printed text from a PNG/JPG/PDF prescription, matches each
line against the local catalog, and lists anything it could not match without
guessing. Unreadable images return an explicit error instead of sample data, and
uploads are deleted after processing.

It needs Tesseract OCR plus the Python bindings:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
python -m pip install -r requirements.txt
```

To use Google Cloud Vision instead for better handwriting support, set a key
before starting the app (1,000 pages/month free, then $1.50 per 1,000 pages).
Without a key, everything is read locally by Tesseract:

```powershell
$env:GOOGLE_VISION_API_KEY = "your-key"
```

With the key set, prescription images are sent to Google; without it they never
leave the machine. Handwriting is still only best-effort, so review every row.

## Tests

Run the focused offline tests with:

```powershell
python -m unittest test_api_fallback_cache test_prescription_ocr -v
```
