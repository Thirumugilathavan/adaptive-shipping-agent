"""
SQLite database management with WAL mode, foreign keys, and safe schema migrations.
Supports both file-based persistent storage and fast in-memory execution for unit tests.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional


DEFAULT_DB_PATH = Path("shipping_agent.db")


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    order_reference TEXT UNIQUE NOT NULL,
    sender_json TEXT NOT NULL,
    recipient_json TEXT NOT NULL,
    parcel_weight_kg REAL NOT NULL,
    length_cm REAL NOT NULL,
    width_cm REAL NOT NULL,
    height_cm REAL NOT NULL,
    package_type TEXT NOT NULL,
    is_cod INTEGER NOT NULL,
    cod_amount REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    order_reference TEXT NOT NULL,
    courier_code TEXT NOT NULL,
    courier_name TEXT NOT NULL,
    service_type TEXT NOT NULL,
    estimated_delivery_days INTEGER NOT NULL,
    total_cost REAL NOT NULL,
    quote_data_json TEXT NOT NULL,
    source_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_reference) REFERENCES orders (order_reference) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id TEXT PRIMARY KEY,
    order_reference TEXT UNIQUE NOT NULL,
    selected_quote_id TEXT NOT NULL,
    status TEXT NOT NULL,
    courier_code TEXT NOT NULL,
    tracking_number TEXT,
    label_status TEXT NOT NULL DEFAULT 'UNSUPPORTED',
    label_data TEXT,
    idempotency_key TEXT UNIQUE NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_reference) REFERENCES orders (order_reference) ON DELETE CASCADE,
    FOREIGN KEY (selected_quote_id) REFERENCES quotes (quote_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    order_reference TEXT,
    shipment_id TEXT,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_ref ON orders(order_reference);
CREATE INDEX IF NOT EXISTS idx_quotes_ref ON quotes(order_reference);
CREATE INDEX IF NOT EXISTS idx_shipments_ref ON shipments(order_reference);
CREATE INDEX IF NOT EXISTS idx_shipments_idem ON shipments(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_audit_ref ON audit_logs(order_reference);
"""


class DatabaseManager:
    """Manages SQLite connections, transactions, and schema setup."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            # Keep a persistent connection for in-memory mode so tables persist
            self._mem_conn = sqlite3.connect(":memory:", timeout=10.0)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._mem_conn is None:
                conn.close()

    def init_db(self) -> None:
        """Initialize database schema tables and indexes."""
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def insert_order(self, order_data: dict[str, Any]) -> None:
        """Insert a validated order record."""
        sql = """
        INSERT INTO orders (
            order_id, order_reference, sender_json, recipient_json,
            parcel_weight_kg, length_cm, width_cm, height_cm,
            package_type, is_cod, cod_amount, created_at
        ) VALUES (
            :order_id, :order_reference, :sender_json, :recipient_json,
            :parcel_weight_kg, :length_cm, :width_cm, :height_cm,
            :package_type, :is_cod, :cod_amount, :created_at
        )
        """
        with self.connection() as conn:
            conn.execute(sql, order_data)

    def get_order(self, order_reference: str) -> Optional[dict[str, Any]]:
        """Retrieve an order by unique order reference."""
        sql = "SELECT * FROM orders WHERE order_reference = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            return dict(row) if row else None

    def insert_quotes(self, quotes: list[dict[str, Any]]) -> None:
        """Bulk insert courier quotes."""
        sql = """
        INSERT OR REPLACE INTO quotes (
            quote_id, order_reference, courier_code, courier_name,
            service_type, estimated_delivery_days, total_cost,
            quote_data_json, source_type, input_hash, created_at
        ) VALUES (
            :quote_id, :order_reference, :courier_code, :courier_name,
            :service_type, :estimated_delivery_days, :total_cost,
            :quote_data_json, :source_type, :input_hash, :created_at
        )
        """
        with self.connection() as conn:
            conn.executemany(sql, quotes)

    def get_quotes_for_order(self, order_reference: str) -> list[dict[str, Any]]:
        """Retrieve all quotes for an order."""
        sql = "SELECT * FROM quotes WHERE order_reference = ? ORDER BY total_cost ASC"
        with self.connection() as conn:
            rows = conn.execute(sql, (order_reference,)).fetchall()
            return [dict(r) for r in rows]

    def insert_audit_log(
        self,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        order_reference: Optional[str] = None,
        shipment_id: Optional[str] = None,
    ) -> None:
        """Insert a sanitized audit log entry."""
        sql = """
        INSERT INTO audit_logs (
            event_type, order_reference, shipment_id, actor, details_json, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        details_str = json.dumps(details, default=str)
        with self.connection() as conn:
            conn.execute(sql, (event_type, order_reference, shipment_id, actor, details_str, timestamp))

    def get_audit_logs(self, order_reference: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve audit log history."""
        with self.connection() as conn:
            if order_reference:
                sql = "SELECT * FROM audit_logs WHERE order_reference = ? ORDER BY log_id DESC"
                rows = conn.execute(sql, (order_reference,)).fetchall()
            else:
                sql = "SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT 100"
                rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
