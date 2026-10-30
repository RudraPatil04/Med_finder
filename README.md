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

## Tests

Run the focused offline tests with:

```powershell
python -m unittest test_api_fallback_cache -v
```
