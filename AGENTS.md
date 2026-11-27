# MED FINDER — Agent Notes

> Start with `CLAUDE.md`: it holds the current state, the non-negotiable rules, the open
> problems and how to run the project. This file is the dated changelog of every change and
> its measured caveats — read the relevant section when you need the reasoning behind a rule.

## Current working state (2026-10-31)

The changes below are **uncommitted** and were made in a Qoder session. They are present in the
working tree and in the local SQLite database (`database/medfinder.db`, which is git-ignored).

Goal: searching for a medicine must surface cheaper products with the same recorded active
ingredients and strength, without presenting them as therapeutically interchangeable.

## What changed

### 1. ETL: composition repair
- `etl/extract_fields.py`
  - Added `repair_compositions()` and helpers. The source scrape dropped the first ingredient
    name from most compositions (e.g. `(650mg)` instead of `Paracetamol (650mg)`). The name is
    rebuilt from the embedded composition in `Product_Name` / `Type_of_Medicine` using an
    ingredient lexicon learned from intact rows.
  - Fixed column references to the cleaned names `Product_Name` / `Type_of_Medicine`.
  - Added a `pd.NA`-safe text helper.
- `etl/clean_dataset.py`, `etl/build_database.py`
  - Force UTF-8 stdout so the Windows cp1252 console no longer crashes on `✓`.

### 2. Database rebuild
- Rebuilt `database/medfinder.db`: **348,211** medicines, **453,217** ingredient rows,
  **270,780** with complete compositions (previously ~18,128 complete).
- Duplicate source rows are deduped by `product_key`.

### 3. Cheaper-alternatives logic
- `backend/recommendation_service.py`
  - Per-unit price normalization (`_price_per_unit`, `_pack_quantity`) so different pack sizes
    (10 vs 15 tablets) are comparable.
  - `_candidate_rows(..., require_pack_match=False)` to allow different pack sizes.
  - New `same_content` list: identical ingredients + strength + form + release, only when the
    candidate is actually cheaper (per unit, or per pack when units match).
  - New response fields: `same_composition_alternatives`, `same_composition_count`, and
    `savings_reliable` per item (false for stale dataset prices).
  - New status `cheaper_same_composition`; fixed a misleading message when same-ingredient
    products exist but none is cheaper.
  - Deduped alternatives by `product_key`.
  - Verified therapeutic substitutes still require `medicine_equivalence_evidence` rows; the
    table currently has zero rows, so `substitutes` is normally empty.

### 4. UI
- `templates/results.html` — new `#cheaperAlternatives` panel on the search page.
- `static/js/app.js` — `loadCheaperAlternatives()` / `clearCheaperAlternatives()` during search;
  renders price, per-unit price, caution text. The Compare Prices modal renders a
  "Cheaper same-composition options" section and hides savings percentages when prices are stale.
- `static/css/style.css` — styles for the panel.

## Update — live India catalog fallback (2026-10-31, later session)

Goal: when a searched medicine is **not in the local dataset**, fetch it from the India drug
catalog (DrugDB / NRCeS), **save it into `medicines`**, and give the saved record the same
features (alternatives, price comparison, detail page). Also fill incomplete compositions on
demand. All changes below are uncommitted.

### Flow
1. `search_medicines()` searches locally (`medicine_name LIKE`, `brand_name LIKE`, and the new
   normalized `search_norm LIKE`). A local hit returns immediately — no API call.
2. On a miss, `backend/services/drug_catalog_service.py` queries
   `https://drugdb.in/search?q=...&type=medicine&detail=full` and maps each product concept to
   the local schema: structured composition (`Paracetamol (650mg) + ...`), strength, dosage form,
   brand (taken from the product title), drug-type category.
3. Records are saved with `source='api'`, `api_source='DrugDB · NRCeS India drug catalog'`,
   `api_record_id`, `api_fetched_at`, `composition_complete=1`, a recomputed `product_key`,
   `search_norm`, and rows in `medicine_ingredients`. Saved ids are returned directly (they are
   not re-matched with `LIKE`, so hyphenated brands work).
4. Dedupe: an existing `api_source + api_record_id` row wins; otherwise a row with the same
   strength, dosage form, brand and composition returns the existing id (so `Crocin 500` maps
   `Crocin Advance 500mg Tablet` to its existing local row instead of duplicating it).
5. RxNorm stays as the last resort. RxNorm rows have no composition and no price, so they still
   cannot produce alternatives.
6. A query-token filter drops unrelated fuzzy catalog hits before anything is saved.
7. Results of a miss are cached in `api_search_cache` (30 days on success, 1 day when empty).

### Composition enrichment for existing rows
- `get_substitutes()` calls `enrich_medicine_composition()` when a row has
  `composition_complete = 0` and a brand. It looks the brand up in DrugDB, accepts only a record
  with the same dosage form and the same strength tokens (exact brand first, then a single
  prefix-compatible product such as `Metoz` → `Metoz 2.5`), and writes composition,
  `product_key` and `medicine_ingredients`.
- Complete compositions are never overwritten. Attempts are cached per medicine id
  (`api_search_cache` key `enrich:<id>`), so a failure is retried at most daily.
- There is no bulk backfill of the ~77k incomplete rows; enrichment happens on demand.

### Alternatives when the reference has no price
- Catalog-added rows have `price = NULL` (DrugDB provides no price or pack size). The existing
  `same_composition_alternatives` logic requires a reference price, so it stays empty.
- New response fields from `/api/substitutes/<id>`: `same_composition_options` (cheapest
  same-composition products, deduped, sorted by price per unit), `same_composition_option_count`,
  `reference_price_available`; new status `reference_price_unknown`.
- `static/js/app.js` shows these under "Lowest-priced options with the same composition" on the
  search page and in the Compare Prices modal, and explicitly says no saving can be claimed.
  Option ids are removed from the "Same-ingredient catalog matches" list to avoid duplicates.
- The medicine detail page shows a "Live catalog record" badge and "No price is recorded".

### Schema
- `medicines` gains `api_source`, `api_record_id`, `api_fetched_at`, `search_norm`.
  `ensure_catalog_schema()` adds them and backfills `search_norm` once per process
  (keyset batches), so an existing DB upgrades in place.
- `etl/build_database.py` creates the same columns and populates `search_norm` on rebuild.
- `api_search_cache` helpers moved to `backend/services/api_cache.py`.

### Verification (this session)
- `mortrin 650` / `compain 650` (local miss): saved as id 348212 / 348213 with
  `Paracetamol (650mg)`; `/api/substitutes` returns `reference_price_unknown` with 656 options
  (Alice ₹3 / 10 tablets = ₹0.30 per unit, …).
- `Crocin 500` (local miss that has a same-composition local product): added `Crocin 500mg
  Tablet` (348214) and `Crocin Quik 500mg Tablet` (348215), reused the existing
  `CROcin Advance 500mg Tablet` (137294, ₹18); 132 options.
- Enrichment: `Eprisan SR Capsule` (348195) → `Eperisone hydrochloride (150mg)`;
  `Metoz 2.5 Tablet` (348166) → `Metolazone (2.5mg)`.
- Repeat searches hit the saved rows locally (`search_norm`), no second API call.
- Browser-verified: search panel, Compare Prices modal (options + dedupe), detail page badge.
- The live catalog is small: brands it does not know (e.g. `becosules`, `Pletoz`) still return
  no results and nothing is saved.

Files: `backend/services/drug_catalog_service.py` (new), `backend/services/api_cache.py` (new),
`backend/services/medicine_service.py`, `backend/services/external_api_service.py`,
`backend/recommendation_service.py`, `etl/build_database.py`, `static/js/app.js`.

## Update — live pharmacy prices + wider brand coverage (2026-10-31, latest session)

Goal: an API-added medicine must be usable as the reference side of a price comparison, and brand
queries that DrugDB cannot resolve must still find products. All changes below are uncommitted.

### Price source: PharmEasy
- `backend/services/drug_catalog_service.py` scrapes the PharmEasy search page and reads its
  Next.js `__NEXT_DATA__` payload (`productList` + `genericsProductList`). Each item yields
  composition, sale price, MRP, pack text (`10 Tablet(s) in Strip`), strength, form, availability
  and `isRxRequired`. Mapped rows carry `source='api'`,
  `api_source='PharmEasy live catalog'`, `api_record_id=<productId>`,
  `price_source='PharmEasy listing price'`, `price_checked_at=<now>`, and a `pack_size`.
- `search_pharmeasy_catalog(query, limit)` is the second search fallback (after DrugDB, before
  RxNorm). `search_catalog` now retries up to three query variants (full query, strength
  stripped, first long token) and still filters with `_matches_query`.
- `1mg.com` is not usable server-side: its search page returns a client-rendered shell
  (`isFetched:false`, no product cards or JSON-LD, captcha/bot markers). Only PharmEasy is active
  as a pharmacy provider.

### Persisted reference price
- `backend/services/price_enrichment_service.py` (new): `enrich_medicine_price(medicine)` looks up
  the exact listing for a row that has no price and writes it to `medicines.price`,
  `price_source`, `price_checked_at`, `pack_size` via `persist_reference_price` — only when the
  current price is NULL/0, so dataset prices are never overwritten. Attempts are cached
  (`price:<id>`, 7 days on success, 1 day on miss).
- `get_substitutes()` calls it for price-less rows before building comparisons;
  `/api/price-alternatives/<id>` persists the reference listing it finds.
- `save_catalog_medicine()` calls `_store_price_if_missing()` in both dedupe branches, so a later
  provider can price an existing row.

### Match tightening (prevents wrong reference prices)
- `price_comparison_service` collapses ingredient synonyms (paracetamol/acetaminophen, vitamin
  aliases) and numerically normalizes strengths before comparing.
- `_pack_quantity` falls back to parsing `10 Tablet(s) in Strip` when `packQuantityValue` is null.
- `_matches_brand` requires the exact normalized brand (or brand + strength remainder), and
  `_name_matches_product` rejects extra sub-brand tokens — so `Dolopar` no longer inherits
  `Dolo`'s price and `Crocin` does not inherit `Crocin Advance`'s.
- `compare_current_prices` returns "No current listing with the same ingredients, form and pack
  size is available for comparison." when no same-product listing exists.

### Search cache versioning
- `medicine_service._normalized_search_key` prefixes keys with `SEARCH_CACHE_VERSION`
  (`catalog-v2`). Bump it whenever a new provider is added, otherwise an old empty-result row
  (e.g. `becosules` cached on 2026-10-30) suppresses the new fallback for a day.

