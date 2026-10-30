"""Import market events from an official or licensed source export (JSON).

Usage (from the repo root, DATABASE_URL set as for the API):
  python scripts/import_events.py events.json [--dry-run]

JSON: {"events": [...]} with the fields of POST /api/v1/admin/events/import (see
docs/fundamentals-events.md). Re-importing the same file is safe: identical events
are stored as DUPLICATE and ignored by the event-risk gate; changed content
becomes an UPDATE linked to the earlier version. No news is bundled.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.events.store import EventsImport, import_events  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="Validate only.")
    args = ap.parse_args(argv)
    try:
        body = EventsImport.model_validate(json.loads(args.file.read_text(encoding="utf-8")))
    except (ValidationError, ValueError) as exc:
        print(f"Rejected: {exc}")
        return 1
    print(f"Valid: {len(body.events)} events.")
    if args.dry_run:
        return 0
    db = next(get_db())
    try:
        res = import_events(db, body)
        db.commit()
        print(json.dumps({k: v for k, v in res.items() if k != "ids"}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
