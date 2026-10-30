# MED FINDER — Agent Notes

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

## How to run

```bat
REM Full ETL (only needed to rebuild the database)
python -m etl.build_database

REM App
venv\Scripts\python.exe -m backend.app
```

App runs at http://127.0.0.1:5000. Restart it after Python changes; the database is read live.

## Verification status

- `Dolo 650` (id 7087): status `cheaper_same_composition`, 639 alternatives.
- `Azithromycin 500`, `Augmentin 625`, `Pantoprazole 40`, `Cetirizine 10`: alternatives returned.
- `Metformin 500` (id 113251): top result is already the cheapest generic; 132 same-ingredient
  matches are listed, none cheaper.
- Search-page panel and Compare Prices modal verified in a browser.

## Caveats

- `database/medfinder.db` is git-ignored. A different clone/container must rerun the ETL.
- Search uses contiguous `LIKE '%...%'` matching plus a normalized `search_norm` containment
  match. The normalized match fixes separator differences (`Montek LC` finds `Montek-LC`) but
  still cannot match different brand words (`Crocin 500` does not match `CROcin Advance 500mg`).
  In that case the catalog fallback adds the queried product instead.
- Catalog-added rows only get a price when PharmEasy lists the exact brand/product. DrugDB rows
  (and brands PharmEasy lacks) stay price-less and cannot be the "cheaper" side of a comparison.
- PharmEasy scraping depends on its Next.js payload shape; a site redesign breaks
  `_search_payload`. Prices persisted this way are marked "Checked <date>" and go stale; the
  30-day freshness rule still suppresses savings claims for old dataset prices.
- 1mg is blocked for server-side scraping (client-rendered shell + bot markers).
- Some files show as modified in `git status` for unrelated reasons (README, template base,
  external API/service, routes, `.claude/settings.local.json`); they are not part of this change
  set.