### UI
- The detail page now says "The price shown is the live listing price from that catalog." when an
  API row has a price, and keeps "No price is recorded for this product." when it does not
  (`static/js/app.js`, `renderMedicineDetail`).

### Verification (latest session)
- `Nofemol 650` (id 348216): reference price persisted as ₹12.41 / 10 tablets;
  `/api/substitutes/348216` → `cheaper_same_composition`, 51 cheaper same-composition products,
  `savings_reliable=false` for stale dataset prices.
- `Becosules` (local 0, DrugDB 0): PharmEasy returns 5 products saved as ids 348217–348221
  (Becosules ₹50.76, Z ₹50.54, Performance ₹153.93, Plus ₹189.94, Women ₹199.62), each with a
  9-ingredient composition and `20 capsules` pack. Repeat search is local (215 ms).
- Browser-verified: Becosules search cards with price provenance, detail page (live-catalog badge,
  ₹50.76, corrected provenance sentence), Compare Prices modal showing the no-listing message.
- Regressions: `Dolo 650` → 640 same-composition alternatives; `Nofemol 650` → 51; `Metformin 500`
  unchanged; `Crocin 500` finds no exact PharmEasy listing, so it keeps
  `reference_price_unknown` with 132 same-composition options.

Files: `backend/services/price_enrichment_service.py` (new),
`backend/services/price_comparison_service.py`, `backend/services/drug_catalog_service.py`,
`backend/services/medicine_service.py`, `backend/services/api_cache.py`,
`backend/routes/medicine_routes.py`, `backend/medicine_data.py`, `static/js/app.js`.

## Update — prescription upload module (2026-10-31, later session)

Goal: prescription upload must return real OCR output and catalog matches, never demo data.
All changes below are uncommitted.

### Environment (required on every machine)
- Tesseract 5.4 installed via `winget install --id UB-Mannheim.TesseractOCR -e` (path
  `C:\Program Files\Tesseract-OCR\tesseract.exe`).
- `pytesseract==0.3.13` and `PyMuPDF==1.24.10` added to `requirements.txt`.
- If the engine is missing, `/api/ocr` returns an explicit failure instead of guessing.

### OCR engine selection (Google Vision optional)
- Set `GOOGLE_VISION_API_KEY` to use Google Cloud Vision (`DOCUMENT_TEXT_DETECTION`,
  handwriting-capable). The image is posted directly to
  `https://vision.googleapis.com/v1/images:annotate` with the key in `X-Goog-Api-Key`, images are
  capped at 2400px on the long side, and the request times out after 20 s.
- Without the key everything stays local on Tesseract. With the key, any Google failure (quota,
  network, bad key) is logged and the page silently falls back to Tesseract, so a scan never fails
  only because the cloud call failed.
- Scanned PDF pages are rasterized and sent per page; text-layer PDFs still use the embedded text
  and never leave the machine.
- The API response now includes `provider` (`google-vision`, `tesseract`, `pdf-text-layer`, or a
  `+` combination); the "Extracted text" block shows it, and the "How it works" copy changes to
  say images are sent to Google Cloud Vision when the key is configured.
- Privacy: with the key set, prescription images are health data sent to a third-party processor.
  Enable it only with consent/notice appropriate to your deployment.

### Real OCR pipeline (`backend/ocr_service.py`, rewritten)
- Accepts PNG/JPG/WEBP/BMP/TIFF and PDF; rejects other extensions.
- PDFs use the embedded text layer when present, otherwise up to 3 pages are rasterized and
  OCR'd.
- Images are EXIF-corrected, converted to grayscale, upscaled when narrower than 1200px and
  autocontrasted before OCR (`--oem 3 --psm 6`).
- Uploads are stored under `uploads/` with a UUID name and deleted in `finally`; the response
  only carries the sanitized original filename, extracted text and matches.
- The old hard-coded demo medicines ("Paracetamol 650mg … confidence 94") are gone. Too little
  text returns `success: false` with a readable reason.

### Conservative matching
- Matching is local-only (no catalog/RxNorm calls) so a scan never adds records.
- For each printed line the strength is parsed, then the token is searched as `"<name> <number>"`
  first (`Dolo 650`) and as the plain token second, with limit 15 so single-ingredient products
  are not crowded out by combinations.
- Ranking: strength match (exact strength column or the number appears in the product name) >
  exact first-word name/brand > printed dosage-form hint.
- Each result carries `medicine_id`, `match_type` (`exact`/`partial`), `strength_match`
  (`true`/`false`/`null`), `dosage_text` (`1-0-1`, `OD`, `after meals`, `x5 days` …),
  `source_line` and catalog fields.
- Lines with no catalog match are returned in `unmatched_lines` (with a search `query`) instead
  of being dropped or guessed.

### UI (`templates/scan_medicine.html`, `static/js/app.js`, `static/css/style.css`)
- Detected rows show match badge, strength/form, dosage, the printed line, `View` (medicine
  page), `Search`, and `Remove` (client-side review). Fake confidence percentages are gone.
- New "Read but not matched" panel and a collapsible "Extracted text" block.
- Fixed a pre-existing CSS bug: `.scan-preview { display: grid }` overrode the `hidden`
  attribute, so the preview panel was visible on page load. Added
  `[hidden] { display: none !important; }`.
- Static asset cache version bumped in `templates/base.html`.

### Tests and verification
- `test_prescription_ocr.py` (6 tests): strength/dosage extraction, unknown medicine listed as
  unmatched, unreadable text failure, unsupported type, upload deletion, no sample medicines.
- Full suite: `python -m unittest test_api_fallback_cache test_prescription_ocr` → 17 tests pass.
- Generated printed prescription image and text PDF through `/api/ocr`: Dolo 650 Tablet,
  Augmentin 625 Duo Tablet, Pantoprazole 40mg Tablet, each with strength and dosage; an unknown
  `Zorblax` line appears under "Read but not matched". Remove and Scan another verified in the
  browser; processed files left nothing in `uploads/`.

Files: `backend/ocr_service.py`, `backend/routes/medicine_routes.py` (unchanged route),
`templates/scan_medicine.html`, `static/js/app.js`, `static/css/style.css`,
`templates/base.html`, `requirements.txt`, `test_prescription_ocr.py`.

### Caveats
- Handwritten prescriptions are unreliable; printed text works well. The UI says so.
- Unit-less brand strengths (`Dolo 650`, `Augmentin 625 Duo`) are read from the product name, and
  common OCR confusions (`4omg`, `40wg`, digit `O`) are normalized before matching.
- Measured 2026-10-31 on rendered samples: printed control 4/4 medicines with correct strength;
  neat handwriting-style fonts 11/12; cursive/messy handwriting (`Ink Free`) 1/4.
- Matching uses the local database only, so a medicine absent from the catalog is listed as
  unmatched rather than fetched.
- PDF processing is capped at 3 pages; dosage parsing is heuristic and may miss free text.
- No prescription history is stored (explicitly out of scope for this checkpoint).

## Update — price reliability gate (2026-10-31, step 1)

Goal: a dataset price must never be presented as "cheaper" or "save X%". Only a current
listing price on both sides can support a saving claim. All changes below are uncommitted.

### Backend (`backend/recommendation_service.py`)
- `get_substitutes()` now splits same-composition products into:
  - `same_composition_alternatives` — only products where the reference and candidate both
    have a price checked within 30 days (`price_checked_at`) and the candidate is cheaper per
    unit (or per pack when packs match). Every item has `savings_reliable: true`.
  - `same_composition_options` — every other same-composition product, ranked by catalog
    price per unit, with no savings fields.
- `reference_price_available` and the new `reference_price_is_current` explain why no saving
  can be claimed. New statuses: `catalog_price_unverified` (the reference price is an old
  dataset price, or no candidate with a current listing price is cheaper) and
  `reference_pack_unknown`.
- Products without a pack size are excluded from both lists, because a price cannot be
  normalized per unit or compared per pack against an unknown pack.
- Duplicate rows are collapsed by `product_key` and then by normalized
  name/brand/strength/form/pack/release, keeping the cheapest copy (Dolo 650: 926 priced
  candidates → 655 unique products).

### UI (`static/js/app.js`, `templates/base.html` asset version)
- Search panel: "Save on the same content" appears only when a reliable cheaper listing
  exists; otherwise it says "Same-composition catalog options" and each item reads "Catalog
  price (unverified)". Three messages cover no recorded price, a stale reference price, and a
  current price with no cheaper current listing.
- Compare Prices modal: same rules; the options section is no longer called "Lowest-priced"
  and carries "Catalog price (unverified)".

### Tests (`test_api_fallback_cache.py`)
- Stale prices produce `catalog_price_unverified` with no savings fields.
- Current prices on both sides still produce `cheaper_same_composition` with savings.
- Rows without a pack size are excluded from the option list.

### Verification (step 1)
- Dolo 650 (7087): `catalog_price_unverified`, 0 cheaper claims, 655 catalog options
  (Alice ₹3 / 10 tablets = ₹0.30 per unit first). Search panel and modal checked in a browser.
- Nofemol 650 (348216, current PharmEasy price): 655 catalog options; the UI says no cheaper
  current listing was found and makes no saving claim.
- Crocin 500 (348214, no price): `reference_price_unknown`, 128 options.
- 30 random priced dataset medicines: all `catalog_price_unverified`, zero unreliable claims.
- Full focused suite: `python -m unittest test_api_fallback test_api_fallback_cache
  test_prescription_ocr test_clean` → 23 tests pass.

## Update — ingredient-aware local search (2026-10-31, step 2)

Goal: a generic ingredient search must find brand-named products, not only products whose name
contains the ingredient. All changes below are uncommitted.

### Problem measured
`Diclofenac` matched 31 products by name/brand but 8,617 ingredient rows (8,407 distinct
products) carried diclofenac in their structured composition; the search page showed only 2
results.

### Changes
- `backend/services/medicine_service.py`
  - `_search_local()` also matches `medicine_ingredients.ingredient_name_norm LIKE '<key>%'`,
    restricted to products with exactly one distinct recorded ingredient so combination products
    do not crowd generic searches. Uses an `IN (SELECT …)` subquery: 212–251 ms warm for sparse
    queries vs 365–374 ms with the earlier `EXISTS` form.
  - `_search_ingredient_key()` treats the query as an ingredient only after stripping strength,
    unit and dosage-form words; `+`, `/`, `and` queries and keys under 4 characters are ignored.
  - Optional strength filter (`_search_strength_pattern`): `Metformin 500` keeps 500 mg and drops
    1000 mg rows.
  - `form_order` ranks tablet → capsule → syrup → … → injection after exact name/brand matches
    (so `Voveran 50mg Tablet` precedes `Voveran Gel`).
  - `_dedupe_search_records()` collapses identical name/brand/strength/form rows, keeping the
    cheapest priced copy. Pack size is excluded because search cards do not show it.
  - Local, catalog and RxNorm result paths are all deduped and trimmed to `limit`.
