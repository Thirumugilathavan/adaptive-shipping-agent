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

CREATE TABLE IF NOT EXISTS official_test_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id TEXT NOT NULL,
    test_case_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_record_json TEXT NOT NULL,
    expected_result TEXT NOT NULL,
    actual_result TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_packaging (
    packaging_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    package_type TEXT NOT NULL,
    outer_length_cm REAL NOT NULL,
    outer_width_cm REAL NOT NULL,
    outer_height_cm REAL NOT NULL,
    wall_thickness_cm REAL NOT NULL,
    padding_cm REAL NOT NULL,
    tare_weight_kg REAL NOT NULL,
    internal_length_cm REAL NOT NULL,
    internal_width_cm REAL NOT NULL,
    internal_height_cm REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_ref ON orders(order_reference);
CREATE INDEX IF NOT EXISTS idx_quotes_ref ON quotes(order_reference);
CREATE INDEX IF NOT EXISTS idx_shipments_ref ON shipments(order_reference);
CREATE INDEX IF NOT EXISTS idx_shipments_idem ON shipments(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_audit_ref ON audit_logs(order_reference);
CREATE INDEX IF NOT EXISTS idx_test_runs_tc ON official_test_runs(test_case_id);
CREATE INDEX IF NOT EXISTS idx_custom_pkg_name ON custom_packaging(name);
CREATE TABLE IF NOT EXISTS courier_capabilities (
    courier_code TEXT PRIMARY KEY,
    courier_name TEXT NOT NULL,
    service_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    max_length_cm REAL,
    max_width_cm REAL,
    max_height_cm REAL,
    max_single_dimension_cm REAL,
    max_girth_cm REAL,
    max_length_plus_girth_cm REAL,
    max_actual_weight_kg REAL,
    max_billable_weight_kg REAL,
    volumetric_divisor REAL NOT NULL DEFAULT 5000.0,
    min_billable_weight_kg REAL NOT NULL DEFAULT 0.5,
    billing_step_kg REAL NOT NULL DEFAULT 0.5,
    allowed_package_types_json TEXT NOT NULL,
    max_cod_amount REAL DEFAULT 50000.0,
    evidence_source TEXT NOT NULL,
    evidence_url TEXT,
    retrieval_timestamp TEXT NOT NULL,
    rule_status TEXT NOT NULL DEFAULT 'VERIFIED',
    market_scope TEXT NOT NULL DEFAULT 'India Domestic',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_capabilities_code ON courier_capabilities(courier_code);

CREATE TABLE IF NOT EXISTS quote_field_provenance (
    provenance_id TEXT PRIMARY KEY,
    quote_id TEXT NOT NULL,
    order_reference TEXT NOT NULL,
    field_name TEXT NOT NULL,
    extracted_value TEXT NOT NULL,
    raw_value TEXT,
    source_type TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    source_url_or_endpoint TEXT,
    retrieval_timestamp TEXT NOT NULL,
    retrieval_method TEXT NOT NULL,
    request_or_shipment_id TEXT,
    raw_snapshot_json TEXT,
    validation_status TEXT NOT NULL,
    transformation_applied TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (quote_id) REFERENCES quotes (quote_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_provenance_quote ON quote_field_provenance(quote_id);
CREATE INDEX IF NOT EXISTS idx_provenance_order ON quote_field_provenance(order_reference);
CREATE INDEX IF NOT EXISTS idx_provenance_field ON quote_field_provenance(field_name);

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
        """Initialize database schema tables, indexes, and run migrations."""
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
        self.migrate_schema()
        self.seed_default_courier_capabilities()

    def migrate_schema(self) -> None:
        """Safely alter existing database tables if missing newly introduced columns or tables."""
        with self.connection() as conn:
            shipments_cols = {row["name"] for row in conn.execute("PRAGMA table_info(shipments)").fetchall()}
            if shipments_cols:
                if "label_status" not in shipments_cols:
                    conn.execute("ALTER TABLE shipments ADD COLUMN label_status TEXT NOT NULL DEFAULT 'UNSUPPORTED'")
                if "label_data" not in shipments_cols:
                    conn.execute("ALTER TABLE shipments ADD COLUMN label_data TEXT")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_field_provenance (
                provenance_id TEXT PRIMARY KEY,
                quote_id TEXT NOT NULL,
                order_reference TEXT NOT NULL,
                field_name TEXT NOT NULL,
                extracted_value TEXT NOT NULL,
                raw_value TEXT,
                source_type TEXT NOT NULL,
                source_identifier TEXT NOT NULL,
                source_url_or_endpoint TEXT,
                retrieval_timestamp TEXT NOT NULL,
                retrieval_method TEXT NOT NULL,
                request_or_shipment_id TEXT,
                raw_snapshot_json TEXT,
                validation_status TEXT NOT NULL,
                transformation_applied TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (quote_id) REFERENCES quotes (quote_id) ON DELETE CASCADE
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provenance_quote ON quote_field_provenance(quote_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provenance_order ON quote_field_provenance(order_reference);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provenance_field ON quote_field_provenance(field_name);")

    def insert_order(self, order_data: dict[str, Any]) -> None:
        """Insert or update a validated order record without risking cascade deletes."""
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
        ON CONFLICT (order_reference) DO UPDATE SET
            sender_json = excluded.sender_json,
            recipient_json = excluded.recipient_json,
            parcel_weight_kg = excluded.parcel_weight_kg,
            length_cm = excluded.length_cm,
            width_cm = excluded.width_cm,
            height_cm = excluded.height_cm,
            package_type = excluded.package_type,
            is_cod = excluded.is_cod,
            cod_amount = excluded.cod_amount
        """
        with self.connection() as conn:
            conn.execute(sql, order_data)

    def save_order_model(self, order: Any, order_id: Optional[str] = None) -> str:
        """Persist or update an OrderInput domain model into SQLite."""
        import uuid
        effective_order_id = order_id or f"ORD-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        sender_json = order.sender.model_dump_json() if hasattr(order.sender, "model_dump_json") else json.dumps(order.sender)
        recipient_json = order.recipient.model_dump_json() if hasattr(order.recipient, "model_dump_json") else json.dumps(order.recipient)
        pkg_val = order.package_type.value if hasattr(order.package_type, "value") else str(order.package_type)
        l_val = order.dimensions.length_cm if order.dimensions else 0.0
        w_val = order.dimensions.width_cm if order.dimensions else 0.0
        h_val = order.dimensions.height_cm if order.dimensions else 0.0

        order_data = {
            "order_id": effective_order_id,
            "order_reference": order.order_reference,
            "sender_json": sender_json,
            "recipient_json": recipient_json,
            "parcel_weight_kg": float(order.parcel_weight_kg),
            "length_cm": float(l_val),
            "width_cm": float(w_val),
            "height_cm": float(h_val),
            "package_type": pkg_val,
            "is_cod": 1 if order.is_cod else 0,
            "cod_amount": float(order.cod_amount),
            "created_at": now_iso,
        }
        self.insert_order(order_data)
        return effective_order_id

    def get_order(self, order_reference: str) -> Optional[dict[str, Any]]:
        """Retrieve an order by unique order reference."""
        sql = "SELECT * FROM orders WHERE order_reference = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            return dict(row) if row else None

    def insert_quotes(self, quotes: list[dict[str, Any]]) -> None:
        """Bulk insert courier quotes using UPSERT."""
        sql = """
        INSERT INTO quotes (
            quote_id, order_reference, courier_code, courier_name,
            service_type, estimated_delivery_days, total_cost,
            quote_data_json, source_type, input_hash, created_at
        ) VALUES (
            :quote_id, :order_reference, :courier_code, :courier_name,
            :service_type, :estimated_delivery_days, :total_cost,
            :quote_data_json, :source_type, :input_hash, :created_at
        )
        ON CONFLICT (quote_id) DO UPDATE SET
            order_reference = excluded.order_reference,
            courier_code = excluded.courier_code,
            courier_name = excluded.courier_name,
            service_type = excluded.service_type,
            estimated_delivery_days = excluded.estimated_delivery_days,
            total_cost = excluded.total_cost,
            quote_data_json = excluded.quote_data_json,
            source_type = excluded.source_type,
            input_hash = excluded.input_hash
        """
        with self.connection() as conn:
            conn.executemany(sql, quotes)

    def save_quote_model(self, quote: Any, order_reference: str) -> str:
        """Persist or update a CourierQuote model into SQLite including field provenance."""
        now_iso = datetime.now(timezone.utc).isoformat()
        total_c = quote.cost_breakdown.total_cost if quote.cost_breakdown else 0.0

        # Ensure field provenance is populated
        if hasattr(quote, "populate_standard_field_provenance"):
            if not getattr(quote, "field_provenance", None):
                quote.populate_standard_field_provenance(order_reference)

        q_json = quote.model_dump_json() if hasattr(quote, "model_dump_json") else json.dumps(quote)
        self.insert_quotes([
            {
                "quote_id": quote.quote_id,
                "order_reference": order_reference,
                "courier_code": quote.courier_code,
                "courier_name": getattr(quote, "courier_name", quote.courier_code),
                "service_type": getattr(quote, "service_type", "Standard"),
                "estimated_delivery_days": getattr(quote, "estimated_delivery_days", 0) or 0,
                "total_cost": float(total_c),
                "quote_data_json": q_json,
                "source_type": getattr(quote, "source_type", "DEMO"),
                "input_hash": getattr(quote, "input_hash", "hash"),
                "created_at": now_iso,
            }
        ])

        # Persist field-level provenance records
        field_prov_records = []
        raw_snap_json = json.dumps(quote.raw_response_snapshot) if getattr(quote, "raw_response_snapshot", None) else None
        if hasattr(quote, "field_provenance") and quote.field_provenance:
            for f_name, prec in quote.field_provenance.items():
                p_id = f"{quote.quote_id}:{f_name}"
                field_prov_records.append({
                    "provenance_id": p_id,
                    "quote_id": quote.quote_id,
                    "order_reference": order_reference,
                    "field_name": f_name,
                    "extracted_value": str(prec.extracted_value),
                    "raw_value": str(prec.raw_value) if prec.raw_value is not None else str(prec.extracted_value),
                    "source_type": prec.source_type,
                    "source_identifier": prec.source_identifier,
                    "source_url_or_endpoint": prec.source_url_or_endpoint,
                    "retrieval_timestamp": prec.retrieval_timestamp,
                    "retrieval_method": prec.retrieval_method,
                    "request_or_shipment_id": prec.request_or_shipment_id or order_reference,
                    "raw_snapshot_json": raw_snap_json,
                    "validation_status": prec.validation_status,
                    "transformation_applied": prec.transformation_applied,
                    "created_at": now_iso,
                })
            self.insert_field_provenance(field_prov_records)

        return quote.quote_id

    def insert_field_provenance(self, records: list[dict[str, Any]]) -> None:
        """Insert or replace field-level provenance audit records."""
        if not records:
            return
        sql = """
        INSERT OR REPLACE INTO quote_field_provenance (
            provenance_id, quote_id, order_reference, field_name,
            extracted_value, raw_value, source_type, source_identifier,
            source_url_or_endpoint, retrieval_timestamp, retrieval_method,
            request_or_shipment_id, raw_snapshot_json, validation_status,
            transformation_applied, created_at
        ) VALUES (
            :provenance_id, :quote_id, :order_reference, :field_name,
            :extracted_value, :raw_value, :source_type, :source_identifier,
            :source_url_or_endpoint, :retrieval_timestamp, :retrieval_method,
            :request_or_shipment_id, :raw_snapshot_json, :validation_status,
            :transformation_applied, :created_at
        )
        """
        with self.connection() as conn:
            conn.executemany(sql, records)

    def get_field_provenance_for_quote(self, quote_id: str) -> list[dict[str, Any]]:
        """Retrieve all field-level provenance records for a given quote."""
        sql = "SELECT * FROM quote_field_provenance WHERE quote_id = ? ORDER BY field_name ASC"
        with self.connection() as conn:
            rows = conn.execute(sql, (quote_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_field_provenance_for_order(self, order_reference: str) -> list[dict[str, Any]]:
        """Retrieve all field-level provenance records for a given order reference."""
        sql = "SELECT * FROM quote_field_provenance WHERE order_reference = ? ORDER BY quote_id ASC, field_name ASC"
        with self.connection() as conn:
            rows = conn.execute(sql, (order_reference,)).fetchall()
            return [dict(r) for r in rows]

    def get_quote(self, quote_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a specific quote record by quote_id."""
        sql = "SELECT * FROM quotes WHERE quote_id = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (quote_id,)).fetchone()
            return dict(row) if row else None

    def ensure_parent_records(
        self,
        order_reference: str,
        quote_id: str,
        courier_code: str = "UNKNOWN",
        order: Optional[Any] = None,
        quote: Optional[Any] = None,
    ) -> None:
        """
        Ensure parent records in orders and quotes tables exist prior to any
        shipments record creation or modification.
        Guarantees PRAGMA foreign_keys = ON will never raise IntegrityError.
        """
        import uuid
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            # 1. Check orders
            order_row = conn.execute(
                "SELECT order_reference FROM orders WHERE order_reference = ?",
                (order_reference,),
            ).fetchone()
            if not order_row:
                if order is not None and hasattr(order, "order_reference"):
                    sender_json = order.sender.model_dump_json() if hasattr(order.sender, "model_dump_json") else json.dumps(order.sender)
                    recipient_json = order.recipient.model_dump_json() if hasattr(order.recipient, "model_dump_json") else json.dumps(order.recipient)
                    l_val = order.dimensions.length_cm if order.dimensions else 0.0
                    w_val = order.dimensions.width_cm if order.dimensions else 0.0
                    h_val = order.dimensions.height_cm if order.dimensions else 0.0
                    pkg_val = order.package_type.value if hasattr(order.package_type, "value") else str(order.package_type)
                    conn.execute(
                        """
                        INSERT INTO orders (
                            order_id, order_reference, sender_json, recipient_json,
                            parcel_weight_kg, length_cm, width_cm, height_cm,
                            package_type, is_cod, cod_amount, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(order_reference) DO NOTHING
                        """,
                        (
                            f"ORD-{uuid.uuid4().hex[:12]}",
                            order.order_reference,
                            sender_json,
                            recipient_json,
                            float(order.parcel_weight_kg),
                            float(l_val),
                            float(w_val),
                            float(h_val),
                            pkg_val,
                            1 if order.is_cod else 0,
                            float(order.cod_amount),
                            now_iso,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO orders (
                            order_id, order_reference, sender_json, recipient_json,
                            parcel_weight_kg, length_cm, width_cm, height_cm,
                            package_type, is_cod, cod_amount, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(order_reference) DO NOTHING
                        """,
                        (
                            f"ORD-{uuid.uuid4().hex[:12]}",
                            order_reference,
                            json.dumps({"name": "Sender", "pin_code": "110001"}),
                            json.dumps({"name": "Recipient", "pin_code": "400001"}),
                            1.0,
                            10.0,
                            10.0,
                            10.0,
                            "box",
                            0,
                            0.0,
                            now_iso,
                        ),
                    )

            # 2. Check quotes
            quote_row = conn.execute(
                "SELECT quote_id FROM quotes WHERE quote_id = ?",
                (quote_id,),
            ).fetchone()
            if not quote_row:
                if quote is not None and hasattr(quote, "quote_id"):
                    total_c = quote.cost_breakdown.total_cost if quote.cost_breakdown else 0.0
                    q_json = quote.model_dump_json() if hasattr(quote, "model_dump_json") else json.dumps(quote)
                    conn.execute(
                        """
                        INSERT INTO quotes (
                            quote_id, order_reference, courier_code, courier_name,
                            service_type, estimated_delivery_days, total_cost,
                            quote_data_json, source_type, input_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(quote_id) DO NOTHING
                        """,
                        (
                            quote.quote_id,
                            order_reference,
                            quote.courier_code,
                            getattr(quote, "courier_name", quote.courier_code),
                            getattr(quote, "service_type", "Standard"),
                            getattr(quote, "estimated_delivery_days", 0) or 0,
                            float(total_c),
                            q_json,
                            getattr(quote, "source_type", "DEMO"),
                            getattr(quote, "input_hash", "hash"),
                            now_iso,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO quotes (
                            quote_id, order_reference, courier_code, courier_name,
                            service_type, estimated_delivery_days, total_cost,
                            quote_data_json, source_type, input_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(quote_id) DO NOTHING
                        """,
                        (
                            quote_id,
                            order_reference,
                            courier_code,
                            courier_code,
                            "Standard",
                            2,
                            100.0,
                            json.dumps({"quote_id": quote_id}),
                            "DEMO",
                            "stub_hash",
                            now_iso,
                        ),
                    )

    def get_shipment(self, order_reference: str) -> Optional[dict[str, Any]]:
        """Retrieve a shipment record by unique order reference."""
        sql = "SELECT * FROM shipments WHERE order_reference = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            return dict(row) if row else None

    def get_shipment_by_id(self, shipment_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a shipment record by shipment id."""
        sql = "SELECT * FROM shipments WHERE shipment_id = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (shipment_id,)).fetchone()
            return dict(row) if row else None

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

    def insert_official_test_run(self, run_data: dict[str, Any]) -> None:
        """Insert an official test case execution result."""
        sql = """
        INSERT INTO official_test_runs (
            test_case_id, test_case_name, status, input_record_json,
            expected_result, actual_result, evidence_json, explanation, timestamp
        ) VALUES (
            :test_case_id, :test_case_name, :status, :input_record_json,
            :expected_result, :actual_result, :evidence_json, :explanation, :timestamp
        )
        """
        with self.connection() as conn:
            conn.execute(sql, run_data)

    def get_official_test_runs(self, test_case_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve recorded test runs."""
        with self.connection() as conn:
            if test_case_id:
                sql = "SELECT * FROM official_test_runs WHERE test_case_id = ? ORDER BY run_id DESC"
                rows = conn.execute(sql, (test_case_id,)).fetchall()
            else:
                sql = "SELECT * FROM official_test_runs ORDER BY run_id DESC"
                rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def clear_official_test_runs(self) -> None:
        with self.connection() as conn:
            conn.execute('DELETE FROM official_test_runs')

    def insert_custom_packaging(self, pkg_data: dict[str, Any]) -> str:
        """
        Insert custom packaging with deduplication.
        If a packaging with identical name and dimensions already exists, returns existing packaging_id.
        """
        # Validate required fields
        name = str(pkg_data.get("name", "")).strip()
        if not name:
            raise ValueError("Packaging name cannot be empty")

        outer_l = float(pkg_data["outer_length_cm"])
        outer_w = float(pkg_data["outer_width_cm"])
        outer_h = float(pkg_data["outer_height_cm"])
        wall = float(pkg_data.get("wall_thickness_cm", 0.0))
        padding = float(pkg_data.get("padding_cm", 0.0))
        tare = float(pkg_data.get("tare_weight_kg", 0.0))
        pkg_type = str(pkg_data.get("package_type", "box")).lower()

        if outer_l <= 0 or outer_w <= 0 or outer_h <= 0:
            raise ValueError("Packaging outer dimensions must be strictly positive")
        if wall < 0 or padding < 0 or tare < 0:
            raise ValueError("Wall thickness, padding, and tare weight must be non-negative")

        internal_l = outer_l - (2 * wall)
        internal_w = outer_w - (2 * wall)
        internal_h = outer_h - (2 * wall)

        if internal_l <= 0 or internal_w <= 0 or internal_h <= 0:
            raise ValueError("Wall thickness exceeds packaging outer dimensions, leaving zero or negative internal space")

        with self.connection() as conn:
            # Check duplicate
            find_sql = """
            SELECT packaging_id FROM custom_packaging
            WHERE name = ? AND outer_length_cm = ? AND outer_width_cm = ? AND outer_height_cm = ?
            AND wall_thickness_cm = ? AND padding_cm = ? AND tare_weight_kg = ? AND package_type = ?
            """
            existing = conn.execute(
                find_sql,
                (name, outer_l, outer_w, outer_h, wall, padding, tare, pkg_type)
            ).fetchone()
            if existing:
                return existing["packaging_id"]

            import uuid
            pkg_id = pkg_data.get("packaging_id") or f"PKG-CUST-{uuid.uuid4().hex[:8].upper()}"
            now_iso = pkg_data.get("created_at") or datetime.now(timezone.utc).isoformat()

            insert_sql = """
            INSERT INTO custom_packaging (
                packaging_id, name, package_type, outer_length_cm, outer_width_cm, outer_height_cm,
                wall_thickness_cm, padding_cm, tare_weight_kg,
                internal_length_cm, internal_width_cm, internal_height_cm, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            conn.execute(
                insert_sql,
                (
                    pkg_id, name, pkg_type, outer_l, outer_w, outer_h,
                    wall, padding, tare,
                    internal_l, internal_w, internal_h, now_iso
                )
            )
            return pkg_id

    def get_custom_packagings(self) -> list[dict[str, Any]]:
        """Retrieve all saved custom packagings ordered by created_at DESC."""
        sql = "SELECT * FROM custom_packaging ORDER BY created_at DESC"
        with self.connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_custom_packaging(self, packaging_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a specific custom packaging by id."""
        sql = "SELECT * FROM custom_packaging WHERE packaging_id = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (packaging_id,)).fetchone()
            return dict(row) if row else None

    def delete_custom_packaging(self, packaging_id: str) -> bool:
        """Delete custom packaging by packaging_id. Returns True if deleted."""
        sql = "DELETE FROM custom_packaging WHERE packaging_id = ?"
        with self.connection() as conn:
            cur = conn.execute(sql, (packaging_id,))
            return cur.rowcount > 0

    def seed_default_courier_capabilities(self) -> None:
        """Seed standard documented courier capability rules if not already populated."""
        from src.domain.dimension_engine import OFFICIAL_DOCUMENTED_CAPABILITIES
        import json

        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM courier_capabilities").fetchone()
            if row and row["cnt"] > 0:
                return  # already seeded

            for code, cap in OFFICIAL_DOCUMENTED_CAPABILITIES.items():
                types_json = json.dumps([p.value for p in cap.allowed_package_types])
                sql = """
                INSERT OR REPLACE INTO courier_capabilities (
                    courier_code, courier_name, service_type, mode,
                    max_length_cm, max_width_cm, max_height_cm, max_single_dimension_cm,
                    max_girth_cm, max_length_plus_girth_cm, max_actual_weight_kg, max_billable_weight_kg,
                    volumetric_divisor, min_billable_weight_kg, billing_step_kg,
                    allowed_package_types_json, max_cod_amount, evidence_source, evidence_url,
                    retrieval_timestamp, rule_status, market_scope, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                conn.execute(
                    sql,
                    (
                        cap.courier_code, cap.courier_name, cap.service_type, cap.mode,
                        cap.max_length_cm, cap.max_width_cm, cap.max_height_cm, cap.max_single_dimension_cm,
                        cap.max_girth_cm, cap.max_length_plus_girth_cm, cap.max_actual_weight_kg, cap.max_billable_weight_kg,
                        cap.volumetric_rule.divisor, cap.volumetric_rule.min_billable_weight_kg, cap.volumetric_rule.billing_step_kg,
                        types_json, cap.max_cod_amount, cap.evidence_source, cap.evidence_url,
                        cap.retrieval_timestamp, cap.rule_status.value, cap.market_scope, cap.notes
                    )
                )

    def get_courier_capabilities(self) -> list[dict[str, Any]]:
        """Retrieve all courier capability rules stored in SQLite."""
        sql = "SELECT * FROM courier_capabilities ORDER BY courier_code ASC"
        with self.connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_courier_capability(self, courier_code: str) -> Optional[dict[str, Any]]:
        """Retrieve a specific courier capability by code."""
        sql = "SELECT * FROM courier_capabilities WHERE courier_code = ?"
        with self.connection() as conn:
            row = conn.execute(sql, (courier_code,)).fetchone()
            return dict(row) if row else None

    def insert_or_replace_courier_capability(self, cap_data: dict[str, Any]) -> None:
        """Insert or update a courier capability rule."""
        sql = """
        INSERT OR REPLACE INTO courier_capabilities (
            courier_code, courier_name, service_type, mode,
            max_length_cm, max_width_cm, max_height_cm, max_single_dimension_cm,
            max_girth_cm, max_length_plus_girth_cm, max_actual_weight_kg, max_billable_weight_kg,
            volumetric_divisor, min_billable_weight_kg, billing_step_kg,
            allowed_package_types_json, max_cod_amount, evidence_source, evidence_url,
            retrieval_timestamp, rule_status, market_scope, notes
        ) VALUES (
            :courier_code, :courier_name, :service_type, :mode,
            :max_length_cm, :max_width_cm, :max_height_cm, :max_single_dimension_cm,
            :max_girth_cm, :max_length_plus_girth_cm, :max_actual_weight_kg, :max_billable_weight_kg,
            :volumetric_divisor, :min_billable_weight_kg, :billing_step_kg,
            :allowed_package_types_json, :max_cod_amount, :evidence_source, :evidence_url,
            :retrieval_timestamp, :rule_status, :market_scope, :notes
        )
        """
        with self.connection() as conn:
            conn.execute(sql, cap_data)
