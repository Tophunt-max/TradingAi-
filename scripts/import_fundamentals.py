"""Import fundamentals from a licensed source export (JSON or CSV).

Usage (from the repo root, DATABASE_URL set as for the API):
  python scripts/import_fundamentals.py data.json
  python scripts/import_fundamentals.py reports.csv [--dry-run]

JSON: {"reports": [...], "profiles": [...], "corporate_actions": [...]} (see
docs/fundamentals-events.md). CSV: one statement per row with the report fields
as columns (symbol, period_end, period_type, value_basis, source, published_at,
revenue, ...); empty cells are missing values, never zero.

Every row is validated exactly like POST /api/v1/admin/fundamentals/import:
`source` and a timezone-aware `published_at` are required and future times are
rejected. Nothing is bundled: this repository ships no company data.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.fundamentals.store import FundamentalsImport, import_all  # noqa: E402


def load(path: Path) -> FundamentalsImport:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            rows = [{k: v for k, v in r.items() if v not in ("", None)}
                    for r in csv.DictReader(f)]
        return FundamentalsImport.model_validate({"reports": rows})
    return FundamentalsImport.model_validate(json.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="Validate only.")
    args = ap.parse_args(argv)
    try:
        body = load(args.file)
    except (ValidationError, ValueError) as exc:
        print(f"Rejected: {exc}")
        return 1
    print(f"Valid: {len(body.reports)} reports, {len(body.profiles)} profiles, "
          f"{len(body.corporate_actions)} corporate actions.")
    if args.dry_run:
        return 0
    db = next(get_db())
    try:
        res = import_all(db, body)
        db.commit()
        print(json.dumps(res, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