- `backend/database.py`: `execute_query` / `execute_non_query` close the connection in `finally`.
  A failed query used to leave a SQLite handle open, which made Windows temp-DB cleanup fail in
  tests.
- `test_api_fallback_cache.py`: generic ingredient search (brand found by composition,
  combination excluded), strength filter, duplicate collapse.
- `test_prescription_ocr.py`: fixture now creates `search_norm`, `api_source` and
  `medicine_ingredients`, matching the real schema.

### Verification
- `Diclofenac` → 20 results, all single-ingredient products; warm 251 ms. `Metformin 500` → 20
  rows, all 500 mg. `Atorvastatin 10` → 20 rows, all 10 mg. `Dolo 650`, `Montek LC`,
  `Crocin 500` unchanged.
- Compare Prices for `Diclofenac Sodium Injection` (38530): `no_verified_alternatives`; 761
  same-ingredient products, none with a pack size, so the modal explains that no per-unit price
  comparison can be made instead of claiming savings. The search-page panel stays hidden because
  there are no pack-size options.
- Full focused suite: `python -m unittest test_api_fallback test_api_fallback_cache
  test_prescription_ocr test_clean` → 27 tests pass.

### Caveats
- Matches reflect the *recorded* composition. Some source rows are incomplete (e.g.
  `Alfapsin D 50mg/50000IU Tablet` records only `Diclofenac (50mg)` despite the combination brand
  name), so they appear as single-ingredient products until enriched.
- Injection rows in the dataset carry no pack size, so they cannot support per-unit price
  comparisons.

## Update — DrugSetu enrichment (2026-11-10)

Goal: fill missing composition and pack size for dataset rows, and record MRP as an
attributable reference price, without touching the retail comparison logic.

### What was added
- `backend/services/drugsetu_service.py` — searches `https://api.drugsetu.com/v1` with
  `X-API-Key` and writes only missing fields.
- `backend/services/enrichment_worker.py` — one background daemon thread, one medicine at a
  time. `GET /api/medicine/<id>` queues a row when composition, pack size or MRP is missing; the
  response renders the current data and the next view shows the filled fields.
- Schema: `medicines.mrp REAL`, `medicines.price_type TEXT`, `medicines.composition_source TEXT`
  added in `ensure_catalog_schema()` and in `etl/build_database.py`.
- `test_drugsetu_enrichment.py` — 11 offline tests (matching, pack parsing, writes, caching).

### Matching rules (deliberate)
- The first token of `brand_name` must equal the first token of the product name.
- Dosage-form families must match (`tablet`, `capsule`, `injection`, `cream`, …). This is
  mandatory: 4 of 13 sampled hits were the wrong form.
- Strength pairs parsed from `strength` and from the returned composition must be identical.
- Two candidates with different compositions for the same brand/form/strength are ambiguous and
  skipped.
- Complete compositions, existing pack sizes and existing MRP are never overwritten.

### Price rules
- MRP goes to `mrp` with `price_type='mrp'`; `price`, `price_source` and `price_checked_at` are
  untouched, so the savings gate cannot fire on an MRP.
- `composition_source` records that DrugSetu supplied the composition.

### Transport
- 2 s minimum spacing between calls; three attempts on a burst 429.
- Missing/rejected key → `unavailable`; trial/quota exhaustion → `quota_exceeded`; transport
  failures → `failed`. None of these are cached, so a fixed key or plan retries on the next view.
- A completed no-match is cached for 7 days, a successful enrichment for 30 days
  (`api_search_cache` key `drugsetu:<id>`).

### Measured coverage (50 products, 2026-11-10)
- 25 known rows: 10 search hits, 10/10 same form, 9 same composition, 1 conflict
  (`Uniprogestin 250`: ours `Progesterone`, DrugSetu `Hydroxyprogesterone` — ours looks wrong).
- 25 random incomplete rows: 13 hits, 4 rejected on form → 9 usable fills (36%).
- Every hit returned a pack size and an MRP.
- The known-row hit rate is understated: the probe sent full combination strength strings as
  query text (`Becosules 150/100/50/...`).

### Blocker
- The provided key is a 100-request trial and is exhausted (`Trial Limit Reached`). No real row
  has been enriched end-to-end yet; the write path is verified by mocked tests only. Buy a plan,
  set `DRUGSETU_API_KEY`, restart, then open an incomplete medicine to verify.

## Update — Jan Aushadhi reference price import (2026-11-13, uncommitted)

Goal: give the app a dated, per-product price that can actually be cheaper than the
searched brand. NPPA ceilings cannot do this (the ceiling is per formulation, identical
for every brand); the PMBJP list prices an actual generic product.

### Script (`etl/import_jan_aushadhi.py`, new)
- Reads the PMBJP product/MRP PDF with PyMuPDF `find_tables()`; the table columns are
  Sr.No, Drug Code, Generic Name, Unit Size, MRP.
- Parses each generic name into ingredient + strength (every component must carry a
  strength, otherwise the row is skipped), derives the dosage form, and converts the
  pack MRP to `price_per_unit`.
- Writes to a new table `reference_prices` with `price_kind='reference_generic'`,
  `source='Jan Aushadhi (PMBJP)'`, `source_edition`, `observed_at` (only when
  `--edition-date` is given), and a `composition_key` of
  `<form>|<ingredient:strength>+...` built with the same `normalize_measure` /
  `normalize_medication_text` functions as `medicine_ingredients`, so a reference price
  can be joined to the catalogue.
- Unique on `(source, source_edition, drug_code)`, so re-importing an edition updates
  in place. `--replace` clears the edition first.
- Cursor in `backfill_state` under `jan_aushadhi:v1`; the cursor only advances past a
  fully consumed page, so `--limit` and Ctrl-C resume without skipping rows.
- `--dry-run` writes nothing (not even the table) and leaves the cursor alone.
- Skipped rows are reported by reason: unparseable name, zero MRP, ambiguous pack size
  ("1's Tin 250g" has both a pack count and a net weight), short row.

### Measured on the 2025 list (63 pages, 2,485 rows)
- 1,721 parsed and importable; 629 join the catalogue by form + composition
  (623 distinct compositions).
- 439 unparseable names — dominated by surgical/consumable items (janaushadhi branded
  kits, polypropylene mesh, endotracheal tubes, suction catheters) and multivitamin /
  vaccine entries that carry no per-ingredient strength. Skipping them is correct.
- 297 rows have MRP 0.00 in the source ("not priced").
- 26 ambiguous pack sizes.
- Atorvastatin 10 mg = ₹8.80 per 10's (₹0.88/tablet) and 20 mg = ₹12.10 per 10's
  (₹1.21/tablet), against NPPA ceilings of ₹5.09 and ₹12.86 per tablet
  (S.O. 1575(E), 25.03.2026).
- Verified against a throwaway database: 1,721 rows written, second run leaves 1,721
  with 0 duplicates, full run sets cursor 63, resume reads 0 rows.

### Caveats
- The PDF used for verification is a third-party mirror
  (`gpvdspharma.com/.../Jan-Aushadhi-Drug-Code-List-2025.pdf`); mirrors are known to
  corrupt decimals. The official list is a JS app (`janaushadhi.gov.in`) and an
  Angular/ASP.NET form (`pmbi.co.in`), so download the PDF by hand from the official
  site and pass it with `--pdf`.
- PMBI publishes editions, not row dates. Without `--edition-date` the price has
  `observed_at = NULL` and must not satisfy a freshness check.
- A reference price is not evidence of equivalence; it is only a price for the same
  recorded composition and form.
- Re-run after `python -m etl.build_database`, which replaces `medicine_ingredients`
  and therefore invalidates every `composition_key`.
- Nothing in the app reads `reference_prices` yet; the search/comparison UI is unchanged.

Files: `etl/import_jan_aushadhi.py` (new).

## Update — reference prices in the alternatives response (2026-11-14, uncommitted)

Goal: surface a dated published generic price next to the catalog alternatives, without
turning it into a second kind of "cheaper brand" claim. All changes below are uncommitted.

### Shared form key (`backend/medicine_data.py`)
- `normalize_dosage_form()` + `_DOSAGE_FORM_KEYWORDS` collapse a dosage-form string into
  `tablet`, `capsule`, `oral_liquid`, `injection`, … and `COUNTABLE_DOSAGE_FORMS` marks the
  forms with a countable pack unit.
- `etl/import_jan_aushadhi.py` now imports those helpers instead of keeping its own copy, so the
  importer and the API build identical keys. A dry run still parses 1,721 rows and matches 629.

### Response (`backend/recommendation_service.py`)
- `_composition_key(medicine, signature)` builds `<form family>|<ingredient:strength>+...` from
  the *same* normalized ingredient rows used for candidate matching, so the join is by recorded
  composition, not by name.
- `_reference_price_options()` reads up to 20 rows for that key, cheapest per unit first, and
  returns `reference_price_options` plus `reference_price_option_count`. `_reference_table_ready()`
  makes a database without the table (fresh clone, tests) return an empty list instead of failing.
- Each option carries `source`, `edition`, `product_name`, `composition`, `pack`, `price`,
  `price_per_unit`, `unit`, `price_kind`, `observed_at`, `price_is_current` and
  `match_classification`. When the reference product has a per-unit price in the same unit it
  also carries `reference_price_per_unit` and `difference_per_unit`.
- Freshness: `REFERENCE_PRICE_MAX_AGE_DAYS = 400` (`_fresh_within`), separate from the 30-day
  listing window. An edition is not a live listing, but an undated or year-old one still cannot
  support a saving.
- `savings_amount` / `savings_percent` / `savings_basis` are set **only** when the searched
  product's price is a current listing (30 days) *and* the list price carries a recent
  observation date. Otherwise only the arithmetic difference is reported.

### UI (`static/js/app.js`, `static/css/style.css`, `templates/base.html`)
- Search page panel: a "Jan Aushadhi list price" block under the catalog options, up to three
  entries, with product, composition, pack, list date and per-unit price. The block also renders
  when a medicine has no priced catalog alternative at all.
- Compare Prices modal: a "Jan Aushadhi list price for the same composition" section in both the
  empty and the verified render paths, showing the per-unit difference against the searched
  product's recorded price, and a savings badge only when the backend supplied one.
- Both places state that the price belongs to a generic product, not the searched brand, and that
  it does not show the products can be substituted.
