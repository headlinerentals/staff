from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from db import (
    add_expense,
    add_invoice_item,
    add_monthly_adjustment,
    expense_total_for_invoice_category,
    upsert_invoice,
)


DEFAULT_IMPORT_PATHS = {
    "Net Profit Per Invoice": "/Users/oshaniwatto/Downloads/Net Profit Per Invoice   - Sheet1.csv",
    "Expenses As Per Invoice": "/Users/oshaniwatto/Downloads/_Expenses As Per Invoice - Sheet1.csv",
    "Wages Per Person (Monthly)": "/Users/oshaniwatto/Downloads/Wages Per Person (Monthly) - Sheet1.csv",
    "Re-Rental Spreadsheet": "/Users/oshaniwatto/Downloads/Re-Rental Spreadsheet - Sheet1.csv",
    "Monthly Expenses": "/Users/oshaniwatto/Downloads/Monthly Expenses - Sheet1.csv",
    "Monthly Net Profit": "/Users/oshaniwatto/Downloads/Monthly Net Profit - Sheet1.csv",
    "Shopify Orders CSV (optional)": "",
}

DETAILED_EXPENSE_CATEGORIES = {"Wages", "Re-Rental"}
MONTHLY_ROLLUP_COLUMNS = {
    "Re-Rental",
    "Wages",
    "Bad Debt",
    "Petrol (Add By Invoice #)",
    "Unforseen Expenses",
    "Unforeseen Expenses",
}


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "n/a", "na"}:
        return ""
    return text


