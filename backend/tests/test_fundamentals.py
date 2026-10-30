"""Fundamentals (work package G). All data here is FICTIONAL test data (TESTCO, PEERA,
PEERB); the repository ships no real company numbers."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.fundamentals import ratios as RT
from app.fundamentals import service, store
from app.models import FundamentalReport, User, UserRole
from tests.conftest import auth_header, register_and_verify

D = Decimal
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def q(end: str, rev: int, ni: int, *, pub_days: int = 40, **over) -> dict:
    """A fictional quarterly statement (amounts in INR crore-like units)."""
    pe = date.fromisoformat(end)
    pub = datetime.combine(pe + timedelta(days=pub_days), datetime.min.time(), UTC)
    row = {"symbol": "TESTCO", "period_end": end, "period_type": "Q",
           "value_basis": "REPORTED", "source": "TEST_FIXTURE", "published_at": pub.isoformat(),
           "available_at": (pub + timedelta(hours=2)).isoformat(),
           "revenue": rev, "gross_profit": rev * 0.4, "operating_income": rev * 0.2,
           "net_income": ni, "eps": ni / 100, "operating_cash_flow": ni * 1.1, "capex": 30,
           "total_debt": 400, "interest_expense": 10, "equity": 1000,
           "capital_employed": 1400, "total_assets": 2000, "current_assets": 700,
           "current_liabilities": 400, "shares_outstanding": 100,
           "dividends_per_share": 0.5, "cash": 150}
    row.update(over)
    return row


QUARTERS = [q("2024-03-31", 1000, 100), q("2024-06-30", 1050, 105),
            q("2024-09-30", 1100, 110), q("2024-12-31", 1150, 115),
            q("2025-03-31", 1200, 120)]


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


@pytest.fixture
def admin(client, otp, engine):
    tokens = register_and_verify(client, otp, "admin@example.com")
    with Session(engine) as db:
        db.execute(update(User).where(User.email == "admin@example.com")
                   .values(role=UserRole.ADMIN))
        db.commit()
    return auth_header(tokens)


def periods(rows: list[dict]) -> list[dict]:
    body = store.FundamentalsImport.model_validate({"reports": rows})
    return [{"period_end": r.period_end, "period_type": r.period_type,
             "value_basis": r.value_basis, "revision": 0, "source": r.source,
             "items": store._items(r), "quality_flags": [], "available_at": r.available_at}
            for r in body.reports]


# ---------------------------------------------------------------- pure ratios


def test_ratio_formulas_on_fictional_testco():
    ps = periods(QUARTERS)
    r = RT.period_ratios(ps[-1], ps)
    assert r["revenue_growth_yoy"] == 0.2           # 1200 vs 1000
    assert r["revenue_growth_qoq"] == round(50 / 1150, 4)
    assert r["gross_margin"] == 0.4 and r["operating_margin"] == 0.2
    assert r["net_margin"] == 0.1 and r["eps_growth_yoy"] == 0.2
    assert D(r["free_cash_flow"]) == D("132") - D("30")
    assert r["debt_to_equity"] == 0.4 and r["interest_coverage"] == 24.0
    assert D(r["working_capital"]) == D("300") and r["current_ratio"] == 1.75
    assert r["cash_conversion"] == 1.1
    assert r["accruals_ratio"] == round((120 - 132) / 2000, 4) and r["earnings_quality"] == "OK"
    base = RT.ttm(ps)
    assert base["items"]["eps"] == D("4.5")         # 1.05 + 1.10 + 1.15 + 1.20
    assert base["items"]["net_income"] == D("450")
    ret = RT.return_ratios(base)
    assert ret["roe"] == 0.45 and ret["roce"] == round(900 / 1400, 4)
    v = RT.valuation(D("90"), base)
    assert v["pe"] == 20.0 and v["pb"] == 9.0       # BVPS = 1000/100
    assert v["ps"] == 2.0 and v["dividend_yield"] == round(2 / 90, 4)
    assert v["ev_ebitda"] is None and v["forward_pe"] is None
    assert any("depreciation" in n for n in v["notes"])


def test_ev_ebitda_needs_da_line_and_forward_pe_needs_estimates():
    rows = [dict(r, depreciation_amortization=20) for r in QUARTERS]
    ps = periods(rows)
    est = periods([q("2025-06-30", 1250, 125, value_basis="ESTIMATED", eps=1.3)])
    v = RT.valuation(D("90"), RT.ttm(ps), est)
    # EV = 90*100 + 400 - 150; EBITDA = (210+220+230+240) + 4*20
    assert v["ev_ebitda"] == round((9000 + 400 - 150) / 980, 2)
    assert v["forward_pe"] == round(90 / 1.3, 2) and v["forward_pe_basis"].startswith("ESTIMATE")


def test_negative_earnings_and_missing_inputs_are_never_guessed():
    ps = periods([dict(r, eps=-1, net_income=-100) for r in QUARTERS])
    v = RT.valuation(D("50"), RT.ttm(ps))
    assert v["pe"] is None and any("negative" in n for n in v["notes"])
    assert RT.div(D(1), D(0)) is None and RT.growth(D(5), None) is None
    assert RT.valuation(None, RT.ttm(ps))["pe"] is None
    only3 = periods(QUARTERS[:3])
    assert RT.ttm(only3) is None


def test_quality_and_coverage_flags():
    bad = periods([q("2024-03-31", 100, 200, gross_profit=150, eps=-2)])[0]
    flags = RT.quality_flags(bad)
    assert {"GROSS_PROFIT_EXCEEDS_REVENUE", "NET_INCOME_EXCEEDS_REVENUE",
            "EPS_SIGN_MISMATCH"} <= set(flags)
    gap = periods([QUARTERS[0], QUARTERS[2]])
    cov = RT.coverage_flags(gap, date(2026, 1, 1), 150)
    assert any(f.startswith("MISSING_QUARTER") for f in cov)
    assert any(f.startswith("STALE") for f in cov) and any("ONLY_2" in f for f in cov)
    assert RT.percentile_rank([1, 2, 3, 4], 3) == 0.625


# ------------------------------------------------------------ point in time


def test_restatement_published_later_is_invisible_earlier(engine):
    with Session(engine) as db:
        store.import_all(db, store.FundamentalsImport.model_validate({"reports": QUARTERS}))
        restated = q("2025-03-31", 1200, 90, value_basis="RESTATED", pub_days=120)
        res = store.import_all(db, store.FundamentalsImport.model_validate(
            {"reports": [restated]}))
        db.commit()
        assert res["restatements"] == 1
        rows = db.scalars(select(FundamentalReport).where(
            FundamentalReport.period_end == date(2025, 3, 31))).all()
        assert sorted(r.revision for r in rows) == [0, 1]  # original row kept, never overwritten
        before = datetime(2025, 6, 1, tzinfo=UTC)   # after original (May 10), before restatement
        after = datetime(2025, 8, 1, tzinfo=UTC)
        p_before = [p for p in store.as_of(db, "TESTCO", before)
                    if p["period_end"] == date(2025, 3, 31)]
        p_after = [p for p in store.as_of(db, "TESTCO", after)
                   if p["period_end"] == date(2025, 3, 31)]
        assert p_before[0]["items"]["net_income"] == D("120") and p_before[0]["revision"] == 0
        assert p_after[0]["items"]["net_income"] == D("90") and p_after[0]["revision"] == 1
        # Nothing published after ts is visible at all.
        early = store.as_of(db, "TESTCO", datetime(2024, 9, 1, tzinfo=UTC))
        assert [p["period_end"] for p in early] == [date(2024, 3, 31), date(2024, 6, 30)]
        # Re-importing identical data is idempotent.
        again = store.import_all(db, store.FundamentalsImport.model_validate(
            {"reports": QUARTERS}))
        assert again["reports_unchanged"] == 5 and again["reports_added"] == 0


def test_available_at_gates_visibility_even_after_publication(engine):
    late = q("2024-03-31", 1000, 100)
    late["available_at"] = "2024-12-01T00:00:00+00:00"   # system received it much later
    with Session(engine) as db:
        store.import_all(db, store.FundamentalsImport.model_validate({"reports": [late]}))
        assert store.as_of(db, "TESTCO", datetime(2024, 6, 1, tzinfo=UTC)) == []
        assert len(store.as_of(db, "TESTCO", datetime(2024, 12, 2, tzinfo=UTC))) == 1


def test_import_validation_rejects_future_and_missing_provenance():
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError, match="future"):
        store.FundamentalsImport.model_validate({"reports": [q("2024-03-31", 1, 1,
                                                                available_at=future)]})
    no_src = q("2024-03-31", 1, 1)
    del no_src["source"]
    with pytest.raises(ValidationError):
        store.FundamentalsImport.model_validate({"reports": [no_src]})
    naive = q("2024-03-31", 1, 1, published_at="2024-05-01T00:00:00")
    with pytest.raises(ValidationError, match="timezone"):
        store.FundamentalsImport.model_validate({"reports": [naive]})
    early = q("2024-03-31", 1, 1, available_at="2024-01-01T00:00:00+00:00")
    with pytest.raises(ValidationError, match="earlier"):
        store.FundamentalsImport.model_validate({"reports": [early]})
    with pytest.raises(ValidationError):
        store.FundamentalsImport.model_validate({"reports": [q("2024-03-31", 1, 1,
                                                                capex=-5)]})


# ------------------------------------------------------------------- API


def test_api_no_data_index_and_admin_only_import(client, h, admin):
    b = client.get("/api/v1/fundamentals/RELIANCE", headers=h).json()
    assert b["status"] == "NO_DATA" and "never fabricates" in b["message"]
    assert b["ratios"] is None and b["flags"] == ["NO_DATA"]
    idx = client.get("/api/v1/fundamentals/NIFTY", headers=h).json()
    assert idx["status"] == "INDEX" and "unavailable" in idx["message"]
    r = client.post("/api/v1/admin/fundamentals/import", headers=h, json={"reports": QUARTERS})
    assert r.status_code == 403
    r = client.post("/api/v1/admin/fundamentals/import", headers=admin,
                    json={"reports": [q("2024-03-31", 1, 1, available_at="2999-01-01T00:00:00Z")]})
    assert r.status_code == 422
    future = client.get("/api/v1/fundamentals/TESTCO?as_of=2999-01-01T00:00:00Z", headers=h)
    assert future.status_code == 422


def test_api_ratios_peers_and_point_in_time(client, h, admin):
    peers = [dict(r, symbol=s, operating_income=r["revenue"] * m)
             for s, m in (("PEERA", 0.1), ("PEERB", 0.3)) for r in QUARTERS]
    body = {"reports": QUARTERS + peers,
            "profiles": [{"symbol": s, "sector": "FICTIONAL_SECTOR", "source": "TEST_FIXTURE",
                          "available_at": "2024-01-01T00:00:00Z"}
                         for s in ("TESTCO", "PEERA", "PEERB")],
            "corporate_actions": [{"symbol": "TESTCO", "action_type": "DIVIDEND",
                                   "ex_date": "2025-02-10", "amount": 0.5,
                                   "announced_at": "2025-01-20T10:00:00+05:30",
                                   "available_at": "2025-01-20T12:00:00+05:30",
                                   "source": "TEST_FIXTURE"}]}
    r = client.post("/api/v1/admin/fundamentals/import", headers=admin, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["reports_added"] == 15 and r.json()["actions_added"] == 1
    b = client.get("/api/v1/fundamentals/TESTCO?as_of=2025-06-01T00:00:00Z", headers=h).json()
    assert b["status"] == "OK" and b["ratios"]["revenue_growth_yoy"] == 0.2
    assert b["peers"]["count"] == 2 and b["peers"]["percentiles"]["operating_margin"] == 0.5
    # TESTCO has no D&A line: EV/EBITDA is null, never guessed.
    assert b["valuation"]["ev_ebitda"] is None
    assert b["price"]["label"] in ("SIMULATED", "UNAVAILABLE")
    assert b["explanation"]["supporting"] and any("Revenue growth year on year 20.0%" in s
                                                   for s in b["explanation"]["supporting"])
    assert any("context and event-risk" in s for s in b["explanation"]["context"])
    assert b["corporate_actions"][0]["action_type"] == "DIVIDEND"
    early = client.get("/api/v1/fundamentals/TESTCO?as_of=2024-09-01T00:00:00Z",
                       headers=h).json()
    assert len(early["periods"]) == 2 and early["ratios"]["revenue_growth_qoq"] == 0.05
    assert any("ONLY_2" in f for f in early["flags"])


def test_index_constituent_weighted_view(engine):
    with Session(engine) as db:
        store.import_all(db, store.FundamentalsImport.model_validate({
            "reports": QUARTERS,
            "profiles": [{"symbol": "TESTIDX", "is_index": True, "source": "TEST_FIXTURE",
                          "available_at": "2024-01-01T00:00:00Z",
                          "constituents": [{"symbol": "TESTCO", "weight": 0.6},
                                           {"symbol": "MISSINGCO", "weight": 0.4}]}]}))
        out = service.build(db, None, "TESTIDX", datetime(2025, 6, 1, tzinfo=UTC), 150)
        assert out["status"] == "INDEX"
        assert out["constituents"]["weight_with_data"] == 0.6
        assert out["constituents"]["weighted_ratios"]["operating_margin"] == 0.2


def test_cli_loader_reads_json_and_csv(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "import_fundamentals", Path(__file__).resolve().parents[2] / "scripts"
        / "import_fundamentals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    jp = tmp_path / "f.json"
    jp.write_text(json.dumps({"reports": QUARTERS[:2]}))
    assert len(mod.load(jp).reports) == 2
    cp = tmp_path / "f.csv"
    cp.write_text("symbol,period_end,period_type,source,published_at,revenue,net_income\n"
                  "TESTCO,2024-03-31,Q,TEST_FIXTURE,2024-05-10T00:00:00+00:00,1000,\n")
    r = mod.load(cp).reports[0]
    assert r.revenue == D("1000") and r.net_income is None   # empty cell = missing, not 0
    assert mod.main([str(cp), "--dry-run"]) == 0