- `formatDateValue()` added next to the other formatters; assets bumped to `v=20261114-1`.

### Verification
- On a copy of the real database with the 2025 list imported: Atorvastatin 20mg (117090) →
  `reference_price_option_count: 1`, `Atorvastatin Tablets IP 20mg`, `10's`, ₹12.10,
  ₹1.21/tablet, difference ₹0.79/unit, `price_is_current: false`, no savings fields.
  Dolo 650 (7087) → `Paracetamol Tablets IP 650 mg`, `15's`, ₹15, ₹1.00/tablet vs ₹2.13/tablet
  recorded, difference ₹1.13/unit, no saving claim.
- Browser-verified on a server pointed at the copy: the search panel shows the block under six
  catalog options, and the Compare Prices modal renders the section for Atorvastatin with no
  console errors. Existing catalog-option behaviour (Dolo 650) is unchanged apart from the block.
- New tests in `test_api_fallback_cache.py`: no table → no options; a capsule product does not
  join a tablet key; an old edition reports a difference but no saving; a recent edition plus a
  current reference price yields 80% for a ₹10 → ₹1 per-capsule gap. 55 tests pass across
  `test_api_fallback`, `test_api_fallback_cache`, `test_prescription_ocr`, `test_clean`,
  `test_drug_catalog_client`, `test_backfill_compositions`; 11 more in `test_drugsetu_enrichment`.

### Caveats
- The join needs a complete recorded composition, a dosage form, and a matching imported edition.
  Only 629 of the 1,721 imported rows join today; incomplete catalog rows get nothing.
- Price differences are arithmetic on the searched product's *recorded* price, which is usually an
  undated dataset price; they are labelled as a difference, never as a saving.
- `database/medfinder.db` now carries the 2025 edition: 1,721 rows imported on 2026-11-14 from
  `datasets/Jan-Aushadhi-Drug-Code-List-2025.pdf` (a third-party mirror of the PMBI list, passed
  with `--source-url`), `observed_at = 2025-04-01`, 629 rows join the catalogue.
- Every imported price is labelled "over a year old" or "list date not published" until a newer
  edition is imported; none of them can currently support a saving claim.

Files: `backend/medicine_data.py`, `backend/recommendation_service.py`, `etl/import_jan_aushadhi.py`,
`static/js/app.js`, `static/css/style.css`, `templates/base.html`, `test_api_fallback_cache.py`.

### Still open
- Import a newer edition when PMBI publishes one (the 2025 list is already over a year old).

## Update — safety blocklist (2026-11-14, uncommitted)

Goal: never offer a product as a cheaper same-composition option when the switch itself is a
clinical decision. All changes below are uncommitted.

### Rules (`backend/medicine_data.py`)
- `NARROW_THERAPEUTIC_INDEX` — 25 curated ingredient terms (warfarin, acenocoumarol,
  levothyroxine, lithium, digoxin, phenytoin, carbamazepine, valproate/valproic acid,
  theophylline, tacrolimus, ciclosporin, mycophenolate, methotrexate, colchicine, clozapine, …),
  matched as substrings of the normalized ingredient name so salts and combination products are
  caught.
- `SUPERVISED_DOSAGE_FORMS = {injection, inhaler}` — forms where a cheaper brand is not a
  self-service choice.
- `is_modified_release(*values)` — true when the release column is set, when a name carries
  XR/ER/SR/CR/MR/XL/DR/EC/LA/OD/TR, or when it says extended/sustained/controlled/modified/
  prolonged/slow/delayed release, retard, depot or osmotic.
- `restricted_reason(medicine, ingredient_names)` returns a clause for the message
  ("it is a modified-release formulation", "it is an injectable or inhaled form",
  "it contains a narrow therapeutic index ingredient") or None.

### Behaviour (`backend/recommendation_service.py`)
- A restricted **reference** returns status `restricted_class` with
  `restricted_class_reason`; same-composition alternatives, catalog options, same-ingredient
  matches, verified substitutes, and Jan Aushadhi reference prices are all left empty.
- `_candidate_rows()` also drops candidates whose name marks modified release even when their
  `release_type` column is empty — the hole where an `… SR Tablet` could pass as a plain
  equivalent.
- UI: the Compare Prices modal titles the empty state "Cheaper options are not shown" and shows
  the message. The search-page panel stays hidden (no lists to show); nothing is silently
  mislabelled. Assets bumped to `v=20261114-2`.

### Measured on the real database
- 42,606 of 270,793 complete rows are now restricted: 24,418 modified-release, 17,829
  injectable/inhaled, 359 narrow-therapeutic-index.
- `Cofarin 1mg Tablet` (warfarin) → `restricted_class`, message names the narrow therapeutic
  index; `Accumet 1 GM Tablet SR` (159681) → `restricted_class`, modified release; both with zero
  lists and zero reference prices.
- Unchanged: Dolo 650, Nofemol 650, Metformin 500, Crocin 500 (no restriction, same option
  counts as before). 69 tests pass across the seven focused modules.

### Caveats
- Substring matching also catches rows where the manufacturer name was glued to the ingredient
  (`aviatriscompanyciclosporin`), which is correct — same drug.
- `EC` and `TR` are blocked as abbreviations; a brand that merely contains them as a standalone
  token is excluded conservatively.
- The restriction is calculated from recorded composition and form only. A row with an
  incomplete composition can still slip through the NTI check.
- Search results themselves are not flagged; only the alternatives and price sections are
  suppressed.

Files: `backend/medicine_data.py`, `backend/recommendation_service.py`,
`static/js/app.js`, `templates/base.html`, `test_api_fallback_cache.py`.

## Update — manufacturer-prefix repair in compositions (2026-11-15, uncommitted)

Goal: a searched brand whose source composition starts with the manufacturer (e.g.
`Cure Healthcare Naproxen (500mg) + Domperidone (10mg)`) records the ingredient as
`curehealthcarenaproxen`, so it can never match cleanly recorded products and gets no
alternatives at all. `Naxdom 500` was the reported case: zero options.

### Rule (`etl/repair_ingredient_prefixes.py`, new)
- A phrase is learned as a manufacturer name only when it is a separate word (or words) in
  front of **>= 3 distinct** ingredients that the catalog records often (>= 25 rows). 168
  phrases qualify.
- A name is rewritten only when the learned phrase is a word-boundary prefix **and** the
  remainder is itself a recorded ingredient name.
- A multi-word name whose full normalized form is already a recorded ingredient is never
  split, which protects real drug names that look like prefix + drug (`levocetirizine`,
  `methylprednisolone`). Verified: `levocetirizine` rows 8,477 -> 8,649 (absorbed the prefixed
  spellings), none lost; `methylprednisolone` 1,910 -> 1,942.
- `repair_component()` keeps the strength text and trailing markers
  (`Cure Healthcare Naproxen (500mg)ADD` -> `Naproxen (500mg)ADD`).
- Writes `medicine_ingredients.ingredient_name` / `_norm`, the medicine `composition` text,
  and recomputes `product_key`. `search_norm` (name + brand) is untouched.
- Dry run by default; `--apply` writes; `--sample N` prints rewrites. Idempotent — a second
  run finds nothing.
- This runs on the built database, so re-run it after `python -m etl.build_database`, which
  rebuilds these tables from the still-polluted source dataset.

### Measured on a copy of the real database
- 6,118 medicines repaired (6,118 ingredient rows, 168 phrases). Largest phrases:
  `slaboratories` 572, `pharmaceuticals` 463, `research` 423, `chemicals` 210,
  `careincorp` 200.
- `Naxdom 500 Tablet` (70888): `Cure Healthcare Naproxen (500mg) + Domperidone (10mg)` ->
  `Naproxen (500mg) + Domperidone (10mg)`; `/api/substitutes/70888` goes from no options to
  `catalog_price_unverified` with **73** same-composition options (Pandex 10 tablets Rs 69,
  Rs 6.90/tablet, first).
- An earlier draft stripped prefixes without checking the remainder: 6,834 rows, but it also
  rewrote names whose remainder the catalog does not record. The apply-time guard was added
  for that reason (716 fewer rows).
- Tests: `test_repair_ingredient_prefixes.py` (6) covers phrase learning, the unknown-remainder
  guard, the `levo`/`methyl` protection, marker-preserving rewrites, idempotency and
  read-only planning. Focused suite: 64 tests pass.

### Caveats
- Only compositions where the manufacturer is separated by a space can be recognised; a fully
  glued `Curehealthcarenaproxen` (one word in the source) is not repaired.
- The phrases are learned from the current data, so a manufacturer that appears in front of
  fewer than 3 different ingredients is left alone.
- Rewriting `composition` drops nothing else: only the ingredient words before the first `(`
  are replaced; components without a parenthetical are untouched.
- Not yet applied to `database/medfinder.db` — the numbers above come from a copy.

Files: `etl/repair_ingredient_prefixes.py` (new), `test_repair_ingredient_prefixes.py` (new).

## Update — home search suggestion dropdown (2026-11-15, uncommitted)

Goal: both search boxes (home hero and results toolbar) offer a type-ahead list of medicines whose
price comparison actually works. The list is populated before anything is typed and narrows to the
typed prefix, including a single letter, without changing free-text search (typing a query and
pressing Enter still searches everything).

### "Fully functional" definition (`suggest_functional_medicines`)
A medicine is suggested only when all of these hold:
- `composition_complete = 1` and a non-empty `brand_name`;
- `restricted_reason()` is None (no narrow-therapeutic-index ingredient, not an
  injection/inhaler, not modified release);
- at least one other product records the **same ingredient set** (same count, no missing
  ingredient), same strength, same dosage form, same release type, and carries a price **and**
  a pack size (`_PRICED_OPTION_SQL`). Whether that price is current only sets the label, it
  does not gate the suggestion.

That is exactly the condition that makes `/api/substitutes` return a cheaper-or-catalog option
list; a medicine whose composition has no priced sibling (e.g. `Becosules`, a lone brand) is
never suggested.

### Endpoint (`GET /api/search-suggestions?q=`, any length, max 8 items)
- Empty `q` returns a starter recommendation list (one item per `SUGGESTION_SEEDS` brand:
  dolo, crocin, calpol, combiflam, augmentin, azithral, montek, zerodol — all verified to
  resolve on the real database), so the box has content before anything is typed.
- Any non-empty query, including a single letter (`m`), matches `search_norm GLOB '<key>*'`
  (name + brand prefix). Strength, unit and punctuation are stripped by
  `normalize_medication_text`, so `dolo 650` matches `dolo650`.
