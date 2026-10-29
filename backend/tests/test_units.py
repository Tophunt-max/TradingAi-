from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.core.logging import JsonFormatter, redact
from app.core.time import to_ist
from app.market_data.demo import demo_index_quotes
from app.schemas.identity import normalize_phone, parse_identifier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("9876543210", "+919876543210"), ("09876543210", "+919876543210"),
     ("+91 98765-43210", "+919876543210"), ("+14155550100", "+14155550100")],
)
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "5876543210", "abc"])
def test_phone_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_identifier_detects_email():
    ident = parse_identifier("  Foo@Example.COM ")
    assert ident.kind == "email" and ident.value == "foo@example.com"


def test_redaction_of_sensitive_keys():
    out = redact({"password": "p", "nested": {"refresh_token": "t", "ok": 1}, "otp": "1"})
    assert out == {"password": "[REDACTED]", "nested": {"refresh_token": "[REDACTED]", "ok": 1},
                   "otp": "[REDACTED]"}


def test_json_formatter_redacts_extras():
    rec = logging.makeLogRecord({"msg": "hi", "levelname": "INFO", "name": "t",
                                 "api_key": "SECRET", "path": "/x"})
    line = JsonFormatter().format(rec)
    assert "SECRET" not in line and '"/x"' in line


def test_ist_conversion():
    assert to_ist(datetime(2026, 1, 1, 3, 45, tzinfo=UTC)).strftime("%H:%M") == "09:15"


def test_demo_quotes_are_deterministic_and_consistent():
    now = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)
    a, b = demo_index_quotes(now), demo_index_quotes(now)
    assert a == b
    for q in a:
        assert q["last"] - q["prev_close"] == q["change"] or abs(
            (q["last"] - q["prev_close"]) - q["change"]) <= 0.01
