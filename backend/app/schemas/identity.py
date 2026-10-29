"""Normalisation and validation for login identifiers and passwords."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from email_validator import EmailNotValidError, validate_email

_PHONE_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def normalize_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address.") from exc
    return result.normalized.lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"[\s\-()]", "", value.strip())
    if _INDIAN_MOBILE.match(digits):
        return f"+91{digits}"
    if digits.startswith("0") and _INDIAN_MOBILE.match(digits[1:]):
        return f"+91{digits[1:]}"
    if _PHONE_E164.match(digits):
        return digits
    raise ValueError("Enter a valid mobile number (e.g. +919876543210).")


@dataclass(frozen=True)
class Identifier:
    kind: Literal["email", "phone"]
    value: str


def parse_identifier(value: str) -> Identifier:
    value = value.strip()
    if "@" in value:
        return Identifier("email", normalize_email(value))
    return Identifier("phone", normalize_phone(value))


def validate_password_strength(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one number.")
    return password
