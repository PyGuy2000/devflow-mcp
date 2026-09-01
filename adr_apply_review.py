#!/usr/bin/env python3
"""
Apply a filled-in ADR category review sheet to the overrides file.

The review CSV is a worksheet; nothing reads it. The overrides file
(adr_categories.json) is what the index actually uses. Without this script
every correction has to be typed twice, which is how a backfill stalls.

Fill in correct_layer / correct_domain on the rows the guess got wrong, leave
the rest blank, then run this. Blank rows are skipped, so a half-finished
sheet is fine — run it as often as you like.

Usage:
    python3 adr_apply_review.py                 # apply, then rebuild the index
    python3 adr_apply_review.py --dry-run       # show what would change
    python3 adr_apply_review.py --csv PATH      # a sheet somewhere else
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adr_categories import DOMAIN_LABELS, LAYERS

REVIEW_CSV = Path.home() / "homelab-gitops" / "docs" / "project_notes" / "adr_category_review.csv"
OVERRIDES = Path.home() / "homelab-gitops" / "docs" / "project_notes" / "adr_categories.json"


def main():
    ap = argparse.ArgumentParser(description="Apply a reviewed ADR category sheet.")
    ap.add_argument("--csv", default=str(REVIEW_CSV))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"No review sheet at {csv_path}\nGenerate one: python3 adr_index.py --review-csv {csv_path}")

    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    added, changed, skipped, bad = 0, 0, 0, []

    with open(csv_path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            uid = (row.get("uid") or "").strip()
            layer = (row.get("correct_layer") or "").strip().lower()
            domain = (row.get("correct_domain") or "").strip().lower()

            if not uid or (not layer and not domain):
                skipped += 1
                continue

            # A typo here would silently classify an ADR into a bucket the UI
            # never renders, so reject unknown values instead of storing them.
            if layer and layer not in LAYERS:
                bad.append(f"  line {lineno} {uid}: unknown layer '{layer}'")
                continue
            if domain and domain not in DOMAIN_LABELS:
                bad.append(f"  line {lineno} {uid}: unknown domain '{domain}'")
                continue

            entry = dict(overrides.get(uid) or {}) if isinstance(overrides.get(uid), dict) else {}
            before = dict(entry)
            if layer:
                entry["layer"] = layer
            if domain:
                entry["domain"] = domain

            if uid not in overrides:
                added += 1
            elif entry != before:
                changed += 1
            else:
                skipped += 1
                continue

            overrides[uid] = entry

    if bad:
        print("Rejected rows (fix these and re-run):")
        print("\n".join(bad))
        print()

    print(f"{added} new, {changed} updated, {skipped} unchanged or blank")

    if args.dry_run:
        print("(dry run — nothing written)")
        return

    if not added and not changed:
        print("Nothing to write.")
        return

    OVERRIDES.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OVERRIDES}")

    from adr_index import build, write_index
    index = build(verbose=False)
    write_index(index)
    print(f"Rebuilt index: {index['counts']['adrs']} ADRs, "
          f"{index['health']['unconfirmedCategories']} still unconfirmed")


if __name__ == "__main__":
    main()
