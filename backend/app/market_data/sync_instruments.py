"""Download the INDstocks instrument master into the database.

Usage (from backend/):  python -m app.market_data.sync_instruments [equity fno index]
Run it once a day before market open (lot sizes and contracts change).
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_db
from app.market_data.factory import get_client
from app.market_data.indstocks.instruments import sync_source

SOURCES = ("index", "equity", "fno")


def main(argv: list[str]) -> int:
    configure_logging()
    sources = argv or list(SOURCES)
    bad = set(sources) - set(SOURCES)
    if bad:
        print(f"Unknown source(s): {', '.join(sorted(bad))}. Use: {' '.join(SOURCES)}")
        return 2
    client = get_client(get_settings())
    db = next(get_db())
    failed = 0
    try:
        for source in sources:
            log = sync_source(db, client, source)
            print(
                f"{source}: {log.status} rows={log.row_count} rejected={log.rejected_rows}"
                + (f" ({log.message})" if log.message else "")
            )
            failed += log.status != "SUCCESS"
    finally:
        db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