- Up to `SUGGESTION_CANDIDATES = 200` candidates, then at most `SUGGESTION_SCAN_LIMIT = 60`
  priced-sibling checks or `SUGGESTION_SECONDS = 0.5` of wall clock (`time.monotonic()`), then
  the matches are deduped with `_search_duplicate_key`.
- Ranking: exact brand match, then brand prefix, then shorter name.
- Each item: `id, name, brand, strength, form, pack, price, price_per_unit, unit,
  price_is_current, price_label` (`"Catalog price"` when the price is not a current listing).
- `_priced_option_cache` memoises the priced-sibling check per medicine id in-process.
- Two indexes are created by `ensure_catalog_schema()` (and by the ETL): `idx_search_norm`
  (prefix match; without it SQLite prefers a full scan) and `idx_group_price`
  (`strength COLLATE NOCASE, dosage_form COLLATE NOCASE, price, composition_complete`) — the
  `lower(col)=lower(?)` form of that predicate used the index too poorly.

### UI (`templates/index.html`, `templates/results.html`, `static/js/app.js`, `static/css/style.css`)
- Both search boxes have the dropdown: the home hero and the results-page toolbar. The
  results-page form carries `data-suggest-inline`, so choosing an item runs the in-page search
  (the form emits `medfinder:search` and `setupSearchPage` calls `runSearch`) instead of
  navigating; the home hero navigates to `/search?q=<name>`.
- The input is a combobox (`role=combobox`, `aria-expanded`, `aria-controls`,
  `aria-activedescendant`); the listbox is `<div class="search-suggest" role="listbox" hidden>`
  inside the form (`#heroSuggestions`, `#resultsSuggestions`).
- Focusing an empty box fetches the starter list; every keystroke fetches after a 220 ms
  debounce, with a stale-response guard. The pending debounce is cancelled when an item is
  chosen or a search is submitted (`debounce()` now returns a `.cancel()`), otherwise the panel
  reopened a moment after the click.
- ArrowUp/ArrowDown move `aria-selected`, Escape closes and keeps the typed text
  (`preventDefault`, otherwise `type=search` clears the field), Enter opens the active item or
  runs the normal search.
- `.hero-search` and `.search-toolbar` need `z-index: 5` (and `position: relative`); without it
  the panel sits under the cards, because the `.glass` cards are painted later in the document.
- On the results page the live-search debounce (280 ms, `>= 2` chars) is cancelled when a
  suggestion is chosen, so the in-page search runs once.

### Measured on `database/medfinder.db`
- 348,237 medicines, 270,803 complete, 269,246 with a matchable composition; 42,577 restricted;
  226,669 eligible; **190,850 (84.2%)** have at least one priced same-composition product.
  Zero possible saving claims (only 6 rows carry a current listing price).
- Warm responses: empty `q` 46 ms (8 starter items), `m` 79 ms, `dolo` 79 ms, `metformin` 15 ms
  (empty: brands start with `met`, not `metformin`).
- Browser-verified (viewport 640 px wide): focusing the empty hero box lists the 8 starter
  medicines with pack, per-unit price and "Catalog price"; typing `m` narrows the list to eight
  `M*` medicines only (`M2Day 4 Tablet`, …); ArrowDown+Enter opens
  `/search?q=Met%20400mg%20Tablet`. On `/search` the toolbar shows the same list, typing `m`
  narrows it, and clicking an item runs the in-page search (one navigation entry, 1 result)
  without reloading. `metformin` lists nothing and Enter still searches (20 results); Escape
  closes the panel and keeps the typed text.
- Tests: `test_search_suggestions.py` (7) — priced sibling required, per-unit/provenance
  fields, price without pack size is not a comparison, warfarin never suggested, single-letter
  prefix matches, unknown query returns [], empty/whitespace query returns the starter list in
  seed order, a fixture with no seed brand returns no starter items.

### Caveats
- A suggested medicine needs a priced *sibling*; products that exist only once in the catalog,
  or whose composition is incomplete, are never listed.
- Prices are dataset prices unless `price_checked_at` is set, so the label reads
  "Catalog price" (never a saving).
- The prefix match is by name/brand, so an ingredient word (`metformin`) suggests nothing even
  though the results page finds products by composition.
- The starter list is a fixed seed list, so it shows one item per resolvable seed brand (8 today)
  rather than a ranking of the whole catalog.
- `idx_group_price` is a partial-order index: it serves `strength`/`dosage_form` lookups in
  that column order only.
- The first request on a database without the two indexes builds them (several seconds).

Files: `backend/services/medicine_service.py`, `backend/routes/medicine_routes.py`,
`backend/services/drug_catalog_service.py`, `etl/build_database.py`, `templates/index.html`,
`templates/results.html`, `static/js/app.js`, `static/css/style.css`, `templates/base.html`,
`test_search_suggestions.py` (new).

## How to run

```bat
REM Full ETL (only needed to rebuild the database)
python -m etl.build_database

REM App
venv\Scripts\python.exe -m backend.app
```

App runs at http://127.0.0.1:5000. Restart it after Python changes; the database is read live.

Repair manufacturer names glued onto ingredients (re-run after every `etl.build_database`):

```bat
venv\Scripts\python.exe -m etl.repair_ingredient_prefixes
venv\Scripts\python.exe -m etl.repair_ingredient_prefixes --apply
```

Dry run by default; `--apply` rewrites `medicine_ingredients`, `composition` and `product_key`.

Optional DrugSetu enrichment (fills missing composition, pack size and MRP):

```bat
set DRUGSETU_API_KEY=your-key
venv\Scripts\python.exe -m backend.app
```

The key is read from the environment at lookup time; no key is committed. Without it the
background enricher does nothing.

Fill missing compositions from the free DrugDB catalog (resumable, no key required):

```bat
venv\Scripts\python.exe -m etl.backfill_compositions --status
venv\Scripts\python.exe -m etl.backfill_compositions --sample 60 --dry-run
venv\Scripts\python.exe -m etl.backfill_compositions
```

`--dry-run` looks up and matches but writes nothing and leaves the cursor alone. An optional
`DRUGDB_API_KEY` is sent as `X-Api-Key`; without it the endpoint still answers.

Import the Jan Aushadhi (PMBJP) reference price list (download the PDF from the official
site by hand; no key required):

```bat
venv\Scripts\python.exe -m etl.import_jan_aushadhi --pdf path\to\list.pdf --edition 2025 --dry-run
venv\Scripts\python.exe -m etl.import_jan_aushadhi --pdf path\to\list.pdf --edition 2025
venv\Scripts\python.exe -m etl.import_jan_aushadhi --status
```

`--edition-date YYYY-MM-DD` records the list date; without it the reference price has no
observation date and cannot satisfy a freshness check.

Prescription OCR additionally needs Tesseract and the Python bindings:

