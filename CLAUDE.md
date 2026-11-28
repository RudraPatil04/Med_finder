# MED FINDER — read this first

A Flask + SQLite web app (http://127.0.0.1:5000) over an Indian medicine catalog. For a
searched medicine it shows cheaper products with the **same recorded active ingredients,
strength, dosage form and release type**, with honest price labelling. It is not a medical
advice tool, does not decide therapeutic equivalence, and does not sell medicines.

`AGENTS.md` (1,100 lines) is the dated changelog of every change, its measured numbers and its
caveats. This file is the current state; read AGENTS.md when you need the *why* behind a rule.

## Project handoff (written 2026-11-28; read this before anything else)

The owner (Vineet, GitHub `RudraPatil04`) has moved to a different Claude account. This section
carries over what earlier Claude sessions learned, so a new session does not have to ask again.
Where it differs from older sections below, this section is newer.

**What it is.** Med Finder: search a medicine, see cheaper generic alternatives with the same
composition, their prices, and a prescription scanner. Repo `RudraPatil04/Med_finder`; Vineet's
local checkout is `D:\MED_FINDER` (Windows). Run with `venv\Scripts\python.exe -m backend.app`.
168 tests pass on `claude/project-thread-ygw48f`.

**Urgency.** A professor demo is coming and Vineet calls it do-or-die. Prefer small, safe, tested
changes over new features or refactors.

**Branches (no PRs opened yet).**
- Chain, each built on the previous: `claude/project-thread-cbb6yv` → `claude/project-thread-f86dlf`
  (prescription OCR: preprocessing, fuzzy suggestions, confirm step) → `claude/project-thread-1aw1u1`
  (search accuracy) → `claude/project-thread-4by47q` → `claude/project-thread-nsu8vn` →
  `claude/project-thread-ygw48f` (latest; contains the whole chain).
- Separate: `claude/project-thread-xx4toi` (Gemini model fallback chain; `gemini-2.5-flash` retired).
- Vineet's local `feat/search-no-price-notice` has an uncommitted `backend/ocr_service.py` edit and an
  unpushed commit `f9c1f8d` (requirements pins relaxed for Python 3.12). It exists only on his laptop.

**Decisions already made.**
- Prefix repair (`etl/repair_ingredient_prefixes.py --apply`) has been applied to the real database:
  6,118 medicines fixed. (Supersedes "Prefix repair not applied" further down.)
- Jan Aushadhi prices imported, dated 2026-11-16.
- Live PharmEasy price-on-search is built, but PharmEasy rate-limits (HTTP 405) after about 800 lookups.
- 19 to 21 demo medicines have verified savings that stay current until about 16 Dec 2026.
- Vineet approved (2026-11-15) a clearly labelled "estimated saving vs catalog price" where no verified
  saving exists. This relaxes rule 2 for that labelled case only; verified savings still need two
  current prices.
- Google Vision is out (Google Cloud wants a Rs 1,000 prepayment). The scanner uses Gemini via a free
  AI Studio key in `GEMINI_API_KEY`, falling back Gemini → Vision → Tesseract. On Vineet's laptop
  `GEMINI_API_KEY` is a Windows user variable, so only terminals opened after it was set can see it.

**Measured results.**
- Search: 97.2% top-1 on a 72-query set, 96.1% on 51 held-out queries.
- Alternatives: 100% precision / 99.6% recall on a 59-medicine set.
- Added 37 listings for 5 missing brands. Backup taken first:
  `database/medfinder.before-listed-brands-20261127.db`. If a brand is missing in the demo, run
  `etl/add_listed_brands.py`.

**Known issues before the demo.**
- Ciplox / Cifran rows are mislabelled as ofloxacin. Avoid them in the demo.
- PharmEasy was blocking the laptop on 2026-11-27. Test one search about 30 minutes before the demo.
- Product photos load from 1mg servers, so they are fine for a local demo only.

## Non-negotiable rules

1. **Composition decides matching.** Products are only compared when
   `medicine_ingredients` rows match by normalized ingredient name + strength, and
   `strength`, `dosage_form`, `release_type` agree. Name similarity is never evidence.
2. **No savings claim without two current prices.** `PRICE_MAX_AGE_DAYS = 30` in
   `backend/services/medicine_service.py`. If either side's `price_checked_at` is older (or
   missing), the UI says "Catalog price (unverified)" and the response carries no
   `savings_amount` / `savings_percent` / `savings_basis`. Since 2026-11-16 (owner's decision)
   such options may carry `estimated_savings_percent` / `estimated_savings_amount` /
   `estimated_savings_basis` (per-unit difference of recorded catalog prices); the UI labels
   them "Est. save X%" with a dashed badge and never merges them with verified savings.
   `/api/substitutes` also fetches live PharmEasy prices first (`live_price_refresh.py`;
   `?live=0` or `MEDFINDER_LIVE_PRICES=0` turns that off).
3. **Never call products interchangeable.** Same composition is not substitution. UI copy and
   API text must keep saying the user should confirm with a doctor or pharmacist.
4. **Restricted classes get nothing.** `restricted_reason()` in `backend/medicine_data.py`
   returns a reason for narrow-therapeutic-index ingredients (warfarin, levothyroxine,
   lithium, …), injection/inhaler forms, and modified-release products. Such a reference
   returns status `restricted_class` with zero alternatives, zero options, zero reference
   prices.
5. **Suggestions only where a comparison works.** `suggest_functional_medicines()` requires
   `composition_complete = 1`, a brand, no restriction, and at least one other product with the
   same ingredient set/strength/form/release carrying `price > 0` and a pack size.
6. **No invented content.** No ratings, testimonials, confidence scores or "savings" that are
   not computed from stored prices. (A previous pass removed all of those — do not reintroduce.)
7. **Secrets come from the environment**: `DRUGSETU_API_KEY`, `GEMINI_API_KEY` (plus optional
   `GEMINI_API_KEY_2` … `_9`, used in turn when one free quota runs out), `GOOGLE_VISION_API_KEY`,
   `DRUGDB_API_KEY`. Never commit a key.
8. **Do not rebuild the database casually.** `etl/build_database.py` drops `medicines`,
   `medicine_ingredients` and `api_search_cache`. `etl/repair_ingredient_prefixes.py` must be
   re-run after any rebuild.

## How a request flows

```
templates/*.html + static/js/app.js
        │ fetch
        ▼
backend/routes/medicine_routes.py      /api/search, /api/search-suggestions, /api/substitutes/<id>,
        │                              /api/medicine/<id>, /api/cheapest, /api/ocr, /api/disease, …
        ▼
backend/services/medicine_service.py   local search (LIKE name/brand, search_norm, single-ingredient
        │                              composition) → miss → DrugDB v1 → PharmEasy → RxNorm → save
        ▼
backend/recommendation_service.py      same_composition_alternatives (current prices both sides),
                                       same_composition_options (everything else), reference_price_options,
                                       status: cheaper_same_composition / catalog_price_unverified /
                                       reference_price_unknown / restricted_class / no_verified_alternatives
        ▼
database/medfinder.db
```

Supporting services: `price_comparison_service.py` (per-unit normalization, pack parsing),
`price_enrichment_service.py` (persist a PharmEasy listing price for a price-less row),
`drug_catalog_service.py` (DrugDB v1 + PharmEasy mapping, dedupe, `ensure_catalog_schema`),
`drugsetu_service.py` + `enrichment_worker.py` (background fill of composition/pack/MRP),
`api_cache.py` (`api_search_cache` keys + TTLs), `backend/ocr_service.py` (Gemini, then Google Vision, then Tesseract;
Google Vision, conservative local matching).

## Data model

| Table | Purpose |
|---|---|
| `medicines` | one row per product: name, brand, strength, form, release, pack, composition, `composition_complete`, `product_key`, `search_norm`, `price`, `price_source`, `price_checked_at`, `mrp`, `source`, `api_source`, `api_record_id` |
| `medicine_ingredients` | structured composition: `ingredient_name_norm`, `ingredient_strength_norm` |
| `reference_prices` | Jan Aushadhi (PMBJP) list prices, keyed by `composition_key` = `<form>|<ingredient:strength>+…` |
| `api_search_cache` | cached catalog lookups / enrichment attempts (+ TTL) |
| `backfill_state` | resumable cursor for `etl/backfill_compositions.py` |

Indexes that matter: `idx_search_norm` (prefix GLOB; queries force it with `INDEXED BY`) and
`idx_group_price` (`strength COLLATE NOCASE, dosage_form COLLATE NOCASE, price, composition_complete`).

## Current state (measured 2026-11-15 on `database/medfinder.db`)

- 348,237 medicines; 270,803 with a complete composition (77.8%); 453,343 ingredient rows.
- 348,208 carry a dataset price, but only **6** have `price_checked_at` inside 30 days — so the
  app currently makes **zero** savings claims. Everything shows as "catalog price (unverified)".
- 1,721 Jan Aushadhi rows imported (edition 2025, `observed_at = 2025-04-01`, already over a
  year old); 629 join the catalog by composition. All report `price_is_current = false`.
- 26 medicines added from live catalogs; 28 cached searches.
- Suggestions: 190,850 medicines (84.2% of eligible) pass the "priced sibling" test.
- `Naxdom 500 Tablet` (id 70888) still reads `Cure Healthcare Naproxen (500mg) + …` — the
  prefix repair has been **measured but not applied** to the real database. Its alternatives
  list stays empty (a clean copy yields 73 options).

## Environment and how to run

Python 3.12 venv at `venv\` (`.venv/` also ignored), SQLite, no server. Tesseract 5.4 is
installed at `C:\Program Files\Tesseract-OCR\tesseract.exe`.

```bat
venv\Scripts\python.exe -m backend.app          REM the real entry point (port 5000, debug)
venv\Scripts\python.exe -m unittest test_search_suggestions test_api_fallback_cache
```

`backend/app.py` is the current app. Root `app.py` is the v1 duplicate (and `start.bat` still
launches it) — do not extend it.

Optional tools (all safe to skip): `etl/backfill_compositions.py` (resumable, needs the 110 MB
source CSV), `etl/import_jan_aushadhi.py --pdf … --edition 2025 --edition-date …`,
`etl/repair_ingredient_prefixes.py [--apply]` (dry run by default).

Tests (unittest, offline, 83 focused tests):
`test_search_suggestions`, `test_api_fallback_cache`, `test_prescription_ocr`,
`test_drug_catalog_client`, `test_backfill_compositions`, `test_repair_ingredient_prefixes`,
`test_drugsetu_enrichment`, `test_search_ranking`. `test_api_fallback.py` and `test_clean.py` contain 0 test cases.

## Problems to fix, in priority order

1. **A fresh clone cannot serve data, and crashes instead of degrading.** Verified by cloning
   GitHub into a temp dir: pages and static assets return 200, tests pass, but
   `/api/search`, `/api/cheapest`, `/api/search-suggestions` return **500**
   (`no such table: medicines`). The 221 MB database and the 110 MB source CSV are both
   gitignored, so a clone can neither find nor rebuild the catalog. Fix: catch the
   missing-table case and return empty results plus a "catalog not built" message.
2. **`requirements.txt` cannot install a working app.** `backend/ocr_service.py` does
   `from rapidfuzz import fuzz, process`, but `rapidfuzz` is not listed; `medicine_routes`
   imports `ocr_service`, so a fresh `pip install -r requirements.txt` leaves the app unable
   to start. The pin `Flask==2.3.3` also disagrees with the installed 3.1.3.
3. **README setup is stale.** It tells you to run `migrate_medicine_quality.py`; the actual
   path is `etl/build_database.py` + `etl/repair_ingredient_prefixes.py --apply`. It also says
   nothing about the missing dataset, the env keys, or the price-honesty rules.
4. **No price can support a saving.** Only 6 rows have a current listing price; the Jan
   Aushadhi edition is over a year old (400-day window in `REFERENCE_PRICE_MAX_AGE_DAYS`).
   Until a newer edition is imported or PharmEasy prices are refreshed, the product's headline
   promise is unverifiable in practice.
5. **Prefix repair not applied.** `Naxdom 500` and 6,118 other medicines still carry the
   manufacturer inside the ingredient name, so they match nothing. The tool exists and is
   tested; it needs a dry run, then `--apply` on the real database.
6. **Composition gap.** 77,435 rows are incomplete; the backfill fills ~78% of sampled rows at
   ~2.3 s/row (about 49 hours for the whole pool) and needs the source CSV. Enrichment is
   currently on-demand only (DrugSetu key was a 100-request trial and is exhausted).
6b. **Ciprofloxacin is recorded as ofloxacin.** Every Ciplox/Cifran row reads "Ofloxacin"; only
   26 rows say ciprofloxacin; ~600 "Ofloxacin" tablets at 250/500/750/1000 mg are probably cipro.
   From the source data. Alternatives *matching* measured 100% precision / 99.6% recall on 2026-11-27
   (`python -m eval.alternatives_eval`, plus `sample 40`), but that cannot see catalog errors like this.
7. **Suggestions are prefix-only.** (Search itself measured 97.2% / 96.1% top-1 on the
   72-query and 51-query held-out sets on 2026-11-27: `python -m eval.search_eval all`.) `metformin` suggests nothing because seeds/queries match
   name and brand, never ingredients; the starter list is fixed at 8 brands.
8. **Loose ends in the UI.** Footer "Resources"/"Legal" links point at `/`, the hero voice
   button has no handler, the disease page updates without a live region.

## Suggested next steps (ranked by payoff)

1. Make "clone → run" true: add `rapidfuzz`, align the Flask pin, degrade data endpoints on an
   empty database, rewrite the README setup section, and add a smoke test that boots against an
   empty DB. Highest value for anyone else starting on this repo.
2. Refresh price evidence: import the current Jan Aushadhi edition with a real edition date (or
   schedule PharmEasy refreshes) so at least some comparisons can claim a dated saving.
3. Apply the prefix repair to `database/medfinder.db` (dry run first, back up the file) — it
   unlocks alternatives for ~6.1k medicines at no code risk.
4. Decide the data delivery story (the 110 MB CSV / 221 MB DB cannot live in git; document a
   download step or ship a small sample).
5. Extend suggestions to ingredient queries and replace the fixed seed list with a data-driven
   popular-brand list.
6. Only then: new features (prescription history, saved lists). Keep the honesty rules intact.

## Gotchas

- `.claude/settings.local.json` is tracked but intentionally left modified locally — it holds
  machine-specific permissions; do not commit or "fix" it.
- `.qoder/` (agent skills) and `datasets/Jan-Aushadhi-Drug-Code-List-2025.pdf` are untracked.
- `backend/medicine_service.py` is an empty legacy file; the real one is
  `backend/services/medicine_service.py`.
- Windows: console encoding needs UTF-8 (`etl` scripts force it); SQLite handles are closed in
  `finally` in `backend/database.py` so temp-DB tests can clean up.
- Static asset versions live in `templates/base.html` (`v=20261127-1`); bump them when editing
  `static/js/app.js` or `static/css/style.css`.
