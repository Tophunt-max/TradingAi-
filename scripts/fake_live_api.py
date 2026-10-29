"""Run the real API in INDSTOCKS mode against the in-memory INDstocks fake.

For end-to-end UI testing of live-shaped data without a real access token.
Never use this for anything else: all prices come from tests/fake_indstocks.py.

Usage (from repo root): python scripts/fake_live_api.py  (env as for the API)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend")]
os.environ.setdefault("MARKET_DATA_MODE", "INDSTOCKS")
os.environ.setdefault("INDSTOCKS_ACCESS_TOKEN", "test-indstocks-token")

import uvicorn  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.market_data import factory  # noqa: E402
from app.market_data.indstocks.client import IndstocksClient  # noqa: E402
from app.market_data.indstocks.instruments import sync_source  # noqa: E402
from tests.fake_indstocks import TOKEN, FakeIndstocks  # noqa: E402


def main() -> None:
    expiry = (datetime.now(UTC) + timedelta(days=3)).date()
    fake = FakeIndstocks(expiry=expiry)

    def make(_settings):  # type: ignore[no-untyped-def]
        return IndstocksClient(base_url="https://fake.indstocks", token=TOKEN,
                               transport=fake.transport(), sleep=lambda _s: None)

    factory.client_factory = make
    db = next(get_db())
    for source in ("index", "equity", "fno"):
        log = sync_source(db, make(None), source)
        print(f"fake sync {source}: {log.status} rows={log.row_count}", flush=True)
    db.close()
    from app.main import app

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
