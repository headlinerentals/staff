from __future__ import annotations

import os
import sqlite3
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


def _resolve_db_path() -> Path:
    # Priority:
    # 1) HR_DB_PATH (explicit db file path)
    # 2) HR_DATA_DIR/finance_hub.db (persistent data directory)
    # 3) local default beside this file
    explicit = (os.getenv("HR_DB_PATH", "") or "").strip()
    if explicit:
        db_path = Path(explicit).expanduser()
    else:
        data_dir = (os.getenv("HR_DATA_DIR", "") or "").strip()
        if data_dir:
            db_path = Path(data_dir).expanduser() / "finance_hub.db"
        else:
            db_path = Path(__file__).with_name("finance_hub.db")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


DB_PATH = _resolve_db_path()

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

_INVENTORY_NAME_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "for",
    "with",
    "of",
    "rental",
    "rentals",
    "service",
    "services",
    "event",
    "events",
}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL UNIQUE,
                event_date TEXT,
                event_time TEXT NOT NULL DEFAULT '11:00',
                rental_hours REAL NOT NULL DEFAULT 24,
                event_timezone TEXT NOT NULL DEFAULT 'America/Jamaica',
                event_location TEXT,
                document_type TEXT NOT NULL DEFAULT 'invoice',
                order_status TEXT NOT NULL DEFAULT 'confirmed',
                created_by TEXT,
                source_device TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                customer_email TEXT,
                contact_detail TEXT,
                delivered_to TEXT,
                paid_to TEXT,
                payment_status TEXT NOT NULL DEFAULT 'paid_full',
                amount_paid REAL NOT NULL DEFAULT 0,
                payment_notes TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'product',
                quantity REAL NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL DEFAULT 0,
                unit_cost REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date TEXT NOT NULL,
                invoice_id INTEGER,
                category TEXT NOT NULL,
                expense_kind TEXT NOT NULL DEFAULT 'transaction',
                vendor TEXT,
                description TEXT,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS monthly_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                adjustment_type TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invoice_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                original_name TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE,
                item_name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'General',
                unit TEXT NOT NULL DEFAULT 'pcs',
                current_quantity REAL NOT NULL DEFAULT 0,
                reorder_level REAL NOT NULL DEFAULT 0,
                default_rental_price REAL NOT NULL DEFAULT 0,
                default_unit_cost REAL NOT NULL DEFAULT 0,
                unit_weight_kg REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_item_id INTEGER NOT NULL,
                movement_date TEXT NOT NULL,
                movement_type TEXT NOT NULL,
                quantity_change REAL NOT NULL,
                unit_cost REAL,
                reference_invoice_id INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE CASCADE,
                FOREIGN KEY (reference_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS event_notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(invoice_id, notification_type),
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invoice_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER,
                invoice_number TEXT NOT NULL,
                action_type TEXT NOT NULL,
                document_type TEXT NOT NULL,
                order_status TEXT NOT NULL,
                actor_name TEXT,
                device_name TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_invoices_event_date ON invoices(event_date);
            CREATE INDEX IF NOT EXISTS idx_items_invoice_id ON invoice_items(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_invoice_id ON expenses(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
            CREATE INDEX IF NOT EXISTS idx_adjustments_month ON monthly_adjustments(month);
            CREATE INDEX IF NOT EXISTS idx_attach_invoice_id ON invoice_attachments(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory_items(item_name);
            CREATE INDEX IF NOT EXISTS idx_inventory_active ON inventory_items(active);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_item ON inventory_movements(inventory_item_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_date ON inventory_movements(movement_date);
            CREATE INDEX IF NOT EXISTS idx_notification_invoice ON event_notification_log(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_notification_type ON event_notification_log(notification_type);
            CREATE INDEX IF NOT EXISTS idx_invoice_activity_invoice_id ON invoice_activity_log(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_invoice_activity_created_at ON invoice_activity_log(created_at);
            """
        )
        invoice_columns = conn.execute("PRAGMA table_info(invoices)").fetchall()
        invoice_column_names = {str(row[1]) for row in invoice_columns}
        if "event_time" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN event_time TEXT NOT NULL DEFAULT '11:00'"
            )
        if "rental_hours" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN rental_hours REAL NOT NULL DEFAULT 24"
            )
        if "event_timezone" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN event_timezone TEXT NOT NULL DEFAULT 'America/Jamaica'"
            )
        if "event_location" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN event_location TEXT")
        if "document_type" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN document_type TEXT NOT NULL DEFAULT 'invoice'"
            )
        if "order_status" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN order_status TEXT NOT NULL DEFAULT 'confirmed'"
            )
        if "created_by" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN created_by TEXT")
        if "source_device" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN source_device TEXT")
        if "customer_phone" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN customer_phone TEXT")
        if "customer_email" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN customer_email TEXT")
        if "payment_status" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'paid_full'"
            )
        if "amount_paid" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN amount_paid REAL NOT NULL DEFAULT 0"
            )
        if "payment_notes" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN payment_notes TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_doc_type ON invoices(document_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_order_status ON invoices(order_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_payment_status ON invoices(payment_status)")

        columns = conn.execute("PRAGMA table_info(expenses)").fetchall()
        column_names = {str(row[1]) for row in columns}
        if "expense_kind" not in column_names:
            conn.execute(
                "ALTER TABLE expenses ADD COLUMN expense_kind TEXT NOT NULL DEFAULT 'transaction'"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_kind ON expenses(expense_kind)")

        inventory_columns = conn.execute("PRAGMA table_info(inventory_items)").fetchall()
        inventory_column_names = {str(row[1]) for row in inventory_columns}
        if "default_rental_price" not in inventory_column_names:
            conn.execute(
                "ALTER TABLE inventory_items ADD COLUMN default_rental_price REAL NOT NULL DEFAULT 0"
            )
        if "unit_weight_kg" not in inventory_column_names:
            conn.execute(
                "ALTER TABLE inventory_items ADD COLUMN unit_weight_kg REAL NOT NULL DEFAULT 0"
            )


def fetch_dataframe(query: str, params: Iterable | None = None) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params or ())


def upsert_invoice(
    invoice_number: str,
    event_date: str | None = None,
    event_time: str = "11:00",
    rental_hours: float = 24.0,
    event_timezone: str = "America/Jamaica",
    event_location: str = "",
    document_type: str = "invoice",
    order_status: str = "confirmed",
    created_by: str = "",
    source_device: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    customer_email: str = "",
    contact_detail: str = "",
    delivered_to: str = "",
    paid_to: str = "",
    payment_status: str = "paid_full",
    amount_paid: float = 0,
    payment_notes: str = "",
    notes: str = "",
) -> int:
    number = (invoice_number or "").strip()
    if not number:
        raise ValueError("Invoice number is required.")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, event_date, event_time, rental_hours, event_timezone, event_location,
                document_type, order_status, created_by, source_device,
                customer_name, customer_phone, customer_email, contact_detail,
                delivered_to, paid_to, payment_status, amount_paid, payment_notes, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                event_date = COALESCE(excluded.event_date, invoices.event_date),
                event_time = CASE
                    WHEN excluded.event_time <> '' THEN excluded.event_time
                    ELSE invoices.event_time
                END,
                rental_hours = CASE
                    WHEN excluded.rental_hours > 0 THEN excluded.rental_hours
                    ELSE invoices.rental_hours
                END,
                event_timezone = CASE
                    WHEN excluded.event_timezone <> '' THEN excluded.event_timezone
                    ELSE invoices.event_timezone
                END,
                event_location = CASE
                    WHEN excluded.event_location <> '' THEN excluded.event_location
                    ELSE invoices.event_location
                END,
                document_type = CASE
                    WHEN excluded.document_type IN ('quote', 'invoice') THEN excluded.document_type
                    ELSE invoices.document_type
                END,
                order_status = CASE
                    WHEN excluded.order_status IN ('pending', 'confirmed', 'cancelled') THEN excluded.order_status
                    ELSE invoices.order_status
                END,
                created_by = CASE
                    WHEN excluded.created_by <> '' THEN excluded.created_by
                    ELSE invoices.created_by
                END,
                source_device = CASE
                    WHEN excluded.source_device <> '' THEN excluded.source_device
                    ELSE invoices.source_device
                END,
                customer_name = CASE
                    WHEN excluded.customer_name <> '' THEN excluded.customer_name
                    ELSE invoices.customer_name
                END,
                customer_phone = CASE
                    WHEN excluded.customer_phone <> '' THEN excluded.customer_phone
                    ELSE invoices.customer_phone
                END,
                customer_email = CASE
                    WHEN excluded.customer_email <> '' THEN excluded.customer_email
                    ELSE invoices.customer_email
                END,
                contact_detail = CASE
                    WHEN excluded.contact_detail <> '' THEN excluded.contact_detail
                    ELSE invoices.contact_detail
                END,
                delivered_to = CASE
                    WHEN excluded.delivered_to <> '' THEN excluded.delivered_to
                    ELSE invoices.delivered_to
                END,
                paid_to = CASE
                    WHEN excluded.paid_to <> '' THEN excluded.paid_to
                    ELSE invoices.paid_to
                END,
                payment_status = CASE
                    WHEN excluded.payment_status IN ('unpaid', 'deposit_paid', 'paid_full') THEN excluded.payment_status
                    ELSE invoices.payment_status
                END,
                amount_paid = CASE
                    WHEN excluded.amount_paid >= 0 THEN excluded.amount_paid
                    ELSE invoices.amount_paid
                END,
                payment_notes = CASE
                    WHEN excluded.payment_notes <> '' THEN excluded.payment_notes
                    ELSE invoices.payment_notes
                END,
                notes = CASE
                    WHEN excluded.notes <> '' THEN excluded.notes
                    ELSE invoices.notes
                END
            """,
            (
                number,
                event_date,
                event_time.strip() or "11:00",
                float(rental_hours if rental_hours and rental_hours > 0 else 24.0),
                event_timezone.strip() or "America/Jamaica",
                event_location.strip(),
                (document_type or "invoice").strip().lower(),
                (order_status or "confirmed").strip().lower(),
                created_by.strip(),
                source_device.strip(),
                customer_name.strip(),
                customer_phone.strip(),
                customer_email.strip(),
                contact_detail.strip(),
                delivered_to.strip(),
                paid_to.strip(),
                (payment_status or "paid_full").strip().lower(),
                float(amount_paid if amount_paid and amount_paid > 0 else 0.0),
                payment_notes.strip(),
                notes.strip(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = ?",
            (number,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Unable to save invoice.")
        return int(row["id"])


def replace_invoice_items(invoice_id: int, items: pd.DataFrame) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
        for _, raw in items.iterrows():
            item_name = str(raw.get("item_name", "")).strip()
            if not item_name:
                continue

            quantity = float(raw.get("quantity") or 0)
            unit_price = float(raw.get("unit_price") or 0)
            unit_cost = float(raw.get("unit_cost") or 0)
            if quantity <= 0:
                continue

            item_type = str(raw.get("item_type", "product")).strip() or "product"
            conn.execute(
                """
                INSERT INTO invoice_items (
                    invoice_id, item_name, item_type, quantity, unit_price, unit_cost
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
            )


def add_invoice_item(
    invoice_id: int,
    item_name: str,
    item_type: str,
    quantity: float,
    unit_price: float,
    unit_cost: float = 0,
) -> int:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM invoice_items
            WHERE invoice_id = ?
              AND item_name = ?
              AND item_type = ?
              AND ABS(quantity - ?) < 0.0001
              AND ABS(unit_price - ?) < 0.0001
              AND ABS(unit_cost - ?) < 0.0001
            LIMIT 1
            """,
            (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO invoice_items (
                invoice_id, item_name, item_type, quantity, unit_price, unit_cost
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
        )
        return int(cursor.lastrowid)


def add_expense(
    expense_date: str,
    amount: float,
    category: str,
    invoice_id: int | None = None,
    expense_kind: str = "transaction",
    vendor: str = "",
    description: str = "",
) -> int:
    if amount <= 0:
        raise ValueError("Expense amount must be positive.")
    allowed_kinds = {"transaction", "recurring_monthly", "summary_rollup", "adjustment"}
    normalized_kind = (expense_kind or "transaction").strip().lower()
    if normalized_kind not in allowed_kinds:
        raise ValueError("Invalid expense kind.")

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM expenses
            WHERE date(expense_date) = date(?)
              AND COALESCE(invoice_id, -1) = COALESCE(?, -1)
              AND lower(category) = lower(?)
              AND lower(COALESCE(expense_kind, 'transaction')) = lower(?)
              AND lower(COALESCE(vendor, '')) = lower(?)
              AND ABS(amount - ?) < 0.0001
              AND lower(COALESCE(description, '')) = lower(?)
            LIMIT 1
            """,
            (
                expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                amount,
                description.strip(),
            ),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO expenses (
                expense_date, invoice_id, category, expense_kind, vendor, description, amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                description.strip(),
                amount,
            ),
        )
        return int(cursor.lastrowid)


def update_expense(
    expense_id: int,
    expense_date: str,
    amount: float,
    category: str,
    invoice_id: int | None = None,
    expense_kind: str = "transaction",
    vendor: str = "",
    description: str = "",
) -> None:
    if int(expense_id) <= 0:
        raise ValueError("Expense id is required.")
    if amount <= 0:
        raise ValueError("Expense amount must be positive.")
    allowed_kinds = {"transaction", "recurring_monthly", "summary_rollup", "adjustment"}
    normalized_kind = (expense_kind or "transaction").strip().lower()
    if normalized_kind not in allowed_kinds:
        raise ValueError("Invalid expense kind.")

    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE expenses
            SET expense_date = ?,
                invoice_id = ?,
                category = ?,
                expense_kind = ?,
                vendor = ?,
                description = ?,
                amount = ?
            WHERE id = ?
            """,
            (
                expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                description.strip(),
                float(amount),
                int(expense_id),
            ),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Expense record not found.")


def delete_expense(expense_id: int) -> None:
    if int(expense_id) <= 0:
        raise ValueError("Expense id is required.")
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM expenses WHERE id = ?",
            (int(expense_id),),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Expense record not found.")


def delete_invoice(invoice_id: int) -> dict:
    inv_id = int(invoice_id)
    if inv_id <= 0:
        raise ValueError("Invoice id is required.")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, invoice_number
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Invoice not found.")

        attachment_rows = conn.execute(
            """
            SELECT file_path
            FROM invoice_attachments
            WHERE invoice_id = ?
            """,
            (inv_id,),
        ).fetchall()
        attachment_paths = [str(r["file_path"] or "").strip() for r in attachment_rows]

        # Remove auto-generated rental movements tied to this invoice to keep availability correct.
        conn.execute(
            """
            DELETE FROM inventory_movements
            WHERE reference_invoice_id = ?
              AND movement_type IN ('Auto Rental Out', 'Auto Rental Return')
            """,
            (inv_id,),
        )

        conn.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
        return {
            "invoice_id": inv_id,
            "invoice_number": str(row["invoice_number"] or "").strip(),
            "attachment_paths": attachment_paths,
        }


def purge_all_records(preserve_settings: bool = True) -> dict:
    """
    Clear operational data for a clean restart while optionally preserving app settings.
    By default, settings (including Finance password) are preserved.
    """
    tables_to_clear = [
        "event_notification_log",
        "invoice_activity_log",
        "inventory_movements",
        "invoice_items",
        "invoice_attachments",
        "expenses",
        "monthly_adjustments",
        "invoices",
        "inventory_items",
    ]
    deleted_counts: dict[str, int] = {}

    with get_connection() as conn:
        attachment_rows = conn.execute(
            "SELECT file_path FROM invoice_attachments"
        ).fetchall()
        attachment_paths = [str(r["file_path"] or "").strip() for r in attachment_rows]

        for table in tables_to_clear:
            row = conn.execute(f"SELECT COUNT(*) AS row_count FROM {table}").fetchone()
            deleted_counts[table] = int(row["row_count"]) if row is not None else 0
            conn.execute(f"DELETE FROM {table}")

        if not preserve_settings:
            row = conn.execute("SELECT COUNT(*) AS row_count FROM app_settings").fetchone()
            deleted_counts["app_settings"] = int(row["row_count"]) if row is not None else 0
            conn.execute("DELETE FROM app_settings")

        sequence_targets = tables_to_clear.copy()
        if not preserve_settings:
            sequence_targets.append("app_settings")
        for table in sequence_targets:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

    return {
        "deleted_counts": deleted_counts,
        "attachment_paths": attachment_paths,
        "preserved_settings": bool(preserve_settings),
    }


def add_monthly_adjustment(
    month: str,
    adjustment_type: str,
    amount: float,
    description: str = "",
) -> int:
    normalized_month = (month or "").strip()[:7]
    if len(normalized_month) != 7:
        raise ValueError("Month must be YYYY-MM.")
    if amount <= 0:
        raise ValueError("Adjustment amount must be positive.")

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM monthly_adjustments
            WHERE month = ?
              AND lower(adjustment_type) = lower(?)
              AND ABS(amount - ?) < 0.0001
              AND lower(COALESCE(description, '')) = lower(?)
            LIMIT 1
            """,
            (normalized_month, adjustment_type.strip(), amount, description.strip()),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO monthly_adjustments (month, adjustment_type, description, amount)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_month, adjustment_type.strip(), description.strip(), amount),
        )
        return int(cursor.lastrowid)


def invoice_options(
    include_quotes: bool = True,
    confirmed_only: bool = False,
) -> pd.DataFrame:
    where_parts: list[str] = []
    if not include_quotes:
        where_parts.append("lower(COALESCE(document_type, 'invoice')) = 'invoice'")
    if confirmed_only:
        where_parts.append("lower(COALESCE(order_status, 'confirmed')) = 'confirmed'")

    where_clause = ""
    if where_parts:
        where_clause = "WHERE " + " AND ".join(where_parts)

    return fetch_dataframe(
        f"""
        SELECT
            id,
            invoice_number,
            COALESCE(event_date, '') AS event_date,
            COALESCE(event_time, '11:00') AS event_time,
            COALESCE(document_type, 'invoice') AS document_type,
            COALESCE(order_status, 'confirmed') AS order_status,
            COALESCE(payment_status, 'paid_full') AS payment_status,
            COALESCE(amount_paid, 0) AS amount_paid,
            COALESCE(created_by, '') AS created_by,
            COALESCE(source_device, '') AS source_device,
            COALESCE(customer_name, '') AS customer_name
        FROM invoices
        {where_clause}
        ORDER BY
            CASE WHEN event_date IS NULL THEN 1 ELSE 0 END,
            event_date DESC,
            invoice_number DESC
        """
    )


def invoice_meta_by_number(invoice_number: str) -> dict | None:
    number = (invoice_number or "").strip()
    if not number:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                invoice_number,
                COALESCE(document_type, 'invoice') AS document_type,
                COALESCE(order_status, 'confirmed') AS order_status,
                COALESCE(payment_status, 'paid_full') AS payment_status,
                COALESCE(amount_paid, 0) AS amount_paid
            FROM invoices
            WHERE invoice_number = ?
            LIMIT 1
            """,
            (number,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def invoice_export_bundle(invoice_id: int) -> tuple[dict, pd.DataFrame]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                invoice_number,
                COALESCE(event_date, '') AS event_date,
                COALESCE(event_time, '11:00') AS event_time,
                COALESCE(rental_hours, 24) AS rental_hours,
                COALESCE(event_timezone, 'America/Jamaica') AS event_timezone,
                COALESCE(NULLIF(event_location, ''), delivered_to, '') AS event_location,
                COALESCE(document_type, 'invoice') AS document_type,
                COALESCE(order_status, 'confirmed') AS order_status,
                COALESCE(created_by, '') AS created_by,
                COALESCE(source_device, '') AS source_device,
                COALESCE(customer_name, '') AS customer_name,
                COALESCE(customer_phone, '') AS customer_phone,
                COALESCE(customer_email, '') AS customer_email,
                COALESCE(contact_detail, '') AS contact_detail,
                COALESCE(delivered_to, '') AS delivered_to,
                COALESCE(paid_to, '') AS paid_to,
                COALESCE(payment_status, 'paid_full') AS payment_status,
                COALESCE(amount_paid, 0) AS amount_paid,
                COALESCE(payment_notes, '') AS payment_notes,
                COALESCE(notes, '') AS notes
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (int(invoice_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Invoice not found.")

        items = pd.read_sql_query(
            """
            SELECT
                COALESCE(item_name, '') AS item_name,
                COALESCE(item_type, 'product') AS item_type,
                COALESCE(quantity, 0) AS quantity,
                COALESCE(unit_price, 0) AS unit_price,
                COALESCE(unit_cost, 0) AS unit_cost,
                COALESCE(quantity, 0) * COALESCE(unit_price, 0) AS line_total
            FROM invoice_items
            WHERE invoice_id = ?
            ORDER BY id ASC
            """,
            conn,
            params=(int(invoice_id),),
        )
    return dict(row), items


def add_invoice_attachment(
    invoice_id: int,
    file_path: str,
    file_type: str,
    original_name: str,
    notes: str = "",
) -> int:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM invoice_attachments
            WHERE invoice_id = ?
              AND file_path = ?
            LIMIT 1
            """,
            (invoice_id, file_path),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO invoice_attachments (
                invoice_id, file_path, file_type, original_name, notes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_id, file_path, file_type, original_name, notes.strip()),
        )
        return int(cursor.lastrowid)


def load_invoice_attachments(invoice_id: int) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT id, invoice_id, file_path, file_type, original_name, notes, created_at
        FROM invoice_attachments
        WHERE invoice_id = ?
        ORDER BY created_at DESC
        """,
        (invoice_id,),
    )


def delete_invoice_attachment(attachment_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, invoice_id, file_path, file_type, original_name
            FROM invoice_attachments
            WHERE id = ?
            LIMIT 1
            """,
            (int(attachment_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Attachment not found.")

        conn.execute(
            "DELETE FROM invoice_attachments WHERE id = ?",
            (int(attachment_id),),
        )
    return dict(row)


def upcoming_invoices(days_ahead: int = 14) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT
                i.id,
                i.invoice_number,
                i.event_date,
                COALESCE(i.event_time, '11:00') AS event_time,
                COALESCE(i.rental_hours, 24) AS rental_hours,
                COALESCE(NULLIF(i.event_location, ''), i.delivered_to, '') AS event_location,
                COALESCE(i.customer_name, '') AS customer_name,
                COALESCE(i.contact_detail, '') AS contact_detail,
                COALESCE(SUM(it.quantity * it.unit_price), 0) AS revenue
            FROM invoices i
            LEFT JOIN invoice_items it ON it.invoice_id = i.id
            WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
              AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
              AND date(i.event_date) >= date('now', 'localtime')
              AND date(i.event_date) <= date('now', 'localtime', '+' || ? || ' day')
            GROUP BY i.id
            ORDER BY date(i.event_date) ASC
            """,
            conn,
            params=(days_ahead,),
        )


def log_invoice_activity(
    invoice_id: int,
    invoice_number: str,
    action_type: str,
    document_type: str,
    order_status: str,
    actor_name: str = "",
    device_name: str = "",
    notes: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO invoice_activity_log (
                invoice_id,
                invoice_number,
                action_type,
                document_type,
                order_status,
                actor_name,
                device_name,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(invoice_id),
                (invoice_number or "").strip(),
                (action_type or "updated").strip().lower(),
                (document_type or "invoice").strip().lower(),
                (order_status or "confirmed").strip().lower(),
                (actor_name or "").strip(),
                (device_name or "").strip(),
                (notes or "").strip(),
            ),
        )
    return int(cursor.lastrowid)


def set_invoice_payment_status(
    invoice_id: int,
    payment_status: str,
    amount_paid: float,
    payment_notes: str = "",
) -> None:
    normalized = (payment_status or "").strip().lower()
    if normalized not in {"unpaid", "deposit_paid", "paid_full"}:
        raise ValueError("Invalid payment status.")
    safe_paid = max(0.0, float(amount_paid or 0.0))
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE invoices
            SET
                payment_status = ?,
                amount_paid = ?,
                payment_notes = CASE
                    WHEN ? <> '' THEN ?
                    ELSE payment_notes
                END
            WHERE id = ?
            """,
            (normalized, safe_paid, payment_notes.strip(), payment_notes.strip(), int(invoice_id)),
        )


def load_invoice_build_log(limit: int = 200) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            id,
            invoice_id,
            invoice_number,
            action_type,
            document_type,
            order_status,
            actor_name,
            device_name,
            notes,
            created_at
        FROM invoice_activity_log
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def expense_total_for_invoice_category(
    invoice_id: int,
    category: str,
    excluded_vendors: tuple[str, ...] = (),
) -> float:
    params: list = [invoice_id, category.strip()]
    vendor_clause = ""
    if excluded_vendors:
        placeholders = ", ".join(["?"] * len(excluded_vendors))
        vendor_clause = f" AND COALESCE(vendor, '') NOT IN ({placeholders})"
        params.extend(excluded_vendors)

    query = f"""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE invoice_id = ?
          AND lower(category) = lower(?)
          {vendor_clause}
    """

    with get_connection() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    if row is None:
        return 0.0
    return float(row["total"] or 0.0)


def cleanup_legacy_double_counts() -> dict[str, int]:
    monthly_rollup_cols = (
        "Re-Rental",
        "Wages",
        "Bad Debt",
        "Petrol (Add By Invoice #)",
        "Unforseen Expenses",
        "Unforeseen Expenses",
    )
    result = {
        "removed_monthly_rollups": 0,
        "removed_working_expense_imports": 0,
        "removed_legacy_invoice_summaries": 0,
    }

    with get_connection() as conn:
        placeholders = ", ".join(["?"] * len(monthly_rollup_cols))
        result["removed_monthly_rollups"] = conn.execute(
            f"""
            DELETE FROM expenses
            WHERE vendor = 'Monthly Ledger'
              AND category IN ({placeholders})
              AND lower(COALESCE(expense_kind, 'transaction')) <> 'summary_rollup'
            """,
            monthly_rollup_cols,
        ).rowcount

        result["removed_working_expense_imports"] = conn.execute(
            """
            DELETE FROM expenses
            WHERE category = 'Working Expense (Imported)'
            """
        ).rowcount

        result["removed_legacy_invoice_summaries"] = conn.execute(
            """
            DELETE FROM expenses
            WHERE vendor = 'Legacy Sheet'
              AND category IN ('Wages', 'Re-Rental')
              AND invoice_id IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM expenses d
                WHERE d.invoice_id = expenses.invoice_id
                  AND lower(d.category) = lower(expenses.category)
                  AND COALESCE(d.vendor, '') NOT IN (
                    'Legacy Sheet',
                    'Summary Adjustment',
                    'Monthly Ledger',
                    ''
                  )
              )
            """
        ).rowcount

    return result


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            (key.strip(),),
        ).fetchone()
    if row is None:
        return default
    return str(row["setting_value"])


def set_setting(key: str, value: str) -> None:
    setting_key = key.strip()
    if not setting_key:
        raise ValueError("Setting key is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (setting_key, str(value)),
        )


def upsert_inventory_item(
    item_name: str,
    sku: str = "",
    category: str = "General",
    unit: str = "pcs",
    reorder_level: float = 0,
    default_rental_price: float = 0,
    default_unit_cost: float = 0,
    unit_weight_kg: float = 0,
    active: int = 1,
) -> int:
    name = (item_name or "").strip()
    normalized_sku = (sku or "").strip() or None
    if not name:
        raise ValueError("Inventory item name is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO inventory_items (
                sku, item_name, category, unit, reorder_level, default_rental_price, default_unit_cost, unit_weight_kg, active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(item_name) DO UPDATE SET
                sku = CASE WHEN excluded.sku <> '' THEN excluded.sku ELSE inventory_items.sku END,
                category = excluded.category,
                unit = excluded.unit,
                reorder_level = excluded.reorder_level,
                default_rental_price = excluded.default_rental_price,
                default_unit_cost = excluded.default_unit_cost,
                unit_weight_kg = excluded.unit_weight_kg,
                active = excluded.active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                normalized_sku,
                name,
                (category or "General").strip(),
                (unit or "pcs").strip(),
                float(reorder_level or 0),
                float(default_rental_price or 0),
                float(default_unit_cost or 0),
                float(unit_weight_kg or 0),
                int(1 if active else 0),
            ),
        )
        row = conn.execute(
            "SELECT id FROM inventory_items WHERE item_name = ?",
            (name,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Could not save inventory item.")
    return int(row["id"])


def update_inventory_item_values(
    item_id: int,
    item_name: str,
    category: str,
    unit: str,
    reorder_level: float,
    default_rental_price: float,
    active: int,
    quantity_change: float = 0.0,
    target_quantity: float | None = None,
    movement_notes: str = "Adjusted from Inventory Price List editor.",
) -> int:
    inv_id = int(item_id)
    name = (item_name or "").strip()
    if not name:
        raise ValueError("Item name is required.")

    qty_delta = float(quantity_change or 0.0)
    target_qty = None if target_quantity is None else float(target_quantity)
    with get_connection() as conn:
        current = conn.execute(
            """
            SELECT id, current_quantity
            FROM inventory_items
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Inventory item not found.")

        duplicate = conn.execute(
            """
            SELECT id
            FROM inventory_items
            WHERE lower(trim(item_name)) = lower(trim(?))
              AND id <> ?
            LIMIT 1
            """,
            (name, inv_id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(f"An inventory item named '{name}' already exists.")

        current_qty = float(current["current_quantity"] or 0.0)
        if target_qty is not None:
            safe_target = max(0.0, float(target_qty))
            next_qty = safe_target
            qty_delta = safe_target - current_qty
        else:
            next_qty = current_qty + qty_delta
            if next_qty < 0:
                next_qty = 0.0
                qty_delta = -current_qty

        conn.execute(
            """
            UPDATE inventory_items
            SET item_name = ?,
                category = ?,
                unit = ?,
                current_quantity = ?,
                reorder_level = ?,
                default_rental_price = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                name,
                (category or "General").strip() or "General",
                (unit or "pcs").strip() or "pcs",
                float(next_qty),
                float(reorder_level or 0.0),
                float(default_rental_price or 0.0),
                int(1 if active else 0),
                inv_id,
            ),
        )

        if abs(qty_delta) > 1e-9:
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    inv_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Price List Adjustment (+/-)",
                    float(qty_delta),
                    (movement_notes or "").strip(),
                ),
            )
    return inv_id


def delete_inventory_item(item_id: int) -> None:
    inv_id = int(item_id)
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM inventory_items
            WHERE id = ?
            """,
            (inv_id,),
        )


def inventory_item_options(active_only: bool = True) -> pd.DataFrame:
    query = """
        SELECT
            id,
            sku,
            item_name,
            category,
            unit,
            current_quantity,
            reorder_level,
            default_rental_price,
            default_unit_cost,
            unit_weight_kg,
            active
        FROM inventory_items
    """
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY item_name ASC"
    return fetch_dataframe(query)


def add_inventory_movement(
    inventory_item_id: int,
    movement_date: str,
    movement_type: str,
    quantity_change: float,
    unit_cost: float | None = None,
    reference_invoice_id: int | None = None,
    notes: str = "",
) -> int:
    if quantity_change == 0:
        raise ValueError("Quantity change cannot be zero.")
    movement = (movement_type or "").strip()
    if not movement:
        raise ValueError("Movement type is required.")

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_date, movement_type, quantity_change,
                unit_cost, reference_invoice_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(inventory_item_id),
                movement_date,
                movement,
                float(quantity_change),
                None if unit_cost is None else float(unit_cost),
                reference_invoice_id,
                notes.strip(),
            ),
        )
        conn.execute(
            """
            UPDATE inventory_items
            SET current_quantity = current_quantity + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (float(quantity_change), int(inventory_item_id)),
        )
    return int(cursor.lastrowid)


def inventory_movements(limit: int = 200) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            m.id,
            m.movement_date,
            m.movement_type,
            m.quantity_change,
            m.unit_cost,
            m.reference_invoice_id,
            m.notes,
            i.item_name,
            i.sku
        FROM inventory_movements m
        JOIN inventory_items i ON i.id = m.inventory_item_id
        ORDER BY datetime(m.movement_date) DESC, m.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def _inventory_name_keywords(name: str) -> list[str]:
    raw = (name or "").strip().lower()
    if not raw:
        return []

    normalized = raw.replace("&", " and ").replace("×", " x ")
    normalized = re.sub(r"\bby\b", " x ", normalized)
    normalized = re.sub(r"[^a-z0-9x\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    tokens = [_NUMBER_WORDS.get(token, token) for token in normalized.split(" ") if token]

    # Collapse dimensional phrases into stable tokens (example: "10 x 10" -> "10x10").
    collapsed: list[str] = []
    idx = 0
    while idx < len(tokens):
        if (
            idx + 2 < len(tokens)
            and tokens[idx].isdigit()
            and tokens[idx + 1] == "x"
            and tokens[idx + 2].isdigit()
        ):
            collapsed.append(f"{tokens[idx]}x{tokens[idx + 2]}")
            idx += 3
            continue
        collapsed.append(tokens[idx])
        idx += 1

    return [
        token
        for token in collapsed
        if token and token != "x" and token not in _INVENTORY_NAME_STOPWORDS
    ]


def _inventory_name_signature(name: str) -> str:
    keywords = _inventory_name_keywords(name)
    if not keywords:
        return ""
    return " ".join(sorted(keywords))


def _inventory_keyword_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    inter = len(left & right)
    union = len(left | right)
    if union <= 0:
        return 0.0
    score = inter / union
    if inter >= 2 and (left.issubset(right) or right.issubset(left)):
        score = max(score, 0.9)
    return score


def _resolve_inventory_item_id(
    raw_item_name: str,
    inventory_cache: list[dict],
) -> int | None:
    signature = _inventory_name_signature(raw_item_name)
    if not signature:
        return None

    # Strongest match: normalized keyword signature.
    for row in inventory_cache:
        if row["signature"] and row["signature"] == signature:
            return int(row["id"])

    # Fallback: keyword similarity when names are close variants.
    target_keywords = set(_inventory_name_keywords(raw_item_name))
    if not target_keywords:
        return None

    best_id: int | None = None
    best_score = 0.0
    for row in inventory_cache:
        candidate_keywords = set(row["keywords"])
        score = _inventory_keyword_similarity(target_keywords, candidate_keywords)
        if score > best_score:
            best_score = score
            best_id = int(row["id"])

    if best_id is not None and best_score >= 0.68:
        return best_id
    return None


def sync_auto_invoice_inventory_movements(
    invoice_id: int,
    active: bool = True,
) -> int:
    """
    Keep auto-generated rental movement rows in sync for one invoice.

    Auto rows are stored as an OUT and RETURN pair per inventory product item.
    Pairs net to zero quantity so total stock is not permanently reduced.
    """
    inv_id = int(invoice_id)
    auto_types = ("Auto Rental Out", "Auto Rental Return")
    inserted = 0
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM inventory_movements
            WHERE reference_invoice_id = ?
              AND movement_type IN (?, ?)
            """,
            (inv_id, auto_types[0], auto_types[1]),
        )
        if not active:
            return 0

        invoice_row = conn.execute(
            """
            SELECT
                COALESCE(event_date, '') AS event_date,
                COALESCE(event_time, '11:00') AS event_time,
                COALESCE(rental_hours, 24) AS rental_hours
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if invoice_row is None:
            return 0

        event_date = str(invoice_row["event_date"] or "").strip()
        event_time = str(invoice_row["event_time"] or "11:00").strip() or "11:00"
        rental_hours = float(invoice_row["rental_hours"] or 24.0)
        if rental_hours <= 0:
            rental_hours = 24.0

        if event_date:
            try:
                start_dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                start_dt = datetime.now()
        else:
            start_dt = datetime.now()
        end_dt = start_dt + timedelta(hours=rental_hours)

        products = conn.execute(
            """
            SELECT
                trim(COALESCE(ii.item_name, '')) AS item_name,
                COALESCE(SUM(ii.quantity), 0) AS qty,
                COALESCE(MAX(ii.unit_price), 0) AS rental_price
            FROM invoice_items ii
            WHERE ii.invoice_id = ?
              AND lower(COALESCE(ii.item_type, 'product')) = 'product'
            GROUP BY lower(trim(ii.item_name))
            """,
            (inv_id,),
        ).fetchall()

        if not products:
            return 0

        inventory_rows = conn.execute(
            """
            SELECT id, item_name
            FROM inventory_items
            """
        ).fetchall()
        inventory_cache = [
            {
                "id": int(row["id"]),
                "item_name": str(row["item_name"] or "").strip(),
                "signature": _inventory_name_signature(str(row["item_name"] or "")),
                "keywords": _inventory_name_keywords(str(row["item_name"] or "")),
            }
            for row in inventory_rows
        ]

        start_token = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_token = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        for row in products:
            item_name = str(row["item_name"] or "").strip()
            qty = float(row["qty"] or 0.0)
            if qty <= 0 or not item_name:
                continue

            matched_item_id = _resolve_inventory_item_id(item_name, inventory_cache)
            if matched_item_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO inventory_items (
                        sku, item_name, category, unit, current_quantity, reorder_level,
                        default_rental_price, default_unit_cost, unit_weight_kg, active, updated_at
                    )
                    VALUES (NULL, ?, 'General', 'pcs', 0, 0, ?, 0, 0, 1, CURRENT_TIMESTAMP)
                    """,
                    (item_name, float(row["rental_price"] or 0.0)),
                )
                inventory_item_id = int(cursor.lastrowid)
                inventory_cache.append(
                    {
                        "id": inventory_item_id,
                        "item_name": item_name,
                        "signature": _inventory_name_signature(item_name),
                        "keywords": _inventory_name_keywords(item_name),
                    }
                )
            else:
                inventory_item_id = int(matched_item_id)
                rental_price = float(row["rental_price"] or 0.0)
                conn.execute(
                    """
                    UPDATE inventory_items
                    SET default_rental_price = CASE
                            WHEN COALESCE(default_rental_price, 0) <= 0 AND ? > 0 THEN ?
                            ELSE default_rental_price
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (rental_price, rental_price, inventory_item_id),
                )
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    inventory_item_id,
                    start_token,
                    auto_types[0],
                    -qty,
                    inv_id,
                    "Auto-generated from confirmed real invoice.",
                ),
            )
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    inventory_item_id,
                    end_token,
                    auto_types[1],
                    qty,
                    inv_id,
                    "Auto-generated return based on rental duration.",
                ),
            )
            inserted += 2
    return inserted


def load_notification_log() -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            invoice_id,
            notification_type,
            sent_at
        FROM event_notification_log
        """
    )


def mark_notification_sent(invoice_id: int, notification_type: str) -> None:
    reminder = (notification_type or "").strip().lower()
    if not reminder:
        raise ValueError("Notification type is required.")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO event_notification_log (invoice_id, notification_type, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(invoice_id, notification_type) DO UPDATE SET
                sent_at = excluded.sent_at
            """,
            (int(invoice_id), reminder),
        )
