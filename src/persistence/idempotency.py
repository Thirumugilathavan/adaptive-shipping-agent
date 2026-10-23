"""
Idempotency and duplicate prevention engine.
Guarantees at-most-once shipment creation by managing stable references,
in-flight dispatch locks, and state verifications.
"""

from __future__ import annotations

import hashlib
from typing import Optional
from src.domain.models import ShipmentStatus
from src.persistence.database import DatabaseManager


class DuplicateDispatchError(Exception):
    """Raised when an identical dispatch request is attempted concurrently or while in-flight."""
    pass


class ShipmentAlreadyCreatedError(Exception):
    """Raised when an order has already been booked into a carrier shipment."""
    pass


class ReconciliationRequiredError(Exception):
    """Raised when an order is in an uncertain state and must be reconciled before retry."""
    pass


def generate_idempotency_key(order_reference: str, quote_id: str) -> str:
    """
    Generate a deterministic SHA-256 idempotency key from order and quote references.
    """
    raw = f"IDEM:{order_reference.strip()}:{quote_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdempotencyManager:
    """Manages dispatch concurrency locks and prevents duplicate shipment creation."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def check_can_dispatch(self, order_reference: str, idempotency_key: str) -> None:
        """
        Verify that the shipment is eligible for dispatch.
        Raises descriptive exceptions if duplicate or unsafe.
        """
        sql = "SELECT shipment_id, status, idempotency_key FROM shipments WHERE order_reference = ?"
        with self.db.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            if not row:
                return  # No shipment created yet, clear to dispatch

            status = row["status"]
            shipment_id = row["shipment_id"]

            if status == ShipmentStatus.CREATED.value:
                raise ShipmentAlreadyCreatedError(
                    f"Shipment already exists for order '{order_reference}' (ID: {shipment_id}). "
                    f"Duplicate creation prevented."
                )
            if status == ShipmentStatus.DISPATCHING.value:
                raise DuplicateDispatchError(
                    f"Shipment creation is currently in progress for order '{order_reference}'. "
                    f"Wait for the active request to complete."
                )
            if status == ShipmentStatus.CREATION_UNCERTAIN.value:
                raise ReconciliationRequiredError(
                    f"Previous attempt for order '{order_reference}' resulted in an uncertain outcome (timeout/drop). "
                    f"You must reconcile the shipment status with the provider before attempting a new booking."
                )

    def record_dispatch_start(
        self,
        shipment_id: str,
        order_reference: str,
        quote_id: str,
        courier_code: str,
        idempotency_key: str,
        approved_by: str,
        approved_at: str,
        created_at: str,
        order: Optional[Any] = None,
        quote: Optional[Any] = None,
    ) -> None:
        """
        Record dispatch initiation in SQLite, setting state to DISPATCHING.
        Enforces unique constraint on idempotency_key and order_reference.
        Ensures parent records in orders and quotes exist to satisfy foreign keys.
        """
        import sqlite3

        self.db.ensure_parent_records(
            order_reference=order_reference,
            quote_id=quote_id,
            courier_code=courier_code,
            order=order,
            quote=quote,
        )

        self.check_can_dispatch(order_reference, idempotency_key)

        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT shipment_id FROM shipments WHERE order_reference = ?",
                (order_reference,),
            ).fetchone()

            if existing:
                update_sql = """
                UPDATE shipments
                SET shipment_id = ?, selected_quote_id = ?, status = ?,
                    courier_code = ?, idempotency_key = ?, approved_by = ?,
                    approved_at = ?, updated_at = ?
                WHERE order_reference = ?
                """
                conn.execute(
                    update_sql,
                    (
                        shipment_id,
                        quote_id,
                        ShipmentStatus.DISPATCHING.value,
                        courier_code,
                        idempotency_key,
                        approved_by,
                        approved_at,
                        created_at,
                        order_reference,
                    ),
                )
            else:
                insert_sql = """
                INSERT INTO shipments (
                    shipment_id, order_reference, selected_quote_id, status,
                    courier_code, idempotency_key, approved_by, approved_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                try:
                    conn.execute(
                        insert_sql,
                        (
                            shipment_id,
                            order_reference,
                            quote_id,
                            ShipmentStatus.DISPATCHING.value,
                            courier_code,
                            idempotency_key,
                            approved_by,
                            approved_at,
                            created_at,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    self.check_can_dispatch(order_reference, idempotency_key)
                    raise DuplicateDispatchError(
                        f"Shipment creation is already recorded or in progress for order '{order_reference}'."
                    )

    def update_dispatch_outcome(
        self,
        shipment_id: str,
        status: ShipmentStatus,
        tracking_number: Optional[str] = None,
        label_status: str = "UNSUPPORTED",
        label_data: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        """Update shipment status after API call completion."""
        from datetime import datetime, timezone
        effective_updated_at = updated_at or datetime.now(timezone.utc).isoformat()

        sql = """
        UPDATE shipments
        SET status = ?, tracking_number = ?, label_status = ?, label_data = ?, updated_at = ?
        WHERE shipment_id = ?
        """
        with self.db.connection() as conn:
            conn.execute(
                sql,
                (
                    status.value,
                    tracking_number,
                    label_status,
                    label_data,
                    effective_updated_at,
                    shipment_id,
                ),
            )