```bat
winget install --id UB-Mannheim.TesseractOCR -e
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optional Google Vision OCR (paid past the free tier; 1,000 pages/month free):

```bat
set GOOGLE_VISION_API_KEY=your-key
venv\Scripts\python.exe -m backend.app
```

The key is read from the environment at scan time; no key is committed to the repo.

## Verification status

- `Dolo 650` (id 7087): status `catalog_price_unverified`; 655 same-composition catalog
  options, no saving claim (dataset prices are not current listings).
- `Metformin 500` (id 113251): status `catalog_price_unverified`; 164 catalog options, none
  claimed as cheaper.
- `Nofemol 650` (id 348216): current PharmEasy reference price, still no saving claim because
  the alternatives only carry dataset prices.
- `Diclofenac` search: 20 single-ingredient results (was 2); `Diclofenac Sodium Injection`
  (38530) explains that no pack size is recorded instead of claiming savings.
- Search-page panel and Compare Prices modal verified in a browser (Dolo 650, Nofemol 650,
  Diclofenac).

## Caveats

- `database/medfinder.db` is git-ignored. A different clone/container must rerun the ETL.
- Search uses contiguous `LIKE '%...%'` matching, a normalized `search_norm` containment match,
  and structured single-ingredient matches from `medicine_ingredients`. The normalized match
  still cannot match different brand words (`Crocin 500` does not match `CROcin Advance 500mg`).
  In that case the catalog fallback adds the queried product instead.
- Ingredient search trusts the recorded composition; incomplete source rows (e.g. combo brands
  whose composition lists only one active) are treated as single-ingredient until enriched.
- Catalog-added rows only get a price when PharmEasy lists the exact brand/product. DrugDB rows
  (and brands PharmEasy lacks) stay price-less and cannot be the "cheaper" side of a comparison.
- PharmEasy scraping depends on its Next.js payload shape; a site redesign breaks
  `_search_payload`. Prices persisted this way are marked "Checked <date>" and go stale; the
  30-day freshness rule still suppresses savings claims for old dataset prices.
- 1mg is blocked for server-side scraping (client-rendered shell + bot markers).
- Some files show as modified in `git status` for unrelated reasons (README, template base,
  external API/service, routes, `.claude/settings.local.json`); they are not part of this change
  set.

## Update — UI trust and accessibility pass (2026-11-10)

Goal: remove fabricated content from the interface and fix the accessibility defects found by
auditing the UI against Vercel's Web Interface Guidelines. These changes are committed.

### Skills installed (Qoder)
- `.qoder/skills/frontend-design/` — Anthropic design-direction guidance (`SKILL.md`,
  `LICENSE.txt`).
- `.qoder/skills/web-design-guidelines/` — Vercel audit skill, plus `rules.md`, a cached copy of
  the rule set it normally fetches from
  `raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`.
- Qoder picks up project-level `.qoder/skills/*` without a restart. The folder is untracked; add
  it to version control if other machines should get the same skills.
- Of the five repos reviewed, only these two fit the stack. `vercel-labs/agent-skills` is
  React/Vercel-specific apart from `web-design-guidelines`; `taste-skill` and
  `ui-ux-pro-max-skill` are greenfield site/design-system generators; `impeccable` is a viable
  next step but ships a compiled engine.

### P0 — trust and reachability
- Fabricated "Rating" removed from cards and the detail page. Both derived `4.<id % 5 + 3>` from
  the medicine id and presented it as a rating (`renderMedicineCard`, `renderMedicineDetail`).
- Fabricated social proof removed from the landing page: the stats band ("1.2M Users Helped",
  "42% Avg. Savings") and three testimonials attributed to named pharmacists and doctors.
- The scanner drop zone was a `<label>` wrapping a hidden input, so it could only be opened with a
  mouse. It is now `<button type="button" data-file-trigger>` and `setupScanner` calls
  `input.click()`. `.drop-card` gained width/font/color/padding resets for the button element.
- Search inputs had `outline: 0` with no replacement. Added `:focus-within` rings on
  `.hero-search`, `.search-toolbar`, `.disease-search` and `.chat-form`, plus a global
  `:focus-visible` ring for links, buttons, summaries, checkboxes and the price range.

### P1 — standards
- Compare Prices modal: `role="dialog"`, `aria-modal`, `aria-labelledby`; focus moves to Close on
  open, Tab/Shift+Tab wrap inside the dialog, and focus returns to the triggering button on close.
- Labels: `aria-label` on the four search inputs; the max-price range has a real
  `<label for="maxPrice">` and `aria-describedby`.
- Live regions: `#resultCount`, `#chatMessages`, and a new `#scanStatus` (`role="status"`) that
  announces scan start, failure and match counts. `.sr-only` utility added.
- Ten literal `"..."` replaced with `"…"` in status copy and placeholders.
- All four `transition: all` replaced with the properties that actually animate.
- `prefers-reduced-motion` block stops the pulse animation, modal fade/slide, smooth scroll and
  transitions; `overscroll-behavior: contain` on `.modal-content`.

### P2 — polish
- Skip link to `#main` as the first focusable element; `<main id="main" tabindex="-1">` with
  `scroll-margin-top: 96px`.
- `aria-current="page"` on the active nav link and pagination button; pagination is now `<nav>`.
- View toggle is `role="group"` with labelled buttons and `aria-pressed`; FAQ toggles maintain
  `aria-expanded`/`aria-controls`.
- Results page gained a visually hidden `<h1>`.
- Prices and dates use `Intl.NumberFormat`/`Intl.DateTimeFormat` (`en-IN`); `.price` and
  `.price-large` use `tabular-nums`; interactive elements use `touch-action: manipulation`.
- Removed the non-functional "Buy now" and "Save to list" buttons from the detail page (the FAQ
  already states the product does not sell medicines).
- "1 results" → "1 result".

### Verification (this session)
- No console errors on the home, search, detail and scanner pages.
- Modal: 23 focusable elements, both Tab wraps work, Escape closes and restores focus.
- Scanner: drop card is a `<button tabindex=0>`, clicking it triggers the file input; a rejected
  upload populated `#scanStatus` with the backend message.
- Search `paracetamol`: 20 results, pagination visible, page 2 moves `aria-current`; the view
  toggle flips `aria-pressed`. `Dolo 650` reports "1 result". Detail 348216 shows `Rs. 12.41`,
  "Checked 31 Oct 2026", `tabular-nums`, and no dead buttons.
- Skip link is the first focusable element and moves focus to `<main>`.
- Not verified by eye: the skip link focus state (the automation window reports
  `document.hasFocus() === false`, so `:focus` does not match) and the reduced-motion query (the
  browser tool cannot emulate it). Both rules parse correctly.

### Open items
- Footer "Resources"/"Legal" links point to `/` and the social icons to `#`; they need real pages
  or removal.
- The hero voice button has an `aria-label` but still has no handler.
- The disease page updates `#diseaseDetail` without a live region.
- Static assets are at `v=20261110-3`. Restart the app after Python changes only.

Files: `static/css/style.css`, `static/js/app.js`, `templates/base.html`,
`templates/index.html`, `templates/results.html`, `templates/scan_medicine.html`,
`templates/assistant.html`, `templates/disease_guidance.html`, `.qoder/skills/` (untracked).

## Update — composition backfill + DrugDB v1 API (2026-11-10, uncommitted)

Goal: close the missing-composition gap with the free catalog, at scale and resumably.

### Blocker found and fixed: DrugDB changed its API
- `https://drugdb.in/search?type=medicine&detail=full` now returns the React shell (HTML), so
  `response.json()` raised and every DrugDB call failed — search fallback, on-demand composition
  enrichment, and the new backfill.
- DrugDB publishes a documented API: `GET /v1/search?q=&type=drug&limit=&offset=`, plus
  `/v1/generics`, `/v1/uses?q=`, `/v1/products/{id}`. An optional key is sent as `X-Api-Key`
  (read from `DRUGDB_API_KEY`).
- The public endpoint answers without a key; `max_results` was 120 in probes.

### Client changes (`backend/services/drug_catalog_service.py`)
- `_fetch_records()` calls `/v1/search` with `type=drug`, raises `ValueError` on a non-JSON body
  (so the HTML shell is a readable failure, not a decode crash).
- `_map_record()` maps the flat v1 rows: `composition` (already `Paracetamol (650mg) + ...`),
  `dosage_form`, `packaging` → `pack_size`, `product_id` → `api_record_id`, image URL. The brand
  comes from the product title (`_title_brand_phrase`). Price stays NULL and `mrp` is not mapped,
  because a catalogue MRP is not a retail listing price.
- `DRUGDB_SOURCE` is now `DrugDB · HealthPilot India drug catalog`. Rows saved earlier keep the
  old NRCeS label; the composition/brand/strength dedupe branch still recognises them.
- Deleted the NRCeS-only mappers (`_component_strength`, `_category`) and their unit tables.

### Enrichment split (`drug_catalog_service.py`)
- New `find_composition_record(medicine)` does the lookup and matching without writing, and lets
  transport errors escape so a caller can tell an outage from a miss.
- `enrich_medicine_composition(medicine, use_cache=True, raise_errors=False)` keeps its behaviour
  for the app; backfills pass `use_cache=False` (the cursor owns progress) and `raise_errors=True`.

### Backfill (`etl/backfill_compositions.py`, new)
- Targets rows with `composition_complete = 0` and a brand (77,435 today; no row lacks a brand).
- Keyset pagination by id; the cursor is written to a new table `backfill_state`
  (`composition:v1`) after every non-error row, so a stop resumes at the next medicine.
- `--sample N` (seeded, does not move the cursor), `--limit N`, `--dry-run`, `--status`, `--reset`.
- Minimum `--delay` of 0.5 s (default 2 s); five consecutive failures abort and keep the cursor
  before the failing row.
- Only rows the catalog can match are touched: same brand, same dosage form, same strength tokens,
  and an unambiguous composition.

### Measured (2026-11-10)
- 72 sampled incomplete rows → 56 filled (78%): 12-row seed-0 sample 92%, 60-row seed-1 sample 75%.
  At 2.3 s/row the full 77,435-row pool is about 49 hours.
- Real write verified on id 139030 (`Isopreg 5mg/ml Injection`): composition
  `Isoxsuprine (5mg)`, `composition_complete = 1`, recomputed `product_key`, one
  `medicine_ingredients` row, `api_source`/`api_record_id`/`api_fetched_at` filled. The sample run
  left the cursor untouched.
- Search fallback works again: `Imik 500` returns two catalog products, `Nofemol 650` and a
  nonsense query return nothing.
- Tests: `test_drug_catalog_client.py` (7, new) pins the v1 shape, the `X-Api-Key` header and the
  non-JSON error; `test_backfill_compositions.py` (7, new) covers resume, dry run, reset, sample
  determinism and the error abort. Focused suite: 52 tests pass.

### Caveats
- Strength matching compares numeric tokens only, so a `5mg/ml` row can receive a `5mg`
  composition. Matches still require the same dosage form, and the composition text keeps the
  catalog's wording, but per-ml concentrations are not preserved.
- The v1 endpoint is undocumented in this repo (only observed); a payload change breaks
  `_map_record` and the client tests are the guard.
- `backend/services/india_medicine_api.py` (the `/api/live-alternatives` endpoint) still calls the
  dead `/search` endpoint and now answers "temporarily unavailable". Not fixed in this change.
- The backfill never fills pack size or MRP; catalog rows only get a pack size when
  `save_catalog_medicine` runs, and MRP is not stored at all.

Files: `backend/services/drug_catalog_service.py`, `etl/backfill_compositions.py` (new),
`test_backfill_compositions.py` (new), `test_drug_catalog_client.py` (new), `AGENTS.md`.

## Update — OCR junk filter + fuzzy fallback (2026-11-11, uncommitted)

Goal: read garbled printed brand names that the literal search misses, without ever
presenting a guessed medicine as a confirmed one.

### Matching (`backend/ocr_service.py`)
- `_HEADER_WORDS` expanded (clinic/patient/diagnosis/degree/address vocabulary). Degree
  abbreviations stay conservative: `md`/`ms`/`mb` are excluded so brands such as
  `Vomikind MD` are not dropped.
- `_ADDRESS_UNIT` treats `12 MG Road`, `5 ML Road`, etc. as an address even though the
  strength regex matches the unit; `_looks_like_header` uses it before deciding the line has
  a dose.
- `_fuzzy_lookup()` is new and runs only when the literal path finds no row:
  - candidates are brands sharing the first three letters (`LIMIT 400`), scored with
    rapidfuzz `fuzz.WRatio`;
  - `FUZZY_CUTOFF = 92` (88 scored 43% wrong in calibration), `FUZZY_MIN_GAP = 3`;
  - the printed line must carry a strength or a dosage form, and the candidate must match
    the strength (column or in the product name) and the form;
  - the best candidate must beat every other brand; a tie is rejected as ambiguous.
- `_match_token()` now returns `(row, match_type, score)`; fuzzy hits are `possible` with
  the WRatio score, literal hits stay `exact`/`partial` with a null score. Results are
  cached per token/strength/form key.
- Literal hits must contain the printed token as a whole word (`\bPramod\b`), because the
  old substring match turned a signature line `Pramod` into `Pramodep 50mg Tablet` and
  `Biclav DS` into `Abiclav`/`Nabiclav`. Ingredient-only hits contain no printed word, so
  a generic-name line (`Paracetamol 650`) is now reported as unmatched instead of being
  mapped to an arbitrary brand.
- When two printed words name the product (`Ibugesic Plus`), the second word is a ranking
  hint: products containing it win, and among those the one adding the fewest extra words
  wins (`Ibugesic Plus Tablet` over `IBUgesic Plus NF`).
- A line with no strength and no dosage form is matched only by a whole-word brand of five
  or more letters. This stops `Tel.: 2261594698` matching `Tel 40mg Tablet` and clinic
  hours `Mon to Sat 10-1, 5-8` matching `Mon 10mg Tablet`, while a bare brand list line
  (`Ibugesic Plus`, `Betadine Gargle`) still resolves.
- `_NAME_TOKEN` accepts three-letter words (`PAN`), with dosage abbreviations added to
  `_STOPWORDS` so `BD`/`TDS`/`SYR` are not looked up.
- A one-word medicine line that cannot be matched (`Cap Biclav DS`) is now listed under
  "Read but not matched" instead of disappearing: the unmatched filter accepts a line when
  it carries a dosage form.

### UI (`static/js/app.js`, `static/css/style.css`, `templates/base.html`)
- `possible` matches render as `Close match — verify` with an orange `.match-badge.possible`;
  `exact` stays `Name match`, everything else `Possible match`.
- `.detected-list > div > .min-grow` is a grid so name, form, dosage and printed line stack
  instead of running together.
- Static asset version is `v=20261111-2`.

### Measured (223 real brands, one-character OCR error, true strength + form context)
- Before the whole-word rule, the literal path matched 32 garbled tokens and half of them
  were fragments (`Zevace` -> `Zevacef`, `Biclav` -> `Abiclav`).
- Now: literal 5 matched (3 correct, 2 plausible truncations), fuzzy adds 58 (54 correct,
  4 wrong). Total 63 matched, 57 correct = 90% precision, versus 67/87 = 77% before.
- 46 letterhead words paired with `500mg Tablet`: 7 false hits at the old cutoff 88 without
  the gap/context gates, 1 at the new settings (`advice` -> `Advicef 500mg Tablet`, both a
  real brand and a word in the header list).
- End-to-end image through `/api/ocr`: `Tab Augmentn 625 mg 1-0-1 after meals for 5 days`
  -> `Augmentin 625 Duo Tablet`, `possible`, score 94.1, strength ok, dosage parsed;
  `Tab Pan 40 mg 1-0-0 before breakfast` -> `PAN 40 Tablet`, `exact`; clinic/patient/address
  lines ignored. Browser-verified on `/scanner` with no console errors.
- Letterhead image (`Dr. Pramod Balakrishnan`, `Halleys Tower, MG Road`, a bare `Pramod`
  line, `Tab Ibugesic Plus 1-0-1`, `Cap Biclav DS 1-0-1`): no `Pramodep`/`Abiclav` match,
  `Ibugesic Plus` -> `IBUgesic Plus Tablet`, `Biclav DS` listed under "Read but not
  matched"; status reads "2 medicines matched and 1 printed line not matched".

### Tests
- `test_prescription_ocr.py` adds: header/address filtering, garbled brand as `possible`
  with a score, fuzzy requiring strength or form context, ambiguous two-brand tie rejected,
  three-letter brand (`PAN 40`) read, doctor name not matched inside `Pramodep`, `Biclav`
  not matched inside `Nabiclav` (and listed unmatched), second printed word preferring the
  matching product, phone/clinic-hours lines ignored, bare brand line still matched. Full
  focused suite: 62 tests pass.

### Caveats
- A printed generic (`Tab Paracetamol 650 mg`) can still resolve to a plain-name catalog row,
  for example `Paracetamol 500mg Tablet` when no 650 mg row exists. That result is returned as
  `possible` with `strength_match: false` and the badge `Possible match — check strength`; it
  is never presented as a confirmed name match.
- There is no image cropping. The header is filtered line by line instead, because a fixed
  top-25% crop cuts the first medicine line on dense handwritten slips and cannot apply to
  text-layer PDFs.
- Fuzzy recovery is intentionally narrow: 3-letter prefix and cutoff 92 recover about a
  quarter of single-character brand typos; a typo in the first three letters still misses.
- A reading within three points of two brands is dropped rather than guessed; it appears in
  "Read but not matched" so the user can search manually.
- `possible` matches still need the printed strength to be recorded in the catalog row;
  rows with no recorded strength are never accepted by the fuzzy pass.

Files: `backend/ocr_service.py`, `static/js/app.js`, `static/css/style.css`,
`templates/base.html`, `test_prescription_ocr.py`, `AGENTS.md`.

### Strength-conflict downgrade (2026-11-11)
- `_extract_medicines()` downgrades `match_type` from `exact` to `possible` whenever
  `strength_match` is `False`, so a name-only hit whose printed strength contradicts the row
  is never returned as a confirmed match. Ranking, `_strength_state()` and the SQL are
  unchanged.
- Measured on the real database: `Tab Paracetamol 650 mg 1-0-1` -> `Paracetamol 500mg Tablet`
  (id 257284), `match_type: possible`, `strength_match: false`.
- `renderDetectedMedicine()` (`static/js/app.js`) renders `Possible match — check strength`
  with the `.match-badge.warning` style for any row with `strength_match === false`; the
  separate "Printed strength differs" chip is gone.
- `test_prescription_ocr.py` gains `test_conflicting_printed_strength_downgrades_an_exact_name_match`
  and a plain `Paracetamol 500mg Tablet` fixture row. 21 OCR tests pass; 31 tests across
  `test_api_fallback`, `test_api_fallback_cache`, `test_clean`, `test_drug_catalog_client`
  and `test_backfill_compositions` pass.
- Verified with a rendered image through `/api/ocr` and in the browser on `/scanner`: the
  detected row shows the warning badge and "1 medicine matched."; asset version `20261111-3`.

## Update — handwritten prescription accuracy (2026-11-17)

Google Vision already used `DOCUMENT_TEXT_DETECTION` with `languageHints: ["en"]`; the losses
were in the image sent and in matching garbled words. Changes in `backend/ocr_service.py`:

- `preprocess_prescription_image()` runs before Vision and Tesseract: EXIF rotation, crop to the
  bright sheet (Otsu threshold on a 400px copy; only when the sheet is 30–95% of the photo),
  grayscale, autocontrast (1% cutoff), resize to ~1600–2400px (≤3× upscale), unsharp mask.
- `normalize_ocr_line()` fixes look-alikes only next to digits (`65O`→650, `l0`→10, `rng`/`mq`→mg)
  and a single trailing 0/1 after a word (`Dol0`→Dolo). `B12`, `D3`, `Telma40` are left alone.
  Leading `Rx`, `℞`, `1)` markers are stripped; `T.`/`C.` read as tablet/capsule.
- Vision per-word confidence is collected and returned per line as `ocr_confidence` (0–100).
- Lines with no literal match are compared (Levenshtein `fuzz.ratio`, cutoff 72) with every
  catalog brand first word and ingredient name (`_catalog_vocabulary()`, cached 1h per DB path,
  empty when tables are missing). Up to 3 `suggestions` are returned per unmatched line. A misspelt
  ingredient with a printed strength resolves to a single-ingredient product of that strength.
- Auto-accept only when the top suggestion scores ≥90, is a catalog product, agrees with the printed
  strength/form and leads rivals by ≥5; it is still `match_type = "possible"`.
- UI (`static/js/app.js`, `scan_medicine.html`): every detected medicine has Confirm / Change /
  View / Remove, a "N of M confirmed" live summary, read-confidence and name-similarity text
  (both computed, never invented), suggestion chips on unmatched lines and a type-to-correct search.
- `rapidfuzz` added to `requirements.txt`. 30 OCR tests (9 new), all offline.

Not done: a vision-LLM reader (Claude/Gemini returning JSON of medicine/strength/dose) would read
messy handwriting better than OCR; it needs a new API key, so it is left as an option.

## Update — Gemini reads prescriptions (2026-11-18)

The owner's Google Cloud account needs a prepayment, so Vision is unavailable. `_image_text()` now
tries **Gemini** first when `GEMINI_API_KEY` is set (free Google AI Studio key, sent in the
`x-goog-api-key` header, never in the URL or repo; model `gemini-2.5-flash`, override with
`GEMINI_MODEL`). The preprocessed image is sent with a prompt asking for one medicine line per
line, as written, with no added medicines or substitutes; `NONE` means no medicines. Any failure
(network, 429 rate limit, quota) falls back to Google Vision if its key is set, then Tesseract.
The returned text goes through the same cleanup, fuzzy suggestions and confirm step; Gemini gives
no per-word confidence, so `ocr_confidence` is null for it. 3 new tests (33 OCR tests).

## Update — Gemini actually used again (2026-11-25)

The owner's scanner kept reading with Tesseract. Two causes: (1) the Gemini commit (`dbee16c`) only
lived on `claude/project-thread-f86dlf`, never on `feat/search-no-price-notice`, which the owner
runs; it is now merged onto `claude/project-thread-xx4toi` (asset versions `20261125-1`).
(2) Google retired `gemini-2.5-flash` for new keys (HTTP 404 "no longer available to new users"),
so every call failed and fell through. `_gemini_text()` now tries `GEMINI_MODEL` (if set), then
`gemini-flash-latest`, `gemini-3.5-flash`, `gemini-flash-lite-latest`; 404/429/5xx/timeout moves to
the next model, other errors (bad key, 400) stop. The free tier answered 503 "high demand" for
`gemini-flash-latest` during testing, and `gemini-3.5-flash` took ~35 s per page. The server logs
which models were skipped and why, and says when `GEMINI_API_KEY` is missing from its environment.
The key is a Windows *user* variable, so a terminal opened before it was set does not see it.
2 new tests (100 offline tests pass).

## Update — ORS line + scanner accuracy check (2026-11-25)

A real hospital prescription ("Inj 5% Dextrose iv stat", "Adequate fluid intake", "ORS 2 packets")
showed only Dextrose 5% Infusion. Cause: the catalog has **no ORS product at all** (no "ORS" name,
no Electral/ORSL, no oral-rehydration composition), so the line fell into "Read but not matched",
where the word "packets" produced look-alike suggestions (Pacset, Parcet, Paknet-BC).
Fix in `backend/ocr_service.py`: pack words (packet(s), sachets, pouch, bottle, strip, vial,
ampoule) are stopwords and "2 packets" is dosage text; `_KNOWN_ABBREVIATIONS` maps `ORS` (also
`O.R.S.`) to "Oral rehydration salts". A line naming one matches a catalog product whose name has
the word or whose name/composition has the generic name; otherwise it is listed unmatched with
`not_in_catalog: true`, no suggestions, and a `note` the UI shows ("read from the prescription,
but the catalog has no oral rehydration salts product"). 3 new tests; 107 offline tests pass.
Asset version `20261125-2`.

Accuracy check (synthetic only, no real photos in the repo): 9 rendered prescriptions in 8
handwriting-style fonts with blur/rotation/noise, 26 medicine lines (24 brands + Dextrose + ORS),
scored with the app's own parser. Gemini: 26/26 correct product and strength. Tesseract: 19
correct, 2 only as the top suggestion, 1 wrong strength (Dolo "€50"), 4 missed; 0 extra
medicines for either. Fonts are far neater than real handwriting, so these are upper bounds.
Gemini free tier answered 503 for `gemini-flash-latest` on every page; pages took 11-65 s.

### Real-photo check (2026-11-25, same day)
8 unique real photos from the owner (10 sent, 2 duplicates). Ground truth was read by eye; two
photos (dense Hindi/Devanagari, and one where Gemini itself answered "Unclear") were left out, and
one illegible line, leaving 23 medicine lines. Gemini transcribed 20/23 names correctly. After the
parser, 9/23 were the right catalog product, 1 was offered as the top suggestion, 0 wrong, 13
missed: 10 because the medicine is not in the catalog (Ultracal-D, Cartilix, Relentus, Vonalong,
Vitazyme, Enzabil C, Ultrafen Plus, Pantakind Raft, PND otic, …) and 3 that Gemini misread or
skipped. Tesseract: 0/23 correct, 2 wrong. Before this round's fixes the parser had 5 correct and
5 wrong on the same Gemini text. Fixes: `Syp` is a syrup hint, syrup/injection hints accept
suspension/infusion forms (`_FORM_SYNONYMS`), a printed form outranks strength when ranking,
`(250/5)` on a syrup line is 250 mg, direction words (pain, otic, ear, right, …) are stopwords, and
a match is `exact` only when the printed strength is confirmed by the row. Gemini free tier hit
HTTP 429 (quota) after ~20 pages in one afternoon. 109 offline tests pass.

## Update — scanner accuracy round 2 (2026-11-26)

Branch `claude/project-thread-a9csqw` (from `claude/project-thread-of3tt4`), `backend/ocr_service.py`:
- **Extra Gemini keys.** `GEMINI_API_KEY_2` … `GEMINI_API_KEY_9` are optional. A key that answers
  429 on every model rests for an hour (`GEMINI_KEY_COOLDOWN_SECONDS`) and the next key is used; a
  rejected key (400/401/403) also hands over. The log names the variable ("GEMINI_API_KEY used up
  its free quota; switching to GEMINI_API_KEY_2"), never the key. 503/timeouts do not switch keys.
  Each key must come from its own Google Cloud project (quota is per project). Stays free tier.
- **Structured Gemini answer.** The prompt asks for JSON `[{line, form, generic}]`
  (`responseMimeType: application/json`); plain-text answers still work. `generic` is Gemini's
  own knowledge, so it is used only (a) to match a line that itself prints that generic, or
  (b) to list catalog products recorded with that composition for a brand missing from the catalog,
  as an `unmatched_lines` entry with `generic_source: "gemini"` and a note saying the composition is
  not verified and same composition is not interchangeability.
- **Gemini lines count as medicine lines** (no printed strength/form needed), and Gemini's `form`
  fills in a missing form hint. `2 tsf` is a liquid dose (syrup hint).
- **Live catalog lookup for missing brands** (Gemini lines only, at most 4 lookups per scan,
  `MEDFINDER_SCAN_LIVE_CATALOG=0` turns it off): the printed brand words (before the first number)
  are looked up in DrugDB, then PharmEasy, via the search page's own clients and
  `save_catalog_medicine`; only listings whose name starts with the printed brand as whole words are
  saved, so every added row carries its listing's name/form/strength/composition and `api_source`.
  Misses are cached as `scan-live:<name>`. When a local brand row lacks the printed form ("Pantakind
  Raf 2 tsf" vs Pantakind tablets) the live catalog is asked once for the fuller product.
- **ORS** now matches a catalog product listing sodium + potassium chloride with dextrose/glucose
  (`matched_via: "composition"`), looking it up live once if the catalog has none.
- **Top three when unsure.** Any non-exact match carries up to two `alternatives` (other products of
  the same printed brand, or other products of the same printed generic).

Re-score on the same 23 real-photo lines, same stored Gemini transcription (so only matching
changed): right product **8 → 13**, wrong 0 → 0. Gained: ORS, Pantakind Raft, Vonalong, Vitazyme,
Celecoxib. Enzabil matches the tablet; the line may mean Enzabil C syrup, so it is not counted.
Still missed: 6 brands absent from every Indian catalog checked (Ultrafen Plus, Relentus,
Ultracal-D ×2, Cartilix — Bangladesh; PND otic — Philippines) and 3 lines Gemini misread or skipped
(Omeprazole, Frisium, Tonoferon). The `generic` field could not be scored fairly: the main Gemini
models were out of free quota and `gemini-flash-lite-latest` reads these photos much worse.
Rows added to the real DB by the live lookups: Ranbaxy ORS, 7 celecoxib 200 mg products,
Vonalong 10/20, Vitazyme capsule/drops, Enzabil, Pantakind variants incl. Pantakind Raft. Misread
lookups during testing (Sevelamer "Sevent", Pneumovax, Lycozon) were deleted. 111 offline tests pass.

## Update — search accuracy (2026-11-27)

Measured with `python -m eval.search_eval all` (local search only, no API fallback, nothing
written). Original 72-query set from 2026-11-26; held-out 51-query set written before any change.

| Set | Before top-1 / top-3 | After top-1 / top-3 |
|---|---|---|
| Original 72 | 53 (73.6%) / 56 (77.8%) | 70 (97.2%) / 70 (97.2%) |
| Held-out 51 | 37 (72.5%) / 38 (74.5%) | 49 (96.1%) / 49 (96.1%) |

What changed:
- `backend/services/search_ranking.py` (new): query parsing (`pan40`/`Telma-40` -> `pan 40`;
  `B12` stays glued), Python ranking of candidate rows (exact name words > name starts with the
  words > brand equal > first word prefix > word later in name > substring; any tier whose
  strength does not match drops by 5; strength is read from the name *or* the strength column),
  family size (number of catalog names using the first word) breaks ties among partial matches,
  and spelling correction (rapidfuzz ratio >= 80, same first letter, weighted by product count,
  threshold 90).
- `medicine_service`: generic-ingredient path only when the query *is* an ingredient (exact,
  salt-stripped, synonym such as amoxicillin->amoxycillin, or >= 80% of one ingredient name).
  Brand stems (`Atorva`, `Telma`) now take the name path. `_record_ingredient_keys` no longer
  invents an ingredient from an Indian brand name (only RxNorm "10 MG" / " / " names), so
  Ondem/Metrogyl return all their rows.
- `search_medicines_detailed(..., allow_correction=True)` is used by `/api/search`; it returns
  `corrected_query` and the page says "No exact match for X. Showing results for Y" with a
  "Search only for X" button (`exact=1`). `search_medicines` (OCR, assistant) never corrects.
- Data: `etl/add_listed_brands.py --apply Zincovit Digene "Liv 52" "Neurobion Forte" Electral`
  saved 37 PharmEasy listings (ids 348260-348296). Electral and the Neurobion Forte injection
  carry compositions; Zincovit, Digene, Liv 52 and the Neurobion Forte tablet are search-only
  rows (`composition NULL`, `composition_complete 0`) because PharmEasy lists no composition.
  Backup before the insert: `database/medfinder.before-listed-brands-20261127.db`.

Caveats:
- 5 of the 17 original-set gains are data (the added brands), not code. Code alone: 65/72.
- Eval rule change: brand checks now treat `-` as a space (`Montair-LC` = `Montair LC`).
- Remaining misses: `Dol` (top is the real brand "Dol 80"), `Combi` (Combikit ranks over
  Combiflam), `Hifen` (real brand "Hifen 250"), `Brufin` (no confident correction).
- A misspelling of a missing brand can be corrected to a look-alike product; the banner and the
  exact-search button are the guard. Threshold 90 keeps `Electral` from becoming `Electril`.

## Update — alternative-medicines accuracy (2026-11-27)

New offline eval `python -m eval.alternatives_eval` (59 hand-picked cases: 24 single-ingredient,
8 combinations, 11 liquids/drops/sachets, 3 form variants, 8 that must be blocked, 5 with no
composition) and `python -m eval.alternatives_eval sample 40` (held-out, products picked by a
hash of the id). It scores what `/api/substitutes/<id>?limit=10&live=0` shows, with enrichment
stubbed. The checker re-parses raw `composition` text with decimals and units, reads form
variants and release markers from the name, and measures recall against every catalog product
that agrees and has a price + pack size.

| Set | Before precision / recall / restricted blocked | After |
|---|---|---|
| Hand-picked 59 | 682/741 (92.0%) / 23,447/29,775 (78.7%) / 4 of 8 | 673/673 (100%) / 29,662/29,773 (99.6%) / 8 of 8 |
| Held-out 40 | 497/512 (97.1%) / 15,241/19,775 (77.1%) / – | 531/531 (100%) / 19,676/19,774 (99.5%) / – |

Known brands (Crocin 650 for Dolo 650, Clavam for Augmentin, ...): 55 of 55 in the candidate set
before and after (not necessarily in the 10 shown).

What changed:
- `medicine_data`: `PR`, `DSR`, `SA` count as modified release (Pan-D PR, Glycomet-GP PR, Amlong MT
  PR were compared with plain products); `thyroxine` is narrow therapeutic index (Thyronorm reads
  "Thyroxine", so it was compared); respules/rotacaps/nebuliser/inhalation names are inhaled forms;
  new `form_variant()` (DT/dispersible, MD/ODT, VT/vaginal, chewable, effervescent, sublingual).
  6,245 complete-composition products are newly restricted (5,323 MR, 330 inhaled, 592 thyroxine).
- `recommendation_service._candidate_rows`: candidates come through the reference's rarest
  ingredient instead of `lower(strength) = ?`, because combination rows fill the strength column
  inconsistently ("200mg" vs "200mg/500mg"); 6,326 true matches were missed that way. The strength
  columns must still agree: equal, or both only restate amounts from the identical composition.
  Raw ingredient strengths are compared with decimals, because `ingredient_strength_norm` drops
  the point (12.5mg, 125mg and 1.25mg all read "125mg"). Form variants must match.
- Jan Aushadhi rows of another form variant (e.g. orally disintegrating) are not offered.

Caveats:
- The checker and the fixes encode the same rules, so 100% means "no violation of these rules",
  not clinical equivalence. The catalog's own errors are invisible to it.
- PharmEasy spot check (46 references + 1 shown alternative each): 43 agree, 36 not listed, 11
  differ; most differences were the crude name lookup picking another product. One real error:
  every `Ciplox` / `Cifran` row is recorded as **Ofloxacin** (should be ciprofloxacin; the catalog
  has only 26 ciprofloxacin rows). About 600 "Ofloxacin" tablets at 250/500/750/1000 mg are probably
  ciprofloxacin. Present in the 2026-11-15 backup, so it comes from the source data. Not fixed.
- Liquids: almost none have a pack size, so they only get the same-pack review list, never a
  price-ranked one. `Azithral 200 Liquid` has no dosage form and gets nothing.
- Missing composition (Zincovit, Digene, Neurobion Forte tablet, Betnovate, Moxicip): no
  alternatives at all, as designed.