def _normalize_invoice_number(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _money(value: object) -> float:
    text = _clean_text(value)
    if not text:
        return 0.0
    stripped = re.sub(r"[^0-9.\-]", "", text)
    if stripped in {"", "-", "."}:
        return 0.0
    try:
        return float(stripped)
    except ValueError:
        return 0.0


def _date(value: object) -> str | None:
    text = _clean_text(value)
    if not text:
        return None

    for dayfirst in (False, True):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
        if not pd.isna(parsed):
            return parsed.date().isoformat()
    return None


def _month(value: object) -> str | None:
    text = _clean_text(value)
    if not text:
        return None

    for dayfirst in (False, True):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
        if not pd.isna(parsed):
            return f"{parsed.year:04d}-{parsed.month:02d}"

    for fmt in ("%B %y", "%b %y", "%B %Y", "%b %Y", "%m/%Y", "%m/%y"):
        try:
            parsed = pd.to_datetime(text, format=fmt, errors="raise")
            return f"{parsed.year:04d}-{parsed.month:02d}"
        except Exception:
            continue
    return None


def _read_csv(path: str) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(file_path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("")


def import_net_profit_per_invoice(path: str) -> dict:
    df = _read_csv(path)
    counts = {"invoices": 0, "items": 0, "rows_seen": 0}

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        invoice_number = _normalize_invoice_number(row.get("Invoice #", ""))
        if not invoice_number:
            continue

        event_date = _date(row.get("Event Date", "")) or date.today().isoformat()
        invoice_id = upsert_invoice(
            invoice_number=invoice_number,
            event_date=event_date,
            customer_name=_clean_text(row.get("Customer Name", "")),
            contact_detail=_clean_text(row.get("Contact Detail", "")),
            delivered_to=_clean_text(row.get("Delivered To", "")),
            paid_to=_clean_text(row.get("Paid To", "")),
            notes="Imported from Net Profit Per Invoice",
        )
        counts["invoices"] += 1

        revenue = _money(row.get("Total Revenue", ""))
        if revenue > 0:
            add_invoice_item(
                invoice_id=invoice_id,
                item_name="Imported Invoice Revenue",
                item_type="service",
                quantity=1,
                unit_price=revenue,
                unit_cost=0,
            )
            counts["items"] += 1

    return counts


def import_expenses_per_invoice(path: str) -> dict:
    df = _read_csv(path)
    counts = {
        "invoices": 0,
        "expenses": 0,
        "rows_seen": 0,
        "skipped_existing": 0,
        "topup_adjustments": 0,
    }

    category_map = {
        "Wages": "Wages",
        "Petrol": "Petrol",
        "Re-Rental": "Re-Rental",
        "Bad Debt": "Bad Debt",
        "Unforseen Expense": "Unforeseen Expense",
    }

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        invoice_number = _normalize_invoice_number(row.get("Invoice #", ""))
        if not invoice_number:
            continue

        event_date = _date(row.get("Event Date", "")) or date.today().isoformat()
        invoice_id = upsert_invoice(invoice_number=invoice_number, event_date=event_date)
        counts["invoices"] += 1

        for source_col, category in category_map.items():
            amount = _money(row.get(source_col, ""))
            if amount <= 0:
                continue

            amount_to_add = amount
            vendor = "Legacy Sheet"
            description = "From Expenses As Per Invoice CSV"

            if category in DETAILED_EXPENSE_CATEGORIES:
                existing_detail_total = expense_total_for_invoice_category(
                    invoice_id=invoice_id,
                    category=category,
                    excluded_vendors=("Legacy Sheet", "Summary Adjustment"),
                )
                remaining = round(amount - existing_detail_total, 2)
                if remaining <= 0.5:
                    counts["skipped_existing"] += 1
                    continue
                if existing_detail_total > 0:
                    amount_to_add = remaining
                    vendor = "Summary Adjustment"
                    description = (
                        "Top-up from Expenses As Per Invoice CSV "
                        f"(target {amount:,.2f}, detailed {existing_detail_total:,.2f})"
                    )
                    counts["topup_adjustments"] += 1

            add_expense(
                expense_date=event_date,
                amount=amount_to_add,
                category=category,
                invoice_id=invoice_id,
                expense_kind="transaction",
                vendor=vendor,
                description=description,
            )
            counts["expenses"] += 1
    return counts


def import_wages_per_person(path: str) -> dict:
    df = _read_csv(path)
    counts = {"invoices": 0, "expenses": 0, "rows_seen": 0}

    meta_cols = {"Invoice Number", "Invoice Number ", "Date", "Total Pay"}
    person_cols = [c for c in df.columns if c not in meta_cols]

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        invoice_number = _normalize_invoice_number(
            row.get("Invoice Number", row.get("Invoice Number ", ""))
        )
        if not invoice_number:
            continue

        expense_date = _date(row.get("Date", "")) or date.today().isoformat()
        invoice_id = upsert_invoice(invoice_number=invoice_number, event_date=expense_date)
        counts["invoices"] += 1

        for person in person_cols:
            amount = _money(row.get(person, ""))
            if amount <= 0:
                continue
            add_expense(
                expense_date=expense_date,
                amount=amount,
                category="Wages",
                invoice_id=invoice_id,
                expense_kind="transaction",
                vendor=person.strip(),
                description="From Wages Per Person CSV",
            )
            counts["expenses"] += 1
    return counts


def import_rerental_spreadsheet(path: str) -> dict:
    df = _read_csv(path)
    counts = {"invoices": 0, "expenses": 0, "rows_seen": 0}

    meta_cols = {"Invoice Number", "Invoice Number ", "Date", "Total Pay"}
    vendor_cols = [c for c in df.columns if c not in meta_cols]

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        invoice_number = _normalize_invoice_number(
            row.get("Invoice Number", row.get("Invoice Number ", ""))
        )
        if not invoice_number:
            continue

        expense_date = _date(row.get("Date", "")) or date.today().isoformat()
        invoice_id = upsert_invoice(invoice_number=invoice_number, event_date=expense_date)
        counts["invoices"] += 1

        for vendor in vendor_cols:
            amount = _money(row.get(vendor, ""))
            if amount <= 0:
                continue
            add_expense(
                expense_date=expense_date,
                amount=amount,
                category="Re-Rental",
                invoice_id=invoice_id,
                expense_kind="transaction",
                vendor=vendor.strip(),
                description="From Re-Rental Spreadsheet CSV",
            )
            counts["expenses"] += 1
    return counts


def import_monthly_expenses(path: str) -> dict:
    df = _read_csv(path)
    counts = {
        "expenses": 0,
        "rows_seen": 0,
        "imported_rollup_reference": 0,
        "imported_recurring": 0,
    }

    if "Month" not in df.columns:
        return counts

    excluded = {"Month", "Total Monthly Expense"}
    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        month_key = _month(row.get("Month", ""))
        if not month_key:
            continue

        expense_date = f"{month_key}-01"
        for col in df.columns:
            if col in excluded:
                continue
            col_name = col.strip()
            amount = _money(row.get(col, ""))
            if amount <= 0:
                continue

            if col_name in MONTHLY_ROLLUP_COLUMNS:
                add_expense(
                    expense_date=expense_date,
                    amount=amount,
                    category=col_name,
                    invoice_id=None,
                    expense_kind="summary_rollup",
                    vendor="Monthly Ledger",
                    description="From Monthly Expenses CSV (summary reference only)",
                )
                counts["expenses"] += 1
                counts["imported_rollup_reference"] += 1
                continue

            add_expense(
                expense_date=expense_date,
                amount=amount,
                category=col_name,
                invoice_id=None,
                expense_kind="recurring_monthly",
                vendor="Monthly Ledger",
                description="From Monthly Expenses CSV (recurring/month-level)",
            )
            counts["expenses"] += 1
            counts["imported_recurring"] += 1
    return counts


def import_monthly_net_profit(path: str) -> dict:
    df = _read_csv(path)
    counts = {"adjustments": 0, "rows_seen": 0}

    if "Month" not in df.columns:
        return counts

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        month_key = _month(row.get("Month", ""))
        if not month_key:
            continue

        purchase = _money(row.get("Purchasing New Inventory", ""))
        if purchase > 0:
            add_monthly_adjustment(
                month=month_key,
                adjustment_type="Inventory Purchase",
                amount=purchase,
                description="From Monthly Net Profit CSV",
            )
            counts["adjustments"] += 1
    return counts


def import_shopify_orders(path: str) -> dict:
    if not path.strip():
        return {"rows_seen": 0, "orders_imported": 0, "orders_skipped": 0}

    df = _read_csv(path)
    counts = {"rows_seen": 0, "orders_imported": 0, "orders_skipped": 0}

    def pick(row: pd.Series, names: list[str]) -> str:
        for name in names:
            if name in row.index:
                value = _clean_text(row.get(name, ""))
                if value:
                    return value
        return ""

    for _, row in df.iterrows():
        counts["rows_seen"] += 1
        order_name = pick(row, ["Name", "Order Name", "Order Number"])
        if not order_name:
            counts["orders_skipped"] += 1
            continue

        financial_status = pick(row, ["Financial Status", "Payment Status"]).lower()
        if financial_status in {"voided", "refunded"}:
            counts["orders_skipped"] += 1
            continue

        created_at = pick(row, ["Created at", "Paid at", "Processed at", "Date"])
        event_date = _date(created_at) or date.today().isoformat()
        total_value = _money(
            pick(row, ["Total", "Total Price", "Current Total Price", "Subtotal"])
        )
        if total_value <= 0:
            counts["orders_skipped"] += 1
            continue

        invoice_number = f"SHOPIFY-{order_name.lstrip('#').strip()}"
        customer_name = pick(
            row,
            ["Shipping Name", "Billing Name", "Customer Name", "Customer"],
        )
        contact = pick(row, ["Email", "Phone", "Shipping Phone", "Billing Phone"])
        destination = pick(
            row,
            ["Shipping Address1", "Shipping Address", "Ship To"],
        )

        invoice_id = upsert_invoice(
            invoice_number=invoice_number,
            event_date=event_date,
            customer_name=customer_name,
            contact_detail=contact,
            delivered_to=destination,
            paid_to="Shopify",
            notes="Imported from Shopify Orders CSV",
        )
        add_invoice_item(
            invoice_id=invoice_id,
            item_name="Shopify Order Revenue",
            item_type="service",
            quantity=1,
            unit_price=total_value,
            unit_cost=0,
        )
        counts["orders_imported"] += 1

    return counts


def import_all(paths: dict[str, str]) -> dict[str, dict]:
    output: dict[str, dict] = {}
    output["Net Profit Per Invoice"] = import_net_profit_per_invoice(
        paths["Net Profit Per Invoice"]
    )
    output["Wages Per Person (Monthly)"] = import_wages_per_person(
        paths["Wages Per Person (Monthly)"]
    )
    output["Re-Rental Spreadsheet"] = import_rerental_spreadsheet(
        paths["Re-Rental Spreadsheet"]
    )
    output["Expenses As Per Invoice"] = import_expenses_per_invoice(
        paths["Expenses As Per Invoice"]
    )
    output["Monthly Expenses"] = import_monthly_expenses(paths["Monthly Expenses"])
    output["Monthly Net Profit"] = import_monthly_net_profit(paths["Monthly Net Profit"])
    output["Shopify Orders CSV (optional)"] = import_shopify_orders(
        paths.get("Shopify Orders CSV (optional)", "")
    )
    return output
