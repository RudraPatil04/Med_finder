r"""Share of search results that carry a real product photo (local catalog, nothing written).

    venv\Scripts\python.exe -m eval.image_coverage            # first 100 eval queries, top 12 cards
    venv\Scripts\python.exe -m eval.image_coverage 100 12

A photo counts only when the row has an https image URL that is not the shared
1mg placeholder; the UI shows a dosage-form drawing for everything else.
"""
import sys

from backend.services import medicine_service
from backend.services.image_lookup import has_real_image
from eval.search_eval import CASES, HELDOUT


def queries(limit):
    seen, out = set(), []
    for q, _, _ in CASES + HELDOUT:
        if q.casefold() not in seen:
            seen.add(q.casefold())
            out.append(q)
    return out[:limit]


def measure(limit=100, cards=12, verbose=True):
    shown = photos = top1 = searches = 0
    for q in queries(limit):
        result = medicine_service.search_medicines_detailed(q, enable_api_fallback=False, allow_correction=True)
        rows = [dict(r) for r in result["rows"]][:cards]
        if not rows:
            continue
        searches += 1
        flags = [has_real_image(r.get("image_url")) for r in rows]
        shown += len(flags)
        photos += sum(flags)
        top1 += flags[0]
        if verbose:
            print(f"{sum(flags):2}/{len(flags):2} {q!r}")
    print(f"\nsearches={searches} cards={shown} with photo={photos} ({100 * photos / max(shown, 1):.1f}%) "
          f"top result with photo={top1}/{searches} ({100 * top1 / max(searches, 1):.1f}%)")
    return photos, shown, top1, searches


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    measure(*args)
