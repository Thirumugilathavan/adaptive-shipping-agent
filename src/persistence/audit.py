"""
Audit Logging and Secret Sanitization Engine.
Guarantees a permanent, tamper-evident audit trail for all quotes,
approvals, dispatch attempts, and lifecycle transitions without leaking sensitive secrets.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional
from src.persistence.database import DatabaseManager


class AuditEventType(str, Enum):
    """Standardized event taxonomy for shipping audit trails."""
    ORDER_CREATED = "ORDER_CREATED"
    QUOTES_REQUESTED = "QUOTES_REQUESTED"
    QUOTES_RECEIVED = "QUOTES_RECEIVED"
    PACKAGING_OPTIMIZED = "PACKAGING_OPTIMIZED"
    STRESS_TEST_RUN = "STRESS_TEST_RUN"
    HUMAN_APPROVAL_GRANTED = "HUMAN_APPROVAL_GRANTED"
    DISPATCH_ATTEMPTED = "DISPATCH_ATTEMPTED"
    SHIPMENT_CREATED = "SHIPMENT_CREATED"
    DISPATCH_UNCERTAIN = "DISPATCH_UNCERTAIN"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    SHIPMENT_RECONCILED = "SHIPMENT_RECONCILED"
    SHIPMENT_CANCELLED = "SHIPMENT_CANCELLED"
    QUOTE_REFRESHED = "QUOTE_REFRESHED"
    QUOTE_PRICE_CHANGED = "QUOTE_PRICE_CHANGED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    MANUAL_RESOLUTION_APPLIED = "MANUAL_RESOLUTION_APPLIED"
    LABEL_GENERATED = "LABEL_GENERATED"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    OFFICIAL_TEST_RUN = "OFFICIAL_TEST_RUN"


SENSITIVE_KEY_PATTERNS = re.compile(
    r"(?i)(api[_-]?key|token|auth|password|secret|bearer|authorization|credential)"
)
BEARER_TOKEN_REGEX = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{8,}")


def sanitize_secrets(data: Any) -> Any:
    """
    Recursively sanitize sensitive fields (tokens, passwords, API keys) from
    dictionaries, lists, or strings before writing to audit logs.
    """
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                sanitized[k] = sanitize_secrets(v)
            elif SENSITIVE_KEY_PATTERNS.search(str(k)):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_secrets(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_secrets(item) for item in data]
    elif isinstance(data, str):
        # Redact embedded bearer tokens in string messages/URLs
        return BEARER_TOKEN_REGEX.sub("Bearer [REDACTED]", data)
    return data


class AuditLogger:
    """Provides high-level audit logging with guaranteed secret scrubbing."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def record_event(
        self,
        event_type: AuditEventType,
        actor: str,
        details: dict[str, Any],
        order_reference: Optional[str] = None,
        shipment_id: Optional[str] = None,
    ) -> None:
        """Sanitize and write an audit event to the persistent SQLite log."""
        clean_details = sanitize_secrets(details)
        self.db.insert_audit_log(
            event_type=event_type.value,
            actor=actor,
            details=clean_details,
            order_reference=order_reference,
            shipment_id=shipment_id,
        )

    def get_trail(self, order_reference: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve historical audit events."""
        return self.db.get_audit_logs(order_reference=order_reference)
