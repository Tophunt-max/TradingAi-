"""Account lookup and the DEMO cash ledger.

Ledger accounting (DEMO only; see docs/accounts.md):
- OPENING_CREDIT  +10,000 when the account is created.
- TRADE_BUY       -(fill price x qty) for every BUY fill.
- TRADE_SELL      +(fill price x qty) for every SELL fill.
- CHARGES         -(estimated charges) for every fill that has charges.
- RESET_CLOSE     +(mark x qty) for each position closed by a demo reset.
- RESET_ADJUSTMENT the signed amount that brings the balance back to the opening balance.

cash = balance_after of the newest entry (= sum of all amounts).
equity = cash + market value of open positions (last price, or entry price if never marked).
Realised P&L is never posted separately: it is already the difference between the TRADE_SELL
credits and the TRADE_BUY debits, so posting it again would double count.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.accounts import DEMO_OPENING_BALANCE, Account, LedgerEntry
from app.models.user import UserPreferences

ZERO = Decimal("0")
MODES = ("DEMO", "REAL")


class AccountModeMismatch(RuntimeError):
    """An operation reached an account (or adapter) of the wrong mode. Never caught to
    continue: it signals a bug that would otherwise mix DEMO and REAL."""


def get_account(db: Session, user_id: uuid.UUID, mode: str) -> Account | None:
    return db.scalar(select(Account).where(Account.user_id == user_id, Account.mode == mode))


def create_demo_account(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> Account:
    """New DEMO account with the virtual opening credit, in the caller's transaction."""
    now = now or utcnow()
    acc = Account(user_id=user_id, mode="DEMO", status="ACTIVE", broker="PAPER",
                  opening_balance=DEMO_OPENING_BALANCE, reset_count=0, created_at=now)
    db.add(acc)
    db.flush()
    post(db, acc, "OPENING_CREDIT", DEMO_OPENING_BALANCE, note="Virtual opening balance.",
         ts=now)
    return acc


def get_or_create_demo(db: Session, user_id: uuid.UUID) -> Account:
    acc = get_account(db, user_id, "DEMO")
    if acc is not None:
        return acc
    try:
        with db.begin_nested():
            acc = create_demo_account(db, user_id)
    except IntegrityError:  # created concurrently by another request
        acc = get_account(db, user_id, "DEMO")
    assert acc is not None
    return acc


def active_mode(db: Session, user_id: uuid.UUID) -> str:
    prefs = db.get(UserPreferences, user_id)
    mode = prefs.active_account_mode if prefs else "DEMO"
    return mode if mode in MODES else "DEMO"


def resolve(db: Session, user_id: uuid.UUID, mode: str | None = None) -> tuple[str, Account | None]:
    """The account an API request acts on: the explicit mode, else the user's active mode.
    DEMO always exists (created lazily); REAL may be None (not connected)."""
    mode = mode or active_mode(db, user_id)
    if mode == "DEMO":
        return mode, get_or_create_demo(db, user_id)
    return mode, get_account(db, user_id, mode)


# ------------------------------------------------------------------- ledger


def last_entry(db: Session, account_id: uuid.UUID) -> LedgerEntry | None:
    return db.scalar(select(LedgerEntry).where(LedgerEntry.account_id == account_id)
                     .order_by(LedgerEntry.seq.desc()).limit(1))


def cash(db: Session, account: Account) -> Decimal | None:
    """DEMO cash from the ledger. None for REAL: real balances come only from a broker."""
    if account.mode != "DEMO":
        return None
    e = last_entry(db, account.id)
    return e.balance_after if e else ZERO


def post(db: Session, account: Account, kind: str, amount: Decimal, *,
         ref_type: str | None = None, ref_id: str | None = None, note: str | None = None,
         ts: datetime | None = None) -> LedgerEntry:
    if account.mode != "DEMO":
        raise AccountModeMismatch("Ledger entries exist only for DEMO accounts.")
    last = last_entry(db, account.id)
    bal = (last.balance_after if last else ZERO) + amount
    e = LedgerEntry(account_id=account.id, account_mode="DEMO",
                    seq=(last.seq if last else 0) + 1, kind=kind, amount=amount,
                    balance_after=bal, ref_type=ref_type, ref_id=ref_id,
                    note=note[:300] if note else None, created_at=ts or utcnow())
    db.add(e)
    db.flush()
    return e
