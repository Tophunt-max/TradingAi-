from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import redact
from app.core.rate_limit import client_ip
from app.core.request_context import get_request_id
from app.models.audit import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    outcome: str = "SUCCESS",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (caller commits)."""
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        outcome=outcome,
        ip_address=client_ip(request) if request else None,
        user_agent=(request.headers.get("user-agent", "")[:255] if request else None),
        request_id=get_request_id(),
        details=redact(details or {}),
    )
    db.add(entry)
    return entry
