"""
============================================================
MED FINDER
ETL MODULE : REPAIR INGREDIENT PREFIXES

Purpose:
Some source composition strings start with the manufacturer
name instead of the ingredient:

    "Cure Healthcare Naproxen (500mg) + Domperidone (10mg)"

parse_composition() keeps that whole phrase as the ingredient
("curehealthcarenaproxen"), so the product can never match a
cleanly recorded Naproxen product and gets no alternatives.

This module learns the manufacturer phrases from the data and
removes them. A phrase is only learned when it is a separate
word (or words) in front of >= 3 distinct recorded ingredients,
and a name is only rewritten when:

  - the prefix was learned from the catalog itself, and
  - the remainder is an ingredient name the catalog really uses.

A whole name that is itself a known ingredient is never split
("levocetirizine", "methylprednisolone"), so real drug names are
not damaged by a manufacturer that happens to look like a prefix.

Repairs medicine_ingredients and medicines.composition, then
recomputes product_key. search_norm (name + brand) is untouched.

Re-run after `python -m etl.build_database`, which rebuilds these
tables from the source dataset.

Usage:
    python -m etl.repair_ingredient_prefixes
    python -m etl.repair_ingredient_prefixes --sample 30
    python -m etl.repair_ingredient_prefixes --apply
============================================================
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.config import MEDICINE_DATABASE
from backend.medicine_data import make_product_key, normalize_medication_text, parse_composition

# A remainder must be an ingredient this catalog actually records, and a
# prefix must lead at least this many different ingredients before it is
# treated as a manufacturer name rather than part of a drug name.
MIN_REMAINDER_ROWS = 25
MIN_REMAINDER_INGREDIENTS = 3
MIN_PREFIX_LENGTH = 4

_OPEN_PAREN = re.compile(r"\(")


# ============================================================
# Prefix learning
# ============================================================

def _word_splits(name):
    """Yield (prefix, remainder) norms for every word boundary in `name`."""
    words = str(name or "").split()
    for index in range(1, len(words)):
        prefix = normalize_medication_text(" ".join(words[:index]))
        remainder = normalize_medication_text(" ".join(words[index:]))
        if prefix and remainder:
            yield prefix, remainder


def learn_prefixes(name_counts, known_names, min_ingredients=MIN_REMAINDER_INGREDIENTS,
                   min_prefix_length=MIN_PREFIX_LENGTH):
    """Learn manufacturer phrases from recorded ingredient names.

    `name_counts` maps a display ingredient name to its row count and
    `known_names` is the set of normalized names recorded often enough to be
    treated as real ingredients. Returns `{prefix: {remainder: row_count}}`.
    """
    groups = {}
    for name, count in name_counts.items():
        words = str(name or "").split()
        if len(words) < 2:
            continue
        # The whole phrase is already a catalog ingredient: never split it,
        # even if a shorter drug name happens to sit at its end.
        if normalize_medication_text(name) in known_names:
            continue
        for prefix, remainder in _word_splits(name):
            if len(prefix) < min_prefix_length or remainder not in known_names:
                continue
            remainders = groups.setdefault(prefix, {})
            remainders[remainder] = remainders.get(remainder, 0) + count
    return {
        prefix: remainders
        for prefix, remainders in groups.items()
        if len(remainders) >= min_ingredients
    }


def known_ingredient_names(norm_counts, min_rows=MIN_REMAINDER_ROWS):
    """Normalized names recorded often enough to be trusted as ingredients."""
    return {name for name, count in norm_counts.items() if count >= min_rows}


def repair_name(name, prefixes, known_names):
    """Return the name without a learned manufacturer prefix, or None.

    The remainder must still be a recorded ingredient, so a learned phrase is
    never stripped from a name the catalog does not otherwise know.
    """
    words = str(name or "").split()
    if len(words) < 2:
        return None
    for index in range(len(words) - 1, 0, -1):
        if normalize_medication_text(" ".join(words[:index])) not in prefixes:
            continue
        remainder = " ".join(words[index:])
        if normalize_medication_text(remainder) in known_names:
            return remainder
    return None


def repair_component(component, renames):
    """Replace the ingredient words of one `Name (strength)` component."""
    match = _OPEN_PAREN.search(component)
    if not match:
        return component
    name_part, tail = component[:match.start()], component[match.start():]
    parsed = parse_composition(component)
    if len(parsed) != 1:
        return component
    new_name = renames.get(parsed[0]["ingredient_name_norm"])
    if not new_name:
        return component
    return f"{new_name} {tail.lstrip()}"


def repair_composition(composition, renames):
    """Rewrite only the component names that carry a learned prefix."""
    if not composition or not renames:
        return composition
    parts = re.split(r"\s*\+\s*", str(composition))
    return " + ".join(repair_component(part, renames) for part in parts)


# ============================================================
# Database
# ============================================================

def _connect():
    connection = sqlite3.connect(MEDICINE_DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def load_name_counts(connection):
    """Return (display name counts, normalized name counts)."""
    display_counts, norm_counts = {}, {}
    for row in connection.execute(
        "SELECT ingredient_name, ingredient_name_norm, COUNT(*) AS rows_count "
        "FROM medicine_ingredients GROUP BY ingredient_name, ingredient_name_norm"
    ):
        name, norm, count = row["ingredient_name"], row["ingredient_name_norm"], row["rows_count"]
        display_counts[name] = display_counts.get(name, 0) + count
        norm_counts[norm] = norm_counts.get(norm, 0) + count
    return display_counts, norm_counts


def find_repairs(connection, prefixes, known_names):
    """Return {medicine_id: {old_norm: new_name}} for every row to rewrite."""
    renames = {}
    for row in connection.execute(
        "SELECT medicine_id, ingredient_name, ingredient_name_norm FROM medicine_ingredients "
        "ORDER BY medicine_id"
    ):
        new_name = repair_name(row["ingredient_name"], prefixes, known_names)
        if new_name is None or new_name == row["ingredient_name"]:
            continue
        renames.setdefault(row["medicine_id"], {})[row["ingredient_name_norm"]] = new_name
    return renames


def plan_repairs(connection, renames):
    """Recompute product keys and composition text for the affected medicines."""
    if not renames:
        return []
    placeholders = ",".join("?" * len(renames))
    medicines = {
        row["id"]: row
        for row in connection.execute(
            f"SELECT id, medicine_name, brand_name, strength, dosage_form, composition, "
            f"release_type, pack_size FROM medicines WHERE id IN ({placeholders})",
            tuple(renames),
        )
    }
    plan = []
    for medicine_id, names in renames.items():
        medicine = medicines.get(medicine_id)
        if medicine is None:
            continue
        composition = repair_composition(medicine["composition"], names)
        plan.append({
            "medicine_id": medicine_id,
            "medicine_name": medicine["medicine_name"],
            "old_composition": medicine["composition"],
            "new_composition": composition,
            "new_product_key": make_product_key(
                medicine["medicine_name"], medicine["brand_name"], medicine["strength"],
                medicine["dosage_form"], composition, medicine["release_type"],
                medicine["pack_size"],
            ),
            "renames": names,
        })
    return plan


def apply_repairs(connection, plan):
    for item in plan:
        for old_norm, new_name in item["renames"].items():
            connection.execute(
                "UPDATE medicine_ingredients SET ingredient_name = ?, ingredient_name_norm = ? "
                "WHERE medicine_id = ? AND ingredient_name_norm = ?",
                (new_name, normalize_medication_text(new_name), item["medicine_id"], old_norm),
            )
        if item["new_composition"] != item["old_composition"]:
            connection.execute(
                "UPDATE medicines SET composition = ?, product_key = ? WHERE id = ?",
                (item["new_composition"], item["new_product_key"], item["medicine_id"]),
            )
    connection.commit()


# ============================================================
# CLI
# ============================================================

def _report(prefixes, renames, plan, sample):
    ingredient_rows = sum(len(names) for names in renames.values())
    composition_rows = sum(
        1 for item in plan if item["new_composition"] != item["old_composition"]
    )
    print(f"Learned manufacturer phrases : {len(prefixes)}")
    print(f"Medicines affected           : {len(plan)}")
    print(f"Ingredient rows rewritten    : {ingredient_rows}")
    print(f"Composition texts rewritten  : {composition_rows}")
    ordered = sorted(prefixes.items(), key=lambda item: -sum(item[1].values()))
    print("\nTop phrases:")
    for prefix, remainders in ordered[:10]:
        print(f"  {prefix:<28} {sum(remainders.values()):>6} rows, {len(remainders)} ingredients")
    if plan and sample:
        print("\nSample rewrites:")
        step = max(1, len(plan) // sample)
        for item in plan[::step][:sample]:
            names = ", ".join(f"{old} -> {new}" for old, new in sorted(item["renames"].items()))
            print(f"  {item['medicine_id']} {item['medicine_name']}: {names}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("Purpose:")[1].split("Usage:")[0].strip())
    parser.add_argument("--apply", action="store_true", help="write the repair (default is a dry run)")
    parser.add_argument("--sample", type=int, default=15, help="rewrites to print (0 for none)")
    args = parser.parse_args(argv)

    connection = _connect()
    try:
        display_counts, norm_counts = load_name_counts(connection)
        known_names = known_ingredient_names(norm_counts)
        prefixes = learn_prefixes(display_counts, known_names)
        renames = find_repairs(connection, prefixes, known_names)
        plan = plan_repairs(connection, renames)
        _report(prefixes, renames, plan, args.sample)
        if not plan:
            print("\nNothing to repair.")
            return 0
        if not args.apply:
            print("\nDRY RUN - nothing was written. Re-run with --apply to repair.")
            return 0
        apply_repairs(connection, plan)
        print(f"\nApplied: {len(plan)} medicines repaired.")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
