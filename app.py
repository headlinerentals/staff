from __future__ import annotations

import base64
import html
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import platform
import random
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import (
    canonical_expense_categories,
    load_budget_vs_actual,
    load_event_calendar,
    load_event_product_allocations,
    load_daily_summary,
    load_expense_category_budget_vs_actual,
    load_expenses,
    load_finance_data_quality_checks,
    load_inventory_availability_schedule,
    load_inventory_live_status,
    load_inventory_snapshot,
    load_invoice_level,
    load_monthly_summary,
    load_monthly_expense_modes,
    load_product_profitability,
    load_product_type_profitability,
    load_supplier_expenses,
    load_supplier_monthly_expenses,
    load_supplier_performance_ranking,
    load_tax_pack_invoice_detail,
    load_tax_pack_monthly,
    load_weekly_cashflow_forecast,
    load_weekly_summary,
    load_wages_by_person_monthly,
    load_wages_period_summary,
    load_yearly_summary,
    normalize_expense_category,
)
import db as _db_module


def _missing_db_callable(name: str):
    def _missing(*args, **kwargs):
        raise RuntimeError(
            f"Database API '{name}' is unavailable in this deployment. "
            "Upload matching app.py and db.py from the same version."
        )

    return _missing


def _db_attr(name: str, default=None):
    if default is None:
        default = _missing_db_callable(name)
    return getattr(_db_module, name, default)


DB_PATH = _db_attr("DB_PATH", Path(__file__).with_name("finance_hub.db"))
add_expense = _db_attr("add_expense")
add_inventory_purchase = _db_attr("add_inventory_purchase")
add_invoice_attachment = _db_attr("add_invoice_attachment")
add_monthly_adjustment = _db_attr("add_monthly_adjustment")
cleanup_legacy_double_counts = _db_attr("cleanup_legacy_double_counts")
delete_inventory_purchase = _db_attr("delete_inventory_purchase")
delete_monthly_budget = _db_attr("delete_monthly_budget")
delete_recurring_expense_template = _db_attr("delete_recurring_expense_template")
db_storage_status = _db_attr("db_storage_status")
delete_invoice_attachment = _db_attr("delete_invoice_attachment")
delete_inventory_item = _db_attr("delete_inventory_item")
delete_expense = _db_attr("delete_expense")
delete_invoice = _db_attr("delete_invoice")
delete_expense_category_budget = _db_attr("delete_expense_category_budget")
find_similar_expense_candidates = _db_attr("find_similar_expense_candidates")
create_db_backup_snapshot = _db_attr("create_db_backup_snapshot")
get_setting = _db_attr("get_setting")
init_db = _db_attr("init_db")
invoice_meta_by_number = _db_attr("invoice_meta_by_number")
invoice_export_bundle = _db_attr("invoice_export_bundle")
invoice_options = _db_attr("invoice_options")
list_backup_snapshots = _db_attr("list_backup_snapshots")
load_finance_activity = _db_attr("load_finance_activity")
load_invoice_build_log = _db_attr("load_invoice_build_log")
load_invoice_attachments = _db_attr("load_invoice_attachments")
load_inventory_purchases = _db_attr("load_inventory_purchases")
load_expense_category_budgets = _db_attr("load_expense_category_budgets")
load_monthly_budgets = _db_attr("load_monthly_budgets")
load_notification_log = _db_attr("load_notification_log")
load_recurring_draft_expenses = _db_attr("load_recurring_draft_expenses")
load_recurring_expense_templates = _db_attr("load_recurring_expense_templates")
log_finance_activity = _db_attr("log_finance_activity")
log_invoice_activity = _db_attr("log_invoice_activity")
mark_notification_sent = _db_attr("mark_notification_sent")
purge_all_records = _db_attr("purge_all_records")
run_recurring_template_autopost = _db_attr("run_recurring_template_autopost")
restore_db_from_snapshot = _db_attr("restore_db_from_snapshot")
restore_db_from_uploaded_bytes = _db_attr("restore_db_from_uploaded_bytes")
inspect_uploaded_backup_bytes = _db_attr("inspect_uploaded_backup_bytes")
backup_snapshot_summary = _db_attr("backup_snapshot_summary")
finalize_recurring_draft_expense = _db_attr("finalize_recurring_draft_expense")
upsert_monthly_budget = _db_attr("upsert_monthly_budget")
upsert_expense_category_budget = _db_attr("upsert_expense_category_budget")
upsert_recurring_expense_template = _db_attr("upsert_recurring_expense_template")
upcoming_invoices = _db_attr("upcoming_invoices")
replace_invoice_items = _db_attr("replace_invoice_items")
set_invoice_payment_status = _db_attr("set_invoice_payment_status")
set_setting = _db_attr("set_setting")
sync_auto_invoice_inventory_movements = _db_attr("sync_auto_invoice_inventory_movements")
update_expense = _db_attr("update_expense")
update_inventory_purchase = _db_attr("update_inventory_purchase")
update_inventory_item_values = _db_attr("update_inventory_item_values")
upsert_inventory_item = _db_attr("upsert_inventory_item")
upsert_invoice = _db_attr("upsert_invoice")
get_last_startup_restore_info = _db_attr("get_last_startup_restore_info", lambda: {})

from invoice_export import build_invoice_payload, render_invoice_pdf, render_invoice_png
from messaging import (
    build_invoice_message,
    gmail_compose_link,
    instagram_dm_link,
    normalize_sms_to,
    normalize_whatsapp_to,
    send_email_smtp,
    send_instagram_dm_meta,
    send_twilio_message,
    send_whatsapp_cloud_text,
    sms_link,
    whatsapp_link,
)
from pdf_parser import parse_invoice_pdf

APP_TITLE = "Headline Rentals Staff App"


def resolve_brand_logo_path() -> Path | None:
    candidates: list[Path] = []
    search_dirs = [
        Path(__file__).resolve().with_name("assets"),
        Path.cwd() / "assets",
        Path(__file__).resolve().parent,
        Path.cwd(),
    ]
    fixed_names = [
        "headline-rentals-logo.png",
        "headline-rentals-logo.PNG",
        "Headline-Rentals-Logo.png",
        "headline rentals logo.png",
        "headline_rentals_logo.png",
        "logo.png",
        "favicon.png",
    ]
    for base_dir in search_dirs:
        candidates.extend([base_dir / name for name in fixed_names])
        for pattern in (
            "*headline*logo*.png",
            "*headline*logo*.PNG",
            "*headline*rent*.png",
            "*headline*rent*.PNG",
            "*logo*.png",
            "*logo*.PNG",
            "*favicon*.png",
            "*icon*.png",
        ):
            try:
                candidates.extend(sorted(base_dir.glob(pattern)))
            except Exception:
                continue
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


BRAND_LOGO_PATH = resolve_brand_logo_path()
WATTBOT_AVATAR_PATH = Path(__file__).with_name("assets") / "wattbot-avatar.jpg"
PAGE_ICON = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH else "📈"
PRIMARY_COLOR = "#5927e5"
SECONDARY_COLOR = "#a7eaff"
DEFAULT_EVENT_TIME = "11:00"
DEFAULT_EVENT_HOURS = 24.0
DEFAULT_EVENT_TIMEZONE = "America/Jamaica"
CLIENT_REVIEW_LINK_DEFAULT = "https://g.page/r/CUXsxv4KxbM_EBE/review"
DEFAULT_SELLER_BANKING = {
    "seller_name": "Headline Event Rentals",
    "seller_address_1": "61 West Main Drive",
    "seller_address_2": "Kingston",
    "bank_account_name": "Headline Event Rentals",
    "bank_account_type": "Scotia Savings Account (JM$)",
    "bank_branch": "HWT",
    "bank_account_number": "909039",
}
FINANCE_PASSWORD_KEY = "security.finance_hub_password_hash"
FINANCE_AUTH_SESSION_KEY = "finance_hub_authenticated"
APP_UNLOCKED_SESSION_KEY = "app_unlocked"
APP_ACCESS_LEVEL_SESSION_KEY = "app_access_level"
WATTBOT_NAME = "Reason Wid Watto (WattBot)"
WATTBOT_HISTORY_KEY = "wattbot.chat_history"
WATTBOT_JOKES = [
    "I told my suitcase there is no vacation this month. Now it has emotional baggage.",
    "I started a band called 1023MB. We still have not got a gig.",
    "My phone battery and I have one thing in common. We both panic at 5 percent.",
    "I tried to eat a clock once. It was too time-consuming.",
    "Why did the calendar get promoted? It had a lot of dates.",
    "I opened a bakery because I kneaded dough. The finance team was not amused.",
    "I asked the Wi-Fi for commitment. It said: signal is weak right now.",
    "Parallel parking and parallel universes are both stressful for me.",
    "I told my plant a joke. It needed thyme to process it.",
    "I finally fixed my posture. My neck sent a thank-you note.",
]
WATTBOT_MOTIVATIONS = [
    "You do not need a perfect day. You need a consistent next move.",
    "Small disciplined actions beat occasional big effort every single time.",
    "Pressure is heavy, but your standards can stay simple and sharp.",
    "Build momentum quietly. Results make noise later.",
    "If the plan feels big, shrink the step, not the ambition.",
    "Do the boring fundamentals well and your future self will thank you.",
    "Progress counts even when it feels slow.",
    "Protect your energy, then deploy it where returns are highest.",
    "You can restart the day at any hour. A reset is still progress.",
    "Confidence is built by keeping promises to yourself.",
]
FINANCE_CATEGORY_OPTIONS = canonical_expense_categories()
AUTO_QUOTE_NUMBER_WORDS = {
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
}
AUTO_QUOTE_ITEM_STOPWORDS = {
    "rental",
    "rentals",
    "event",
    "events",
    "service",
    "services",
    "the",
    "a",
    "an",
    "for",
    "with",
    "and",
    "to",
    "of",
}
AUTO_QUOTE_META_KEYWORDS = {
    "delivery",
    "set-up",
    "setup",
    "discount",
    "gct",
    "tax",
    "customer",
    "client",
    "email",
    "phone",
    "location",
    "address",
    "date",
    "time",
    "duration",
    "hours",
    "days",
    "quote",
    "invoice",
}
INVOICE_BUNDLE_PRESETS_KEY = "invoice.bundle_presets_json"
INVENTORY_PURCHASE_LABELS = {
    "inventory purchase",
    "inventory purchases",
    "stock purchase",
    "stock purchases",
    "purchase inventory",
    "purchasing new inventory",
    "new inventory purchase",
}

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=PAGE_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

ATTACHMENTS_DIR = Path(__file__).with_name("uploads")
uploads_dir_override = (os.getenv("HR_UPLOADS_DIR", "") or "").strip()
if uploads_dir_override:
    ATTACHMENTS_DIR = Path(uploads_dir_override).expanduser()
else:
    ATTACHMENTS_DIR = DB_PATH.parent / "uploads"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def money(value: float) -> str:
    return f"JM${value:,.2f}"


def inventory_purchase_mask(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().isin(INVENTORY_PURCHASE_LABELS)


def _extract_first_email(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", raw)
    return str(match.group(1)).strip() if match else ""


def _extract_first_phone(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    candidates = re.findall(r"(\+?\d[\d\s().\-]{6,}\d)", raw)
    for candidate in candidates:
        digits = "".join(ch for ch in candidate if ch.isdigit())
        if len(digits) >= 7:
            if candidate.strip().startswith("+"):
                return f"+{digits}"
            return digits
    return ""


def resolve_contact_channels(
    customer_phone: str,
    customer_email: str,
    contact_detail: str = "",
) -> dict[str, str]:
    phone = str(customer_phone or "").strip()
    email = str(customer_email or "").strip()
    detail = str(contact_detail or "").strip()

    if not email:
        email = _extract_first_email(detail)
    if not phone:
        phone = _extract_first_phone(detail)

    contact_target = phone or email or detail or "No contact"
    return {
        "phone": phone,
        "email": email,
        "contact_target": contact_target,
    }


def ensure_link_in_message(message: str, link: str) -> str:
    text = str(message or "").strip()
    url = str(link or "").strip()
    if not url:
        return text
    if url.lower() in text.lower():
        return text
    if not text:
        return url
    return f"{text}\n\n👉 Leave a review here:\n{url}"


def invoice_due_message(event_date: date) -> tuple[str, str]:
    days_left = (event_date - date.today()).days
    if days_left < 0:
        return ("info", f"Event date was {abs(days_left)} day(s) ago.")
    if days_left == 0:
        return ("warning", "Event is today.")
    if days_left <= 3:
        return ("warning", f"Event is in {days_left} day(s).")
    return ("info", f"Event is in {days_left} day(s).")


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or "").strip("._")
    return cleaned or f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def invoice_download_filename(customer_name: str, invoice_number: str, document_label: str = "Invoice") -> str:
    customer = str(customer_name or "").strip() or "Customer"
    number = str(invoice_number or "").strip() or datetime.now().strftime("%Y%m%d%H%M%S")
    doc = str(document_label or "Invoice").strip() or "Invoice"
    base = f"{customer} - {doc} {number}"
    base = re.sub(r'[\\/:*?"<>|]+', "", base)
    base = re.sub(r"\s+", " ", base).strip().strip(".")
    return base or f"Invoice {number}"


def profile_currency_symbol() -> str:
    currency_code = str(get_profile_setting("currency", "JMD") or "JMD").strip().upper()
    return "JM$" if currency_code == "JMD" else "$"


def build_payment_receipt_assets(
    invoice_id: int,
    *,
    invoice_total_override: float | None = None,
    amount_paid_override: float | None = None,
    outstanding_override: float | None = None,
    payment_method: str = "",
    payment_note: str = "",
) -> dict[str, object]:
    header, items = invoice_export_bundle(int(invoice_id))
    currency_symbol = profile_currency_symbol()
    payload = build_invoice_payload(
        header=header,
        items=items,
        business_name=get_profile_setting("business_name", "Headline Rentals"),
        currency=currency_symbol,
        bank_info=DEFAULT_SELLER_BANKING,
    )

    total_raw = pd.to_numeric(
        invoice_total_override if invoice_total_override is not None else payload.get("total", 0.0),
        errors="coerce",
    )
    invoice_total = float(0.0 if pd.isna(total_raw) else total_raw)
    invoice_total = max(0.0, invoice_total)

    paid_raw = pd.to_numeric(
        amount_paid_override if amount_paid_override is not None else header.get("amount_paid", invoice_total),
        errors="coerce",
    )
    amount_paid = float(0.0 if pd.isna(paid_raw) else paid_raw)
    amount_paid = max(0.0, amount_paid)

    if (
        amount_paid_override is None
        and str(header.get("payment_status", "") or "").strip().lower() == "paid_full"
        and amount_paid <= 0.0
    ):
        amount_paid = invoice_total

    if outstanding_override is not None:
        outstanding_raw = pd.to_numeric(outstanding_override, errors="coerce")
        outstanding_value = float(0.0 if pd.isna(outstanding_raw) else outstanding_raw)
        outstanding_value = max(0.0, outstanding_value)
        if amount_paid_override is None:
            amount_paid = max(0.0, invoice_total - outstanding_value)
    else:
        amount_paid = min(max(0.0, amount_paid), invoice_total)
        outstanding_value = max(invoice_total - amount_paid, 0.0)

    if amount_paid_override is not None and outstanding_override is None:
        amount_paid = min(max(0.0, amount_paid), invoice_total)
        outstanding_value = max(invoice_total - amount_paid, 0.0)

    payment_status = (
        "paid_full"
        if outstanding_value <= 0.01
        else ("deposit_paid" if amount_paid > 0.01 else "unpaid")
    )
    payload["document_type"] = "invoice"
    payload["order_status"] = "confirmed"
    payload["document_title_override"] = "Payment Received"
    payload["payment_status"] = payment_status
    payload["amount_paid"] = float(round(amount_paid, 2))
    payload["balance_due_later"] = float(round(outstanding_value, 2))

    receipt_stamp = jamaica_now().strftime("%Y-%m-%d %I:%M %p")
    receipt_note = (
        f"Receipt issued: {receipt_stamp} | Total Paid: {money(amount_paid)} | "
        f"Balance Due: {money(outstanding_value)}"
    )
    if payment_method.strip():
        receipt_note += f" | Method: {payment_method.strip()}"
    if payment_note.strip():
        receipt_note += f" | Note: {payment_note.strip()}"
    current_note = str(payload.get("notes", "") or "").strip()
    payload["notes"] = f"{current_note} | {receipt_note}" if current_note else receipt_note

    logo_path = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH else None
    pdf_bytes = render_invoice_pdf(payload, logo_path=logo_path)
    png_bytes = render_invoice_png(payload, logo_path=logo_path)
    file_stub = invoice_download_filename(
        customer_name=str(payload.get("customer_name", "") or ""),
        invoice_number=str(payload.get("invoice_number", "") or ""),
        document_label="Payment Receipt",
    )

    resolved_contact = resolve_contact_channels(
        customer_phone=str(payload.get("customer_phone", "") or "").strip(),
        customer_email=str(payload.get("customer_email", "") or "").strip(),
        contact_detail=str(header.get("contact_detail", "") or "").strip(),
    )
    subject = (
        f"{payload.get('business_name', 'Headline Rentals')} Payment Receipt "
        f"#{payload.get('invoice_number', '')}"
    )
    total_display = f"{payload.get('currency', 'JM$')}{invoice_total:,.2f}"
    message = build_invoice_message(
        business_name=str(payload.get("business_name", "Headline Rentals")),
        invoice_number=str(payload.get("invoice_number", "")),
        document_label="Payment Receipt",
        event_date=str(payload.get("event_date", "")),
        event_time=str(payload.get("event_time", "")),
        total_display=total_display,
        review_link="",
        extra_note=(
            f"Total Paid: {money(amount_paid)} | Balance Due: {money(outstanding_value)}"
        ),
    )
    message = f"{message}\nPlease see attached paid receipt (PDF/PNG)."

    return {
        "payload": payload,
        "invoice_total": invoice_total,
        "amount_paid": amount_paid,
        "balance_due": outstanding_value,
        "pdf_bytes": pdf_bytes,
        "png_bytes": png_bytes,
        "file_stub": file_stub,
        "phone": str(resolved_contact.get("phone", "") or "").strip(),
        "email": str(resolved_contact.get("email", "") or "").strip(),
        "subject": subject,
        "message": message,
    }


def document_label_from_type(document_type: object) -> str:
    raw = str(document_type or "").strip().lower().replace("_", " ").replace("-", " ")
    if "quote" in raw:
        return "Price Quote"
    return "Invoice"


def next_invoice_number_from_last(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "1"
    match = re.search(r"(\d+)(?!.*\d)", raw)
    if match is None:
        return f"{raw}-1"
    current = match.group(1)
    next_number = str(int(current) + 1).zfill(len(current))
    return f"{raw[:match.start(1)]}{next_number}{raw[match.end(1):]}"


def next_confirmed_invoice_number(history: pd.DataFrame) -> str:
    if not isinstance(history, pd.DataFrame) or history.empty:
        return "1"
    scoped = history.copy()
    if "document_type" in scoped.columns:
        scoped = scoped[
            scoped["document_type"].fillna("").astype(str).str.strip().str.lower() == "invoice"
        ].copy()
    if scoped.empty:
        return "1"
    if "id" in scoped.columns:
        scoped["id"] = pd.to_numeric(scoped["id"], errors="coerce").fillna(0.0)
        scoped = scoped.sort_values(by="id", ascending=False)
    for raw in scoped["invoice_number"].tolist():
        token = str(raw or "").strip()
        if token:
            return next_invoice_number_from_last(token)
    return "1"


def tzinfo_for_name(tz_name: str) -> timezone | ZoneInfo:
    name = (tz_name or DEFAULT_EVENT_TIMEZONE).strip() or DEFAULT_EVENT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-5))


def jamaica_now() -> datetime:
    return datetime.now(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))


def time_str_to_time(value: str, fallback: time | None = None) -> time:
    raw = (value or "").strip()
    if not raw:
        return fallback or time(11, 0)
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return fallback or time(11, 0)
    return time(parsed.hour, parsed.minute)


def to_time_string(value: time | str | None) -> str:
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return time_str_to_time(str(value or "")).strftime("%H:%M")


def combine_event_window(
    event_date_value: object,
    event_time_value: object,
    rental_hours: float,
    event_timezone: str = DEFAULT_EVENT_TIMEZONE,
) -> tuple[datetime | None, datetime | None]:
    if not event_date_value:
        return None, None

    tz_obj = tzinfo_for_name(event_timezone)
    parsed_date = pd.to_datetime(event_date_value, errors="coerce")
    parsed_time = pd.to_datetime(str(event_time_value or DEFAULT_EVENT_TIME), errors="coerce")
    if pd.isna(parsed_date) or pd.isna(parsed_time):
        return None, None

    start = datetime(
        parsed_date.year,
        parsed_date.month,
        parsed_date.day,
        parsed_time.hour,
        parsed_time.minute,
        tzinfo=tz_obj,
    )
    safe_hours = float(rental_hours if rental_hours and rental_hours > 0 else DEFAULT_EVENT_HOURS)
    end = start + timedelta(hours=safe_hours)
    return start, end


def build_event_schedule(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events

    now_jm = jamaica_now()
    rows: list[dict] = []
    for _, raw in events.iterrows():
        tz_name = str(raw.get("event_timezone", DEFAULT_EVENT_TIMEZONE) or DEFAULT_EVENT_TIMEZONE)
        rental_hours = float(raw.get("rental_hours") or DEFAULT_EVENT_HOURS)
        start, end = combine_event_window(
            raw.get("event_date"),
            raw.get("event_time"),
            rental_hours,
            event_timezone=tz_name,
        )
        if start is None or end is None:
            continue

        now_local = now_jm.astimezone(start.tzinfo)
        if now_local < start:
            status = "Upcoming"
        elif now_local >= end:
            status = "Past"
        else:
            status = "Ongoing"

        row = dict(raw)
        row["event_start"] = start
        row["event_end"] = end
        row["event_date_display"] = start.strftime("%Y-%m-%d")
        row["event_time_display"] = start.strftime("%I:%M %p")
        row["event_end_display"] = end.strftime("%Y-%m-%d %I:%M %p")
        row["status"] = status
        row["hours_until_start"] = (start.astimezone(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)) - now_jm).total_seconds() / 3600.0
        row["hours_since_end"] = (now_jm - end.astimezone(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))).total_seconds() / 3600.0
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=list(events.columns) + ["event_start", "event_end", "status"])
    out = pd.DataFrame(rows)
    return out.sort_values("event_start")


def maps_search_link(location: str) -> str:
    if not (location or "").strip():
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(location.strip())}"


def google_calendar_link(
    title: str,
    start: datetime,
    end: datetime,
    location: str,
    details: str,
    tz_name: str = DEFAULT_EVENT_TIMEZONE,
) -> str:
    start_token = start.strftime("%Y%m%dT%H%M%S")
    end_token = end.strftime("%Y%m%dT%H%M%S")
    params = {
        "action": "TEMPLATE",
        "text": title or "Event",
        "dates": f"{start_token}/{end_token}",
        "ctz": tz_name or DEFAULT_EVENT_TIMEZONE,
        "location": location or "",
        "details": details or "",
    }
    encoded = "&".join([f"{k}={quote_plus(str(v))}" for k, v in params.items()])
    return f"https://calendar.google.com/calendar/render?{encoded}"


def apply_start_month(df: pd.DataFrame, start_month: str, month_col: str = "month") -> pd.DataFrame:
    if df.empty or not start_month or month_col not in df.columns:
        return df
    out = df.copy()
    try:
        period = pd.Period(str(start_month).strip(), freq="M")
    except Exception:
        return out
    out = out[out[month_col].notna()]
    if out.empty:
        return out
    month_text = out[month_col].astype(str).str.strip()
    parsed_month = pd.to_datetime(month_text, format="%Y-%m", errors="coerce")
    valid_mask = parsed_month.notna()
    if not bool(valid_mask.any()):
        return out.iloc[0:0].copy()
    valid_out = out.loc[valid_mask].copy()
    valid_periods = pd.PeriodIndex(parsed_month.loc[valid_mask], freq="M")
    return valid_out.loc[valid_periods >= period]


def reporting_window_label(start_month: str) -> str:
    token = str(start_month or "").strip()
    return f"from {token}" if token else "all data"


def recommended_expense_type_label(category: str) -> str:
    token = normalize_expense_category(category).strip().lower()
    if token in {"wages", "re-rental", "petrol", "bad debt", "unforeseen expense", "transport"}:
        return "Transaction (invoice/day level)"
    return "Recurring Monthly (ChatGPT/Ads/Shopify)"


def get_profile_setting(key: str, default: str) -> str:
    return get_setting(f"profile.{key}", default)


def set_profile_setting(key: str, value: str) -> None:
    set_setting(f"profile.{key}", value)


def bytes_to_data_uri(raw: bytes, mime_type: str = "image/jpeg") -> str:
    if not raw:
        return ""
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def wattbot_avatar_data_uri() -> str:
    stored = get_profile_setting("wattbot_avatar_data_uri", "").strip()
    if stored.startswith("data:image/"):
        return stored

    candidate_paths: list[Path] = [WATTBOT_AVATAR_PATH]
    if BRAND_LOGO_PATH is not None:
        candidate_paths.append(BRAND_LOGO_PATH)
    for candidate in candidate_paths:
        if candidate.exists():
            guessed = mimetypes.guess_type(candidate.name)[0] or "image/jpeg"
            try:
                return bytes_to_data_uri(candidate.read_bytes(), guessed)
            except Exception:
                continue
    return ""


def get_delivery_setting(key: str, default: str = "") -> str:
    return get_setting(f"delivery.{key}", default)


def set_delivery_setting(key: str, value: str) -> None:
    set_setting(f"delivery.{key}", value)


def get_google_maps_api_key() -> str:
    for key in ("GOOGLE_MAPS_API_KEY", "google_maps_api_key"):
        try:
            value = st.secrets.get(key, "")
        except Exception:
            value = ""
        if str(value or "").strip():
            return str(value).strip()
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


TRAFFIC_AWARE_HOURLY_DELIVERY_THRESHOLD_MIN = 90.0


def delivery_billing_mode_for_drive_time(delivery_minutes: float = 0.0, collection_minutes: float = 0.0) -> str:
    """Default delivery labour billing from the longest traffic-aware leg time."""
    try:
        delivery_value = max(0.0, float(delivery_minutes or 0.0))
    except (TypeError, ValueError):
        delivery_value = 0.0
    try:
        collection_value = max(0.0, float(collection_minutes or 0.0))
    except (TypeError, ValueError):
        collection_value = 0.0
    longest_leg_min = max(delivery_value, collection_value)
    if 0 < longest_leg_min < TRAFFIC_AWARE_HOURLY_DELIVERY_THRESHOLD_MIN:
        return "Hourly Rate"
    return "Day Rate"


def google_maps_distance(
    origin: str,
    destination: str,
    api_key: str,
    delivery_departure: datetime | None = None,
    collection_departure: datetime | None = None,
) -> tuple[float, float, float, float, float, str, str]:
    """
    Uses Google Maps Platform Geocoding API + Distance Matrix API.
    Requires a Google Cloud project with billing enabled.
    """
    if not api_key:
        return 0.0, 0.0, 0.0, 0.0, 0.0, "Google Maps API key is missing. Add GOOGLE_MAPS_API_KEY in Streamlit secrets.", ""
    if not origin.strip() or not destination.strip():
        return 0.0, 0.0, 0.0, 0.0, 0.0, "Origin and destination are required for automatic delivery calculation.", ""

    def _address_variants(address: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", str(address or "").strip())
        if not cleaned:
            return []
        variants = [cleaned]
        if "jamaica" not in cleaned.lower():
            variants.append(f"{cleaned}, Jamaica")
        variants.append(cleaned.replace("Kingston 20", "Kingston").replace("kingston 20", "Kingston"))
        variants.append(cleaned.replace("Marverly", "Kingston").replace("marverly", "Kingston"))
        variants.append(cleaned.replace("Marverly, Kingston 20", "Kingston, Jamaica"))
        deduped: list[str] = []
        for item in variants:
            normalized = re.sub(r"\s+", " ", item).strip(" ,")
            if normalized and normalized.lower() not in {x.lower() for x in deduped}:
                deduped.append(normalized)
        return deduped

    def _google_status_help(status: str, message: str = "") -> str:
        status = str(status or "UNKNOWN").strip() or "UNKNOWN"
        extra = f" ({message})" if message else ""
        if status == "REQUEST_DENIED":
            return (
                f"Google status: REQUEST_DENIED{extra}. Check that billing is active, "
                "Geocoding API and Distance Matrix API are enabled, and the API key restrictions allow server-side use."
            )
        if status == "ZERO_RESULTS":
            return f"Google status: ZERO_RESULTS{extra}. Try a more specific Jamaica address."
        return f"Google status: {status}{extra}."

    def _geocode(address: str) -> tuple[float, float, str, str]:
        last_error = ""
        for candidate in _address_variants(address):
            params = urllib.parse.urlencode({"address": candidate, "region": "jm", "key": api_key})
            url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
            with urllib.request.urlopen(url, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            status = str(payload.get("status", ""))
            error_message = str(payload.get("error_message", "") or "")
            if status == "OK" and payload.get("results"):
                result = payload["results"][0]
                loc = result["geometry"]["location"]
                return float(loc["lat"]), float(loc["lng"]), "", ""
            last_error = _google_status_help(status, error_message)
            if status == "REQUEST_DENIED":
                break
        return 0.0, 0.0, f"Could not geocode '{address}'. {last_error or 'Google returned no usable result.'}", ""

    def _departure_unix(value: datetime | None) -> int:
        if value is None:
            return int(jamaica_now().timestamp())
        if value.tzinfo is None:
            value = value.replace(tzinfo=tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))
        return max(int(value.timestamp()), int(jamaica_now().timestamp()))

    def _distance_lookup(origins: str, destinations: str, departure: datetime | None = None) -> tuple[float, float, float, str]:
        params = {
            "origins": origins,
            "destinations": destinations,
            "units": "metric",
            "region": "jm",
            "mode": "driving",
            "departure_time": str(_departure_unix(departure)),
            "traffic_model": "best_guess",
            "key": api_key,
        }
        encoded = urllib.parse.urlencode(params)
        url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{encoded}"
        with urllib.request.urlopen(url, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = str(payload.get("status", ""))
        if status != "OK":
            return 0.0, 0.0, 0.0, f"Distance lookup failed. {_google_status_help(status, str(payload.get('error_message', '') or ''))}"
        rows = payload.get("rows") or []
        elements = rows[0].get("elements") if rows else []
        element = elements[0] if elements else {}
        element_status = str(element.get("status", ""))
        if element_status != "OK":
            return 0.0, 0.0, 0.0, f"No driving route found. {_google_status_help(element_status)}"
        distance_km = float(element["distance"]["value"]) / 1000.0
        free_flow_min = float(element.get("duration", {}).get("value", 0.0) or 0.0) / 60.0
        traffic_min = float(element.get("duration_in_traffic", {}).get("value", 0.0) or 0.0) / 60.0
        if traffic_min <= 0:
            traffic_min = free_flow_min
        return distance_km, free_flow_min, traffic_min, ""

    try:
        origin_lat, origin_lng, origin_error, _origin_parish = _geocode(origin)
        if origin_error:
            # If Geocoding API is blocked but Distance Matrix is allowed, try direct address lookup.
            for origin_candidate in _address_variants(origin):
                for dest_candidate in _address_variants(destination):
                    distance_km, free_min, traffic_min, direct_error = _distance_lookup(
                        origin_candidate,
                        dest_candidate,
                        delivery_departure,
                    )
                    if not direct_error:
                        _return_km, return_free_min, return_traffic_min, return_error = _distance_lookup(
                            dest_candidate,
                            origin_candidate,
                            collection_departure,
                        )
                        if return_error:
                            return_free_min = free_min
                            return_traffic_min = traffic_min
                        return distance_km, traffic_min, free_min, return_traffic_min, return_free_min, "", ""
            return 0.0, 0.0, 0.0, 0.0, 0.0, origin_error, ""
        dest_lat, dest_lng, dest_error, dest_parish = _geocode(destination)
        if dest_error:
            for dest_candidate in _address_variants(destination):
                distance_km, free_min, traffic_min, direct_error = _distance_lookup(
                    f"{origin_lat},{origin_lng}",
                    dest_candidate,
                    delivery_departure,
                )
                if not direct_error:
                    _return_km, return_free_min, return_traffic_min, return_error = _distance_lookup(
                        dest_candidate,
                        f"{origin_lat},{origin_lng}",
                        collection_departure,
                    )
                    if return_error:
                        return_free_min = free_min
                        return_traffic_min = traffic_min
                    return distance_km, traffic_min, free_min, return_traffic_min, return_free_min, "", ""
            return 0.0, 0.0, 0.0, 0.0, 0.0, dest_error, ""

        distance_km, free_min, traffic_min, distance_error = _distance_lookup(
            f"{origin_lat},{origin_lng}",
            f"{dest_lat},{dest_lng}",
            delivery_departure,
        )
        if distance_error:
            return distance_km, traffic_min, free_min, 0.0, 0.0, distance_error, dest_parish
        _return_km, return_free_min, return_traffic_min, return_error = _distance_lookup(
            f"{dest_lat},{dest_lng}",
            f"{origin_lat},{origin_lng}",
            collection_departure,
        )
        if return_error:
            return_free_min = free_min
            return_traffic_min = traffic_min
        return distance_km, traffic_min, free_min, return_traffic_min, return_free_min, "", dest_parish
    except Exception as exc:
        return 0.0, 0.0, 0.0, 0.0, 0.0, f"Google Maps lookup failed: {exc}", ""


def setup_minutes_defaults() -> list[dict[str, object]]:
    return [
        {"category": "chair", "keywords": "chair,chairs", "minutes_per_unit": 0.35},
        {"category": "table", "keywords": "table,tables", "minutes_per_unit": 2.0},
        {"category": "place setting", "keywords": "plate,plates,glass,glasses,cutlery,napkin,knife,knives,fork,forks,spoon,spoons", "minutes_per_unit": 0.75},
        {"category": "igloo/cooler", "keywords": "igloo,cooler,coolers", "minutes_per_unit": 3.0},
        {"category": "food warmer", "keywords": "warmer,chafing,food warmer", "minutes_per_unit": 3.0},
        {"category": "large item", "keywords": "tent,bounce,trampoline,stage,backdrop", "minutes_per_unit": 15.0},
        {"category": "default", "keywords": "", "minutes_per_unit": 5.0},
    ]


def load_setup_minutes_table() -> pd.DataFrame:
    raw = get_delivery_setting("setup_minutes_table_json", "")
    try:
        rows = json.loads(raw) if raw else setup_minutes_defaults()
    except Exception:
        rows = setup_minutes_defaults()
    table = pd.DataFrame(rows)
    for col, default in [
        ("category", "default"),
        ("keywords", ""),
        ("minutes_per_unit", 5.0),
    ]:
        if col not in table.columns:
            table[col] = default
    table["category"] = table["category"].astype(str).str.strip()
    table["keywords"] = table["keywords"].astype(str)
    place_setting_mask = table["category"].str.lower().eq("place setting")
    required_place_setting_keywords = ["knife", "knives", "fork", "forks", "spoon", "spoons"]
    if place_setting_mask.any():
        for idx in table[place_setting_mask].index:
            existing = [
                token.strip().lower()
                for token in str(table.at[idx, "keywords"] or "").split(",")
                if token.strip()
            ]
            for token in required_place_setting_keywords:
                if token not in existing:
                    existing.append(token)
            table.at[idx, "keywords"] = ",".join(existing)
    table["minutes_per_unit"] = pd.to_numeric(table["minutes_per_unit"], errors="coerce").fillna(5.0)
    return table[["category", "keywords", "minutes_per_unit"]].copy()


def save_setup_minutes_table(table: pd.DataFrame) -> None:
    clean = table.copy()
    for col in ["category", "keywords", "minutes_per_unit"]:
        if col not in clean.columns:
            clean[col] = "" if col != "minutes_per_unit" else 5.0
    clean["category"] = clean["category"].astype(str).str.strip()
    clean["keywords"] = clean["keywords"].astype(str).str.strip()
    clean["minutes_per_unit"] = pd.to_numeric(clean["minutes_per_unit"], errors="coerce").fillna(5.0)
    clean = clean[clean["category"] != ""].copy()
    set_delivery_setting(
        "setup_minutes_table_json",
        json.dumps(clean[["category", "keywords", "minutes_per_unit"]].to_dict(orient="records")),
    )


def public_holiday_defaults() -> list[dict[str, object]]:
    return [
        {"holiday_date": "", "holiday_name": ""},
    ]


def load_public_holidays_table() -> pd.DataFrame:
    raw = get_delivery_setting("public_holidays_table_json", "")
    try:
        rows = json.loads(raw) if raw else public_holiday_defaults()
    except Exception:
        rows = public_holiday_defaults()
    table = pd.DataFrame(rows)
    for col in ["holiday_date", "holiday_name"]:
        if col not in table.columns:
            table[col] = ""
    # Streamlit DateColumn requires a real date/datetime dtype, not text blanks.
    table["holiday_date"] = pd.to_datetime(table["holiday_date"], errors="coerce")
    table["holiday_name"] = table["holiday_name"].astype(str).str.strip()
    return table[["holiday_date", "holiday_name"]].copy()


def save_public_holidays_table(table: pd.DataFrame) -> None:
    clean = table.copy()
    for col in ["holiday_date", "holiday_name"]:
        if col not in clean.columns:
            clean[col] = ""
    clean["holiday_date"] = pd.to_datetime(clean["holiday_date"], errors="coerce").dt.date.astype(str)
    clean["holiday_date"] = clean["holiday_date"].replace("NaT", "")
    clean["holiday_name"] = clean["holiday_name"].astype(str).str.strip()
    clean = clean[clean["holiday_date"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)].copy()
    set_delivery_setting(
        "public_holidays_table_json",
        json.dumps(clean[["holiday_date", "holiday_name"]].to_dict(orient="records")),
    )


def public_holiday_dates_from_table(table: pd.DataFrame) -> set[date]:
    dates: set[date] = set()
    if table is None or table.empty:
        return dates
    for value in table.get("holiday_date", []):
        try:
            parsed = pd.to_datetime(value, errors="coerce")
            if not pd.isna(parsed):
                dates.add(parsed.date())
        except Exception:
            continue
    return dates


def east_west_toll_defaults() -> list[dict[str, object]]:
    return [
        {"plaza": "Portmore", "c1_no_tag": 380, "c1_tag": 370, "c2_no_tag": 710, "c2_tag": 690, "c3_no_tag": 1150, "c3_tag": 1150},
        {"plaza": "Spanish Town", "c1_no_tag": 285, "c1_tag": 275, "c2_no_tag": 530, "c2_tag": 510, "c3_no_tag": 850, "c3_tag": 850},
        {"plaza": "Vineyards", "c1_no_tag": 790, "c1_tag": 780, "c2_no_tag": 1200, "c2_tag": 1180, "c3_no_tag": 2400, "c3_tag": 2400},
        {"plaza": "May Pen", "c1_no_tag": 270, "c1_tag": 260, "c2_no_tag": 430, "c2_tag": 410, "c3_no_tag": 770, "c3_tag": 770},
        {"plaza": "Toll Gate-Main Line", "c1_no_tag": 480, "c1_tag": 470, "c2_no_tag": 720, "c2_tag": 700, "c3_no_tag": 1400, "c3_tag": 1400},
        {"plaza": "Toll Gate-Ramp", "c1_no_tag": 240, "c1_tag": 235, "c2_no_tag": 360, "c2_tag": 350, "c3_no_tag": 700, "c3_tag": 700},
    ]


def load_east_west_toll_table() -> pd.DataFrame:
    raw = get_delivery_setting("east_west_toll_table_json", "")
    try:
        rows = json.loads(raw) if raw else east_west_toll_defaults()
    except Exception:
        rows = east_west_toll_defaults()
    table = pd.DataFrame(rows)
    columns = ["plaza", "c1_no_tag", "c1_tag", "c2_no_tag", "c2_tag", "c3_no_tag", "c3_tag"]
    for col in columns:
        if col not in table.columns:
            table[col] = "" if col == "plaza" else 0.0
    table["plaza"] = table["plaza"].astype(str).str.strip()
    for col in columns[1:]:
        table[col] = pd.to_numeric(table[col], errors="coerce").fillna(0.0)
    return table[columns].copy()


def save_east_west_toll_table(table: pd.DataFrame) -> None:
    clean = table.copy()
    columns = ["plaza", "c1_no_tag", "c1_tag", "c2_no_tag", "c2_tag", "c3_no_tag", "c3_tag"]
    for col in columns:
        if col not in clean.columns:
            clean[col] = "" if col == "plaza" else 0.0
    clean["plaza"] = clean["plaza"].astype(str).str.strip()
    for col in columns[1:]:
        clean[col] = pd.to_numeric(clean[col], errors="coerce").fillna(0.0)
    clean = clean[clean["plaza"] != ""].copy()
    set_delivery_setting("east_west_toll_table_json", json.dumps(clean[columns].to_dict(orient="records")))


def route_toll_preset_defaults() -> list[dict[str, object]]:
    return [
        {
            "destination_match_keywords": "ocho rios, st ann, mammee bay",
            "highway_used": "North-South",
            "class_1_toll_jmd": 1600,
            "class_2_toll_jmd": 3200,
            "class_3_toll_jmd": 4900,
        }
    ]


def load_route_toll_presets_table() -> pd.DataFrame:
    raw = get_delivery_setting("route_toll_presets_json", "")
    try:
        rows = json.loads(raw) if raw else route_toll_preset_defaults()
    except Exception:
        rows = route_toll_preset_defaults()
    table = pd.DataFrame(rows)
    columns = [
        "destination_match_keywords",
        "highway_used",
        "class_1_toll_jmd",
        "class_2_toll_jmd",
        "class_3_toll_jmd",
    ]
    for col in columns:
        if col not in table.columns:
            table[col] = "" if col in {"destination_match_keywords", "highway_used"} else 0.0
    table["destination_match_keywords"] = table["destination_match_keywords"].astype(str).str.strip()
    table["highway_used"] = table["highway_used"].astype(str).str.strip()
    for col in columns[2:]:
        table[col] = pd.to_numeric(table[col], errors="coerce").fillna(0.0)
    return table[columns].copy()


def save_route_toll_presets_table(table: pd.DataFrame) -> None:
    clean = table.copy()
    columns = [
        "destination_match_keywords",
        "highway_used",
        "class_1_toll_jmd",
        "class_2_toll_jmd",
        "class_3_toll_jmd",
    ]
    for col in columns:
        if col not in clean.columns:
            clean[col] = "" if col in {"destination_match_keywords", "highway_used"} else 0.0
    clean["destination_match_keywords"] = clean["destination_match_keywords"].astype(str).str.strip()
    clean["highway_used"] = clean["highway_used"].astype(str).str.strip()
    for col in columns[2:]:
        clean[col] = pd.to_numeric(clean[col], errors="coerce").fillna(0.0)
    clean = clean[clean["destination_match_keywords"] != ""].copy()
    set_delivery_setting("route_toll_presets_json", json.dumps(clean[columns].to_dict(orient="records")))


def route_toll_preset_match(destination: str, presets: pd.DataFrame, toll_class: str) -> tuple[float, str, str]:
    if presets is None or presets.empty:
        return 0.0, "", ""
    normalized_destination = str(destination or "").lower()
    class_key = str(toll_class or "Class 1").lower().replace(" ", "_")
    rate_col = f"{class_key}_toll_jmd"
    if rate_col not in presets.columns:
        rate_col = "class_1_toll_jmd"
    for _, row in presets.iterrows():
        keywords = [
            token.strip().lower()
            for token in str(row.get("destination_match_keywords", "") or "").split(",")
            if token.strip()
        ]
        if keywords and any(token in normalized_destination for token in keywords):
            highway = str(row.get("highway_used", "") or "").strip() or "Preset"
            return float(row.get(rate_col, 0.0) or 0.0), highway, ", ".join(keywords)
    return 0.0, "", ""


def east_west_plaza_column(toll_class: str, has_ttag: bool) -> str:
    class_number = "1"
    if "2" in str(toll_class):
        class_number = "2"
    elif "3" in str(toll_class):
        class_number = "3"
    tag_suffix = "tag" if bool(has_ttag) else "no_tag"
    return f"c{class_number}_{tag_suffix}"


def east_west_plaza_fallback_rate(
    destination: str,
    toll_class: str,
    has_ttag: bool,
    east_west_table: pd.DataFrame,
) -> tuple[float, str]:
    """Conservative address-keyword fallback when no route preset or Google toll data exists."""
    if east_west_table is None or east_west_table.empty:
        return 0.0, ""
    dest = str(destination or "").lower()
    plaza_names: list[str] = []
    if "portmore" in dest:
        plaza_names = ["Portmore"]
    elif "spanish town" in dest or "st catherine" in dest or "angels" in dest:
        plaza_names = ["Spanish Town"]
    elif "old harbour" in dest or "vineyard" in dest or "vineyards" in dest:
        plaza_names = ["Spanish Town", "Vineyards"]
    elif "may pen" in dest or "clarendon" in dest:
        plaza_names = ["Spanish Town", "Vineyards", "May Pen"]
    elif "toll gate" in dest:
        plaza_names = ["Toll Gate-Main Line"]
    if not plaza_names:
        return 0.0, ""
    rate_col = east_west_plaza_column(toll_class, has_ttag)
    if rate_col not in east_west_table.columns:
        return 0.0, ""
    total = 0.0
    found_plazas: list[str] = []
    for plaza in plaza_names:
        rows = east_west_table[east_west_table["plaza"].astype(str).str.lower().eq(plaza.lower())]
        if rows.empty:
            continue
        total += float(rows.iloc[0].get(rate_col, 0.0) or 0.0)
        found_plazas.append(plaza)
    if total <= 0:
        return 0.0, ""
    tag_label = "tag" if has_ttag else "cash/no-tag"
    return total, f"East-West table fallback: {', '.join(found_plazas)} ({toll_class}, {tag_label})"


def google_routes_toll_estimate(
    origin: str,
    destination: str,
    api_key: str,
    departure_dt: datetime,
    fuel_type: str = "petrol",
) -> tuple[float, str]:
    """Best-effort Google toll lookup. Manual presets stay the trusted source for Jamaica."""
    if not api_key:
        return 0.0, "Google toll fallback skipped: API key missing."
    if not str(origin or "").strip() or not str(destination or "").strip():
        return 0.0, "Google toll fallback skipped: origin/destination missing."
    if departure_dt.tzinfo is None:
        departure_dt = departure_dt.replace(tzinfo=tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))
    departure_utc = departure_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": departure_utc,
        "extraComputations": ["TOLLS"],
        "routeModifiers": {
            "vehicleInfo": {
                "emissionType": "DIESEL" if str(fuel_type).lower() == "diesel" else "GASOLINE"
            }
        },
    }
    request = urllib.request.Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.travelAdvisory.tollInfo",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=14) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return 0.0, f"Google toll fallback unavailable: {exc}"
    prices = (
        (((data.get("routes") or [{}])[0]).get("travelAdvisory") or {})
        .get("tollInfo", {})
        .get("estimatedPrice", [])
    )
    total = 0.0
    for price in prices:
        units = float(price.get("units", 0) or 0)
        nanos = float(price.get("nanos", 0) or 0) / 1_000_000_000.0
        total += units + nanos
    if total <= 0:
        return 0.0, "Google returned no usable toll price for this Jamaica route."
    return round(total, 2), "Google Routes API toll estimate used because no manual preset matched."


def calculate_delivery_tolls(
    origin: str,
    destination: str,
    api_key: str,
    delivery_departure_dt: datetime,
    collection_departure_dt: datetime,
    round_trip_count: int,
    vehicle_rows: list[tuple],
    presets: pd.DataFrame,
    east_west_table: pd.DataFrame,
) -> tuple[float, list[dict[str, object]], list[str]]:
    total_tolls = 0.0
    detail_rows: list[dict[str, object]] = []
    notes: list[str] = []
    one_way_passes = max(1, int(round_trip_count or 1)) * 2
    for vehicle in vehicle_rows:
        vehicle_name = str(vehicle[0])
        fuel_type = str(vehicle[1])
        toll_class = str(vehicle[4]) if len(vehicle) > 4 else "Class 1"
        has_ttag = bool(vehicle[5]) if len(vehicle) > 5 else False
        one_way_rate, highway, matched_keywords = route_toll_preset_match(destination, presets, toll_class)
        if one_way_rate > 0:
            vehicle_toll = one_way_rate * one_way_passes
            total_tolls += vehicle_toll
            detail_rows.append(
                {
                    "Vehicle": vehicle_name,
                    "Toll Source": f"Preset: {highway}",
                    "Toll Class": toll_class,
                    "T-Tag/E-Pass": "Yes" if has_ttag else "No",
                    "One-Way Toll": money(one_way_rate),
                    "One-Way Passes": one_way_passes,
                    "Vehicle Toll Total": money(vehicle_toll),
                }
            )
            notes.append(
                f"{vehicle_name}: matched route toll preset ({matched_keywords}); Google toll lookup skipped."
            )
            continue

        google_one_way, google_note = google_routes_toll_estimate(
            origin,
            destination,
            api_key,
            delivery_departure_dt,
            fuel_type=fuel_type,
        )
        if google_one_way > 0:
            vehicle_toll = google_one_way * one_way_passes
            total_tolls += vehicle_toll
            detail_rows.append(
                {
                    "Vehicle": vehicle_name,
                    "Toll Source": "Google fallback",
                    "Toll Class": toll_class,
                    "T-Tag/E-Pass": "Yes" if has_ttag else "No",
                    "One-Way Toll": money(google_one_way),
                    "One-Way Passes": one_way_passes,
                    "Vehicle Toll Total": money(vehicle_toll),
                }
            )
            notes.append(f"{vehicle_name}: {google_note}")
        else:
            east_west_one_way, east_west_note = east_west_plaza_fallback_rate(
                destination,
                toll_class,
                has_ttag,
                east_west_table,
            )
            if east_west_one_way > 0:
                vehicle_toll = east_west_one_way * one_way_passes
                total_tolls += vehicle_toll
                detail_rows.append(
                    {
                        "Vehicle": vehicle_name,
                        "Toll Source": "East-West table fallback",
                        "Toll Class": toll_class,
                        "T-Tag/E-Pass": "Yes" if has_ttag else "No",
                        "One-Way Toll": money(east_west_one_way),
                        "One-Way Passes": one_way_passes,
                        "Vehicle Toll Total": money(vehicle_toll),
                    }
                )
                notes.append(f"{vehicle_name}: {east_west_note}. {google_note}")
                continue
            detail_rows.append(
                {
                    "Vehicle": vehicle_name,
                    "Toll Source": "None detected",
                    "Toll Class": toll_class,
                    "T-Tag/E-Pass": "Yes" if has_ttag else "No",
                    "One-Way Toll": money(0),
                    "One-Way Passes": one_way_passes,
                    "Vehicle Toll Total": money(0),
                }
            )
            notes.append(f"{vehicle_name}: no route toll preset matched. {google_note}")
    return round(total_tolls, 2), detail_rows, notes


def labour_premium_for_departure(
    departure_dt: datetime,
    public_holiday_dates: set[date],
    public_holiday_pct: float,
    sunday_pct: float,
    late_pct: float,
    early_pct: float,
) -> tuple[float, str]:
    local_dt = departure_dt.astimezone(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))
    applicable: list[tuple[float, str]] = []
    if local_dt.date() in public_holiday_dates:
        applicable.append((float(public_holiday_pct), "Public holiday"))
    if local_dt.weekday() == 6:
        applicable.append((float(sunday_pct), "Sunday"))
    if local_dt.time() >= time(18, 30):
        applicable.append((float(late_pct), "After 6:30pm"))
    if local_dt.time() < time(9, 0):
        applicable.append((float(early_pct), "Before 9:00am"))
    applicable = [(max(0.0, pct), label) for pct, label in applicable if pct > 0]
    if not applicable:
        return 0.0, "no premium"
    pct, label = max(applicable, key=lambda item: item[0])
    return pct, f"{label} premium (+{pct:g}%) applied"


def parse_jamaica_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_timestamp = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed_timestamp):
            return None
        parsed = parsed_timestamp.to_pydatetime()
    jamaica_tz = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=jamaica_tz)
    return parsed.astimezone(jamaica_tz)


def short_notice_premium_for_labour(
    labour_amount: float,
    delivery_departure_dt: datetime,
    order_placed_dt: datetime | None,
    threshold_hours: float,
    premium_pct: float,
    minimum_fee: float,
) -> tuple[float, float | None, str]:
    if order_placed_dt is None:
        return 0.0, None, "Short Notice not evaluated: no confirmed-booking timestamp yet."
    jamaica_tz = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    local_delivery = delivery_departure_dt.astimezone(jamaica_tz)
    local_order = order_placed_dt.astimezone(jamaica_tz)
    hours_notice = (local_delivery - local_order).total_seconds() / 3600.0
    threshold = max(0.0, float(threshold_hours or 0.0))
    if hours_notice < threshold:
        percent_addon = max(0.0, float(labour_amount or 0.0)) * (
            max(0.0, float(premium_pct or 0.0)) / 100.0
        )
        addon = round(max(percent_addon, max(0.0, float(minimum_fee or 0.0))), 2)
        return (
            addon,
            hours_notice,
            (
                f"applied: {hours_notice:,.1f}h notice is under {threshold:g}h; "
                f"{money(addon)} added once per booking"
            ),
        )
    return (
        0.0,
        hours_notice,
        f"not applied: {hours_notice:,.1f}h notice is at/above {threshold:g}h",
    )


def item_category_minutes(item_name: str, setup_table: pd.DataFrame) -> tuple[str, float]:
    normalized = str(item_name or "").lower()
    fallback = 5.0
    fallback_rows = setup_table[setup_table["category"].astype(str).str.lower() == "default"]
    if not fallback_rows.empty:
        fallback = float(fallback_rows.iloc[0].get("minutes_per_unit", 5.0) or 5.0)
    for _, row in setup_table.iterrows():
        category = str(row.get("category", "") or "").strip()
        if category.lower() == "default":
            continue
        keywords = [
            token.strip().lower()
            for token in str(row.get("keywords", "") or "").split(",")
            if token.strip()
        ]
        if keywords and any(token in normalized for token in keywords):
            return category, float(row.get("minutes_per_unit", fallback) or fallback)
    return "default", fallback


def setup_catalog_match_audit(inventory: pd.DataFrame, setup_table: pd.DataFrame) -> pd.DataFrame:
    columns = ["item_name", "matched_category", "minutes_per_unit"]
    if inventory is None or inventory.empty or "item_name" not in inventory.columns:
        return pd.DataFrame(columns=columns)

    item_names = sorted(
        {
            str(value).strip()
            for value in inventory["item_name"].dropna().tolist()
            if str(value).strip()
        },
        key=lambda value: value.lower(),
    )
    rows: list[dict[str, object]] = []
    for item_name in item_names:
        category, minutes = item_category_minutes(item_name, setup_table)
        rows.append(
            {
                "item_name": item_name,
                "matched_category": category,
                "minutes_per_unit": float(minutes),
                "_default_first": 0 if str(category).lower() == "default" else 1,
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(rows)
    out = out.sort_values(["_default_first", "matched_category", "item_name"], kind="stable")
    return out[columns].reset_index(drop=True)


def load_setup_time_log() -> list[dict[str, object]]:
    raw = get_delivery_setting("setup_time_log_json", "[]")
    try:
        rows = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def save_setup_time_log(rows: list[dict[str, object]]) -> None:
    set_delivery_setting("setup_time_log_json", json.dumps(rows, default=str))


def append_setup_time_log(entry: dict[str, object]) -> None:
    rows = load_setup_time_log()
    rows.append(entry)
    save_setup_time_log(rows)


def setup_log_items_from_invoice(invoice_id: int | None) -> pd.DataFrame:
    columns = ["item_name", "quantity"]
    if not invoice_id:
        return pd.DataFrame(columns=columns)
    try:
        _, items = invoice_export_bundle(int(invoice_id))
    except Exception:
        return pd.DataFrame(columns=columns)
    rows = remove_auto_fee_rows(items)
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows = rows[rows["item_type"].astype(str).str.lower().eq("product")].copy()
    rows = rows[rows["quantity"] > 0]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    grouped = rows.groupby("item_name", as_index=False)["quantity"].sum()
    return grouped[columns].reset_index(drop=True)


def normalize_setup_log_items(items: pd.DataFrame | object) -> pd.DataFrame:
    if isinstance(items, pd.DataFrame):
        out = items.copy()
    else:
        out = pd.DataFrame(items)
    for col, default in [("item_name", ""), ("quantity", 0.0)]:
        if col not in out.columns:
            out[col] = default
    out["item_name"] = out["item_name"].astype(str).str.strip()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0.0)
    out = out[(out["item_name"] != "") & (out["quantity"] > 0)]
    return out[["item_name", "quantity"]].reset_index(drop=True)


def setup_time_log_summary(log_entries: list[dict[str, object]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for entry in log_entries:
        items = entry.get("items", [])
        item_count = 0
        item_names: list[str] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_name = str(item.get("item_name", "") or "").strip()
                qty = float(item.get("quantity", 0.0) or 0.0)
                if item_name and qty > 0:
                    item_count += 1
                    item_names.append(f"{item_name} x{qty:g}")
        rows.append(
            {
                "date": entry.get("date", ""),
                "order_reference": entry.get("order_reference", ""),
                "client_name": entry.get("client_name", ""),
                "crew_size": entry.get("crew_size", ""),
                "wall_clock_minutes": entry.get("wall_clock_minutes", ""),
                "items_logged": item_count,
                "items": "; ".join(item_names[:6]) + ("..." if len(item_names) > 6 else ""),
            }
        )
    return pd.DataFrame(rows)


def setup_time_log_comparison(log_entries: list[dict[str, object]], setup_table: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for entry in log_entries:
        items = normalize_setup_log_items(entry.get("items", []))
        if items.empty:
            continue
        wall_clock_minutes = float(entry.get("wall_clock_minutes", 0.0) or 0.0)
        crew_size = max(1.0, float(entry.get("crew_size", 1.0) or 1.0))
        if wall_clock_minutes <= 0:
            continue

        category_qty: dict[str, float] = {}
        for _, item in items.iterrows():
            category, _ = item_category_minutes(str(item["item_name"]), setup_table)
            category_qty[category] = category_qty.get(category, 0.0) + float(item["quantity"])
        total_qty = sum(category_qty.values())
        if total_qty <= 0:
            continue
        dominant_category, dominant_qty = max(category_qty.items(), key=lambda pair: pair[1])
        if dominant_qty / total_qty < 0.70:
            continue

        rows.append(
            {
                "matched_category": dominant_category,
                "observed_minutes_per_unit": (wall_clock_minutes * crew_size) / dominant_qty,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "matched_category",
                "currently_configured_min_per_unit",
                "logged_jobs",
                "average_observed_min_per_unit",
                "gap_min_per_unit",
            ]
        )

    observed = pd.DataFrame(rows)
    grouped = observed.groupby("matched_category", as_index=False).agg(
        logged_jobs=("observed_minutes_per_unit", "count"),
        average_observed_min_per_unit=("observed_minutes_per_unit", "mean"),
    )
    grouped = grouped[grouped["logged_jobs"] >= 3].copy()
    if grouped.empty:
        return pd.DataFrame(
            columns=[
                "matched_category",
                "currently_configured_min_per_unit",
                "logged_jobs",
                "average_observed_min_per_unit",
                "gap_min_per_unit",
            ]
        )

    configured_lookup = {
        str(row.get("category", "") or "").strip(): float(row.get("minutes_per_unit", 0.0) or 0.0)
        for _, row in setup_table.iterrows()
    }
    grouped["currently_configured_min_per_unit"] = grouped["matched_category"].map(configured_lookup).fillna(0.0)
    grouped["gap_min_per_unit"] = (
        grouped["average_observed_min_per_unit"] - grouped["currently_configured_min_per_unit"]
    )
    for col in ["currently_configured_min_per_unit", "average_observed_min_per_unit", "gap_min_per_unit"]:
        grouped[col] = grouped[col].round(2)
    return grouped[
        [
            "matched_category",
            "currently_configured_min_per_unit",
            "logged_jobs",
            "average_observed_min_per_unit",
            "gap_min_per_unit",
        ]
    ].sort_values("matched_category", kind="stable")


def fragile_subtotal_or_order_total(items: pd.DataFrame, order_total: float) -> tuple[float, bool]:
    fragile_keywords = ("glass", "champagne", "plate", "bowl", "cutlery", "ceramic", "mirror", "vase")
    rows = normalize_invoice_items_df(items)
    subtotal = 0.0
    for _, row in rows.iterrows():
        name = str(row.get("item_name", "") or "").lower()
        if any(token in name for token in fragile_keywords):
            subtotal += float(row.get("quantity", 0.0) or 0.0) * float(row.get("unit_price", 0.0) or 0.0)
    if subtotal > 0:
        return subtotal, True
    return float(order_total), False


AUTO_FEE_ITEM_NAMES = {
    "gct (15%)",
    "delivery fee",
    "delivery and collection",
    "set-up fee",
    "setup and breakdown",
    "discount",
    "rental day multiplier",
    "day(s)",
}

STEP2_RESERVED_FEE_KEYWORDS = ("gct", "delivery", "discount")

DELIVERY_ZONE_DISTANCES_KM = {
    "Kingston & St. Andrew": 8.0,
    "St. Catherine": 24.0,
    "St. Thomas": 35.0,
    "Clarendon": 55.0,
    "St. Mary": 68.0,
    "St. Ann": 72.0,
    "Portland": 78.0,
    "Manchester": 86.0,
    "St. Elizabeth": 108.0,
    "Trelawny": 114.0,
    "St. James": 130.0,
    "Hanover": 162.0,
    "Westmoreland": 178.0,
}

DELIVERY_ZONE_KEYWORDS = {
    "Kingston & St. Andrew": ["kingston", "st andrew", "st. andrew", "new kingston", "half way tree"],
    "St. Catherine": ["st catherine", "st. catherine", "spanish town", "portmore", "linstead"],
    "St. Thomas": ["st thomas", "st. thomas", "morant bay", "yallahs"],
    "Clarendon": ["clarendon", "may pen"],
    "St. Mary": ["st mary", "st. mary", "port maria"],
    "St. Ann": ["st ann", "st. ann", "ocho rios", "runaway bay"],
    "Portland": ["portland", "port antonio"],
    "Manchester": ["manchester", "mandeville"],
    "St. Elizabeth": ["st elizabeth", "st. elizabeth", "black river", "santa cruz"],
    "Trelawny": ["trelawny", "falmouth"],
    "St. James": ["st james", "st. james", "montego bay", "mobay"],
    "Hanover": ["hanover", "lucea"],
    "Westmoreland": ["westmoreland", "savanna-la-mar", "sav la mar", "negril"],
}


def normalize_invoice_items_df(items: pd.DataFrame | object) -> pd.DataFrame:
    if isinstance(items, pd.DataFrame):
        out = items.copy()
    else:
        out = pd.DataFrame(items)

    for col, default in [
        ("item_name", ""),
        ("item_type", "product"),
        ("quantity", 0.0),
        ("unit_price", 0.0),
        ("unit_cost", 0.0),
    ]:
        if col not in out.columns:
            out[col] = default

    out["item_name"] = out["item_name"].astype(str).str.strip()
    out["item_type"] = out["item_type"].fillna("product").astype(str).str.strip().str.lower()
    out["item_type"] = out["item_type"].where(out["item_type"].isin(["product", "service"]), "product")
    for col in ["quantity", "unit_price", "unit_cost"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out


def remove_auto_fee_rows(items: pd.DataFrame | object) -> pd.DataFrame:
    out = normalize_invoice_items_df(items)
    keep = ~out["item_name"].apply(is_auto_fee_item_name)
    return out[keep].copy()


def is_auto_fee_item_name(name: object) -> bool:
    label = str(name or "").strip().lower()
    if not label:
        return False
    if label in AUTO_FEE_ITEM_NAMES:
        return True
    return bool(re.match(r"^day\(s\)\s*x\d+$", label))


def is_step2_reserved_fee_name(name: object) -> bool:
    label = str(name or "").strip().lower()
    if not label:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", label)
    if not compact:
        return False
    return any(keyword in compact for keyword in STEP2_RESERVED_FEE_KEYWORDS)


def sanitize_step2_items(items: pd.DataFrame | object) -> tuple[pd.DataFrame, list[str]]:
    out = normalize_invoice_items_df(items)
    reserved_mask = out["item_name"].apply(is_step2_reserved_fee_name)
    reserved_labels = sorted(
        {
            str(raw).strip()
            for raw in out.loc[reserved_mask, "item_name"].tolist()
            if str(raw).strip()
        }
    )
    if reserved_mask.any():
        out.loc[reserved_mask, "item_name"] = ""
        out.loc[reserved_mask, "item_type"] = "product"
        out.loc[reserved_mask, "quantity"] = 1.0
        out.loc[reserved_mask, "unit_price"] = 0.0
        out.loc[reserved_mask, "unit_cost"] = 0.0
    return out, reserved_labels


def _bundle_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def _bundle_items_from_source(items: pd.DataFrame | object) -> list[dict[str, object]]:
    base = remove_auto_fee_rows(items)
    base = base[
        (base["item_name"].str.strip() != "")
        & (pd.to_numeric(base["quantity"], errors="coerce").fillna(0.0) > 0)
    ].copy()
    if base.empty:
        return []
    rows: list[dict[str, object]] = []
    for _, row in base.iterrows():
        rows.append(
            {
                "item_name": str(row.get("item_name", "") or "").strip(),
                "item_type": str(row.get("item_type", "product") or "product").strip().lower(),
                "quantity": float(row.get("quantity", 0.0) or 0.0),
                "unit_price": float(row.get("unit_price", 0.0) or 0.0),
            }
        )
    return rows


def _bundle_items_to_df(items: list[dict[str, object]]) -> pd.DataFrame:
    if not isinstance(items, list) or not items:
        return pd.DataFrame(
            [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
        )
    frame = normalize_invoice_items_df(pd.DataFrame(items))
    frame = frame[(frame["item_name"].str.strip() != "") & (frame["quantity"] > 0)].copy()
    if frame.empty:
        frame = pd.DataFrame(
            [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
        )
    return frame[["item_name", "item_type", "quantity", "unit_price"]].copy()


def load_invoice_bundle_presets() -> list[dict[str, object]]:
    raw = get_setting(INVOICE_BUNDLE_PRESETS_KEY, "[]")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []

    out: list[dict[str, object]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        if not name:
            continue
        item_rows = _bundle_items_from_source(entry.get("items", []))
        if not item_rows:
            continue
        out.append(
            {
                "name": name,
                "notes": str(entry.get("notes", "") or "").strip(),
                "items": item_rows,
                "updated_at": str(entry.get("updated_at", "") or "").strip(),
            }
        )
    out = sorted(
        out,
        key=lambda row: (
            str(row.get("updated_at", "") or ""),
            str(row.get("name", "") or "").lower(),
        ),
        reverse=True,
    )
    return out


def save_invoice_bundle_presets(presets: list[dict[str, object]]) -> None:
    payload: list[dict[str, object]] = []
    for row in presets:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        item_rows = _bundle_items_from_source(row.get("items", []))
        if not item_rows:
            continue
        payload.append(
            {
                "name": name,
                "notes": str(row.get("notes", "") or "").strip(),
                "items": item_rows,
                "updated_at": str(row.get("updated_at", "") or "").strip()
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    set_setting(INVOICE_BUNDLE_PRESETS_KEY, json.dumps(payload, ensure_ascii=True))


def upsert_invoice_bundle_preset(
    bundle_name: str,
    items: pd.DataFrame | object,
    notes: str = "",
) -> tuple[bool, str]:
    clean_name = str(bundle_name or "").strip()
    if not clean_name:
        return (False, "Bundle name is required.")

    item_rows = _bundle_items_from_source(items)
    if not item_rows:
        return (False, "Bundle must include at least one item with quantity above 0.")

    slug = _bundle_slug(clean_name)
    if not slug:
        return (False, "Bundle name is invalid.")

    presets = load_invoice_bundle_presets()
    updated = False
    now_token = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_rows: list[dict[str, object]] = []
    for row in presets:
        if _bundle_slug(str(row.get("name", ""))) == slug:
            next_rows.append(
                {
                    "name": clean_name,
                    "notes": str(notes or "").strip(),
                    "items": item_rows,
                    "updated_at": now_token,
                }
            )
            updated = True
        else:
            next_rows.append(row)

    if not updated:
        next_rows.append(
            {
                "name": clean_name,
                "notes": str(notes or "").strip(),
                "items": item_rows,
                "updated_at": now_token,
            }
        )

    save_invoice_bundle_presets(next_rows)
    return (True, "Bundle saved.")


def delete_invoice_bundle_preset(bundle_name: str) -> bool:
    slug = _bundle_slug(bundle_name)
    if not slug:
        return False
    presets = load_invoice_bundle_presets()
    kept = [row for row in presets if _bundle_slug(str(row.get("name", ""))) != slug]
    removed = len(kept) != len(presets)
    if removed:
        save_invoice_bundle_presets(kept)
    return removed


def auto_quote_keywords(name: str) -> list[str]:
    raw = str(name or "").strip().lower()
    if not raw:
        return []

    normalized = raw.replace("&", " and ").replace("×", " x ")
    normalized = re.sub(r"\bby\b", " x ", normalized)
    normalized = re.sub(r"[^a-z0-9x\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    tokens = [AUTO_QUOTE_NUMBER_WORDS.get(token, token) for token in normalized.split(" ") if token]

    def _normalize_token(token: str) -> str:
        cleaned = str(token or "").strip().lower()
        if len(cleaned) > 3 and cleaned.endswith("s") and not cleaned.endswith("ss"):
            cleaned = cleaned[:-1]
        return cleaned

    tokens = [_normalize_token(token) for token in tokens if token]
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

    return [token for token in collapsed if token and token != "x" and token not in AUTO_QUOTE_ITEM_STOPWORDS]


def auto_quote_similarity(left: set[str], right: set[str]) -> float:
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


def auto_quote_match_inventory_item(
    raw_name: str,
    inventory_catalog: list[dict[str, object]],
) -> tuple[str, float, float]:
    name = str(raw_name or "").strip()
    if not name:
        return ("", 0.0, 0.0)
    signature = " ".join(sorted(auto_quote_keywords(name)))
    target_keywords = set(auto_quote_keywords(name))
    if not target_keywords:
        return (name, 0.0, 0.0)

    for row in inventory_catalog:
        if row["signature"] and row["signature"] == signature:
            return (
                str(row["item_name"]),
                float(row["unit_price"]),
                1.0,
            )

    best_name = name
    best_price = 0.0
    best_score = 0.0
    for row in inventory_catalog:
        score = auto_quote_similarity(target_keywords, set(row["keywords"]))
        if score > best_score:
            best_score = score
            best_name = str(row["item_name"])
            best_price = float(row["unit_price"])

    if best_score >= 0.62:
        return (best_name, best_price, best_score)
    return (name, 0.0, 0.0)


def auto_quote_extract_quantity(segment: str) -> float:
    text = str(segment or "").lower()
    patterns = [
        r"\bqty\s*[:=]?\s*(\d+(?:\.\d+)?)",
        r"\bquantity\s*[:=]?\s*(\d+(?:\.\d+)?)",
        r"\bx\s*(\d+(?:\.\d+)?)\b",
        r"\b(\d+(?:\.\d+)?)\s*x\b",
        r"\b(\d+(?:\.\d+)?)\s*(?:pcs|pieces|units?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                qty = float(match.group(1))
            except Exception:
                qty = 1.0
            return max(1.0, qty)
    return 1.0


def auto_quote_extract_amount(text: str, label_pattern: str) -> float | None:
    pattern = rf"{label_pattern}[^0-9]{{0,15}}(\d[\d,]*(?:\.\d+)?)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(str(match.group(1)).replace(",", ""))
    except Exception:
        return None


def auto_quote_extract_date(text: str) -> date | None:
    candidates: list[tuple[str, bool]] = []
    for match in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
        candidates.append((match, False))
    for match in re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text):
        candidates.append((match, True))
    for match in re.findall(
        r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+\d{1,2}(?:,\s*\d{4})?\b",
        text,
        flags=re.IGNORECASE,
    ):
        candidates.append((match, False))

    for token, dayfirst in candidates:
        parsed = pd.to_datetime(token, errors="coerce", dayfirst=dayfirst)
        if pd.notna(parsed):
            return parsed.date()
    return None


def auto_quote_extract_time(text: str) -> time | None:
    patterns = [
        r"\b(?:time|at)\s*[:=-]?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
        r"\b(?:time|at)\s*[:=-]?\s*(\d{1,2}:\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = pd.to_datetime(match.group(1), errors="coerce")
        if pd.notna(parsed):
            return time(parsed.hour, parsed.minute)
    return None


def parse_auto_quote_prompt(prompt_text: str) -> dict:
    raw = str(prompt_text or "").strip()
    text = raw.lower()
    inventory = cached_inventory_snapshot()
    catalog: list[dict[str, object]] = []
    if not inventory.empty:
        for _, row in inventory.iterrows():
            item_name = str(row.get("item_name", "") or "").strip()
            if not item_name:
                continue
            unit_price = float(pd.to_numeric(row.get("default_rental_price", 0.0), errors="coerce") or 0.0)
            keywords = auto_quote_keywords(item_name)
            catalog.append(
                {
                    "item_name": item_name,
                    "unit_price": unit_price,
                    "keywords": keywords,
                    "signature": " ".join(sorted(keywords)),
                }
            )

    customer_name = ""
    for pattern in [
        r"\bcustomer\s*[:=-]\s*([a-z0-9 .'\-]+)",
        r"\bclient\s*[:=-]\s*([a-z0-9 .'\-]+)",
        r"\bfor\s+([a-z][a-z0-9 .'\-]{2,60})",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = re.split(r"[,\n;]| at | on | with ", match.group(1), maxsplit=1)[0].strip()
        if candidate and all(token not in candidate for token in ["hour", "day", "delivery", "setup", "gct"]):
            customer_name = candidate.title()
            break

    event_date = auto_quote_extract_date(raw)
    event_time = auto_quote_extract_time(raw)
    location_match = re.search(
        r"\b(?:location|venue|event location|address)\s*[:=-]\s*([^\n,;]+)",
        raw,
        flags=re.IGNORECASE,
    )
    event_location = str(location_match.group(1)).strip() if location_match else ""

    duration_match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|days?|day)\b",
        text,
        flags=re.IGNORECASE,
    )
    if duration_match:
        raw_duration = float(duration_match.group(1))
        unit_token = duration_match.group(2).lower()
        duration_unit = "Days" if unit_token.startswith("day") else "Hours"
        duration_value = max(1.0, raw_duration)
    else:
        duration_unit = "Hours"
        duration_value = DEFAULT_EVENT_HOURS

    rental_hours = float(duration_value * 24.0) if duration_unit == "Days" else float(duration_value)
    rental_hours = max(1.0, rental_hours)

    gct_enabled = True
    if any(token in text for token in ["no gct", "without gct", "exclude gct", "gct off"]):
        gct_enabled = False
    elif any(token in text for token in ["add gct", "include gct", "with gct", "gct on"]):
        gct_enabled = True

    delivery_amount = auto_quote_extract_amount(text, r"delivery(?:\s+fee|\s+cost|\s+amount)?")
    setup_amount = auto_quote_extract_amount(text, r"(?:set[\s-]?up)(?:\s+fee|\s+cost|\s+amount)?")
    discount_percent_match = re.search(
        r"discount[^0-9]{0,15}(\d+(?:\.\d+)?)\s*%",
        text,
        flags=re.IGNORECASE,
    )
    if discount_percent_match:
        discount_mode = "Discount %"
        discount_percent = max(
            0.0,
            min(100.0, float(discount_percent_match.group(1) or 0.0)),
        )
        discount_amount = 0.0
    else:
        discount_amount_raw = auto_quote_extract_amount(text, r"discount(?:\s+amount|\s+value)?")
        discount_amount = max(0.0, float(discount_amount_raw or 0.0))
        discount_percent = 0.0
        discount_mode = "Discount Amount (JMD)" if discount_amount > 0 else "No Discount"

    items_source_match = re.search(
        r"\b(?:items?|equipment|products?)\s*[:=-]\s*(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    items_text = str(items_source_match.group(1)).strip() if items_source_match else raw
    segments = [
        seg.strip()
        for seg in re.split(r"[,\n;]+|\s+\band\b\s+", items_text, flags=re.IGNORECASE)
        if seg and seg.strip()
    ]

    parsed_items: list[dict[str, object]] = []
    unmatched_items: list[str] = []
    for segment in segments:
        lowered = segment.lower().strip()
        if not lowered:
            continue
        if any(keyword in lowered for keyword in AUTO_QUOTE_META_KEYWORDS):
            continue

        quantity = auto_quote_extract_quantity(segment)
        name_candidate = re.sub(
            r"\b(?:qty|quantity)\s*[:=]?\s*\d+(?:\.\d+)?|\bx\s*\d+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s*x\b|\b\d+(?:\.\d+)?\s*(?:pcs|pieces|units?)\b",
            " ",
            segment,
            flags=re.IGNORECASE,
        )
        name_candidate = re.sub(r"\s+", " ", name_candidate).strip(" -:")
        if not name_candidate:
            continue

        item_name, unit_price, score = auto_quote_match_inventory_item(name_candidate, catalog)
        if score <= 0 and catalog:
            unmatched_items.append(name_candidate)
        parsed_items.append(
            {
                "item_name": item_name,
                "item_type": "product",
                "quantity": float(quantity),
                "unit_price": float(unit_price),
            }
        )

    if not parsed_items:
        parsed_items = [
            {
                "item_name": "10x10 Tent",
                "item_type": "product",
                "quantity": 1.0,
                "unit_price": 0.0,
            }
        ]

    return {
        "customer_name": customer_name,
        "event_date": event_date,
        "event_time": event_time,
        "event_location": event_location,
        "duration_unit": duration_unit,
        "duration_value": float(duration_value),
        "rental_hours": float(rental_hours),
        "apply_gct": bool(gct_enabled),
        "delivery_amount": float(delivery_amount or 0.0),
        "setup_amount": float(setup_amount or 0.0),
        "discount_mode": discount_mode,
        "discount_percent": float(discount_percent),
        "discount_amount": float(discount_amount),
        "items_df": pd.DataFrame(parsed_items),
        "unmatched_items": unmatched_items[:5],
    }


def apply_auto_quote_draft(draft: dict) -> None:
    st.session_state["invoice_document_type_selector"] = "Price Quote"
    st.session_state["invoice_event_date_input"] = draft.get("event_date") or date.today()
    st.session_state["invoice_event_time_input"] = draft.get("event_time") or time(11, 0)
    st.session_state["invoice_event_location_input"] = str(draft.get("event_location", "") or "")
    st.session_state["invoice_customer_name_input"] = str(draft.get("customer_name", "") or "")
    st.session_state["invoice_apply_gct_input"] = bool(draft.get("apply_gct", True))
    st.session_state["invoice_delivery_manual_amount_input"] = float(draft.get("delivery_amount", 0.0) or 0.0)
    st.session_state["invoice_setup_fee_input"] = float(draft.get("setup_amount", 0.0) or 0.0)
    st.session_state["invoice_discount_mode_input"] = str(
        draft.get("discount_mode", "No Discount") or "No Discount"
    )
    st.session_state["invoice_discount_percent_input"] = float(draft.get("discount_percent", 0.0) or 0.0)
    st.session_state["invoice_discount_amount_input"] = float(draft.get("discount_amount", 0.0) or 0.0)

    duration_unit = str(draft.get("duration_unit", "Hours") or "Hours")
    duration_value = float(draft.get("duration_value", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS)
    rental_hours = float(draft.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS)
    if duration_unit == "Days":
        rental_days = max(1, int(math.ceil(duration_value)))
    else:
        rental_days = max(1, int(math.ceil(rental_hours / 24.0)))
    st.session_state["invoice_rental_days_input"] = int(rental_days)
    st.session_state["invoice_rental_hours_input"] = float(rental_days * 24.0)

    items_df = draft.get("items_df")
    if isinstance(items_df, pd.DataFrame) and not items_df.empty:
        out = items_df.copy()
        if "unit_cost" in out.columns:
            out = out.drop(columns=["unit_cost"])
        st.session_state["invoice_items_editor_data"] = out[
            ["item_name", "item_type", "quantity", "unit_price"]
        ].copy()
    st.session_state["invoice_items_editor_seed"] = int(st.session_state.get("invoice_items_editor_seed", 0)) + 1
    st.session_state["invoice_builder_allow_overwrite"] = False
    st.session_state["invoice_builder_loaded_invoice_number"] = ""


def wattbot_run_auto_quote(prompt_text: str) -> tuple[str, bool]:
    draft = parse_auto_quote_prompt(prompt_text)
    apply_auto_quote_draft(draft)
    st.session_state["nav_pending_section"] = "Build Invoice"

    rows = int(len(draft["items_df"])) if isinstance(draft.get("items_df"), pd.DataFrame) else 0
    duration_unit = str(draft.get("duration_unit", "Hours"))
    duration_value = float(draft.get("duration_value", DEFAULT_EVENT_HOURS))
    rental_hours = float(draft.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS)
    if duration_unit == "Days":
        duration_days = max(1, int(math.ceil(duration_value)))
    else:
        duration_days = max(1, int(math.ceil(rental_hours / 24.0)))
    unmatched = draft.get("unmatched_items", [])
    unmatched_line = ""
    if unmatched:
        unmatched_line = "\nUnmatched items need review: " + ", ".join([str(item) for item in unmatched])

    return (
        "Auto Quote Assistant drafted a price quote and opened Build Invoice.\n"
        f"- Items drafted: {rows}\n"
        f"- Rental Days: {duration_days}\n"
        f"- Delivery: {money(float(draft.get('delivery_amount', 0.0) or 0.0))}\n"
        f"- Set-Up: {money(float(draft.get('setup_amount', 0.0) or 0.0))}\n"
        f"- Discount: {str(draft.get('discount_mode', 'No Discount'))}\n"
        f"- GCT: {'ON' if bool(draft.get('apply_gct', True)) else 'OFF'}"
        f"{unmatched_line}\n"
        "Review line items and prices before saving.",
        True,
    )


def detect_delivery_zone(location_text: str) -> str:
    lowered = (location_text or "").strip().lower()
    if not lowered:
        return "Kingston & St. Andrew"
    for zone, keywords in DELIVERY_ZONE_KEYWORDS.items():
        if any(token in lowered for token in keywords):
            return zone
    return "Kingston & St. Andrew"


def estimate_invoice_weight_kg(
    items: pd.DataFrame | object,
    inventory_lookup: dict[str, dict[str, float | str]],
) -> float:
    rows = normalize_invoice_items_df(items)
    rows = rows[rows["item_type"] == "product"]
    total_weight = 0.0
    for _, row in rows.iterrows():
        qty = float(row.get("quantity") or 0.0)
        if qty <= 0:
            continue
        key = str(row.get("item_name", "")).strip().lower()
        unit_weight = float(inventory_lookup.get(key, {}).get("unit_weight_kg", 0.0) or 0.0)
        if unit_weight <= 0:
            continue
        total_weight += qty * unit_weight
    return float(total_weight)


def estimate_delivery_fee(
    distance_km: float,
    total_weight_kg: float,
    base_fee: float,
    per_km_fee: float,
    per_kg_fee: float,
) -> float:
    distance_value = max(0.0, float(distance_km or 0.0))
    weight_value = max(0.0, float(total_weight_kg or 0.0))
    base_value = max(0.0, float(base_fee or 0.0))
    per_km_value = max(0.0, float(per_km_fee or 0.0))
    per_kg_value = max(0.0, float(per_kg_fee or 0.0))
    return round(base_value + (distance_value * per_km_value) + (weight_value * per_kg_value), 2)


def delivery_setting_float(key: str, default: float) -> float:
    raw = str(get_delivery_setting(key, str(default))).strip()
    try:
        return float(raw)
    except Exception:
        return float(default)


def effective_setup_hourly_rate(
    setup_labour_cost_rate: float | None = None,
    setup_margin_pct: float | None = None,
) -> float:
    cost_rate = (
        float(setup_labour_cost_rate)
        if setup_labour_cost_rate is not None
        else delivery_setting_float("setup_labour_cost_rate", 625.0)
    )
    margin_pct = (
        float(setup_margin_pct)
        if setup_margin_pct is not None
        else delivery_setting_float("setup_margin_pct", 45.0)
    )
    return round(max(0.0, cost_rate) * (1.0 + (max(0.0, margin_pct) / 100.0)), 2)


def _hash_password(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def finance_password_hash() -> str:
    return get_setting(FINANCE_PASSWORD_KEY, "").strip()


def finance_password_enabled() -> bool:
    return bool(finance_password_hash())


def set_finance_password(secret: str) -> None:
    hashed = _hash_password(secret)
    set_setting(FINANCE_PASSWORD_KEY, hashed)


def verify_finance_password(secret: str) -> bool:
    stored = finance_password_hash()
    if not stored:
        return True
    attempt = _hash_password(secret)
    return bool(attempt) and hmac.compare_digest(stored, attempt)


def can_view_finance_data() -> bool:
    if not finance_password_enabled():
        return True
    return bool(st.session_state.get(FINANCE_AUTH_SESSION_KEY, False))


def current_device_name() -> str:
    return (platform.node() or "Unknown Device").strip() or "Unknown Device"


def render_startup_access_gate() -> bool:
    if st.session_state.get(APP_UNLOCKED_SESSION_KEY, False):
        return True

    st.title(APP_TITLE)
    st.markdown(
        "<div class='brand-strip'><b>Secure Access</b> | Owner password controls Finance Hub confidentiality.</div>",
        unsafe_allow_html=True,
    )

    if not finance_password_enabled():
        st.warning(
            "First-time setup: create the Finance Hub password now. This protects profitability and wage data."
        )
        st.caption(
            "After setup, owner access will require this password each new app session. "
            "On supported devices, password autofill/passkey can use Face ID or Touch ID."
        )
        with st.form("first_time_finance_password_form", clear_on_submit=True):
            new_pw = st.text_input("Create Finance Password", type="password")
            confirm_pw = st.text_input("Confirm Finance Password", type="password")
            setup_submit = st.form_submit_button("Set Password and Continue")
        if setup_submit:
            if len((new_pw or "").strip()) < 6:
                st.error("Password must be at least 6 characters.")
            elif new_pw != confirm_pw:
                st.error("Passwords do not match.")
            else:
                set_finance_password(new_pw)
                st.session_state[APP_UNLOCKED_SESSION_KEY] = True
                st.session_state[APP_ACCESS_LEVEL_SESSION_KEY] = "owner"
                st.session_state[FINANCE_AUTH_SESSION_KEY] = True
                st.success("Password set. Owner access unlocked.")
                st.rerun()
        return False

    st.warning("This app is locked. Enter Finance password to continue.")
    st.caption(
        "Every new app session requires this password. "
        "On supported devices, password autofill/passkey can use Face ID or Touch ID."
    )
    with st.form("startup_owner_unlock_form", clear_on_submit=False):
        unlock_password = st.text_input("Finance Password", type="password")
        unlock_owner_submit = st.form_submit_button("Unlock Owner Access")
    if unlock_owner_submit:
        if verify_finance_password(unlock_password):
            st.session_state[APP_UNLOCKED_SESSION_KEY] = True
            st.session_state[APP_ACCESS_LEVEL_SESSION_KEY] = "owner"
            st.session_state[FINANCE_AUTH_SESSION_KEY] = True
            st.success("Owner access unlocked.")
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


def resolve_theme_mode(theme_pref: str) -> str:
    return "day"


def inject_styles(theme_mode: str) -> None:
    st.session_state["active_theme_mode"] = "day"
    bg_color = "#f3f4f6"
    surface_color = "#ffffff"
    sidebar_color = "#eef2f7"
    text_color = "#111827"
    text_muted = "#475569"
    border_color = "#d1d5db"
    shadow_color = "rgba(15, 23, 42, 0.08)"
    accent_soft = "rgba(37, 99, 235, 0.45)"
    accent_bg = "rgba(241, 245, 249, 0.9)"
    chip_bg = "#f1f5f9"
    input_bg = "#ffffff"
    surface_alt = "#f8fafc"
    focus_ring = "0 0 0 3px rgba(59, 130, 246, 0.28)"

    css = """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;700&display=swap');

        :root {
            --primary-color: __PRIMARY_COLOR__;
            --secondary-color: __SECONDARY_COLOR__;
            --bg-color: __BG_COLOR__;
            --surface-color: __SURFACE_COLOR__;
            --surface-alt: __SURFACE_ALT__;
            --sidebar-color: __SIDEBAR_COLOR__;
            --text-color: __TEXT_COLOR__;
            --text-muted: __TEXT_MUTED__;
            --border-color: __BORDER_COLOR__;
            --shadow-color: __SHADOW_COLOR__;
            --accent-soft: __ACCENT_SOFT__;
            --accent-bg: __ACCENT_BG__;
            --chip-bg: __CHIP_BG__;
            --input-bg: __INPUT_BG__;
            --focus-ring: __FOCUS_RING__;
            --layout-max-width: 1260px;
            --space-1: 8px;
            --space-2: 16px;
            --space-3: 24px;
            --space-4: 32px;
            color-scheme: light !important;
        }

        html, body, [class*="css"] {
            font-family: 'Manrope', 'Avenir Next', 'Segoe UI', sans-serif;
            -webkit-font-smoothing: antialiased;
            text-rendering: optimizeLegibility;
            color-scheme: light !important;
            background-color: var(--bg-color) !important;
            color: var(--text-color) !important;
        }
        html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {
            color-scheme: light !important;
            background-color: var(--bg-color) !important;
            color: var(--text-color) !important;
        }
        [data-testid="stMain"], [data-testid="stMainBlockContainer"] {
            background-color: var(--bg-color) !important;
            color: var(--text-color) !important;
        }
        [data-testid="stSidebar"], [data-testid="stSidebarContent"] {
            background: var(--sidebar-color) !important;
            color: var(--text-color) !important;
        }
        @media (prefers-color-scheme: dark) {
            html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"],
            [data-testid="stMain"], [data-testid="stMainBlockContainer"],
            [data-testid="stSidebar"], [data-testid="stSidebarContent"] {
                color-scheme: light !important;
                background-color: var(--bg-color) !important;
                color: var(--text-color) !important;
            }
        }

        [data-testid="stAppViewContainer"] > .main {
            background-color: var(--bg-color) !important;
            background-image: linear-gradient(180deg, var(--accent-bg), transparent 280px);
            color: var(--text-color) !important;
        }
        [data-testid="stAppViewBlockContainer"] {
            max-width: var(--layout-max-width);
            padding-top: var(--space-2);
            padding-bottom: calc(var(--space-4) * 2);
        }
        [data-testid="stSidebar"] {
            background: var(--sidebar-color) !important;
            border-right: 1px solid var(--border-color);
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        h1, h2, h3, h4, h5, h6 {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            color: var(--text-color);
            letter-spacing: -0.01em;
        }
        p, label, li, div[data-testid="stMarkdownContainer"] {
            color: var(--text-color);
            line-height: 1.48;
        }
        [data-testid="stCaptionContainer"] p {
            color: var(--text-muted) !important;
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
            color: var(--text-muted) !important;
        }
        .brand-strip {
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 12px 14px;
            background:
                linear-gradient(180deg, var(--surface-color), var(--surface-alt));
            box-shadow: 0 6px 22px var(--shadow-color);
            color: var(--text-color);
            margin-bottom: 8px;
        }
        .brand-strip b {
            color: var(--text-color);
        }
        .dashboard-card {
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 15px 17px;
            background: var(--surface-color);
            box-shadow: 0 5px 20px var(--shadow-color);
            margin-bottom: var(--space-1);
        }
        .small-label {
            color: var(--text-muted);
            font-size: 0.82rem;
            font-weight: 600;
            margin-bottom: 2px;
        }
        .value-label {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            font-size: 1.22rem;
            font-weight: 700;
            color: var(--text-color);
        }
        .kpi-spark {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            font-size: 0.82rem;
            letter-spacing: 0.12em;
            color: var(--text-muted);
            margin-top: 5px;
            opacity: 0.95;
        }
        .hr-section-shell {
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 13px 14px;
            background: linear-gradient(180deg, var(--surface-color), var(--surface-alt));
            box-shadow: 0 5px 20px var(--shadow-color);
            margin: var(--space-1) 0 var(--space-2);
        }
        .hr-section-shell .title {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            font-weight: 700;
            font-size: 1.13rem;
            color: var(--text-color);
        }
        .hr-section-shell .subtitle {
            color: var(--text-muted);
            font-size: 0.92rem;
            margin-top: 4px;
        }
        .hr-step-card {
            border: 1px solid var(--border-color);
            border-radius: 14px;
            background: var(--surface-color);
            box-shadow: 0 4px 16px var(--shadow-color);
            padding: 10px 12px;
            margin: var(--space-2) 0 var(--space-1);
        }
        .hr-step-title {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            font-weight: 700;
            font-size: 0.98rem;
            color: var(--text-color);
        }
        .hr-step-sub {
            color: var(--text-muted);
            font-size: 0.86rem;
            margin-top: 3px;
        }
        .hr-top-nav {
            position: sticky;
            top: 0;
            z-index: 700;
            background: rgba(255, 255, 255, 0.95);
            border: 1px solid var(--border-color);
            border-radius: 13px;
            box-shadow: 0 6px 20px var(--shadow-color);
            backdrop-filter: blur(8px);
            padding: 8px 10px;
            margin-bottom: var(--space-2);
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }
        .hr-top-nav .hr-nav-link {
            color: var(--text-color);
            border: 1px solid var(--border-color);
            border-radius: 999px;
            padding: 6px 10px;
            font-size: 0.86rem;
            font-weight: 600;
            background: var(--surface-color);
            cursor: pointer;
            appearance: none;
            -webkit-appearance: none;
            text-decoration: none;
        }
        .hr-top-nav .hr-nav-link.active {
            border-color: var(--accent-soft);
            box-shadow: inset 0 -2px 0 var(--text-color);
        }
        .hr-top-nav .meta {
            margin-left: auto;
            color: var(--text-muted);
            font-size: 0.82rem;
            white-space: nowrap;
        }
        .hr-mobile-bottom-nav {
            display: none;
        }
        .hr-mobile-bottom-spacer {
            display: none;
        }
        .insight-strip {
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 12px;
            background: var(--surface-color);
            box-shadow: 0 5px 20px var(--shadow-color);
            margin-bottom: 12px;
        }
        .insight-chip {
            border: 1px solid var(--border-color);
            border-radius: 999px;
            padding: 8px 12px;
            display: inline-block;
            margin: 4px 6px 4px 0;
            font-size: 0.82rem;
            color: var(--text-color);
            background: var(--chip-bg);
        }
        .insight-chip b {
            color: var(--text-color);
        }
        .hint-card {
            border: 1px dashed var(--border-color);
            border-radius: 14px;
            padding: 10px 12px;
            background: var(--surface-alt);
            color: var(--text-muted);
            margin-bottom: 10px;
        }
        .stButton > button,
        [data-testid="baseButton-secondary"],
        [data-testid="baseButton-primary"] {
            border: 1px solid var(--border-color);
            background: var(--surface-color);
            color: var(--text-color);
            border-radius: 11px;
            transition: all 0.18s ease;
            min-height: 2.5rem;
            font-weight: 600;
        }
        .stButton > button:hover {
            border-color: var(--accent-soft);
            box-shadow: 0 5px 14px var(--shadow-color);
            transform: translateY(-0.5px);
        }
        .stButton > button:focus,
        [data-testid="baseButton-secondary"]:focus,
        [data-testid="baseButton-primary"]:focus {
            box-shadow: var(--focus-ring) !important;
            outline: none;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            margin-bottom: 8px;
        }
        .stTabs [data-baseweb="tab"] {
            border: 1px solid var(--border-color);
            border-radius: 11px;
            background: var(--surface-alt);
            color: var(--text-muted);
            font-weight: 600;
            min-height: 2.35rem;
        }
        .stTabs [aria-selected="true"] {
            color: var(--text-color);
            border-color: var(--accent-soft);
            background: var(--surface-color);
            box-shadow: inset 0 -2px 0 var(--text-color), 0 3px 10px var(--shadow-color);
        }
        .stTextInput > div > div > input,
        .stDateInput input,
        .stNumberInput input,
        .stTextArea textarea,
        .stTimeInput input,
        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div {
            background: var(--input-bg);
            color: var(--text-color);
            border-color: var(--border-color);
            border-radius: 10px;
            min-height: 2.5rem;
        }
        .stTextInput > div > div > input:focus,
        .stDateInput input:focus,
        .stNumberInput input:focus,
        .stTextArea textarea:focus,
        .stTimeInput input:focus,
        .stSelectbox [data-baseweb="select"] > div:focus-within,
        .stMultiSelect [data-baseweb="select"] > div:focus-within {
            border-color: var(--accent-soft) !important;
            box-shadow: var(--focus-ring) !important;
        }
        [data-testid="stForm"] {
            border: 1px solid var(--border-color);
            border-radius: 15px;
            padding: 0.95rem 0.95rem 0.45rem;
            background: var(--surface-color);
            box-shadow: 0 4px 18px var(--shadow-color);
        }
        [data-testid="stDataFrame"] {
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            background: var(--surface-color);
        }
        [data-testid="stDataFrame"] * {
            color: var(--text-color) !important;
        }
        [data-testid="stTable"] * {
            color: var(--text-color) !important;
        }
        [data-testid="stMetric"] {
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 8px 12px;
            background: var(--surface-color);
            box-shadow: 0 4px 12px var(--shadow-color);
        }
        [data-testid="stMetricLabel"] p {
            color: var(--text-muted) !important;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] div {
            font-family: 'Space Grotesk', 'Manrope', sans-serif;
            color: var(--text-color);
        }
        [data-testid="stExpander"] {
            border: 1px solid var(--border-color);
            border-radius: 12px;
            background: var(--surface-color);
            overflow: hidden;
        }
        [data-testid="stExpander"] details summary {
            background: var(--surface-alt);
        }
        [data-testid="stAlertContainer"] {
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }
        hr {
            border-color: var(--border-color);
        }
        @media (max-width: 860px) {
            :root {
                --layout-max-width: 100%;
            }
            [data-testid="stAppViewBlockContainer"] {
                padding-left: 0.72rem;
                padding-right: 0.72rem;
                padding-top: 0.55rem;
                padding-bottom: calc(5.6rem + env(safe-area-inset-bottom));
            }
            .hr-top-nav {
                top: 2px;
                border-radius: 12px;
                padding: 7px 8px;
                flex-wrap: nowrap;
                overflow-x: auto;
                overflow-y: hidden;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
                white-space: nowrap;
            }
            .hr-top-nav::-webkit-scrollbar {
                display: none;
            }
            .hr-top-nav .hr-nav-link {
                flex: 0 0 auto;
            }
            .hr-top-nav .meta {
                display: none;
            }
            .hr-mobile-bottom-nav {
                display: flex;
                position: fixed;
                left: 8px;
                right: 8px;
                bottom: calc(8px + env(safe-area-inset-bottom));
                z-index: 760;
                border: 1px solid var(--border-color);
                border-radius: 14px;
                background: rgba(255, 255, 255, 0.98);
                box-shadow: 0 10px 26px var(--shadow-color);
                padding: 7px 8px;
                align-items: center;
                justify-content: space-between;
                gap: 6px;
                backdrop-filter: blur(8px);
            }
            .hr-mobile-bottom-nav .hr-nav-link {
                color: var(--text-color);
                border: 1px solid var(--border-color);
                border-radius: 10px;
                padding: 7px 9px;
                font-size: 0.76rem;
                font-weight: 700;
                background: var(--surface-color);
                text-align: center;
                flex: 1 1 0;
                cursor: pointer;
                appearance: none;
                -webkit-appearance: none;
                text-decoration: none;
            }
            .hr-mobile-bottom-nav .hr-nav-link.active {
                border-color: var(--accent-soft);
                box-shadow: inset 0 -2px 0 var(--text-color);
            }
            .hr-mobile-bottom-spacer {
                display: block;
                height: 88px;
            }
            h1 {
                font-size: 1.58rem !important;
                line-height: 1.25 !important;
            }
            h2 {
                font-size: 1.34rem !important;
                line-height: 1.28 !important;
            }
            h3 {
                font-size: 1.14rem !important;
            }
            p, label, span {
                font-size: 0.96rem;
            }
            .dashboard-card {
                padding: 11px 12px;
                border-radius: 14px;
            }
            .value-label {
                font-size: 1.08rem;
            }
            .small-label {
                font-size: 0.79rem;
            }
            .brand-strip {
                font-size: 0.88rem;
                padding: 9px 10px;
                line-height: 1.35;
                word-break: break-word;
            }
            .stButton > button {
                width: 100%;
                min-height: 2.8rem;
                font-size: 0.98rem;
            }
            [data-testid="stAppViewContainer"] div[data-testid="stHorizontalBlock"] {
                display: flex;
                flex-wrap: wrap;
                gap: 0.55rem;
            }
            [data-testid="stAppViewContainer"] div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }
            .stTabs [data-baseweb="tab-list"] {
                overflow-x: auto;
                overflow-y: hidden;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
                flex-wrap: nowrap !important;
                padding-bottom: 2px;
                gap: 6px;
            }
            .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
                display: none;
            }
            .stTabs [data-baseweb="tab"] {
                flex: 0 0 auto;
                min-width: max-content;
                padding-left: 0.72rem;
                padding-right: 0.72rem;
            }
            .stTextInput > div > div > input,
            .stDateInput input,
            .stNumberInput input,
            .stTextArea textarea,
            .stTimeInput input,
            .stSelectbox [data-baseweb="select"] > div,
            .stMultiSelect [data-baseweb="select"] > div {
                font-size: 16px !important;
                min-height: 2.8rem;
            }
            .stTextArea textarea {
                min-height: 6.5rem;
            }
            [data-testid="stDataFrame"] {
                overflow-x: auto;
            }
            [data-testid="stSidebar"] {
                background: var(--sidebar-color) !important;
            }
            [data-testid="stToolbar"] {
                display: none !important;
            }
        }
        @media (max-width: 520px) {
            [data-testid="stAppViewBlockContainer"] {
                padding-left: 0.58rem;
                padding-right: 0.58rem;
            }
            .stTabs [data-baseweb="tab"] {
                font-size: 0.86rem;
                padding-left: 0.58rem;
                padding-right: 0.58rem;
            }
        }
    </style>
    """
    tokens = {
        "__PRIMARY_COLOR__": PRIMARY_COLOR,
        "__SECONDARY_COLOR__": SECONDARY_COLOR,
        "__BG_COLOR__": bg_color,
        "__SURFACE_COLOR__": surface_color,
        "__SURFACE_ALT__": surface_alt,
        "__SIDEBAR_COLOR__": sidebar_color,
        "__TEXT_COLOR__": text_color,
        "__TEXT_MUTED__": text_muted,
        "__BORDER_COLOR__": border_color,
        "__SHADOW_COLOR__": shadow_color,
        "__ACCENT_SOFT__": accent_soft,
        "__ACCENT_BG__": accent_bg,
        "__CHIP_BG__": chip_bg,
        "__INPUT_BG__": input_bg,
        "__FOCUS_RING__": focus_ring,
    }
    for token, value in tokens.items():
        css = css.replace(token, value)
    st.markdown(css, unsafe_allow_html=True)
def render_section_shell(title: str, subtitle: str = "") -> None:
    safe_title = html.escape(str(title or "").strip())
    safe_subtitle = html.escape(str(subtitle or "").strip())
    subtitle_html = f"<div class='subtitle'>{safe_subtitle}</div>" if safe_subtitle else ""
    st.markdown(
        f"""
        <div class="hr-section-shell">
            <div class="title">{safe_title}</div>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_step_block(step_title: str, step_subtitle: str = "") -> None:
    safe_title = html.escape(str(step_title or "").strip())
    safe_sub = html.escape(str(step_subtitle or "").strip())
    sub_html = f"<div class='hr-step-sub'>{safe_sub}</div>" if safe_sub else ""
    st.markdown(
        f"""
        <div class="hr-step-card">
            <div class="hr-step-title">{safe_title}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def sparkline(values: list[float] | tuple[float, ...] | None) -> str:
    if not values:
        return ""
    try:
        arr = [float(v) for v in values if pd.notna(v)]
    except Exception:
        return ""
    if not arr:
        return ""
    if len(arr) == 1:
        return "▇"
    lo = min(arr)
    hi = max(arr)
    if abs(hi - lo) < 1e-9:
        return "▇" * min(12, len(arr))
    ticks = "▁▂▃▄▅▆▇█"
    scaled = []
    for val in arr[-12:]:
        idx = int(round(((val - lo) / (hi - lo)) * (len(ticks) - 1)))
        idx = max(0, min(len(ticks) - 1, idx))
        scaled.append(ticks[idx])
    return "".join(scaled)


def render_kpi(
    label: str,
    value: str,
    trend_values: list[float] | tuple[float, ...] | None = None,
) -> None:
    spark = sparkline(trend_values)
    spark_html = f"<div class='kpi-spark'>{spark}</div>" if spark else ""
    st.markdown(
        f"""
        <div class="dashboard-card">
            <div class="small-label">{label}</div>
            <div class="value-label">{value}</div>
            {spark_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def current_experience_mode() -> str:
    return str(st.session_state.get("experience_mode", "Guided Visual"))


def style_plotly(fig) -> None:
    axis_color = "rgba(51, 65, 85, 0.18)"
    font_color = "#111827"
    plot_bg = "rgba(255,255,255,0.82)"
    colorway = ["#1d4ed8", "#059669", "#d97706", "#dc2626", "#7c3aed"]
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=plot_bg,
        font={"family": "Manrope, Avenir Next, Segoe UI, sans-serif", "color": font_color},
        colorway=colorway,
        margin={"l": 22, "r": 22, "t": 52, "b": 24},
        legend_title_text="",
    )
    fig.update_xaxes(showgrid=True, gridcolor=axis_color, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=axis_color, zeroline=False)


@st.cache_data(ttl=120, show_spinner=False)
def cached_invoice_level() -> pd.DataFrame:
    return load_invoice_level()


@st.cache_data(ttl=120, show_spinner=False)
def cached_expenses() -> pd.DataFrame:
    return load_expenses()


@st.cache_data(ttl=120, show_spinner=False)
def cached_daily_summary() -> pd.DataFrame:
    return load_daily_summary()


@st.cache_data(ttl=120, show_spinner=False)
def cached_monthly_summary() -> pd.DataFrame:
    return load_monthly_summary()


@st.cache_data(ttl=120, show_spinner=False)
def cached_weekly_summary() -> pd.DataFrame:
    return load_weekly_summary()


@st.cache_data(ttl=120, show_spinner=False)
def cached_yearly_summary() -> pd.DataFrame:
    return load_yearly_summary()


@st.cache_data(ttl=120, show_spinner=False)
def cached_product_profitability() -> pd.DataFrame:
    return load_product_profitability()


@st.cache_data(ttl=120, show_spinner=False)
def cached_product_type_profitability() -> pd.DataFrame:
    return load_product_type_profitability()


@st.cache_data(ttl=120, show_spinner=False)
def cached_supplier_expenses() -> pd.DataFrame:
    return load_supplier_expenses()


@st.cache_data(ttl=120, show_spinner=False)
def cached_supplier_monthly_expenses() -> pd.DataFrame:
    return load_supplier_monthly_expenses()


@st.cache_data(ttl=120, show_spinner=False)
def cached_supplier_performance_ranking() -> pd.DataFrame:
    return load_supplier_performance_ranking()


@st.cache_data(ttl=120, show_spinner=False)
def cached_tax_pack_monthly() -> pd.DataFrame:
    return load_tax_pack_monthly()


@st.cache_data(ttl=120, show_spinner=False)
def cached_tax_pack_invoice_detail() -> pd.DataFrame:
    return load_tax_pack_invoice_detail()


@st.cache_data(ttl=120, show_spinner=False)
def cached_monthly_expense_modes() -> pd.DataFrame:
    return load_monthly_expense_modes()


@st.cache_data(ttl=120, show_spinner=False)
def cached_budget_vs_actual() -> pd.DataFrame:
    return load_budget_vs_actual()


@st.cache_data(ttl=120, show_spinner=False)
def cached_expense_category_budget_vs_actual() -> pd.DataFrame:
    return load_expense_category_budget_vs_actual()


@st.cache_data(ttl=120, show_spinner=False)
def cached_monthly_budgets() -> pd.DataFrame:
    return load_monthly_budgets()


@st.cache_data(ttl=120, show_spinner=False)
def cached_expense_category_budgets() -> pd.DataFrame:
    return load_expense_category_budgets()


@st.cache_data(ttl=120, show_spinner=False)
def cached_finance_activity(limit: int = 300) -> pd.DataFrame:
    return load_finance_activity(limit=limit)


@st.cache_data(ttl=120, show_spinner=False)
def cached_recurring_templates() -> pd.DataFrame:
    return load_recurring_expense_templates(active_only=False)


@st.cache_data(ttl=120, show_spinner=False)
def cached_inventory_purchases() -> pd.DataFrame:
    return load_inventory_purchases()


@st.cache_data(ttl=120, show_spinner=False)
def cached_wages_period(period: str) -> pd.DataFrame:
    return load_wages_period_summary(period)


@st.cache_data(ttl=120, show_spinner=False)
def cached_wages_by_person_monthly() -> pd.DataFrame:
    return load_wages_by_person_monthly()


@st.cache_data(ttl=120, show_spinner=False)
def cached_weekly_cashflow_forecast(weeks: int = 8, history_weeks: int = 8) -> pd.DataFrame:
    return load_weekly_cashflow_forecast(weeks=weeks, history_weeks=history_weeks)


@st.cache_data(ttl=120, show_spinner=False)
def cached_finance_data_quality_checks() -> pd.DataFrame:
    return load_finance_data_quality_checks()


@st.cache_data(ttl=30, show_spinner=False)
def cached_invoice_options(
    include_quotes: bool = True,
    confirmed_only: bool = False,
) -> pd.DataFrame:
    frame = invoice_options(include_quotes=include_quotes, confirmed_only=confirmed_only)
    return frame.copy()


@st.cache_data(ttl=30, show_spinner=False)
def cached_inventory_snapshot() -> pd.DataFrame:
    frame = load_inventory_snapshot()
    return frame.copy()


@st.cache_data(ttl=30, show_spinner=False)
def cached_event_product_allocations() -> pd.DataFrame:
    frame = load_event_product_allocations()
    return frame.copy()


def clear_finance_caches() -> None:
    st.cache_data.clear()
    try:
        create_db_backup_snapshot(reason="autosave", force=False)
    except Exception:
        pass


def finance_actor_name() -> str:
    actor = str(st.session_state.get("invoice_created_by_input", "") or "").strip()
    if actor:
        return actor
    access = str(st.session_state.get(APP_ACCESS_LEVEL_SESSION_KEY, "") or "").strip().title()
    return access or "Owner"


def finance_audit_log(entity_type: str, entity_id: int | None, action_type: str, notes: str) -> None:
    try:
        log_finance_activity(
            entity_type=entity_type,
            entity_id=entity_id,
            action_type=action_type,
            actor_name=finance_actor_name(),
            device_name=current_device_name(),
            notes=notes,
        )
        cached_finance_activity.clear()
    except Exception:
        # Audit should never block user workflow.
        pass


def purge_wattbot_runtime_state() -> None:
    stale_keys = [
        key
        for key in list(st.session_state.keys())
        if key.startswith("wattbot_")
        or key.startswith("profile_wattbot_")
        or key.startswith("inline_command_")
        or key == WATTBOT_HISTORY_KEY
    ]
    for key in stale_keys:
        st.session_state.pop(key, None)


def render_paginated_dataframe(
    source: pd.DataFrame,
    key_prefix: str,
    page_size_default: int = 15,
    page_size_options: tuple[int, ...] = (10, 15, 25, 50, 100),
    hide_index: bool = True,
) -> None:
    frame = source.copy()
    total_rows = int(len(frame))
    if total_rows <= 0:
        st.caption("No rows to display.")
        return

    options = tuple(sorted(set(int(x) for x in page_size_options if int(x) > 0)))
    default_size = page_size_default if page_size_default in options else options[0]
    ctrl1, ctrl2, ctrl3 = st.columns([1.1, 1.1, 2.8])
    page_size = int(
        ctrl1.selectbox(
            "Rows",
            options=list(options),
            index=list(options).index(default_size),
            key=f"{key_prefix}_page_size",
        )
    )
    total_pages = max(1, math.ceil(total_rows / page_size))
    page_index = int(
        ctrl2.number_input(
            "Page",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key=f"{key_prefix}_page_number",
        )
    )
    start = (page_index - 1) * page_size
    end = min(start + page_size, total_rows)
    ctrl3.caption(f"Showing rows {start + 1}-{end} of {total_rows}")
    st.dataframe(frame.iloc[start:end], hide_index=hide_index, use_container_width=True)


def build_finance_summary_pdf(
    title: str,
    subtitle: str,
    kpis: list[tuple[str, str]],
    table_title: str,
    table_df: pd.DataFrame,
    max_rows: int = 18,
) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PDF export requires Pillow.") from exc

    width = 1400
    row_limit = max(1, int(max_rows))
    table_show = table_df.head(row_limit).copy() if isinstance(table_df, pd.DataFrame) else pd.DataFrame()

    line_height = 34
    table_row_height = 30
    top_block = 210
    table_block = 130 + (len(table_show) + 1) * table_row_height
    height = max(950, top_block + table_block + 120)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    def pick_font(size: int, bold: bool = False):
        candidates = [
            "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    f_title = pick_font(46, bold=True)
    f_sub = pick_font(24)
    f_kpi_label = pick_font(21)
    f_kpi_value = pick_font(28, bold=True)
    f_table_header = pick_font(20, bold=True)
    f_table_cell = pick_font(18)

    draw.rectangle((0, 0, width, 14), fill=PRIMARY_COLOR)
    draw.text((38, 30), title, fill="#111827", font=f_title)
    draw.text((40, 92), subtitle, fill="#475569", font=f_sub)

    x = 40
    y = 132
    card_w = int((width - 80 - (max(0, len(kpis) - 1) * 16)) / max(1, len(kpis)))
    for label, value in kpis:
        draw.rounded_rectangle((x, y, x + card_w, y + 88), radius=12, outline="#d1d5db", width=2, fill="#f8fafc")
        draw.text((x + 14, y + 12), str(label), fill="#475569", font=f_kpi_label)
        draw.text((x + 14, y + 42), str(value), fill="#111827", font=f_kpi_value)
        x += card_w + 16

    draw.text((40, 250), table_title, fill="#111827", font=f_table_header)
    if table_show.empty:
        draw.text((40, 290), "No rows available.", fill="#6b7280", font=f_table_cell)
    else:
        cols = list(table_show.columns)
        col_count = max(1, len(cols))
        col_w = int((width - 80) / col_count)
        y_head = 290
        draw.rectangle((40, y_head, width - 40, y_head + 36), fill="#e5eefc")
        for idx, col in enumerate(cols):
            draw.text((48 + idx * col_w, y_head + 8), str(col), fill="#111827", font=f_table_header)
        y_row = y_head + 40
        for _, row in table_show.iterrows():
            for idx, col in enumerate(cols):
                value = str(row[col])
                if len(value) > 26:
                    value = value[:23] + "..."
                draw.text((48 + idx * col_w, y_row), value, fill="#1f2937", font=f_table_cell)
            y_row += table_row_height

    out = io.BytesIO()
    image.save(out, format="PDF", resolution=180.0)
    out.seek(0)
    return out.getvalue()


def _guess_column_name(columns: list[str], keyword_sets: list[tuple[str, ...]]) -> str:
    names = [str(col) for col in columns]
    lowered = {name: name.strip().lower() for name in names}
    for keywords in keyword_sets:
        for name in names:
            token = lowered[name]
            if all(part in token for part in keywords):
                return name
    for keywords in keyword_sets:
        for name in names:
            token = lowered[name]
            if any(part in token for part in keywords):
                return name
    return names[0] if names else ""


def _parse_bank_amount(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return 0.0
    try:
        amount = float(text)
    except Exception:
        return 0.0
    if negative:
        amount = -abs(amount)
    return amount


def _customer_name_tokens(name: str) -> set[str]:
    raw = str(name or "").strip().lower()
    if not raw:
        return set()
    bits = [token for token in re.split(r"[^a-z0-9]+", raw) if token]
    return {token for token in bits if len(token) >= 3}


def run_bank_reconciliation(
    bank_rows: pd.DataFrame,
    invoices: pd.DataFrame,
    date_col: str,
    amount_col: str,
    description_col: str,
    deposits_positive: bool = True,
) -> dict[str, pd.DataFrame]:
    empty_tx_cols = [
        "txn_date",
        "description",
        "amount",
        "matched",
        "confidence",
        "match_score",
        "match_reason",
        "invoice_id",
        "invoice_number",
        "customer_name",
        "invoice_event_date",
        "expected_paid_amount",
        "invoice_revenue",
    ]
    if bank_rows.empty or invoices.empty:
        return {
            "transactions": pd.DataFrame(columns=empty_tx_cols),
            "invoice_gaps": pd.DataFrame(
                columns=[
                    "invoice_id",
                    "invoice_number",
                    "customer_name",
                    "event_date",
                    "expected_paid_amount",
                    "matched_amount",
                    "unreconciled_gap",
                ]
            ),
        }

    tx = bank_rows.copy()
    tx["txn_date"] = pd.to_datetime(tx.get(date_col), errors="coerce")
    tx["amount"] = tx.get(amount_col).apply(_parse_bank_amount)
    tx["description"] = tx.get(description_col).fillna("").astype(str).str.strip()
    tx = tx[tx["txn_date"].notna()].copy()
    tx = tx[tx["amount"].notna()].copy()
    if deposits_positive:
        tx = tx[tx["amount"] > 0].copy()
    else:
        tx = tx[tx["amount"] < 0].copy()
        tx["amount"] = tx["amount"].abs()
    tx = tx.sort_values("txn_date").reset_index(drop=True)
    if tx.empty:
        return {
            "transactions": pd.DataFrame(columns=empty_tx_cols),
            "invoice_gaps": pd.DataFrame(
                columns=[
                    "invoice_id",
                    "invoice_number",
                    "customer_name",
                    "event_date",
                    "expected_paid_amount",
                    "matched_amount",
                    "unreconciled_gap",
                ]
            ),
        }

    inv = invoices.copy()
    inv["id"] = pd.to_numeric(inv["id"], errors="coerce").fillna(0).astype(int)
    inv["invoice_number"] = inv["invoice_number"].fillna("").astype(str).str.strip()
    inv["customer_name"] = inv["customer_name"].fillna("").astype(str).str.strip()
    inv["event_date"] = pd.to_datetime(inv["event_date"], errors="coerce")
    inv["amount_paid"] = pd.to_numeric(inv.get("amount_paid", 0.0), errors="coerce").fillna(0.0)
    inv["revenue"] = pd.to_numeric(inv.get("revenue", 0.0), errors="coerce").fillna(0.0)
    inv["expected_paid_amount"] = np.where(inv["amount_paid"] > 0.0, inv["amount_paid"], 0.0)
    inv["invoice_token"] = inv["invoice_number"].str.lower().str.replace(" ", "", regex=False)
    inv["customer_tokens"] = inv["customer_name"].apply(_customer_name_tokens)
    inv = inv[inv["id"] > 0].copy()
    if inv.empty:
        return {
            "transactions": pd.DataFrame(columns=empty_tx_cols),
            "invoice_gaps": pd.DataFrame(
                columns=[
                    "invoice_id",
                    "invoice_number",
                    "customer_name",
                    "event_date",
                    "expected_paid_amount",
                    "matched_amount",
                    "unreconciled_gap",
                ]
            ),
        }

    out_rows: list[dict[str, object]] = []
    for _, tx_row in tx.iterrows():
        desc = str(tx_row.get("description", "") or "").strip()
        desc_lower = desc.lower().replace(" ", "")
        amount = float(tx_row.get("amount", 0.0) or 0.0)
        tx_date = pd.to_datetime(tx_row.get("txn_date"), errors="coerce")

        best_score = 0.0
        best_conf = "Low"
        best_reason = "No confident invoice match."
        best_invoice: dict[str, object] | None = None

        for _, inv_row in inv.iterrows():
            score = 0.0
            reasons: list[str] = []

            invoice_token = str(inv_row.get("invoice_token", "") or "")
            if invoice_token and invoice_token in desc_lower:
                score += 140.0
                reasons.append("Invoice number found in transaction text.")

            name_tokens = inv_row.get("customer_tokens", set())
            if isinstance(name_tokens, set) and name_tokens:
                hits = sum(1 for token in name_tokens if token in desc_lower)
                if hits > 0:
                    score += min(70.0, 25.0 + (hits * 15.0))
                    reasons.append("Customer name token(s) found.")

            expected_paid = float(inv_row.get("expected_paid_amount", 0.0) or 0.0)
            revenue_amount = float(inv_row.get("revenue", 0.0) or 0.0)
            if expected_paid > 0:
                diff_paid = abs(amount - expected_paid)
                if diff_paid <= 0.01:
                    score += 95.0
                    reasons.append("Exact match to amount paid.")
                elif diff_paid <= 100.0:
                    score += 70.0
                    reasons.append("Near match to amount paid.")
                elif diff_paid <= 500.0:
                    score += 52.0
                    reasons.append("Close match to amount paid.")
            if revenue_amount > 0:
                diff_revenue = abs(amount - revenue_amount)
                if diff_revenue <= 0.01:
                    score += 48.0
                    reasons.append("Exact match to invoice total.")
                elif diff_revenue <= 100.0:
                    score += 32.0
                    reasons.append("Near match to invoice total.")

            event_date = pd.to_datetime(inv_row.get("event_date"), errors="coerce")
            if pd.notna(tx_date) and pd.notna(event_date):
                day_gap = abs((tx_date.normalize() - event_date.normalize()).days)
                if day_gap <= 3:
                    score += 16.0
                    reasons.append("Date proximity <= 3 days.")
                elif day_gap <= 10:
                    score += 10.0
                    reasons.append("Date proximity <= 10 days.")
                elif day_gap <= 30:
                    score += 5.0

            if score > best_score:
                if score >= 170:
                    confidence = "High"
                elif score >= 115:
                    confidence = "Medium"
                else:
                    confidence = "Low"
                best_score = score
                best_conf = confidence
                best_reason = "; ".join(reasons) if reasons else "Weak heuristic match."
                best_invoice = {
                    "id": int(inv_row["id"]),
                    "invoice_number": str(inv_row.get("invoice_number", "") or ""),
                    "customer_name": str(inv_row.get("customer_name", "") or ""),
                    "event_date": inv_row.get("event_date"),
                    "expected_paid_amount": expected_paid,
                    "revenue": revenue_amount,
                }

        matched = bool(best_invoice is not None and best_score >= 115.0)
        out_rows.append(
            {
                "txn_date": tx_date.date().isoformat() if pd.notna(tx_date) else "",
                "description": desc,
                "amount": amount,
                "matched": "Matched" if matched else "Unmatched",
                "confidence": best_conf if matched else "None",
                "match_score": round(float(best_score), 1),
                "match_reason": best_reason,
                "invoice_id": int(best_invoice["id"]) if matched and best_invoice else None,
                "invoice_number": str(best_invoice["invoice_number"]) if matched and best_invoice else "",
                "customer_name": str(best_invoice["customer_name"]) if matched and best_invoice else "",
                "invoice_event_date": (
                    pd.to_datetime(best_invoice["event_date"], errors="coerce").date().isoformat()
                    if matched and best_invoice and pd.notna(pd.to_datetime(best_invoice["event_date"], errors="coerce"))
                    else ""
                ),
                "expected_paid_amount": float(best_invoice["expected_paid_amount"]) if matched and best_invoice else 0.0,
                "invoice_revenue": float(best_invoice["revenue"]) if matched and best_invoice else 0.0,
            }
        )

    tx_out = pd.DataFrame(out_rows)

    matched_map = (
        tx_out[tx_out["invoice_id"].notna()]
        .groupby("invoice_id", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "matched_amount"})
    )
    invoice_gap = inv[
        ["id", "invoice_number", "customer_name", "event_date", "expected_paid_amount"]
    ].rename(columns={"id": "invoice_id"}).copy()
    invoice_gap = invoice_gap.merge(matched_map, on="invoice_id", how="left")
    invoice_gap["matched_amount"] = pd.to_numeric(
        invoice_gap["matched_amount"], errors="coerce"
    ).fillna(0.0)
    invoice_gap["unreconciled_gap"] = (
        invoice_gap["expected_paid_amount"] - invoice_gap["matched_amount"]
    ).round(2)
    invoice_gap["event_date"] = pd.to_datetime(invoice_gap["event_date"], errors="coerce")
    invoice_gap = invoice_gap.sort_values(["event_date", "invoice_number"])

    return {
        "transactions": tx_out,
        "invoice_gaps": invoice_gap,
    }


def render_dashboard_storyboard(
    monthly: pd.DataFrame,
    categories: pd.DataFrame,
    products: pd.DataFrame,
    upcoming: pd.DataFrame,
    reporting_label: str,
) -> None:
    if monthly.empty:
        return

    story = monthly.copy()
    story["month_label"] = story["month_label"].astype(str)
    story = story.sort_values("month")
    story["profit_change"] = story["net_profit_after_adjustments"].diff().fillna(0.0)
    story["cumulative_profit"] = story["net_profit_after_adjustments"].cumsum()
    story["profit_direction"] = story["profit_change"].apply(
        lambda val: "Improved" if val >= 0 else "Dropped"
    )

    best_idx = story["net_profit_after_adjustments"].idxmax()
    worst_idx = story["net_profit_after_adjustments"].idxmin()
    best_row = story.loc[best_idx]
    worst_row = story.loc[worst_idx]
    avg_margin = (
        float(story["net_profit_after_adjustments"].sum()) / float(story["revenue"].sum()) * 100
        if float(story["revenue"].sum()) > 0
        else 0.0
    )
    st.markdown(
        f"""
        <div class="insight-strip">
            <div class="insight-chip"><b>Best Month:</b> {best_row['month_label']} ({money(float(best_row['net_profit_after_adjustments']))})</div>
            <div class="insight-chip"><b>Watch Month:</b> {worst_row['month_label']} ({money(float(worst_row['net_profit_after_adjustments']))})</div>
            <div class="insight-chip"><b>Average Net Margin:</b> {avg_margin:,.1f}%</div>
            <div class="insight-chip"><b>Reporting Window:</b> {reporting_label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([1.35, 1])
    with c1:
        trend_fig = px.area(
            story,
            x="month_label",
            y=["revenue", "total_expenses"],
            title="Revenue vs Expenses Flow",
            labels={"value": "Amount (JMD)", "month_label": "Month", "variable": "Metric"},
        )
        trend_fig.add_scatter(
            x=story["month_label"],
            y=story["net_profit_after_adjustments"],
            mode="lines+markers",
            name="Net Profit (After Adjustments)",
            line={"color": PRIMARY_COLOR, "width": 3, "dash": "dot"},
        )
        style_plotly(trend_fig)
        st.plotly_chart(trend_fig, use_container_width=True)

    with c2:
        accel_fig = px.bar(
            story,
            x="month_label",
            y="profit_change",
            color="profit_direction",
            color_discrete_map={"Improved": "#2EAF7D", "Dropped": "#E05D5D"},
            title="Month-to-Month Profit Change",
            labels={"profit_change": "Profit Delta (JMD)", "month_label": "Month"},
        )
        style_plotly(accel_fig)
        st.plotly_chart(accel_fig, use_container_width=True)

    d1, d2 = st.columns([1.15, 1.15])
    with d1:
        cum_fig = px.line(
            story,
            x="month_label",
            y="cumulative_profit",
            markers=True,
            title="Cumulative Profit Curve",
            labels={"cumulative_profit": "Cumulative Net Profit (JMD)", "month_label": "Month"},
        )
        cum_fig.update_traces(line={"color": PRIMARY_COLOR, "width": 4})
        style_plotly(cum_fig)
        st.plotly_chart(cum_fig, use_container_width=True)

    with d2:
        if categories.empty:
            st.markdown(
                "<div class='hint-card'>Expense category visual appears once expense records are available.</div>",
                unsafe_allow_html=True,
            )
        else:
            tree = px.treemap(
                categories,
                path=["category"],
                values="amount",
                color="amount",
                color_continuous_scale=[SECONDARY_COLOR, PRIMARY_COLOR],
                title="Where Expenses Go",
            )
            style_plotly(tree)
            st.plotly_chart(tree, use_container_width=True)

    e1, e2 = st.columns([1.1, 1.2])
    with e1:
        if products.empty:
            st.markdown(
                "<div class='hint-card'>Product profit bubble chart appears after invoice line items are saved.</div>",
                unsafe_allow_html=True,
            )
        else:
            focus = products.head(24).copy()
            bubble = px.scatter(
                focus,
                x="revenue",
                y="net_profit",
                size="quantity",
                color="margin_pct",
                color_continuous_scale=[SECONDARY_COLOR, PRIMARY_COLOR],
                hover_name="item_name",
                title="Product Profit Bubble Map",
                labels={"revenue": "Revenue (JMD)", "net_profit": "Net Profit (JMD)", "margin_pct": "Margin %"},
            )
            bubble.add_hline(y=0, line_dash="dash", line_color="#6c7385")
            style_plotly(bubble)
            st.plotly_chart(bubble, use_container_width=True)

    with e2:
        if upcoming.empty:
            st.markdown(
                "<div class='hint-card'>Upcoming event load chart appears when future events exist.</div>",
                unsafe_allow_html=True,
            )
        else:
            up = upcoming.copy()
            up["event_date"] = pd.to_datetime(up["event_date"], errors="coerce")
            up = up.dropna(subset=["event_date"])
            up = (
                up.groupby("event_date", as_index=False)
                .agg(event_count=("id", "count"), projected_revenue=("revenue", "sum"))
                .sort_values("event_date")
            )
            up["event_day"] = up["event_date"].dt.strftime("%d %b")
            load_fig = px.bar(
                up,
                x="event_day",
                y="event_count",
                color="projected_revenue",
                color_continuous_scale=[SECONDARY_COLOR, PRIMARY_COLOR],
                title="Upcoming Event Load",
                labels={"event_count": "Events", "event_day": "Date", "projected_revenue": "Projected Revenue"},
            )
            style_plotly(load_fig)
            st.plotly_chart(load_fig, use_container_width=True)


def wattbot_pick_non_repeating(options: list[str], state_key: str) -> str:
    if not options:
        return ""
    previous = str(st.session_state.get(state_key, "")).strip()
    candidate_pool = [line for line in options if line != previous] if len(options) > 1 else list(options)
    chosen = random.choice(candidate_pool or options)
    st.session_state[state_key] = chosen
    return chosen


def wattbot_joke_message() -> str:
    return wattbot_pick_non_repeating(WATTBOT_JOKES, "wattbot_last_joke")


def wattbot_motivation_message() -> str:
    return wattbot_pick_non_repeating(WATTBOT_MOTIVATIONS, "wattbot_last_motivation")


def wattbot_greeting_message() -> str:
    if random.choice([True, False]):
        return wattbot_joke_message()
    return wattbot_motivation_message()


def wattbot_append(role: str, text: str) -> None:
    message = (text or "").strip()
    if not message:
        return
    history = st.session_state.get(WATTBOT_HISTORY_KEY, [])
    history.append({"role": role, "text": message})
    st.session_state[WATTBOT_HISTORY_KEY] = history[-40:]


def wattbot_help_text(available_sections: list[str], finance_unlocked: bool) -> str:
    visible_sections = ", ".join(available_sections)
    finance_note = (
        "Finance insights are currently unlocked."
        if finance_unlocked
        else "Finance insights are locked until Finance Hub is unlocked."
    )
    return (
        f"I can navigate the app, summarize live business data, and chat.\n"
        f"- Available sections: {visible_sections}\n"
        "- Navigation commands: `open today home`, `go to build invoice`, `open supplier re-rental`, `open inventory`, `open finance`, `open client retention`\n"
        "- Operational commands: `inventory status`, `pricing list`, `supplier spend`, `quote vs order`, `ops brief`\n"
        "- Auto quote: `auto quote customer: John, date: 2026-03-21, time: 11am, items: 10x10 tent x2, chairs x60, delivery 10000, setup 5000, gct on`\n"
        "- Conversation commands: `tell me a joke`, `motivate me`, `joke and motivation`, `what should I focus on today`\n"
        "- Finance command: `finance summary` (only after unlock)\n"
        f"- Privacy: {finance_note}"
    )


def set_nav_section(target_section: str, available_sections: list[str]) -> bool:
    section = str(target_section or "").strip()
    if section not in available_sections:
        return False
    st.session_state["nav_pending_section"] = section
    return True


def _query_param_value(key: str) -> str:
    try:
        raw = st.query_params.get(key, "")
    except Exception:
        return ""
    if isinstance(raw, list):
        return str(raw[0] if raw else "").strip()
    return str(raw or "").strip()


def apply_query_section_navigation(available_sections: list[str]) -> None:
    token = _query_param_value("sec").lower()
    if not token:
        return
    token_map = {
        "today": "Today Home",
        "home": "Today Home",
        "finance": "Finance Hub",
        "invoice": "Build Invoice",
        "invoices": "Build Invoice",
        "retention": "Client Retention Automation",
        "deposit": "Deposit Due Tracker",
        "supplier": "Supplier Re-Rental",
        "inventory": "Inventory",
        "mobile": "Mobile & Team",
    }
    target = token_map.get(token, "")
    if target and target in available_sections:
        st.session_state["nav_pending_section"] = target
    try:
        if "sec" in st.query_params:
            del st.query_params["sec"]
    except Exception:
        pass


def render_top_nav_bar(active_section: str) -> None:
    active = str(active_section or "").strip()
    nav_items = [
        ("Today Home", "Today Home"),
        ("Finance Hub", "Finance Hub"),
        ("Build Invoice", "Build Invoice"),
        ("Client Retention", "Client Retention Automation"),
        ("Deposit Tracker", "Deposit Due Tracker"),
        ("Supplier Re-Rental", "Supplier Re-Rental"),
        ("Inventory", "Inventory"),
        ("Mobile & Team", "Mobile & Team"),
    ]
    ordered_sections = [section_name for _, section_name in nav_items]
    section_label_map = {section_name: label for label, section_name in nav_items}
    if active not in ordered_sections:
        active = ordered_sections[0]

    st.markdown("**Top Menu**")
    top_row = nav_items[:5]
    bottom_row = nav_items[5:]

    top_cols = st.columns(len(top_row))
    for idx, ((label, section_name), col) in enumerate(zip(top_row, top_cols)):
        if col.button(
            label,
            key=f"top_nav_btn_row1_{idx}",
            use_container_width=True,
            type="primary" if section_name == active else "secondary",
        ):
            if section_name != active:
                st.session_state["nav_pending_section"] = section_name
                st.rerun()

    bottom_cols = st.columns(len(bottom_row))
    for idx, ((label, section_name), col) in enumerate(zip(bottom_row, bottom_cols)):
        if col.button(
            label,
            key=f"top_nav_btn_row2_{idx}",
            use_container_width=True,
            type="primary" if section_name == active else "secondary",
        ):
            if section_name != active:
                st.session_state["nav_pending_section"] = section_name
                st.rerun()

    st.caption(f"Current: {section_label_map.get(active, active)}")


def render_mobile_bottom_nav(active_section: str) -> None:
    active = str(active_section or "").strip()
    nav_items = [
        ("Home", "today", "Today Home"),
        ("Invoice", "invoices", "Build Invoice"),
        ("Inventory", "inventory", "Inventory"),
        ("Finance", "finance", "Finance Hub"),
        ("More", "mobile", "Mobile & Team"),
    ]
    links = []
    for label, slug, section_name in nav_items:
        active_class = "active" if section_name == active else ""
        links.append(
            "<button type='button' class='hr-nav-link "
            f"{active_class}' data-sec='{html.escape(slug)}'>{html.escape(label)}</button>"
        )
    st.markdown(
        f"""
        <div class="hr-mobile-bottom-nav">
            {''.join(links)}
        </div>
        <div class="hr-mobile-bottom-spacer"></div>
        """,
        unsafe_allow_html=True,
    )


def wattbot_detect_section(prompt_text: str, available_sections: list[str]) -> str | None:
    normalized = (prompt_text or "").strip().lower()
    aliases: list[tuple[str, str]] = [
        ("today home", "Today Home"),
        ("home", "Today Home"),
        ("today", "Today Home"),
        ("start", "Today Home"),
        ("finance", "Finance Hub"),
        ("profit", "Finance Hub"),
        ("dashboard", "Finance Hub"),
        ("build invoice", "Build Invoice"),
        ("invoice builder", "Build Invoice"),
        ("invoice", "Build Invoice"),
        ("quote", "Build Invoice"),
        ("re-rental", "Supplier Re-Rental"),
        ("rerental", "Supplier Re-Rental"),
        ("supplier", "Supplier Re-Rental"),
        ("inventory", "Inventory"),
        ("stock", "Inventory"),
        ("retention", "Client Retention Automation"),
        ("follow-up", "Client Retention Automation"),
        ("followup", "Client Retention Automation"),
        ("deposit", "Deposit Due Tracker"),
        ("balance", "Deposit Due Tracker"),
        ("mobile", "Mobile & Team"),
        ("team", "Mobile & Team"),
    ]
    for token, section in aliases:
        if token in normalized and section in available_sections:
            return section
    return None


def wattbot_inventory_text() -> str:
    stock = cached_inventory_snapshot()
    if stock.empty:
        return "Inventory is empty right now. Add items in Inventory section first."

    low = stock[stock["status"].isin(["Low Stock", "Out of Stock"])].copy()
    live_status = load_inventory_live_status(
        reference_time=pd.Timestamp(jamaica_now().replace(tzinfo=None))
    )
    reserved_now = float(live_status["reserved_now"].sum()) if not live_status.empty else 0.0
    usable_now = float(live_status["usable_now"].sum()) if not live_status.empty else 0.0

    availability = load_inventory_availability_schedule()
    now_local = pd.Timestamp(jamaica_now().replace(tzinfo=None))
    cutoff = now_local + pd.Timedelta(days=30)
    upcoming_shortfalls = 0
    if not availability.empty:
        scoped = availability[
            (availability["start_dt"] >= now_local)
            & (availability["start_dt"] <= cutoff)
            & (availability["shortfall"] > 0)
        ]
        upcoming_shortfalls = int(len(scoped))

    if low.empty:
        low_line = "No low-stock items at this moment."
    else:
        top_low = low.sort_values("current_quantity").head(4)
        low_line = "; ".join(
            f"{row['item_name']} ({float(row['current_quantity']):g} left)"
            for _, row in top_low.iterrows()
        )

    return (
        f"Inventory snapshot: {len(stock)} items tracked.\n"
        f"Reserved now: {reserved_now:,.1f} | Usable now: {usable_now:,.1f}\n"
        f"Projected shortfall lines in next 30 days: {upcoming_shortfalls}\n"
        f"Low-stock watch: {low_line}"
    )


def wattbot_pricing_text() -> str:
    stock = cached_inventory_snapshot()
    if stock.empty:
        return "No inventory pricing list yet. Add inventory items first."
    priced = stock[pd.to_numeric(stock["default_rental_price"], errors="coerce").fillna(0.0) > 0].copy()
    if priced.empty:
        return "Inventory exists, but rental prices are not set yet."
    top = priced.sort_values("default_rental_price", ascending=False).head(5)
    lines = [
        f"{row['item_name']}: {money(float(row['default_rental_price']))}"
        for _, row in top.iterrows()
    ]
    avg = float(priced["default_rental_price"].mean())
    return (
        f"Pricing list coverage: {len(priced)}/{len(stock)} items have rental prices.\n"
        f"Average rental price: {money(avg)}.\n"
        f"Top priced items: {' | '.join(lines)}"
    )


def wattbot_rerental_text() -> str:
    suppliers = load_supplier_expenses()
    if suppliers.empty:
        return "No supplier re-rental expenses have been recorded yet."

    total_spend = float(pd.to_numeric(suppliers["amount"], errors="coerce").fillna(0.0).sum())
    top_rows = suppliers.head(4)
    top_line = " | ".join(
        f"{str(row['vendor']).strip()}: {money(float(row['amount']))}"
        for _, row in top_rows.iterrows()
    )

    monthly = load_supplier_monthly_expenses()
    latest_line = ""
    if not monthly.empty:
        monthly = monthly.copy().sort_values("month")
        latest_month = str(monthly.iloc[-1]["month"])
        latest_label = str(monthly.iloc[-1]["month_label"])
        latest_total = float(
            pd.to_numeric(
                monthly.loc[monthly["month"] == latest_month, "amount"],
                errors="coerce",
            ).fillna(0.0).sum()
        )
        latest_line = f"\nLatest month ({latest_label}) supplier spend: {money(latest_total)}."

    return (
        f"Supplier re-rental total: {money(total_spend)} across {len(suppliers)} suppliers."
        f"{latest_line}\nTop suppliers: {top_line}"
    )


def wattbot_upcoming_events_text(alert_window_days: int) -> str:
    events = build_event_schedule(load_event_calendar())
    if events.empty:
        return "No upcoming or past events are in the calendar yet."

    now_jm = jamaica_now()
    tz_jm = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    upcoming = events[
        events["event_start"].apply(lambda dt: dt.astimezone(tz_jm) >= now_jm)
    ].copy()
    if upcoming.empty:
        return "No upcoming events right now."

    window_end = now_jm + timedelta(days=int(alert_window_days))
    window_rows = upcoming[
        upcoming["event_start"].apply(lambda dt: dt.astimezone(tz_jm) <= window_end)
    ].sort_values("event_start")
    if window_rows.empty:
        return f"No events in the next {alert_window_days} days."

    sample = window_rows.head(4)
    detail = " | ".join(
        f"{row['invoice_number']} on {row['event_start'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')}"
        for _, row in sample.iterrows()
    )
    return (
        f"Upcoming events in next {alert_window_days} days: {len(window_rows)}.\n"
        f"Next events: {detail}"
    )


def wattbot_finance_text(report_start_month: str) -> str:
    monthly = apply_start_month(load_monthly_summary(), report_start_month)
    if monthly.empty:
        return "No finance summary data yet."

    latest = monthly.sort_values("month").iloc[-1]
    year_rows = monthly[monthly["year"] == int(latest["year"])]
    ytd_profit = float(year_rows["net_profit_after_adjustments"].sum()) if not year_rows.empty else 0.0
    return (
        f"Finance pulse ({latest['month_label']}): "
        f"Revenue {money(float(latest['revenue']))}, "
        f"Expenses {money(float(latest['total_expenses']))}, "
        f"Net {money(float(latest['net_profit_after_adjustments']))}.\n"
        f"YTD net after adjustments: {money(ytd_profit)}."
    )


def wattbot_ops_brief_text(report_start_month: str) -> str:
    sections = [
        f"Inventory:\n{wattbot_inventory_text()}",
        f"Supplier Re-Rental:\n{wattbot_rerental_text()}",
    ]
    if can_view_finance_data():
        sections.append(f"Finance:\n{wattbot_finance_text(report_start_month)}")
    else:
        sections.append("Finance:\nFinance is locked. Unlock Finance Hub to include profitability and wage insights.")
    return "Here is your operations brief:\n\n" + "\n\n".join(sections)


def wattbot_focus_text(report_start_month: str) -> str:
    priorities: list[str] = []

    stock = cached_inventory_snapshot()
    if not stock.empty and "status" in stock.columns:
        low_count = int(stock["status"].isin(["Low Stock", "Out of Stock"]).sum())
        if low_count > 0:
            priorities.append(
                f"Inventory: review {low_count} low/out-of-stock item(s) before confirming new rentals."
            )

    suppliers = load_supplier_monthly_expenses()
    if not suppliers.empty:
        scoped = suppliers.copy().sort_values("month")
        latest_label = str(scoped.iloc[-1]["month_label"])
        latest_total = float(pd.to_numeric(scoped.iloc[-1]["amount"], errors="coerce") or 0.0)
        priorities.append(
            f"Supplier spend: latest month ({latest_label}) is {money(latest_total)}. Check top vendor lines for savings."
        )

    if can_view_finance_data():
        monthly = apply_start_month(load_monthly_summary(), report_start_month)
        if not monthly.empty:
            latest = monthly.sort_values("month").iloc[-1]
            net_latest = float(latest["net_profit_after_adjustments"])
            if net_latest < 0:
                priorities.append(
                    f"Profitability: latest month is negative ({money(net_latest)}). Trim variable costs and confirm deposits earlier."
                )
            else:
                priorities.append(
                    f"Profitability: latest month net is {money(net_latest)}. Protect this by prioritizing higher-margin bundles."
                )
    else:
        priorities.append("Finance is locked, so profit-focused priorities are hidden until unlocked.")

    if not priorities:
        priorities = [
            "Keep invoice entry same-day so reporting stays clean.",
            "Update supplier re-rental lines weekly to avoid month-end backlog.",
            "Review inventory rental pricing monthly and adjust stale prices.",
        ]
    return "Focus plan:\n- " + "\n- ".join(priorities[:4])


def wattbot_general_guidance(
    prompt_text: str,
    available_sections: list[str],
    report_start_month: str,
    alert_window_days: int,
) -> str:
    text = (prompt_text or "").strip().lower()
    finance_unlocked = can_view_finance_data()

    if re.search(r"\b(hello|hi|hey|yo)\b", text) or any(
        phrase in text for phrase in ["good morning", "good afternoon", "good evening"]
    ):
        return (
            "I am here and ready. I can chat normally, navigate the app, and run quick business summaries. "
            "Try `ops brief`, `go to inventory`, or ask any question."
        )
    if "how are you" in text or "how you doing" in text:
        return (
            "Running smooth and ready to work with you. "
            "If you want, I can give a joke, motivation, or a full operations brief right now."
        )
    if any(token in text for token in ["thank you", "thanks", "appreciate it"]):
        return "Always. If you want another quick win, ask for `ops brief` or `what should I focus on today`."
    if any(token in text for token in ["bye", "goodnight", "later"]):
        return "Respect. I will be here when you are back."
    if "who are you" in text or "your name" in text:
        return (
            f"I am {WATTBOT_NAME}. I can chat, navigate sections, summarize operations data, and support day-to-day decisions. "
            "Finance details stay protected until Finance Hub is unlocked."
        )

    wants_joke = any(token in text for token in ["joke", "funny", "laugh"])
    wants_motivation = any(token in text for token in ["motivat", "inspire", "encourage"])
    if wants_joke and wants_motivation:
        return f"{wattbot_joke_message()}\n{wattbot_motivation_message()}"
    if wants_joke:
        return wattbot_joke_message()
    if wants_motivation:
        return wattbot_motivation_message()

    if any(
        token in text
        for token in [
            "ops brief",
            "daily brief",
            "status update",
            "summarize app",
            "business summary",
            "overall summary",
            "run down",
            "rundown",
        ]
    ):
        return wattbot_ops_brief_text(report_start_month)

    if any(token in text for token in ["next step", "what should i do", "focus today", "priority", "plan today"]):
        return wattbot_focus_text(report_start_month)

    if any(token in text for token in ["capital", "fund", "loan", "investor"]):
        return (
            "Capital plan: tighten monthly reporting, prove repeatable margins, then choose a channel "
            "(retained earnings, supplier credit, bank line, or partner capital). "
            "Lead with clean statements and a 12-month cashflow forecast."
        )
    if any(token in text for token in ["facebook", "marketplace", "ads", "marketing", "shopify", "tiktok"]):
        return (
            "Growth plan: test 3-5 creatives per offer, track CAC vs gross margin, "
            "retarget viewers, and prioritize bundles with higher average order value."
        )
    if any(token in text for token in ["operations", "process", "team", "staff"]):
        return (
            "Operations focus: standardize invoice templates, enforce same-day data entry, "
            "and review weekly: stock shortfalls, supplier spend, and margin leaks."
        )
    if any(token in text for token in ["calendar", "event", "schedule", "reminder"]):
        return (
            "Calendar/reminder module is currently disabled to keep navigation simple. "
            "I can still help with Build Invoice, Supplier Re-Rental, Inventory, and Finance guidance."
        )

    section_text = ", ".join(available_sections)
    finance_line = "Finance is unlocked." if finance_unlocked else "Finance stays locked until password unlock."
    return (
        "I can still help with this. "
        f"Current sections: {section_text}. {finance_line} "
        f"Try: `help`, `go to build invoice`, `inventory status`, `supplier spend`, `ops brief`, "
        "`auto quote customer: ... items: ...`, `tell me a joke`, `motivate me`, or ask any general question."
    )


def resolve_wattbot_prompt(
    prompt_text: str,
    available_sections: list[str],
    report_start_month: str,
    alert_window_days: int,
) -> tuple[str, bool]:
    text = (prompt_text or "").strip()
    lowered = text.lower()
    if not text:
        return ("Type a command or question and I will help.", False)

    finance_unlocked = can_view_finance_data()
    finance_keywords = (
        "finance",
        "profit",
        "revenue",
        "expense",
        "wage",
        "margin",
        "cashflow",
        "net profit",
    )
    nav_keywords = ("go to", "open", "navigate", "switch to", "take me", "bring me")

    if lowered in {"help", "commands", "menu"} or "what can you do" in lowered:
        return (wattbot_help_text(available_sections, finance_unlocked), False)

    if "lock app" in lowered or "lock finance" in lowered:
        st.session_state[FINANCE_AUTH_SESSION_KEY] = False
        return ("Finance Hub locked for this session.", True)

    if any(token in lowered for token in ["joke and motivation", "both joke and motivation", "joke + motivation"]):
        return (
            f"{wattbot_joke_message()}\n{wattbot_motivation_message()}",
            False,
        )

    if any(token in lowered for token in ["tell me a joke", "make me laugh", "joke", "funny"]):
        if any(token in lowered for token in ["motivat", "inspire", "encourage"]):
            return (
                f"{wattbot_joke_message()}\n{wattbot_motivation_message()}",
                False,
            )
        return (wattbot_joke_message(), False)

    if any(token in lowered for token in ["motivate", "motivation", "inspire me", "encourage me"]):
        return (wattbot_motivation_message(), False)

    if any(token in lowered for token in ["ops brief", "daily brief", "status update", "summarize app"]):
        return (wattbot_ops_brief_text(report_start_month), False)

    if any(
        token in lowered
        for token in [
            "auto quote",
            "quote assistant",
            "draft quote",
            "build quote from",
            "create quote from",
        ]
    ):
        return wattbot_run_auto_quote(text)

    if any(token in lowered for token in ["deposit tracker", "due tracker", "outstanding tracker"]):
        target_section = "Deposit Due Tracker"
        if target_section in available_sections:
            set_nav_section(target_section, available_sections)
            return (f"Opening {target_section}.", True)

    if any(token in lowered for token in ["retention queue", "client retention", "follow-up queue", "followup queue"]):
        target_section = "Client Retention Automation"
        if target_section in available_sections:
            set_nav_section(target_section, available_sections)
            return (f"Opening {target_section}.", True)

    if any(token in lowered for token in ["what should i do", "focus today", "next step", "priorities"]):
        return (wattbot_focus_text(report_start_month), False)

    if any(token in lowered for token in nav_keywords):
        target_section = wattbot_detect_section(lowered, available_sections)
        if target_section:
            if target_section == "Finance Hub" and not finance_unlocked:
                return (
                    "Finance Hub is locked. Unlock with your Finance password first, then ask me again.",
                    False,
                )
            set_nav_section(target_section, available_sections)
            return (f"Opening {target_section}.", True)

    if any(
        token in lowered
        for token in [
            "re-rental summary",
            "rerental summary",
            "supplier spend",
            "supplier expense",
            "supplier expenses",
        ]
    ):
        return (wattbot_rerental_text(), False)

    if any(token in lowered for token in finance_keywords):
        if not finance_unlocked:
            return (
                "Finance data is locked for this session. Unlock Finance Hub to access profitability, wages, and expense insights.",
                False,
            )
        return (wattbot_finance_text(report_start_month), False)

    if any(token in lowered for token in ["inventory", "stock", "shortfall", "availability", "usable"]):
        return (wattbot_inventory_text(), False)

    if any(token in lowered for token in ["price list", "pricing list", "rental price", "pricing"]):
        return (wattbot_pricing_text(), False)

    if any(token in lowered for token in ["calendar", "event", "upcoming", "schedule", "reminder"]):
        return (
            "Calendar and review-reminder modules are currently disabled to keep the app simple. "
            "Use Build Invoice, Finance Hub, Supplier Re-Rental, and Inventory for core workflow.",
            False,
        )

    if (
        "quote vs order" in lowered
        or "quote vs invoice" in lowered
        or ("quote" in lowered and "invoice" in lowered)
        or ("quote" in lowered and "order" in lowered)
    ):
        return (
            "Use `Price Quote` for draft pricing (no system impact). "
            "Use `Confirmed Order` to update inventory and finance.",
            False,
        )

    return (
        wattbot_general_guidance(
            prompt_text=text,
            available_sections=available_sections,
            report_start_month=report_start_month,
            alert_window_days=alert_window_days,
        ),
        False,
    )


def inject_wattbot_widget_css(avatar_uri: str, pulse: bool = True) -> None:
    avatar_bg = f"url('{avatar_uri}')" if avatar_uri else "none"
    label_color = "transparent" if avatar_uri else "var(--text-color)"
    pulse_style = "animation: wattbotPulse 6.8s ease-in-out infinite;" if pulse else ""
    css = f"""
    <style>
        @keyframes wattbotPulse {{
            0% {{
                transform: scale(1);
                box-shadow: 0 10px 24px rgba(0, 0, 0, 0.24);
            }}
            50% {{
                transform: scale(1.03);
                box-shadow: 0 11px 26px rgba(89, 39, 229, 0.22);
            }}
            100% {{
                transform: scale(1);
                box-shadow: 0 10px 24px rgba(0, 0, 0, 0.24);
            }}
        }}
        div[data-testid="stPopover"] {{
            position: fixed;
            right: max(14px, env(safe-area-inset-right));
            left: auto;
            bottom: var(--wattbot-bottom-offset, max(18px, env(safe-area-inset-bottom)));
            z-index: 1200;
        }}
        div[data-testid="stPopover"] > div > button {{
            width: 84px;
            height: 84px;
            min-height: 84px;
            border-radius: 999px;
            padding: 0 !important;
            border: 2px solid var(--border-color) !important;
            background-color: var(--surface-color) !important;
            background-image: {avatar_bg};
            background-size: cover;
            background-position: center;
            box-shadow: 0 8px 22px rgba(0, 0, 0, 0.24);
            {pulse_style}
            color: {label_color} !important;
            cursor: grab !important;
        }}
        @media (prefers-reduced-motion: reduce) {{
            div[data-testid="stPopover"] > div > button {{
                animation: none !important;
            }}
        }}
        div[data-testid="stPopover"] > div > button p {{
            font-size: 0.70rem;
            font-weight: 700;
        }}
        div[data-testid="stPopoverContent"] {{
            width: min(92vw, 390px) !important;
        }}
        @media (max-width: 768px) {{
            div[data-testid="stPopover"] > div > button {{
                width: 76px;
                height: 76px;
                min-height: 76px;
            }}
        }}
    </style>
    <script>
    (() => {{
        if (window.__wattbotDragInit) return;
        window.__wattbotDragInit = true;
        const KEY_X = "wattbot_right_x";
        const KEY_Y = "wattbot_right_y";
        const MARGIN = 8;

        const findWattbotPopover = () => {{
            const popovers = Array.from(document.querySelectorAll('div[data-testid="stPopover"]'));
            if (!popovers.length) return null;
            const preferred = popovers.find((node) => {{
                const btn = node.querySelector("button");
                if (!btn) return false;
                const txt = (btn.innerText || btn.textContent || btn.getAttribute("aria-label") || "").toLowerCase();
                return txt.includes("wattbot");
            }});
            return preferred || popovers[0];
        }};

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

        const init = () => {{
            const popover = findWattbotPopover();
            if (!popover || popover.dataset.dragReady === "1") return;
            popover.dataset.dragReady = "1";
            const button = popover.querySelector("button");
            if (!button) return;

            popover.style.position = "fixed";
            popover.style.zIndex = "1200";
            popover.style.touchAction = "none";

            const applyPosition = (nextLeft, nextTop) => {{
                popover.style.left = nextLeft + "px";
                popover.style.top = nextTop + "px";
                popover.style.right = "auto";
                popover.style.bottom = "auto";
            }};

            const restoreSavedPosition = () => {{
                const savedX = Number(window.localStorage.getItem(KEY_X));
                const savedY = Number(window.localStorage.getItem(KEY_Y));
                if (!Number.isFinite(savedX) || !Number.isFinite(savedY)) return;
                const maxLeft = Math.max(MARGIN, window.innerWidth - popover.offsetWidth - MARGIN);
                const maxTop = Math.max(MARGIN, window.innerHeight - popover.offsetHeight - MARGIN);
                applyPosition(clamp(savedX, MARGIN, maxLeft), clamp(savedY, MARGIN, maxTop));
            }};
            restoreSavedPosition();

            let dragging = false;
            let moved = false;
            let startX = 0;
            let startY = 0;
            let baseLeft = 0;
            let baseTop = 0;

            const beginDrag = (clientX, clientY) => {{
                dragging = true;
                moved = false;
                startX = clientX;
                startY = clientY;
                const rect = popover.getBoundingClientRect();
                baseLeft = rect.left;
                baseTop = rect.top;
                button.style.cursor = "grabbing";
            }};

            const moveDrag = (clientX, clientY) => {{
                if (!dragging) return;
                const dx = clientX - startX;
                const dy = clientY - startY;
                if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
                const maxLeft = Math.max(MARGIN, window.innerWidth - popover.offsetWidth - MARGIN);
                const maxTop = Math.max(MARGIN, window.innerHeight - popover.offsetHeight - MARGIN);
                const nextLeft = clamp(baseLeft + dx, MARGIN, maxLeft);
                const nextTop = clamp(baseTop + dy, MARGIN, maxTop);
                applyPosition(nextLeft, nextTop);
            }};

            const endDrag = () => {{
                if (!dragging) return;
                dragging = false;
                button.style.cursor = "grab";
                const rect = popover.getBoundingClientRect();
                window.localStorage.setItem(KEY_X, String(Math.round(rect.left)));
                window.localStorage.setItem(KEY_Y, String(Math.round(rect.top)));
                if (moved) {{
                    popover.dataset.justDragged = "1";
                    window.setTimeout(() => {{
                        popover.dataset.justDragged = "0";
                    }}, 220);
                }}
            }};

            const onPointerMove = (e) => moveDrag(e.clientX, e.clientY);
            const onPointerUp = () => endDrag();
            const onTouchMove = (e) => {{
                if (!dragging) return;
                const touch = e.touches && e.touches[0];
                if (!touch) return;
                moveDrag(touch.clientX, touch.clientY);
                e.preventDefault();
            }};
            const onTouchEnd = () => endDrag();

            button.addEventListener(
                "pointerdown",
                (e) => {{
                    if (e.button !== undefined && e.button !== 0) return;
                    beginDrag(e.clientX, e.clientY);
                    e.preventDefault();
                }},
                {{ passive: false }},
            );
            window.addEventListener("pointermove", onPointerMove);
            window.addEventListener("pointerup", onPointerUp);
            window.addEventListener("pointercancel", onPointerUp);

            button.addEventListener(
                "touchstart",
                (e) => {{
                    const touch = e.touches && e.touches[0];
                    if (!touch) return;
                    beginDrag(touch.clientX, touch.clientY);
                    e.preventDefault();
                }},
                {{ passive: false }},
            );
            window.addEventListener("touchmove", onTouchMove, {{ passive: false }});
            window.addEventListener("touchend", onTouchEnd);
            window.addEventListener("touchcancel", onTouchEnd);

            const clampToViewport = () => {{
                const rect = popover.getBoundingClientRect();
                if (!rect.width || !rect.height) return;
                const maxLeft = Math.max(MARGIN, window.innerWidth - rect.width - MARGIN);
                const maxTop = Math.max(MARGIN, window.innerHeight - rect.height - MARGIN);
                applyPosition(clamp(rect.left, MARGIN, maxLeft), clamp(rect.top, MARGIN, maxTop));
            }};
            window.addEventListener("resize", clampToViewport);

            button.addEventListener(
                "click",
                (e) => {{
                    if (popover.dataset.justDragged === "1") {{
                        e.preventDefault();
                        e.stopPropagation();
                    }}
                }},
                true,
            );
        }};

        init();
        const obs = new MutationObserver(() => init());
        obs.observe(document.body, {{ childList: true, subtree: true }});
    }})();
    </script>
    """
    st.markdown(css, unsafe_allow_html=True)


def render_wattbot_greeting_popup() -> None:
    greeting = str(st.session_state.get("wattbot_greeting_popup_text", "")).strip()
    started_at = float(st.session_state.get("wattbot_greeting_popup_started_at", 0.0) or 0.0)
    if not greeting or started_at <= 0:
        return

    now_ts = datetime.now().timestamp()
    if (now_ts - started_at) >= 60.0:
        return

    safe_message = html.escape(greeting).replace("\n", "<br/>")
    start_ms = int(started_at * 1000)
    st.markdown(
        f"""
        <style>
            #wattbot-greeting-popup {{
                position: fixed;
                right: max(106px, calc(env(safe-area-inset-right) + 106px));
                bottom: max(18px, calc(env(safe-area-inset-bottom) + 18px));
                z-index: 1188;
                max-width: min(360px, calc(100vw - 120px));
                border: 1px solid var(--border-color);
                border-radius: 12px;
                background: var(--surface-color);
                color: var(--text-color);
                box-shadow: 0 8px 22px rgba(0, 0, 0, 0.22);
                padding: 10px 12px;
                font-size: 0.9rem;
                line-height: 1.35;
            }}
            @media (max-width: 768px) {{
                #wattbot-greeting-popup {{
                    right: max(92px, calc(env(safe-area-inset-right) + 92px));
                    max-width: min(300px, calc(100vw - 104px));
                    font-size: 0.84rem;
                    padding: 9px 10px;
                }}
            }}
        </style>
        <div id="wattbot-greeting-popup"><b>WattBot:</b> {safe_message}</div>
        <script>
            (() => {{
                const node = document.getElementById("wattbot-greeting-popup");
                if (!node) return;
                const navEntry = (performance.getEntriesByType("navigation") || [])[0];
                const navType = navEntry && navEntry.type ? navEntry.type : "navigate";
                const shownKey = "wattbot_popup_seen_once";
                if (navType !== "reload" && window.sessionStorage.getItem(shownKey) === "1") {{
                    node.remove();
                    return;
                }}
                window.sessionStorage.setItem(shownKey, "1");
                const ttlMs = Math.max(0, 60000 - (Date.now() - {start_ms}));
                if (ttlMs <= 0) {{
                    node.remove();
                    return;
                }}
                window.setTimeout(() => {{
                    if (node && node.parentNode) {{
                        node.remove();
                    }}
                }}, ttlMs);
            }})();
        </script>
        """,
        unsafe_allow_html=True,
    )


def render_wattbot_panel(
    available_sections: list[str],
    report_start_month: str,
    alert_window_days: int,
) -> None:
    greeting_key = "wattbot_session_greeting_text"
    if not str(st.session_state.get(greeting_key, "")).strip():
        st.session_state[greeting_key] = wattbot_greeting_message()
    greeting = str(st.session_state.get(greeting_key, "")).strip()
    if WATTBOT_HISTORY_KEY not in st.session_state:
        intro = (
            f"{greeting}\n"
            f"I am {WATTBOT_NAME}. I can help with navigation, operations summaries, and normal conversation."
        )
        st.session_state[WATTBOT_HISTORY_KEY] = [{"role": "assistant", "text": intro}]

    avatar_uri = wattbot_avatar_data_uri()
    inject_wattbot_widget_css(avatar_uri)
    if not bool(st.session_state.get("wattbot_popup_shown_once_this_session", False)):
        st.session_state["wattbot_greeting_popup_text"] = greeting
        st.session_state["wattbot_greeting_popup_started_at"] = datetime.now().timestamp()
        render_wattbot_greeting_popup()
        st.session_state["wattbot_popup_shown_once_this_session"] = True

    with st.popover("WattBot"):
        finance_note = (
            "Finance insights: unlocked."
            if can_view_finance_data()
            else "Finance insights: locked until Finance Hub password unlock."
        )
        c1, c2 = st.columns([0.26, 0.74])
        if avatar_uri:
            c1.markdown(
                f"<img src='{avatar_uri}' width='54' height='54' style='border-radius:50%;object-fit:cover;border:2px solid #a7eaff;'/>",
                unsafe_allow_html=True,
            )
        else:
            c1.markdown("### 🤖")
        c2.markdown(f"**{WATTBOT_NAME}**")
        c2.caption(finance_note)

        history = st.session_state.get(WATTBOT_HISTORY_KEY, [])
        for msg in history[-6:]:
            speaker = "WattBot" if msg.get("role") == "assistant" else "You"
            st.markdown(f"**{speaker}:** {msg.get('text', '')}")

        quick1, quick2, quick3 = st.columns(3)
        quick4, quick5, quick6 = st.columns(3)
        quick7, quick8, quick9 = st.columns(3)
        quick_prompt = ""
        if quick1.button("Build Invoice", key="wattbot_quick_build"):
            quick_prompt = "go to build invoice"
        if quick2.button("Inventory", key="wattbot_quick_inventory"):
            quick_prompt = "inventory status"
        if quick3.button("Re-Rental", key="wattbot_quick_rerental"):
            quick_prompt = "open supplier re-rental"
        if quick4.button("Finance", key="wattbot_quick_finance"):
            quick_prompt = "finance summary"
        if quick5.button("Ops Brief", key="wattbot_quick_ops_brief"):
            quick_prompt = "ops brief"
        if quick6.button("Joke + Motivate", key="wattbot_quick_joke_motivate"):
            quick_prompt = "joke and motivation"
        if quick7.button("Auto Quote", key="wattbot_quick_auto_quote"):
            quick_prompt = "auto quote customer: , date: , time: 11am, items: , delivery 0, setup 0, gct on"
        if quick8.button("Retention Queue", key="wattbot_quick_retention"):
            quick_prompt = "open client retention automation"
        if quick9.button("Deposit Tracker", key="wattbot_quick_deposit"):
            quick_prompt = "open deposit due tracker"

        with st.form("wattbot_command_form", clear_on_submit=True):
            prompt = st.text_input(
                "Ask WattBot",
                placeholder="Try: auto quote customer: John, date: 2026-03-21, items: 10x10 tent x2, chairs x60",
            )
            submitted = st.form_submit_button("Send")

        user_prompt = quick_prompt or (prompt.strip() if submitted else "")
        if user_prompt:
            wattbot_append("user", user_prompt)
            response, needs_rerun = resolve_wattbot_prompt(
                prompt_text=user_prompt,
                available_sections=available_sections,
                report_start_month=report_start_month,
                alert_window_days=alert_window_days,
            )
            wattbot_append("assistant", response)
            if needs_rerun:
                st.rerun()


def render_notification_center(alert_window_days: int, review_link: str) -> None:
    upcoming = upcoming_invoices(days_ahead=alert_window_days)
    if upcoming.empty:
        st.sidebar.success(f"No upcoming events in next {alert_window_days} days.")
    else:
        upcoming = upcoming.copy()
        upcoming["event_date"] = pd.to_datetime(upcoming["event_date"], errors="coerce")
        upcoming["days_left"] = (upcoming["event_date"] - pd.Timestamp.today().normalize()).dt.days
        total = len(upcoming)
        imminent = int((upcoming["days_left"] <= 3).sum())

        st.sidebar.warning(f"{total} upcoming event(s) in next {alert_window_days} days.")
        if imminent > 0:
            st.sidebar.error(f"{imminent} event(s) due within 3 days.")

        with st.sidebar.expander("View Upcoming Notifications", expanded=False):
            view = upcoming.copy()
            view["event_date"] = view["event_date"].dt.date.astype("string")
            st.dataframe(
                view[
                    ["invoice_number", "event_date", "event_time", "event_location", "customer_name", "days_left"]
                ],
                hide_index=True,
                use_container_width=True,
            )

    events = build_event_schedule(load_event_calendar())
    if events.empty:
        return

    sent = load_notification_log()
    sent_pairs = set()
    if not sent.empty:
        sent_pairs = {
            (int(row["invoice_id"]), str(row["notification_type"]).strip().lower())
            for _, row in sent.iterrows()
        }

    now_jm = jamaica_now()
    due_pre_event: list[dict] = []
    due_followup: list[dict] = []
    for _, row in events.iterrows():
        invoice_id = int(row["invoice_id"])
        start_jm = row["event_start"].astimezone(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))
        end_jm = row["event_end"].astimezone(tzinfo_for_name(DEFAULT_EVENT_TIMEZONE))

        reminder_6h_key = "event_reminder_6h"
        reminder_2h_key = "event_reminder_2h"
        followup_key = "post_event_followup"

        six_window_open = start_jm - timedelta(hours=6) <= now_jm < start_jm - timedelta(hours=2)
        two_window_open = start_jm - timedelta(hours=2) <= now_jm < start_jm
        followup_window_open = now_jm >= end_jm + timedelta(hours=1)

        if six_window_open and (invoice_id, reminder_6h_key) not in sent_pairs:
            due_pre_event.append({"type": reminder_6h_key, **row.to_dict()})
        if two_window_open and (invoice_id, reminder_2h_key) not in sent_pairs:
            due_pre_event.append({"type": reminder_2h_key, **row.to_dict()})
        if followup_window_open and (invoice_id, followup_key) not in sent_pairs:
            due_followup.append({"type": followup_key, **row.to_dict()})

    if due_pre_event:
        st.sidebar.markdown("**Event Reminders (Jamaica Time)**")
        for reminder in due_pre_event:
            when_label = "6 hours before event" if reminder["type"] == "event_reminder_6h" else "2 hours before event"
            st.sidebar.warning(
                f"{when_label}: {reminder['invoice_number']} at {reminder['event_time_display']} ({reminder['event_date_display']})"
            )
            st.toast(
                f"{when_label} reminder: {reminder['invoice_number']} ({reminder['event_date_display']} {reminder['event_time_display']})"
            )
            mark_notification_sent(int(reminder["invoice_id"]), reminder["type"])

    if due_followup:
        st.sidebar.markdown("**Post-Event Follow-Up**")
        for follow in due_followup:
            target_contact = (
                str(follow.get("customer_phone", "")).strip()
                or str(follow.get("customer_email", "")).strip()
                or "contact not set"
            )
            st.sidebar.info(
                f"Send thank-you + review request for {follow['invoice_number']} ({follow['customer_name']}) to {target_contact}."
            )
            if st.sidebar.button(
                f"Mark follow-up sent ({follow['invoice_number']})",
                key=f"sidebar_followup_done_{int(follow['invoice_id'])}",
            ):
                mark_notification_sent(int(follow["invoice_id"]), follow["type"])
                st.sidebar.success(f"Follow-up marked sent: {follow['invoice_number']}")
                st.rerun()

        with st.sidebar.expander("Follow-up Message Template", expanded=False):
            review_line = (
                f"If you have a minute, please leave us a review: {review_link.strip()}"
                if review_link.strip()
                else "If you have a minute, we would appreciate your Google review."
            )
            st.code(
                "Hi {customer_name}, thank you for choosing Headline Rentals for your event. "
                f"{review_line}",
                language="text",
            )


def save_invoice(
    form_data: dict,
    raw_items: pd.DataFrame,
    allow_overwrite: bool = False,
    force_quote_unlock: bool = False,
) -> int:
    invoice_number = str(form_data.get("invoice_number", "") or "").strip()
    if not invoice_number:
        raise ValueError("Invoice number is required.")

    normalized_doc_type = (
        str(form_data.get("document_type", "invoice")).strip().lower() or "invoice"
    )
    if normalized_doc_type not in {"quote", "invoice"}:
        normalized_doc_type = "invoice"
    normalized_status = (
        str(form_data.get("order_status", "confirmed")).strip().lower() or "confirmed"
    )
    if normalized_status not in {"pending", "confirmed", "cancelled"}:
        normalized_status = "confirmed"
    if normalized_doc_type == "invoice" and normalized_status == "pending":
        normalized_status = "confirmed"
    if normalized_doc_type == "invoice" and normalized_status == "confirmed":
        event_check = pd.to_datetime(form_data.get("event_date"), errors="coerce")
        if pd.isna(event_check):
            raise ValueError("Event date is required for confirmed order invoices.")

    previous_meta = invoice_meta_by_number(invoice_number)
    if previous_meta is not None and not bool(allow_overwrite):
        existing_doc_type = str(previous_meta.get("document_type", "invoice")).strip().lower()
        existing_status = str(previous_meta.get("order_status", "confirmed")).strip().lower()
        existing_label = (
            "Price Quote"
            if existing_doc_type == "quote"
            else ("Confirmed Order (Cancelled)" if existing_status == "cancelled" else "Confirmed Order")
        )
        raise ValueError(
            f"Invoice number `{invoice_number}` already exists ({existing_label}). "
            "Load/Edit that record or use a new invoice number."
        )
    normalized_payment_status = (
        str(form_data.get("payment_status", "paid_full")).strip().lower() or "paid_full"
    )
    explicit_deposit_flag = form_data.get("deposit_balance_enabled", None)
    if explicit_deposit_flag is None:
        previous_deposit_flag = (
            int(previous_meta.get("deposit_balance_enabled", 0))
            if isinstance(previous_meta, dict)
            else 0
        )
        if normalized_doc_type == "invoice" and normalized_status == "confirmed":
            deposit_balance_enabled = bool(
                normalized_payment_status == "deposit_paid" or previous_deposit_flag
            )
        else:
            deposit_balance_enabled = False
    else:
        deposit_balance_enabled = bool(explicit_deposit_flag)

    invoice_id = upsert_invoice(
        invoice_number=invoice_number,
        event_date=form_data["event_date"].isoformat() if form_data["event_date"] else None,
        event_time=form_data.get("event_time", DEFAULT_EVENT_TIME),
        rental_hours=float(form_data.get("rental_hours", DEFAULT_EVENT_HOURS)),
        event_timezone=form_data.get("event_timezone", DEFAULT_EVENT_TIMEZONE),
        event_location=form_data.get("event_location", ""),
        document_type=normalized_doc_type,
        order_status=normalized_status,
        created_by=form_data.get("created_by", ""),
        source_device=form_data.get("source_device", ""),
        customer_name=form_data["customer_name"],
        customer_phone=form_data.get("customer_phone", ""),
        customer_email=form_data.get("customer_email", ""),
        contact_detail=form_data.get("contact_detail", ""),
        delivered_to=form_data["delivered_to"],
        paid_to=form_data["paid_to"],
        payment_status=form_data.get("payment_status", "paid_full"),
        amount_paid=float(form_data.get("amount_paid", 0.0) or 0.0),
        deposit_balance_enabled=deposit_balance_enabled,
        payment_notes=form_data.get("payment_notes", ""),
        notes=form_data["notes"],
        force_quote_unlock=bool(force_quote_unlock),
    )

    clean_items = raw_items.copy()
    clean_items.columns = [c.strip().lower() for c in clean_items.columns]
    required = {"item_name", "quantity", "unit_price", "unit_cost", "item_type"}
    if not required.issubset(set(clean_items.columns)):
        raise ValueError("Item table is missing required columns.")

    clean_items["item_name"] = clean_items["item_name"].fillna("").astype(str).str.strip()
    clean_items["item_type"] = clean_items["item_type"].fillna("product").astype(str).str.strip()
    for numeric_col in ["quantity", "unit_price", "unit_cost"]:
        clean_items[numeric_col] = pd.to_numeric(clean_items[numeric_col], errors="coerce").fillna(0.0)

    clean_items = clean_items[
        (clean_items["item_name"] != "") & (clean_items["quantity"] > 0)
    ]
    replace_invoice_items(invoice_id=invoice_id, items=clean_items)
    sync_auto_invoice_inventory_movements(
        invoice_id=invoice_id,
        active=(normalized_doc_type == "invoice" and normalized_status == "confirmed"),
    )

    action_type = "created"
    action_note = ""
    if previous_meta is not None:
        prev_doc = str(previous_meta.get("document_type", "invoice")).strip().lower()
        prev_status = str(previous_meta.get("order_status", "confirmed")).strip().lower()
        if prev_doc == "quote" and normalized_doc_type == "invoice":
            action_type = "quote_converted_to_invoice"
            if force_quote_unlock:
                action_note = "Converted quote to confirmed order (unlock confirmed)."
            else:
                action_note = "Converted quote to confirmed order."
        elif prev_status != normalized_status:
            action_type = "status_changed"
            action_note = f"Status changed from {prev_status} to {normalized_status}."
        elif prev_doc != normalized_doc_type:
            action_type = "document_type_changed"
            action_note = f"Document type changed from {prev_doc} to {normalized_doc_type}."
        else:
            action_type = "updated"

    log_invoice_activity(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        action_type=action_type,
        document_type=normalized_doc_type,
        order_status=normalized_status,
        actor_name=form_data.get("created_by", ""),
        device_name=form_data.get("source_device", ""),
        notes=action_note,
    )
    clear_finance_caches()
    # Invoice records are highest priority. Force an immediate snapshot so
    # manual invoices are restorable even if app restarts soon after save.
    try:
        create_db_backup_snapshot(reason="invoice_save", force=True)
    except Exception:
        pass
    return invoice_id


def invoice_label_map() -> dict[str, int | None]:
    invoices = cached_invoice_options(include_quotes=False, confirmed_only=True)
    label_to_id = {"Not linked to invoice": None}
    for _, row in invoices.iterrows():
        date_part = str(row.get("event_date", "") or "").strip()
        if not date_part:
            # Transaction expenses must tie to confirmed orders with an event date.
            continue
        time_part = row["event_time"] if row.get("event_time", "") else ""
        label = (
            f"{row['invoice_number']} | "
            f"{date_part}{' ' + time_part if time_part else ''} | "
            f"{row['customer_name'] if row['customer_name'] else 'No Customer'}"
        )
        label_to_id[label] = int(row["id"])
    return label_to_id


def invoice_choice_map() -> dict[str, int]:
    invoices = cached_invoice_options()
    label_to_id: dict[str, int] = {}
    for _, row in invoices.iterrows():
        doc_type = str(row.get("document_type", "invoice")).strip().lower()
        status = str(row.get("order_status", "confirmed")).strip().lower()
        doc_label = "QUOTE" if doc_type == "quote" else "INVOICE"
        status_label = status.upper()
        label = (
            f"[{doc_label}/{status_label}] {row['invoice_number']} | "
            f"{row['event_date'] if row['event_date'] else 'No Date'} "
            f"{row['event_time'] if row.get('event_time', '') else ''}| "
            f"{row['customer_name'] if row['customer_name'] else 'No Customer'}"
        )
        label_to_id[label] = int(row["id"])
    return label_to_id


def render_invoice_profit_table() -> None:
    st.markdown("**Invoice Profit Table**")
    invoice_level = load_invoice_level()
    if invoice_level.empty:
        st.info("No invoices yet.")
        return

    raw_table = invoice_level.copy().sort_values("event_date", ascending=False)
    table = raw_table.copy()
    table["event_date"] = table["event_date"].dt.date.astype("string")
    table["invoice_total"] = pd.to_numeric(
        table.get("invoice_total", table.get("revenue", 0.0)),
        errors="coerce",
    ).fillna(0.0)
    table["revenue_accounted"] = pd.to_numeric(
        table.get("revenue", 0.0),
        errors="coerce",
    ).fillna(0.0)
    for col in [
        "invoice_total",
        "revenue_accounted",
        "amount_paid",
        "amount_outstanding",
        "invoice_expenses",
        "net_profit",
    ]:
        table[col] = table[col].map(money)
    table["payment_status"] = table["payment_status"].map(
        {
            "unpaid": "UNPAID / COD",
            "deposit_paid": "DEPOSIT PAID",
            "paid_full": "PAID FULL",
        }
    ).fillna("PAID FULL")

    st.dataframe(
        table[
            [
                "invoice_number",
                "event_date",
                "customer_name",
                "invoice_total",
                "revenue_accounted",
                "amount_paid",
                "amount_outstanding",
                "payment_status",
                "invoice_expenses",
                "net_profit",
                "payment_reminder",
            ]
        ].rename(
            columns={
                "invoice_number": "Invoice #",
                "event_date": "Event Date",
                "customer_name": "Customer",
                "invoice_total": "Invoice Total",
                "revenue_accounted": "Revenue Accounted",
                "amount_paid": "Amount Paid",
                "amount_outstanding": "Outstanding",
                "payment_status": "Payment Status",
                "invoice_expenses": "Invoice Expenses",
                "net_profit": "Net Profit",
                "payment_reminder": "Reminder",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )

    pending = raw_table[raw_table["amount_outstanding"] > 0.01].copy()
    st.markdown("**Payment Reminder Queue**")
    if pending.empty:
        st.success("All confirmed orders are fully paid.")
    else:
        st.warning(
            f"{len(pending)} invoice(s) still have outstanding balances. "
            "These can be deposit balances, COD balances, or partial payments."
        )
        pending_show = pending.copy()
        pending_show["event_date"] = pending_show["event_date"].dt.date.astype("string")
        pending_show["invoice_total"] = pd.to_numeric(
            pending_show.get("invoice_total", pending_show.get("revenue", 0.0)),
            errors="coerce",
        ).fillna(0.0).map(money)
        pending_show["revenue_accounted"] = pd.to_numeric(
            pending_show.get("revenue", 0.0),
            errors="coerce",
        ).fillna(0.0).map(money)
        pending_show["amount_paid"] = pending_show["amount_paid"].map(money)
        pending_show["amount_outstanding"] = pending_show["amount_outstanding"].map(money)
        st.dataframe(
            pending_show[
                [
                    "invoice_number",
                    "event_date",
                    "customer_name",
                    "invoice_total",
                    "revenue_accounted",
                    "amount_paid",
                    "amount_outstanding",
                    "payment_reminder",
                ]
            ].rename(
                columns={
                    "invoice_number": "Invoice #",
                    "event_date": "Event Date",
                    "customer_name": "Customer",
                    "invoice_total": "Invoice Total",
                    "revenue_accounted": "Revenue Accounted",
                    "amount_paid": "Amount Paid",
                    "amount_outstanding": "Outstanding",
                    "payment_reminder": "Reminder",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )

        labels = {
            f"{row['invoice_number']} | Balance {money(float(row['amount_outstanding']))} | {row['customer_name']}": int(row["id"])
            for _, row in pending.iterrows()
        }
        with st.form("invoice_payment_update_form", clear_on_submit=True):
            p1, p2 = st.columns(2)
            selected_label = p1.selectbox(
                "Invoice to Update",
                options=list(labels.keys()),
            )
            action = p2.selectbox(
                "Payment Action",
                options=["Mark as Fully Paid", "Record Additional Payment"],
            )
            selected_id = int(labels[selected_label])
            current_row = pending[pending["id"] == selected_id].iloc[0]
            additional_payment = st.number_input(
                "Additional Amount Received (JMD)",
                min_value=0.0,
                step=100.0,
                value=float(current_row["amount_outstanding"]),
                disabled=action != "Record Additional Payment",
            )
            payment_note = st.text_input(
                "Payment Note",
                placeholder="e.g. Balance settled by transfer",
            )
            submit_payment = st.form_submit_button("Update Payment Status")

        if submit_payment:
            current_paid = float(current_row["amount_paid"])
            invoice_total_value = float(current_row.get("invoice_total", current_row["revenue"]))
            if action == "Mark as Fully Paid":
                new_paid = invoice_total_value
            else:
                if float(additional_payment) <= 0:
                    st.error("Additional payment must be greater than 0.")
                    return
                new_paid = min(invoice_total_value, current_paid + float(additional_payment))
            current_status = str(current_row.get("payment_status", "paid_full")).strip().lower()
            if new_paid >= invoice_total_value - 0.01:
                new_status = "paid_full"
            elif current_status == "unpaid":
                new_status = "unpaid"
            else:
                new_status = "deposit_paid"
            set_invoice_payment_status(
                invoice_id=selected_id,
                payment_status=new_status,
                amount_paid=float(new_paid),
                payment_notes=payment_note.strip(),
            )
            finance_audit_log(
                entity_type="invoice_payment",
                entity_id=selected_id,
                action_type="update",
                notes=(
                    f"action={action} | status={new_status} | paid={money(float(new_paid))} | "
                    f"invoice_total={money(float(invoice_total_value))}"
                ),
            )
            clear_finance_caches()
            st.success("Invoice payment status updated.")
            st.rerun()

    st.markdown("---")
    st.markdown("**Manage Invoices (Edit/Delete)**")
    invoice_records = cached_invoice_options()
    if invoice_records.empty:
        st.caption("No invoices available to edit.")
        return

    invoice_manage_labels = {
        (
            f"[{str(row.get('document_type','invoice')).upper()}/"
            f"{str(row.get('order_status','confirmed')).upper()}] "
            f"{row['invoice_number']} | "
            f"{row['event_date'] if row['event_date'] else 'No Date'} "
            f"{row['event_time'] if row.get('event_time', '') else ''} | "
            f"{row['customer_name'] if row['customer_name'] else 'No Customer'}"
        ): int(row["id"])
        for _, row in invoice_records.iterrows()
    }
    selected_manage_label = st.selectbox(
        "Select Invoice",
        options=list(invoice_manage_labels.keys()),
        key="finance_manage_invoice_selector",
    )
    selected_manage_invoice_id = int(invoice_manage_labels[selected_manage_label])

    try:
        invoice_header, invoice_items = invoice_export_bundle(selected_manage_invoice_id)
    except Exception as exc:
        st.error(f"Could not load invoice: {exc}")
        return

    edit_items = invoice_items.copy()
    for col in ["line_total", "unit_cost"]:
        if col in edit_items.columns:
            edit_items = edit_items.drop(columns=[col])
    if edit_items.empty:
        edit_items = pd.DataFrame(
            [
                {
                    "item_name": "",
                    "item_type": "product",
                    "quantity": 1.0,
                    "unit_price": 0.0,
                }
            ]
        )

    event_date_raw = str(invoice_header.get("event_date", "") or "").strip()
    event_date_value = (
        pd.to_datetime(event_date_raw, errors="coerce").date()
        if event_date_raw
        else date.today()
    )
    if pd.isna(pd.to_datetime(event_date_raw, errors="coerce")):
        event_date_value = date.today()

    doc_type_value = str(invoice_header.get("document_type", "invoice") or "invoice").strip().lower()
    status_value = str(invoice_header.get("order_status", "confirmed") or "confirmed").strip().lower()
    payment_status_value = str(
        invoice_header.get("payment_status", "paid_full") or "paid_full"
    ).strip().lower()

    doc_options = ["Price Quote", "Confirmed Order"]
    doc_index = 0 if doc_type_value == "quote" else 1
    status_options = ["Confirmed", "Cancelled"]
    status_lookup = {"confirmed": 0, "cancelled": 1}
    status_index = status_lookup.get(status_value, 0)
    payment_options = ["Cash on Delivery (Unpaid)", "Deposit Paid", "Paid Full"]
    payment_lookup = {"unpaid": 0, "deposit_paid": 1, "paid_full": 2}
    payment_index = payment_lookup.get(payment_status_value, 2)

    with st.form(f"invoice_edit_form_{selected_manage_invoice_id}", clear_on_submit=False):
        i1, i2, i3, i4 = st.columns(4)
        locked_invoice_number = i1.text_input(
            "Invoice Number",
            value=str(invoice_header.get("invoice_number", "") or ""),
            disabled=True,
        )
        edited_doc_label = i2.selectbox(
            "Document Type",
            options=doc_options,
            index=doc_index,
        )
        edited_status_label = i3.selectbox(
            "Order Status",
            options=status_options,
            index=status_index,
        )
        edited_payment_label = i4.selectbox(
            "Payment Status",
            options=payment_options,
            index=payment_index,
        )

        j1, j2, j3 = st.columns(3)
        edited_event_date = j1.date_input("Event Date", value=event_date_value)
        edited_event_time = j2.time_input(
            "Event Time",
            value=time_str_to_time(str(invoice_header.get("event_time", DEFAULT_EVENT_TIME))),
        )
        current_rental_hours = float(
            invoice_header.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS
        )
        default_edit_days = max(1, int(math.ceil(current_rental_hours / 24.0)))
        edited_rental_days = int(
            j3.number_input(
                "Rental Day(s)",
                min_value=1,
                max_value=30,
                step=1,
                value=default_edit_days,
            )
        )
        edited_rental_hours = float(edited_rental_days * 24.0)
        j3.caption(
            f"Stored as {edited_rental_hours:g} hours."
        )

        k1, k2, k3 = st.columns(3)
        edited_customer_name = k1.text_input(
            "Customer Name",
            value=str(invoice_header.get("customer_name", "") or ""),
        )
        edited_customer_phone = k2.text_input(
            "Customer Phone",
            value=str(invoice_header.get("customer_phone", "") or ""),
        )
        edited_customer_email = k3.text_input(
            "Customer Email",
            value=str(invoice_header.get("customer_email", "") or ""),
        )

        l1, l2 = st.columns(2)
        edited_location = l1.text_input(
            "Event Location",
            value=str(invoice_header.get("event_location", "") or ""),
        )
        edited_paid_to = l2.text_input(
            "Paid To",
            value=str(invoice_header.get("paid_to", "") or ""),
        )

        m1, m2 = st.columns(2)
        edited_delivered_to = m1.text_input(
            "Delivered To",
            value=str(invoice_header.get("delivered_to", "") or ""),
        )
        edited_amount_paid = m2.number_input(
            "Amount Paid (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(invoice_header.get("amount_paid", 0.0) or 0.0),
        )
        edited_payment_notes = st.text_input(
            "Payment Notes",
            value=str(invoice_header.get("payment_notes", "") or ""),
        )
        edited_notes = st.text_input(
            "Notes",
            value=str(invoice_header.get("notes", "") or ""),
        )

        st.markdown("**Invoice Items**")
        edited_items_table = st.data_editor(
            edit_items,
            num_rows="dynamic",
            use_container_width=True,
            key=f"finance_invoice_items_editor_{selected_manage_invoice_id}",
            column_config={
                "item_name": st.column_config.TextColumn("Item Name"),
                "item_type": st.column_config.SelectboxColumn(
                    "Type",
                    options=["product", "service"],
                ),
                "quantity": st.column_config.NumberColumn(
                    "Qty",
                    min_value=0.0,
                    step=1.0,
                ),
                "unit_price": st.column_config.NumberColumn(
                    "Unit Price (JMD)",
                    min_value=0.0,
                    step=100.0,
                ),
            },
        )

        save_invoice_edit = st.form_submit_button("Save Invoice Changes")

    if save_invoice_edit:
        try:
            doc_map = {"Price Quote": "quote", "Confirmed Order": "invoice"}
            status_map = {"Confirmed": "confirmed", "Cancelled": "cancelled"}
            payment_map = {
                "Cash on Delivery (Unpaid)": "unpaid",
                "Deposit Paid": "deposit_paid",
                "Paid Full": "paid_full",
            }

            clean_items = normalize_invoice_items_df(edited_items_table)
            clean_items = clean_items[
                (clean_items["item_name"].str.strip() != "")
                & (clean_items["quantity"] > 0)
            ].copy()
            if "line_total" in clean_items.columns:
                clean_items = clean_items.drop(columns=["line_total"])
            clean_items["line_total"] = (
                pd.to_numeric(clean_items.get("quantity", 0.0), errors="coerce").fillna(0.0)
                * pd.to_numeric(clean_items.get("unit_price", 0.0), errors="coerce").fillna(0.0)
            )

            # Rebuild auto-fee rows so GCT/fees always recalculate from current edited prices.
            raw_fee_rows = clean_items[
                clean_items["item_name"].apply(is_auto_fee_item_name)
            ].copy()
            base_items = clean_items[
                ~clean_items["item_name"].apply(is_auto_fee_item_name)
            ].copy()

            base_subtotal_edit = float(pd.to_numeric(base_items["line_total"], errors="coerce").fillna(0.0).sum())
            day_multiplier_amount_edit = round(
                base_subtotal_edit * float(max(0, int(edited_rental_days) - 1)),
                2,
            )

            raw_fee_rows["name_norm"] = raw_fee_rows["item_name"].astype(str).str.strip().str.lower()
            raw_fee_rows["line_total"] = pd.to_numeric(raw_fee_rows["line_total"], errors="coerce").fillna(0.0)

            delivery_amount_edit = float(
                raw_fee_rows.loc[
                    raw_fee_rows["name_norm"].str.contains("delivery", regex=False),
                    "line_total",
                ].sum()
            )
            setup_amount_edit = float(
                raw_fee_rows.loc[
                    raw_fee_rows["name_norm"].str.contains("set-up", regex=False)
                    | raw_fee_rows["name_norm"].str.contains("set up", regex=False)
                    | raw_fee_rows["name_norm"].str.contains("setup", regex=False),
                    "line_total",
                ].sum()
            )
            discount_raw_edit = float(
                raw_fee_rows.loc[
                    raw_fee_rows["name_norm"].str.contains("discount", regex=False),
                    "line_total",
                ].sum()
            )
            discount_amount_edit = abs(discount_raw_edit)
            apply_gct_edit = bool(
                raw_fee_rows["name_norm"].str.contains("gct", regex=False).any()
            )

            pre_discount_total_edit = float(
                base_subtotal_edit + day_multiplier_amount_edit + delivery_amount_edit + setup_amount_edit
            )
            discount_amount_edit = float(
                min(max(discount_amount_edit, 0.0), max(pre_discount_total_edit, 0.0))
            )
            taxable_subtotal_edit = float(max(0.0, pre_discount_total_edit - discount_amount_edit))
            gct_amount_edit = round(taxable_subtotal_edit * 0.15, 2) if apply_gct_edit else 0.0

            auto_fee_rows_edit: list[dict[str, float | str]] = []
            if float(day_multiplier_amount_edit) > 0:
                auto_fee_rows_edit.append(
                    {
                        "item_name": f"Day(s) x{int(max(1, edited_rental_days))}",
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": float(day_multiplier_amount_edit),
                        "unit_cost": 0.0,
                    }
                )
            if float(delivery_amount_edit) > 0:
                auto_fee_rows_edit.append(
                    {
                        "item_name": "Delivery Fee",
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": float(delivery_amount_edit),
                        "unit_cost": 0.0,
                    }
                )
            if float(setup_amount_edit) > 0:
                auto_fee_rows_edit.append(
                    {
                        "item_name": "Set-Up Fee",
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": float(setup_amount_edit),
                        "unit_cost": 0.0,
                    }
                )
            if float(discount_amount_edit) > 0:
                auto_fee_rows_edit.append(
                    {
                        "item_name": "Discount",
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": float(-abs(discount_amount_edit)),
                        "unit_cost": 0.0,
                    }
                )
            if apply_gct_edit and float(gct_amount_edit) > 0:
                auto_fee_rows_edit.append(
                    {
                        "item_name": "GCT (15%)",
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": float(gct_amount_edit),
                        "unit_cost": 0.0,
                    }
                )

            if auto_fee_rows_edit:
                items_to_save = pd.concat(
                    [base_items, pd.DataFrame(auto_fee_rows_edit)],
                    ignore_index=True,
                )
            else:
                items_to_save = base_items.copy()

            items_to_save = items_to_save[
                ["item_name", "item_type", "quantity", "unit_price", "unit_cost"]
            ].copy()
            edited_invoice_total = float(
                (
                    pd.to_numeric(items_to_save.get("quantity", 0.0), errors="coerce").fillna(0.0)
                    * pd.to_numeric(items_to_save.get("unit_price", 0.0), errors="coerce").fillna(0.0)
                ).sum()
            )
            selected_payment_status = payment_map[edited_payment_label]
            amount_paid_for_save = float(max(0.0, float(edited_amount_paid)))
            if selected_payment_status == "paid_full":
                amount_paid_for_save = float(max(0.0, edited_invoice_total))
            elif selected_payment_status == "deposit_paid":
                if amount_paid_for_save <= 0.0:
                    amount_paid_for_save = round(float(max(0.0, edited_invoice_total)) * 0.5, 2)
                amount_paid_for_save = min(amount_paid_for_save, float(max(0.0, edited_invoice_total)))
            else:
                amount_paid_for_save = 0.0

            save_invoice(
                {
                    "invoice_number": str(invoice_header.get("invoice_number", "") or "").strip(),
                    "event_date": edited_event_date,
                    "event_time": to_time_string(edited_event_time),
                    "rental_hours": float(edited_rental_hours),
                    "event_timezone": str(
                        invoice_header.get("event_timezone", DEFAULT_EVENT_TIMEZONE)
                        or DEFAULT_EVENT_TIMEZONE
                    ),
                    "event_location": edited_location,
                    "document_type": doc_map[edited_doc_label],
                    "order_status": status_map[edited_status_label],
                    "created_by": str(invoice_header.get("created_by", "") or ""),
                    "source_device": str(invoice_header.get("source_device", "") or ""),
                    "customer_name": edited_customer_name,
                    "customer_phone": edited_customer_phone,
                    "customer_email": edited_customer_email,
                    "delivered_to": edited_delivered_to,
                    "paid_to": edited_paid_to,
                    "payment_status": selected_payment_status,
                    "amount_paid": amount_paid_for_save,
                    "deposit_balance_enabled": bool(
                        int(float(invoice_header.get("deposit_balance_enabled", 0) or 0))
                    )
                    or (
                        doc_map[edited_doc_label] == "invoice"
                        and status_map[edited_status_label] == "confirmed"
                        and selected_payment_status == "deposit_paid"
                    ),
                    "payment_notes": edited_payment_notes,
                    "notes": edited_notes,
                },
                items_to_save,
                allow_overwrite=True,
            )
            finance_audit_log(
                entity_type="invoice",
                entity_id=selected_manage_invoice_id,
                action_type="update",
                notes=(
                    f"{doc_map[edited_doc_label]}/{status_map[edited_status_label]} | "
                    f"payment={payment_map[edited_payment_label]} | items={len(clean_items)}"
                ),
            )
            clear_finance_caches()
            st.success("Invoice updated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not update invoice: {exc}")

    delete_invoice_confirmed = st.checkbox(
        "I understand this will permanently delete this invoice.",
        key=f"delete_invoice_confirm_{selected_manage_invoice_id}",
    )
    if st.button(
        "Delete Selected Invoice",
        key=f"delete_invoice_btn_{selected_manage_invoice_id}",
        type="secondary",
    ):
        if not delete_invoice_confirmed:
            st.error("Please confirm invoice deletion first.")
        else:
            try:
                deleted = delete_invoice(selected_manage_invoice_id)
                for path_text in deleted.get("attachment_paths", []):
                    path_obj = Path(path_text)
                    if path_obj.exists():
                        try:
                            path_obj.unlink()
                        except Exception:
                            pass
                if st.session_state.get("invoice_last_saved_id") == selected_manage_invoice_id:
                    st.session_state["invoice_last_saved_id"] = None
                finance_audit_log(
                    entity_type="invoice",
                    entity_id=selected_manage_invoice_id,
                    action_type="delete",
                    notes=f"Deleted invoice {deleted.get('invoice_number', '')}",
                )
                clear_finance_caches()
                try:
                    create_db_backup_snapshot(reason="invoice_delete", force=True)
                except Exception:
                    pass
                st.success(f"Invoice deleted: {deleted.get('invoice_number', '')}")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete invoice: {exc}")


def render_finance_hub(
    report_start_month: str,
    alert_window_days: int,
    compact_nav: bool = False,
) -> None:
    render_section_shell(
        "Finance Hub",
        "Dashboard insights, expenses, reports, and invoice profitability controls.",
    )
    autopost_result = run_recurring_template_autopost()
    if int(autopost_result.get("posted_count", 0)) > 0:
        clear_finance_caches()
        st.success(
            f"Auto-created {int(autopost_result.get('posted_count', 0))} recurring draft(s) for {autopost_result.get('month', '')}. "
            "Review and post actual amounts in Expense Transactions."
        )

    finance_views = [
        "Dashboard",
        "Expenses",
        "Inventory Purchases",
        "Reports",
        "Invoice Profit",
    ]
    if compact_nav:
        active_view = st.selectbox(
            "Finance Area",
            options=finance_views,
            index=0,
            key="finance_hub_area_mobile",
        )
    else:
        active_view = st.radio(
            "Finance Area",
            options=finance_views,
            horizontal=True,
            key="finance_hub_area_desktop",
        )

    if active_view == "Dashboard":
        render_dashboard(
            report_start_month=report_start_month,
            alert_window_days=alert_window_days,
        )
    elif active_view == "Expenses":
        render_expenses()
    elif active_view == "Inventory Purchases":
        render_inventory_purchases()
    elif active_view == "Reports":
        render_reports(report_start_month=report_start_month)
    else:
        render_invoice_profit_table()


def render_owner_monthly_adjustment_panel() -> None:
    st.markdown("---")
    with st.expander("Add Monthly Adjustment (Owner Finance Adjustment)", expanded=False):
        st.caption(
            "Use this for owner-level finance adjustments only. Inventory purchases should be logged in Inventory Purchases."
        )
        with st.form("adjustment_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            month_value = c1.date_input("Month", value=date.today().replace(day=1))
            adjustment_type = c2.text_input("Adjustment Type", value="Owner Adjustment")
            adjustment_amount = c3.number_input(
                "Adjustment Amount (JMD)", min_value=0.0, step=500.0
            )
            adjustment_description = st.text_input("Adjustment Description")
            submit_adjustment = st.form_submit_button("Add Adjustment")

        if submit_adjustment:
            if adjustment_amount <= 0:
                st.error("Adjustment amount must be greater than 0.")
            else:
                try:
                    add_monthly_adjustment(
                        month=f"{month_value.year:04d}-{month_value.month:02d}",
                        adjustment_type=adjustment_type.strip() or "Adjustment",
                        amount=float(adjustment_amount),
                        description=adjustment_description.strip(),
                    )
                    finance_audit_log(
                        entity_type="monthly_adjustment",
                        entity_id=None,
                        action_type="create",
                        notes=(
                            f"{month_value.year:04d}-{month_value.month:02d} | "
                            f"{adjustment_type.strip() or 'Adjustment'} | {money(float(adjustment_amount))}"
                        ),
                    )
                    clear_finance_caches()
                    st.success("Monthly adjustment recorded.")
                except Exception as exc:
                    st.error(f"Could not add adjustment: {exc}")


def render_finance_danger_zone() -> None:
    st.markdown("---")
    with st.expander("Owner Danger Zone: Reset All Records", expanded=False):
        st.warning(
            "Permanent action: this clears invoices, invoice items, expenses, monthly adjustments, "
            "inventory purchases, inventory, inventory movements, notification log, build log, and invoice attachments."
        )
        st.caption(
            "Finance password and app profile settings are preserved. "
            "Use the backup download first if you might need to restore."
        )

        if DB_PATH.exists():
            backup_name = f"finance_hub_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            st.download_button(
                "Download Backup (.db)",
                data=DB_PATH.read_bytes(),
                file_name=backup_name,
                mime="application/octet-stream",
                key="download_db_backup_before_reset_btn",
            )

        with st.form("finance_reset_all_records_form", clear_on_submit=True):
            confirm_phrase = st.text_input(
                "Type `RESET ALL DATA` to confirm",
                placeholder="RESET ALL DATA",
            )
            confirm_password = st.text_input(
                "Finance Password",
                type="password",
            )
            acknowledged = st.checkbox("I understand this action cannot be undone.")
            submit_reset = st.form_submit_button("Reset All Records")

        if submit_reset:
            if confirm_phrase.strip().upper() != "RESET ALL DATA":
                st.error("Confirmation text does not match.")
                return
            if not acknowledged:
                st.error("Please confirm that you understand this action is permanent.")
                return
            if not verify_finance_password(confirm_password):
                st.error("Incorrect Finance Password.")
                return

            try:
                result = purge_all_records(preserve_settings=True)
                for path_text in result.get("attachment_paths", []):
                    path_obj = Path(str(path_text or "").strip())
                    if not path_obj.exists():
                        continue
                    try:
                        if ATTACHMENTS_DIR in path_obj.parents:
                            path_obj.unlink()
                    except Exception:
                        pass

                st.session_state["invoice_last_saved_id"] = None
                st.session_state["invoice_export_selected_id"] = None
                st.session_state["invoice_parse_warnings"] = []
                st.session_state["invoice_parse_detected_total"] = 0.0
                st.session_state["invoice_parse_calculated_total"] = 0.0
                st.session_state["invoice_items_editor_data"] = pd.DataFrame(
                    [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
                )
                st.session_state["invoice_items_editor_seed"] = int(
                    st.session_state.get("invoice_items_editor_seed", 0)
                ) + 1
                st.session_state["invoice_number_input"] = ""
                st.cache_data.clear()

                deleted_counts = result.get("deleted_counts", {})
                total_deleted = int(sum(int(v) for v in deleted_counts.values()))
                st.success(
                    f"All operational records cleared. Rows deleted: {total_deleted}."
                )
                st.caption(
                    "Settings were preserved, including Finance Hub password."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Could not reset records: {exc}")


def render_finance_hub_section(
    report_start_month: str,
    alert_window_days: int,
    compact_nav: bool = False,
) -> None:
    if not finance_password_enabled():
        render_section_shell(
            "Finance Hub Setup",
            "Set a password to protect wages, profit, expenses, and reports.",
        )
        st.warning("Set a Finance Hub password to protect wages, profit, expenses, and reports.")
        with st.form("finance_first_password_form", clear_on_submit=True):
            new_pw = st.text_input("Create Finance Password", type="password")
            confirm_pw = st.text_input("Confirm Finance Password", type="password")
            setup_submit = st.form_submit_button("Set Password and Unlock Finance Hub")
        if setup_submit:
            if len((new_pw or "").strip()) < 6:
                st.error("Password must be at least 6 characters.")
            elif new_pw != confirm_pw:
                st.error("Passwords do not match.")
            else:
                set_finance_password(new_pw)
                st.session_state[FINANCE_AUTH_SESSION_KEY] = True
                st.success("Finance Hub password set and unlocked.")
                st.rerun()
        return

    if st.session_state.get(FINANCE_AUTH_SESSION_KEY, False):
        lock_col = st.columns([1, 4])
        if lock_col[0].button("Lock Finance Hub", key="finance_lock_now_btn"):
            st.session_state[FINANCE_AUTH_SESSION_KEY] = False
            st.rerun()
        render_finance_hub(
            report_start_month=report_start_month,
            alert_window_days=alert_window_days,
            compact_nav=compact_nav,
        )
        render_owner_monthly_adjustment_panel()
        render_finance_danger_zone()
        return

    render_section_shell(
        "Finance Hub Locked",
        "Enter Finance Hub password to access profitability, expenses, wages, and reports.",
    )
    st.warning("Enter Finance Hub password to access profitability, expenses, wages, and reports.")
    with st.form("finance_unlock_form", clear_on_submit=False):
        unlock_password = st.text_input("Finance Password", type="password")
        unlock_submit = st.form_submit_button("Unlock Finance Hub")
    if unlock_submit:
        if verify_finance_password(unlock_password):
            st.session_state[FINANCE_AUTH_SESSION_KEY] = True
            st.success("Finance Hub unlocked for this session.")
            st.rerun()
        else:
            st.error("Incorrect Finance Hub password.")


def render_dashboard(report_start_month: str, alert_window_days: int) -> None:
    render_section_shell(
        "Dashboard",
        "Trend view with alerts, month-close checks, and actionable priorities.",
    )
    experience = current_experience_mode()

    monthly_all = apply_start_month(cached_monthly_summary(), report_start_month)
    invoice_level_all = apply_start_month(cached_invoice_level(), report_start_month)
    products = cached_product_profitability()
    expenses_all = apply_start_month(cached_expenses(), report_start_month)
    daily_all = apply_start_month(cached_daily_summary(), report_start_month)
    if not expenses_all.empty:
        expenses_all = expenses_all[
            expenses_all["expense_kind"].str.lower() != "summary_rollup"
        ]
        expenses_all = expenses_all[~inventory_purchase_mask(expenses_all["category"])]

    current_month_token = jamaica_now().strftime("%Y-%m")

    def _collect_month_tokens(*frames: pd.DataFrame) -> list[str]:
        out: set[str] = set()
        for frame in frames:
            if frame.empty or "month" not in frame.columns:
                continue
            series = frame["month"].dropna().astype(str).str.strip()
            for token in series:
                if not re.fullmatch(r"\d{4}-\d{2}", token or ""):
                    continue
                try:
                    pd.Period(token, freq="M")
                    out.add(token)
                except Exception:
                    continue
        return sorted(out, reverse=True)

    available_month_tokens = _collect_month_tokens(monthly_all, invoice_level_all, expenses_all)
    if current_month_token not in available_month_tokens:
        available_month_tokens.append(current_month_token)
    available_month_tokens = sorted(set(available_month_tokens), reverse=True)
    month_label_map: dict[str, str] = {}
    for token in available_month_tokens:
        try:
            month_label_map[token] = pd.Period(token, freq="M").strftime("%b %Y")
        except Exception:
            continue
    available_years = sorted(
        {
            int(token[:4])
            for token in available_month_tokens
            if re.fullmatch(r"\d{4}-\d{2}", token)
        },
        reverse=True,
    )
    if not available_years:
        available_years = [int(current_month_token[:4])]

    scope_col1, scope_col2 = st.columns([1.35, 1.65])
    dashboard_scope_mode = scope_col1.selectbox(
        "Dashboard Data Window",
        options=[
            "Current Month",
            "Specific Day",
            "Specific Week",
            "Specific Month",
            "Specific Year",
            "Custom Range",
            "All Data",
        ],
        index=0,
        key="dashboard_scope_mode_selector",
        help="Choose exactly which period to view in Dashboard metrics/charts.",
    )
    selected_scope_month = current_month_token
    selected_scope_year = int(current_month_token[:4])
    selected_scope_day = jamaica_now().date()
    current_month_date = jamaica_now().date().replace(day=1)
    next_month_date = (current_month_date + timedelta(days=32)).replace(day=1)
    current_month_end = next_month_date - timedelta(days=1)
    range_start: date | None = None
    range_end: date | None = None

    def _normalize_date_range(raw_value: object, fallback_start: date, fallback_end: date) -> tuple[date, date]:
        start_val = fallback_start
        end_val = fallback_end
        if isinstance(raw_value, (list, tuple)) and len(raw_value) == 2:
            if isinstance(raw_value[0], date):
                start_val = raw_value[0]
            if isinstance(raw_value[1], date):
                end_val = raw_value[1]
        elif isinstance(raw_value, date):
            start_val = raw_value
            end_val = raw_value
        if end_val < start_val:
            start_val, end_val = end_val, start_val
        return start_val, end_val

    if dashboard_scope_mode == "Current Month":
        range_start = current_month_date
        range_end = current_month_end
        dashboard_scope_label = (
            f"Current Month ({month_label_map.get(current_month_token, current_month_token)})"
        )
        kpi_prefix = "Current Month"
        scope_col2.caption("Showing all days in the current month.")
    elif dashboard_scope_mode == "Specific Day":
        selected_scope_day = scope_col2.date_input(
            "Select Day",
            value=jamaica_now().date(),
            key="dashboard_scope_day_selector",
        )
        range_start = selected_scope_day
        range_end = selected_scope_day
        dashboard_scope_label = f"Selected Day ({selected_scope_day.isoformat()})"
        kpi_prefix = "Selected Day"
    elif dashboard_scope_mode == "Specific Week":
        today_jm = jamaica_now().date()
        week_start_default = today_jm - timedelta(days=today_jm.weekday())
        week_end_default = week_start_default + timedelta(days=6)
        selected_week_range = scope_col2.date_input(
            "Select Week (Date Range)",
            value=(week_start_default, week_end_default),
            key="dashboard_scope_week_range_selector",
        )
        range_start, range_end = _normalize_date_range(
            selected_week_range,
            week_start_default,
            week_end_default,
        )
        dashboard_scope_label = (
            f"Selected Week ({range_start.isoformat()} to {range_end.isoformat()})"
        )
        kpi_prefix = "Selected Week"
    elif dashboard_scope_mode == "Specific Month":
        month_pick_left, month_pick_right = scope_col2.columns([1, 1])
        default_year_index = (
            available_years.index(int(current_month_token[:4]))
            if int(current_month_token[:4]) in available_years
            else 0
        )
        selected_scope_year = int(
            month_pick_left.selectbox(
                "Year",
                options=available_years,
                index=default_year_index,
                key="dashboard_scope_month_year_selector",
            )
        )
        month_options = list(range(1, 13))
        selected_month_num = int(
            month_pick_right.selectbox(
                "Month",
                options=month_options,
                index=int(current_month_token[5:7]) - 1,
                format_func=lambda m: datetime(2000, int(m), 1).strftime("%b"),
                key="dashboard_scope_month_only_selector",
            )
        )
        range_start = date(selected_scope_year, selected_month_num, 1)
        range_end = (range_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        selected_scope_month = f"{selected_scope_year:04d}-{selected_month_num:02d}"
        dashboard_scope_label = f"Selected Month ({month_label_map.get(selected_scope_month, selected_scope_month)})"
        kpi_prefix = "Selected Month"
    elif dashboard_scope_mode == "Specific Year":
        default_year_index = (
            available_years.index(int(current_month_token[:4]))
            if int(current_month_token[:4]) in available_years
            else 0
        )
        selected_scope_year = int(
            scope_col2.selectbox(
                "Select Year",
                options=available_years,
                index=default_year_index,
                key="dashboard_scope_year_selector",
            )
        )
        range_start = date(selected_scope_year, 1, 1)
        range_end = date(selected_scope_year, 12, 31)
        dashboard_scope_label = f"Selected Year ({selected_scope_year})"
        kpi_prefix = "Selected Year"
    elif dashboard_scope_mode == "Custom Range":
        default_custom_start = jamaica_now().date() - timedelta(days=6)
        default_custom_end = jamaica_now().date()
        custom_range = scope_col2.date_input(
            "Select Date Range",
            value=(default_custom_start, default_custom_end),
            key="dashboard_scope_custom_range_selector",
        )
        range_start, range_end = _normalize_date_range(
            custom_range,
            default_custom_start,
            default_custom_end,
        )
        dashboard_scope_label = (
            f"Custom Range ({range_start.isoformat()} to {range_end.isoformat()})"
        )
        kpi_prefix = "Custom Range"
    else:
        dashboard_scope_label = "All Data"
        kpi_prefix = "All Data"
        scope_col2.caption("Showing all available data.")

    st.caption(f"Dashboard view: {dashboard_scope_label}")

    def _filter_monthly_window(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        if range_start is None and range_end is None:
            return frame.copy()
        if "month" not in frame.columns:
            return frame.copy()
        out = frame.copy()
        month_series = out["month"].fillna("").astype(str).str.strip()
        valid_mask = month_series.str.match(r"^\d{4}-\d{2}$")
        out = out[valid_mask].copy()
        month_series = out["month"].fillna("").astype(str).str.strip()
        period_index = pd.PeriodIndex(month_series, freq="M")
        if range_start is not None:
            out = out[period_index >= pd.Period(range_start.strftime("%Y-%m"), freq="M")]
            month_series = out["month"].fillna("").astype(str).str.strip()
            period_index = pd.PeriodIndex(month_series, freq="M")
        if range_end is not None:
            out = out[period_index <= pd.Period(range_end.strftime("%Y-%m"), freq="M")]
        return out.copy()

    def _filter_by_date_window(frame: pd.DataFrame, date_col: str) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()
        if range_start is None and range_end is None:
            return frame.copy()
        if date_col not in frame.columns:
            return frame.copy()
        out = frame.copy()
        dt_series = pd.to_datetime(out[date_col], errors="coerce")
        mask = dt_series.notna()
        if range_start is not None:
            mask = mask & (dt_series.dt.date >= range_start)
        if range_end is not None:
            mask = mask & (dt_series.dt.date <= range_end)
        return out[mask].copy()

    monthly = _filter_monthly_window(monthly_all)
    invoice_level = _filter_by_date_window(invoice_level_all, "event_date")
    expenses_view = _filter_by_date_window(
        expenses_all,
        "finance_date" if "finance_date" in expenses_all.columns else "expense_date",
    )
    daily_view = _filter_by_date_window(daily_all, "day")

    categories = (
        expenses_view.groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
        if not expenses_view.empty
        else pd.DataFrame(columns=["category", "amount"])
    )
    upcoming = upcoming_invoices(days_ahead=alert_window_days)
    if not upcoming.empty:
        upcoming_event_dates = pd.to_datetime(upcoming["event_date"], errors="coerce")
        if range_start is not None:
            upcoming = upcoming[upcoming_event_dates.dt.date >= range_start].copy()
            upcoming_event_dates = pd.to_datetime(upcoming["event_date"], errors="coerce")
        if range_end is not None:
            upcoming = upcoming[upcoming_event_dates.dt.date <= range_end].copy()

    is_granular_window = dashboard_scope_mode in {"Specific Day", "Specific Week", "Custom Range"}
    kpi_source = daily_view if is_granular_window else monthly

    if kpi_source.empty:
        if dashboard_scope_mode in {"Current Month", "Specific Month"}:
            month_token = (
                current_month_token
                if dashboard_scope_mode == "Current Month"
                else selected_scope_month
            )
            st.info(
                f"No finance data for {month_label_map.get(month_token, month_token)} yet. "
                "Add confirmed orders/expenses for this period."
            )
        elif dashboard_scope_mode == "Specific Year":
            st.info(
                f"No finance data for {selected_scope_year} yet. Add confirmed orders/expenses for that year."
            )
        elif dashboard_scope_mode == "Specific Day":
            st.info("No finance data for the selected day yet.")
        elif dashboard_scope_mode in {"Specific Week", "Custom Range"}:
            st.info("No finance data for the selected date range yet.")
        else:
            st.info("No data yet. Add invoices or expenses to get started.")
    else:
        if is_granular_window:
            revenue_trend = pd.to_numeric(daily_view.get("revenue", 0.0), errors="coerce").fillna(0.0).tolist()
            expense_trend = pd.to_numeric(
                daily_view.get("total_expenses", 0.0),
                errors="coerce",
            ).fillna(0.0).tolist()
            net_trend = pd.to_numeric(daily_view.get("net_profit", 0.0), errors="coerce").fillna(0.0).tolist()
            after_adjustment_trend = net_trend
            cash_collected_trend = pd.to_numeric(
                daily_view.get("cash_collected", 0.0),
                errors="coerce",
            ).fillna(0.0).tolist()
            outstanding_trend = pd.to_numeric(
                daily_view.get("outstanding_receivables", 0.0),
                errors="coerce",
            ).fillna(0.0).tolist()
            total_revenue = float(pd.to_numeric(daily_view.get("revenue", 0.0), errors="coerce").fillna(0.0).sum())
            total_expenses = float(
                pd.to_numeric(daily_view.get("total_expenses", 0.0), errors="coerce").fillna(0.0).sum()
            )
            total_net_profit = float(
                pd.to_numeric(daily_view.get("net_profit", 0.0), errors="coerce").fillna(0.0).sum()
            )
            total_after_adjustments = total_net_profit
            total_cash_collected = float(
                pd.to_numeric(daily_view.get("cash_collected", 0.0), errors="coerce").fillna(0.0).sum()
            )
            total_outstanding = float(
                pd.to_numeric(daily_view.get("outstanding_receivables", 0.0), errors="coerce").fillna(0.0).sum()
            )
        else:
            revenue_trend = pd.to_numeric(monthly["revenue"], errors="coerce").fillna(0.0).tolist()
            expense_trend = pd.to_numeric(monthly["total_expenses"], errors="coerce").fillna(0.0).tolist()
            net_trend = pd.to_numeric(monthly["net_profit"], errors="coerce").fillna(0.0).tolist()
            after_adjustment_trend = pd.to_numeric(
                monthly["net_profit_after_adjustments"], errors="coerce"
            ).fillna(0.0).tolist()
            cash_collected_trend = (
                pd.to_numeric(monthly["cash_collected"], errors="coerce").fillna(0.0).tolist()
                if "cash_collected" in monthly.columns
                else [0.0] * len(monthly)
            )
            outstanding_trend = (
                pd.to_numeric(monthly["outstanding_receivables"], errors="coerce").fillna(0.0).tolist()
                if "outstanding_receivables" in monthly.columns
                else [0.0] * len(monthly)
            )
            total_revenue = float(pd.to_numeric(monthly["revenue"], errors="coerce").fillna(0.0).sum())
            total_expenses = float(pd.to_numeric(monthly["total_expenses"], errors="coerce").fillna(0.0).sum())
            total_net_profit = float(pd.to_numeric(monthly["net_profit"], errors="coerce").fillna(0.0).sum())
            total_after_adjustments = float(
                pd.to_numeric(monthly["net_profit_after_adjustments"], errors="coerce").fillna(0.0).sum()
            )
            total_cash_collected = float(
                pd.to_numeric(monthly.get("cash_collected", 0.0), errors="coerce").fillna(0.0).sum()
            )
            total_outstanding = float(
                pd.to_numeric(monthly.get("outstanding_receivables", 0.0), errors="coerce").fillna(0.0).sum()
            )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_kpi(
                f"{kpi_prefix} Revenue (Accounted)",
                money(total_revenue),
                trend_values=revenue_trend,
            )
        with c2:
            render_kpi(
                f"{kpi_prefix} Expenses",
                money(total_expenses),
                trend_values=expense_trend,
            )
        with c3:
            render_kpi(
                f"{kpi_prefix} Net Profit",
                money(total_net_profit),
                trend_values=net_trend,
            )
        with c4:
            if is_granular_window:
                render_kpi(
                    f"{kpi_prefix} Confirmed Orders",
                    f"{int(len(invoice_level)):,}",
                )
            else:
                render_kpi(
                    f"{kpi_prefix} After Adjustments",
                    money(total_after_adjustments),
                    trend_values=after_adjustment_trend,
                )

        p1, p2 = st.columns(2)
        with p1:
            render_kpi(
                f"{kpi_prefix} Cash Collected",
                money(total_cash_collected),
                trend_values=cash_collected_trend,
            )
        with p2:
            render_kpi(
                f"{kpi_prefix} Outstanding",
                money(total_outstanding),
                trend_values=outstanding_trend,
            )

        if is_granular_window:
            trend = daily_view.copy()
            trend["day"] = pd.to_datetime(trend.get("day"), errors="coerce")
            trend = trend.sort_values("day")
            trend["day_label"] = trend["day"].dt.strftime("%Y-%m-%d")
            trend_plot = trend[
                ["day_label", "revenue", "total_expenses", "net_profit"]
            ].rename(
                columns={
                    "revenue": "Revenue (Accounted)",
                    "total_expenses": "Expenses",
                    "net_profit": "Net Profit",
                }
            )
            for col in ["Revenue (Accounted)", "Expenses", "Net Profit"]:
                trend_plot[col] = pd.to_numeric(trend_plot[col], errors="coerce").fillna(0.0)
            fig = px.line(
                trend_plot,
                x="day_label",
                y=["Revenue (Accounted)", "Expenses", "Net Profit"],
                markers=True,
                labels={
                    "value": "Amount (JMD)",
                    "day_label": "Date",
                    "variable": "Metric",
                },
                title=f"Daily Financial Trend ({dashboard_scope_label})",
            )
        else:
            trend = monthly[
                ["month_label", "revenue", "total_expenses", "net_profit_after_adjustments"]
            ].rename(
                columns={
                    "revenue": "Revenue (Accounted)",
                    "total_expenses": "Expenses",
                    "net_profit_after_adjustments": "Net Profit (After Adjustments)",
                }
            )
            for col in ["Revenue (Accounted)", "Expenses", "Net Profit (After Adjustments)"]:
                trend[col] = pd.to_numeric(trend[col], errors="coerce").fillna(0.0)
            fig = px.line(
                trend,
                x="month_label",
                y=["Revenue (Accounted)", "Expenses", "Net Profit (After Adjustments)"],
                markers=True,
                labels={
                    "value": "Amount (JMD)",
                    "month_label": "Month",
                    "variable": "Metric",
                },
                title=f"Monthly Financial Trend ({dashboard_scope_label})",
            )
        style_plotly(fig)
        st.plotly_chart(fig, use_container_width=True)

        if not invoice_level.empty:
            outstanding = invoice_level[invoice_level["amount_outstanding"] > 0.01].copy()
            if not outstanding.empty:
                due_days_rule = int(
                    float(get_setting("finance.deposit_due_days_before_event", "3") or 3)
                )
                today_jm = pd.Timestamp(jamaica_now().date())
                outstanding["due_date"] = pd.to_datetime(
                    outstanding["event_date"], errors="coerce"
                ) - pd.to_timedelta(due_days_rule, unit="D")
                outstanding["days_overdue"] = (
                    today_jm - outstanding["due_date"].dt.normalize()
                ).dt.days
                outstanding["days_overdue"] = outstanding["days_overdue"].fillna(0).astype(int)
                outstanding["is_overdue"] = outstanding["days_overdue"] > 0
                overdue = outstanding[outstanding["is_overdue"]].copy()

                if not overdue.empty:
                    st.error(
                        f"Red Flag: {len(overdue)} overdue invoice balance(s) totaling {money(float(overdue['amount_outstanding'].sum()))}."
                    )
                else:
                    st.warning(
                        f"Payment reminder: {len(outstanding)} confirmed order(s) still have outstanding balances."
                    )

                age_days = outstanding["days_overdue"].clip(lower=0)
                age_buckets = pd.cut(
                    age_days,
                    bins=[-1, 7, 14, 30, float("inf")],
                    labels=["0-7 days", "8-14 days", "15-30 days", "30+ days"],
                )
                aging = (
                    outstanding.assign(aging_bucket=age_buckets)
                    .groupby("aging_bucket", observed=False, as_index=False)
                    .agg(
                        invoice_count=("id", "count"),
                        outstanding_balance=("amount_outstanding", "sum"),
                    )
                )
                if not aging.empty:
                    aging["outstanding_balance_display"] = aging["outstanding_balance"].map(money)
                    st.markdown("**Aging Buckets (Unpaid Balances)**")
                    st.dataframe(
                        aging[["aging_bucket", "invoice_count", "outstanding_balance_display"]].rename(
                            columns={
                                "aging_bucket": "Bucket",
                                "invoice_count": "Invoices",
                                "outstanding_balance_display": "Outstanding Balance",
                            }
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )

                outstanding_show = outstanding.copy().sort_values(
                    ["is_overdue", "due_date", "event_date"], ascending=[False, True, True]
                )
                outstanding_show["event_date"] = outstanding_show["event_date"].dt.date.astype("string")
                outstanding_show["due_date"] = outstanding_show["due_date"].dt.date.astype("string")
                outstanding_show["status"] = outstanding_show["is_overdue"].map(
                    {True: "Overdue", False: "Not Yet Due"}
                )
                outstanding_show["invoice_total"] = pd.to_numeric(
                    outstanding_show.get("invoice_total", outstanding_show.get("revenue", 0.0)),
                    errors="coerce",
                ).fillna(0.0).map(money)
                outstanding_show["revenue"] = pd.to_numeric(
                    outstanding_show.get("revenue", 0.0),
                    errors="coerce",
                ).fillna(0.0).map(money)
                outstanding_show["amount_paid"] = outstanding_show["amount_paid"].map(money)
                outstanding_show["amount_outstanding"] = outstanding_show["amount_outstanding"].map(money)
                render_paginated_dataframe(
                    outstanding_show[
                        [
                            "invoice_number",
                            "event_date",
                            "due_date",
                            "days_overdue",
                            "status",
                            "customer_name",
                            "invoice_total",
                            "revenue",
                            "amount_paid",
                            "amount_outstanding",
                        ]
                    ].rename(
                        columns={
                            "invoice_number": "Invoice #",
                            "event_date": "Event Date",
                            "due_date": "Due Date",
                            "days_overdue": "Days Overdue",
                            "status": "Status",
                            "customer_name": "Customer",
                            "invoice_total": "Invoice Total",
                            "revenue": "Revenue Accounted",
                            "amount_paid": "Amount Paid",
                            "amount_outstanding": "Outstanding",
                        }
                    ),
                    key_prefix="dashboard_outstanding",
                    page_size_default=10,
                )

    st.markdown("**Month Close Checklist**")
    if monthly_all.empty:
        st.caption("No monthly rows available for checklist.")
    else:
        month_options = monthly_all["month"].dropna().astype(str).drop_duplicates().tolist()
        month_options = sorted(month_options, reverse=True)
        selected_close_month = st.selectbox(
            "Month to Close",
            options=month_options,
            key="dashboard_month_close_selector",
        )
        close_month_label = pd.Period(selected_close_month, freq="M").strftime("%b %Y")

        close_expenses = expenses_all[expenses_all["month"] == selected_close_month].copy()
        close_invoices = invoice_level_all[invoice_level_all["month"] == selected_close_month].copy()
        supplier_close = close_expenses[
            (close_expenses["category"].fillna("").str.lower() == "re-rental")
            & (close_expenses["expense_kind"].fillna("").str.lower() == "transaction")
        ].copy()

        supplier_linked_ok = bool(
            supplier_close.empty or supplier_close["invoice_id"].notna().all()
        )
        wages_posted = bool(
            (close_expenses["category"].fillna("").str.lower() == "wages").any()
        )
        recurring_posted = bool(
            (close_expenses["expense_kind"].fillna("").str.lower() == "recurring_monthly").any()
        )
        outstanding_close = close_invoices[
            close_invoices["amount_outstanding"] > 0.01
        ].copy()
        balance_review_key = f"finance.month_close.{selected_close_month}.balances_reviewed"
        balances_reviewed = str(get_setting(balance_review_key, "0")).strip() == "1"

        close_rows = [
            {
                "Check": "Supplier re-rentals linked",
                "Status": "Done" if supplier_linked_ok else "Action Needed",
                "Detail": (
                    "All supplier re-rentals are linked to invoices."
                    if supplier_linked_ok
                    else f"{int(supplier_close['invoice_id'].isna().sum())} supplier rows are not linked."
                ),
            },
            {
                "Check": "Wages posted",
                "Status": "Done" if wages_posted else "Action Needed",
                "Detail": (
                    "Wages entries exist for this month."
                    if wages_posted
                    else "No wages entries found for this month."
                ),
            },
            {
                "Check": "Recurring monthly expenses posted",
                "Status": "Done" if recurring_posted else "Action Needed",
                "Detail": (
                    "Recurring expenses exist for this month."
                    if recurring_posted
                    else "No recurring monthly expense entries found."
                ),
            },
            {
                "Check": "Outstanding balances reviewed",
                "Status": "Done" if balances_reviewed else "Pending Review",
                "Detail": (
                    f"Outstanding count: {len(outstanding_close)} | Balance: {money(float(outstanding_close['amount_outstanding'].sum()) if not outstanding_close.empty else 0.0)}"
                ),
            },
        ]
        close_df = pd.DataFrame(close_rows)
        done_count = int((close_df["Status"] == "Done").sum())
        c1, c2 = st.columns([1, 2])
        c1.metric("Checklist Completion", f"{done_count}/{len(close_df)}")
        c2.caption(f"Close month: {close_month_label}")
        st.dataframe(close_df, hide_index=True, use_container_width=True)
        toggle_label = (
            "Mark Balances Reviewed"
            if not balances_reviewed
            else "Unmark Balances Reviewed"
        )
        if st.button(toggle_label, key="month_close_balance_review_toggle_btn"):
            new_value = "0" if balances_reviewed else "1"
            set_setting(balance_review_key, new_value)
            finance_audit_log(
                entity_type="month_close",
                entity_id=None,
                action_type="balances_reviewed_toggle",
                notes=f"{selected_close_month}: balances_reviewed={new_value}",
            )
            st.rerun()

    if experience != "Data Dense" and not is_granular_window:
        st.markdown("**Visual Storyboard**")
        st.caption(
            "A chart-first view designed for quick understanding and easier pattern spotting."
        )
        render_dashboard_storyboard(
            monthly=monthly,
            categories=categories,
            products=products,
            upcoming=upcoming,
            reporting_label=dashboard_scope_label,
        )
    elif experience != "Data Dense":
        st.caption("Storyboard is shown for month/year/all views.")

    st.markdown(f"**Upcoming Events (Next {alert_window_days} Days)**")
    if range_start is not None or range_end is not None:
        st.caption(
            "Upcoming events list is still limited to forward-looking events. "
            "Dashboard metrics above are filtered by your selected day/week/month/year/range."
        )
    if upcoming.empty:
        st.caption(f"No upcoming events in the next {alert_window_days} days.")
    else:
        soon = pd.to_datetime(upcoming["event_date"], errors="coerce")
        imminent_count = int((soon - pd.Timestamp.today().normalize()).dt.days.le(3).sum())
        if imminent_count > 0:
            st.warning(f"{imminent_count} event(s) are due within 3 days.")
        upcoming_view = upcoming.copy()
        upcoming_view["event_date"] = pd.to_datetime(
            upcoming_view["event_date"], errors="coerce"
        ).dt.date.astype("string")
        upcoming_view["revenue"] = upcoming_view["revenue"].map(money)
        render_paginated_dataframe(
            upcoming_view[
                [
                    "invoice_number",
                    "event_date",
                    "event_time",
                    "event_location",
                    "customer_name",
                    "revenue",
                ]
            ],
            key_prefix="dashboard_upcoming",
            page_size_default=10,
        )

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("**Top Product Profitability**")
        if dashboard_scope_mode != "All Data":
            st.caption(
                "Top Product Profitability currently shows all-time confirmed-order performance."
            )
        if products.empty:
            st.caption("No invoice items yet.")
        else:
            display = products.copy()
            display["revenue"] = display["revenue"].map(money)
            display["direct_cost"] = display["direct_cost"].map(money)
            display["allocated_expenses"] = display["allocated_expenses"].map(money)
            display["net_profit"] = display["net_profit"].map(money)
            display["margin_pct"] = display["margin_pct"].map(lambda x: f"{x:,.1f}%")
            render_paginated_dataframe(
                display,
                key_prefix="dashboard_top_product_profit",
                page_size_default=10,
            )

    with right:
        st.markdown("**Expense Category Split**")
        if categories.empty:
            st.caption("No expense records yet.")
        else:
            donut = px.pie(
                categories,
                names="category",
                values="amount",
                hole=0.55,
                title=f"Expense Breakdown ({reporting_window_label(report_start_month)})",
            )
            style_plotly(donut)
            st.plotly_chart(donut, use_container_width=True)


def render_invoices() -> None:
    render_section_shell(
        "Build Invoice",
        "Create quotes/orders, review impact, then save/download/send in one flow.",
    )

    history = cached_invoice_options()
    default_actor = ""
    detected_device = current_device_name()
    inventory = cached_inventory_snapshot()
    inventory_templates = {
        str(row["item_name"]): {
            "unit_price": float(row.get("default_rental_price", 0.0) or 0.0),
            "category": str(row["category"]),
        }
        for _, row in inventory.iterrows()
    } if not inventory.empty else {}
    in_stock_inventory_suggestions: list[str] = []
    if not inventory.empty:
        inv_suggest = inventory.copy()
        inv_suggest["item_name"] = inv_suggest["item_name"].astype(str).str.strip()
        inv_suggest = inv_suggest[inv_suggest["item_name"] != ""].copy()
        if "current_quantity" in inv_suggest.columns:
            inv_suggest["current_quantity"] = pd.to_numeric(
                inv_suggest["current_quantity"], errors="coerce"
            ).fillna(0.0)
        else:
            inv_suggest["current_quantity"] = 0.0
        if "active" in inv_suggest.columns:
            inv_suggest["active"] = pd.to_numeric(inv_suggest["active"], errors="coerce").fillna(1.0)
            active_mask = inv_suggest["active"] > 0
        else:
            active_mask = pd.Series([True] * len(inv_suggest), index=inv_suggest.index)
        inv_suggest = inv_suggest[active_mask & (inv_suggest["current_quantity"] > 0)].copy()
        in_stock_inventory_suggestions = sorted(
            {str(name).strip() for name in inv_suggest["item_name"].tolist() if str(name).strip()}
        )

    default_items_template = pd.DataFrame(
        [
            {
                "item_name": "10x10 Tent",
                "item_type": "product",
                "quantity": 1,
                "unit_price": 0.0,
            }
        ]
    )
    default_setup_effective_rate = effective_setup_hourly_rate()
    state_defaults = {
        "invoice_number_input": "",
        "invoice_event_date_input": date.today(),
        "invoice_event_time_input": time(11, 0),
        "invoice_rental_hours_input": 24.0,
        "invoice_rental_days_input": 1,
        "invoice_event_location_input": "",
        "invoice_customer_name_input": "",
        "invoice_customer_phone_input": "",
        "invoice_customer_email_input": "",
        "invoice_document_type_selector": "Price Quote",
        "invoice_created_by_input": default_actor,
        "invoice_delivered_to_input": "",
        "invoice_paid_to_input": "",
        "invoice_notes_input": "",
        "invoice_apply_gct_input": True,
        "invoice_delivery_manual_amount_input": 0.0,
        "invoice_setup_fee_input": 0.0,
        "invoice_auto_delivery_round_trips_input": 2,
        "invoice_auto_delivery_trip_days_input": 2,
        "invoice_auto_delivery_billing_mode_input": "Hourly Rate",
        "invoice_auto_delivery_billing_mode_manually_set": False,
        "invoice_auto_delivery_trip_days_manually_set": False,
        "invoice_auto_delivery_destination_manually_set": False,
        "invoice_auto_delivery_one_way_km": 0.0,
        "invoice_auto_delivery_one_way_min": 0.0,
        "invoice_auto_delivery_free_flow_min": 0.0,
        "invoice_auto_delivery_collection_min": 0.0,
        "invoice_auto_delivery_collection_free_flow_min": 0.0,
        "invoice_auto_delivery_collection_departure_caption": "",
        "invoice_auto_delivery_route_error": "",
        "invoice_auto_setup_hourly_rate_input": float(default_setup_effective_rate),
        "invoice_discount_mode_input": "No Discount",
        "invoice_discount_percent_input": 0.0,
        "invoice_discount_amount_input": 0.0,
        "invoice_real_payment_terms_input": "Paid In Full",
        "invoice_additional_payment_note_input": "",
        "invoice_items_editor_seed": 0,
        "invoice_items_editor_data": default_items_template.copy(),
        "invoice_parse_warnings": [],
        "invoice_parse_detected_total": 0.0,
        "invoice_parse_calculated_total": 0.0,
        "invoice_pending_parsed_payload": None,
        "invoice_pending_saved_quote_id": None,
        "invoice_export_selected_id": None,
        "invoice_last_saved_id": None,
        "invoice_quick_actions_ready_id": None,
        "invoice_quick_payload": None,
        "invoice_quick_file_stub": "",
        "invoice_quick_pdf_bytes": b"",
        "invoice_quick_png_bytes": b"",
        "invoice_builder_allow_overwrite": False,
        "invoice_builder_loaded_invoice_number": "",
        "invoice_post_save_messages": [],
    }
    for key, default in state_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default

    pending_post_save_messages = st.session_state.get("invoice_post_save_messages", [])
    if isinstance(pending_post_save_messages, list) and pending_post_save_messages:
        for entry in pending_post_save_messages:
            if not isinstance(entry, dict):
                continue
            msg_text = str(entry.get("text", "")).strip()
            if not msg_text:
                continue
            msg_level = str(entry.get("level", "info")).strip().lower()
            if msg_level == "success":
                st.success(msg_text)
            elif msg_level == "warning":
                st.warning(msg_text)
            elif msg_level == "error":
                st.error(msg_text)
            else:
                st.info(msg_text)
        st.session_state["invoice_post_save_messages"] = []

    next_confirmed_number = next_confirmed_invoice_number(history)
    current_build_choice = str(
        st.session_state.get("invoice_document_type_selector", "Price Quote") or "Price Quote"
    ).strip()
    if current_build_choice == "Confirmed Order":
        current_invoice_number = str(
            st.session_state.get("invoice_number_input", "") or ""
        ).strip()
        if not current_invoice_number:
            st.session_state["invoice_number_input"] = next_confirmed_number

    def _apply_parsed_invoice_to_builder(parsed: dict) -> None:
        st.session_state["invoice_number_input"] = parsed.get("invoice_number", "")
        st.session_state["invoice_event_date_input"] = parsed.get("event_date") or date.today()
        st.session_state["invoice_event_time_input"] = time_str_to_time(
            str(parsed.get("event_time", DEFAULT_EVENT_TIME))
        )
        parsed_hours = float(parsed.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS)
        parsed_days = max(1, int(math.ceil(parsed_hours / 24.0)))
        st.session_state["invoice_rental_days_input"] = parsed_days
        st.session_state["invoice_rental_hours_input"] = float(parsed_days * 24.0)
        st.session_state["invoice_event_location_input"] = (
            parsed.get("event_location", "")
            or parsed.get("delivered_to", "")
            or ""
        )
        st.session_state["invoice_customer_name_input"] = parsed.get("customer_name", "")
        st.session_state["invoice_customer_phone_input"] = parsed.get("customer_phone", "")
        st.session_state["invoice_customer_email_input"] = parsed.get("customer_email", "")
        st.session_state["invoice_delivered_to_input"] = parsed.get("delivered_to", "")
        st.session_state["invoice_paid_to_input"] = parsed.get("paid_to", "")
        st.session_state["invoice_notes_input"] = parsed.get("notes", "")

        parsed_items = parsed.get("items")
        if isinstance(parsed_items, pd.DataFrame) and not parsed_items.empty:
            parsed_clean = parsed_items.copy()
            if "unit_cost" in parsed_clean.columns:
                parsed_clean = parsed_clean.drop(columns=["unit_cost"])
            st.session_state["invoice_items_editor_data"] = parsed_clean
        else:
            st.session_state["invoice_items_editor_data"] = default_items_template.copy()
        st.session_state["invoice_items_editor_seed"] += 1

        st.session_state["invoice_parse_warnings"] = parsed.get("warnings", [])
        st.session_state["invoice_parse_detected_total"] = float(
            parsed.get("detected_total", 0.0)
        )
        st.session_state["invoice_parse_calculated_total"] = float(
            parsed.get("calculated_total", 0.0)
        )
        st.session_state["invoice_builder_allow_overwrite"] = False
        st.session_state["invoice_builder_loaded_invoice_number"] = ""

    pending_parsed_payload = st.session_state.get("invoice_pending_parsed_payload")
    if isinstance(pending_parsed_payload, dict):
        _apply_parsed_invoice_to_builder(pending_parsed_payload)
        st.session_state["invoice_pending_parsed_payload"] = None

    reset_cols = st.columns([1.1, 2.2, 1.4])
    reset_cols[0].markdown("**Invoice Builder**")
    if reset_cols[2].button("Start New Invoice from Scratch", key="invoice_reset_new_blank"):
        st.session_state["invoice_number_input"] = ""
        st.session_state["invoice_event_date_input"] = date.today()
        st.session_state["invoice_event_time_input"] = time(11, 0)
        st.session_state["invoice_rental_hours_input"] = 24.0
        st.session_state["invoice_rental_days_input"] = 1
        st.session_state["invoice_event_location_input"] = ""
        st.session_state["invoice_customer_name_input"] = ""
        st.session_state["invoice_customer_phone_input"] = ""
        st.session_state["invoice_customer_email_input"] = ""
        st.session_state["invoice_document_type_selector"] = "Price Quote"
        st.session_state["invoice_delivered_to_input"] = ""
        st.session_state["invoice_paid_to_input"] = ""
        st.session_state["invoice_notes_input"] = ""
        st.session_state["invoice_apply_gct_input"] = True
        st.session_state["invoice_delivery_manual_amount_input"] = 0.0
        st.session_state["invoice_setup_fee_input"] = 0.0
        st.session_state["invoice_auto_delivery_round_trips_input"] = 2
        st.session_state["invoice_auto_delivery_trip_days_input"] = 2
        st.session_state["invoice_auto_delivery_billing_mode_input"] = "Hourly Rate"
        st.session_state["invoice_auto_delivery_billing_mode_manually_set"] = False
        st.session_state["invoice_auto_delivery_trip_days_manually_set"] = False
        st.session_state["invoice_auto_delivery_destination_manually_set"] = False
        st.session_state["invoice_auto_delivery_one_way_km"] = 0.0
        st.session_state["invoice_auto_delivery_one_way_min"] = 0.0
        st.session_state["invoice_auto_delivery_free_flow_min"] = 0.0
        st.session_state["invoice_auto_delivery_collection_min"] = 0.0
        st.session_state["invoice_auto_delivery_collection_free_flow_min"] = 0.0
        st.session_state["invoice_auto_delivery_collection_departure_caption"] = ""
        st.session_state["invoice_auto_delivery_route_error"] = ""
        st.session_state["invoice_auto_setup_hourly_rate_input"] = float(
            effective_setup_hourly_rate()
        )
        st.session_state["invoice_discount_mode_input"] = "No Discount"
        st.session_state["invoice_discount_percent_input"] = 0.0
        st.session_state["invoice_discount_amount_input"] = 0.0
        st.session_state["invoice_real_payment_terms_input"] = "Paid In Full"
        st.session_state["invoice_additional_payment_note_input"] = ""
        st.session_state["invoice_items_editor_data"] = default_items_template.copy()
        st.session_state["invoice_items_editor_seed"] += 1
        st.session_state["invoice_parse_warnings"] = []
        st.session_state["invoice_parse_detected_total"] = 0.0
        st.session_state["invoice_parse_calculated_total"] = 0.0
        st.session_state["invoice_pending_parsed_payload"] = None
        st.session_state["invoice_pending_saved_quote_id"] = None
        st.session_state["invoice_last_saved_id"] = None
        st.session_state["invoice_quick_actions_ready_id"] = None
        st.session_state["invoice_quick_payload"] = None
        st.session_state["invoice_quick_file_stub"] = ""
        st.session_state["invoice_quick_pdf_bytes"] = b""
        st.session_state["invoice_quick_png_bytes"] = b""
        st.session_state["invoice_builder_allow_overwrite"] = False
        st.session_state["invoice_builder_loaded_invoice_number"] = ""
        st.toast("New blank invoice ready.")
        st.rerun()

    def _load_saved_invoice_into_builder(
        invoice_header: dict,
        invoice_items: pd.DataFrame,
        build_as: str = "Price Quote",
    ) -> None:
        event_date_raw = pd.to_datetime(
            str(invoice_header.get("event_date", "") or ""),
            errors="coerce",
        )
        event_date_value = (
            event_date_raw.date() if not pd.isna(event_date_raw) else date.today()
        )
        rental_hours_value = float(
            invoice_header.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS
        )
        rental_days_value = max(1, int(math.ceil(rental_hours_value / 24.0)))
        normalized_items = normalize_invoice_items_df(invoice_items)
        base_items = remove_auto_fee_rows(normalized_items)
        if base_items.empty:
            base_items = default_items_template.copy()
        else:
            base_items = base_items[["item_name", "item_type", "quantity", "unit_price"]].copy()

        st.session_state["invoice_number_input"] = str(
            invoice_header.get("invoice_number", "") or ""
        ).strip()
        st.session_state["invoice_event_date_input"] = event_date_value
        st.session_state["invoice_event_time_input"] = time_str_to_time(
            str(invoice_header.get("event_time", DEFAULT_EVENT_TIME))
        )
        st.session_state["invoice_rental_days_input"] = rental_days_value
        st.session_state["invoice_rental_hours_input"] = float(rental_days_value * 24.0)
        st.session_state["invoice_event_location_input"] = str(
            invoice_header.get("event_location", "")
            or invoice_header.get("delivered_to", "")
            or ""
        ).strip()
        st.session_state["invoice_customer_name_input"] = str(
            invoice_header.get("customer_name", "") or ""
        ).strip()
        st.session_state["invoice_customer_phone_input"] = str(
            invoice_header.get("customer_phone", "") or ""
        ).strip()
        st.session_state["invoice_customer_email_input"] = str(
            invoice_header.get("customer_email", "") or ""
        ).strip()
        st.session_state["invoice_created_by_input"] = str(
            invoice_header.get("created_by", "") or ""
        ).strip()
        st.session_state["invoice_delivered_to_input"] = str(
            invoice_header.get("delivered_to", "") or ""
        ).strip()
        st.session_state["invoice_paid_to_input"] = str(
            invoice_header.get("paid_to", "") or ""
        ).strip()
        st.session_state["invoice_notes_input"] = str(invoice_header.get("notes", "") or "").strip()

        delivery_amount_saved = float(
            normalized_items.loc[
                normalized_items["item_name"].str.strip().str.lower() == "delivery fee",
                "line_total",
            ].sum()
        )
        setup_amount_saved = float(
            normalized_items.loc[
                normalized_items["item_name"].str.strip().str.lower() == "set-up fee",
                "line_total",
            ].sum()
        )
        gct_amount_saved = float(
            normalized_items.loc[
                normalized_items["item_name"].str.strip().str.lower() == "gct (15%)",
                "line_total",
            ].sum()
        )
        discount_amount_saved = float(
            normalized_items.loc[
                normalized_items["item_name"].str.strip().str.lower() == "discount",
                "line_total",
            ].sum()
        )

        st.session_state["invoice_delivery_manual_amount_input"] = max(
            0.0, delivery_amount_saved
        )
        st.session_state["invoice_setup_fee_input"] = max(0.0, setup_amount_saved)
        st.session_state["invoice_apply_gct_input"] = gct_amount_saved > 0.0
        if discount_amount_saved > 0:
            st.session_state["invoice_discount_mode_input"] = "Discount Amount (JMD)"
            st.session_state["invoice_discount_amount_input"] = float(discount_amount_saved)
            st.session_state["invoice_discount_percent_input"] = 0.0
        else:
            st.session_state["invoice_discount_mode_input"] = "No Discount"
            st.session_state["invoice_discount_amount_input"] = 0.0
            st.session_state["invoice_discount_percent_input"] = 0.0

        payment_status_saved = str(
            invoice_header.get("payment_status", "paid_full") or "paid_full"
        ).strip().lower()
        if payment_status_saved == "deposit_paid":
            st.session_state["invoice_real_payment_terms_input"] = "50% Deposit (Balance Later)"
        elif payment_status_saved == "unpaid":
            st.session_state["invoice_real_payment_terms_input"] = "Cash on Delivery (Balance Due)"
        else:
            st.session_state["invoice_real_payment_terms_input"] = "Paid In Full"
        st.session_state["invoice_additional_payment_note_input"] = str(
            invoice_header.get("payment_notes", "") or ""
        ).strip()

        st.session_state["invoice_document_type_selector"] = build_as
        st.session_state["invoice_items_editor_data"] = base_items
        st.session_state["invoice_items_editor_seed"] += 1
        st.session_state["invoice_parse_warnings"] = []
        st.session_state["invoice_parse_detected_total"] = 0.0
        st.session_state["invoice_parse_calculated_total"] = 0.0
        st.session_state["invoice_quick_actions_ready_id"] = None
        st.session_state["invoice_quick_payload"] = None
        st.session_state["invoice_quick_file_stub"] = ""
        st.session_state["invoice_quick_pdf_bytes"] = b""
        st.session_state["invoice_quick_png_bytes"] = b""
        st.session_state["invoice_builder_allow_overwrite"] = True
        st.session_state["invoice_builder_loaded_invoice_number"] = str(
            invoice_header.get("invoice_number", "") or ""
        ).strip()

    pending_saved_quote_id = st.session_state.get("invoice_pending_saved_quote_id")
    if pending_saved_quote_id is not None:
        st.session_state["invoice_pending_saved_quote_id"] = None
        try:
            quote_header, quote_items = invoice_export_bundle(int(pending_saved_quote_id))
            _load_saved_invoice_into_builder(
                quote_header,
                quote_items,
                build_as="Price Quote",
            )
            st.success("Saved quote loaded into Invoice Builder.")
        except Exception as exc:
            st.error(f"Could not load saved quote: {exc}")

    quote_records = pd.DataFrame()
    if not history.empty:
        quote_records = history.copy()
        quote_records["document_type"] = (
            quote_records["document_type"].fillna("").astype(str).str.strip().str.lower()
        )
        quote_records = quote_records[quote_records["document_type"] == "quote"].copy()
        if not quote_records.empty:
            quote_records["invoice_number"] = quote_records["invoice_number"].astype(str).str.strip()
            quote_records["customer_name"] = quote_records["customer_name"].fillna("").astype(str).str.strip()
            quote_records["event_date"] = quote_records["event_date"].fillna("").astype(str).str.strip()
            quote_records["event_time"] = quote_records["event_time"].fillna("").astype(str).str.strip()
            quote_records = quote_records.sort_values(
                by=["event_date", "invoice_number"],
                ascending=[False, False],
            )

    def _render_saved_price_quotes_block() -> None:
        st.markdown("**Saved Price Quotes**")
        if quote_records.empty:
            st.caption("No saved price quotes yet.")
            return

        def _clear_quick_action_cache() -> None:
            st.session_state["invoice_quick_actions_ready_id"] = None
            st.session_state["invoice_quick_payload"] = None
            st.session_state["invoice_quick_file_stub"] = ""
            st.session_state["invoice_quick_pdf_bytes"] = b""
            st.session_state["invoice_quick_png_bytes"] = b""

        def _convert_selected_quote(
            selected_quote_id: int,
            unlock_phrase: str,
        ) -> None:
            if str(unlock_phrase or "").strip().upper() != "CONVERT":
                raise ValueError("Type `CONVERT` to confirm quote unlock.")
            quote_header, quote_items = invoice_export_bundle(selected_quote_id)
            if str(quote_header.get("document_type", "")).strip().lower() != "quote":
                raise ValueError("Selected record is no longer a price quote.")

            quote_event_date_raw = pd.to_datetime(
                str(quote_header.get("event_date", "") or ""),
                errors="coerce",
            )
            quote_event_date = (
                quote_event_date_raw.date()
                if not pd.isna(quote_event_date_raw)
                else date.today()
            )

            converted_id = save_invoice(
                {
                    "invoice_number": str(
                        quote_header.get("invoice_number", "") or ""
                    ).strip(),
                    "event_date": quote_event_date,
                    "event_time": to_time_string(
                        quote_header.get("event_time", DEFAULT_EVENT_TIME)
                    ),
                    "rental_hours": float(
                        quote_header.get("rental_hours", DEFAULT_EVENT_HOURS)
                        or DEFAULT_EVENT_HOURS
                    ),
                    "event_timezone": str(
                        quote_header.get("event_timezone", DEFAULT_EVENT_TIMEZONE)
                        or DEFAULT_EVENT_TIMEZONE
                    ),
                    "event_location": str(quote_header.get("event_location", "") or "").strip(),
                    "document_type": "invoice",
                    "order_status": "confirmed",
                    "created_by": str(
                        st.session_state.get("invoice_created_by_input", "") or ""
                    ).strip(),
                    "source_device": detected_device,
                    "customer_name": str(quote_header.get("customer_name", "") or "").strip(),
                    "customer_phone": str(quote_header.get("customer_phone", "") or "").strip(),
                    "customer_email": str(quote_header.get("customer_email", "") or "").strip(),
                    "delivered_to": str(quote_header.get("delivered_to", "") or "").strip(),
                    "paid_to": str(quote_header.get("paid_to", "") or "").strip(),
                    "payment_status": str(
                        quote_header.get("payment_status", "paid_full") or "paid_full"
                    ).strip(),
                    "amount_paid": float(quote_header.get("amount_paid", 0.0) or 0.0),
                    "deposit_balance_enabled": bool(
                        int(float(quote_header.get("deposit_balance_enabled", 0) or 0))
                    ),
                    "payment_notes": str(quote_header.get("payment_notes", "") or "").strip(),
                    "notes": str(quote_header.get("notes", "") or "").strip(),
                },
                quote_items,
                allow_overwrite=True,
                force_quote_unlock=True,
            )
            st.session_state["invoice_last_saved_id"] = int(converted_id)
            st.session_state["invoice_export_selected_id"] = int(converted_id)
            _clear_quick_action_cache()

        latest_quote = quote_records.iloc[0]
        m1, m2, m3 = st.columns(3)
        m1.metric("Saved Quotes", int(len(quote_records)))
        m2.metric(
            "Latest Quote #",
            str(latest_quote.get("invoice_number", "") or "-"),
        )
        m3.metric(
            "Latest Customer",
            str(latest_quote.get("customer_name", "") or "No Customer"),
        )

        search_col, search_hint_col = st.columns([2.2, 1.4])
        quote_search_text = search_col.text_input(
            "Find Saved Quote",
            placeholder="Search by customer name or quote number",
            key="saved_quote_search_text",
        ).strip()
        search_hint_col.caption("Tip: type part of name or invoice number.")

        filtered_quotes = quote_records.copy()
        if quote_search_text:
            needle = quote_search_text.lower()
            mask = (
                filtered_quotes["customer_name"].str.lower().str.contains(needle, na=False)
                | filtered_quotes["invoice_number"].str.lower().str.contains(needle, na=False)
            )
            filtered_quotes = filtered_quotes[mask].copy()

        if filtered_quotes.empty:
            st.warning("No saved quotes match that search.")
            return

        quote_label_to_id: dict[str, int] = {}
        for _, row in filtered_quotes.iterrows():
            customer_label = row["customer_name"] if row["customer_name"] else "No Customer"
            quote_no = row["invoice_number"] if row["invoice_number"] else "No Number"
            date_label = row["event_date"] if row["event_date"] else "No Date"
            time_label = f" {row['event_time']}" if row["event_time"] else ""
            label = f"{customer_label} | Quote #{quote_no} | {date_label}{time_label}"
            quote_label_to_id[label] = int(row["id"])

        selected_quote_label = st.selectbox(
            "Select Saved Price Quote",
            options=list(quote_label_to_id.keys()),
            key="saved_quote_action_selector",
        )
        selected_quote_id = int(quote_label_to_id[selected_quote_label])

        selected_quote_row = filtered_quotes[
            filtered_quotes["id"] == selected_quote_id
        ].head(1)
        if not selected_quote_row.empty:
            selected_quote_row = selected_quote_row.iloc[0]
            s1, s2, s3 = st.columns(3)
            s1.caption(
                f"Customer: {selected_quote_row.get('customer_name', '') or 'No Customer'}"
            )
            s2.caption(
                f"Quote #: {selected_quote_row.get('invoice_number', '') or 'No Number'}"
            )
            event_date_label = selected_quote_row.get("event_date", "") or "No Date"
            event_time_label = selected_quote_row.get("event_time", "") or ""
            s3.caption(f"Event: {event_date_label}{(' ' + event_time_label) if event_time_label else ''}")

        load_tab, convert_tab, delete_tab = st.tabs(["Load", "Convert", "Delete"])

        with load_tab:
            st.caption("Load this saved quote into the form so you can edit before sending.")
            if st.button(
                "Load Quote Into Builder",
                key=f"load_saved_quote_{selected_quote_id}",
                use_container_width=True,
            ):
                st.session_state["invoice_pending_saved_quote_id"] = int(selected_quote_id)
                st.rerun()

        with convert_tab:
            st.caption(
                "Hard safety lock is active. Conversion requires manual confirmation."
            )
            convert_unlock_phrase = st.text_input(
                "Type CONVERT to unlock",
                key=f"convert_saved_quote_phrase_{selected_quote_id}",
                placeholder="CONVERT",
            )
            if st.button(
                "Unlock + Convert Selected Quote",
                key=f"convert_saved_quote_btn_{selected_quote_id}",
                use_container_width=True,
                type="primary",
            ):
                try:
                    _convert_selected_quote(
                        selected_quote_id,
                        unlock_phrase=convert_unlock_phrase,
                    )
                    st.success(
                        "Quote converted to confirmed invoice. Finance + inventory were updated."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not convert quote: {exc}")

        with delete_tab:
            st.caption("Delete this saved quote permanently from the system.")
            delete_quote_confirmed = st.checkbox(
                "Confirm delete selected quote",
                key=f"confirm_delete_saved_quote_{selected_quote_id}",
            )
            if st.button(
                "Delete Selected Quote",
                key=f"delete_saved_quote_{selected_quote_id}",
                use_container_width=True,
                type="secondary",
            ):
                if not delete_quote_confirmed:
                    st.error("Tick confirmation before deleting a saved quote.")
                else:
                    try:
                        quote_header, _ = invoice_export_bundle(selected_quote_id)
                        if str(quote_header.get("document_type", "")).strip().lower() != "quote":
                            st.error("Selected record is no longer a price quote.")
                        else:
                            deleted_quote = delete_invoice(selected_quote_id)
                            for path_text in deleted_quote.get("attachment_paths", []):
                                path_obj = Path(path_text)
                                if path_obj.exists():
                                    try:
                                        path_obj.unlink()
                                    except Exception:
                                        pass
                            clear_finance_caches()
                            try:
                                create_db_backup_snapshot(reason="quote_delete", force=True)
                            except Exception:
                                pass
                            st.success(
                                f"Deleted saved quote #{deleted_quote.get('invoice_number', '')}."
                            )
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete saved quote: {exc}")

    def _render_quick_intake_block() -> None:
        st.markdown("**Quick Intake from Quote/Invoice PDF**")
        left, right = st.columns([2, 1])
        uploaded_pdf = left.file_uploader(
            "Upload a quote/invoice PDF to auto-fill the form",
            type=["pdf"],
            key="invoice_pdf_upload",
        )
        file_path = right.text_input(
            "Or local file path",
            placeholder="/Users/.../Quote.pdf",
            key="invoice_pdf_path",
        )

        b1, b2 = st.columns([1, 1])
        extract_upload = b1.button(
            "Extract from Uploaded PDF",
            use_container_width=True,
            disabled=uploaded_pdf is None,
        )
        extract_path = b2.button(
            "Extract from File Path",
            use_container_width=True,
        )

        if extract_upload and uploaded_pdf is not None:
            try:
                parsed = parse_invoice_pdf(uploaded_pdf.getvalue(), uploaded_pdf.name)
                st.session_state["invoice_pending_parsed_payload"] = parsed
                st.toast("PDF extracted. Builder fields updated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not parse uploaded PDF: {exc}")

        if extract_path:
            if not file_path.strip():
                st.error("Enter a file path first.")
            elif not Path(file_path).exists():
                st.error("File path not found.")
            else:
                try:
                    with open(file_path, "rb") as file_handle:
                        parsed = parse_invoice_pdf(file_handle.read(), Path(file_path).name)
                    st.session_state["invoice_pending_parsed_payload"] = parsed
                    st.toast("PDF extracted. Builder fields updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not parse PDF from path: {exc}")

        if (
            st.session_state["invoice_parse_detected_total"] > 0
            or st.session_state["invoice_parse_calculated_total"] > 0
        ):
            st.caption(
                "Detected total: "
                f"{money(st.session_state['invoice_parse_detected_total'])} | "
                "Calculated line-item total: "
                f"{money(st.session_state['invoice_parse_calculated_total'])}"
            )
        for warning in st.session_state["invoice_parse_warnings"]:
            st.warning(warning)

    st.markdown("---")

    render_step_block(
        "Step 1: Document + Event + Customer",
        "Choose quote/order mode, set event details, and customer information.",
    )
    st.markdown("**Document Type**")
    mode_col1, mode_col2 = st.columns([1.2, 1.8])
    document_type_choice = mode_col1.radio(
        "Build As",
        options=["Price Quote", "Confirmed Order"],
        key="invoice_document_type_selector",
        horizontal=True,
    )
    if document_type_choice == "Price Quote":
        mode_col2.caption("Quote mode: no Finance/Inventory impact.")
        document_mode = "Price Quote (no impact)"
        st.info(
            "You are building a PRICE QUOTE. This will not affect Finance Hub or Inventory."
        )
    else:
        mode_col2.info("Confirmed orders update Finance Hub and Inventory immediately.")
        document_mode = "Confirmed Order (impacts Finance/Inventory)"
        st.success(
            "You are building a CONFIRMED ORDER. This will affect Finance Hub prices/totals and update Inventory now."
        )

    st.markdown("---")

    editor_key = f"invoice_items_editor_{st.session_state['invoice_items_editor_seed']}"
    default_items = st.session_state["invoice_items_editor_data"].copy()
    if "unit_cost" in default_items.columns:
        default_items = default_items.drop(columns=["unit_cost"])

    a1, a2, a3, a4 = st.columns(4)
    invoice_number = a1.text_input(
        "Invoice Number *",
        placeholder="e.g. D177",
        key="invoice_number_input",
    )
    if document_type_choice == "Confirmed Order":
        a1.caption(f"Auto sequence next #: `{next_confirmed_number}`")
    event_date = a2.date_input("Event Date", key="invoice_event_date_input")
    event_time_value = a3.time_input("Event Time", key="invoice_event_time_input")
    rental_days = int(
        a4.number_input(
            "Rental Day(s)",
            min_value=1,
            max_value=30,
            step=1,
            key="invoice_rental_days_input",
        )
    )
    rental_hours = float(rental_days * 24.0)
    st.caption(f"Duration: {rental_days} day(s) ({rental_hours:g} hours).")
    st.session_state["invoice_rental_hours_input"] = float(rental_hours)
    st.caption("Timezone for event scheduling/reminders: America/Jamaica.")

    meta_col1, meta_col2 = st.columns(2)
    created_by = meta_col1.text_input(
        "Built By (Person Name)",
        key="invoice_created_by_input",
        placeholder="e.g. Oshani",
    )
    meta_col2.text_input(
        "Device",
        value=detected_device,
        disabled=True,
        key=f"invoice_device_display_{detected_device}",
    )

    b1, b2, b3 = st.columns(3)
    customer_name = b1.text_input("Customer Name", key="invoice_customer_name_input")
    customer_phone = b2.text_input("Customer Phone", key="invoice_customer_phone_input")
    customer_email = b3.text_input("Customer Email", key="invoice_customer_email_input")

    event_location = st.text_input("Event Location", key="invoice_event_location_input")

    d1, d2 = st.columns(2)
    paid_to = d1.text_input("Paid To", key="invoice_paid_to_input")
    notes = d2.text_input("Notes", key="invoice_notes_input")
    delivered_to = str(event_location or "").strip()

    render_step_block(
        "Step 2: Item Lines",
        "Add equipment/services and confirm quantities and rental price.",
    )
    st.markdown("**Items**")
    st.caption(
        "As you edit Qty and Unit Cost, totals below update automatically. "
        "Day(s) multiplier is added as a separate line."
    )
    st.caption("Rule: Do not enter GCT/Delivery/Discount in this table. Add those only in Step 3 (Fees + Tax).")
    template_item_options = sorted(inventory_templates.keys())
    if template_item_options:
        template_item = st.selectbox(
            "Quick Add Line Item Template",
            options=[""] + template_item_options,
            key="invoice_template_item_selector",
        )
    else:
        template_item = ""
        st.caption("No template items yet. Add inventory items to enable quick add templates.")

    mobile_ctrl_a, mobile_ctrl_b, mobile_ctrl_c, mobile_ctrl_d = st.columns(4)
    if mobile_ctrl_a.button("Add Item Row", key="invoice_items_add_row_btn"):
        current_rows = normalize_invoice_items_df(st.session_state.get("invoice_items_editor_data", pd.DataFrame()))
        if current_rows.empty:
            current_rows = pd.DataFrame(
                [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
            )
        else:
            current_rows = pd.concat(
                [
                    current_rows[["item_name", "item_type", "quantity", "unit_price"]].copy(),
                    pd.DataFrame(
                        [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
                    ),
                ],
                ignore_index=True,
            )
        st.session_state["invoice_items_editor_data"] = current_rows[
            ["item_name", "item_type", "quantity", "unit_price"]
        ].copy()
        st.session_state["invoice_items_editor_seed"] += 1
        st.rerun()

    if mobile_ctrl_b.button("Remove Last Row", key="invoice_items_remove_last_row_btn"):
        current_rows = normalize_invoice_items_df(st.session_state.get("invoice_items_editor_data", pd.DataFrame()))
        if len(current_rows) > 1:
            current_rows = current_rows.iloc[:-1].copy()
        else:
            current_rows = pd.DataFrame(
                [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
            )
        st.session_state["invoice_items_editor_data"] = current_rows[
            ["item_name", "item_type", "quantity", "unit_price"]
        ].copy()
        st.session_state["invoice_items_editor_seed"] += 1
        st.rerun()

    if mobile_ctrl_c.button("Clear Rows", key="invoice_items_clear_rows_btn"):
        st.session_state["invoice_items_editor_data"] = pd.DataFrame(
            [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
        )
        st.session_state["invoice_items_editor_seed"] += 1
        st.rerun()

    if mobile_ctrl_d.button(
        "Add Selected Template",
        key="invoice_template_add_btn",
        disabled=not template_item,
    ):
        template_price = float(
            inventory_templates.get(template_item, {}).get("unit_price", 0.0) or 0.0
        )
        template_type = "product"
        row = {
            "item_name": str(template_item).strip(),
            "item_type": template_type,
            "quantity": 1.0,
            "unit_price": template_price,
        }
        current_rows = normalize_invoice_items_df(
            st.session_state.get("invoice_items_editor_data", pd.DataFrame())
        )
        if current_rows.empty:
            next_rows = pd.DataFrame([row])
        else:
            next_rows = pd.concat(
                [
                    current_rows[["item_name", "item_type", "quantity", "unit_price"]].copy(),
                    pd.DataFrame([row]),
                ],
                ignore_index=True,
            )
        st.session_state["invoice_items_editor_data"] = next_rows[
            ["item_name", "item_type", "quantity", "unit_price"]
        ].copy()
        st.session_state["invoice_items_editor_seed"] += 1
        st.rerun()

    items_editor_source = normalize_invoice_items_df(default_items)
    items_editor_source = items_editor_source[
        ["item_name", "item_type", "quantity", "unit_price"]
    ].copy()
    if items_editor_source.empty:
        items_editor_source = pd.DataFrame(
            [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
        )
    existing_row_names = sorted(
        {
            str(name).strip()
            for name in items_editor_source["item_name"].tolist()
            if str(name).strip()
        }
    )
    item_name_suggestions = sorted(
        set(in_stock_inventory_suggestions).union(set(existing_row_names))
    )
    st.caption(
        "Fast entry mode: press Enter or tap outside a field to commit. Totals update immediately "
        "from the rows below; `Apply Table Changes` is only for locking rows before bundles."
    )

    header_cols = st.columns([2.7, 1.0, 0.9, 1.25, 1.25, 0.55])
    header_cols[0].markdown("**Item Name**")
    header_cols[1].markdown("**Type**")
    header_cols[2].markdown("**Qty**")
    header_cols[3].markdown("**Unit Price**")
    header_cols[4].markdown("**Base Rental**")
    header_cols[5].markdown("**Del**")

    live_item_rows: list[dict[str, object]] = []
    delete_row_index: int | None = None
    for idx, row in items_editor_source.reset_index(drop=True).iterrows():
        row_cols = st.columns([2.7, 1.0, 0.9, 1.25, 1.25, 0.55])
        item_name_value = row_cols[0].text_input(
            f"Item Name {idx + 1}",
            value=str(row.get("item_name", "") or ""),
            key=f"{editor_key}_item_name_{idx}",
            placeholder="e.g. 10x10 Tent",
            label_visibility="collapsed",
        )
        type_options = ["product", "service"]
        current_type = str(row.get("item_type", "product") or "product").strip().lower()
        item_type_value = row_cols[1].selectbox(
            f"Type {idx + 1}",
            options=type_options,
            index=type_options.index(current_type) if current_type in type_options else 0,
            key=f"{editor_key}_item_type_{idx}",
            label_visibility="collapsed",
        )
        quantity_value = row_cols[2].number_input(
            f"Qty {idx + 1}",
            min_value=0.0,
            step=1.0,
            value=float(row.get("quantity", 0.0) or 0.0),
            key=f"{editor_key}_quantity_{idx}",
            label_visibility="collapsed",
        )
        unit_price_value = row_cols[3].number_input(
            f"Unit Price {idx + 1}",
            min_value=0.0,
            step=100.0,
            value=float(row.get("unit_price", 0.0) or 0.0),
            key=f"{editor_key}_unit_price_{idx}",
            label_visibility="collapsed",
        )
        base_rental_value = float(quantity_value or 0.0) * float(unit_price_value or 0.0)
        row_cols[4].markdown(f"**{money(base_rental_value)}**")
        if row_cols[5].button("x", key=f"{editor_key}_delete_{idx}", help="Remove this row"):
            delete_row_index = idx
        live_item_rows.append(
            {
                "item_name": item_name_value,
                "item_type": item_type_value,
                "quantity": float(quantity_value or 0.0),
                "unit_price": float(unit_price_value or 0.0),
            }
        )

    if delete_row_index is not None:
        next_rows = pd.DataFrame(
            [row for idx, row in enumerate(live_item_rows) if idx != delete_row_index]
        )
        if next_rows.empty:
            next_rows = pd.DataFrame(
                [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
            )
        st.session_state["invoice_items_editor_data"] = next_rows[
            ["item_name", "item_type", "quantity", "unit_price"]
        ].copy()
        st.session_state["invoice_items_editor_seed"] += 1
        st.rerun()

    items = normalize_invoice_items_df(pd.DataFrame(live_item_rows))
    items, reserved_step2_labels = sanitize_step2_items(items)
    if reserved_step2_labels:
        st.warning(
            "Step 2 line items cannot include GCT, Delivery, or Discount. "
            "Those are managed only in Step 3 (Fees + Tax). "
            f"Removed: {', '.join(reserved_step2_labels)}"
        )
    persisted_items = items[
        ["item_name", "item_type", "quantity", "unit_price"]
    ].copy()
    apply_col, apply_note_col = st.columns([1.0, 2.4])
    if apply_col.button(
        "Apply Table Changes",
        key="invoice_items_apply_changes_btn",
        use_container_width=True,
    ):
        st.session_state["invoice_items_editor_data"] = persisted_items.copy()
        st.success("Table changes applied.")
    apply_note_col.caption(
        "Totals already use the live rows above. Apply is only needed before bundle save/load actions."
    )
    st.session_state["invoice_items_editor_data"] = persisted_items.copy()
    if item_name_suggestions:
        st.caption(
            "Inventory suggestions: "
            + ", ".join(item_name_suggestions[:12])
            + ("..." if len(item_name_suggestions) > 12 else "")
        )

    def _render_bundle_builder_block(items_frame: pd.DataFrame, default_items_frame: pd.DataFrame) -> None:
        st.markdown("**Bundle Builder (One-Click Presets)**")
        st.caption("Save, load, and append package presets using the table you just edited.")
        bundle_presets = load_invoice_bundle_presets()
        bundle_col_left, bundle_col_right = st.columns([1.5, 1.3])

        with bundle_col_left:
            if bundle_presets:
                bundle_label_map: dict[str, dict[str, object]] = {}
                for row in bundle_presets:
                    item_count = len(row.get("items", []) if isinstance(row.get("items", []), list) else [])
                    label = f"{str(row.get('name', '')).strip()} ({item_count} items)"
                    bundle_label_map[label] = row

                selected_bundle_label = st.selectbox(
                    "Saved Bundles",
                    options=list(bundle_label_map.keys()),
                    key="invoice_bundle_apply_selector",
                )
                selected_bundle = bundle_label_map[selected_bundle_label]
                bundle_items_df = _bundle_items_to_df(
                    selected_bundle.get("items", []) if isinstance(selected_bundle, dict) else []
                )

                apply1, apply2 = st.columns(2)
                if apply1.button(
                    "Load Bundle (Replace)",
                    key="invoice_bundle_apply_replace_btn",
                    use_container_width=True,
                ):
                    st.session_state["invoice_items_editor_data"] = bundle_items_df.copy()
                    st.session_state["invoice_items_editor_seed"] += 1
                    st.success("Bundle loaded into invoice table.")
                    st.rerun()
                if apply2.button(
                    "Add Bundle (Append)",
                    key="invoice_bundle_apply_append_btn",
                    use_container_width=True,
                ):
                    current_rows = normalize_invoice_items_df(
                        st.session_state.get("invoice_items_editor_data", default_items_frame)
                    )
                    combined = pd.concat(
                        [
                            current_rows[["item_name", "item_type", "quantity", "unit_price"]].copy(),
                            bundle_items_df[["item_name", "item_type", "quantity", "unit_price"]].copy(),
                        ],
                        ignore_index=True,
                    )
                    st.session_state["invoice_items_editor_data"] = combined.copy()
                    st.session_state["invoice_items_editor_seed"] += 1
                    st.success("Bundle appended to invoice table.")
                    st.rerun()

                with st.expander("Bundle Preview", expanded=False):
                    if str(selected_bundle.get("notes", "")).strip():
                        st.caption(f"Notes: {str(selected_bundle.get('notes', '')).strip()}")
                    preview = bundle_items_df.copy()
                    preview["quantity"] = pd.to_numeric(preview["quantity"], errors="coerce").fillna(0.0)
                    preview["unit_price"] = pd.to_numeric(preview["unit_price"], errors="coerce").fillna(0.0)
                    preview["base_rental"] = (preview["quantity"] * preview["unit_price"]).round(2)
                    preview["unit_price"] = preview["unit_price"].map(money)
                    preview["base_rental"] = preview["base_rental"].map(money)
                    st.dataframe(
                        preview[["item_name", "item_type", "quantity", "unit_price", "base_rental"]],
                        hide_index=True,
                        use_container_width=True,
                    )
            else:
                st.caption("No bundles saved yet. Save your first package from the right panel.")

        with bundle_col_right:
            with st.form("invoice_bundle_save_form", clear_on_submit=True):
                bundle_name_input = st.text_input(
                    "Bundle Name",
                    placeholder="e.g. Kids Party Pack",
                )
                bundle_notes_input = st.text_input(
                    "Bundle Notes (optional)",
                    placeholder="e.g. Most requested Saturday setup",
                )
                save_bundle_now = st.form_submit_button("Save Current Rows as Bundle")

            if save_bundle_now:
                source_rows = normalize_invoice_items_df(
                    st.session_state.get("invoice_items_editor_data", default_items_frame)
                )
                ok, message = upsert_invoice_bundle_preset(
                    bundle_name=bundle_name_input,
                    items=source_rows,
                    notes=bundle_notes_input,
                )
                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

            if bundle_presets:
                delete_map = {
                    f"{str(row.get('name', '')).strip()}": str(row.get("name", "")).strip()
                    for row in bundle_presets
                }
                delete_selection = st.selectbox(
                    "Delete Saved Bundle",
                    options=list(delete_map.keys()),
                    key="invoice_bundle_delete_selector",
                )
                delete_confirm = st.checkbox(
                    "Confirm bundle delete",
                    key="invoice_bundle_delete_confirm",
                )
                if st.button(
                    "Delete Bundle",
                    key="invoice_bundle_delete_btn",
                    use_container_width=True,
                    type="secondary",
                ):
                    if not delete_confirm:
                        st.error("Tick confirmation before deleting bundle.")
                    else:
                        if delete_invoice_bundle_preset(delete_map[delete_selection]):
                            st.success("Bundle deleted.")
                            st.rerun()
                        else:
                            st.info("Selected bundle was already removed.")

    st.markdown("---")

    render_step_block(
        "Step 3: Fees + Tax + Payment",
        "Enter fees, apply optional discount/GCT, then choose payment terms.",
    )
    st.markdown("**Charges + Tax**")
    st.caption("Simple flow: Delivery + Set-Up -> Discount -> GCT -> Total.")

    preview_items = remove_auto_fee_rows(items)
    preview_items = preview_items[
        (preview_items["item_name"].str.strip() != "")
        & (preview_items["quantity"] > 0)
    ].copy()
    base_subtotal = float((preview_items["quantity"] * preview_items["unit_price"]).sum())
    day_multiplier_amount = round(base_subtotal * float(max(0, rental_days - 1)), 2)
    adjusted_rental_subtotal = round(base_subtotal + day_multiplier_amount, 2)

    st.markdown("**Rate Settings**")
    with st.expander("Automatic fee rate settings", expanded=False):
        rs1, rs2 = st.columns(2)
        origin_address = rs1.text_input(
            "Delivery Origin Address",
            value=get_delivery_setting("origin_address", "61 West Main Drive, Kingston, Jamaica"),
            key="rate_origin_address_input",
        )
        fuel_price_per_litre = rs2.number_input(
            "Fuel Price Per Litre (JMD)",
            min_value=0.0,
            step=10.0,
            value=delivery_setting_float("fuel_price_per_litre", 175.0),
            key="rate_fuel_price_per_litre_input",
            help="Petrojam updates prices weekly — update this figure accordingly.",
        )
        rs3, rs4, rs5 = st.columns(3)
        delivery_labour_day_rate = rs3.number_input(
            "Delivery Labour Day Rate Per Person (JMD)",
            min_value=0.0,
            step=500.0,
            value=delivery_setting_float("delivery_labour_day_rate", 5000.0),
            key="rate_delivery_labour_day_rate_input",
        )
        default_delivery_crew_size = rs4.number_input(
            "Default Delivery Crew Size",
            min_value=1,
            step=1,
            value=int(delivery_setting_float("delivery_crew_size", 2.0)),
            key="rate_delivery_crew_size_input",
        )
        wear_per_vehicle_km = rs5.number_input(
            "Vehicle Wear Rate Per Vehicle Km (JMD)",
            min_value=0.0,
            step=1.0,
            value=delivery_setting_float("wear_per_vehicle_km", 16.0),
            key="rate_wear_per_vehicle_km_input",
            help="Distance-scaled vehicle wear. Calculated as total delivery km x vehicles used x this rate.",
        )
        rs5a, rs5b = st.columns(2)
        delivery_hourly_rate_per_person = rs5a.number_input(
            "Delivery Hourly Rate Per Person (JMD)",
            min_value=0.0,
            step=100.0,
            value=delivery_setting_float(
                "delivery_hourly_rate_per_person",
                float(delivery_labour_day_rate) / 8.0 if float(delivery_labour_day_rate) > 0 else 625.0,
            ),
            key="rate_delivery_hourly_rate_per_person_input",
            help="Used for short/local deliveries when hourly labour is fairer than a full day rate.",
        )
        loading_buffer_minutes = rs5b.number_input(
            "Loading/Unloading Buffer (minutes per round trip)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("loading_buffer_minutes", 30.0),
            key="rate_loading_buffer_minutes_input",
            help=(
                "Vehicle handling time only: loading items into the vehicle and unloading them from the vehicle. "
                "It does not include on-site arranging, setup, breakdown, or gathering equipment."
            ),
        )
        rs6, rs7, rs8, rs8b = st.columns(4)
        risk_buffer_percent = rs6.number_input(
            "Risk / Breakage Buffer (%)",
            min_value=0.0,
            step=1.0,
            value=delivery_setting_float("risk_buffer_percent", 5.0),
            key="rate_risk_buffer_percent_input",
        )
        delivery_margin_percent = rs7.number_input(
            "Delivery Margin (%)",
            min_value=0.0,
            step=1.0,
            value=delivery_setting_float("delivery_margin_percent", 45.0),
            key="rate_delivery_margin_percent_input",
        )
        setup_labour_cost_rate = rs8.number_input(
            "Setup Labour Cost Rate (JMD/hr)",
            min_value=0.0,
            step=25.0,
            value=delivery_setting_float("setup_labour_cost_rate", 625.0),
            key="rate_setup_labour_cost_rate_input",
        )
        setup_margin_pct = rs8b.number_input(
            "Setup Margin (%)",
            min_value=0.0,
            step=1.0,
            value=delivery_setting_float("setup_margin_pct", 45.0),
            key="rate_setup_margin_pct_input",
        )
        setup_effective_hourly_rate = effective_setup_hourly_rate(
            setup_labour_cost_rate, setup_margin_pct
        )
        st.caption(
            f"Computed setup hourly rate: {money(setup_effective_hourly_rate)} / hr "
            f"({money(setup_labour_cost_rate)} cost + {float(setup_margin_pct):.1f}% margin)."
        )
        rs9, rs10 = st.columns(2)
        setup_crew_size_default = rs9.number_input(
            "Default Set-Up Crew Size",
            min_value=1,
            step=1,
            value=int(delivery_setting_float("setup_crew_size", 2.0)),
            key="rate_setup_crew_size_input",
        )
        setup_collection_multiplier = rs10.number_input(
            "Breakdown / Collection Labour Multiplier",
            min_value=0.0,
            step=0.1,
            value=delivery_setting_float("setup_collection_multiplier", 1.0),
            key="rate_setup_collection_multiplier_input",
        )

        st.markdown("Labour Premiums")
        prem1, prem2, prem3, prem4 = st.columns(4)
        public_holiday_premium_pct = prem1.number_input(
            "Public Holiday Premium (%)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("public_holiday_premium_pct", 50.0),
            key="rate_public_holiday_premium_pct_input",
        )
        sunday_premium_pct = prem2.number_input(
            "Sunday Premium (%)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("sunday_premium_pct", 25.0),
            key="rate_sunday_premium_pct_input",
        )
        after_630_premium_pct = prem3.number_input(
            "After 6:30pm Premium (%)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("after_630_premium_pct", 20.0),
            key="rate_after_630_premium_pct_input",
        )
        before_9_premium_pct = prem4.number_input(
            "Before 9:00am Premium (%)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("before_9_premium_pct", 15.0),
            key="rate_before_9_premium_pct_input",
        )
        st.caption("If multiple labour premiums apply to the same leg, only the highest one is used.")
        sn1, sn2, sn3 = st.columns(3)
        short_notice_threshold_hours = sn1.number_input(
            "Short Notice Threshold (hours)",
            min_value=0.0,
            step=1.0,
            value=delivery_setting_float("short_notice_threshold_hours", 24.0),
            key="rate_short_notice_threshold_hours_input",
        )
        short_notice_premium_pct = sn2.number_input(
            "Short Notice Premium (%)",
            min_value=0.0,
            step=5.0,
            value=delivery_setting_float("short_notice_premium_pct", 30.0),
            key="rate_short_notice_premium_pct_input",
        )
        short_notice_minimum_fee = sn3.number_input(
            "Short Notice Minimum Fee (JMD)",
            min_value=0.0,
            step=500.0,
            value=delivery_setting_float("short_notice_minimum_fee", 2000.0),
            key="rate_short_notice_minimum_fee_input",
        )
        st.caption(
            "Short Notice stacks with the calendar/time labour premium and applies once per confirmed booking to Delivery Labour only."
        )

        st.markdown("Public Holidays")
        public_holidays_table = st.data_editor(
            load_public_holidays_table(),
            hide_index=True,
            num_rows="dynamic",
            key="rate_public_holidays_table_editor",
            column_config={
                "holiday_date": st.column_config.DateColumn("Holiday Date", format="YYYY-MM-DD"),
                "holiday_name": st.column_config.TextColumn("Holiday Name"),
            },
            use_container_width=True,
        )

        st.markdown("Toll Reference Tables")
        st.caption(
            "Route Toll Presets are checked first and are the trusted source for known Jamaica toll routes. "
            "Google toll pricing is only a fallback for destinations that do not match a preset."
        )
        route_toll_presets_table = st.data_editor(
            load_route_toll_presets_table(),
            hide_index=True,
            num_rows="dynamic",
            key="rate_route_toll_presets_table_editor",
            column_config={
                "destination_match_keywords": st.column_config.TextColumn("Destination Match Keywords"),
                "highway_used": st.column_config.SelectboxColumn(
                    "Highway Used",
                    options=["East-West", "North-South", "None"],
                ),
                "class_1_toll_jmd": st.column_config.NumberColumn("Class 1 Toll (JMD)", min_value=0.0, step=50.0),
                "class_2_toll_jmd": st.column_config.NumberColumn("Class 2 Toll (JMD)", min_value=0.0, step=50.0),
                "class_3_toll_jmd": st.column_config.NumberColumn("Class 3 Toll (JMD)", min_value=0.0, step=50.0),
            },
            use_container_width=True,
        )
        ew_table = st.data_editor(
            load_east_west_toll_table(),
            hide_index=True,
            num_rows="dynamic",
            key="rate_east_west_toll_table_editor",
            column_config={
                "plaza": st.column_config.TextColumn("Plaza"),
                "c1_no_tag": st.column_config.NumberColumn("C1 no-tag", min_value=0.0, step=5.0),
                "c1_tag": st.column_config.NumberColumn("C1 tag", min_value=0.0, step=5.0),
                "c2_no_tag": st.column_config.NumberColumn("C2 no-tag", min_value=0.0, step=5.0),
                "c2_tag": st.column_config.NumberColumn("C2 tag", min_value=0.0, step=5.0),
                "c3_no_tag": st.column_config.NumberColumn("C3 no-tag", min_value=0.0, step=5.0),
                "c3_tag": st.column_config.NumberColumn("C3 tag", min_value=0.0, step=5.0),
            },
            use_container_width=True,
        )
        ns1, ns2, ns3 = st.columns(3)
        ns_full_toll_class_1 = ns1.number_input(
            "NS Highway Full-Route Toll Class 1 (JMD)",
            min_value=0.0,
            step=50.0,
            value=delivery_setting_float("ns_full_toll_class_1", 1600.0),
            key="rate_ns_full_toll_class_1_input",
        )
        ns_full_toll_class_2 = ns2.number_input(
            "NS Highway Full-Route Toll Class 2 (JMD)",
            min_value=0.0,
            step=50.0,
            value=delivery_setting_float("ns_full_toll_class_2", 3200.0),
            key="rate_ns_full_toll_class_2_input",
        )
        ns_full_route_distance_km = ns3.number_input(
            "NS Highway Full-Route Distance (km)",
            min_value=1.0,
            step=1.0,
            value=delivery_setting_float("ns_full_route_distance_km", 66.0),
            key="rate_ns_full_route_distance_km_input",
        )
        st.caption(
            "North-South distance-prorated settings are kept as reference/fallback values. "
            "For frequent routes, add a Route Toll Preset with the exact one-way toll."
        )

        st.markdown("Vehicle Profiles")
        vp1, vp2 = st.columns(2)
        with vp1:
            v1_name = st.text_input(
                "Vehicle 1 Name",
                value=get_delivery_setting("vehicle_1_name", "Toyota Wish"),
                key="rate_vehicle_1_name_input",
            )
            v1_fuel_type = st.selectbox(
                "Vehicle 1 Fuel Type",
                options=["petrol", "diesel"],
                index=0 if get_delivery_setting("vehicle_1_fuel_type", "petrol") == "petrol" else 1,
                key="rate_vehicle_1_fuel_type_input",
            )
            v1_efficiency = st.number_input(
                "Vehicle 1 Efficiency (km per litre)",
                min_value=1.0,
                step=0.5,
                value=delivery_setting_float("vehicle_1_efficiency", 7.0),
                key="rate_vehicle_1_efficiency_input",
            )
            v1_congestion_sensitivity = st.number_input(
                "Vehicle 1 Congestion Sensitivity",
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                value=delivery_setting_float("vehicle_1_congestion_sensitivity", 0.5),
                key="rate_vehicle_1_congestion_sensitivity_input",
                help="0 means traffic does not reduce fuel efficiency; 1 means full stop-start penalty.",
            )
            v1_toll_class = st.selectbox(
                "Vehicle 1 Toll Class",
                options=["Class 1", "Class 2", "Class 3"],
                index=["Class 1", "Class 2", "Class 3"].index(
                    get_delivery_setting("vehicle_1_toll_class", "Class 1")
                    if get_delivery_setting("vehicle_1_toll_class", "Class 1") in ["Class 1", "Class 2", "Class 3"]
                    else "Class 1"
                ),
                key="rate_vehicle_1_toll_class_input",
            )
            v1_has_ttag = st.toggle(
                "Vehicle 1 Has T-Tag/E-Pass",
                value=get_delivery_setting("vehicle_1_has_ttag", "0") == "1",
                key="rate_vehicle_1_has_ttag_input",
            )
        with vp2:
            v2_name = st.text_input(
                "Vehicle 2 Name",
                value=get_delivery_setting("vehicle_2_name", "Nissan Caravan"),
                key="rate_vehicle_2_name_input",
            )
            v2_fuel_type = st.selectbox(
                "Vehicle 2 Fuel Type",
                options=["petrol", "diesel"],
                index=0 if get_delivery_setting("vehicle_2_fuel_type", "petrol") == "petrol" else 1,
                key="rate_vehicle_2_fuel_type_input",
            )
            v2_efficiency = st.number_input(
                "Vehicle 2 Efficiency (km per litre)",
                min_value=1.0,
                step=0.5,
                value=delivery_setting_float("vehicle_2_efficiency", 7.0),
                key="rate_vehicle_2_efficiency_input",
            )
            v2_congestion_sensitivity = st.number_input(
                "Vehicle 2 Congestion Sensitivity",
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                value=delivery_setting_float("vehicle_2_congestion_sensitivity", 0.3),
                key="rate_vehicle_2_congestion_sensitivity_input",
                help="Lower values suit lighter vehicles that lose less efficiency in congestion.",
            )
            v2_toll_class = st.selectbox(
                "Vehicle 2 Toll Class",
                options=["Class 1", "Class 2", "Class 3"],
                index=["Class 1", "Class 2", "Class 3"].index(
                    get_delivery_setting("vehicle_2_toll_class", "Class 2")
                    if get_delivery_setting("vehicle_2_toll_class", "Class 2") in ["Class 1", "Class 2", "Class 3"]
                    else "Class 2"
                ),
                key="rate_vehicle_2_toll_class_input",
            )
            v2_has_ttag = st.toggle(
                "Vehicle 2 Has T-Tag/E-Pass",
                value=get_delivery_setting("vehicle_2_has_ttag", "0") == "1",
                key="rate_vehicle_2_has_ttag_input",
            )

        st.markdown("Set-Up Minutes Reference")
        setup_minutes_table = st.data_editor(
            load_setup_minutes_table(),
            hide_index=True,
            num_rows="dynamic",
            key="rate_setup_minutes_table_editor",
            column_config={
                "category": st.column_config.TextColumn("Category"),
                "keywords": st.column_config.TextColumn("Matching Keywords"),
                "minutes_per_unit": st.column_config.NumberColumn("Minutes Per Unit", min_value=0.0, step=0.25),
            },
            use_container_width=True,
        )

        audit_tab, log_tab, evidence_tab = st.tabs(
            ["Catalog Match Audit", "Observed Setup Time Log", "Real Timing Evidence"]
        )
        with audit_tab:
            st.caption(
                "Uses the exact same substring keyword matcher as the automatic set-up calculator. "
                "Items landing on default are listed first because they are most likely to be silently mispriced."
            )
            inventory_catalog = cached_inventory_snapshot()
            audit_report = setup_catalog_match_audit(inventory_catalog, setup_minutes_table)
            if audit_report.empty:
                st.info(
                    "No inventory catalog items were found in this database. "
                    "Once the deployed app is connected to your live Pricing List, this audit will populate here."
                )
            else:
                default_count = int(
                    audit_report["matched_category"].astype(str).str.lower().eq("default").sum()
                )
                a1, a2, a3 = st.columns(3)
                a1.metric("Catalog Items Audited", len(audit_report))
                a2.metric("Default Matches", default_count)
                a3.metric("Named Category Matches", len(audit_report) - default_count)
                st.dataframe(
                    audit_report,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "item_name": st.column_config.TextColumn("Item Name"),
                        "matched_category": st.column_config.TextColumn("Matched Category"),
                        "minutes_per_unit": st.column_config.NumberColumn("Minutes Per Unit", format="%.2f"),
                    },
                )
                st.download_button(
                    "Download Setup Match Audit CSV",
                    data=audit_report.to_csv(index=False).encode("utf-8"),
                    file_name="setup_minutes_catalog_match_audit.csv",
                    mime="text/csv",
                    key="setup_match_audit_download_btn",
                )

        with log_tab:
            st.caption(
                "Log real stopwatch results after jobs. This does not change quote calculations or the "
                "Set-Up Minutes Reference table."
            )
            log_source = st.radio(
                "Item Source",
                options=["Pull from confirmed order", "Manual item entry"],
                horizontal=True,
                key="setup_time_log_source_input",
            )
            selected_log_invoice_id: int | None = None
            selected_log_label = ""
            selected_log_client = ""
            invoice_items_for_log = pd.DataFrame(columns=["item_name", "quantity"])
            if log_source == "Pull from confirmed order":
                log_invoice_options = cached_invoice_options(include_quotes=False, confirmed_only=True)
                if log_invoice_options.empty:
                    st.info("No confirmed orders found yet. Use manual item entry for this setup log.")
                else:
                    log_invoice_options = log_invoice_options.copy()
                    log_invoice_options["label"] = log_invoice_options.apply(
                        lambda row: (
                            f"{row.get('invoice_number', '')} | {row.get('customer_name', '')} | "
                            f"{row.get('event_date', '')} {row.get('event_time', '')}"
                        ),
                        axis=1,
                    )
                    selected_log_label = st.selectbox(
                        "Confirmed Order",
                        options=log_invoice_options["label"].tolist(),
                        key="setup_time_log_invoice_select_input",
                    )
                    selected_log_row = log_invoice_options[log_invoice_options["label"] == selected_log_label].iloc[0]
                    selected_log_invoice_id = int(selected_log_row["id"])
                    selected_log_client = str(selected_log_row.get("customer_name", "") or "")
                    invoice_items_for_log = setup_log_items_from_invoice(selected_log_invoice_id)
                    if invoice_items_for_log.empty:
                        st.warning("This order did not return product items. You can still enter items manually below.")

            manual_ref_default = selected_log_label if selected_log_label else ""
            manual_client_default = selected_log_client if selected_log_client else ""
            l1, l2, l3 = st.columns(3)
            setup_log_date = l1.date_input(
                "Setup Date",
                value=date.today(),
                key="setup_time_log_date_input",
            )
            setup_log_crew_size = l2.number_input(
                "Actual Crew Size",
                min_value=1,
                step=1,
                value=2,
                key="setup_time_log_crew_size_input",
            )
            setup_log_minutes = l3.number_input(
                "Actual Wall-Clock Setup Minutes",
                min_value=0.0,
                step=5.0,
                value=0.0,
                key="setup_time_log_wall_clock_minutes_input",
                help="Use the stopwatch time from arrival/setup start to setup completion.",
            )
            l4, l5 = st.columns(2)
            setup_log_reference = l4.text_input(
                "Order Reference / Client Name",
                value=manual_ref_default,
                key="setup_time_log_reference_input",
            )
            setup_log_client = l5.text_input(
                "Client Name",
                value=manual_client_default,
                key="setup_time_log_client_input",
            )

            if invoice_items_for_log.empty:
                default_log_items = pd.DataFrame([{"item_name": "", "quantity": 1.0}])
            else:
                default_log_items = invoice_items_for_log
            source_key = selected_log_invoice_id if selected_log_invoice_id else "manual"
            setup_log_items = st.data_editor(
                default_log_items,
                hide_index=True,
                num_rows="dynamic",
                key=f"setup_time_log_items_editor_{source_key}",
                column_config={
                    "item_name": st.column_config.TextColumn("Item Name"),
                    "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0),
                },
                use_container_width=True,
            )
            if st.button("Log Setup Time", key="setup_time_log_save_btn", type="secondary"):
                clean_items = normalize_setup_log_items(setup_log_items)
                if setup_log_minutes <= 0:
                    st.warning("Enter the actual wall-clock setup minutes before saving.")
                elif clean_items.empty:
                    st.warning("Add at least one item and quantity before saving.")
                else:
                    append_setup_time_log(
                        {
                            "logged_at": datetime.now(timezone.utc).isoformat(),
                            "date": str(setup_log_date),
                            "invoice_id": selected_log_invoice_id,
                            "order_reference": setup_log_reference.strip(),
                            "client_name": setup_log_client.strip(),
                            "crew_size": int(setup_log_crew_size),
                            "wall_clock_minutes": float(setup_log_minutes),
                            "items": clean_items.to_dict("records"),
                        }
                    )
                    st.success("Setup time logged. Quote calculations were not changed.")

            existing_setup_logs = load_setup_time_log()
            st.metric("Logged Setup Measurements", len(existing_setup_logs))
            log_summary = setup_time_log_summary(existing_setup_logs)
            if log_summary.empty:
                st.info("No setup time measurements logged yet.")
            else:
                st.dataframe(log_summary.tail(12), hide_index=True, use_container_width=True)

        with evidence_tab:
            st.caption(
                "Shows evidence only after at least 3 logged jobs are dominated by one category. "
                "This is informational only; it never edits the configured minutes."
            )
            comparison = setup_time_log_comparison(load_setup_time_log(), setup_minutes_table)
            if comparison.empty:
                st.info(
                    "Not enough dominated setup logs yet. Log at least 3 jobs where one category makes up "
                    "70% or more of the item quantities."
                )
            else:
                st.dataframe(
                    comparison,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "matched_category": st.column_config.TextColumn("Category"),
                        "currently_configured_min_per_unit": st.column_config.NumberColumn(
                            "Currently Configured (min/unit)", format="%.2f"
                        ),
                        "logged_jobs": st.column_config.NumberColumn("Logged Jobs"),
                        "average_observed_min_per_unit": st.column_config.NumberColumn(
                            "Average From Logged Jobs (min/unit)", format="%.2f"
                        ),
                        "gap_min_per_unit": st.column_config.NumberColumn("Gap (min/unit)", format="%.2f"),
                    },
                )

        if st.button("Save Rate Settings", key="save_fee_rate_settings_btn", type="secondary"):
            for key, value in {
                "origin_address": origin_address,
                "fuel_price_per_litre": fuel_price_per_litre,
                "delivery_labour_day_rate": delivery_labour_day_rate,
                "delivery_hourly_rate_per_person": delivery_hourly_rate_per_person,
                "loading_buffer_minutes": loading_buffer_minutes,
                "delivery_crew_size": default_delivery_crew_size,
                "wear_per_vehicle_km": wear_per_vehicle_km,
                "risk_buffer_percent": risk_buffer_percent,
                "delivery_margin_percent": delivery_margin_percent,
                "setup_labour_cost_rate": setup_labour_cost_rate,
                "setup_margin_pct": setup_margin_pct,
                "setup_hourly_rate": setup_effective_hourly_rate,
                "setup_crew_size": setup_crew_size_default,
                "setup_collection_multiplier": setup_collection_multiplier,
                "vehicle_1_name": v1_name,
                "vehicle_1_fuel_type": v1_fuel_type,
                "vehicle_1_efficiency": v1_efficiency,
                "vehicle_1_congestion_sensitivity": v1_congestion_sensitivity,
                "vehicle_1_toll_class": v1_toll_class,
                "vehicle_1_has_ttag": "1" if v1_has_ttag else "0",
                "vehicle_2_name": v2_name,
                "vehicle_2_fuel_type": v2_fuel_type,
                "vehicle_2_efficiency": v2_efficiency,
                "vehicle_2_congestion_sensitivity": v2_congestion_sensitivity,
                "vehicle_2_toll_class": v2_toll_class,
                "vehicle_2_has_ttag": "1" if v2_has_ttag else "0",
                "public_holiday_premium_pct": public_holiday_premium_pct,
                "sunday_premium_pct": sunday_premium_pct,
                "after_630_premium_pct": after_630_premium_pct,
                "before_9_premium_pct": before_9_premium_pct,
                "short_notice_threshold_hours": short_notice_threshold_hours,
                "short_notice_premium_pct": short_notice_premium_pct,
                "short_notice_minimum_fee": short_notice_minimum_fee,
                "ns_full_toll_class_1": ns_full_toll_class_1,
                "ns_full_toll_class_2": ns_full_toll_class_2,
                "ns_full_route_distance_km": ns_full_route_distance_km,
            }.items():
                set_delivery_setting(key, str(value))
            save_setup_minutes_table(setup_minutes_table)
            save_public_holidays_table(public_holidays_table)
            save_route_toll_presets_table(route_toll_presets_table)
            save_east_west_toll_table(ew_table)
            st.session_state["invoice_auto_setup_hourly_rate_input"] = float(
                setup_effective_hourly_rate
            )
            st.success("Rate settings saved.")

    fee1, fee2, fee3 = st.columns([1, 1, 1])
    delivery_manual_amount = fee1.number_input(
        "Delivery Fee Amount (JMD)",
        min_value=0.0,
        step=100.0,
        key="invoice_delivery_manual_amount_input",
    )
    setup_fee_amount = fee2.number_input(
        "Set-Up Fee Amount (JMD)",
        min_value=0.0,
        step=100.0,
        key="invoice_setup_fee_input",
    )
    apply_gct = fee3.toggle(
        "Add GCT (15%)",
        key="invoice_apply_gct_input",
        help="GCT is calculated on (Adjusted Rental + Delivery + Set-Up - Discount).",
    )

    mode1, mode2 = st.columns(2)
    delivery_fee_mode = mode1.radio(
        "Delivery Fee Mode",
        options=["Manual Entry", "Calculate Automatically"],
        index=0,
        horizontal=True,
        key="invoice_delivery_fee_mode_input",
    )
    setup_fee_mode = mode2.radio(
        "Set-Up Fee Mode",
        options=["Manual Entry", "Calculate Automatically"],
        index=0,
        horizontal=True,
        key="invoice_setup_fee_mode_input",
    )

    if delivery_fee_mode == "Calculate Automatically":
        with st.expander("Automatic Delivery Calculator", expanded=True):
            destination_default = str(event_location or delivered_to or "").strip()
            if "invoice_auto_delivery_destination_input" not in st.session_state:
                st.session_state["invoice_auto_delivery_destination_input"] = destination_default
            if "invoice_auto_delivery_destination_manually_set" not in st.session_state:
                st.session_state["invoice_auto_delivery_destination_manually_set"] = False
            if (
                destination_default
                and not bool(st.session_state.get("invoice_auto_delivery_destination_manually_set", False))
                and str(st.session_state.get("invoice_auto_delivery_destination_input", "") or "").strip() != destination_default
            ):
                st.session_state["invoice_auto_delivery_destination_input"] = destination_default

            def _mark_delivery_destination_manual() -> None:
                st.session_state["invoice_auto_delivery_destination_manually_set"] = True

            destination_address = st.text_input(
                "Destination Address",
                key="invoice_auto_delivery_destination_input",
                on_change=_mark_delivery_destination_manual,
                help="Auto-fills from Event Location until you edit it here manually.",
            )
            if "invoice_auto_delivery_trip_days_manually_set" not in st.session_state:
                st.session_state["invoice_auto_delivery_trip_days_manually_set"] = False
            if "invoice_auto_delivery_round_trips_input" not in st.session_state:
                st.session_state["invoice_auto_delivery_round_trips_input"] = 2
            if "invoice_auto_delivery_trip_days_input" not in st.session_state:
                st.session_state["invoice_auto_delivery_trip_days_input"] = 2

            def _sync_trip_days_to_round_trips() -> None:
                if not bool(st.session_state.get("invoice_auto_delivery_trip_days_manually_set", False)):
                    st.session_state["invoice_auto_delivery_trip_days_input"] = int(
                        max(1, st.session_state.get("invoice_auto_delivery_round_trips_input", 1))
                    )

            def _mark_trip_days_manually_set() -> None:
                st.session_state["invoice_auto_delivery_trip_days_manually_set"] = True

            dcalc1, dcalc2, dcalc3 = st.columns(3)
            round_trip_count = dcalc1.selectbox(
                "Number of round trips",
                options=[1, 2],
                index=1,
                key="invoice_auto_delivery_round_trips_input",
                on_change=_sync_trip_days_to_round_trips,
                help=(
                    "Default is 2 because a normal rental needs drop-off and next-day collection. "
                    "Use 1 only for true same-day pickup/collection exceptions."
                ),
            )
            delivery_crew_size = dcalc2.number_input(
                "Crew Size",
                min_value=1,
                step=1,
                value=int(default_delivery_crew_size),
                key="invoice_auto_delivery_crew_size_input",
            )
            trip_days = dcalc3.number_input(
                "Trip Days",
                min_value=1,
                step=1,
                key="invoice_auto_delivery_trip_days_input",
                on_change=_mark_trip_days_manually_set,
                help=(
                    "Default is 2 because most 24-hour rentals are collected the next day. "
                    "It follows round trips until you manually edit this field for the current quote."
                ),
            )
            st.caption(
                "Standard delivery assumption: 2 round trips and 2 trip days "
                "(drop-off plus next-day collection). Set Trip Days to 1 only for same-day collection "
                "or multiple same-day loads."
            )
            delivery_departure_dt = datetime.combine(
                event_date,
                event_time_value,
                tzinfo=tzinfo_for_name(DEFAULT_EVENT_TIMEZONE),
            )
            collection_departure_dt = delivery_departure_dt + timedelta(days=max(0, int(trip_days) - 1))
            collection_departure_caption = (
                "Collection drive-time assumes the same time-of-day as delivery "
                f"on {collection_departure_dt.strftime('%Y-%m-%d %H:%M')} "
                f"({DEFAULT_EVENT_TIMEZONE}), based on Trip Days = {int(trip_days)}."
            )
            vehicle_a, vehicle_b = st.columns(2)
            with vehicle_a:
                use_v1 = st.checkbox(
                    f"Use {v1_name}",
                    value=True,
                    key="invoice_auto_delivery_use_vehicle_1_input",
                )
            with vehicle_b:
                use_v2 = st.checkbox(
                    f"Use {v2_name}",
                    value=False,
                    key="invoice_auto_delivery_use_vehicle_2_input",
                )

            if st.button("Calculate Delivery Route", key="invoice_auto_delivery_route_btn", type="secondary"):
                (
                    one_way_km,
                    one_way_min,
                    free_flow_min,
                    collection_min,
                    collection_free_flow_min,
                    route_error,
                    _destination_parish,
                ) = google_maps_distance(
                    origin_address,
                    destination_address,
                    get_google_maps_api_key(),
                    delivery_departure_dt,
                    collection_departure_dt,
                )
                st.session_state["invoice_auto_delivery_one_way_km"] = one_way_km
                st.session_state["invoice_auto_delivery_one_way_min"] = one_way_min
                st.session_state["invoice_auto_delivery_free_flow_min"] = free_flow_min
                st.session_state["invoice_auto_delivery_collection_min"] = collection_min
                st.session_state["invoice_auto_delivery_collection_free_flow_min"] = collection_free_flow_min
                st.session_state["invoice_auto_delivery_collection_departure_caption"] = collection_departure_caption
                st.session_state["invoice_auto_delivery_route_error"] = route_error
                if one_way_km > 0 and not bool(st.session_state.get("invoice_auto_delivery_billing_mode_manually_set", False)):
                    st.session_state["invoice_auto_delivery_billing_mode_input"] = (
                        delivery_billing_mode_for_drive_time(one_way_min, collection_min)
                    )

            route_error = str(st.session_state.get("invoice_auto_delivery_route_error", "") or "")
            one_way_km = float(st.session_state.get("invoice_auto_delivery_one_way_km", 0.0) or 0.0)
            one_way_min = float(st.session_state.get("invoice_auto_delivery_one_way_min", 0.0) or 0.0)
            free_flow_min = float(st.session_state.get("invoice_auto_delivery_free_flow_min", 0.0) or 0.0)
            collection_min = float(st.session_state.get("invoice_auto_delivery_collection_min", 0.0) or 0.0)
            collection_free_flow_min = float(
                st.session_state.get("invoice_auto_delivery_collection_free_flow_min", 0.0) or 0.0
            )
            collection_departure_caption_saved = str(
                st.session_state.get("invoice_auto_delivery_collection_departure_caption", "") or ""
            )
            if "invoice_auto_delivery_billing_mode_input" not in st.session_state:
                st.session_state["invoice_auto_delivery_billing_mode_input"] = "Hourly Rate"
            if "invoice_auto_delivery_billing_mode_manually_set" not in st.session_state:
                st.session_state["invoice_auto_delivery_billing_mode_manually_set"] = False

            def _mark_delivery_billing_mode_manual() -> None:
                st.session_state["invoice_auto_delivery_billing_mode_manually_set"] = True

            billing_mode = st.radio(
                "Delivery Labour Billing",
                options=["Day Rate", "Hourly Rate"],
                horizontal=True,
                key="invoice_auto_delivery_billing_mode_input",
                on_change=_mark_delivery_billing_mode_manual,
                help="Hourly Rate is usually fairer for short local trips. Day Rate preserves the original long-job calculation.",
            )
            if route_error:
                st.warning(f"{route_error} Manual entry remains available.")
            elif one_way_km <= 0:
                st.info("Calculate the route to generate a delivery suggestion. Manual entry remains available.")
            else:
                default_billing_mode = delivery_billing_mode_for_drive_time(one_way_min, collection_min)
                longest_leg_min = max(float(one_way_min or 0.0), float(collection_min or 0.0))
                st.caption(
                    f"Traffic-aware labour default: under {TRAFFIC_AWARE_HOURLY_DELIVERY_THRESHOLD_MIN:,.0f} minutes "
                    f"defaults to Hourly Rate; {TRAFFIC_AWARE_HOURLY_DELIVERY_THRESHOLD_MIN:,.0f} minutes or more "
                    f"defaults to Day Rate. Longest leg is {longest_leg_min:,.0f} minute(s), so the default is "
                    f"{default_billing_mode}. Manual override remains available."
                )

            total_delivery_km = float(one_way_km * 2.0 * int(round_trip_count))
            vehicle_rows = []
            if use_v1:
                vehicle_rows.append(
                    (
                        v1_name,
                        v1_fuel_type,
                        float(v1_efficiency),
                        float(v1_congestion_sensitivity),
                        v1_toll_class,
                        bool(v1_has_ttag),
                    )
                )
            if use_v2:
                vehicle_rows.append(
                    (
                        v2_name,
                        v2_fuel_type,
                        float(v2_efficiency),
                        float(v2_congestion_sensitivity),
                        v2_toll_class,
                        bool(v2_has_ttag),
                    )
                )

            def _effective_efficiency(base_efficiency: float, traffic_min: float, base_min: float, sensitivity: float) -> float:
                base_efficiency = max(float(base_efficiency or 0.0), 0.1)
                traffic_min = float(traffic_min or 0.0)
                base_min = float(base_min or 0.0)
                sensitivity = min(max(float(sensitivity or 0.0), 0.0), 1.0)
                if traffic_min <= 0 or base_min <= 0:
                    return base_efficiency
                congestion_ratio = traffic_min / base_min
                if congestion_ratio <= 1.0:
                    return base_efficiency
                return base_efficiency / (1.0 + (congestion_ratio - 1.0) * sensitivity)

            fuel_cost = 0.0
            fuel_detail_rows: list[dict[str, object]] = []
            delivery_efficiency_notes: list[str] = []
            delivery_pair_km = float(one_way_km * 2.0)
            collection_pair_count = max(0, int(round_trip_count) - 1)
            for vehicle_name, _fuel_type, efficiency, congestion_sensitivity, *_toll_fields in vehicle_rows:
                delivery_eff = _effective_efficiency(efficiency, one_way_min, free_flow_min, congestion_sensitivity)
                collection_eff = _effective_efficiency(
                    efficiency,
                    collection_min,
                    collection_free_flow_min,
                    congestion_sensitivity,
                )
                delivery_fuel = (delivery_pair_km / max(delivery_eff, 0.1)) * float(fuel_price_per_litre)
                collection_fuel = (
                    (delivery_pair_km * collection_pair_count / max(collection_eff, 0.1)) * float(fuel_price_per_litre)
                    if collection_pair_count > 0
                    else 0.0
                )
                fuel_cost += delivery_fuel + collection_fuel
                delivery_ratio = (one_way_min / free_flow_min) if free_flow_min > 0 else 1.0
                collection_ratio = (
                    (collection_min / collection_free_flow_min)
                    if collection_free_flow_min > 0
                    else 1.0
                )
                delivery_efficiency_notes.append(
                    f"{vehicle_name}: delivery {delivery_eff:,.2f} km/L"
                    + (
                        f", collection {collection_eff:,.2f} km/L"
                        if collection_pair_count > 0
                        else ""
                    )
                )
                fuel_detail_rows.append(
                    {
                        "Vehicle": vehicle_name,
                        "Base km/L": f"{float(efficiency):,.2f}",
                        "Sensitivity": f"{float(congestion_sensitivity):,.2f}",
                        "Delivery Traffic Ratio": f"{delivery_ratio:,.2f}",
                        "Delivery Effective km/L": f"{delivery_eff:,.2f}",
                        "Collection Traffic Ratio": f"{collection_ratio:,.2f}" if collection_pair_count > 0 else "N/A",
                        "Collection Effective km/L": f"{collection_eff:,.2f}" if collection_pair_count > 0 else "N/A",
                    }
                )
            one_way_drive_time_hours = float(one_way_min) / 60.0
            collection_drive_time_hours = float(collection_min) / 60.0
            loading_buffer_hours = float(loading_buffer_minutes) / 60.0
            delivery_labour_hours = (2.0 * one_way_drive_time_hours) + loading_buffer_hours
            collection_labour_hours = (
                float(collection_pair_count) * ((2.0 * collection_drive_time_hours) + loading_buffer_hours)
            )
            hourly_labour_hours = delivery_labour_hours + collection_labour_hours

            public_holiday_dates = public_holiday_dates_from_table(public_holidays_table)
            delivery_premium_pct, delivery_premium_label = labour_premium_for_departure(
                delivery_departure_dt,
                public_holiday_dates,
                public_holiday_premium_pct,
                sunday_premium_pct,
                after_630_premium_pct,
                before_9_premium_pct,
            )
            collection_premium_pct, collection_premium_label = labour_premium_for_departure(
                collection_departure_dt,
                public_holiday_dates,
                public_holiday_premium_pct,
                sunday_premium_pct,
                after_630_premium_pct,
                before_9_premium_pct,
            )
            delivery_premium_multiplier = 1.0 + (float(delivery_premium_pct) / 100.0)
            collection_premium_multiplier = 1.0 + (float(collection_premium_pct) / 100.0)

            if billing_mode == "Hourly Rate":
                delivery_labour_cost = (
                    int(delivery_crew_size)
                    * delivery_labour_hours
                    * float(delivery_hourly_rate_per_person)
                    * delivery_premium_multiplier
                )
                collection_labour_cost = (
                    int(delivery_crew_size)
                    * collection_labour_hours
                    * float(delivery_hourly_rate_per_person)
                    * collection_premium_multiplier
                )
                labour_cost = delivery_labour_cost + collection_labour_cost
                labour_label = (
                    f"Hourly ({int(delivery_crew_size)} crew x "
                    f"{hourly_labour_hours:,.2f} total leg-hour(s) x {money(delivery_hourly_rate_per_person)}/hr, "
                    "with leg premiums applied)"
                )
                labour_detail_rows = [
                    {
                        "Component": "Delivery Leg Labour",
                        "Value": (
                            f"{money(delivery_labour_cost)} - {delivery_premium_label} "
                            f"({delivery_labour_hours:,.2f}h before crew multiplier)"
                        ),
                    },
                    {
                        "Component": "Collection Leg Labour",
                        "Value": (
                            f"{money(collection_labour_cost)} - "
                            + (
                                collection_premium_label
                                if collection_pair_count > 0
                                else "no separate collection leg billed"
                            )
                            + f" ({collection_labour_hours:,.2f}h before crew multiplier)"
                        ),
                    },
                ]
            else:
                day_rate_base = float(delivery_labour_day_rate) * int(delivery_crew_size)
                if int(trip_days) >= 2:
                    delivery_labour_cost = day_rate_base * delivery_premium_multiplier
                    collection_day_count = max(0, int(trip_days) - 1)
                    collection_labour_cost = day_rate_base * collection_day_count * collection_premium_multiplier
                    labour_cost = delivery_labour_cost + collection_labour_cost
                    labour_label = (
                        f"Day Rate ({int(delivery_crew_size)} crew x {int(trip_days)} day(s) "
                        f"x {money(delivery_labour_day_rate)}, with delivery/collection day premiums applied)"
                    )
                    labour_detail_rows = [
                        {
                            "Component": "Delivery Day Labour",
                            "Value": f"{money(delivery_labour_cost)} - {delivery_premium_label}",
                        },
                        {
                            "Component": "Collection Day Labour",
                            "Value": (
                                f"{money(collection_labour_cost)} - {collection_premium_label} "
                                f"({collection_day_count} collection day(s))"
                            ),
                        },
                    ]
                else:
                    same_day_premium_pct, same_day_premium_label = max(
                        [
                            (float(delivery_premium_pct), f"Delivery leg: {delivery_premium_label}"),
                            (float(collection_premium_pct), f"Collection leg: {collection_premium_label}"),
                        ],
                        key=lambda item: item[0],
                    )
                    same_day_premium_multiplier = 1.0 + (same_day_premium_pct / 100.0)
                    labour_cost = day_rate_base * same_day_premium_multiplier
                    labour_label = (
                        f"Day Rate ({int(delivery_crew_size)} crew x 1 day x "
                        f"{money(delivery_labour_day_rate)}, highest same-day premium applied once)"
                    )
                    labour_detail_rows = [
                        {
                            "Component": "Same-Day Labour Premium",
                            "Value": (
                                f"{money(labour_cost)} - "
                                + (same_day_premium_label if same_day_premium_pct > 0 else "no premium")
                            ),
                        }
                    ]

            labour_cost_before_short_notice = float(labour_cost)
            short_notice_addon = 0.0
            short_notice_status = "not applied: price quote mode is not a confirmed booking"
            document_mode_label = str(locals().get("document_mode", "") or "")
            is_confirmed_order_context = document_mode_label != "Price Quote (no impact)"
            if is_confirmed_order_context:
                confirmed_at_value = ""
                try:
                    current_meta = invoice_meta_by_number(str(invoice_number or "").strip())
                    if isinstance(current_meta, dict):
                        confirmed_at_value = str(current_meta.get("confirmed_at", "") or "").strip()
                except Exception:
                    confirmed_at_value = ""
                order_placed_dt = parse_jamaica_datetime(confirmed_at_value) or datetime.now(
                    tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
                )
                short_notice_addon, _hours_notice, short_notice_status = short_notice_premium_for_labour(
                    labour_cost_before_short_notice,
                    delivery_departure_dt,
                    order_placed_dt,
                    short_notice_threshold_hours,
                    short_notice_premium_pct,
                    short_notice_minimum_fee,
                )
                if short_notice_addon > 0:
                    labour_cost = round(labour_cost_before_short_notice + short_notice_addon, 2)
                    labour_label = f"{labour_label}; Short Notice +{money(short_notice_addon)}"

            toll_cost = 0.0
            toll_detail_rows: list[dict[str, object]] = []
            toll_notes: list[str] = []
            if one_way_km > 0 and not route_error and vehicle_rows:
                toll_cost, toll_detail_rows, toll_notes = calculate_delivery_tolls(
                    origin_address,
                    destination_address,
                    get_google_maps_api_key(),
                    delivery_departure_dt,
                    collection_departure_dt,
                    int(round_trip_count),
                    vehicle_rows,
                    route_toll_presets_table,
                    ew_table,
                )
            wear_cost = float(wear_per_vehicle_km) * float(total_delivery_km) * len(vehicle_rows)
            risk_basis, risk_is_fragile = fragile_subtotal_or_order_total(preview_items, adjusted_rental_subtotal)
            risk_cost = float(risk_basis) * (float(risk_buffer_percent) / 100.0)
            delivery_subtotal_before_margin = fuel_cost + labour_cost + wear_cost + toll_cost + risk_cost
            margin_cost = delivery_subtotal_before_margin * (float(delivery_margin_percent) / 100.0)
            raw_calculated_delivery_amount = round(delivery_subtotal_before_margin + margin_cost, 2)
            calculated_delivery_amount = (
                float(math.ceil(raw_calculated_delivery_amount / 500.0) * 500.0)
                if raw_calculated_delivery_amount > 0
                else 0.0
            )

            internal_breakdown = pd.DataFrame(
                [
                    {"Component": "One-way Distance", "Value": f"{one_way_km:,.2f} km"},
                    {"Component": "Delivery Drive Time (traffic)", "Value": f"{one_way_min:,.0f} min"},
                    {"Component": "Delivery Drive Time (free-flow)", "Value": f"{free_flow_min:,.0f} min"},
                    {"Component": "Collection Drive Time (traffic)", "Value": f"{collection_min:,.0f} min"},
                    {"Component": "Collection Drive Time (free-flow)", "Value": f"{collection_free_flow_min:,.0f} min"},
                    {"Component": "Total Delivery Distance", "Value": f"{total_delivery_km:,.2f} km"},
                    {
                        "Component": "Fuel",
                        "Value": (
                            f"{money(fuel_cost)}"
                            + (f" ({'; '.join(delivery_efficiency_notes)})" if delivery_efficiency_notes else "")
                        ),
                    },
                    {"Component": "Labour Billing", "Value": labour_label},
                    {"Component": "Labour Before Short Notice", "Value": money(labour_cost_before_short_notice)},
                    {"Component": "Short Notice Premium", "Value": short_notice_status},
                    {"Component": "Labour", "Value": money(labour_cost)},
                    *labour_detail_rows,
                    {
                        "Component": "Hourly Labour Time",
                        "Value": (
                            f"{hourly_labour_hours:,.2f} trip-hour(s) before crew multiplier "
                            f"({loading_buffer_minutes:,.0f} min buffer/round trip)"
                        ),
                    },
                    {
                        "Component": "Vehicle Wear",
                        "Value": (
                            f"{money(wear_cost)} "
                            f"({total_delivery_km:,.2f} km x {len(vehicle_rows)} vehicle(s) "
                            f"x {money(wear_per_vehicle_km)}/km)"
                        ),
                    },
                    {"Component": "Tolls", "Value": money(toll_cost)},
                    {
                        "Component": "Risk / Breakage Buffer",
                        "Value": f"{money(risk_cost)} ({'fragile items' if risk_is_fragile else 'whole order'})",
                    },
                    {"Component": "Margin", "Value": money(margin_cost)},
                    {"Component": "Suggested Delivery Fee", "Value": money(calculated_delivery_amount)},
                ]
            )
            st.dataframe(internal_breakdown, hide_index=True, use_container_width=True)
            if collection_departure_caption_saved and one_way_km > 0 and int(round_trip_count) >= 2:
                st.caption(collection_departure_caption_saved)
            if fuel_detail_rows:
                with st.expander("Traffic-adjusted fuel efficiency details", expanded=False):
                    st.dataframe(pd.DataFrame(fuel_detail_rows), hide_index=True, use_container_width=True)
                    st.caption(
                        "Fuel efficiency is reduced only when Google returns traffic-aware duration above free-flow duration. "
                        "Vehicle Wear remains distance-only and is not traffic-adjusted."
                    )
            if toll_detail_rows:
                with st.expander("Toll calculation details", expanded=False):
                    st.dataframe(pd.DataFrame(toll_detail_rows), hide_index=True, use_container_width=True)
                    for note in toll_notes:
                        st.caption(note)
                    st.caption(
                        "Manual Route Toll Presets are checked first. Google toll pricing is only a fallback "
                        "and should not be trusted for known Jamaica toll routes until verified."
                    )
            st.caption(
                f"Suggested Delivery Fee is rounded up to nearest JM$500 "
                f"from {money(raw_calculated_delivery_amount)}."
            )
            if int(round_trip_count) >= 2 and not bool(
                st.session_state.get("invoice_auto_setup_include_breakdown_input", True)
            ):
                st.warning(
                    "This job includes a collection trip, but breakdown labour isn't included in the Set-Up Fee. "
                    "If your crew is repacking equipment on-site, consider enabling it."
                )
            st.button(
                "Use this delivery amount",
                key="invoice_auto_delivery_use_amount_btn",
                type="secondary",
                on_click=lambda amount=calculated_delivery_amount: st.session_state.update(
                    {"invoice_delivery_manual_amount_input": float(amount)}
                ),
                disabled=calculated_delivery_amount <= 0 or bool(route_error) or one_way_km <= 0,
            )

    if setup_fee_mode == "Calculate Automatically":
        with st.expander("Automatic Set-Up Calculator", expanded=True):
            setup_calc_table = load_setup_minutes_table()
            sc1, sc2, sc3 = st.columns(3)
            setup_crew_size = sc1.number_input(
                "Crew Size",
                min_value=1,
                step=1,
                value=int(setup_crew_size_default),
                key="invoice_auto_setup_crew_size_input",
            )
            include_breakdown_labour = sc2.toggle(
                "Include breakdown/collection labour",
                value=True,
                key="invoice_auto_setup_include_breakdown_input",
            )
            setup_rate_override = sc3.number_input(
                "Hourly Labour Rate Override (JMD)",
                min_value=0.0,
                step=250.0,
                value=float(setup_effective_hourly_rate),
                key="invoice_auto_setup_hourly_rate_input",
            )
            st.caption(
                f"Default setup rate: {money(setup_labour_cost_rate)} cost/hr + "
                f"{float(setup_margin_pct):.1f}% margin = {money(setup_effective_hourly_rate)} / hr. "
                "The override replaces this final hourly rate for this quote only."
            )
            setup_rows = []
            total_setup_minutes = 0.0
            for _, row in preview_items.iterrows():
                qty = float(row.get("quantity", 0.0) or 0.0)
                if qty <= 0:
                    continue
                category, minutes_per_unit = item_category_minutes(str(row.get("item_name", "")), setup_calc_table)
                line_minutes = qty * float(minutes_per_unit)
                total_setup_minutes += line_minutes
                setup_rows.append(
                    {
                        "Item": str(row.get("item_name", "")),
                        "Qty": qty,
                        "Matched Category": category,
                        "Minutes / Unit": float(minutes_per_unit),
                        "Estimated Minutes": round(line_minutes, 2),
                    }
                )
            if include_breakdown_labour:
                total_setup_minutes *= 1.0 + float(setup_collection_multiplier)
            setup_hours = total_setup_minutes / 60.0
            estimated_completion_minutes = total_setup_minutes / max(int(setup_crew_size), 1)
            calculated_setup_amount = round(setup_hours * float(setup_rate_override), 2)
            if setup_rows:
                st.dataframe(pd.DataFrame(setup_rows), hide_index=True, use_container_width=True)
            st.caption(
                f"Internal setup estimate -> Labour minutes: {total_setup_minutes:,.1f} | Billable hours: {setup_hours:,.2f} | "
                f"Suggested Set-Up Fee: {money(calculated_setup_amount)}"
            )
            st.caption(
                f"Estimated completion: ~{estimated_completion_minutes:,.0f} minutes with a crew of {int(setup_crew_size)}. "
                "Crew size changes completion time only, not the fee."
            )
            st.button(
                "Use this set-up amount",
                key="invoice_auto_setup_use_amount_btn",
                type="secondary",
                on_click=lambda amount=calculated_setup_amount: st.session_state.update(
                    {"invoice_setup_fee_input": float(amount)}
                ),
                disabled=calculated_setup_amount <= 0,
            )

    discount_col1, discount_col2 = st.columns([1.3, 1.7])
    discount_mode = discount_col1.selectbox(
        "Discount Type",
        options=["No Discount", "Discount %", "Discount Amount (JMD)"],
        key="invoice_discount_mode_input",
    )
    discount_percent_input = 0.0
    discount_amount_input = 0.0
    if discount_mode == "Discount %":
        discount_percent_input = discount_col2.number_input(
            "Discount (%)",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            key="invoice_discount_percent_input",
        )
    elif discount_mode == "Discount Amount (JMD)":
        discount_amount_input = discount_col2.number_input(
            "Discount Amount (JMD)",
            min_value=0.0,
            step=100.0,
            key="invoice_discount_amount_input",
        )
    else:
        discount_col2.caption("No discount applied.")
    delivery_amount = float(delivery_manual_amount)
    setup_amount = float(setup_fee_amount)
    pre_discount_total = float(adjusted_rental_subtotal + setup_amount + delivery_amount)
    if discount_mode == "Discount %":
        discount_amount = round(pre_discount_total * (float(discount_percent_input) / 100.0), 2)
    elif discount_mode == "Discount Amount (JMD)":
        discount_amount = float(discount_amount_input)
    else:
        discount_amount = 0.0
    discount_amount = float(min(max(discount_amount, 0.0), pre_discount_total))

    taxable_subtotal = float(max(0.0, pre_discount_total - discount_amount))
    gct_amount = round(taxable_subtotal * 0.15, 2) if apply_gct else 0.0
    estimated_total = float(taxable_subtotal + gct_amount)

    fee_net = float(delivery_amount + setup_amount - discount_amount + gct_amount)
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Base Rental", money(base_subtotal))
    s2.metric(f"Day(s) x{rental_days}", money(adjusted_rental_subtotal))
    s3.metric("Fees/Tax Net", money(fee_net))
    s4.metric("Estimated Total", money(estimated_total))
    st.caption(
        f"Breakdown -> Delivery: {money(delivery_amount)} | Set-Up: {money(setup_amount)} | "
        f"Discount: {money(discount_amount)} | GCT: {money(gct_amount)}"
    )
    if rental_days > 1:
        st.caption(
            f"Day(s) multiplier added: +{money(day_multiplier_amount)} for {rental_days} day(s). "
            "Unit cost stays unchanged."
        )

    st.markdown("**Payment Terms**")
    is_quote_mode = document_mode == "Price Quote (no impact)"
    pay1, pay2 = st.columns([1.2, 1.8])
    real_payment_terms = pay1.radio(
        "Payment Option",
        options=[
            "Paid In Full",
            "50% Deposit (Balance Later)",
            "Cash on Delivery (Balance Due)",
        ],
        key="invoice_real_payment_terms_input",
        horizontal=True,
        help=(
            "Deposit is 50% of final total. Cash on Delivery saves as unpaid with full balance "
            "outstanding until payment is posted."
        ),
    )
    payment_note = pay2.text_input(
        "Payment Note (optional)",
        key="invoice_additional_payment_note_input",
        placeholder="e.g. Deposit received via bank transfer",
    )
    if is_quote_mode:
        st.caption(
            "Quote mode: payment terms are for customer communication only and do not affect Finance Hub/Inventory."
        )

    if real_payment_terms == "50% Deposit (Balance Later)":
        payment_status_for_save = "deposit_paid"
        paid_now_amount = round(float(estimated_total) * 0.5, 2)
        outstanding_amount = round(float(estimated_total) - paid_now_amount, 2)
        st.info(f"Deposit Due Now (50%): {money(paid_now_amount)} | Balance Later: {money(outstanding_amount)}")
        preview_paid_label = "Deposit Due Now"
        preview_balance_label = "Balance Later"
    elif real_payment_terms == "Cash on Delivery (Balance Due)":
        payment_status_for_save = "unpaid"
        paid_now_amount = 0.0
        outstanding_amount = float(estimated_total)
        st.info(
            "Cash on Delivery selected: "
            f"Paid Now: {money(paid_now_amount)} | Balance Due on Delivery: {money(outstanding_amount)}"
        )
        preview_paid_label = "Paid Now"
        preview_balance_label = "Balance Due on Delivery"
    else:
        payment_status_for_save = "paid_full"
        paid_now_amount = float(estimated_total)
        outstanding_amount = 0.0
        st.info(f"Paid In Full selected: {money(paid_now_amount)}")
        preview_paid_label = "Paid Now"
        preview_balance_label = "Balance Outstanding"

    render_step_block(
        "Step 4: Commit Preview + Save",
        "Final check before saving: totals, finance impact, and inventory impact.",
    )
    st.markdown("**Commit Preview**")
    preview_a, preview_b, preview_c = st.columns(3)
    preview_a.metric("Estimated Total", money(float(estimated_total)))
    preview_b.metric(preview_paid_label, money(float(paid_now_amount)))
    preview_c.metric(preview_balance_label, money(float(outstanding_amount)))

    if document_mode == "Price Quote (no impact)":
        finance_impact_text = "Quote only: no Finance Hub or inventory impact."
    else:
        finance_impact_text = (
            "Confirmed order: Finance Hub totals update now and inventory movement is applied automatically."
        )
    st.info(f"Finance impact: {finance_impact_text}")

    def _render_last_saved_invoice_quick_actions() -> None:
        latest_saved_id = st.session_state.get("invoice_last_saved_id")
        st.markdown("**Quick Actions: Last Saved Invoice**")
        st.caption("These actions target the latest invoice/quote saved in this session.")

        latest_payload: Dict[str, Any] = {}
        latest_file_stub = "invoice"
        latest_pdf_bytes = b""
        latest_png_bytes = b""
        latest_doc_label = "Invoice"
        quick_actions_ready = False

        if latest_saved_id is not None:
            quick_ready_id = st.session_state.get("invoice_quick_actions_ready_id")
            quick_payload = st.session_state.get("invoice_quick_payload")
            if (
                quick_ready_id is not None
                and int(quick_ready_id) == int(latest_saved_id)
                and isinstance(quick_payload, dict)
            ):
                latest_payload = quick_payload
                latest_file_stub = str(st.session_state.get("invoice_quick_file_stub", "") or "invoice")
                latest_pdf_bytes = st.session_state.get("invoice_quick_pdf_bytes", b"")
                latest_png_bytes = st.session_state.get("invoice_quick_png_bytes", b"")
                latest_doc_label = document_label_from_type(
                    latest_payload.get("document_type", "invoice")
                )
                quick_actions_ready = bool(latest_pdf_bytes) and bool(latest_png_bytes)
            else:
                try:
                    latest_header, latest_items = invoice_export_bundle(int(latest_saved_id))
                    latest_currency_code = get_profile_setting("currency", "JMD").strip().upper()
                    latest_currency_symbol = "JM$" if latest_currency_code == "JMD" else "$"
                    latest_payload = build_invoice_payload(
                        header=latest_header,
                        items=latest_items,
                        business_name=get_profile_setting("business_name", "Headline Rentals"),
                        currency=latest_currency_symbol,
                        bank_info=DEFAULT_SELLER_BANKING,
                    )
                    latest_doc_label = document_label_from_type(
                        latest_payload.get("document_type", "invoice")
                    )
                    latest_logo = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH else None
                    latest_pdf_bytes = render_invoice_pdf(latest_payload, logo_path=latest_logo)
                    latest_png_bytes = render_invoice_png(latest_payload, logo_path=latest_logo)
                    latest_file_stub = invoice_download_filename(
                        customer_name=str(latest_payload.get("customer_name", "") or ""),
                        invoice_number=str(latest_payload.get("invoice_number", "") or ""),
                        document_label=latest_doc_label,
                    )
                    st.session_state["invoice_quick_actions_ready_id"] = int(latest_saved_id)
                    st.session_state["invoice_quick_payload"] = latest_payload
                    st.session_state["invoice_quick_file_stub"] = latest_file_stub
                    st.session_state["invoice_quick_pdf_bytes"] = latest_pdf_bytes
                    st.session_state["invoice_quick_png_bytes"] = latest_png_bytes
                    quick_actions_ready = bool(latest_pdf_bytes) and bool(latest_png_bytes)
                except Exception as exc:
                    st.error(f"Quick actions unavailable for latest invoice: {exc}")

        q1, q2, q3 = st.columns([1, 1, 1.6])
        q1.download_button(
            "Quick Download PDF",
            data=latest_pdf_bytes,
            file_name=f"{latest_file_stub}.pdf",
            mime="application/pdf",
            key=f"quick_invoice_pdf_{int(latest_saved_id)}" if latest_saved_id is not None else "quick_invoice_pdf_empty",
            disabled=not quick_actions_ready,
        )
        q2.download_button(
            "Quick Download PNG",
            data=latest_png_bytes,
            file_name=f"{latest_file_stub}.png",
            mime="image/png",
            key=f"quick_invoice_png_{int(latest_saved_id)}" if latest_saved_id is not None else "quick_invoice_png_empty",
            disabled=not quick_actions_ready,
        )
        if quick_actions_ready:
            q3.markdown(
                f"**Invoice:** {latest_payload.get('invoice_number', '')}  \n"
                f"**Total:** {money(float(latest_payload.get('total', 0.0)))}"
            )
        else:
            q3.caption("Save an invoice/quote first, then download buttons will activate.")

        if quick_actions_ready:
            latest_payment_status = str(
                latest_payload.get("payment_status", "paid_full")
            ).strip().lower()
            if latest_payment_status == "deposit_paid":
                q3.caption(
                    "Deposit Due Now (50%): "
                    f"{money(float(latest_payload.get('deposit_due_now', 0.0)))}"
                )
            elif latest_payment_status == "unpaid":
                q3.caption(
                    "Amount Due on Delivery: "
                    f"{money(float(latest_payload.get('total', 0.0)))}"
                )

            latest_country_code = (
                get_delivery_setting("default_country_code", "1").strip() or "1"
            )
            resolved_latest_contact = resolve_contact_channels(
                customer_phone=str(latest_payload.get("customer_phone", "")).strip(),
                customer_email=str(latest_payload.get("customer_email", "")).strip(),
                contact_detail="",
            )
            latest_phone_default = str(resolved_latest_contact.get("phone", "")).strip()
            latest_email_default = str(resolved_latest_contact.get("email", "")).strip()
            latest_subject = (
                f"{latest_payload.get('business_name', 'Headline Rentals')} "
                f"{latest_doc_label} #{latest_payload.get('invoice_number', '')}"
            )
            latest_extra_note = ""
            if latest_payment_status == "deposit_paid":
                latest_extra_note = (
                    "Deposit Due Now (50%): "
                    f"{latest_payload.get('currency', 'JM$')}{float(latest_payload.get('deposit_due_now', 0.0)):,.2f}"
                )
            elif latest_payment_status == "unpaid":
                latest_extra_note = (
                    "Amount Due on Delivery: "
                    f"{latest_payload.get('currency', 'JM$')}{float(latest_payload.get('total', 0.0)):,.2f}"
                )
            latest_message = build_invoice_message(
                business_name=str(latest_payload.get("business_name", "Headline Rentals")),
                invoice_number=str(latest_payload.get("invoice_number", "")),
                document_label=latest_doc_label,
                event_date=str(latest_payload.get("event_date", "")),
                event_time=str(latest_payload.get("event_time", "")),
                total_display=(
                    f"{latest_payload.get('currency', 'JM$')}"
                    f"{float(latest_payload.get('total', 0.0)):,.2f}"
                ),
                review_link="",
                extra_note=latest_extra_note,
            )

            s1, s2 = st.columns(2)
            latest_send_phone = s1.text_input(
                "Quick Recipient Phone",
                value=latest_phone_default,
                key=f"quick_send_phone_{int(latest_saved_id)}",
            )
            latest_send_email = s2.text_input(
                "Quick Recipient Email",
                value=latest_email_default,
                key=f"quick_send_email_{int(latest_saved_id)}",
            )
            latest_send_message = st.text_area(
                "Quick Message",
                value=latest_message,
                height=110,
                key=f"quick_send_msg_{int(latest_saved_id)}",
            )

            latest_whatsapp_digits = normalize_whatsapp_to(
                latest_send_phone,
                default_country_code=latest_country_code,
            )
            latest_wa_url = (
                whatsapp_link(latest_send_phone, latest_send_message)
                if latest_whatsapp_digits
                else ""
            )
            latest_gmail_url = (
                gmail_compose_link(latest_send_email, latest_subject, latest_send_message)
                if latest_send_email.strip()
                else ""
            )

            l1, l2 = st.columns(2)
            if latest_wa_url:
                l1.link_button(
                    "Quick Send WhatsApp",
                    latest_wa_url,
                    use_container_width=True,
                )
            else:
                l1.info("Add phone")
            if latest_gmail_url:
                l2.link_button(
                    "Quick Send Email",
                    latest_gmail_url,
                    use_container_width=True,
                )
            else:
                l2.info("Add email")

    _render_last_saved_invoice_quick_actions()

    preview_products = preview_items[
        preview_items["item_type"].astype(str).str.lower().eq("product")
    ].copy()
    if not preview_products.empty:
        preview_products["match_key"] = preview_products["item_name"].apply(
            lambda x: " ".join(sorted(auto_quote_keywords(str(x)))) or str(x).strip().lower()
        )
        preview_products["quantity"] = pd.to_numeric(
            preview_products["quantity"], errors="coerce"
        ).fillna(0.0)
        req_by_key = (
            preview_products.groupby("match_key", as_index=False)
            .agg(required_qty=("quantity", "sum"), item_name=("item_name", "first"))
        )

        inv = inventory.copy() if isinstance(inventory, pd.DataFrame) else pd.DataFrame()
        overlap_by_key = pd.DataFrame(columns=["match_key", "reserved_overlap"])
        if not inv.empty:
            inv["match_key"] = inv["item_name"].apply(
                lambda x: " ".join(sorted(auto_quote_keywords(str(x)))) or str(x).strip().lower()
            )
            inv["current_quantity"] = pd.to_numeric(
                inv["current_quantity"], errors="coerce"
            ).fillna(0.0)
            stock_by_key = (
                inv.groupby("match_key", as_index=False)
                .agg(stock_quantity=("current_quantity", "sum"))
            )

            start_naive = pd.to_datetime(
                f"{event_date.isoformat()} {to_time_string(event_time_value)}",
                errors="coerce",
            )
            end_naive = (
                start_naive + pd.to_timedelta(float(rental_hours), unit="h")
                if pd.notna(start_naive)
                else pd.NaT
            )
            allocations = cached_event_product_allocations()
            if (
                isinstance(allocations, pd.DataFrame)
                and not allocations.empty
                and pd.notna(start_naive)
                and pd.notna(end_naive)
            ):
                allocations = allocations.copy()
                allocations["match_key"] = allocations["item_name"].apply(
                    lambda x: " ".join(sorted(auto_quote_keywords(str(x)))) or str(x).strip().lower()
                )
                allocations["required_qty"] = pd.to_numeric(
                    allocations["required_qty"], errors="coerce"
                ).fillna(0.0)
                overlap = allocations[
                    (allocations["start_dt"] < end_naive)
                    & (allocations["end_dt"] > start_naive)
                ].copy()
                if not overlap.empty:
                    overlap_by_key = (
                        overlap.groupby("match_key", as_index=False)
                        .agg(reserved_overlap=("required_qty", "sum"))
                    )

            impact_table = (
                req_by_key.merge(stock_by_key, on="match_key", how="left")
                .merge(overlap_by_key, on="match_key", how="left")
            )
            impact_table["stock_quantity"] = pd.to_numeric(
                impact_table["stock_quantity"], errors="coerce"
            ).fillna(0.0)
            impact_table["reserved_overlap"] = pd.to_numeric(
                impact_table["reserved_overlap"], errors="coerce"
            ).fillna(0.0)
            impact_table["available_before"] = (
                impact_table["stock_quantity"] - impact_table["reserved_overlap"]
            )
            impact_table["projected_after"] = (
                impact_table["available_before"] - impact_table["required_qty"]
            )
            impact_table["status"] = impact_table["projected_after"].apply(
                lambda x: "Shortfall" if float(x) < 0 else "OK"
            )

            st.markdown("**Inventory Impact Preview (Selected Date/Duration)**")
            show = impact_table[
                [
                    "item_name",
                    "required_qty",
                    "stock_quantity",
                    "reserved_overlap",
                    "available_before",
                    "projected_after",
                    "status",
                ]
            ].copy()
            for col in [
                "required_qty",
                "stock_quantity",
                "reserved_overlap",
                "available_before",
                "projected_after",
            ]:
                show[col] = pd.to_numeric(show[col], errors="coerce").fillna(0.0).map(
                    lambda x: f"{float(x):g}"
                )
            st.dataframe(show, hide_index=True, use_container_width=True)
            if "Shortfall" in impact_table["status"].values:
                st.error(
                    "One or more items are projected to shortfall at this event window. Adjust quantity or stock before confirming."
                )
        else:
            st.caption("Inventory impact preview unavailable until inventory items are loaded.")

    submitted = st.button("Save Invoice", key="invoice_save_button")

    if submitted:
        if not invoice_number.strip():
            st.error("Invoice number is required.")
        else:
            try:
                base_items = normalize_invoice_items_df(items)
                reserved_step2_rows = sorted(
                    {
                        str(raw).strip()
                        for raw in base_items.loc[
                            base_items["item_name"].apply(is_step2_reserved_fee_name),
                            "item_name",
                        ].tolist()
                        if str(raw).strip()
                    }
                )
                if reserved_step2_rows:
                    raise ValueError(
                        "Step 2 item lines cannot include GCT, Delivery, or Discount. "
                        "Use Step 3 (Fees + Tax) for those values."
                    )
                base_items = base_items[
                    ~base_items["item_name"].apply(is_auto_fee_item_name)
                ].copy()
                base_items = base_items[
                    (base_items["item_name"].str.strip() != "")
                    & (base_items["quantity"] > 0)
                ].copy()

                auto_fee_rows: list[dict] = []
                if float(day_multiplier_amount) > 0:
                    auto_fee_rows.append(
                        {
                            "item_name": f"Day(s) x{int(max(1, rental_days))}",
                            "item_type": "service",
                            "quantity": 1.0,
                            "unit_price": float(day_multiplier_amount),
                            "unit_cost": 0.0,
                        }
                    )
                if float(delivery_amount) > 0:
                    auto_fee_rows.append(
                        {
                            "item_name": "Delivery Fee",
                            "item_type": "service",
                            "quantity": 1.0,
                            "unit_price": float(delivery_amount),
                            "unit_cost": 0.0,
                        }
                    )
                if float(setup_amount) > 0:
                    auto_fee_rows.append(
                        {
                            "item_name": "Set-Up Fee",
                            "item_type": "service",
                            "quantity": 1.0,
                            "unit_price": float(setup_amount),
                            "unit_cost": 0.0,
                        }
                    )
                if float(discount_amount) > 0:
                    auto_fee_rows.append(
                        {
                            "item_name": "Discount",
                            "item_type": "service",
                            "quantity": 1.0,
                            "unit_price": float(-abs(discount_amount)),
                            "unit_cost": 0.0,
                        }
                    )
                if apply_gct and float(gct_amount) > 0:
                    auto_fee_rows.append(
                        {
                            "item_name": "GCT (15%)",
                            "item_type": "service",
                            "quantity": 1.0,
                            "unit_price": float(gct_amount),
                            "unit_cost": 0.0,
                        }
                    )

                if auto_fee_rows:
                    items_to_save = pd.concat(
                        [base_items, pd.DataFrame(auto_fee_rows)],
                        ignore_index=True,
                    )
                else:
                    items_to_save = base_items

                items_to_save = items_to_save[
                    ["item_name", "item_type", "quantity", "unit_price", "unit_cost"]
                ].copy()

                loaded_invoice_number = str(
                    st.session_state.get("invoice_builder_loaded_invoice_number", "") or ""
                ).strip()
                allow_builder_overwrite = bool(
                    st.session_state.get("invoice_builder_allow_overwrite", False)
                ) and bool(loaded_invoice_number) and (
                    invoice_number.strip().lower() == loaded_invoice_number.lower()
                )

                if document_mode == "Price Quote (no impact)":
                    document_type = "quote"
                    order_status = "pending"
                    payment_status = payment_status_for_save
                    amount_paid_value = float(paid_now_amount)
                    payment_notes_value = payment_note.strip()
                else:
                    document_type = "invoice"
                    order_status = "confirmed"
                    payment_status = payment_status_for_save
                    amount_paid_value = float(paid_now_amount)
                    payment_notes_value = payment_note.strip()

                saved_invoice_id = save_invoice(
                    {
                        "invoice_number": invoice_number.strip(),
                        "event_date": event_date,
                        "event_time": to_time_string(event_time_value),
                        "rental_hours": float(rental_hours),
                        "event_timezone": DEFAULT_EVENT_TIMEZONE,
                        "event_location": event_location,
                        "document_type": document_type,
                        "order_status": order_status,
                        "created_by": created_by,
                        "source_device": detected_device,
                        "customer_name": customer_name,
                        "customer_phone": customer_phone,
                        "customer_email": customer_email,
                        "delivered_to": delivered_to,
                        "paid_to": paid_to,
                        "payment_status": payment_status,
                        "amount_paid": amount_paid_value,
                        "deposit_balance_enabled": (
                            real_payment_terms == "50% Deposit (Balance Later)"
                        ),
                        "payment_notes": payment_notes_value,
                        "notes": notes,
                    },
                    items_to_save,
                    allow_overwrite=allow_builder_overwrite,
                )
                st.session_state["invoice_last_saved_id"] = int(saved_invoice_id)
                st.session_state["invoice_export_selected_id"] = int(saved_invoice_id)
                st.session_state["invoice_quick_actions_ready_id"] = None
                st.session_state["invoice_quick_payload"] = None
                st.session_state["invoice_quick_file_stub"] = ""
                st.session_state["invoice_quick_pdf_bytes"] = b""
                st.session_state["invoice_quick_png_bytes"] = b""
                st.session_state["invoice_builder_allow_overwrite"] = True
                st.session_state["invoice_builder_loaded_invoice_number"] = invoice_number.strip()

                post_messages: list[dict[str, str]] = []
                if document_type == "quote":
                    post_messages.append(
                        {
                            "level": "success",
                            "text": (
                                f"Price quote {invoice_number.strip()} saved "
                                "(no finance/inventory impact)."
                            ),
                        }
                    )
                else:
                    post_messages.append(
                        {
                            "level": "success",
                            "text": (
                                f"Confirmed order {invoice_number.strip()} saved. "
                                "Inventory movement entries were added automatically."
                            ),
                        }
                    )
                level, message = invoice_due_message(event_date)
                if document_type == "invoice" and order_status == "confirmed":
                    if level == "warning":
                        post_messages.append(
                            {"level": "warning", "text": f"Upcoming Event Alert: {message}"}
                        )
                    else:
                        post_messages.append({"level": "info", "text": f"Event Timeline: {message}"})
                else:
                    post_messages.append(
                        {
                            "level": "info",
                            "text": "Quote mode: this document does not impact Finance Hub or Inventory.",
                        }
                    )
                post_messages.append(
                    {
                        "level": "info",
                        "text": (
                            f"Auto fees saved -> Day Multiplier: {money(day_multiplier_amount)} | "
                            f"Delivery: {money(delivery_amount)} | "
                            f"Set-Up: {money(setup_amount)} | Discount: {money(discount_amount)} | "
                            f"GCT: {money(gct_amount)}"
                        ),
                    }
                )
                if (
                    document_type == "invoice"
                    and payment_status in {"deposit_paid", "unpaid"}
                    and float(outstanding_amount) > 0
                ):
                    if payment_status == "deposit_paid":
                        post_messages.append(
                            {
                                "level": "warning",
                                "text": f"Finance reminder: deposit logged ({money(amount_paid_value)}).",
                            }
                        )
                    else:
                        post_messages.append(
                            {
                                "level": "warning",
                                "text": (
                                    "Finance reminder: Cash on Delivery order saved with "
                                    f"balance due {money(float(outstanding_amount))}."
                                ),
                            }
                        )
                st.session_state["invoice_parse_warnings"] = []
                st.session_state["invoice_post_save_messages"] = post_messages
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save invoice: {exc}")

    with st.expander("Invoice Utilities", expanded=False):
        util_tab_tools, util_tab_attach, util_tab_log = st.tabs(
            [
                "Post-Preview Tools",
                "Attach Files (PNG/PDF) to Invoices",
                "Build Log (Quotes + Invoices)",
            ]
        )

        with util_tab_tools:
            st.caption("Use these after checking inventory impact.")
            tool_tab1, tool_tab2, tool_tab3 = st.tabs(
                ["Bundle Builder", "Saved Price Quotes", "Quick Intake PDF"]
            )
            with tool_tab1:
                _render_bundle_builder_block(items_frame=items, default_items_frame=default_items)
            with tool_tab2:
                _render_saved_price_quotes_block()
            with tool_tab3:
                _render_quick_intake_block()

        with util_tab_attach:
            inv = cached_invoice_options()
            if inv.empty:
                st.caption("Save at least one invoice first to attach files.")
            else:
                attach_labels = {
                    (
                        f"[{str(row.get('document_type','invoice')).upper()}/{str(row.get('order_status','confirmed')).upper()}] "
                        f"{row['invoice_number']} | "
                        f"{row['event_date'] if row['event_date'] else 'No Date'}"
                        f"{(' ' + row['event_time']) if row.get('event_time', '') else ''} | "
                        f"{row['customer_name'] if row['customer_name'] else 'No Customer'}"
                    ): int(row["id"])
                    for _, row in inv.iterrows()
                }
                selected_attach_label = st.selectbox(
                    "Choose invoice for attachment",
                    options=list(attach_labels.keys()),
                    key="attach_invoice_selector",
                )
                selected_invoice_id = attach_labels[selected_attach_label]
                uploads = st.file_uploader(
                    "Upload attachment(s)",
                    type=["png", "jpg", "jpeg", "pdf"],
                    accept_multiple_files=True,
                    key="invoice_attachments_upload",
                )
                if st.button("Save Attachments", key="save_invoice_attachments"):
                    if not uploads:
                        st.error("Select one or more files first.")
                    else:
                        saved_count = 0
                        for file in uploads:
                            original = file.name or "attachment"
                            name = safe_filename(original)
                            target = ATTACHMENTS_DIR / f"{selected_invoice_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{name}"
                            target.write_bytes(file.getvalue())
                            suffix = target.suffix.lower().lstrip(".")
                            file_type = "image" if suffix in {"png", "jpg", "jpeg"} else "pdf"
                            add_invoice_attachment(
                                invoice_id=selected_invoice_id,
                                file_path=str(target),
                                file_type=file_type,
                                original_name=original,
                                notes="Uploaded from invoice attachments",
                            )
                            saved_count += 1
                        st.success(f"{saved_count} attachment(s) saved.")

                existing_files = load_invoice_attachments(selected_invoice_id)
                if existing_files.empty:
                    st.caption("No attachments on this invoice yet.")
                else:
                    preview = existing_files.copy()
                    st.dataframe(
                        preview[["original_name", "file_type", "file_path", "created_at"]],
                        hide_index=True,
                        use_container_width=True,
                    )
                    attachment_labels = {
                        (
                            f"[{str(row.get('file_type', '')).upper()}] "
                            f"{str(row.get('original_name', '')).strip()} | "
                            f"{str(row.get('created_at', '')).strip()}"
                        ): int(row["id"])
                        for _, row in preview.iterrows()
                    }
                    remove_col1, remove_col2 = st.columns([1.8, 1.2])
                    selected_attachment_label = remove_col1.selectbox(
                        "Select attachment to unattach",
                        options=list(attachment_labels.keys()),
                        key="remove_invoice_attachment_selector",
                    )
                    if remove_col2.button("Unattach Selected File", key="remove_invoice_attachment_btn"):
                        attachment_id = int(attachment_labels[selected_attachment_label])
                        try:
                            deleted_attachment = delete_invoice_attachment(attachment_id)
                            path_text = str(deleted_attachment.get("file_path", "") or "").strip()
                            if path_text:
                                target_path = Path(path_text)
                                try:
                                    if target_path.exists() and ATTACHMENTS_DIR in target_path.parents:
                                        target_path.unlink()
                                except Exception:
                                    pass
                            st.success("Attachment removed from this invoice.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not unattach file: {exc}")
                    first_image = preview[preview["file_type"] == "image"].head(1)
                    if not first_image.empty:
                        image_path = str(first_image.iloc[0]["file_path"])
                        if Path(image_path).exists():
                            st.image(image_path, caption=first_image.iloc[0]["original_name"], width=320)

        with util_tab_log:
            build_log = load_invoice_build_log(limit=300)
            if build_log.empty:
                st.caption("No quote/invoice build activity yet.")
            else:
                log_show = build_log.copy()
                log_show["document_type"] = log_show["document_type"].str.upper()
                log_show["order_status"] = log_show["order_status"].str.upper()
                log_show["actor"] = log_show["actor_name"].where(
                    log_show["actor_name"].fillna("").str.strip() != "",
                    log_show["device_name"],
                )
                st.dataframe(
                    log_show[
                        [
                            "created_at",
                            "invoice_number",
                            "document_type",
                            "order_status",
                            "action_type",
                            "actor",
                            "device_name",
                        ]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
                with st.expander("View Full Build Log Details", expanded=False):
                    st.dataframe(log_show, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.info("Finance tables (including Invoice Profit) are now in the separate Finance Hub section.")


def render_inventory_purchases() -> None:
    render_section_shell(
        "Inventory Purchases",
        "Track stock purchases separately from operational expenses and profit.",
    )
    st.caption(
        "Inventory purchases are isolated from Finance Hub expense and profit calculations."
    )

    with st.form("inventory_purchase_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        purchase_date = c1.date_input("Purchase Date", value=date.today())
        amount = c2.number_input(
            "Purchase Amount (JMD) *",
            min_value=0.0,
            step=500.0,
        )
        quantity = c3.number_input(
            "Quantity (optional)",
            min_value=0.0,
            step=1.0,
            value=0.0,
        )
        d1, d2 = st.columns(2)
        item_name = d1.text_input("Item Name", placeholder="e.g. 10x10 Tent")
        vendor = d2.text_input("Supplier / Vendor", placeholder="e.g. Kingston Supplies")
        notes = st.text_input("Notes (optional)")
        submit_purchase = st.form_submit_button("Add Inventory Purchase")

    if submit_purchase:
        if float(amount) <= 0:
            st.error("Purchase amount must be greater than 0.")
        else:
            try:
                purchase_id = add_inventory_purchase(
                    purchase_date=purchase_date.isoformat(),
                    amount=float(amount),
                    item_name=item_name.strip(),
                    vendor=vendor.strip(),
                    quantity=float(quantity),
                    notes=notes.strip(),
                )
                finance_audit_log(
                    entity_type="inventory_purchase",
                    entity_id=purchase_id,
                    action_type="create",
                    notes=(
                        f"{purchase_date.isoformat()} | {item_name.strip() or 'Inventory Purchase'} | "
                        f"{money(float(amount))}"
                    ),
                )
                clear_finance_caches()
                st.success("Inventory purchase recorded.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save inventory purchase: {exc}")

    purchases = cached_inventory_purchases()
    if purchases.empty:
        st.info("No inventory purchases recorded yet.")
        return

    show = purchases.copy()
    show["purchase_date"] = pd.to_datetime(show["purchase_date"], errors="coerce")
    show = show[show["purchase_date"].notna()].copy()
    if show.empty:
        st.info("No valid inventory purchase dates found.")
        return

    show["month"] = show["purchase_date"].dt.to_period("M").astype("string")
    show["year"] = show["purchase_date"].dt.year
    now_jm = jamaica_now()
    month_key = f"{now_jm.year:04d}-{now_jm.month:02d}"
    year_key = int(now_jm.year)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Purchases", money(float(show["amount"].sum())))
    k2.metric(
        "Current Month",
        money(float(show.loc[show["month"] == month_key, "amount"].sum())),
    )
    k3.metric(
        "Current Year",
        money(float(show.loc[show["year"] == year_key, "amount"].sum())),
    )
    k4.metric("Purchase Entries", str(int(len(show))))

    st.markdown("**Monthly Inventory Purchase Totals**")
    monthly = (
        show.groupby("month", as_index=False)["amount"]
        .sum()
        .sort_values("month")
        .copy()
    )
    monthly["month_label"] = pd.PeriodIndex(monthly["month"], freq="M").strftime("%b %Y")
    monthly["amount"] = monthly["amount"].map(money)
    render_paginated_dataframe(
        monthly[["month_label", "amount"]].rename(
            columns={
                "month_label": "Month",
                "amount": "Inventory Purchases",
            }
        ),
        key_prefix="inventory_purchases_monthly",
        page_size_default=12,
    )

    st.markdown("**Recent Inventory Purchases**")
    recent = show.sort_values("purchase_date", ascending=False).copy()
    recent["purchase_date"] = recent["purchase_date"].dt.date.astype("string")
    recent["amount"] = pd.to_numeric(recent["amount"], errors="coerce").fillna(0.0)
    recent_display = recent.copy()
    recent_display["amount"] = recent_display["amount"].map(money)
    render_paginated_dataframe(
        recent_display[
            [
                "id",
                "purchase_date",
                "item_name",
                "vendor",
                "quantity",
                "amount",
                "notes",
            ]
        ].rename(
            columns={
                "id": "Purchase ID",
                "purchase_date": "Date",
                "item_name": "Item",
                "vendor": "Supplier",
                "quantity": "Qty",
                "amount": "Amount",
                "notes": "Notes",
            }
        ),
        key_prefix="inventory_purchases_recent",
        page_size_default=15,
    )

    st.markdown("---")
    show_manage = st.toggle(
        "Show Manage Inventory Purchases (Edit/Delete)",
        value=False,
        key="inventory_purchase_manage_toggle",
    )
    if not show_manage:
        return

    st.markdown("**Manage Inventory Purchases (Edit/Delete)**")
    purchase_choice_map: dict[str, int] = {}
    for _, row in recent.iterrows():
        purchase_id = int(row["id"])
        purchase_date_text = (
            row["purchase_date"].isoformat()
            if hasattr(row["purchase_date"], "isoformat")
            else str(row["purchase_date"])
        )
        item_text = str(row.get("item_name", "") or "").strip() or "Inventory Purchase"
        purchase_choice_map[
            f"#{purchase_id} | {purchase_date_text} | {item_text} | {money(float(row.get('amount', 0.0) or 0.0))}"
        ] = purchase_id

    selected_purchase_label = st.selectbox(
        "Select Inventory Purchase",
        options=list(purchase_choice_map.keys()),
        key="inventory_purchase_manage_selector",
    )
    selected_purchase_id = int(purchase_choice_map[selected_purchase_label])
    selected_purchase = recent[recent["id"] == selected_purchase_id].iloc[0]
    selected_purchase_date = pd.to_datetime(
        selected_purchase.get("purchase_date"),
        errors="coerce",
    )

    with st.form(f"inventory_purchase_edit_form_{selected_purchase_id}", clear_on_submit=False):
        e1, e2, e3 = st.columns(3)
        edited_date = e1.date_input(
            "Purchase Date",
            value=selected_purchase_date.date() if pd.notna(selected_purchase_date) else date.today(),
        )
        edited_amount = e2.number_input(
            "Amount (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(selected_purchase.get("amount", 0.0) or 0.0),
        )
        edited_quantity = e3.number_input(
            "Quantity",
            min_value=0.0,
            step=1.0,
            value=float(selected_purchase.get("quantity", 0.0) or 0.0),
        )
        f1, f2 = st.columns(2)
        edited_item_name = f1.text_input(
            "Item Name",
            value=str(selected_purchase.get("item_name", "") or ""),
        )
        edited_vendor = f2.text_input(
            "Supplier / Vendor",
            value=str(selected_purchase.get("vendor", "") or ""),
        )
        edited_notes = st.text_input(
            "Notes",
            value=str(selected_purchase.get("notes", "") or ""),
        )
        save_purchase = st.form_submit_button("Save Purchase Changes")

    if save_purchase:
        if float(edited_amount) <= 0:
            st.error("Purchase amount must be greater than 0.")
        else:
            try:
                update_inventory_purchase(
                    purchase_id=selected_purchase_id,
                    purchase_date=edited_date.isoformat(),
                    amount=float(edited_amount),
                    item_name=edited_item_name.strip(),
                    vendor=edited_vendor.strip(),
                    quantity=float(edited_quantity),
                    notes=edited_notes.strip(),
                )
                finance_audit_log(
                    entity_type="inventory_purchase",
                    entity_id=selected_purchase_id,
                    action_type="update",
                    notes=f"Updated inventory purchase #{selected_purchase_id}",
                )
                clear_finance_caches()
                st.success("Inventory purchase updated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not update inventory purchase: {exc}")

    delete_purchase_ok = st.checkbox(
        "I understand this will permanently delete this inventory purchase.",
        key=f"delete_inventory_purchase_confirm_{selected_purchase_id}",
    )
    if st.button(
        "Delete Selected Inventory Purchase",
        key=f"delete_inventory_purchase_btn_{selected_purchase_id}",
        type="secondary",
    ):
        if not delete_purchase_ok:
            st.error("Please confirm deletion first.")
        else:
            try:
                delete_inventory_purchase(selected_purchase_id)
                finance_audit_log(
                    entity_type="inventory_purchase",
                    entity_id=selected_purchase_id,
                    action_type="delete",
                    notes=f"Deleted inventory purchase #{selected_purchase_id}",
                )
                clear_finance_caches()
                st.success("Inventory purchase deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete inventory purchase: {exc}")


def render_expenses() -> None:
    render_section_shell(
        "Expense Transactions",
        "Record expenses individually with duplicate guardrails and recurring templates.",
    )
    st.caption(
        "Record each expense individually. Use Expense Type to separate recurring monthly costs from transaction-level costs."
    )
    st.info(
        "Date rule: if an expense is linked to an invoice, finance calculations use that invoice's event date/day."
    )
    st.caption(
        "Rule: day-to-day (`Transaction`) expenses must be linked to a confirmed order invoice with an event date."
    )
    st.caption(
        "Stock purchases are managed in `Inventory Purchases` and excluded from expense/profit totals."
    )
    st.caption("Supplier re-rental entries are managed in the separate `Supplier Re-Rental` section.")

    category_options = FINANCE_CATEGORY_OPTIONS.copy()
    labels = invoice_label_map()
    options = list(labels.keys())
    not_linked_label = next(
        (label for label in options if labels.get(label) is None),
        "Not linked to invoice",
    )
    confirmed_invoice_options = [label for label in options if labels[label] is not None]
    invoice_link_options = [not_linked_label] + [
        label for label in confirmed_invoice_options if label != not_linked_label
    ]
    if not invoice_link_options:
        invoice_link_options = [not_linked_label]
    actor = finance_actor_name()
    device = current_device_name()
    show_advanced_expense_controls = st.toggle(
        "Show Advanced Expense Controls",
        value=False,
        key="expenses_advanced_toggle",
        help="Shows duplicate override, summary-rollup type, and manual monthly adjustments.",
    )

    with st.form("expense_form", clear_on_submit=True):
        a1, a2, a3, a4 = st.columns(4)
        expense_date = a1.date_input("Expense Date", value=date.today())
        amount = a2.number_input("Amount (JMD) *", min_value=0.0, step=100.0)
        category = a3.selectbox(
            "Category *",
            category_options,
            key="expense_form_category",
        )
        expense_type_options = [
            "Transaction (invoice/day level)",
            "Recurring Monthly (ChatGPT/Ads/Shopify)",
        ]
        if show_advanced_expense_controls:
            expense_type_options.append("Summary Reference (roll-up only)")
        recommended_kind = recommended_expense_type_label(category)
        prev_category_key = st.session_state.get("expense_form_prev_category", "")
        current_category_key = normalize_expense_category(category).strip().lower()
        if (
            prev_category_key != current_category_key
            or st.session_state.get("expense_form_kind") not in expense_type_options
        ):
            default_kind = recommended_kind if recommended_kind in expense_type_options else expense_type_options[0]
            st.session_state["expense_form_kind"] = default_kind
            st.session_state["expense_form_prev_category"] = current_category_key
        expense_kind_label = a4.selectbox(
            "Expense Type *",
            expense_type_options,
            key="expense_form_kind",
        )
        st.caption(f"Recommended type for {normalize_expense_category(category)}: {recommended_kind}")

        b1, b2 = st.columns(2)
        vendor = b1.text_input("Vendor / Person")
        description = b2.text_input("Description")

        link_label = st.selectbox(
            "Link to Invoice (required for Transaction)",
            options=invoice_link_options,
            index=0,
            help="For recurring/summary rows choose Not linked to invoice.",
        )
        if not confirmed_invoice_options:
            st.caption("No confirmed invoices with event date found yet.")
        allow_possible_duplicate = False
        if show_advanced_expense_controls:
            allow_possible_duplicate = st.checkbox(
                "Allow if potential duplicate is detected",
                value=False,
            )
        submit_expense = st.form_submit_button("Add Expense")

    if submit_expense:
        if amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                kind_map = {
                    "Transaction (invoice/day level)": "transaction",
                    "Recurring Monthly (ChatGPT/Ads/Shopify)": "recurring_monthly",
                    "Summary Reference (roll-up only)": "summary_rollup",
                }
                expense_kind_value = kind_map[expense_kind_label]
                selected_invoice_id = labels[link_label]
                normalized_category = normalize_expense_category(category)

                if (
                    str(normalized_category).strip().lower() == "re-rental"
                    and selected_invoice_id is None
                ):
                    st.error("Re-Rental expenses must be linked to an invoice.")
                    return
                if expense_kind_value == "transaction" and selected_invoice_id is None:
                    st.error("Day-to-day transaction expenses must be linked to a confirmed invoice.")
                    return
                if expense_kind_value == "recurring_monthly" and selected_invoice_id is not None:
                    st.error("Recurring monthly expenses should not be linked to an invoice.")
                    return
                if expense_kind_value == "summary_rollup" and selected_invoice_id is not None:
                    st.error("Summary reference rows should not be linked to an invoice.")
                    return

                duplicate_hits = find_similar_expense_candidates(
                    expense_date=expense_date.isoformat(),
                    amount=float(amount),
                    category=normalized_category,
                    invoice_id=selected_invoice_id,
                    vendor=vendor.strip(),
                    description=description.strip(),
                )
                if not duplicate_hits.empty and not allow_possible_duplicate:
                    duplicate_show = duplicate_hits.copy()
                    duplicate_show["amount"] = pd.to_numeric(
                        duplicate_show["amount"], errors="coerce"
                    ).fillna(0.0).map(money)
                    st.error(
                        "Potential duplicate found. Tick 'Allow if potential duplicate is detected' to save anyway."
                    )
                    st.dataframe(duplicate_show, hide_index=True, use_container_width=True)
                    return

                add_expense(
                    expense_date=expense_date.isoformat(),
                    amount=float(amount),
                    category=normalized_category,
                    invoice_id=selected_invoice_id,
                    expense_kind=expense_kind_value,
                    vendor=vendor,
                    description=description,
                )
                finance_audit_log(
                    entity_type="expense",
                    entity_id=None,
                    action_type="create",
                    notes=(
                        f"{normalized_category} | {money(float(amount))} | kind={expense_kind_value} | "
                        f"invoice_id={selected_invoice_id if selected_invoice_id is not None else 'none'} | "
                        f"actor={actor} | device={device}"
                    ),
                )
                clear_finance_caches()
                st.success("Expense recorded.")
            except Exception as exc:
                st.error(f"Could not add expense: {exc}")

    st.markdown("---")
    st.markdown("**Recurring Draft Queue (Review + Post Actual)**")
    current_month_token = date.today().strftime("%Y-%m")
    draft_month = st.text_input(
        "Draft Month (YYYY-MM)",
        value=current_month_token,
        key="recurring_draft_month_filter",
        help="Example: 2026-02",
    ).strip()
    if not re.match(r"^\d{4}-\d{2}$", draft_month):
        draft_month = current_month_token
    recurring_drafts = load_recurring_draft_expenses(month=draft_month)
    if recurring_drafts.empty:
        st.caption(f"No recurring drafts waiting for {draft_month}.")
    else:
        draft_show = recurring_drafts.copy()
        draft_show["amount"] = pd.to_numeric(draft_show["amount"], errors="coerce").fillna(0.0)
        draft_show["amount_preview"] = draft_show["amount"].map(money)
        st.dataframe(
            draft_show[
                [
                    "id",
                    "expense_date",
                    "template_name",
                    "category",
                    "vendor",
                    "description",
                    "amount_preview",
                ]
            ].rename(
                columns={
                    "id": "Draft ID",
                    "expense_date": "Draft Date",
                    "template_name": "Template",
                    "category": "Category",
                    "vendor": "Vendor",
                    "description": "Description",
                    "amount_preview": "Default Amount",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
        draft_label_map = {
            (
                f"#{int(row['id'])} | {str(row.get('template_name', '')).strip() or str(row.get('category', '')).strip()} "
                f"| {str(row.get('expense_date', '')).strip()} | {money(float(row.get('amount', 0.0) or 0.0))}"
            ): int(row["id"])
            for _, row in recurring_drafts.iterrows()
        }
        draft_choice = st.selectbox(
            "Select Draft to Post",
            options=list(draft_label_map.keys()),
            key="recurring_draft_post_selector",
        )
        selected_draft_id = int(draft_label_map[draft_choice])
        selected_draft_row = recurring_drafts[recurring_drafts["id"] == selected_draft_id].iloc[0]
        raw_default_amount = pd.to_numeric(selected_draft_row.get("amount", 0.0), errors="coerce")
        default_actual_amount = 0.0 if pd.isna(raw_default_amount) else float(raw_default_amount)
        c1, c2, c3 = st.columns(3)
        actual_amount = c1.number_input(
            "Actual Amount (JMD)",
            min_value=0.0,
            step=100.0,
            value=default_actual_amount,
            key="recurring_draft_actual_amount",
        )
        parsed_draft_date = pd.to_datetime(
            selected_draft_row.get("expense_date"),
            errors="coerce",
        )
        default_actual_date = (
            parsed_draft_date.date() if pd.notna(parsed_draft_date) else date.today()
        )
        actual_date = c2.date_input(
            "Actual Date",
            value=default_actual_date,
            key="recurring_draft_actual_date",
        )
        actual_note = c3.text_input(
            "Optional Note",
            value="",
            key="recurring_draft_actual_note",
            placeholder="e.g. Final Google Ads charge for month",
        )
        if st.button("Post Actual Amount", key="recurring_draft_post_btn"):
            if float(actual_amount) <= 0:
                st.error("Actual amount must be greater than 0.")
            else:
                try:
                    finalize_recurring_draft_expense(
                        expense_id=selected_draft_id,
                        actual_amount=float(actual_amount),
                        actual_date=actual_date.isoformat(),
                        note_suffix=actual_note.strip(),
                    )
                    finance_audit_log(
                        entity_type="expense",
                        entity_id=selected_draft_id,
                        action_type="recurring_finalize",
                        notes=(
                            f"Recurring draft posted as actual | {draft_month} | "
                            f"amount={money(float(actual_amount))} | date={actual_date.isoformat()}"
                        ),
                    )
                    clear_finance_caches()
                    st.success("Recurring draft posted as actual monthly expense.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not post recurring draft: {exc}")

    st.markdown("---")
    with st.expander("Recurring Monthly Expense Templates", expanded=False):
        st.caption(
            "Templates auto-create monthly drafts. Post each draft with actual amount/date so Finance Hub uses real values."
        )
        with st.form("recurring_template_form", clear_on_submit=True):
            t1, t2, t3, t4 = st.columns(4)
            template_name = t1.text_input("Template Name", placeholder="ChatGPT Subscription")
            template_category = t2.selectbox(
                "Category",
                options=category_options,
            )
            template_amount = t3.number_input("Amount (JMD)", min_value=0.0, step=100.0)
            template_day = int(
                t4.number_input("Post Day", min_value=1, max_value=31, value=1, step=1)
            )
            tv1, tv2, tv3 = st.columns(3)
            template_vendor = tv1.text_input("Vendor")
            template_description = tv2.text_input("Description")
            template_active = tv3.checkbox("Active", value=True)
            save_template = st.form_submit_button("Save Recurring Template")

        if save_template:
            if not template_name.strip():
                st.error("Template name is required.")
            elif float(template_amount) <= 0:
                st.error("Template amount must be greater than 0.")
            else:
                try:
                    normalized_template_category = normalize_expense_category(template_category)
                    template_id = upsert_recurring_expense_template(
                        template_name=template_name.strip(),
                        category=normalized_template_category,
                        default_amount=float(template_amount),
                        vendor=template_vendor.strip(),
                        description=template_description.strip(),
                        day_of_month=int(template_day),
                        active=int(1 if template_active else 0),
                    )
                    finance_audit_log(
                        entity_type="recurring_template",
                        entity_id=template_id,
                        action_type="upsert",
                        notes=(
                            f"{template_name.strip()} | "
                            f"{normalized_template_category} | {money(float(template_amount))}"
                        ),
                    )
                    clear_finance_caches()
                    st.success("Recurring template saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save recurring template: {exc}")

        templates = cached_recurring_templates()
        if templates.empty:
            st.caption("No recurring templates yet.")
        else:
            template_show = templates.copy()
            template_show["default_amount"] = pd.to_numeric(
                template_show["default_amount"], errors="coerce"
            ).fillna(0.0).map(money)
            template_show["active"] = template_show["active"].map({1: "Yes", 0: "No"}).fillna("No")
            render_paginated_dataframe(
                template_show[
                    [
                        "id",
                        "template_name",
                        "category",
                        "vendor",
                        "description",
                        "default_amount",
                        "day_of_month",
                        "active",
                    ]
                ],
                key_prefix="expenses_recurring_templates",
                page_size_default=10,
            )
            d1, d2 = st.columns([2.5, 1.0])
            template_delete_map = {
                f"#{int(row['id'])} | {row['template_name']} | {row['default_amount']}": int(row["id"])
                for _, row in template_show.iterrows()
            }
            selected_template_delete = d1.selectbox(
                "Delete Template (optional)",
                options=list(template_delete_map.keys()),
                key="expenses_delete_template_selector",
            )
            if d2.button("Delete Template", key="expenses_delete_template_btn"):
                try:
                    template_id = int(template_delete_map[selected_template_delete])
                    deleted = delete_recurring_expense_template(template_id)
                    if deleted:
                        finance_audit_log(
                            entity_type="recurring_template",
                            entity_id=template_id,
                            action_type="delete",
                            notes="Recurring template deleted.",
                        )
                        clear_finance_caches()
                        st.success("Recurring template deleted.")
                        st.rerun()
                    else:
                        st.info("Template already removed.")
                except Exception as exc:
                    st.error(f"Could not delete recurring template: {exc}")

    if show_advanced_expense_controls:
        st.caption("Owner monthly adjustments are available at the bottom of Finance Hub, above Owner Danger Zone.")

    st.markdown("---")
    with st.expander("Recent Expenses", expanded=False):
        expenses = cached_expenses()
        if not expenses.empty:
            expenses = expenses[~inventory_purchase_mask(expenses["category"])].copy()
        if expenses.empty:
            st.info("No expenses yet.")
            return

        shown = expenses.sort_values("expense_date", ascending=False).copy()
        shown["expense_date"] = shown["expense_date"].dt.date.astype("string")
        if "finance_date" in shown.columns:
            shown["finance_date"] = pd.to_datetime(shown["finance_date"], errors="coerce").dt.date.astype("string")
        if "date_basis" in shown.columns:
            shown["date_basis"] = shown["date_basis"].map(
                {
                    "event_date": "Invoice Event Date",
                    "expense_date": "Entry Date",
                }
            ).fillna("Entry Date")
        shown["amount"] = shown["amount"].map(money)
        render_paginated_dataframe(
            shown[
                [
                    "id",
                    "expense_date",
                    "finance_date",
                    "date_basis",
                    "invoice_id",
                    "category",
                    "expense_kind",
                    "vendor",
                    "description",
                    "amount",
                ]
            ],
            key_prefix="expenses_recent",
            page_size_default=15,
        )

    st.markdown("---")
    show_manage_expenses = st.toggle(
        "Show Manage Expenses (Edit/Delete)",
        value=False,
        key="expenses_manage_toggle",
    )
    if not show_manage_expenses:
        return
    st.markdown("**Manage Expenses (Edit/Delete)**")
    expense_records = cached_expenses().sort_values("expense_date", ascending=False).copy()
    if not expense_records.empty:
        expense_records = expense_records[~inventory_purchase_mask(expense_records["category"])].copy()
    if expense_records.empty:
        st.caption("No expenses available to edit.")
        return

    expense_records["expense_date"] = pd.to_datetime(
        expense_records["expense_date"], errors="coerce"
    )
    expense_label_map: dict[str, int] = {}
    for _, row in expense_records.iterrows():
        row_id = int(row["id"])
        date_str = (
            row["expense_date"].date().isoformat()
            if pd.notna(row["expense_date"])
            else "No Date"
        )
        vendor_text = str(row.get("vendor", "") or "").strip() or "No Vendor"
        expense_label = (
            f"#{row_id} | {date_str} | {str(row.get('category', '')).strip() or 'Other'} | "
            f"{money(float(row.get('amount', 0.0) or 0.0))} | {vendor_text}"
        )
        expense_label_map[expense_label] = row_id

    selected_expense_label = st.selectbox(
        "Select Expense",
        options=list(expense_label_map.keys()),
        key="finance_manage_expense_selector",
    )
    selected_expense_id = int(expense_label_map[selected_expense_label])
    selected_expense = expense_records[expense_records["id"] == selected_expense_id].iloc[0]

    current_invoice_id = (
        None
        if pd.isna(selected_expense.get("invoice_id"))
        else int(selected_expense.get("invoice_id"))
    )
    invoice_option_labels = list(labels.keys())
    invoice_option_labels_transaction = [label for label in invoice_option_labels if labels[label] is not None]
    invoice_option_labels_all = [not_linked_label] + [
        label for label in invoice_option_labels_transaction if label != not_linked_label
    ]
    if not invoice_option_labels_all:
        invoice_option_labels_all = [not_linked_label]
    reverse_invoice_labels = {value: key for key, value in labels.items()}
    selected_invoice_label = reverse_invoice_labels.get(current_invoice_id, not_linked_label)

    kind_option_labels = [
        "Transaction (invoice/day level)",
        "Recurring Monthly (ChatGPT/Ads/Shopify)",
    ]
    if show_advanced_expense_controls:
        kind_option_labels.append("Summary Reference (roll-up only)")
    kind_to_value = {
        "Transaction (invoice/day level)": "transaction",
        "Recurring Monthly (ChatGPT/Ads/Shopify)": "recurring_monthly",
        "Summary Reference (roll-up only)": "summary_rollup",
    }
    value_to_kind = {value: key for key, value in kind_to_value.items()}
    current_kind_value = str(selected_expense.get("expense_kind", "transaction")).strip().lower()
    kind_default_label = value_to_kind.get(
        current_kind_value,
        "Transaction (invoice/day level)",
    )
    if kind_default_label not in kind_option_labels:
        kind_default_label = "Transaction (invoice/day level)"
    kind_default_index = kind_option_labels.index(kind_default_label)
    invoice_default_index = (
        invoice_option_labels_all.index(selected_invoice_label)
        if selected_invoice_label in invoice_option_labels_all
        else 0
    )

    current_category = normalize_expense_category(
        str(selected_expense.get("category", "") or "").strip() or "Other"
    )
    category_edit_options = category_options.copy()
    if current_category not in category_edit_options:
        category_edit_options.append(current_category)
    category_edit_index = category_edit_options.index(current_category)

    with st.form(f"expense_edit_form_{selected_expense_id}", clear_on_submit=False):
        e1, e2, e3, e4 = st.columns(4)
        edited_date = e1.date_input(
            "Expense Date",
            value=(
                selected_expense["expense_date"].date()
                if pd.notna(selected_expense["expense_date"])
                else date.today()
            ),
        )
        edited_amount = e2.number_input(
            "Amount (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(selected_expense.get("amount", 0.0) or 0.0),
        )
        edited_category = e3.selectbox(
            "Category",
            options=category_edit_options,
            index=category_edit_index,
        )
        edited_kind_label = e4.selectbox(
            "Expense Type",
            options=kind_option_labels,
            index=kind_default_index,
        )

        f1, f2, f3 = st.columns(3)
        edited_vendor = f1.text_input(
            "Vendor / Person",
            value=str(selected_expense.get("vendor", "") or ""),
        )
        edited_description = f2.text_input(
            "Description",
            value=str(selected_expense.get("description", "") or ""),
        )
        edited_link_label = f3.selectbox(
            "Link to Invoice (required for Transaction)",
            options=invoice_option_labels_all,
            index=invoice_default_index,
        )
        edit_allow_duplicate = False
        if show_advanced_expense_controls:
            edit_allow_duplicate = st.checkbox(
                "Allow if potential duplicate is detected (edit)",
                value=False,
            )

        save_edit = st.form_submit_button("Save Expense Changes")

    if save_edit:
        if edited_amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                edited_kind_value = kind_to_value[edited_kind_label]
                edited_invoice_id = labels[edited_link_label]
                normalized_edited_category = normalize_expense_category(edited_category)
                if (
                    str(normalized_edited_category).strip().lower() == "re-rental"
                    and edited_invoice_id is None
                ):
                    st.error("Re-Rental expenses must be linked to an invoice.")
                    return
                if edited_kind_value == "transaction" and edited_invoice_id is None:
                    st.error("Day-to-day transaction expenses must be linked to a confirmed invoice.")
                    return
                if edited_kind_value == "recurring_monthly" and edited_invoice_id is not None:
                    st.error("Recurring monthly expenses should not be linked to an invoice.")
                    return
                if edited_kind_value == "summary_rollup" and edited_invoice_id is not None:
                    st.error("Summary reference rows should not be linked to an invoice.")
                    return

                duplicate_hits = find_similar_expense_candidates(
                    expense_date=edited_date.isoformat(),
                    amount=float(edited_amount),
                    category=normalized_edited_category,
                    invoice_id=edited_invoice_id,
                    vendor=edited_vendor.strip(),
                    description=edited_description.strip(),
                    exclude_expense_id=selected_expense_id,
                )
                if not duplicate_hits.empty and not edit_allow_duplicate:
                    duplicate_show = duplicate_hits.copy()
                    duplicate_show["amount"] = pd.to_numeric(
                        duplicate_show["amount"], errors="coerce"
                    ).fillna(0.0).map(money)
                    st.error(
                        "Potential duplicate found. Tick 'Allow if potential duplicate is detected (edit)' to save anyway."
                    )
                    st.dataframe(duplicate_show, hide_index=True, use_container_width=True)
                    return

                update_expense(
                    expense_id=selected_expense_id,
                    expense_date=edited_date.isoformat(),
                    amount=float(edited_amount),
                    category=normalized_edited_category,
                    invoice_id=edited_invoice_id,
                    expense_kind=edited_kind_value,
                    vendor=edited_vendor,
                    description=edited_description,
                )
                finance_audit_log(
                    entity_type="expense",
                    entity_id=selected_expense_id,
                    action_type="update",
                    notes=(
                        f"{normalized_edited_category} | {money(float(edited_amount))} | kind={edited_kind_value} | "
                        f"invoice_id={edited_invoice_id if edited_invoice_id is not None else 'none'} | "
                        f"actor={actor} | device={device}"
                    ),
                )
                clear_finance_caches()
                st.success("Expense updated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not update expense: {exc}")

    delete_ok = st.checkbox(
        "I understand this will permanently delete this expense.",
        key=f"delete_expense_confirm_{selected_expense_id}",
    )
    if st.button(
        "Delete Selected Expense",
        key=f"delete_expense_btn_{selected_expense_id}",
        type="secondary",
    ):
        if not delete_ok:
            st.error("Please confirm deletion first.")
        else:
            try:
                delete_expense(selected_expense_id)
                finance_audit_log(
                    entity_type="expense",
                    entity_id=selected_expense_id,
                    action_type="delete",
                    notes=f"Deleted expense #{selected_expense_id} by {actor} on {device}.",
                )
                clear_finance_caches()
                st.success("Expense deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete expense: {exc}")


def render_supplier_rerental() -> None:
    render_section_shell(
        "Supplier Re-Rental",
        "Log supplier costs linked to invoices without exposing confidential finance views.",
    )
    st.caption(
        "Staff can log supplier re-rental costs here. Entries automatically feed Finance Hub expenses and reports."
    )
    st.info(
        "Date rule: supplier re-rental entries are linked to the invoice event date/day for daily, monthly, and yearly finance calculations."
    )

    labels = invoice_label_map()
    options = [label for label in labels.keys() if labels[label] is not None]
    if not options:
        st.warning("No confirmed invoices available yet. Confirm an order first, then log supplier re-rental.")
        return

    confirmed_invoice_rows = cached_invoice_options(include_quotes=False, confirmed_only=True)
    invoice_event_date_by_id: dict[int, date] = {}
    if not confirmed_invoice_rows.empty:
        for _, inv_row in confirmed_invoice_rows.iterrows():
            inv_id_raw = pd.to_numeric(inv_row.get("id"), errors="coerce")
            if pd.isna(inv_id_raw):
                continue
            inv_date_raw = pd.to_datetime(inv_row.get("event_date"), errors="coerce")
            if pd.isna(inv_date_raw):
                continue
            invoice_event_date_by_id[int(inv_id_raw)] = inv_date_raw.date()

    if str(st.session_state.get("supplier_rerental_link_label", "") or "") not in options:
        st.session_state["supplier_rerental_link_label"] = options[0]
    link_label = st.selectbox(
        "Link to Invoice *",
        options=options,
        key="supplier_rerental_link_label",
    )
    linked_invoice_id = labels.get(link_label)
    linked_expense_date = (
        invoice_event_date_by_id.get(int(linked_invoice_id), date.today())
        if linked_invoice_id is not None
        else date.today()
    )
    st.date_input(
        "Expense Date (Auto from linked invoice)",
        value=linked_expense_date,
        disabled=True,
    )

    with st.form("supplier_rerental_form", clear_on_submit=True):
        a1, a2 = st.columns(2)
        vendor = a1.text_input("Supplier Name *", placeholder="Supplier / Vendor")
        amount = a2.number_input("Amount (JMD) *", min_value=0.0, step=100.0)

        description = st.text_input(
            "Description",
            placeholder="Items or services supplied",
        )
        allow_duplicate_rerental = st.checkbox(
            "Allow if potential duplicate is detected",
            value=False,
        )

        submit_rerental = st.form_submit_button("Add Supplier Re-Rental")

    if submit_rerental:
        if not vendor.strip():
            st.error("Supplier name is required.")
        elif amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                if linked_invoice_id is None:
                    st.error("Select a linked invoice for supplier re-rental.")
                    return
                duplicate_hits = find_similar_expense_candidates(
                    expense_date=linked_expense_date.isoformat(),
                    amount=float(amount),
                    category="Re-Rental",
                    invoice_id=int(linked_invoice_id),
                    vendor=vendor.strip(),
                    description=description.strip(),
                )
                if not duplicate_hits.empty and not allow_duplicate_rerental:
                    duplicate_show = duplicate_hits.copy()
                    duplicate_show["amount"] = pd.to_numeric(
                        duplicate_show["amount"], errors="coerce"
                    ).fillna(0.0).map(money)
                    st.error(
                        "Potential duplicate found. Tick 'Allow if potential duplicate is detected' to save anyway."
                    )
                    st.dataframe(duplicate_show, hide_index=True, use_container_width=True)
                    return
                add_expense(
                    expense_date=linked_expense_date.isoformat(),
                    amount=float(amount),
                    category="Re-Rental",
                    invoice_id=int(linked_invoice_id),
                    expense_kind="transaction",
                    vendor=vendor.strip(),
                    description=description.strip(),
                )
                finance_audit_log(
                    entity_type="supplier_rerental",
                    entity_id=None,
                    action_type="create",
                    notes=(
                        f"{vendor.strip()} | {money(float(amount))} | "
                        f"invoice_id={int(linked_invoice_id)}"
                    ),
                )
                clear_finance_caches()
                st.success("Supplier re-rental expense recorded.")
            except Exception as exc:
                st.error(f"Could not add supplier re-rental expense: {exc}")

    st.markdown("---")
    month_key = f"{date.today().year:04d}-{date.today().month:02d}"
    expenses = cached_expenses()
    if expenses.empty:
        st.info("No supplier re-rental expenses yet.")
        return

    rerental = expenses[
        (expenses["category"].fillna("").str.lower() == "re-rental")
        & (expenses["expense_kind"].fillna("").str.lower() == "transaction")
        & (expenses["vendor"].fillna("").str.strip() != "")
    ].copy()
    if rerental.empty:
        st.info("No supplier re-rental expenses yet.")
        return

    rerental["month"] = rerental["month"].fillna("")
    total_spend = float(rerental["amount"].sum())
    month_spend = float(
        rerental.loc[rerental["month"] == month_key, "amount"].sum()
    )
    supplier_count = int(rerental["vendor"].nunique())

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Supplier Spend", money(total_spend))
    k2.metric("Current Month", money(month_spend))
    k3.metric("Suppliers Logged", f"{supplier_count}")

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("**Recent Supplier Re-Rental Entries**")
        show = rerental.sort_values("expense_date", ascending=False).copy()
        show["expense_date"] = show["expense_date"].dt.date.astype("string")
        show["amount"] = show["amount"].map(money)
        render_paginated_dataframe(
            show[
                [
                    "expense_date",
                    "invoice_id",
                    "vendor",
                    "description",
                    "amount",
                ]
            ],
            key_prefix="supplier_rerental_recent",
            page_size_default=10,
        )

    with right:
        st.markdown("**Supplier Totals**")
        supplier_totals = cached_supplier_expenses()
        if supplier_totals.empty:
            st.caption("No supplier totals yet.")
        else:
            supplier_view = supplier_totals.copy()
            supplier_view["amount"] = supplier_view["amount"].map(money)
            render_paginated_dataframe(
                supplier_view,
                key_prefix="supplier_rerental_totals",
                page_size_default=10,
            )

    st.markdown("---")
    show_manage_supplier_entries = st.toggle(
        "Show Manage Supplier Re-Rental Entries (Edit/Delete)",
        value=False,
        key="supplier_manage_toggle",
    )
    if not show_manage_supplier_entries:
        return
    st.markdown("**Manage Supplier Re-Rental Entries (Edit/Delete)**")
    st.caption(
        "Select a supplier re-rental row to edit amount/date/vendor/linked invoice or remove it."
    )
    manage_rows = rerental.sort_values("expense_date", ascending=False).copy()
    manage_rows["amount"] = pd.to_numeric(manage_rows["amount"], errors="coerce").fillna(0.0)
    invoice_id_to_label = {
        int(invoice_id): label
        for label, invoice_id in labels.items()
        if invoice_id is not None
    }
    manage_choice_map: dict[str, int] = {}
    for _, row in manage_rows.iterrows():
        row_id = int(row.get("id"))
        date_text = (
            row["expense_date"].date().isoformat()
            if pd.notna(row.get("expense_date"))
            else "No Date"
        )
        vendor_text = str(row.get("vendor", "") or "").strip() or "No Supplier"
        amount_text = money(float(row.get("amount", 0.0) or 0.0))
        inv_id_raw = pd.to_numeric(row.get("invoice_id"), errors="coerce")
        inv_id = int(inv_id_raw) if pd.notna(inv_id_raw) else None
        linked_label = (
            invoice_id_to_label.get(inv_id, f"Invoice ID {inv_id}")
            if inv_id is not None
            else "Not linked"
        )
        label = f"#{row_id} | {date_text} | {vendor_text} | {amount_text} | {linked_label}"
        manage_choice_map[label] = row_id

    if not manage_choice_map:
        st.caption("No supplier re-rental entries available to edit yet.")
        return

    selected_manage_label = st.selectbox(
        "Select Re-Rental Entry",
        options=list(manage_choice_map.keys()),
        key="supplier_rerental_manage_selector",
    )
    selected_manage_id = int(manage_choice_map[selected_manage_label])
    selected_manage = manage_rows[manage_rows["id"] == selected_manage_id].copy()
    if selected_manage.empty:
        st.info("Selected entry no longer exists.")
        return
    selected_row = selected_manage.iloc[0]
    selected_date_raw = pd.to_datetime(selected_row.get("expense_date"), errors="coerce")
    selected_date = selected_date_raw.date() if not pd.isna(selected_date_raw) else date.today()
    selected_vendor = str(selected_row.get("vendor", "") or "").strip()
    selected_description = str(selected_row.get("description", "") or "").strip()
    selected_amount_raw = pd.to_numeric(selected_row.get("amount"), errors="coerce")
    selected_amount = float(0.0 if pd.isna(selected_amount_raw) else selected_amount_raw)
    selected_invoice_raw = pd.to_numeric(selected_row.get("invoice_id"), errors="coerce")
    selected_invoice_id = int(selected_invoice_raw) if pd.notna(selected_invoice_raw) else None
    selected_invoice_label = invoice_id_to_label.get(selected_invoice_id, options[0])
    selected_link_index = options.index(selected_invoice_label) if selected_invoice_label in options else 0

    with st.form("supplier_rerental_manage_form", clear_on_submit=False):
        e1, e2, e3 = st.columns(3)
        selected_linked_event_date = (
            invoice_event_date_by_id.get(int(selected_invoice_id), selected_date)
            if selected_invoice_id is not None
            else selected_date
        )
        edited_date = e1.date_input(
            "Expense Date (Auto from linked invoice)",
            value=selected_linked_event_date,
            key=f"supplier_rerental_edit_date_{selected_manage_id}",
            disabled=True,
        )
        edited_vendor = e2.text_input(
            "Supplier Name *",
            value=selected_vendor,
            key=f"supplier_rerental_edit_vendor_{selected_manage_id}",
        )
        edited_amount = e3.number_input(
            "Amount (JMD) *",
            min_value=0.0,
            step=100.0,
            value=selected_amount,
            key=f"supplier_rerental_edit_amount_{selected_manage_id}",
        )
        f1, f2 = st.columns(2)
        edited_link_label = f1.selectbox(
            "Link to Invoice *",
            options=options,
            index=selected_link_index,
            key=f"supplier_rerental_edit_invoice_{selected_manage_id}",
        )
        edited_description = f2.text_input(
            "Description",
            value=selected_description,
            key=f"supplier_rerental_edit_desc_{selected_manage_id}",
        )
        allow_duplicate_edit = st.checkbox(
            "Allow if potential duplicate is detected (edit)",
            value=False,
            key=f"supplier_rerental_edit_allow_dup_{selected_manage_id}",
        )
        save_rerental_edit = st.form_submit_button("Save Re-Rental Changes")

    if save_rerental_edit:
        if not edited_vendor.strip():
            st.error("Supplier name is required.")
        elif float(edited_amount) <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                linked_invoice_id = labels.get(edited_link_label)
                if linked_invoice_id is None:
                    st.error("Select a linked invoice for supplier re-rental.")
                    return
                linked_invoice_date = invoice_event_date_by_id.get(
                    int(linked_invoice_id),
                    selected_date,
                )
                duplicate_hits = find_similar_expense_candidates(
                    expense_date=linked_invoice_date.isoformat(),
                    amount=float(edited_amount),
                    category="Re-Rental",
                    invoice_id=int(linked_invoice_id),
                    vendor=edited_vendor.strip(),
                    description=edited_description.strip(),
                    exclude_expense_id=selected_manage_id,
                )
                if not duplicate_hits.empty and not allow_duplicate_edit:
                    duplicate_show = duplicate_hits.copy()
                    duplicate_show["amount"] = pd.to_numeric(
                        duplicate_show["amount"], errors="coerce"
                    ).fillna(0.0).map(money)
                    st.error(
                        "Potential duplicate found. Tick 'Allow if potential duplicate is detected (edit)' to save anyway."
                    )
                    st.dataframe(duplicate_show, hide_index=True, use_container_width=True)
                else:
                    update_expense(
                        expense_id=selected_manage_id,
                        expense_date=linked_invoice_date.isoformat(),
                        amount=float(edited_amount),
                        category="Re-Rental",
                        invoice_id=int(linked_invoice_id),
                        expense_kind="transaction",
                        vendor=edited_vendor.strip(),
                        description=edited_description.strip(),
                    )
                    finance_audit_log(
                        entity_type="supplier_rerental",
                        entity_id=selected_manage_id,
                        action_type="update",
                        notes=(
                            f"{edited_vendor.strip()} | {money(float(edited_amount))} | "
                            f"invoice_id={int(linked_invoice_id)}"
                        ),
                    )
                    clear_finance_caches()
                    st.success("Supplier re-rental entry updated.")
                    st.rerun()
            except Exception as exc:
                st.error(f"Could not update supplier re-rental entry: {exc}")

    delete_confirm = st.checkbox(
        "Confirm delete supplier re-rental entry",
        key=f"supplier_rerental_delete_confirm_{selected_manage_id}",
    )
    if st.button(
        "Delete Re-Rental Entry",
        key=f"supplier_rerental_delete_btn_{selected_manage_id}",
        type="secondary",
    ):
        if not delete_confirm:
            st.error("Please confirm delete first.")
        else:
            try:
                delete_expense(selected_manage_id)
                finance_audit_log(
                    entity_type="supplier_rerental",
                    entity_id=selected_manage_id,
                    action_type="delete",
                    notes=f"Deleted supplier re-rental expense #{selected_manage_id}.",
                )
                clear_finance_caches()
                st.success("Supplier re-rental entry deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete supplier re-rental entry: {exc}")


def _fmt_qty(value: float) -> str:
    number = float(value or 0.0)
    if abs(number - round(number)) < 0.0001:
        return str(int(round(number)))
    return f"{number:.1f}"


def _game_stats_key(game_id: str) -> str:
    return f"game_stats_{game_id}"


def _get_game_stats(game_id: str) -> dict[str, int]:
    key = _game_stats_key(game_id)
    if key not in st.session_state:
        st.session_state[key] = {
            "attempts": 0,
            "correct": 0,
            "streak": 0,
            "best_streak": 0,
        }
    return st.session_state[key]


def _record_game_result(game_id: str, correct: bool) -> None:
    stats = _get_game_stats(game_id)
    stats["attempts"] += 1
    if correct:
        stats["correct"] += 1
        stats["streak"] += 1
        stats["best_streak"] = max(stats["best_streak"], stats["streak"])
    else:
        stats["streak"] = 0
    st.session_state[_game_stats_key(game_id)] = stats


def _game_stats_caption(game_id: str) -> str:
    stats = _get_game_stats(game_id)
    attempts = int(stats["attempts"])
    correct = int(stats["correct"])
    accuracy = (correct / attempts * 100.0) if attempts else 0.0
    return (
        f"Attempts: {attempts} | Correct: {correct} | "
        f"Accuracy: {accuracy:,.0f}% | Streak: {int(stats['streak'])} | "
        f"Best Streak: {int(stats['best_streak'])}"
    )


def _price_option(value: float) -> str:
    rounded = round(float(value) / 10.0) * 10.0
    return money(float(max(0.0, rounded)))


def _price_choices(answer_value: float, seed_values: list[float], count: int = 4) -> list[str]:
    answer = round(float(answer_value) / 10.0) * 10.0
    candidates: list[float] = [max(0.0, answer)]
    for seed in seed_values:
        val = round(float(seed) / 10.0) * 10.0
        if val >= 0 and val not in candidates:
            candidates.append(val)

    step = 10.0
    attempts = 0
    while len(candidates) < count and attempts < 60:
        direction = -1.0 if attempts % 2 else 1.0
        magnitude = (attempts // 2 + 1) * step
        candidate = max(0.0, round((answer + (direction * magnitude)) / 10.0) * 10.0)
        if candidate not in candidates:
            candidates.append(candidate)
        attempts += 1

    labels = [_price_option(value) for value in candidates[:count]]
    return labels


def build_price_duel_question(stock: pd.DataFrame) -> dict:
    valid = stock[stock["default_rental_price"] > 0].copy()
    if valid.empty:
        return {
            "id": random.randint(1000, 999999),
            "question": "No priced inventory items yet. Add rental prices to unlock this game.",
            "options": ["Add pricing first"],
            "answer": "Add pricing first",
            "explanation": "Set default rental price on inventory items.",
        }

    qtypes = ["exact", "bundle"]
    if len(valid) >= 3:
        qtypes.append("highest")
    qtype = random.choice(qtypes)

    if qtype == "exact":
        row = valid.sample(1).iloc[0]
        answer_price = float(row["default_rental_price"])
        item_name = str(row["item_name"])
        options = _price_choices(
            answer_value=answer_price,
            seed_values=[
                answer_price * 0.85,
                answer_price * 1.12,
                answer_price * 1.3,
                answer_price - 30.0,
            ],
            count=4,
        )
        random.shuffle(options)
        return {
            "id": random.randint(1000, 999999),
            "question": f"Price Duel: What is the default rental price for `{item_name}`?",
            "options": options,
            "answer": _price_option(answer_price),
            "explanation": f"{item_name} default rental price is {_price_option(answer_price)}.",
        }

    if qtype == "highest":
        sample = valid.sample(min(4, len(valid))).copy()
        sample = sample.sort_values("default_rental_price", ascending=False)
        answer_name = str(sample.iloc[0]["item_name"])
        answer_price = float(sample.iloc[0]["default_rental_price"])
        options = sample["item_name"].astype(str).tolist()
        random.shuffle(options)
        return {
            "id": random.randint(1000, 999999),
            "question": "Price Duel: Which item has the highest default rental price in this set?",
            "options": options,
            "answer": answer_name,
            "explanation": f"{answer_name} leads this set at {_price_option(answer_price)}.",
        }

    row = valid.sample(1).iloc[0]
    qty = random.choice([2, 3, 4, 5, 6])
    unit_price = float(row["default_rental_price"])
    total = qty * unit_price
    option_list = _price_choices(
        answer_value=total,
        seed_values=[
            total * 0.85,
            total * 1.15,
            total + unit_price,
            total - (unit_price * 0.7),
        ],
        count=4,
    )
    random.shuffle(option_list)
    return {
        "id": random.randint(1000, 999999),
        "question": (
            f"Bundle Brain: A client books {qty} x `{row['item_name']}`. "
            f"What is the default rental subtotal?"
        ),
        "options": option_list,
        "answer": _price_option(total),
        "explanation": f"{qty} x {_price_option(unit_price)} = {_price_option(total)}.",
    }


def build_match_round(stock: pd.DataFrame) -> dict:
    valid = stock[stock["default_rental_price"] > 0].copy()
    if valid.empty:
        return {"id": random.randint(1000, 999999), "items": [], "price_options": [], "answers": {}}

    valid["game_price"] = (
        pd.to_numeric(valid["default_rental_price"], errors="coerce").fillna(0.0).apply(
            lambda value: round(float(value) / 10.0) * 10.0
        )
    )
    valid = valid[valid["game_price"] > 0].copy()
    unique_price_pool = valid.drop_duplicates(subset=["game_price"], keep="first")
    sample_size = min(5, len(unique_price_pool))
    if sample_size < 3:
        return {"id": random.randint(1000, 999999), "items": [], "price_options": [], "answers": {}}

    sample = unique_price_pool.sample(sample_size).copy().sort_values("item_name")
    answers = {
        str(row["item_name"]): _price_option(float(row["game_price"]))
        for _, row in sample.iterrows()
    }
    price_options = list(answers.values())
    random.shuffle(price_options)
    return {
        "id": random.randint(1000, 999999),
        "items": list(answers.keys()),
        "price_options": price_options,
        "answers": answers,
    }


def render_inventory_training_arcade(
    stock: pd.DataFrame,
    default_reference_date: date,
    default_reference_time: time,
) -> None:
    st.markdown("---")
    st.subheader("Pricing Lab & Training Arcade")
    st.caption(
        "Use this section to train staff on pricing, operations logic, and inventory readiness."
    )
    tabs = st.tabs(
        [
            "Pricing List",
            "Price Duel Quiz",
            "Match-Up Arena",
        ]
    )

    with tabs[0]:
        st.markdown("**Availability Check Time**")
        st.caption(
            "Set a date/time to check availability of each rental item at that exact moment."
        )
        ref1, ref2, ref3 = st.columns([1, 1, 2])
        availability_date = ref1.date_input(
            "Availability Date",
            value=default_reference_date,
            key="inventory_live_reference_date_input",
        )
        availability_time = ref2.time_input(
            "Availability Time",
            value=default_reference_time,
            key="inventory_live_reference_time_input",
        )
        availability_reference = pd.Timestamp(datetime.combine(availability_date, availability_time))
        ref3.caption(
            "Inventory availability is calculated at: "
            f"{availability_reference.strftime('%Y-%m-%d %H:%M')} (America/Jamaica)."
        )
        st.caption(
            "This compares confirmed bookings vs stock at the selected check time."
        )

        live_status = load_inventory_live_status(reference_time=availability_reference)
        price_df = stock.copy()
        required_cols = [
            "id",
            "item_name",
            "unit",
            "current_quantity",
            "default_rental_price",
            "active",
        ]
        for col in required_cols:
            if col not in price_df.columns:
                price_df[col] = 0 if col in {"id", "current_quantity", "default_rental_price", "active"} else ""
        price_df["current_quantity"] = pd.to_numeric(price_df["current_quantity"], errors="coerce").fillna(0.0)
        price_df["default_rental_price"] = pd.to_numeric(
            price_df["default_rental_price"], errors="coerce"
        ).fillna(0.0)
        price_df["active"] = (
            pd.to_numeric(price_df["active"], errors="coerce")
            .fillna(1)
            .astype(int)
            .map(lambda x: True if int(x) == 1 else False)
        )
        live_df = live_status.copy() if live_status is not None else pd.DataFrame()
        if live_df.empty:
            price_df["reserved_now"] = 0.0
            price_df["usable_now"] = price_df["current_quantity"]
        else:
            live_df["reserved_now"] = pd.to_numeric(live_df["reserved_now"], errors="coerce").fillna(0.0)
            live_df["usable_now"] = pd.to_numeric(live_df["usable_now"], errors="coerce").fillna(0.0)
            price_df = price_df.merge(
                live_df[["item_name", "reserved_now", "usable_now"]],
                on="item_name",
                how="left",
            )
            price_df["reserved_now"] = price_df["reserved_now"].fillna(0.0)
            price_df["usable_now"] = price_df["usable_now"].fillna(price_df["current_quantity"])

        price_df["stock_state"] = price_df["usable_now"].apply(
            lambda value: "Out of Stock" if float(value) <= 0 else "In Stock"
        )
        editor_source = price_df[
            [
                "item_name",
                "unit",
                "current_quantity",
                "reserved_now",
                "usable_now",
                "stock_state",
                "default_rental_price",
                "active",
                "id",
            ]
        ].sort_values(["item_name"])
        editor_source = editor_source.reset_index(drop=True)
        editor_source["inventory_count"] = (
            pd.Series(range(1, len(editor_source) + 1), index=editor_source.index).astype(int)
        )
        inventory_id_by_count = {
            int(row["inventory_count"]): int(row["id"])
            for _, row in editor_source.iterrows()
            if pd.notna(row["inventory_count"]) and pd.notna(row["id"])
        }
        original_qty_by_id = {
            int(row["id"]): float(row["current_quantity"])
            for _, row in editor_source.iterrows()
            if pd.notna(row["id"])
        }
        source_ids = set(original_qty_by_id.keys())
        editor_source = editor_source[
            [
                "inventory_count",
                "item_name",
                "current_quantity",
                "default_rental_price",
                "active",
                "reserved_now",
                "usable_now",
                "stock_state",
                "unit",
            ]
        ]

        st.caption(
            "Add, edit, or delete products directly in this list. "
            "Editable fields: Item Name, Stock Quantity, Rental Price, Active. "
            "`Available` and `Current Status` are automatic from confirmed-order date windows at the selected check time. "
            "Remove a row to delete that product."
        )
        edited = st.data_editor(
            editor_source,
            hide_index=True,
            use_container_width=True,
            key="inventory_price_list_editor_v2",
            num_rows="dynamic",
            disabled=["inventory_count", "reserved_now", "usable_now", "stock_state", "unit"],
            column_config={
                "inventory_count": st.column_config.NumberColumn("Inventory Count"),
                "item_name": st.column_config.TextColumn("Item Name"),
                "unit": st.column_config.SelectboxColumn(
                    "Unit",
                    options=["pcs", "sets", "units", "boxes"],
                ),
                "current_quantity": st.column_config.NumberColumn(
                    "Stock Quantity",
                    min_value=0.0,
                    step=1.0,
                    format="%.2f",
                ),
                "reserved_now": st.column_config.NumberColumn(
                    "Booked @ Check Time",
                    format="%.2f",
                ),
                "usable_now": st.column_config.NumberColumn(
                    "Available @ Check Time",
                    min_value=0.0,
                    step=1.0,
                    format="%.2f",
                ),
                "stock_state": st.column_config.SelectboxColumn(
                    "Status @ Check Time",
                    options=["In Stock", "Out of Stock"],
                ),
                "default_rental_price": st.column_config.NumberColumn(
                    "Rental Price (JMD)",
                    min_value=0.0,
                    step=100.0,
                    format="%.2f",
                ),
                "active": st.column_config.CheckboxColumn("Active"),
            },
        )

        if st.button("Save Price List Updates", key="save_inventory_price_list_btn"):
            updates = 0
            created = 0
            deleted = 0
            stock_changes = 0
            errors: list[str] = []
            seen_existing_ids: set[int] = set()
            edited_rows = edited.copy()

            for _, row in edited_rows.iterrows():
                row_count_raw = pd.to_numeric(row.get("inventory_count"), errors="coerce")
                row_count = int(row_count_raw) if pd.notna(row_count_raw) else None
                row_id = inventory_id_by_count.get(row_count) if row_count is not None else None
                is_existing = row_id is not None and row_id in source_ids
                name = str(row.get("item_name", "")).strip()
                unit = str(row.get("unit", "pcs")).strip() or "pcs"
                price_raw = pd.to_numeric(row.get("default_rental_price"), errors="coerce")
                stock_qty_raw = pd.to_numeric(row.get("current_quantity"), errors="coerce")
                rental_price = float(0.0 if pd.isna(price_raw) else price_raw)
                stock_qty = max(0.0, float(0.0 if pd.isna(stock_qty_raw) else stock_qty_raw))
                active = 1 if bool(row.get("active", True)) else 0
                desired_total_qty = stock_qty

                try:
                    if is_existing and row_id is not None:
                        seen_existing_ids.add(row_id)
                        if not name:
                            errors.append("Existing inventory rows must have an item name.")
                            continue
                        update_inventory_item_values(
                            item_id=row_id,
                            item_name=name,
                            category="General",
                            unit=unit,
                            reorder_level=0.0,
                            default_rental_price=rental_price,
                            active=active,
                            quantity_change=0.0,
                            target_quantity=desired_total_qty,
                        )
                        updates += 1
                        previous_qty = float(original_qty_by_id.get(row_id, desired_total_qty))
                        if abs(desired_total_qty - previous_qty) > 1e-9:
                            stock_changes += 1
                    else:
                        # New row
                        if not name:
                            continue
                        new_id = upsert_inventory_item(
                            item_name=name,
                            category="General",
                            unit=unit,
                            reorder_level=0.0,
                            default_rental_price=rental_price,
                            active=active,
                        )
                        update_inventory_item_values(
                            item_id=new_id,
                            item_name=name,
                            category="General",
                            unit=unit,
                            reorder_level=0.0,
                            default_rental_price=rental_price,
                            active=active,
                            quantity_change=0.0,
                            target_quantity=desired_total_qty,
                            movement_notes="Created from Pricing List editor.",
                        )
                        created += 1
                        if desired_total_qty > 0:
                            stock_changes += 1
                except Exception as exc:
                    errors.append(str(exc))

            removed_ids = sorted(source_ids - seen_existing_ids)
            for removed_id in removed_ids:
                try:
                    delete_inventory_item(removed_id)
                    deleted += 1
                except Exception as exc:
                    errors.append(str(exc))

            if errors:
                st.error(f"Could not save all rows. {len(errors)} error(s) found.")
                st.caption(errors[0])
            else:
                st.success(
                    f"Saved updates: {updates} | Added: {created} | Deleted: {deleted} | "
                    f"Rows with stock quantity changes: {stock_changes}."
                )
                st.rerun()

        view = edited.copy().reset_index(drop=True)

        p1, p2, p3 = st.columns(3)
        avg_price = float(pd.to_numeric(view["default_rental_price"], errors="coerce").fillna(0.0).mean()) if not view.empty else 0.0
        priced_count = int((pd.to_numeric(view["default_rental_price"], errors="coerce").fillna(0.0) > 0).sum())
        p1.metric("Items With Price", f"{priced_count}/{len(view)}")
        p2.metric("Average Rental Price", money(avg_price))
        p3.metric(
            "Top Price",
            money(float(pd.to_numeric(view["default_rental_price"], errors="coerce").fillna(0.0).max() if not view.empty else 0.0)),
        )

        export_view = view.copy()
        csv_bytes = export_view.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Pricing List CSV",
            data=csv_bytes,
            file_name="inventory_pricing_list.csv",
            mime="text/csv",
        )

    with tabs[1]:
        st.caption("Challenging multiple-choice rounds on pricing and bundle math.")
        q_key = "inventory_game_price_duel_question"
        if q_key not in st.session_state:
            st.session_state[q_key] = build_price_duel_question(stock)
        duel = st.session_state[q_key]
        st.caption(_game_stats_caption("price_duel"))
        if st.button("New Duel Question", key="price_duel_new_btn"):
            st.session_state[q_key] = build_price_duel_question(stock)
            st.rerun()

        st.markdown(f"**{duel['question']}**")
        selected = st.radio(
            "Choose your answer",
            options=duel["options"],
            key=f"price_duel_answer_{duel['id']}",
        )
        if st.button("Submit Duel Answer", key=f"price_duel_submit_{duel['id']}"):
            is_correct = selected == duel["answer"]
            _record_game_result("price_duel", is_correct)
            if is_correct:
                st.success("Correct. Sharp work.")
            else:
                st.error(f"Not this round. Correct answer: {duel['answer']}")
            st.info(duel["explanation"])

    with tabs[2]:
        st.caption("Match items to their rental prices under pressure.")
        round_key = "inventory_game_match_round"
        if round_key not in st.session_state:
            st.session_state[round_key] = build_match_round(stock)
        match_round = st.session_state[round_key]
        st.caption(_game_stats_caption("match_arena"))

        if st.button("Shuffle Match Round", key="match_round_new_btn"):
            st.session_state[round_key] = build_match_round(stock)
            st.rerun()
        match_round = st.session_state[round_key]
        if not match_round["items"]:
            st.info("Add at least 3 priced inventory items to unlock Match-Up Arena.")
        else:
            st.markdown("**Match each item with the correct rental price**")
            guesses: dict[str, str] = {}
            for idx, item_name in enumerate(match_round["items"]):
                c1, c2 = st.columns([1.2, 1])
                c1.markdown(f"- `{item_name}`")
                guesses[item_name] = c2.selectbox(
                    f"Price for {item_name}",
                    options=match_round["price_options"],
                    key=f"match_guess_{match_round['id']}_{idx}",
                    label_visibility="collapsed",
                )

            if st.button("Submit Match Answers", key=f"submit_match_round_{match_round['id']}"):
                answers = match_round["answers"]
                total = len(answers)
                correct = sum(1 for name, price in answers.items() if guesses.get(name) == price)
                _record_game_result("match_arena", correct == total)
                if correct == total:
                    st.success(f"Perfect round: {correct}/{total}.")
                else:
                    st.warning(f"You got {correct}/{total}. Review mismatches below.")
                    mismatches = [
                        {
                            "item_name": name,
                            "your_answer": guesses.get(name, ""),
                            "correct_price": price,
                        }
                        for name, price in answers.items()
                        if guesses.get(name) != price
                    ]
                    st.dataframe(pd.DataFrame(mismatches), hide_index=True, use_container_width=True)

def render_inventory() -> None:
    render_section_shell(
        "Inventory",
        "Edit pricing list and stock quantity while tracking usable availability windows.",
    )
    st.caption(
        "Track stock levels, rental pricing, and movement history with a simpler workflow."
    )
    st.caption(
        "Stock is updated automatically from `Confirmed Order` using each event's rental length "
        "(for example, a 24-hour rental reduces available stock during that window, then returns automatically)."
    )

    stock = cached_inventory_snapshot()
    default_reference_date_raw = st.session_state.get("invoice_event_date_input", date.today())
    if isinstance(default_reference_date_raw, datetime):
        default_reference_date = default_reference_date_raw.date()
    elif isinstance(default_reference_date_raw, date):
        default_reference_date = default_reference_date_raw
    else:
        default_reference_date = date.today()

    default_reference_time_raw = st.session_state.get("invoice_event_time_input", time(11, 0))
    if isinstance(default_reference_time_raw, datetime):
        default_reference_time = default_reference_time_raw.time().replace(second=0, microsecond=0)
    elif isinstance(default_reference_time_raw, time):
        default_reference_time = default_reference_time_raw.replace(second=0, microsecond=0)
    else:
        default_reference_time = time(11, 0)

    with st.expander("Add / Update Inventory Item", expanded=False):
        with st.form("inventory_item_form", clear_on_submit=True):
            item_name = st.text_input("Item Name *", placeholder="10x20 Tent")

            j1, j2 = st.columns(2)
            unit = j1.selectbox("Unit", ["pcs", "sets", "units", "boxes"])
            active = j2.selectbox("Status", ["Active", "Inactive"])

            k1, k2 = st.columns(2)
            stock_quantity = k1.number_input("Stock Quantity", min_value=0.0, step=1.0, value=0.0)
            default_rental_price = k2.number_input(
                "Default Rental Price (JMD)",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )

            save_item = st.form_submit_button("Save Inventory Item")

        if save_item:
            if not item_name.strip():
                st.error("Item Name is required.")
            else:
                try:
                    item_id = upsert_inventory_item(
                        item_name=item_name.strip(),
                        category="General",
                        unit=unit,
                        reorder_level=0.0,
                        default_rental_price=float(default_rental_price),
                        active=1 if active == "Active" else 0,
                    )
                    update_inventory_item_values(
                        item_id=item_id,
                        item_name=item_name.strip(),
                        category="General",
                        unit=unit,
                        reorder_level=0.0,
                        default_rental_price=float(default_rental_price),
                        active=1 if active == "Active" else 0,
                        target_quantity=float(stock_quantity),
                        movement_notes="Set from inventory item save form.",
                    )
                    st.success(f"Inventory item '{item_name.strip()}' saved.")
                except Exception as exc:
                    st.error(f"Could not save inventory item: {exc}")

    st.caption(
        "Tip: use `Pricing List` below to quickly edit prices and stock quantity, "
        "and use Availability Check Time there to see date/time-specific availability."
    )
    st.markdown("---")
    if stock.empty:
        st.info("No inventory items yet.")
        render_inventory_training_arcade(
            stock=stock,
            default_reference_date=default_reference_date,
            default_reference_time=default_reference_time,
        )
        return

    render_inventory_training_arcade(
        stock=stock,
        default_reference_date=default_reference_date,
        default_reference_time=default_reference_time,
    )


def render_reports(report_start_month: str) -> None:
    render_section_shell(
        "Reports",
        "Daily/monthly/yearly reporting, tax packs, exports, and reconciliation tools.",
    )
    experience = current_experience_mode()
    daily = apply_start_month(cached_daily_summary(), report_start_month)
    weekly = apply_start_month(cached_weekly_summary(), report_start_month)
    monthly = apply_start_month(cached_monthly_summary(), report_start_month)
    yearly = cached_yearly_summary()
    products = cached_product_profitability()
    product_types = cached_product_type_profitability()
    supplier_totals = cached_supplier_expenses()
    supplier_monthly = cached_supplier_monthly_expenses()
    supplier_rank = cached_supplier_performance_ranking()
    wages_daily = cached_wages_period("D")
    wages_weekly = cached_wages_period("W")
    wages_monthly = cached_wages_period("M")
    wages_yearly = cached_wages_period("Y")
    wages_person_monthly = apply_start_month(cached_wages_by_person_monthly(), report_start_month)
    expense_modes = cached_monthly_expense_modes()
    expenses_all = apply_start_month(cached_expenses(), report_start_month)
    category_budget_vs_actual = apply_start_month(
        cached_expense_category_budget_vs_actual(),
        report_start_month,
    )
    category_budget_rows = apply_start_month(cached_expense_category_budgets(), report_start_month)
    data_quality_checks = cached_finance_data_quality_checks()
    invoices = cached_invoice_level()
    budgets = apply_start_month(cached_budget_vs_actual(), report_start_month)
    tax_monthly = apply_start_month(cached_tax_pack_monthly(), report_start_month)
    tax_detail = cached_tax_pack_invoice_detail()

    if monthly.empty:
        st.info("No report data yet.")
        return

    if report_start_month and not yearly.empty:
        yearly = yearly[yearly["year"] >= int(report_start_month[:4])]
    if not supplier_monthly.empty:
        supplier_monthly = apply_start_month(supplier_monthly, report_start_month)
    if not expense_modes.empty:
        expense_modes = apply_start_month(expense_modes, report_start_month)
    if report_start_month:
        report_start_ts = pd.Period(report_start_month, freq="M").to_timestamp()
        if not wages_daily.empty:
            wages_daily = wages_daily[wages_daily["period_start"] >= report_start_ts]
        if not wages_weekly.empty:
            wages_weekly = wages_weekly[wages_weekly["period_start"] >= report_start_ts]
        if not wages_monthly.empty:
            wages_monthly = wages_monthly[wages_monthly["period_start"] >= report_start_ts]
        if not wages_yearly.empty:
            wages_yearly = wages_yearly[wages_yearly["period_start"].dt.year >= int(report_start_month[:4])]

    year_values = sorted([int(y) for y in monthly["year"].dropna().unique()])
    filter_col1, filter_col2 = st.columns([1.1, 2.1])
    report_period = filter_col1.selectbox(
        "Period Filter",
        options=["Daily", "Weekly", "Monthly", "Yearly"],
        index=2,
        key="reports_global_period_filter",
    )
    report_scope = "All Filtered Rows"
    picker_today = jamaica_now().date()
    calendar_start_ts: pd.Timestamp | None = None
    calendar_end_ts: pd.Timestamp | None = None
    selected_years: list[int] = year_values.copy()
    if report_period == "Daily":
        chosen_day = filter_col2.date_input(
            "Specific Day",
            value=picker_today,
            key="reports_calendar_day_picker",
        )
        calendar_start_ts = pd.Timestamp(chosen_day)
        calendar_end_ts = pd.Timestamp(chosen_day)
    elif report_period == "Weekly":
        chosen_week_day = filter_col2.date_input(
            "Specific Week (pick any day in the week)",
            value=picker_today,
            key="reports_calendar_week_picker",
        )
        week_start = chosen_week_day - timedelta(days=int(chosen_week_day.weekday()))
        week_end = week_start + timedelta(days=6)
        calendar_start_ts = pd.Timestamp(week_start)
        calendar_end_ts = pd.Timestamp(week_end)
    elif report_period == "Monthly":
        chosen_month_day = filter_col2.date_input(
            "Specific Month (pick any day in the month)",
            value=picker_today,
            key="reports_calendar_month_picker",
        )
        month_start = chosen_month_day.replace(day=1)
        month_end = ((month_start + timedelta(days=32)).replace(day=1)) - timedelta(days=1)
        calendar_start_ts = pd.Timestamp(month_start)
        calendar_end_ts = pd.Timestamp(month_end)
    else:
        chosen_year_day = filter_col2.date_input(
            "Specific Year (pick any day in the year)",
            value=picker_today,
            key="reports_calendar_year_picker",
        )
        calendar_start_ts = pd.Timestamp(year=int(chosen_year_day.year), month=1, day=1)
        calendar_end_ts = pd.Timestamp(year=int(chosen_year_day.year), month=12, day=31)

    if calendar_start_ts is not None and calendar_end_ts is not None:
        selected_years = list(range(int(calendar_start_ts.year), int(calendar_end_ts.year) + 1))
        st.caption(
            f"Calendar filter active: {calendar_start_ts.date().isoformat()} to {calendar_end_ts.date().isoformat()}."
        )
    elif not selected_years:
        selected_years = [jamaica_now().year]

    if selected_years:
        if not daily.empty:
            daily = daily[daily["year"].isin(selected_years)]
        if not weekly.empty:
            weekly = weekly[weekly["year"].isin(selected_years)]
        monthly = monthly[monthly["year"].isin(selected_years)]
        yearly = yearly[yearly["year"].isin(selected_years)]
        invoices = invoices[invoices["year"].isin(selected_years)]
        if not tax_monthly.empty:
            tax_month_year = pd.PeriodIndex(tax_monthly["month"], freq="M").year
            tax_monthly = tax_monthly[tax_month_year.isin(selected_years)]
        if not tax_detail.empty:
            tax_detail = tax_detail[
                pd.to_datetime(tax_detail["event_date"], errors="coerce").dt.year.isin(selected_years)
            ]
        if not budgets.empty:
            budget_year = pd.PeriodIndex(budgets["month"], freq="M").year
            budgets = budgets[budget_year.isin(selected_years)]
        if not wages_daily.empty:
            wages_daily = wages_daily[wages_daily["period_start"].dt.year.isin(selected_years)]
        if not wages_weekly.empty:
            wages_weekly = wages_weekly[wages_weekly["period_start"].dt.year.isin(selected_years)]
        if not wages_monthly.empty:
            wages_monthly = wages_monthly[wages_monthly["period_start"].dt.year.isin(selected_years)]
        if not wages_yearly.empty:
            wages_yearly = wages_yearly[wages_yearly["period_start"].dt.year.isin(selected_years)]
        if not wages_person_monthly.empty:
            wages_person_year = pd.PeriodIndex(wages_person_monthly["month"], freq="M").year
            wages_person_monthly = wages_person_monthly[wages_person_year.isin(selected_years)]
        if not expenses_all.empty:
            expenses_all = expenses_all[expenses_all["year"].isin(selected_years)]
        if not category_budget_vs_actual.empty:
            cb_year = pd.PeriodIndex(category_budget_vs_actual["month"], freq="M").year
            category_budget_vs_actual = category_budget_vs_actual[cb_year.isin(selected_years)]
        if not category_budget_rows.empty:
            cbr_year = pd.PeriodIndex(category_budget_rows["month"], freq="M").year
            category_budget_rows = category_budget_rows[cbr_year.isin(selected_years)]

    summary_base = pd.DataFrame()
    summary_label_col = ""
    summary_key_col = ""
    if report_period == "Daily":
        summary_base = daily.copy().sort_values("day")
        summary_label_col = "day_label"
        summary_key_col = "day"
    elif report_period == "Weekly":
        summary_base = weekly.copy().sort_values("week_start")
        summary_label_col = "week_label"
        summary_key_col = "week_start"
    elif report_period == "Monthly":
        summary_base = monthly.copy().sort_values("month")
        summary_label_col = "month_label"
        summary_key_col = "month"
    else:
        summary_base = yearly.copy().sort_values("year")
        summary_label_col = "year"
        summary_key_col = "year"

    summary_selected_token = None
    summary_selected_label = f"All {report_period.lower()} rows"
    summary_filtered = summary_base.copy()
    if report_scope == "Selected Row" and not summary_base.empty:
        option_map = {
            str(row[summary_label_col]): row[summary_key_col]
            for _, row in summary_base.iterrows()
        }
        summary_selected_label = st.selectbox(
            "Selected Period Row",
            options=list(option_map.keys()),
            key="reports_global_selected_row",
        )
        summary_selected_token = option_map[summary_selected_label]
        summary_filtered = summary_base[
            summary_base[summary_key_col].astype(str) == str(summary_selected_token)
        ].copy()

    selected_start_ts: pd.Timestamp | None = calendar_start_ts
    selected_end_ts: pd.Timestamp | None = calendar_end_ts
    selected_month_tokens: set[str] = set()
    selected_year_tokens: set[int] = set()

    if report_scope == "Selected Row" and summary_selected_token is not None:
        if report_period == "Daily":
            selected_start_ts = pd.to_datetime(str(summary_selected_token), errors="coerce")
            selected_end_ts = selected_start_ts
        elif report_period == "Weekly":
            selected_start_ts = pd.to_datetime(str(summary_selected_token), errors="coerce")
            if pd.notna(selected_start_ts):
                selected_end_ts = selected_start_ts + pd.to_timedelta(6, unit="D")
        elif report_period == "Monthly":
            period = pd.Period(str(summary_selected_token), freq="M")
            selected_start_ts = period.start_time
            selected_end_ts = period.end_time.normalize()
        else:
            year_token = int(float(summary_selected_token))
            selected_start_ts = pd.Timestamp(year=year_token, month=1, day=1)
            selected_end_ts = pd.Timestamp(year=year_token, month=12, day=31)

    if (
        selected_start_ts is not None
        and selected_end_ts is not None
        and pd.notna(selected_start_ts)
        and pd.notna(selected_end_ts)
    ):
        start_period = selected_start_ts.to_period("M")
        end_period = selected_end_ts.to_period("M")
        cursor_period = start_period
        while cursor_period <= end_period:
            selected_month_tokens.add(str(cursor_period))
            cursor_period = cursor_period + 1
        selected_year_tokens = {int(year) for year in range(selected_start_ts.year, selected_end_ts.year + 1)}
    else:
        if "month" in summary_filtered.columns and not summary_filtered.empty:
            selected_month_tokens = set(summary_filtered["month"].dropna().astype(str).tolist())
        if report_period == "Yearly" and "year" in summary_filtered.columns and not summary_filtered.empty:
            selected_year_tokens = {int(y) for y in summary_filtered["year"].dropna().tolist()}
        elif selected_years:
            selected_year_tokens = {int(y) for y in selected_years}

    def _filter_by_month_tokens(frame: pd.DataFrame, month_col: str = "month") -> pd.DataFrame:
        if frame.empty or not selected_month_tokens or month_col not in frame.columns:
            return frame
        return frame[frame[month_col].astype(str).isin(selected_month_tokens)].copy()

    def _filter_by_year_tokens(frame: pd.DataFrame, year_col: str = "year") -> pd.DataFrame:
        if frame.empty or not selected_year_tokens or year_col not in frame.columns:
            return frame
        return frame[pd.to_numeric(frame[year_col], errors="coerce").isin(selected_year_tokens)].copy()

    if (
        selected_start_ts is not None
        and selected_end_ts is not None
        and pd.notna(selected_start_ts)
        and pd.notna(selected_end_ts)
    ):
        if not invoices.empty:
            invoice_dates = pd.to_datetime(invoices["event_date"], errors="coerce")
            invoices = invoices[(invoice_dates >= selected_start_ts) & (invoice_dates <= selected_end_ts)].copy()
        if not expenses_all.empty:
            expense_dates = pd.to_datetime(expenses_all["finance_date"], errors="coerce")
            expenses_all = expenses_all[
                (expense_dates >= selected_start_ts) & (expense_dates <= selected_end_ts)
            ].copy()
        if not supplier_monthly.empty:
            supplier_monthly = _filter_by_month_tokens(supplier_monthly, "month")
        budgets = _filter_by_month_tokens(budgets, "month")
        expense_modes = _filter_by_month_tokens(expense_modes, "month")
        category_budget_vs_actual = _filter_by_month_tokens(category_budget_vs_actual, "month")
        category_budget_rows = _filter_by_month_tokens(category_budget_rows, "month")
        if not tax_monthly.empty:
            tax_monthly = _filter_by_month_tokens(tax_monthly, "month")
        if not tax_detail.empty:
            tax_dates = pd.to_datetime(tax_detail["event_date"], errors="coerce")
            tax_detail = tax_detail[(tax_dates >= selected_start_ts) & (tax_dates <= selected_end_ts)].copy()
        if not wages_daily.empty:
            wages_daily = wages_daily[
                (wages_daily["period_start"] >= selected_start_ts)
                & (wages_daily["period_start"] <= selected_end_ts)
            ].copy()
        if not wages_weekly.empty:
            wages_week_end = wages_weekly["period_start"] + pd.to_timedelta(6, unit="D")
            wages_weekly = wages_weekly[
                (wages_weekly["period_start"] <= selected_end_ts)
                & (wages_week_end >= selected_start_ts)
            ].copy()
        if not wages_monthly.empty:
            wages_monthly = wages_monthly[
                wages_monthly["period_start"].dt.to_period("M").astype(str).isin(selected_month_tokens)
            ].copy()
        if not wages_yearly.empty:
            wages_yearly = wages_yearly[
                wages_yearly["period_start"].dt.year.isin(selected_year_tokens)
            ].copy()
        if not wages_person_monthly.empty:
            wages_person_monthly = _filter_by_month_tokens(wages_person_monthly, "month")

    if not supplier_monthly.empty:
        supplier_totals = (
            supplier_monthly.groupby("vendor", as_index=False)["amount"]
            .sum()
            .sort_values("amount", ascending=False)
        )
        if not supplier_rank.empty:
            supplier_rank = supplier_rank[
                supplier_rank["vendor"].isin(supplier_totals["vendor"].dropna().astype(str))
            ].copy()
    else:
        supplier_totals = pd.DataFrame(columns=["vendor", "amount"])
        supplier_rank = pd.DataFrame(
            columns=[
                "vendor",
                "supplier_spend",
                "transaction_count",
                "linked_event_count",
                "linked_revenue",
                "avg_spend_per_event",
                "margin_impact_pct",
            ]
        )

    if experience != "Data Dense":
        st.markdown("**Visual Lab**")
        st.caption("Charts follow the same Year/Period/Scope filters.")
        if summary_filtered.empty:
            st.caption("No chart rows for current filters.")
        else:
            metric_profit_col = (
                "net_profit_after_adjustments"
                if "net_profit_after_adjustments" in summary_filtered.columns
                else "net_profit"
            )
            chart_source = summary_filtered.copy()
            chart_source["_label"] = chart_source[summary_label_col].astype(str)
            chart_source = chart_source.sort_values(summary_key_col)
            for metric_col in ["revenue", "total_expenses", metric_profit_col]:
                if metric_col in chart_source.columns:
                    chart_source[metric_col] = pd.to_numeric(chart_source[metric_col], errors="coerce").fillna(0.0)
            c1, c2 = st.columns([1.2, 1.0])
            with c1:
                overview_fig = px.bar(
                    chart_source,
                    x="_label",
                    y=["revenue", "total_expenses", metric_profit_col],
                    barmode="group",
                    title=f"{report_period} Performance Compare",
                    labels={"_label": report_period, "value": "Amount (JMD)", "variable": "Metric"},
                )
                style_plotly(overview_fig)
                st.plotly_chart(overview_fig, use_container_width=True)
            with c2:
                cumulative_source = chart_source.copy()
                cumulative_source["cumulative_profit"] = (
                    pd.to_numeric(cumulative_source[metric_profit_col], errors="coerce").fillna(0.0).cumsum()
                )
                cumulative = px.line(
                    cumulative_source,
                    x="_label",
                    y="cumulative_profit",
                    markers=True,
                    title=f"{report_period} Cumulative Net",
                    labels={"_label": report_period, "cumulative_profit": "Cumulative Net (JMD)"},
                )
                cumulative.update_traces(line={"color": PRIMARY_COLOR, "width": 4})
                style_plotly(cumulative)
                st.plotly_chart(cumulative, use_container_width=True)

    st.markdown("**Finance Summary (Unified Period View)**")
    if report_period == "Daily":
        if summary_filtered.empty:
            st.caption("No daily finance rows yet.")
        else:
            daily_show = summary_filtered.copy().sort_values("day")
            for col in [
                "revenue",
                "cash_collected",
                "outstanding_receivables",
                "recurring_expenses",
                "summarized_expenses",
                "total_expenses",
                "net_profit",
            ]:
                daily_show[col] = daily_show[col].map(money)
            render_paginated_dataframe(
                daily_show[
                    [
                        "day_label",
                        "revenue",
                        "cash_collected",
                        "outstanding_receivables",
                        "recurring_expenses",
                        "summarized_expenses",
                        "total_expenses",
                        "net_profit",
                    ]
                ],
                key_prefix="reports_daily_summary",
                page_size_default=15,
            )
    elif report_period == "Weekly":
        if summary_filtered.empty:
            st.caption("No weekly finance rows yet.")
        else:
            weekly_show = summary_filtered.copy().sort_values("week_start")
            for col in [
                "revenue",
                "cash_collected",
                "outstanding_receivables",
                "recurring_expenses",
                "summarized_expenses",
                "total_expenses",
                "net_profit",
            ]:
                weekly_show[col] = weekly_show[col].map(money)
            render_paginated_dataframe(
                weekly_show[
                    [
                        "week_label",
                        "revenue",
                        "cash_collected",
                        "outstanding_receivables",
                        "recurring_expenses",
                        "summarized_expenses",
                        "total_expenses",
                        "net_profit",
                    ]
                ],
                key_prefix="reports_weekly_summary",
                page_size_default=12,
            )
    elif report_period == "Monthly":
        monthly_show = summary_filtered.copy()
        for col in [
            "revenue",
            "cash_collected",
            "outstanding_receivables",
            "linked_expenses",
            "general_expenses",
            "recurring_expenses",
            "summarized_expenses",
            "total_expenses",
            "adjustments",
            "net_profit",
            "net_profit_after_adjustments",
        ]:
            monthly_show[col] = monthly_show[col].map(money)
        render_paginated_dataframe(
            monthly_show[
                [
                    "month_label",
                    "revenue",
                    "cash_collected",
                    "outstanding_receivables",
                    "recurring_expenses",
                    "summarized_expenses",
                    "total_expenses",
                    "adjustments",
                    "net_profit",
                    "net_profit_after_adjustments",
                ]
            ],
            key_prefix="reports_monthly_summary",
            page_size_default=12,
        )
    else:
        if summary_filtered.empty:
            st.caption("No yearly rows yet.")
        else:
            yearly_show = summary_filtered.copy()
            for col in [
                "revenue",
                "cash_collected",
                "outstanding_receivables",
                "linked_expenses",
                "general_expenses",
                "recurring_expenses",
                "summarized_expenses",
                "total_expenses",
                "adjustments",
                "net_profit",
                "net_profit_after_adjustments",
            ]:
                yearly_show[col] = yearly_show[col].map(money)
            render_paginated_dataframe(
                yearly_show[
                    [
                        "year",
                        "revenue",
                        "cash_collected",
                        "outstanding_receivables",
                        "recurring_expenses",
                        "summarized_expenses",
                        "total_expenses",
                        "adjustments",
                        "net_profit",
                        "net_profit_after_adjustments",
                    ]
                ],
                key_prefix="reports_yearly_summary",
                page_size_default=10,
            )

    st.markdown("**Budget vs Actual (Monthly)**")
    budget_months = cached_monthly_budgets()
    with st.form("reports_budget_form", clear_on_submit=True):
        b1, b2, b3, b4 = st.columns([1.1, 1.1, 1.2, 1.6])
        budget_month_date = b1.date_input(
            "Budget Month",
            value=date.today().replace(day=1),
        )
        budget_revenue = b2.number_input(
            "Revenue Target (JMD)",
            min_value=0.0,
            step=1000.0,
            value=0.0,
        )
        budget_expense = b3.number_input(
            "Expense Target (JMD)",
            min_value=0.0,
            step=1000.0,
            value=0.0,
        )
        budget_notes = b4.text_input("Notes")
        save_budget = st.form_submit_button("Save Monthly Budget")

    if save_budget:
        try:
            month_token = f"{budget_month_date.year:04d}-{budget_month_date.month:02d}"
            upsert_monthly_budget(
                month=month_token,
                revenue_target=float(budget_revenue),
                expense_target=float(budget_expense),
                notes=budget_notes.strip(),
            )
            finance_audit_log(
                entity_type="budget",
                entity_id=None,
                action_type="upsert",
                notes=f"{month_token} | Revenue target {money(float(budget_revenue))} | Expense target {money(float(budget_expense))}",
            )
            clear_finance_caches()
            st.success(f"Budget saved for {month_token}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save budget: {exc}")

    if budgets.empty:
        st.caption("No budget rows yet.")
    else:
        budget_show = budgets.copy().sort_values("month")
        budget_show["revenue_target"] = budget_show["revenue_target"].map(money)
        budget_show["revenue_actual"] = budget_show["revenue_actual"].map(money)
        budget_show["revenue_variance"] = budget_show["revenue_variance"].map(money)
        budget_show["revenue_variance_pct"] = budget_show["revenue_variance_pct"].map(lambda x: f"{x:,.1f}%")
        budget_show["expense_target"] = budget_show["expense_target"].map(money)
        budget_show["expense_actual"] = budget_show["expense_actual"].map(money)
        budget_show["expense_variance"] = budget_show["expense_variance"].map(money)
        budget_show["expense_variance_pct"] = budget_show["expense_variance_pct"].map(lambda x: f"{x:,.1f}%")
        render_paginated_dataframe(
            budget_show[
                [
                    "month_label",
                    "revenue_target",
                    "revenue_actual",
                    "revenue_variance",
                    "revenue_variance_pct",
                    "expense_target",
                    "expense_actual",
                    "expense_variance",
                    "expense_variance_pct",
                    "notes",
                ]
            ],
            key_prefix="reports_budget_vs_actual",
            page_size_default=12,
        )

    if not budget_months.empty:
        delete_choices = {
            f"{row['month']} | Revenue {money(float(row.get('revenue_target', 0.0) or 0.0))} | Expense {money(float(row.get('expense_target', 0.0) or 0.0))}": str(
                row["month"]
            )
            for _, row in budget_months.iterrows()
        }
        if delete_choices:
            d1, d2 = st.columns([2.4, 1.1])
            selected_budget = d1.selectbox(
                "Delete Saved Budget (optional)",
                options=list(delete_choices.keys()),
                key="reports_delete_budget_selector",
            )
            if d2.button("Delete Budget", key="reports_delete_budget_btn"):
                try:
                    month_to_delete = delete_choices[selected_budget]
                    if delete_monthly_budget(month_to_delete):
                        finance_audit_log(
                            entity_type="budget",
                            entity_id=None,
                            action_type="delete",
                            notes=f"Deleted budget for {month_to_delete}",
                        )
                        clear_finance_caches()
                        st.success(f"Budget deleted for {month_to_delete}.")
                        st.rerun()
                    else:
                        st.info("Selected budget row was already removed.")
                except Exception as exc:
                    st.error(f"Could not delete budget: {exc}")

    st.markdown("**Expense Budget vs Actual (Category-Level)**")
    with st.form("reports_category_budget_form", clear_on_submit=True):
        cb1, cb2, cb3, cb4 = st.columns([1.1, 1.2, 1.1, 1.6])
        cb_month = cb1.date_input(
            "Budget Month",
            value=date.today().replace(day=1),
            key="reports_category_budget_month",
        )
        cb_category = cb2.selectbox(
            "Category",
            options=FINANCE_CATEGORY_OPTIONS,
            key="reports_category_budget_category",
        )
        cb_amount = cb3.number_input(
            "Target (JMD)",
            min_value=0.0,
            step=500.0,
            value=0.0,
            key="reports_category_budget_amount",
        )
        cb_notes = cb4.text_input(
            "Notes",
            key="reports_category_budget_notes",
        )
        save_category_budget = st.form_submit_button("Save Category Budget")

    if save_category_budget:
        try:
            month_token = f"{cb_month.year:04d}-{cb_month.month:02d}"
            category_token = normalize_expense_category(cb_category)
            upsert_expense_category_budget(
                month=month_token,
                category=category_token,
                amount_target=float(cb_amount),
                notes=cb_notes.strip(),
            )
            finance_audit_log(
                entity_type="category_budget",
                entity_id=None,
                action_type="upsert",
                notes=f"{month_token} | {category_token} | target={money(float(cb_amount))}",
            )
            clear_finance_caches()
            st.success("Category budget saved.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save category budget: {exc}")

    if category_budget_vs_actual.empty:
        st.caption("No category budget rows yet.")
    else:
        category_show = category_budget_vs_actual.copy().sort_values(["month", "category"])
        category_show["amount_target"] = category_show["amount_target"].map(money)
        category_show["amount_actual"] = category_show["amount_actual"].map(money)
        category_show["variance"] = category_show["variance"].map(money)
        category_show["variance_pct"] = category_show["variance_pct"].map(lambda x: f"{float(x):,.1f}%")
        render_paginated_dataframe(
            category_show[
                [
                    "month_label",
                    "category",
                    "amount_target",
                    "amount_actual",
                    "variance",
                    "variance_pct",
                    "notes",
                ]
            ],
            key_prefix="reports_category_budget_vs_actual",
            page_size_default=20,
        )

    if not category_budget_rows.empty:
        delete_category_choices = {
            f"{row['month']} | {row['category']} | Target {money(float(row.get('amount_target', 0.0) or 0.0))}": (
                str(row["month"]),
                str(row["category"]),
            )
            for _, row in category_budget_rows.iterrows()
        }
        if delete_category_choices:
            dc1, dc2 = st.columns([2.4, 1.1])
            selected_cat_budget = dc1.selectbox(
                "Delete Category Budget (optional)",
                options=list(delete_category_choices.keys()),
                key="reports_delete_category_budget_selector",
            )
            if dc2.button("Delete Category Budget", key="reports_delete_category_budget_btn"):
                try:
                    month_token, category_token = delete_category_choices[selected_cat_budget]
                    removed = delete_expense_category_budget(month_token, category_token)
                    if removed:
                        finance_audit_log(
                            entity_type="category_budget",
                            entity_id=None,
                            action_type="delete",
                            notes=f"Deleted category budget {month_token} | {category_token}",
                        )
                        clear_finance_caches()
                        st.success("Category budget deleted.")
                        st.rerun()
                    else:
                        st.info("Selected category budget row was already removed.")
                except Exception as exc:
                    st.error(f"Could not delete category budget: {exc}")

    st.markdown("**Weekly Cashflow Forecast**")
    st.caption(
        "Forecast uses expected invoice collections (outstanding balances by event week) minus recurring template due amounts and recent average weekly variable expenses."
    )
    fc1, fc2 = st.columns(2)
    forecast_weeks = int(
        fc1.selectbox(
            "Forecast Horizon",
            options=[4, 6, 8, 12],
            index=2,
            key="reports_forecast_horizon_weeks",
        )
    )
    history_weeks = int(
        fc2.selectbox(
            "History Weeks for Expense Baseline",
            options=[4, 8, 12],
            index=1,
            key="reports_forecast_history_weeks",
        )
    )
    forecast = cached_weekly_cashflow_forecast(weeks=forecast_weeks, history_weeks=history_weeks)
    if forecast.empty:
        st.caption("No forecast rows available.")
    else:
        forecast_show = forecast.copy()
        for col in [
            "inflow_expected",
            "outflow_expected",
            "net_expected",
            "cumulative_net",
            "avg_weekly_variable_expense",
            "recurring_due",
        ]:
            forecast_show[col] = forecast_show[col].map(money)
        render_paginated_dataframe(
            forecast_show[
                [
                    "week_label",
                    "inflow_expected",
                    "outflow_expected",
                    "net_expected",
                    "cumulative_net",
                    "avg_weekly_variable_expense",
                    "recurring_due",
                ]
            ],
            key_prefix="reports_weekly_cashflow_forecast",
            page_size_default=12,
        )

    st.markdown("**Data Quality Checks**")
    if data_quality_checks.empty:
        st.caption("No data quality issues detected.")
    else:
        dq_show = data_quality_checks.copy()
        dq_show["severity"] = dq_show["severity"].str.title()
        render_paginated_dataframe(
            dq_show[
                [
                    "issue",
                    "count",
                    "severity",
                    "recommendation",
                ]
            ],
            key_prefix="reports_data_quality_checks",
            page_size_default=10,
        )
        issue_choices = dq_show["issue_key"].dropna().astype(str).tolist()
        selected_issue_key = st.selectbox(
            "Inspect Issue Details",
            options=issue_choices,
            key="reports_data_quality_issue_selector",
        )
        issue_rows = pd.DataFrame()
        if not expenses_all.empty:
            scoped_dq = expenses_all.copy()
            scoped_dq["finance_date"] = pd.to_datetime(scoped_dq["finance_date"], errors="coerce")
            scoped_dq["expense_date"] = pd.to_datetime(scoped_dq["expense_date"], errors="coerce")
            scoped_dq = scoped_dq[
                ~scoped_dq["expense_kind"].fillna("").astype(str).str.lower().isin(["summary_rollup", "recurring_draft"])
            ]
            if selected_issue_key == "missing_or_other_category":
                issue_rows = scoped_dq[
                    scoped_dq["category"].fillna("").astype(str).str.strip().isin(["", "Other"])
                ][["id", "expense_date", "category", "vendor", "description", "amount"]]
            elif selected_issue_key == "missing_vendor_critical_categories":
                critical = {"wages", "re-rental", "google ads", "facebook ads", "chatgpt", "shopify"}
                issue_rows = scoped_dq[
                    scoped_dq["category"].fillna("").str.lower().isin(critical)
                    & (scoped_dq["vendor"].fillna("").astype(str).str.strip() == "")
                ][["id", "expense_date", "category", "vendor", "description", "amount"]]
            elif selected_issue_key == "non_standard_category_labels":
                issue_rows = scoped_dq[
                    scoped_dq["category_raw"].fillna("").astype(str).str.strip()
                    != scoped_dq["category"].fillna("").astype(str).str.strip()
                ][["id", "expense_date", "category_raw", "category", "vendor", "description", "amount"]]
            elif selected_issue_key == "transaction_without_invoice_link":
                issue_rows = scoped_dq[
                    (scoped_dq["expense_kind"].fillna("").str.lower() == "transaction")
                    & (scoped_dq["invoice_id"].isna())
                ][["id", "expense_date", "category", "vendor", "description", "amount"]]
            elif selected_issue_key == "possible_duplicates":
                dup_scan = scoped_dq.copy()
                dup_scan["finance_day"] = dup_scan["finance_date"].dt.strftime("%Y-%m-%d")
                dup_scan["vendor_norm"] = dup_scan["vendor"].fillna("").astype(str).str.strip().str.lower()
                dup_scan["category_norm"] = dup_scan["category"].fillna("").astype(str).str.strip().str.lower()
                dup_scan["amount_round"] = pd.to_numeric(dup_scan["amount"], errors="coerce").fillna(0.0).round(2)
                dup_scan = dup_scan.dropna(subset=["finance_day"])
                dup_hits = (
                    dup_scan.groupby(["finance_day", "category_norm", "vendor_norm", "amount_round"], as_index=False)["id"]
                    .count()
                    .rename(columns={"id": "entry_count"})
                )
                dup_hits = dup_hits[dup_hits["entry_count"] > 1]
                if not dup_hits.empty:
                    issue_rows = dup_hits.rename(
                        columns={
                            "finance_day": "day",
                            "category_norm": "category",
                            "vendor_norm": "vendor",
                            "amount_round": "amount",
                        }
                    )[["day", "category", "vendor", "amount", "entry_count"]]
        if selected_issue_key == "invoice_missing_customer_name" and not invoices.empty:
            issue_rows = invoices[
                invoices["customer_name"].fillna("").astype(str).str.strip() == ""
            ][["invoice_number", "event_date", "customer_name", "revenue", "amount_outstanding"]]

        if issue_rows.empty:
            st.caption("No detail rows for this issue in current filters.")
        else:
            issue_display = issue_rows.copy()
            if "expense_date" in issue_display.columns:
                issue_display["expense_date"] = pd.to_datetime(
                    issue_display["expense_date"], errors="coerce"
                ).dt.date.astype("string")
            for money_col in ["amount", "revenue", "amount_outstanding"]:
                if money_col in issue_display.columns:
                    issue_display[money_col] = pd.to_numeric(
                        issue_display[money_col], errors="coerce"
                    ).fillna(0.0).map(money)
            render_paginated_dataframe(
                issue_display,
                key_prefix="reports_data_quality_issue_rows",
                page_size_default=15,
            )

    st.markdown("**Deposit & Outstanding Tracker**")
    if invoices.empty:
        st.caption("No invoice payment records yet.")
    else:
        pending_payments = invoices[invoices["amount_outstanding"] > 0.01].copy()
        if pending_payments.empty:
            st.caption("No outstanding balances. All confirmed orders are paid in full.")
        else:
            pending_payments = pending_payments.sort_values("event_date")
            pending_payments["event_date"] = pending_payments["event_date"].dt.date.astype("string")
            pending_payments["revenue"] = pending_payments["revenue"].map(money)
            pending_payments["amount_paid"] = pending_payments["amount_paid"].map(money)
            pending_payments["amount_outstanding"] = pending_payments["amount_outstanding"].map(money)
            st.warning(
                f"{len(pending_payments)} invoice(s) still have outstanding balances."
            )
            render_paginated_dataframe(
                pending_payments[
                    [
                        "invoice_number",
                        "event_date",
                        "customer_name",
                        "revenue",
                        "amount_paid",
                        "amount_outstanding",
                        "payment_reminder",
                    ]
                ],
                key_prefix="reports_outstanding_balances",
                page_size_default=10,
            )

    st.markdown("**Expense Modes (Recurring vs Summarized)**")
    if expense_modes.empty:
        st.caption("No expenses yet.")
    else:
        modes = expense_modes.copy()
        if selected_years:
            mode_year = pd.PeriodIndex(modes["month"], freq="M").year
            modes = modes[mode_year.isin(selected_years)]
        modes = modes.rename(
            columns={
                "recurring_monthly": "Recurring Monthly",
                "summarized_from_transactions": "Summarized From Daily/Invoice",
                "summary_reference_rollups": "Monthly Rollup Reference",
                "other_expenses_used": "Other Expenses Used",
                "total_used": "Total Used In Profit",
            }
        )
        for col in [
            "Recurring Monthly",
            "Summarized From Daily/Invoice",
            "Monthly Rollup Reference",
            "Other Expenses Used",
            "Total Used In Profit",
        ]:
            modes[col] = modes[col].map(money)
        render_paginated_dataframe(
            modes[
                [
                    "month_label",
                    "Recurring Monthly",
                    "Summarized From Daily/Invoice",
                    "Monthly Rollup Reference",
                    "Other Expenses Used",
                    "Total Used In Profit",
                ]
            ],
            key_prefix="reports_expense_modes",
            page_size_default=12,
        )

    st.markdown("**Product Profitability**")
    if products.empty:
        st.caption("No product rows yet.")
    else:
        product_show = products.copy()
        product_show["revenue"] = product_show["revenue"].map(money)
        product_show["direct_cost"] = product_show["direct_cost"].map(money)
        product_show["allocated_expenses"] = product_show["allocated_expenses"].map(money)
        product_show["net_profit"] = product_show["net_profit"].map(money)
        product_show["margin_pct"] = product_show["margin_pct"].map(lambda x: f"{x:,.1f}%")
        render_paginated_dataframe(
            product_show,
            key_prefix="reports_product_profit",
            page_size_default=15,
        )

    st.markdown("**Category P&L (Product Type)**")
    if product_types.empty:
        st.caption("No product-type profitability rows yet.")
    else:
        type_show = product_types.copy()
        for col in ["revenue", "direct_cost", "allocated_expenses", "net_profit"]:
            type_show[col] = type_show[col].map(money)
        type_show["margin_pct"] = type_show["margin_pct"].map(lambda x: f"{x:,.1f}%")
        render_paginated_dataframe(
            type_show,
            key_prefix="reports_product_type_pnl",
            page_size_default=12,
        )

    st.markdown("**Supplier Expenses (Re-Rental)**")
    if supplier_totals.empty:
        st.caption("No supplier-level re-rental expenses yet.")
    else:
        supplier_show = supplier_totals.copy()
        supplier_show["amount"] = supplier_show["amount"].map(money)
        render_paginated_dataframe(
            supplier_show,
            key_prefix="reports_supplier_totals",
            page_size_default=12,
        )

    st.markdown("**Supplier by Month (Re-Rental)**")
    if supplier_monthly.empty:
        st.caption("No monthly supplier rows yet.")
    else:
        supplier_filtered = supplier_monthly.copy()
        supplier_options = sorted(supplier_filtered["vendor"].dropna().astype(str).unique().tolist())
        selected_suppliers = st.multiselect(
            "Supplier Filter",
            options=supplier_options,
            default=supplier_options,
            key="reports_supplier_monthly_filter",
        )
        if selected_suppliers:
            supplier_filtered = supplier_filtered[supplier_filtered["vendor"].isin(selected_suppliers)]
        else:
            supplier_filtered = supplier_filtered.iloc[0:0]

        if supplier_filtered.empty:
            st.caption("No rows for the selected supplier filter.")
        else:
            supplier_table = supplier_filtered.copy().sort_values(["month", "amount"], ascending=[True, False])
            supplier_table["amount"] = supplier_table["amount"].map(money)
            render_paginated_dataframe(
                supplier_table[
                    [
                        "month_label",
                        "vendor",
                        "amount",
                    ]
                ],
                key_prefix="reports_supplier_monthly_table",
                page_size_default=20,
            )

            supplier_fig = px.bar(
                supplier_filtered,
                x="month_label",
                y="amount",
                color="vendor",
                barmode="stack",
                title="Monthly Supplier Spend",
                labels={"month_label": "Month", "amount": "Amount (JMD)", "vendor": "Supplier"},
            )
            style_plotly(supplier_fig)
            st.plotly_chart(supplier_fig, use_container_width=True)

    st.markdown("**Supplier Performance Ranking**")
    if supplier_rank.empty:
        st.caption("No supplier performance rows yet.")
    else:
        supplier_rank_show = supplier_rank.copy()
        supplier_rank_show["supplier_spend"] = supplier_rank_show["supplier_spend"].map(money)
        supplier_rank_show["linked_revenue"] = supplier_rank_show["linked_revenue"].map(money)
        supplier_rank_show["avg_spend_per_event"] = supplier_rank_show["avg_spend_per_event"].map(money)
        supplier_rank_show["margin_impact_pct"] = supplier_rank_show["margin_impact_pct"].map(
            lambda x: f"{float(x):,.1f}%"
        )
        render_paginated_dataframe(
            supplier_rank_show,
            key_prefix="reports_supplier_performance",
            page_size_default=12,
        )

    st.markdown("**Wages Summary (Daily / Weekly / Monthly / Yearly)**")
    if (
        wages_daily.empty
        and wages_weekly.empty
        and wages_monthly.empty
        and wages_yearly.empty
    ):
        st.caption("No wages records yet.")
    else:
        wd_tab, ww_tab, wm_tab, wy_tab = st.tabs(
            ["Daily", "Weekly", "Monthly", "Yearly"]
        )

        def _render_wages_table(source_df: pd.DataFrame, label_col_name: str) -> None:
            if source_df.empty:
                st.caption("No rows for this period.")
                return
            show = source_df.copy().sort_values("period_start")
            show = show.rename(
                columns={
                    "period_label": label_col_name,
                    "wages_person_level": "Person-Level Wages",
                    "wages_summary_topups": "Invoice Summary Top-Ups",
                    "wages_monthly_sheet_rollup": "Monthly Sheet Rollup",
                    "wages_total_used": "Total Wages Used",
                }
            )
            for col in [
                "Person-Level Wages",
                "Invoice Summary Top-Ups",
                "Monthly Sheet Rollup",
                "Total Wages Used",
            ]:
                show[col] = show[col].map(money)
            render_paginated_dataframe(
                show[
                    [
                        label_col_name,
                        "Person-Level Wages",
                        "Invoice Summary Top-Ups",
                        "Monthly Sheet Rollup",
                        "Total Wages Used",
                    ]
                ],
                key_prefix=f"reports_wages_{label_col_name.lower()}",
                page_size_default=15,
            )

        with wd_tab:
            _render_wages_table(wages_daily, "Day")
        with ww_tab:
            _render_wages_table(wages_weekly, "Week")
        with wm_tab:
            _render_wages_table(wages_monthly, "Month")
        with wy_tab:
            _render_wages_table(wages_yearly, "Year")

    st.markdown("**Wages by Person (Monthly)**")
    if wages_person_monthly.empty:
        st.caption("No monthly person-level wages rows yet.")
    else:
        wages_person = wages_person_monthly.copy()
        person_options = sorted(wages_person["person"].dropna().astype(str).unique().tolist())
        selected_people = st.multiselect(
            "Person Filter",
            options=person_options,
            default=person_options,
            key="reports_wages_person_filter",
        )
        if selected_people:
            wages_person = wages_person[wages_person["person"].isin(selected_people)]
        else:
            wages_person = wages_person.iloc[0:0]

        if wages_person.empty:
            st.caption("No rows for the selected person filter.")
        else:
            wages_person = wages_person.sort_values(["month", "amount"], ascending=[True, False])
            wages_person["amount"] = wages_person["amount"].map(money)
            render_paginated_dataframe(
                wages_person[
                    [
                        "month_label",
                        "person",
                        "amount",
                        "entry_count",
                    ]
                ],
                key_prefix="reports_wages_person_monthly",
                page_size_default=20,
            )

    st.markdown("**Finance Audit Trail**")
    audit_rows = cached_finance_activity(limit=500)
    if audit_rows.empty:
        st.caption("No finance audit entries yet.")
    else:
        audit_show = audit_rows.copy()
        audit_show["actor"] = audit_show["actor_name"].where(
            audit_show["actor_name"].fillna("").str.strip() != "",
            audit_show["device_name"],
        )
        render_paginated_dataframe(
            audit_show[
                [
                    "created_at",
                    "entity_type",
                    "action_type",
                    "entity_id",
                    "actor",
                    "notes",
                ]
            ],
            key_prefix="reports_finance_audit",
            page_size_default=20,
        )

    st.markdown("**One-Click Exports (Unified Filtered Finance Pack)**")
    st.caption(
        "Export by Daily/Weekly/Monthly/Yearly filter with income, expenses, supplier re-rental, and full expense category breakdown."
    )
    st.caption(
        f"Using current filters -> Years: {', '.join([str(y) for y in selected_years]) if selected_years else 'All'} | "
        f"Period: {report_period} | Scope: {report_scope}"
    )

    export_period = report_period
    export_scope = "Selected period row" if report_scope == "Selected Row" else "All rows in current filters"

    if export_period == "Daily":
        export_summary_base = daily.copy().sort_values("day")
        export_label_col = "day_label"
        export_key_col = "day"
    elif export_period == "Weekly":
        export_summary_base = weekly.copy().sort_values("week_start")
        export_label_col = "week_label"
        export_key_col = "week_start"
    elif export_period == "Monthly":
        export_summary_base = monthly.copy().sort_values("month")
        export_label_col = "month_label"
        export_key_col = "month"
    else:
        export_summary_base = yearly.copy().sort_values("year")
        export_label_col = "year"
        export_key_col = "year"

    if export_summary_base.empty:
        st.caption("No rows available for this export period.")
    else:
        selected_token = summary_selected_token
        selected_label = summary_selected_label

        if export_scope == "Selected period row" and selected_token is not None:
            export_summary_rows = export_summary_base[
                export_summary_base[export_key_col].astype(str) == str(selected_token)
            ].copy()
        else:
            export_summary_rows = export_summary_base.copy()

        export_expenses = expenses_all.copy()
        if export_scope == "Selected period row" and selected_token is not None and not export_expenses.empty:
            if export_period == "Daily":
                day_key = str(selected_token)
                export_expenses = export_expenses[
                    pd.to_datetime(export_expenses["finance_date"], errors="coerce").dt.strftime("%Y-%m-%d")
                    == day_key
                ]
            elif export_period == "Weekly":
                week_start_key = str(selected_token)
                expense_week_start = (
                    pd.to_datetime(export_expenses["finance_date"], errors="coerce")
                    - pd.to_timedelta(
                        pd.to_datetime(export_expenses["finance_date"], errors="coerce").dt.weekday,
                        unit="D",
                    )
                ).dt.strftime("%Y-%m-%d")
                export_expenses = export_expenses[expense_week_start == week_start_key]
            elif export_period == "Monthly":
                month_key = str(selected_token)
                export_expenses = export_expenses[export_expenses["month"].astype(str) == month_key]
            else:
                year_key = int(float(selected_token))
                export_expenses = export_expenses[pd.to_numeric(export_expenses["year"], errors="coerce") == year_key]

        if export_scope == "Selected period row" and selected_token is not None:
            export_invoices = invoices.copy()
            if export_period == "Daily":
                export_invoices = export_invoices[
                    pd.to_datetime(export_invoices["event_date"], errors="coerce").dt.strftime("%Y-%m-%d")
                    == str(selected_token)
                ]
            elif export_period == "Weekly":
                invoice_week_start = (
                    pd.to_datetime(export_invoices["event_date"], errors="coerce")
                    - pd.to_timedelta(
                        pd.to_datetime(export_invoices["event_date"], errors="coerce").dt.weekday,
                        unit="D",
                    )
                ).dt.strftime("%Y-%m-%d")
                export_invoices = export_invoices[invoice_week_start == str(selected_token)]
            elif export_period == "Monthly":
                export_invoices = export_invoices[export_invoices["month"].astype(str) == str(selected_token)]
            else:
                export_invoices = export_invoices[
                    pd.to_numeric(export_invoices["year"], errors="coerce")
                    == int(float(selected_token))
                ]
        else:
            export_invoices = invoices.copy()

        revenue_total = float(pd.to_numeric(export_summary_rows.get("revenue", 0.0), errors="coerce").fillna(0.0).sum())
        expenses_total = float(
            pd.to_numeric(export_summary_rows.get("total_expenses", 0.0), errors="coerce").fillna(0.0).sum()
        )
        cash_collected_total = float(
            pd.to_numeric(export_summary_rows.get("cash_collected", 0.0), errors="coerce").fillna(0.0).sum()
        )
        outstanding_total = float(
            pd.to_numeric(export_summary_rows.get("outstanding_receivables", 0.0), errors="coerce").fillna(0.0).sum()
        )
        if "net_profit_after_adjustments" in export_summary_rows.columns:
            net_total = float(
                pd.to_numeric(
                    export_summary_rows.get("net_profit_after_adjustments", 0.0),
                    errors="coerce",
                ).fillna(0.0).sum()
            )
        else:
            net_total = float(pd.to_numeric(export_summary_rows.get("net_profit", 0.0), errors="coerce").fillna(0.0).sum())

        expense_by_category = pd.DataFrame(columns=["category", "amount"])
        expense_by_supplier = pd.DataFrame(columns=["supplier", "amount"])
        if not export_expenses.empty:
            cleaned = export_expenses.copy()
            cleaned["category"] = cleaned["category"].fillna("").astype(str).str.strip()
            cleaned["vendor"] = cleaned["vendor"].fillna("").astype(str).str.strip()
            cleaned["amount"] = pd.to_numeric(cleaned["amount"], errors="coerce").fillna(0.0)
            cleaned = cleaned[
                ~cleaned["expense_kind"].fillna("").astype(str).str.lower().isin(["summary_rollup", "recurring_draft"])
            ].copy()

            expense_by_category = (
                cleaned.groupby("category", as_index=False)["amount"]
                .sum()
                .sort_values("amount", ascending=False)
            )
            expense_by_category = expense_by_category[expense_by_category["category"] != ""]

            supplier_scope = cleaned[
                (cleaned["category"].str.lower() == "re-rental")
                & (cleaned["vendor"] != "")
            ].copy()
            if not supplier_scope.empty:
                expense_by_supplier = (
                    supplier_scope.groupby("vendor", as_index=False)["amount"]
                    .sum()
                    .rename(columns={"vendor": "supplier"})
                    .sort_values("amount", ascending=False)
                )

        pdf_table_parts: list[pd.DataFrame] = []
        if not expense_by_category.empty:
            cat_pdf = expense_by_category.head(10).copy()
            cat_pdf["section"] = "Expense Category"
            cat_pdf = cat_pdf.rename(columns={"category": "name"})
            cat_pdf["amount"] = cat_pdf["amount"].map(money)
            pdf_table_parts.append(cat_pdf[["section", "name", "amount"]])
        if not expense_by_supplier.empty:
            sup_pdf = expense_by_supplier.head(10).copy()
            sup_pdf["section"] = "Supplier Re-Rental"
            sup_pdf = sup_pdf.rename(columns={"supplier": "name"})
            sup_pdf["amount"] = sup_pdf["amount"].map(money)
            pdf_table_parts.append(sup_pdf[["section", "name", "amount"]])
        if pdf_table_parts:
            export_pdf_table = pd.concat(pdf_table_parts, ignore_index=True)
        else:
            export_pdf_table = pd.DataFrame(columns=["section", "name", "amount"])

        export_title = "Headline Rentals - Full Finance Summary"
        export_subtitle = f"{export_period} | {selected_label}"
        export_kpis = [
            ("Income", money(revenue_total)),
            ("Expenses", money(expenses_total)),
            ("Net", money(net_total)),
            ("Cash Collected", money(cash_collected_total)),
            ("Outstanding", money(outstanding_total)),
        ]
        export_pdf = build_finance_summary_pdf(
            title=export_title,
            subtitle=export_subtitle,
            kpis=export_kpis,
            table_title="Top Expense Categories and Supplier Re-Rental",
            table_df=export_pdf_table,
            max_rows=20,
        )

        summary_export = export_summary_rows.copy()
        expense_rows_export = export_expenses.copy()
        if not expense_rows_export.empty:
            expense_rows_export["finance_date"] = pd.to_datetime(
                expense_rows_export["finance_date"], errors="coerce"
            ).dt.date.astype("string")
            expense_rows_export["expense_date"] = pd.to_datetime(
                expense_rows_export["expense_date"], errors="coerce"
            ).dt.date.astype("string")
        invoice_rows_export = export_invoices.copy()
        if not invoice_rows_export.empty:
            invoice_rows_export["event_date"] = pd.to_datetime(
                invoice_rows_export["event_date"], errors="coerce"
            ).dt.date.astype("string")

        metrics_export = pd.DataFrame(
            [
                {"metric": "period", "value": export_period},
                {"metric": "scope", "value": selected_label},
                {"metric": "income", "value": revenue_total},
                {"metric": "expenses", "value": expenses_total},
                {"metric": "net", "value": net_total},
                {"metric": "cash_collected", "value": cash_collected_total},
                {"metric": "outstanding", "value": outstanding_total},
            ]
        )

        safe_period = export_period.lower()
        safe_token = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(selected_label).strip().lower())[:40] or "all"

        csv_zip = io.BytesIO()
        with zipfile.ZipFile(csv_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f"finance_metrics_{safe_period}_{safe_token}.csv",
                metrics_export.to_csv(index=False),
            )
            zf.writestr(
                f"finance_summary_{safe_period}_{safe_token}.csv",
                summary_export.to_csv(index=False),
            )
            zf.writestr(
                f"expense_breakdown_category_{safe_period}_{safe_token}.csv",
                expense_by_category.to_csv(index=False),
            )
            zf.writestr(
                f"expense_breakdown_supplier_rerental_{safe_period}_{safe_token}.csv",
                expense_by_supplier.to_csv(index=False),
            )
            zf.writestr(
                f"expense_transactions_{safe_period}_{safe_token}.csv",
                expense_rows_export.to_csv(index=False),
            )
            zf.writestr(
                f"invoices_{safe_period}_{safe_token}.csv",
                invoice_rows_export.to_csv(index=False),
            )
            zf.writestr(
                f"finance_summary_{safe_period}_{safe_token}.pdf",
                export_pdf,
            )
        csv_zip.seek(0)
        st.download_button(
            "Download Finance Pack",
            data=csv_zip.getvalue(),
            file_name=f"headline_finance_pack_{safe_period}_{safe_token}.zip",
            mime="application/zip",
            key="reports_download_unified_csv_zip_btn",
        )
        with st.expander("More exports", expanded=False):
            st.download_button(
                "Download Finance Summary PDF",
                data=export_pdf,
                file_name=f"headline_finance_summary_{safe_period}_{safe_token}.pdf",
                mime="application/pdf",
                key="reports_download_unified_pdf_btn",
            )

    with st.expander("Tax Pack (GCT-Ready)", expanded=not tax_monthly.empty):
        st.caption(
            "Generate tax-ready monthly files from confirmed orders, including subtotal before GCT and GCT collected."
        )
        if tax_monthly.empty:
            st.caption("No tax-pack rows yet.")
        else:
            tax_show = tax_monthly.copy().sort_values("month")
            tax_show["subtotal_before_gct"] = tax_show["subtotal_before_gct"].map(money)
            tax_show["gct_collected"] = tax_show["gct_collected"].map(money)
            tax_show["gross_total"] = tax_show["gross_total"].map(money)
            tax_show["effective_gct_pct"] = tax_show["effective_gct_pct"].map(lambda x: f"{float(x):,.2f}%")
            render_paginated_dataframe(
                tax_show[
                    [
                        "month_label",
                        "invoice_count",
                        "gct_enabled_invoices",
                        "subtotal_before_gct",
                        "gct_collected",
                        "gross_total",
                        "effective_gct_pct",
                    ]
                ],
                key_prefix="reports_tax_pack_monthly",
                page_size_default=12,
            )

            tax_month_choices = sorted(tax_monthly["month"].dropna().astype(str).unique().tolist(), reverse=True)
            if tax_month_choices:
                tx1, tx2 = st.columns([1.5, 1.2])
                selected_tax_month = tx1.selectbox(
                    "Tax Pack Month",
                    options=tax_month_choices,
                    key="reports_tax_pack_month_selector",
                )
                tax_month_row = tax_monthly[tax_monthly["month"] == selected_tax_month].iloc[-1]
                month_tax_detail = tax_detail[tax_detail["month"] == selected_tax_month].copy()
                if not month_tax_detail.empty:
                    month_tax_detail["event_date"] = pd.to_datetime(
                        month_tax_detail["event_date"], errors="coerce"
                    ).dt.date.astype("string")

                tax_kpis = [
                    ("Orders", f"{int(tax_month_row.get('invoice_count', 0) or 0)}"),
                    ("Subtotal Before GCT", money(float(tax_month_row.get("subtotal_before_gct", 0.0) or 0.0))),
                    ("GCT Collected", money(float(tax_month_row.get("gct_collected", 0.0) or 0.0))),
                    ("Gross Total", money(float(tax_month_row.get("gross_total", 0.0) or 0.0))),
                ]
                tax_table = month_tax_detail[
                    [
                        "invoice_number",
                        "event_date",
                        "customer_name",
                        "subtotal_before_gct",
                        "gct_collected",
                        "gross_total",
                    ]
                ].copy() if not month_tax_detail.empty else pd.DataFrame(
                    columns=[
                        "invoice_number",
                        "event_date",
                        "customer_name",
                        "subtotal_before_gct",
                        "gct_collected",
                        "gross_total",
                    ]
                )
                for money_col in ["subtotal_before_gct", "gct_collected", "gross_total"]:
                    if money_col in tax_table.columns:
                        tax_table[money_col] = pd.to_numeric(
                            tax_table[money_col], errors="coerce"
                        ).fillna(0.0).map(money)

                tax_pdf = build_finance_summary_pdf(
                    title="Headline Rentals - Tax Pack",
                    subtitle=f"GCT Summary {selected_tax_month}",
                    kpis=tax_kpis,
                    table_title="Confirmed Orders (Tax Fields)",
                    table_df=tax_table,
                )
                tax_zip = io.BytesIO()
                with zipfile.ZipFile(tax_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(
                        f"tax_pack_monthly_{selected_tax_month}.csv",
                        tax_monthly[tax_monthly["month"] == selected_tax_month].to_csv(index=False),
                    )
                    zf.writestr(
                        f"tax_pack_invoices_{selected_tax_month}.csv",
                        month_tax_detail.to_csv(index=False),
                    )
                    zf.writestr(f"tax_pack_{selected_tax_month}.pdf", tax_pdf)
                tax_zip.seek(0)
                tx2.download_button(
                    "Download Tax Pack ZIP",
                    data=tax_zip.getvalue(),
                    file_name=f"headline_tax_pack_{selected_tax_month}.zip",
                    mime="application/zip",
                    key="reports_download_tax_pack_btn",
                )

    recon_tx_rows = st.session_state.get("reports_bank_recon_tx", pd.DataFrame())
    recon_gap_rows = st.session_state.get("reports_bank_recon_gaps", pd.DataFrame())
    bank_has_rows = (
        (isinstance(recon_tx_rows, pd.DataFrame) and not recon_tx_rows.empty)
        or (isinstance(recon_gap_rows, pd.DataFrame) and not recon_gap_rows.empty)
    )
    with st.expander("Bank Reconciliation Import", expanded=bank_has_rows):
        st.caption(
            "Upload your bank CSV and auto-match deposit transactions against invoice records."
        )
        bank_file = st.file_uploader(
            "Upload Bank Statement CSV",
            type=["csv"],
            key="reports_bank_recon_upload",
        )
        if bank_file is not None:
            try:
                bank_raw = pd.read_csv(bank_file)
            except Exception as exc:
                st.error(f"Could not read CSV: {exc}")
                bank_raw = pd.DataFrame()

            if bank_raw.empty:
                st.warning("No rows found in this CSV file.")
            else:
                bank_columns = [str(col) for col in bank_raw.columns]
                guess_date = _guess_column_name(
                    bank_columns,
                    [("transaction", "date"), ("posted", "date"), ("date",)],
                )
                guess_amount = _guess_column_name(
                    bank_columns,
                    [("credit",), ("amount",), ("deposit",), ("value",)],
                )
                guess_desc = _guess_column_name(
                    bank_columns,
                    [("description",), ("memo",), ("details",), ("reference",), ("narration",)],
                )

                rb1, rb2, rb3 = st.columns(3)
                date_col = rb1.selectbox(
                    "Date Column",
                    options=bank_columns,
                    index=bank_columns.index(guess_date) if guess_date in bank_columns else 0,
                    key="reports_bank_recon_date_col",
                )
                amount_col = rb2.selectbox(
                    "Amount Column",
                    options=bank_columns,
                    index=bank_columns.index(guess_amount) if guess_amount in bank_columns else 0,
                    key="reports_bank_recon_amount_col",
                )
                description_col = rb3.selectbox(
                    "Description Column",
                    options=bank_columns,
                    index=bank_columns.index(guess_desc) if guess_desc in bank_columns else 0,
                    key="reports_bank_recon_desc_col",
                )
                deposits_positive = st.checkbox(
                    "Deposits are positive amounts",
                    value=True,
                    key="reports_bank_recon_positive_deposit",
                )

                file_tag = f"{bank_file.name}:{bank_file.size}"
                run_recon = st.button(
                    "Run Reconciliation",
                    key="reports_bank_recon_run_btn",
                    type="primary",
                )
                if run_recon:
                    recon_result = run_bank_reconciliation(
                        bank_rows=bank_raw,
                        invoices=invoices,
                        date_col=date_col,
                        amount_col=amount_col,
                        description_col=description_col,
                        deposits_positive=deposits_positive,
                    )
                    st.session_state["reports_bank_recon_tag"] = file_tag
                    st.session_state["reports_bank_recon_tx"] = recon_result["transactions"]
                    st.session_state["reports_bank_recon_gaps"] = recon_result["invoice_gaps"]

                if st.session_state.get("reports_bank_recon_tag") == file_tag:
                    tx_rows = st.session_state.get("reports_bank_recon_tx", pd.DataFrame())
                    gap_rows = st.session_state.get("reports_bank_recon_gaps", pd.DataFrame())
                    if isinstance(tx_rows, pd.DataFrame) and not tx_rows.empty:
                        matched_mask = tx_rows["matched"] == "Matched"
                        matched_count = int(matched_mask.sum())
                        unmatched_count = int((~matched_mask).sum())
                        matched_amount = float(
                            pd.to_numeric(tx_rows.loc[matched_mask, "amount"], errors="coerce").fillna(0.0).sum()
                        )
                        unmatched_amount = float(
                            pd.to_numeric(tx_rows.loc[~matched_mask, "amount"], errors="coerce").fillna(0.0).sum()
                        )
                        r1, r2, r3, r4 = st.columns(4)
                        r1.metric("Imported Deposits", f"{len(tx_rows)}")
                        r2.metric("Matched", f"{matched_count}")
                        r3.metric("Unmatched", f"{unmatched_count}")
                        r4.metric("Matched Amount", money(matched_amount))
                        if unmatched_count > 0:
                            st.warning(f"Unmatched amount: {money(unmatched_amount)}")

                        tx_show = tx_rows.copy()
                        tx_show["amount"] = pd.to_numeric(tx_show["amount"], errors="coerce").fillna(0.0).map(money)
                        tx_show["expected_paid_amount"] = pd.to_numeric(
                            tx_show["expected_paid_amount"], errors="coerce"
                        ).fillna(0.0).map(money)
                        tx_show["invoice_revenue"] = pd.to_numeric(
                            tx_show["invoice_revenue"], errors="coerce"
                        ).fillna(0.0).map(money)
                        render_paginated_dataframe(
                            tx_show[
                                [
                                    "txn_date",
                                    "description",
                                    "amount",
                                    "matched",
                                    "confidence",
                                    "match_score",
                                    "invoice_number",
                                    "customer_name",
                                    "match_reason",
                                ]
                            ],
                            key_prefix="reports_bank_recon_tx",
                            page_size_default=15,
                        )

                        if isinstance(gap_rows, pd.DataFrame) and not gap_rows.empty:
                            gap_show = gap_rows.copy()
                            gap_show = gap_show[
                                pd.to_numeric(gap_show["expected_paid_amount"], errors="coerce").fillna(0.0) > 0.0
                            ].copy()
                            gap_show["event_date"] = pd.to_datetime(
                                gap_show["event_date"], errors="coerce"
                            ).dt.date.astype("string")
                            for amount_col_name in ["expected_paid_amount", "matched_amount", "unreconciled_gap"]:
                                gap_show[amount_col_name] = pd.to_numeric(
                                    gap_show[amount_col_name], errors="coerce"
                                ).fillna(0.0)
                            unresolved = gap_show[gap_show["unreconciled_gap"].abs() > 0.01].copy()
                            if not unresolved.empty:
                                unresolved["expected_paid_amount"] = unresolved["expected_paid_amount"].map(money)
                                unresolved["matched_amount"] = unresolved["matched_amount"].map(money)
                                unresolved["unreconciled_gap"] = unresolved["unreconciled_gap"].map(money)
                                st.markdown("**Invoices With Unreconciled Paid Amounts**")
                                render_paginated_dataframe(
                                    unresolved[
                                        [
                                            "invoice_number",
                                            "customer_name",
                                            "event_date",
                                            "expected_paid_amount",
                                            "matched_amount",
                                            "unreconciled_gap",
                                        ]
                                    ],
                                    key_prefix="reports_bank_recon_gaps",
                                    page_size_default=10,
                                )

                        dl1, dl2 = st.columns(2)
                        dl1.download_button(
                            "Download Reconciliation CSV",
                            data=tx_rows.to_csv(index=False).encode("utf-8"),
                            file_name="bank_reconciliation_results.csv",
                            mime="text/csv",
                            key="reports_bank_recon_download_tx_csv",
                        )
                        if isinstance(gap_rows, pd.DataFrame) and not gap_rows.empty:
                            dl2.download_button(
                                "Download Invoice Gap CSV",
                                data=gap_rows.to_csv(index=False).encode("utf-8"),
                                file_name="bank_reconciliation_invoice_gaps.csv",
                                mime="text/csv",
                                key="reports_bank_recon_download_gap_csv",
                            )
                    else:
                        st.info("Run reconciliation after mapping your CSV columns.")


def render_calendar_and_followups() -> None:
    st.subheader("Event Calendar")
    st.caption(
        "View upcoming/past events, rental windows, locations, equipment, and follow-up reminders."
    )

    review_link = get_profile_setting("google_review_link", "")
    allow_finance = can_view_finance_data()
    events = build_event_schedule(load_event_calendar())
    if events.empty:
        st.info("No event calendar entries yet. Save invoices with event date/time first.")
        return

    tz_jm = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    events = events.copy()
    events["plot_start"] = events["event_start"].apply(lambda dt: dt.astimezone(tz_jm).replace(tzinfo=None))
    events["plot_end"] = events["event_end"].apply(lambda dt: dt.astimezone(tz_jm).replace(tzinfo=None))
    events["location"] = events["event_location"].fillna("").astype(str)
    events["event_window"] = events.apply(
        lambda row: (
            f"{row['event_start'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')} - "
            f"{row['event_end'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')}"
        ),
        axis=1,
    )
    events["map_link"] = events["location"].map(maps_search_link)
    events["calendar_link"] = events.apply(
        lambda row: google_calendar_link(
            title=f"Headline Rentals - {row['invoice_number']}",
            start=row["event_start"],
            end=row["event_end"],
            location=row["location"],
                details=(
                    f"Customer: {row.get('customer_name', '')}\n"
                    f"Equipment: {row.get('equipment_summary', '')}\n"
                    f"Contact: {row.get('customer_phone', '') or row.get('customer_email', '')}"
                ),
            tz_name=str(row.get("event_timezone", DEFAULT_EVENT_TIMEZONE) or DEFAULT_EVENT_TIMEZONE),
        ),
        axis=1,
    )

    timeline = px.timeline(
        events,
        x_start="plot_start",
        x_end="plot_end",
        y="invoice_number",
        color="status",
        color_discrete_map={
            "Upcoming": PRIMARY_COLOR,
            "Ongoing": "#2EAF7D",
            "Past": "#7B8191",
        },
        title="Event Timeline (Jamaica Time)",
        hover_data={
            "customer_name": True,
            "location": True,
            "event_window": True,
            "equipment_summary": True,
            "plot_start": False,
            "plot_end": False,
        },
    )
    timeline.update_layout(
        xaxis_title="Date/Time (America/Jamaica)",
        yaxis_title="Invoice",
        legend_title_text="Status",
    )
    st.plotly_chart(timeline, use_container_width=True)

    now_jm = jamaica_now()
    ongoing = events[
        events.apply(
            lambda row: row["event_start"].astimezone(tz_jm) <= now_jm < row["event_end"].astimezone(tz_jm),
            axis=1,
        )
    ]
    upcoming = events[events["event_start"].apply(lambda dt: dt.astimezone(tz_jm) >= now_jm)]
    past = events[events["event_end"].apply(lambda dt: dt.astimezone(tz_jm) < now_jm)]

    st.markdown("**Ongoing Events**")
    if ongoing.empty:
        st.caption("No ongoing events right now.")
    else:
        on = ongoing.copy().sort_values("event_start")
        if allow_finance:
            on["revenue"] = on["revenue"].map(money)
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "location",
                "customer_name",
                "customer_phone",
                "customer_email",
                "equipment_summary",
                "revenue",
            ]
        else:
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "location",
                "customer_name",
                "customer_phone",
                "customer_email",
                "equipment_summary",
            ]
        st.dataframe(on[cols], hide_index=True, use_container_width=True)

    st.markdown("**Upcoming Events**")
    if upcoming.empty:
        st.caption("No upcoming events.")
    else:
        up = upcoming.copy().sort_values("event_start")
        if allow_finance:
            up["revenue"] = up["revenue"].map(money)
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "rental_hours",
                "location",
                "customer_name",
                "customer_phone",
                "customer_email",
                "equipment_summary",
                "revenue",
            ]
        else:
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "rental_hours",
                "location",
                "customer_name",
                "customer_phone",
                "customer_email",
                "equipment_summary",
            ]
        st.dataframe(up[cols], hide_index=True, use_container_width=True)

        with st.expander("Upcoming Calendar/Map Links", expanded=False):
            for _, row in up.iterrows():
                map_part = (
                    f"[Open Map]({row['map_link']})"
                    if str(row.get("map_link", "")).strip()
                    else "Map not set"
                )
                st.markdown(
                    f"- `{row['invoice_number']}` | "
                    f"{map_part} | "
                    f"[Add/Open in Google Calendar]({row['calendar_link']})"
                )

    st.markdown("**Past Events**")
    if past.empty:
        st.caption("No past events.")
    else:
        p = past.copy().sort_values("event_start", ascending=False)
        if allow_finance:
            p["revenue"] = p["revenue"].map(money)
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "location",
                "customer_name",
                "equipment_summary",
                "revenue",
            ]
        else:
            cols = [
                "invoice_number",
                "event_date_display",
                "event_time_display",
                "location",
                "customer_name",
                "equipment_summary",
            ]
        st.dataframe(p[cols], hide_index=True, use_container_width=True)

    st.markdown("**Post-Event Thank You / Review Reminders**")
    sent = load_notification_log()
    sent_pairs = set()
    if not sent.empty:
        sent_pairs = {
            (int(row["invoice_id"]), str(row["notification_type"]).strip().lower())
            for _, row in sent.iterrows()
        }

    followups = events[
        events["event_end"].apply(lambda dt: now_jm >= dt.astimezone(tz_jm) + timedelta(hours=1))
    ].copy()
    if followups.empty:
        st.caption("No post-event reminders due yet.")
    else:
        pending = followups[
            ~followups.apply(
                lambda row: (int(row["invoice_id"]), "post_event_followup") in sent_pairs,
                axis=1,
            )
        ].copy()
        if pending.empty:
            st.caption("All due follow-ups already marked as sent.")
        else:
            for _, row in pending.sort_values("event_end").iterrows():
                contact_target = (
                    str(row.get("customer_phone", "")).strip()
                    or str(row.get("customer_email", "")).strip()
                    or "contact not set"
                )
                review_line = (
                    f" Please leave us a review: {review_link.strip()}"
                    if review_link.strip()
                    else ""
                )
                message = (
                    f"Hi {row.get('customer_name', '').strip() or 'there'}, thank you for choosing "
                    "Headline Rentals for your event." + review_line
                )
                st.info(
                    f"{row['invoice_number']} | Contact: {contact_target} | Event ended: "
                    f"{row['event_end'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')}"
                )
                st.code(message, language="text")
                if st.button(
                    f"Mark Follow-up Sent ({row['invoice_number']})",
                    key=f"mark_followup_{int(row['invoice_id'])}",
                ):
                    mark_notification_sent(int(row["invoice_id"]), "post_event_followup")
                    st.success(f"Follow-up marked as sent for {row['invoice_number']}.")
                    st.rerun()


def build_client_retention_message(
    customer_name: str,
    review_link: str,
) -> str:
    customer = (customer_name or "").strip() or "there"
    link = (review_link or "").strip() or CLIENT_REVIEW_LINK_DEFAULT
    lines = [
        f"Hi {customer}",
        "Thank you for choosing Headline Event Rentals",
        "",
        "If you enjoyed our service, we’d really appreciate one minute of your time to leave a quick review. "
        "Your feedback would mean the world to us as it helps us continue to grow and lets others choose us confidently.",
        "",
        "👉 Leave a review here:",
        link,
    ]
    return "\n".join(lines)


def render_client_retention_automation() -> None:
    render_section_shell(
        "Client Retention Automation",
        "Queue post-event thank-you and review messages with one-click send links.",
    )
    st.caption(
        "Queue every confirmed invoice immediately. 'Due Now' turns on automatically when event date/time is reached."
    )

    default_review = get_profile_setting("google_review_link", CLIENT_REVIEW_LINK_DEFAULT)

    with st.expander("Retention Settings", expanded=False):
        review_link = st.text_input(
            "Google Review Link",
            value=default_review,
            key="retention_review_link_input",
        )
        if st.button("Save Retention Settings", key="save_retention_settings_btn"):
            set_profile_setting("google_review_link", review_link.strip() or CLIENT_REVIEW_LINK_DEFAULT)
            st.success("Retention settings saved.")

    review_link = get_profile_setting("google_review_link", CLIENT_REVIEW_LINK_DEFAULT)

    events = build_event_schedule(load_event_calendar())
    if events.empty:
        st.info("No confirmed invoices available yet.")
        return

    sent_log = load_notification_log()
    sent_pairs = set()
    if not sent_log.empty:
        sent_pairs = {
            (int(row["invoice_id"]), str(row["notification_type"]).strip().lower())
            for _, row in sent_log.iterrows()
        }

    tz_jm = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    now_jm = jamaica_now()
    events = events.copy()
    events["followup_due_at"] = events["event_start"].apply(
        lambda dt: dt.astimezone(tz_jm)
    )
    events["is_sent"] = events["invoice_id"].apply(
        lambda invoice_id: (int(invoice_id), "post_event_followup") in sent_pairs
    )
    events["is_due"] = events["followup_due_at"].apply(lambda dt: now_jm >= dt)
    events["queue_status"] = events.apply(
        lambda row: "Sent" if bool(row["is_sent"]) else ("Due Now" if bool(row["is_due"]) else "Queued"),
        axis=1,
    )
    events["contact_target"] = events.apply(
        lambda row: (
            resolve_contact_channels(
                customer_phone=str(row.get("customer_phone", "")).strip(),
                customer_email=str(row.get("customer_email", "")).strip(),
                contact_detail=str(row.get("contact_detail", "")).strip(),
            ).get("contact_target", "No contact")
        ),
        axis=1,
    )

    k1, k2, k3 = st.columns(3)
    k1.metric("Due Now", int((events["queue_status"] == "Due Now").sum()))
    k2.metric("Queued", int((events["queue_status"] == "Queued").sum()))
    k3.metric("Sent", int((events["queue_status"] == "Sent").sum()))

    st.success(
        "All confirmed invoices appear in queue immediately. Due Now alerts are based on event date/time."
    )

    st.markdown("**Payment Receipt Sender**")
    paid_receipts = load_invoice_level()
    if paid_receipts.empty:
        st.caption("No paid invoices available for receipt sending yet.")
    else:
        paid_receipts = paid_receipts[paid_receipts["amount_outstanding"] <= 0.01].copy()
        if paid_receipts.empty:
            st.caption("No fully paid invoices yet.")
        else:
            paid_receipts["event_date"] = pd.to_datetime(
                paid_receipts["event_date"],
                errors="coerce",
            )
            paid_receipts = paid_receipts.sort_values(
                ["event_date", "id"],
                ascending=[False, False],
            )
            receipt_label_map = {
                (
                    f"#{str(row.get('invoice_number', '') or '').strip() or row.get('id')} | "
                    f"{str(row.get('customer_name', '') or '').strip() or 'No Customer'} | "
                    f"Event {row['event_date'].date().isoformat() if pd.notna(row['event_date']) else 'No Date'}"
                ): int(row["id"])
                for _, row in paid_receipts.iterrows()
            }
            selected_receipt_label = st.selectbox(
                "Select Paid Invoice Receipt",
                options=list(receipt_label_map.keys()),
                key="retention_paid_receipt_selector",
            )
            selected_receipt_id = int(receipt_label_map[selected_receipt_label])
            try:
                receipt_assets = build_payment_receipt_assets(selected_receipt_id)
                p1, p2, p3 = st.columns([1, 1, 1.8])
                p1.download_button(
                    "Download Receipt PDF",
                    data=receipt_assets["pdf_bytes"],
                    file_name=f"{receipt_assets['file_stub']}.pdf",
                    mime="application/pdf",
                    key=f"retention_paid_receipt_pdf_{selected_receipt_id}",
                )
                p2.download_button(
                    "Download Receipt PNG",
                    data=receipt_assets["png_bytes"],
                    file_name=f"{receipt_assets['file_stub']}.png",
                    mime="image/png",
                    key=f"retention_paid_receipt_png_{selected_receipt_id}",
                )
                p3.markdown(
                    f"**Total Cost:** {money(float(receipt_assets.get('invoice_total', 0.0)))}  \n"
                    f"**Total Paid:** {money(float(receipt_assets.get('amount_paid', 0.0)))}  \n"
                    f"**Balance Due:** {money(float(receipt_assets.get('balance_due', 0.0)))}"
                )

                default_country_code = (
                    get_delivery_setting("default_country_code", "1").strip() or "1"
                )
                phone_key = f"retention_receipt_phone_{selected_receipt_id}"
                email_key = f"retention_receipt_email_{selected_receipt_id}"
                msg_key = f"retention_receipt_msg_{selected_receipt_id}"
                if phone_key not in st.session_state:
                    st.session_state[phone_key] = str(receipt_assets.get("phone", "") or "")
                if email_key not in st.session_state:
                    st.session_state[email_key] = str(receipt_assets.get("email", "") or "")
                if msg_key not in st.session_state:
                    st.session_state[msg_key] = str(receipt_assets.get("message", "") or "")

                c1, c2 = st.columns(2)
                receipt_phone = c1.text_input(
                    "Receipt Phone",
                    value=str(st.session_state.get(phone_key, "") or ""),
                    key=f"{phone_key}_input",
                )
                receipt_email = c2.text_input(
                    "Receipt Email",
                    value=str(st.session_state.get(email_key, "") or ""),
                    key=f"{email_key}_input",
                )
                receipt_message = st.text_area(
                    "Receipt Message",
                    value=str(st.session_state.get(msg_key, "") or ""),
                    height=90,
                    key=f"{msg_key}_input",
                )
                st.session_state[phone_key] = receipt_phone
                st.session_state[email_key] = receipt_email
                st.session_state[msg_key] = receipt_message

                wa_digits = normalize_whatsapp_to(
                    receipt_phone,
                    default_country_code=default_country_code,
                )
                wa_url = whatsapp_link(receipt_phone, receipt_message) if wa_digits else ""
                gmail_url = (
                    gmail_compose_link(
                        receipt_email,
                        str(receipt_assets.get("subject", "Headline Rentals Payment Receipt")),
                        receipt_message,
                    )
                    if receipt_email.strip()
                    else ""
                )
                s1, s2 = st.columns(2)
                if wa_url:
                    s1.link_button("Open WhatsApp Receipt Send", wa_url, use_container_width=True)
                else:
                    s1.info("Add phone")
                if gmail_url:
                    s2.link_button("Open Gmail Receipt Send", gmail_url, use_container_width=True)
                else:
                    s2.info("Add email")
                st.caption("Download the receipt first, then attach it in WhatsApp/Gmail before sending.")
            except Exception as exc:
                st.caption(f"Could not prepare receipt download/send tools: {exc}")
    st.markdown("---")

    status_filter = st.selectbox(
        "Filter Queue",
        options=["All", "Due Now", "Queued", "Sent"],
        index=0,
        key="retention_queue_filter",
    )

    if status_filter == "All":
        queue = events.copy()
    else:
        queue = events[events["queue_status"] == status_filter].copy()

    if queue.empty:
        st.caption("No queue items for this filter.")
        return

    status_rank = {"Due Now": 0, "Queued": 1, "Sent": 2}
    queue["status_rank"] = queue["queue_status"].map(status_rank).fillna(9)
    queue = queue.sort_values(["status_rank", "followup_due_at", "event_end"], ascending=[True, True, False])

    if st.button("Mark All Due Now As Sent", key="retention_mark_all_due_btn"):
        due_rows = queue[(queue["queue_status"] == "Due Now") & (~queue["is_sent"])].copy()
        if due_rows.empty:
            st.info("No due follow-ups to mark.")
        else:
            for _, row in due_rows.iterrows():
                mark_notification_sent(int(row["invoice_id"]), "post_event_followup")
            st.success(f"Marked {len(due_rows)} follow-up(s) as sent.")
            st.rerun()

    st.markdown("**Follow-Up Queue**")
    page_col1, page_col2 = st.columns([1, 1.4])
    page_size = int(
        page_col1.selectbox(
            "Rows Per Page",
            options=[20, 50, 100, 200],
            index=1,
            key="retention_queue_page_size",
        )
    )
    page_count = max(1, int(math.ceil(len(queue) / float(page_size))))
    default_page = int(st.session_state.get("retention_queue_page_number", 1) or 1)
    default_page = min(max(default_page, 1), page_count)
    page_number = int(
        page_col2.number_input(
            "Page",
            min_value=1,
            max_value=page_count,
            value=default_page,
            step=1,
            key="retention_queue_page_number",
        )
    )
    start_idx = (page_number - 1) * page_size
    end_idx = min(start_idx + page_size, len(queue))
    st.caption(f"Showing {start_idx + 1}-{end_idx} of {len(queue)} follow-up records.")
    queue_view = queue.iloc[start_idx:end_idx].copy()

    for _, row in queue_view.iterrows():
        customer_name = str(row.get("customer_name", "") or "").strip()
        invoice_number = str(row.get("invoice_number", "") or "").strip()
        due_label = row["followup_due_at"].astimezone(tz_jm).strftime("%Y-%m-%d %I:%M %p")
        queue_status = str(row.get("queue_status", "Queued"))
        header = (
            f"[{queue_status}] {invoice_number} | "
            f"{customer_name or 'Customer'} | Due: {due_label}"
        )
        with st.expander(header, expanded=(queue_status == "Due Now")):
            resolved_contact = resolve_contact_channels(
                customer_phone=str(row.get("customer_phone", "")).strip(),
                customer_email=str(row.get("customer_email", "")).strip(),
                contact_detail=str(row.get("contact_detail", "")).strip(),
            )
            active_review_link = (review_link or "").strip() or CLIENT_REVIEW_LINK_DEFAULT
            message = build_client_retention_message(
                customer_name=customer_name,
                review_link=active_review_link,
            )
            message = ensure_link_in_message(message, active_review_link)
            target_phone = str(resolved_contact.get("phone", "")).strip()
            target_email = str(resolved_contact.get("email", "")).strip()
            message_subject = f"Thank you from Headline Event Rentals - {invoice_number}"

            st.caption(
                f"Event date/time: {row['event_start'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')} | "
                f"Contact: {row['contact_target']}"
            )
            st.code(message, language="text")
            st.markdown(f"[Open review link]({active_review_link})")

            c1, c2, c3 = st.columns(3)
            wa_digits = normalize_whatsapp_to(target_phone, default_country_code="1").strip()
            if wa_digits:
                c1.link_button(
                    "Open WhatsApp",
                    whatsapp_link(target_phone, message),
                    use_container_width=True,
                )
            else:
                c1.info("No phone")
            if target_email:
                c2.link_button(
                    "Open Gmail",
                    gmail_compose_link(target_email, message_subject, message),
                    use_container_width=True,
                )
            else:
                c2.info("No email")

            if bool(row["is_sent"]):
                c3.success("Already marked sent")
            else:
                if c3.button(
                    "Mark Sent",
                    key=f"retention_mark_sent_{int(row['invoice_id'])}",
                    use_container_width=True,
                ):
                    mark_notification_sent(int(row["invoice_id"]), "post_event_followup")
                    st.success(f"Follow-up marked sent for {invoice_number}.")
                    st.rerun()


def render_deposit_due_tracker(report_start_month: str) -> None:
    render_section_shell(
        "Deposit Due Tracker",
        "Track unpaid balances, due windows, and overdue follow-ups.",
    )
    st.caption(
        "Track all outstanding confirmed-order balances (deposit and COD) with due windows."
    )
    st.caption("This tracker shows all outstanding balances (not filtered by report start month).")

    default_due_days = int(float(get_setting("finance.deposit_due_days_before_event", "3") or 3))
    default_due_soon_days = int(float(get_setting("finance.deposit_due_soon_days", "2") or 2))
    s1, s2 = st.columns(2)
    due_days_before_event = int(
        s1.number_input(
            "Balance Due Rule (days before event)",
            min_value=0,
            max_value=60,
            value=max(0, default_due_days),
            step=1,
            key="deposit_due_rule_days_input",
        )
    )
    due_soon_window = int(
        s2.number_input(
            "Due Soon Window (days)",
            min_value=0,
            max_value=30,
            value=max(0, default_due_soon_days),
            step=1,
            key="deposit_due_soon_window_input",
        )
    )
    if st.button("Save Deposit Tracker Rules", key="save_deposit_tracker_rules_btn"):
        set_setting("finance.deposit_due_days_before_event", str(due_days_before_event))
        set_setting("finance.deposit_due_soon_days", str(due_soon_window))
        st.success("Deposit tracker rules saved.")

    invoice_level = load_invoice_level()
    if invoice_level.empty:
        st.info("No confirmed invoice payment records yet.")
        return

    recent_paid_invoice_id = int(
        pd.to_numeric(
            st.session_state.get("deposit_tracker_recent_paid_invoice_id", 0),
            errors="coerce",
        )
        or 0
    )
    if recent_paid_invoice_id > 0:
        st.markdown("**Paid Receipt Ready (Latest Cleared Balance)**")
        st.caption(
            "Use this right after full payment to send a paid receipt with Total Paid and Balance Due."
        )
        try:
            receipt_assets = build_payment_receipt_assets(recent_paid_invoice_id)
            default_country_code = (
                get_delivery_setting("default_country_code", "1").strip() or "1"
            )
            receipt_phone_key = f"deposit_receipt_phone_{recent_paid_invoice_id}"
            receipt_email_key = f"deposit_receipt_email_{recent_paid_invoice_id}"
            receipt_message_key = f"deposit_receipt_message_{recent_paid_invoice_id}"
            if receipt_phone_key not in st.session_state:
                st.session_state[receipt_phone_key] = str(receipt_assets.get("phone", "") or "")
            if receipt_email_key not in st.session_state:
                st.session_state[receipt_email_key] = str(receipt_assets.get("email", "") or "")
            if receipt_message_key not in st.session_state:
                st.session_state[receipt_message_key] = str(receipt_assets.get("message", "") or "")

            rp1, rp2, rp3 = st.columns([1, 1, 1.8])
            rp1.download_button(
                "Download Paid Receipt PDF",
                data=receipt_assets["pdf_bytes"],
                file_name=f"{receipt_assets['file_stub']}.pdf",
                mime="application/pdf",
                key=f"deposit_tracker_paid_receipt_pdf_{recent_paid_invoice_id}",
            )
            rp2.download_button(
                "Download Paid Receipt PNG",
                data=receipt_assets["png_bytes"],
                file_name=f"{receipt_assets['file_stub']}.png",
                mime="image/png",
                key=f"deposit_tracker_paid_receipt_png_{recent_paid_invoice_id}",
            )
            rp3.markdown(
                f"**Total Cost:** {money(float(receipt_assets.get('invoice_total', 0.0)))}  \n"
                f"**Total Paid:** {money(float(receipt_assets.get('amount_paid', 0.0)))}  \n"
                f"**Balance Due:** {money(float(receipt_assets.get('balance_due', 0.0)))}"
            )

            rs1, rs2 = st.columns(2)
            receipt_phone = rs1.text_input(
                "Receipt Phone",
                value=str(st.session_state.get(receipt_phone_key, "") or ""),
                key=f"{receipt_phone_key}_input",
            )
            receipt_email = rs2.text_input(
                "Receipt Email",
                value=str(st.session_state.get(receipt_email_key, "") or ""),
                key=f"{receipt_email_key}_input",
            )
            receipt_message = st.text_area(
                "Receipt Message",
                value=str(st.session_state.get(receipt_message_key, "") or ""),
                height=100,
                key=f"{receipt_message_key}_input",
            )
            st.session_state[receipt_phone_key] = receipt_phone
            st.session_state[receipt_email_key] = receipt_email
            st.session_state[receipt_message_key] = receipt_message

            receipt_whatsapp_digits = normalize_whatsapp_to(
                receipt_phone,
                default_country_code=default_country_code,
            )
            receipt_whatsapp_url = (
                whatsapp_link(receipt_phone, receipt_message) if receipt_whatsapp_digits else ""
            )
            receipt_gmail_url = (
                gmail_compose_link(
                    receipt_email,
                    str(receipt_assets.get("subject", "Headline Rentals Payment Receipt")),
                    receipt_message,
                )
                if receipt_email.strip()
                else ""
            )
            rl1, rl2, rl3 = st.columns([1, 1, 0.8])
            if receipt_whatsapp_url:
                rl1.link_button(
                    "Send via WhatsApp",
                    receipt_whatsapp_url,
                    use_container_width=True,
                )
            else:
                rl1.info("Add phone")
            if receipt_gmail_url:
                rl2.link_button(
                    "Send via Gmail",
                    receipt_gmail_url,
                    use_container_width=True,
                )
            else:
                rl2.info("Add email")
            if rl3.button("Clear", key=f"deposit_tracker_clear_paid_receipt_{recent_paid_invoice_id}"):
                st.session_state.pop("deposit_tracker_recent_paid_invoice_id", None)
                st.session_state.pop("deposit_tracker_recent_paid_at", None)
                st.session_state.pop(receipt_phone_key, None)
                st.session_state.pop(receipt_email_key, None)
                st.session_state.pop(receipt_message_key, None)
                st.rerun()
            st.caption(
                "Tip: download the receipt first, then open WhatsApp/Gmail and attach the file to send customer proof of payment."
            )
        except Exception as exc:
            st.caption(f"Could not prepare latest paid receipt download: {exc}")
        st.markdown("---")

    tracker = invoice_level[(invoice_level["amount_outstanding"] > 0.01)].copy()
    if tracker.empty:
        st.success(
            "No confirmed-order balances outstanding right now."
        )
        return

    tracker["event_date"] = pd.to_datetime(tracker["event_date"], errors="coerce")
    tracker = tracker[tracker["event_date"].notna()].copy()
    if tracker.empty:
        st.info("Outstanding invoices do not have valid event dates for due tracking.")
        return

    tracker["due_date"] = tracker["event_date"] - pd.to_timedelta(due_days_before_event, unit="D")
    today_jm = pd.Timestamp(jamaica_now().date())
    tracker["days_to_due"] = (tracker["due_date"].dt.normalize() - today_jm).dt.days
    tracker["due_status"] = tracker["days_to_due"].apply(
        lambda d: (
            "Overdue"
            if d < 0
            else ("Due Today" if d == 0 else ("Due Soon" if d <= due_soon_window else "Upcoming"))
        )
    )
    tracker["payment_status_raw"] = (
        tracker["payment_status"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    tracker["payment_status"] = (
        tracker["payment_status_raw"]
        .map(
            {
                "unpaid": "UNPAID / COD",
                "deposit_paid": "DEPOSIT PAID",
                "paid_full": "PAID FULL",
            }
        )
        .fillna("UNKNOWN")
    )

    status_order = {"Overdue": 0, "Due Today": 1, "Due Soon": 2, "Upcoming": 3}
    tracker["status_rank"] = tracker["due_status"].map(status_order).fillna(9)
    tracker = tracker.sort_values(["status_rank", "due_date", "event_date"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Outstanding Invoices", int(len(tracker)))
    m2.metric("Outstanding Balance", money(float(tracker["amount_outstanding"].sum())))
    m3.metric("Overdue Count", int((tracker["due_status"] == "Overdue").sum()))
    m4.metric(
        "Overdue Balance",
        money(float(tracker.loc[tracker["due_status"] == "Overdue", "amount_outstanding"].sum())),
    )

    view_filter = st.selectbox(
        "View",
        options=["Overdue", "Due Today", "Due Soon", "Upcoming", "All"],
        index=0,
        key="deposit_tracker_view_filter",
    )
    if view_filter != "All":
        view = tracker[tracker["due_status"] == view_filter].copy()
    else:
        view = tracker.copy()

    if view.empty:
        st.caption("No invoices match this filter.")
    else:
        show = view.copy()
        show["event_date"] = show["event_date"].dt.date.astype(str)
        show["due_date"] = show["due_date"].dt.date.astype(str)
        show["amount_outstanding"] = show["amount_outstanding"].map(money)
        st.dataframe(
            show[
                [
                    "invoice_number",
                    "customer_name",
                    "event_date",
                    "due_date",
                    "days_to_due",
                    "due_status",
                    "amount_outstanding",
                    "payment_status",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown("**Update Balance Payment**")
    label_map = {
        (
            f"{row['invoice_number']} | {str(row['customer_name']).strip() or 'No Customer'} | "
            f"Due {row['due_date'].date().isoformat()} | Balance {money(float(row['amount_outstanding']))}"
        ): int(row["id"])
        for _, row in tracker.iterrows()
    }
    selected_label = st.selectbox(
        "Select Invoice",
        options=list(label_map.keys()),
        key="deposit_tracker_invoice_selector",
    )
    selected_id = int(label_map[selected_label])
    selected_row = tracker[tracker["id"] == selected_id].iloc[0]

    st.caption(
        "This section tracks confirmed orders with any outstanding balance (deposit, COD, or partial). "
        "When you post payment here, Finance Hub invoice profit updates automatically."
    )
    invoice_total_value_raw = pd.to_numeric(
        selected_row.get("invoice_total", 0.0),
        errors="coerce",
    )
    amount_paid_value_raw = pd.to_numeric(
        selected_row.get("amount_paid", 0.0),
        errors="coerce",
    )
    outstanding_value_raw = pd.to_numeric(
        selected_row.get("amount_outstanding", 0.0),
        errors="coerce",
    )
    invoice_total_value = float(0.0 if pd.isna(invoice_total_value_raw) else invoice_total_value_raw)
    amount_paid_value = float(0.0 if pd.isna(amount_paid_value_raw) else amount_paid_value_raw)
    outstanding_value = float(0.0 if pd.isna(outstanding_value_raw) else outstanding_value_raw)

    related_header: dict[str, object] = {}
    related_items = pd.DataFrame()
    related_bundle_error = ""
    try:
        related_header, related_items = invoice_export_bundle(selected_id)
    except Exception as exc:
        related_bundle_error = str(exc)

    st.markdown("**One-Click Debtor Invoice Download**")
    if related_bundle_error:
        st.caption(f"Could not prepare debtor download for this invoice: {related_bundle_error}")
    else:
        try:
            currency_code = get_profile_setting("currency", "JMD").strip().upper()
            currency_symbol = "JM$" if currency_code == "JMD" else "$"
            debtor_payload = build_invoice_payload(
                header=related_header,
                items=related_items,
                business_name=get_profile_setting("business_name", "Headline Rentals"),
                currency=currency_symbol,
                bank_info=DEFAULT_SELLER_BANKING,
            )
            if outstanding_value > 0.01:
                debtor_payload["payment_status"] = "deposit_paid" if amount_paid_value > 0.01 else "unpaid"
            else:
                debtor_payload["payment_status"] = "paid_full"
            debtor_payload["amount_paid"] = float(max(0.0, amount_paid_value))
            debtor_payload["balance_due_later"] = float(max(0.0, outstanding_value))
            balance_note = f"Balance Due: {money(outstanding_value)}"
            current_note = str(debtor_payload.get("notes", "") or "").strip()
            debtor_payload["notes"] = (
                f"{current_note} | {balance_note}" if current_note else balance_note
            )

            debtor_logo = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH else None
            debtor_pdf_bytes = render_invoice_pdf(debtor_payload, logo_path=debtor_logo)
            debtor_png_bytes = render_invoice_png(debtor_payload, logo_path=debtor_logo)
            debtor_file_stub = invoice_download_filename(
                customer_name=str(debtor_payload.get("customer_name", "") or ""),
                invoice_number=str(debtor_payload.get("invoice_number", "") or ""),
                document_label="Invoice Balance Due",
            )

            d1, d2, d3 = st.columns([1, 1, 1.6])
            d1.download_button(
                "Download Balance-Due PDF",
                data=debtor_pdf_bytes,
                file_name=f"{debtor_file_stub}.pdf",
                mime="application/pdf",
                key=f"deposit_tracker_debtor_pdf_{selected_id}",
            )
            d2.download_button(
                "Download Balance-Due PNG",
                data=debtor_png_bytes,
                file_name=f"{debtor_file_stub}.png",
                mime="image/png",
                key=f"deposit_tracker_debtor_png_{selected_id}",
            )
            d3.markdown(
                f"**Total Cost:** {money(invoice_total_value)}  \n"
                f"**Balance Due:** {money(outstanding_value)}"
            )
            if amount_paid_value > 0.01:
                d3.caption(f"Amount Paid So Far: {money(amount_paid_value)}")
        except Exception as exc:
            st.caption(f"Could not generate debtor download files: {exc}")

    with st.expander("Show Related Invoice Details", expanded=False):
        if related_bundle_error:
            st.caption(f"Could not load invoice details: {related_bundle_error}")
        else:
            event_date_txt = str(related_header.get("event_date", "") or "").strip()
            event_time_txt = str(related_header.get("event_time", "") or "").strip()
            customer_txt = str(related_header.get("customer_name", "") or "").strip() or "No Customer"
            location_txt = str(related_header.get("event_location", "") or "").strip() or "No Location"
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Invoice #", str(related_header.get("invoice_number", "") or "-"))
            s2.metric("Event Date/Time", f"{event_date_txt} {event_time_txt}".strip())
            s3.metric("Invoice Total", money(invoice_total_value))
            s4.metric("Balance Due", money(outstanding_value))
            st.caption(
                f"Customer: {customer_txt} | Location: {location_txt} | Paid So Far: {money(amount_paid_value)}"
            )
            if isinstance(related_items, pd.DataFrame) and not related_items.empty:
                item_view = related_items.copy()
                for col in ["quantity", "unit_price", "line_total"]:
                    item_view[col] = pd.to_numeric(item_view[col], errors="coerce").fillna(0.0)
                item_view["unit_price"] = item_view["unit_price"].map(money)
                item_view["line_total"] = item_view["line_total"].map(money)
                st.dataframe(
                    item_view[
                        ["item_name", "item_type", "quantity", "unit_price", "line_total"]
                    ].rename(
                        columns={
                            "item_name": "Item",
                            "item_type": "Type",
                            "quantity": "Qty",
                            "unit_price": "Unit Price",
                            "line_total": "Line Total",
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("No line items found for this invoice.")

    with st.form("deposit_tracker_update_form", clear_on_submit=True):
        action = st.radio(
            "Action",
            options=["Add Payment", "Mark Balance Fully Paid"],
            horizontal=True,
            key="deposit_tracker_action",
        )
        payment_method = st.radio(
            "Payment Method",
            options=["Cash", "Bank Confirmation"],
            horizontal=True,
            key="deposit_tracker_payment_method",
            help="Bank Confirmation requires an attachment before posting.",
        )
        additional_payment = st.number_input(
            "Payment Amount (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(selected_row["amount_outstanding"]) if action == "Mark Balance Fully Paid" else 0.0,
            disabled=(action == "Mark Balance Fully Paid"),
            key="deposit_tracker_add_payment_amount",
        )
        bank_reference = st.text_input(
            "Bank Reference (optional)",
            value="",
            key="deposit_tracker_bank_reference",
            disabled=(payment_method != "Bank Confirmation"),
        )
        bank_confirmation_file = st.file_uploader(
            "Attach Bank Confirmation (PNG/JPG/PDF)",
            type=["png", "jpg", "jpeg", "pdf"],
            key="deposit_tracker_bank_confirmation_file",
            help="Required when Payment Method is Bank Confirmation.",
            accept_multiple_files=False,
        )
        if payment_method != "Bank Confirmation" and bank_confirmation_file is not None:
            st.caption("Attachment selected. It will only be saved if Payment Method is Bank Confirmation.")
        payment_note = st.text_input(
            "Payment Note (optional)",
            value="",
            key="deposit_tracker_payment_note",
        )
        finance_password = st.text_input(
            "Finance Password Verification *",
            value="",
            type="password",
            key="deposit_tracker_finance_password",
            help="Required for every update (Cash, Mark Fully Paid, and Bank Confirmation).",
        )
        submit_payment = st.form_submit_button("Apply Payment Update")

    if submit_payment:
        try:
            if not finance_password_enabled():
                st.error(
                    "Finance password is not enabled. Set it first in Sidebar > Access Control."
                )
                return
            if not str(finance_password or "").strip():
                st.error(
                    "Finance password is required for all deposit tracker actions "
                    "(Cash, Mark Balance Fully Paid, and Bank Confirmation)."
                )
                return
            if not verify_finance_password(finance_password):
                st.error("Finance password is incorrect. Payment update was not posted.")
                return

            if payment_method == "Bank Confirmation" and bank_confirmation_file is None:
                st.error("Attach a bank confirmation file before posting this payment.")
                return

            current_paid = float(selected_row["amount_paid"])
            invoice_total_value = float(selected_row.get("invoice_total", selected_row["revenue"]))
            if action == "Mark Balance Fully Paid":
                new_paid = invoice_total_value
            else:
                if float(additional_payment) <= 0:
                    st.error("Enter a payment amount greater than 0.")
                    return
                new_paid = min(invoice_total_value, current_paid + float(additional_payment))
            current_status = str(selected_row.get("payment_status_raw", "paid_full")).strip().lower()
            if new_paid >= invoice_total_value - 0.01:
                new_status = "paid_full"
            elif current_status == "unpaid":
                new_status = "unpaid"
            else:
                new_status = "deposit_paid"

            payment_note_lines: list[str] = []
            prior_note = str(selected_row.get("payment_notes", "") or "").strip()
            if prior_note:
                payment_note_lines.append(prior_note)
            update_stamp = jamaica_now().strftime("%Y-%m-%d %H:%M")
            update_line = f"[{update_stamp}] Deposit tracker update | Method: {payment_method}"
            if payment_method == "Bank Confirmation" and bank_reference.strip():
                update_line += f" | Ref: {bank_reference.strip()}"
            payment_note_lines.append(update_line)
            if payment_note.strip():
                payment_note_lines.append(payment_note.strip())
            merged_payment_note = "\n".join([line for line in payment_note_lines if line]).strip()

            attachment_saved = False
            if payment_method == "Bank Confirmation" and bank_confirmation_file is not None:
                original_name = bank_confirmation_file.name or "bank_confirmation"
                safe_name = safe_filename(original_name)
                target = ATTACHMENTS_DIR / (
                    f"{selected_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_"
                    f"bank_confirmation_{safe_name}"
                )
                target.write_bytes(bank_confirmation_file.getvalue())
                suffix = target.suffix.lower().lstrip(".")
                file_type = "image" if suffix in {"png", "jpg", "jpeg"} else "pdf"
                add_invoice_attachment(
                    invoice_id=selected_id,
                    file_path=str(target),
                    file_type=file_type,
                    original_name=original_name,
                    notes=(
                        "Balance payment confirmation uploaded from Deposit Due Tracker"
                        + (
                            f" | Ref: {bank_reference.strip()}"
                            if bank_reference.strip()
                            else ""
                        )
                    ),
                )
                attachment_saved = True

            set_invoice_payment_status(
                invoice_id=selected_id,
                payment_status=new_status,
                amount_paid=float(new_paid),
                payment_notes=merged_payment_note,
            )
            if new_status == "paid_full":
                st.session_state["deposit_tracker_recent_paid_invoice_id"] = int(selected_id)
                st.session_state["deposit_tracker_recent_paid_at"] = jamaica_now().isoformat()
            finance_audit_log(
                entity_type="invoice_payment",
                entity_id=selected_id,
                action_type="update",
                notes=(
                    f"action={action} | status={new_status} | method={payment_method} | "
                    f"amount_paid={money(float(new_paid))}"
                    + (
                        f" | bank_ref={bank_reference.strip()}"
                        if payment_method == "Bank Confirmation" and bank_reference.strip()
                        else ""
                    )
                    + (" | bank_confirmation_attached=yes" if attachment_saved else "")
                ),
            )
            clear_finance_caches()
            if new_status == "paid_full":
                st.success(
                    "Balance marked paid in full and synced to Finance Hub/Invoice Profit."
                )
            else:
                st.success("Payment update saved and synced to Finance Hub.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not update payment: {exc}")


def render_mobile_and_team() -> None:
    render_section_shell(
        "Mobile & Team",
        "Deploy, install on devices, and share backups across staff.",
    )
    st.caption(
        "Run this app for your team across devices, add it to phone home screens, and keep shared backups."
    )

    business_name = get_profile_setting("business_name", "Headline Rentals")
    ops_profile = get_profile_setting("operations_profile", "Balanced")
    deployment_url = get_profile_setting("deployment_url", "")

    st.markdown("**Business Profile Snapshot**")
    st.markdown(
        f"""
        <div class="dashboard-card">
            <div class="small-label">Business</div>
            <div class="value-label">{business_name}</div>
            <div class="small-label" style="margin-top:8px;">Operating Profile: {ops_profile}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("**Mobile Install Guide**")
    if deployment_url.strip():
        st.markdown(f"Live App URL: `{deployment_url.strip()}`")
        st.markdown(
            "1. Open the URL on your phone browser.\n"
            "2. iPhone: Share -> Add to Home Screen.\n"
            "3. Android: Menu -> Install app / Add to Home Screen."
        )
    else:
        st.info(
            "Set a Deployment URL in sidebar Business Profile settings to enable one-tap mobile access instructions."
        )

    st.markdown("**Auto Backup Status**")
    storage = db_storage_status()
    s1, s2, s3 = st.columns(3)
    s1.metric("Live DB Rows", f"{int(storage.get('db_operational_rows', 0)):,}")
    s2.metric("Backup Rows", f"{int(storage.get('latest_backup_rows', 0)):,}")
    s3.metric(
        "Latest Backup",
        str(storage.get("latest_backup_at", "") or "Not yet"),
    )
    st.caption(f"Live DB path: `{storage.get('db_path', '')}`")
    st.caption(f"Auto backup folder: `{storage.get('backup_dir', '')}`")
    latest_backup_path = str(storage.get("latest_backup_path", "") or "").strip()
    if latest_backup_path:
        st.caption(f"Latest backup file: `{latest_backup_path}`")
    mirror_paths = storage.get("mirror_paths", [])
    if isinstance(mirror_paths, list) and mirror_paths:
        mirror_lines = "\n".join([f"- `{str(path)}`" for path in mirror_paths])
        st.markdown(f"Mirror backup files:\n{mirror_lines}")

    st.markdown("**Cloud Backup (Sleep/Redeploy Safe)**")
    remote_enabled = bool(storage.get("remote_backup_enabled", False))
    remote_configured = bool(storage.get("remote_backup_configured", False))
    reset_lock_active = bool(storage.get("auto_restore_suppressed", False))
    if remote_enabled:
        st.success("Cloud backup is connected. App sleep/redeploy will restore from remote backup.")
        st.caption(
            f"Repo: `{storage.get('remote_backup_repo', '')}` | "
            f"Branch: `{storage.get('remote_backup_branch', '')}` | "
            f"File: `{storage.get('remote_backup_file', '')}`"
        )
        st.caption(
            f"Restore mode: `{storage.get('remote_backup_restore_mode', 'empty_or_smaller')}` | "
            f"Pull on startup: `{bool(storage.get('remote_backup_pull_on_init', True))}`"
        )
    elif remote_configured:
        st.error(
            "Cloud backup values are present but look invalid/placeholder. "
            "Replace with real GitHub repo and token values."
        )
    else:
        st.warning(
            "Cloud backup is not configured yet. If the host sleeps/redeploys, local runtime data can reset."
        )
        st.caption(
            "Add these secrets/env vars on deployment: "
            "`HR_REMOTE_BACKUP_REPO`, `HR_REMOTE_BACKUP_TOKEN`, optional "
            "`HR_REMOTE_BACKUP_BRANCH`, `HR_REMOTE_BACKUP_FILE`, `HR_REMOTE_BACKUP_RESTORE_MODE`."
        )
    if reset_lock_active:
        st.info(
            "Auto-restore is temporarily paused because Reset All Data was used. "
            "It re-enables automatically after the next successful data backup save."
        )
    startup_restore_info = storage.get("startup_restore_info", {})
    if isinstance(startup_restore_info, dict) and bool(startup_restore_info.get("restored", False)):
        source = str(startup_restore_info.get("source", "local")).strip().lower()
        source_label = "Cloud backup" if source == "cloud" else "Local backup"
        restored_at = str(startup_restore_info.get("restored_at", "") or "").strip()
        rows_after = int(float(startup_restore_info.get("after_rows", 0) or 0))
        rows_added = int(float(startup_restore_info.get("rows_added", 0) or 0))
        st.success(
            f"Startup auto-restore used {source_label}"
            + (f" at {restored_at}" if restored_at else "")
            + f". Live rows: {rows_after:,}."
            + (f" Recovered rows: +{rows_added:,}." if rows_added > 0 else "")
        )

    if st.button("Create Backup Now", key="mobile_team_force_backup_btn"):
        try:
            created = create_db_backup_snapshot(reason="manual", force=True)
            if created is None:
                st.warning("No backup created yet (database may still be empty).")
            else:
                st.success(f"Backup created: {created}")
        except Exception as exc:
            st.error(f"Backup failed: {exc}")

    st.markdown("**Restore Backup by Time**")
    st.caption("Pick a backup timestamp and restore it immediately in this app.")
    last_restore_result = st.session_state.pop("mobile_team_last_restore_result", None)
    if isinstance(last_restore_result, dict):
        restored_rows = int(last_restore_result.get("restored_rows", 0) or 0)
        source_rows = int(last_restore_result.get("source_rows", 0) or 0)
        st.success(
            f"Last restore completed. Live rows now: {restored_rows:,}. "
            f"Selected backup rows: {source_rows:,}."
        )
        restored_summary = last_restore_result.get("restored_summary")
        if isinstance(restored_summary, dict):
            st.caption(
                "Restored backup contents: "
                f"{int(restored_summary.get('confirmed_invoices', 0) or 0):,} confirmed invoices, "
                f"{int(restored_summary.get('price_quotes', 0) or 0):,} quotes, "
                f"{int(restored_summary.get('invoice_items', 0) or 0):,} invoice items, "
                f"{int(restored_summary.get('outstanding_count', 0) or 0):,} outstanding balances."
            )
        live_summary = last_restore_result.get("live_summary")
        if isinstance(live_summary, dict) and live_summary:
            st.caption(
                "Live database after restore: "
                f"{int(live_summary.get('confirmed_invoices', 0) or 0):,} confirmed invoices, "
                f"{int(live_summary.get('invoice_items', 0) or 0):,} invoice items, "
                f"{int(live_summary.get('outstanding_count', 0) or 0):,} outstanding balances, "
                f"{money(float(live_summary.get('outstanding_total', 0.0) or 0.0))} outstanding."
            )
            if int(live_summary.get("confirmed_invoices", 0) or 0) <= 0:
                st.error(
                    "Restore completed, but the live database still has no confirmed invoices. "
                    "Choose a backup showing confirmed invoices above, or use Restore From Downloaded Backup File."
                )
    if not can_view_finance_data():
        st.info("Unlock Finance Hub password first to restore backups.")
    else:
        backup_catalog = list_backup_snapshots(limit=300)
        if backup_catalog.empty:
            st.caption("No backup snapshots available yet.")
        else:
            catalog = backup_catalog.copy()
            catalog["modified_at"] = pd.to_datetime(catalog["modified_at"], errors="coerce")
            catalog["modified_label"] = catalog["modified_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
            catalog["size_mb"] = (
                pd.to_numeric(catalog.get("size_bytes", 0), errors="coerce").fillna(0.0) / (1024 * 1024)
            )
            catalog["rows"] = pd.to_numeric(catalog.get("rows", 0), errors="coerce").fillna(0).astype(int)
            catalog["display_label"] = catalog.apply(
                lambda row: (
                    f"{row['modified_label']} | {row['source']} | "
                    f"{int(row['rows'])} rows | {float(row['size_mb']):.2f} MB"
                ),
                axis=1,
            )
            options = catalog["display_label"].astype(str).tolist()
            selected_backup_label = st.selectbox(
                "Select Backup Snapshot",
                options=options,
                key="mobile_team_restore_snapshot_select",
            )
            selected_row = catalog[catalog["display_label"] == selected_backup_label].iloc[0]
            selected_path = str(selected_row["path"])
            st.caption(f"Selected file: `{selected_path}`")
            selected_summary: dict[str, object] = {}
            try:
                selected_summary = backup_snapshot_summary(selected_path)
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Backup Rows", f"{int(selected_summary.get('operational_rows', 0) or 0):,}")
                b2.metric(
                    "Confirmed Invoices",
                    f"{int(selected_summary.get('confirmed_invoices', 0) or 0):,}",
                )
                b3.metric("Invoice Items", f"{int(selected_summary.get('invoice_items', 0) or 0):,}")
                b4.metric(
                    "Outstanding",
                    money(float(selected_summary.get("outstanding_total", 0.0) or 0.0)),
                    delta=f"{int(selected_summary.get('outstanding_count', 0) or 0):,} records",
                )
                st.caption(
                    "Also inside selected backup: "
                    f"{int(selected_summary.get('price_quotes', 0) or 0):,} saved price quotes, "
                    f"{int(selected_summary.get('expenses', 0) or 0):,} expenses, "
                    f"{int(selected_summary.get('inventory_items', 0) or 0):,} inventory items, "
                    f"{int(selected_summary.get('inventory_movements', 0) or 0):,} inventory movements."
                )
                if int(selected_summary.get("confirmed_invoices", 0) or 0) <= 0:
                    st.warning(
                        "This selected backup has no confirmed invoices. It can restore data, "
                        "but Deposit Tracker will still look empty because there are no confirmed-order balances in it."
                    )
            except Exception as exc:
                st.warning(f"Could not inspect selected backup before restore: {exc}")
            st.warning(
                "Restore replaces the current live database with the selected backup. "
                "Only restore a backup that shows the invoice/balance counts you expect above."
            )
            restore_confirm = st.checkbox(
                "I understand this will replace current live data with the selected backup.",
                key="mobile_team_restore_snapshot_confirm",
            )
            if st.button(
                "Restore Selected Backup Now",
                type="secondary",
                key="mobile_team_restore_snapshot_btn",
                disabled=not restore_confirm,
            ):
                try:
                    before_summary = selected_summary or backup_snapshot_summary(selected_path)
                    restore_result = restore_db_from_snapshot(selected_path)
                    clear_finance_caches()
                    restore_result["restored_summary"] = before_summary
                    st.session_state["mobile_team_last_restore_result"] = restore_result
                    pre_restore = str(restore_result.get("pre_restore_snapshot", "") or "").strip()
                    if pre_restore:
                        st.session_state["mobile_team_last_restore_snapshot"] = pre_restore
                    st.rerun()
                except Exception as exc:
                    st.error(f"Restore failed: {exc}")
    safety_snapshot = st.session_state.pop("mobile_team_last_restore_snapshot", "")
    if safety_snapshot:
        st.caption(f"Safety snapshot created before restore: `{safety_snapshot}`")

    st.markdown("**Restore From Downloaded Backup File**")
    st.caption(
        "Use this when you download `finance_hub_live_backup.db` from GitHub history. "
        "The app validates the file before replacing live data."
    )
    if not can_view_finance_data():
        st.info("Unlock Finance Hub password first to upload and restore a backup file.")
    else:
        uploaded_backup = st.file_uploader(
            "Upload Headline Rentals backup database (.db)",
            type=["db", "sqlite", "sqlite3"],
            key="mobile_team_uploaded_backup_file",
        )
        if uploaded_backup is not None:
            backup_bytes = uploaded_backup.getvalue()
            try:
                uploaded_info = inspect_uploaded_backup_bytes(
                    backup_bytes,
                    getattr(uploaded_backup, "name", "uploaded_backup.db"),
                )
                table_counts = uploaded_info.get("table_counts", {})
                u1, u2, u3 = st.columns(3)
                u1.metric("Backup Rows", f"{int(uploaded_info.get('operational_rows', 0)):,}")
                u2.metric("Invoices / Quotes", f"{int(table_counts.get('invoices', 0)):,}")
                u3.metric(
                    "Outstanding Balances",
                    f"{int(uploaded_info.get('outstanding_count', 0)):,}",
                    delta=money(float(uploaded_info.get("outstanding_total", 0.0))),
                )
                st.caption(
                    "Backup contains: "
                    f"{int(table_counts.get('invoice_items', 0)):,} invoice items, "
                    f"{int(table_counts.get('expenses', 0)):,} expenses, "
                    f"{int(table_counts.get('inventory_items', 0)):,} inventory items, "
                    f"{int(table_counts.get('inventory_movements', 0)):,} inventory movements."
                )
                upload_restore_confirm = st.checkbox(
                    "I understand this uploaded file will replace the current live app data.",
                    key="mobile_team_uploaded_restore_confirm",
                )
                if st.button(
                    "Restore Uploaded Backup Now",
                    type="secondary",
                    key="mobile_team_uploaded_restore_btn",
                    disabled=not upload_restore_confirm,
                ):
                    try:
                        restore_result = restore_db_from_uploaded_bytes(
                            backup_bytes,
                            getattr(uploaded_backup, "name", "uploaded_backup.db"),
                        )
                        clear_finance_caches()
                        st.success(
                            "Uploaded backup restored. "
                            f"Rows now: {int(restore_result.get('restored_rows', 0)):,}."
                        )
                        pre_restore = str(restore_result.get("pre_restore_snapshot", "") or "").strip()
                        if pre_restore:
                            st.caption(f"Safety snapshot created before restore: `{pre_restore}`")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Uploaded restore failed: {exc}")
            except Exception as exc:
                st.error(f"This file cannot be restored: {exc}")

    st.markdown("**Team Transfer Pack**")
    if DB_PATH.exists():
        st.download_button(
            "Download Team Backup (.db)",
            data=DB_PATH.read_bytes(),
            file_name="headline_rentals_finance_hub_backup.db",
            mime="application/octet-stream",
        )
    else:
        st.caption("Database not created yet.")


def build_global_search_results(query: str, finance_unlocked: bool) -> pd.DataFrame:
    needle = str(query or "").strip().lower()
    if len(needle) < 2:
        return pd.DataFrame(
            columns=[
                "type",
                "title",
                "when",
                "section",
                "context",
            ]
        )

    rows: list[dict[str, str]] = []

    invoice_rows = cached_invoice_options(include_quotes=True, confirmed_only=False)
    if not invoice_rows.empty:
        scan = invoice_rows.copy()
        for col in [
            "invoice_number",
            "customer_name",
            "event_date",
            "event_time",
            "document_type",
            "order_status",
            "created_by",
            "source_device",
        ]:
            if col not in scan.columns:
                scan[col] = ""
            scan[col] = scan[col].fillna("").astype(str)
        scan["search_blob"] = (
            scan["invoice_number"]
            + " "
            + scan["customer_name"]
            + " "
            + scan["event_date"]
            + " "
            + scan["event_time"]
            + " "
            + scan["document_type"]
            + " "
            + scan["order_status"]
            + " "
            + scan["created_by"]
            + " "
            + scan["source_device"]
        ).str.lower()
        hits = scan[scan["search_blob"].str.contains(re.escape(needle), na=False)].head(50)
        for _, row in hits.iterrows():
            rows.append(
                {
                    "type": "Invoice/Quote",
                    "title": f"{row['invoice_number']} | {row['customer_name'] or 'No Customer'}",
                    "when": f"{row['event_date']} {row['event_time']}".strip(),
                    "section": "Build Invoice",
                    "context": f"{str(row['document_type']).upper()} / {str(row['order_status']).upper()}",
                }
            )

    inventory_rows = cached_inventory_snapshot()
    if not inventory_rows.empty:
        scan = inventory_rows.copy()
        for col in ["item_name", "status", "unit"]:
            if col not in scan.columns:
                scan[col] = ""
            scan[col] = scan[col].fillna("").astype(str)
        for col in ["current_quantity", "default_rental_price"]:
            if col not in scan.columns:
                scan[col] = 0.0
            scan[col] = pd.to_numeric(scan[col], errors="coerce").fillna(0.0)
        scan["search_blob"] = (
            scan["item_name"]
            + " "
            + scan["status"]
            + " "
            + scan["unit"]
        ).str.lower()
        hits = scan[scan["search_blob"].str.contains(re.escape(needle), na=False)].head(50)
        for _, row in hits.iterrows():
            rows.append(
                {
                    "type": "Inventory",
                    "title": str(row["item_name"]),
                    "when": "",
                    "section": "Inventory",
                    "context": (
                        f"Stock {float(row['current_quantity']):g} {row['unit']} | "
                        f"Price {money(float(row['default_rental_price']))} | "
                        f"{row['status']}"
                    ),
                }
            )

    expense_rows = cached_expenses()
    if not expense_rows.empty:
        scan = expense_rows.copy()
        for col in ["category", "vendor", "description"]:
            if col not in scan.columns:
                scan[col] = ""
            scan[col] = scan[col].fillna("").astype(str)
        if "expense_date" not in scan.columns:
            scan["expense_date"] = ""
        scan["expense_date"] = pd.to_datetime(scan["expense_date"], errors="coerce").dt.date.astype("string")
        if "amount" not in scan.columns:
            scan["amount"] = 0.0
        scan["amount"] = pd.to_numeric(scan["amount"], errors="coerce").fillna(0.0)
        scan["search_blob"] = (
            scan["category"] + " " + scan["vendor"] + " " + scan["description"]
        ).str.lower()
        hits = scan[scan["search_blob"].str.contains(re.escape(needle), na=False)].head(80)
        for _, row in hits.iterrows():
            category_lower = str(row["category"]).strip().lower()
            if category_lower == "re-rental":
                rows.append(
                    {
                        "type": "Supplier Re-Rental",
                        "title": str(row["vendor"]).strip() or "Supplier",
                        "when": str(row["expense_date"] or ""),
                        "section": "Supplier Re-Rental",
                        "context": str(row["description"]).strip() or "Supplier transaction",
                    }
                )
            elif finance_unlocked:
                rows.append(
                    {
                        "type": "Expense",
                        "title": (
                            f"{str(row['category']).strip() or 'Expense'}"
                            f" | {str(row['vendor']).strip() or 'No Vendor'}"
                        ),
                        "when": str(row["expense_date"] or ""),
                        "section": "Finance Hub",
                        "context": (
                            f"{money(float(row['amount']))} | "
                            f"{str(row['description']).strip() or 'No description'}"
                        ),
                    }
                )

    if not rows:
        return pd.DataFrame(columns=["type", "title", "when", "section", "context"])

    out = pd.DataFrame(rows).drop_duplicates(
        subset=["type", "title", "when", "section", "context"]
    )
    return out.reset_index(drop=True)


def render_global_search_bar(available_sections: list[str]) -> None:
    st.markdown("**Global Search**")
    st.caption("Search invoices, customers, inventory, supplier logs, and expenses.")
    query = st.text_input(
        "Find anything in the app",
        key="global_search_query",
        placeholder="Try invoice number, customer name, item, or supplier",
    )
    if len(str(query or "").strip()) < 2:
        st.caption("Type at least 2 characters to search.")
        return

    finance_unlocked = can_view_finance_data()
    results = build_global_search_results(str(query), finance_unlocked=finance_unlocked)
    if results.empty:
        st.info("No matches found.")
        return

    st.caption(f"{len(results)} match(es) found.")
    render_paginated_dataframe(
        results[["type", "title", "when", "section", "context"]],
        key_prefix="global_search_results",
        page_size_default=10,
    )

    result_options = {
        f"[{row['type']}] {row['title']} -> {row['section']}": str(row["section"])
        for _, row in results.iterrows()
    }
    c1, c2 = st.columns([2.4, 1.1])
    selected_option = c1.selectbox(
        "Open result section",
        options=list(result_options.keys()),
        key="global_search_open_selector",
    )
    if c2.button("Open", key="global_search_open_btn", use_container_width=True):
        target_section = result_options[selected_option]
        if target_section == "Finance Hub" and not finance_unlocked:
            st.warning("Finance Hub is locked for this session.")
            return
        if set_nav_section(target_section, available_sections):
            st.rerun()


def render_today_home(
    report_start_month: str,
    alert_window_days: int,
    available_sections: list[str],
) -> None:
    render_section_shell(
        "Today Home",
        "Focus dashboard: upcoming events, payment risk, stock risk, and tasks due now.",
    )

    now_jm = jamaica_now()
    tz_jm = tzinfo_for_name(DEFAULT_EVENT_TIMEZONE)
    finance_unlocked = can_view_finance_data()
    today_token = now_jm.date().isoformat()

    events = build_event_schedule(load_event_calendar())
    window_7 = now_jm + timedelta(days=7)
    if events.empty:
        upcoming_7 = pd.DataFrame(columns=[])
    else:
        upcoming_7 = events[
            events["event_start"].apply(
                lambda dt: now_jm <= dt.astimezone(tz_jm) <= window_7
            )
        ].copy()

    invoice_level = cached_invoice_level()
    unpaid = invoice_level[invoice_level["amount_outstanding"] > 0.01].copy()
    unpaid_count = int(len(unpaid))
    unpaid_total = float(unpaid["amount_outstanding"].sum()) if not unpaid.empty else 0.0

    availability = load_inventory_availability_schedule()
    now_local = pd.Timestamp(now_jm.replace(tzinfo=None))
    next_14_local = now_local + pd.Timedelta(days=14)
    if availability.empty:
        conflicts = pd.DataFrame(columns=[])
    else:
        conflicts = availability[
            (availability["shortfall"] > 0)
            & (availability["start_dt"] >= now_local)
            & (availability["start_dt"] <= next_14_local)
        ].copy()
    conflict_count = int(len(conflicts))

    followup_due_count = 0
    if not events.empty:
        sent_log = load_notification_log()
        sent_pairs = set()
        if not sent_log.empty:
            sent_pairs = {
                (int(row["invoice_id"]), str(row["notification_type"]).strip().lower())
                for _, row in sent_log.iterrows()
            }
        followups = events[
            events["event_start"].apply(lambda dt: now_jm >= dt.astimezone(tz_jm))
        ].copy()
        if not followups.empty:
            followups = followups[
                ~followups.apply(
                    lambda row: (int(row["invoice_id"]), "post_event_followup") in sent_pairs,
                    axis=1,
                )
            ].copy()
        followup_due_count = int(len(followups))

    overdue_balance_count = 0
    if finance_unlocked and not unpaid.empty:
        due_days_rule = int(float(get_setting("finance.deposit_due_days_before_event", "3") or 3))
        today_ts = pd.Timestamp(now_jm.date())
        unpaid["due_date"] = pd.to_datetime(unpaid["event_date"], errors="coerce") - pd.to_timedelta(
            due_days_rule, unit="D"
        )
        overdue_balance_count = int((today_ts > unpaid["due_date"].dt.normalize()).sum())

    tasks_due = int(conflict_count + followup_due_count + overdue_balance_count)

    daily = cached_daily_summary()
    cash_today_value = 0.0
    if finance_unlocked and not daily.empty:
        day_rows = daily[
            pd.to_datetime(daily["day"], errors="coerce").dt.date.astype(str) == today_token
        ]
        if not day_rows.empty:
            cash_today_value = float(pd.to_numeric(day_rows["cash_collected"], errors="coerce").fillna(0.0).sum())

    upcoming_trend: list[float] = []
    next_7_days = [now_jm.date() + timedelta(days=d) for d in range(7)]
    if not upcoming_7.empty:
        upcoming_day_counts = (
            upcoming_7.assign(
                _event_day=upcoming_7["event_start"].apply(lambda dt: dt.astimezone(tz_jm).date())
            )
            .groupby("_event_day")
            .size()
            .to_dict()
        )
        upcoming_trend = [float(upcoming_day_counts.get(day_key, 0)) for day_key in next_7_days]

    outstanding_trend: list[float] = []
    monthly_for_today = apply_start_month(cached_monthly_summary(), report_start_month)
    if not monthly_for_today.empty and "outstanding_receivables" in monthly_for_today.columns:
        outstanding_trend = pd.to_numeric(
            monthly_for_today["outstanding_receivables"], errors="coerce"
        ).fillna(0.0).tolist()

    conflict_trend: list[float] = []
    if not conflicts.empty:
        conflict_day_counts = (
            conflicts.assign(_day=pd.to_datetime(conflicts["start_dt"], errors="coerce").dt.date)
            .groupby("_day")
            .size()
            .to_dict()
        )
        conflict_trend = [
            float(conflict_day_counts.get((now_local + pd.Timedelta(days=d)).date(), 0))
            for d in range(14)
        ]

    cash_trend: list[float] = []
    if finance_unlocked and not daily.empty and "cash_collected" in daily.columns:
        cash_day_counts = (
            daily.assign(_day=pd.to_datetime(daily["day"], errors="coerce").dt.date)
            .groupby("_day")["cash_collected"]
            .sum()
            .to_dict()
        )
        cash_trend = [
            float(cash_day_counts.get((now_jm - timedelta(days=d)).date(), 0.0))
            for d in range(6, -1, -1)
        ]

    task_trend = [float(conflict_count), float(followup_due_count), float(overdue_balance_count)]

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        render_kpi("Upcoming Events (7d)", f"{int(len(upcoming_7))}", trend_values=upcoming_trend)
    with c2:
        if finance_unlocked:
            render_kpi(
                "Unpaid Balances",
                f"{unpaid_count} | {money(unpaid_total)}",
                trend_values=outstanding_trend,
            )
        else:
            render_kpi("Unpaid Balances", "Locked")
    with c3:
        render_kpi("Stock Conflicts (14d)", f"{conflict_count}", trend_values=conflict_trend)
    with c4:
        render_kpi("Tasks Due", f"{tasks_due}", trend_values=task_trend)
    with c5:
        if finance_unlocked:
            render_kpi("Cash Today", money(cash_today_value), trend_values=cash_trend)
        else:
            render_kpi("Cash Today", "Locked")

    priority_lines: list[str] = []
    if conflict_count > 0:
        priority_lines.append(f"Inventory conflicts detected: {conflict_count} item-event shortfall(s).")
    if followup_due_count > 0:
        priority_lines.append(f"Client follow-ups due: {followup_due_count}.")
    if finance_unlocked and overdue_balance_count > 0:
        priority_lines.append(f"Overdue balances to follow up: {overdue_balance_count}.")
    if not priority_lines:
        priority_lines.append("No urgent blockers detected. Focus on preparing upcoming events.")

    st.markdown("**Top Priorities Today**")
    for line in priority_lines:
        st.write(f"- {line}")

    st.markdown("**Quick Actions**")
    q1, q2, q3, q4, q5 = st.columns(5)
    if q1.button("Build Invoice", key="today_quick_build_btn"):
        if set_nav_section("Build Invoice", available_sections):
            st.rerun()
    if q2.button("Deposit Tracker", key="today_quick_deposit_btn"):
        if set_nav_section("Deposit Due Tracker", available_sections):
            st.rerun()
    if q3.button("Supplier Re-Rental", key="today_quick_rerental_btn"):
        if set_nav_section("Supplier Re-Rental", available_sections):
            st.rerun()
    if q4.button("Inventory", key="today_quick_inventory_btn"):
        if set_nav_section("Inventory", available_sections):
            st.rerun()
    if q5.button("Finance Hub", key="today_quick_finance_btn"):
        if not finance_unlocked:
            st.warning("Finance Hub is locked. Unlock first.")
        elif set_nav_section("Finance Hub", available_sections):
            st.rerun()

    st.markdown("**Upcoming Events (Next 7 Days)**")
    if upcoming_7.empty:
        st.caption("No upcoming confirmed events in the next 7 days.")
    else:
        show = upcoming_7.copy().sort_values("event_start").head(10)
        show["event_date"] = show["event_start"].apply(lambda dt: dt.astimezone(tz_jm).strftime("%Y-%m-%d"))
        show["event_time"] = show["event_start"].apply(lambda dt: dt.astimezone(tz_jm).strftime("%I:%M %p"))
        cols = ["invoice_number", "event_date", "event_time", "customer_name", "event_location", "equipment_summary"]
        if finance_unlocked:
            show["revenue"] = pd.to_numeric(show["revenue"], errors="coerce").fillna(0.0).map(money)
            cols.append("revenue")
        st.dataframe(show[cols], hide_index=True, use_container_width=True)

    st.caption(
        "Cash Today uses event-date linked finance logic. For actual bank-date cash reconciliation, use Reports -> Bank Reconciliation Import."
    )


def main() -> None:
    init_db()
    startup_restore_info = get_last_startup_restore_info()
    if isinstance(startup_restore_info, dict) and bool(startup_restore_info.get("restored", False)):
        restore_token = "|".join(
            [
                str(startup_restore_info.get("source", "")),
                str(startup_restore_info.get("restored_at", "")),
                str(startup_restore_info.get("after_rows", "")),
            ]
        )
        last_token = str(st.session_state.get("startup_restore_notice_token", "") or "")
        if restore_token and restore_token != last_token:
            st.session_state["startup_restore_notice_token"] = restore_token
            st.session_state["startup_restore_notice_data"] = dict(startup_restore_info)

    purge_wattbot_runtime_state()
    if FINANCE_AUTH_SESSION_KEY not in st.session_state:
        st.session_state[FINANCE_AUTH_SESSION_KEY] = False

    st.sidebar.markdown("### Settings")
    experience_mode = st.sidebar.selectbox(
        "Experience Mode",
        options=["Guided Visual", "Balanced", "Data Dense"],
        index=0,
        help="Guided Visual is chart-first and easiest for quick understanding.",
    )
    st.session_state["experience_mode"] = experience_mode
    st.sidebar.caption("Theme: Day (fixed for readability)")
    theme_pref = "Day"
    active_theme = resolve_theme_mode(theme_pref)
    inject_styles(active_theme)

    with st.sidebar.expander("Finance Session", expanded=False):
        if can_view_finance_data():
            st.caption("Finance Hub is unlocked for this session.")
        else:
            st.caption("Finance Hub is currently locked.")
            st.caption("Open the Finance Hub section and enter password to unlock.")
        if st.button("Lock Finance Hub Session", key="lock_app_session_btn"):
            st.session_state[FINANCE_AUTH_SESSION_KEY] = False
            st.rerun()

    default_profile_name = get_profile_setting("business_name", "Headline Rentals")
    default_ops_profile = get_profile_setting("operations_profile", "Balanced")
    default_currency = get_profile_setting("currency", "JMD")
    default_deploy_url = get_profile_setting("deployment_url", "")

    with st.sidebar.expander("Business Profile", expanded=False):
        business_name = st.text_input(
            "Business Name",
            value=default_profile_name,
            key="profile_business_name",
        )
        ops_options = ["Conservative", "Balanced", "Growth"]
        ops_index = ops_options.index(default_ops_profile) if default_ops_profile in ops_options else 1
        operations_profile = st.selectbox(
            "Operations Profile",
            options=ops_options,
            index=ops_index,
            key="profile_operations_profile",
        )
        currency = st.selectbox(
            "Primary Currency",
            options=["JMD", "USD"],
            index=0 if default_currency == "JMD" else 1,
            key="profile_currency",
        )
        deployment_url = st.text_input(
            "Deployment URL (mobile access)",
            value=default_deploy_url,
            key="profile_deployment_url",
            placeholder="https://your-finance-hub-url",
        )
        if st.button("Save Business Profile", key="save_business_profile_btn"):
            set_profile_setting("business_name", business_name.strip() or "Headline Rentals")
            set_profile_setting("operations_profile", operations_profile)
            set_profile_setting("currency", currency)
            set_profile_setting("deployment_url", deployment_url.strip())
            st.success("Profile saved.")

    with st.sidebar.expander("Access Control", expanded=False):
        locked = finance_password_enabled()
        if locked:
            st.success("Finance Hub lock: Enabled")
        else:
            st.warning("Finance Hub lock: Not set")
        if st.session_state.get(FINANCE_AUTH_SESSION_KEY, False):
            st.caption("Current session: Finance Hub is unlocked")
            if st.button("Lock Current Session", key="lock_finance_session_sidebar_btn"):
                st.session_state[FINANCE_AUTH_SESSION_KEY] = False
                st.success("Finance Hub locked for current session.")
                st.rerun()

        if not locked:
            with st.form("set_finance_password_form", clear_on_submit=True):
                new_pw = st.text_input("Set Finance Password", type="password")
                confirm_pw = st.text_input("Confirm Finance Password", type="password")
                set_pw_submit = st.form_submit_button("Enable Finance Lock")
            if set_pw_submit:
                if len((new_pw or "").strip()) < 6:
                    st.error("Password must be at least 6 characters.")
                elif new_pw != confirm_pw:
                    st.error("Passwords do not match.")
                else:
                    set_finance_password(new_pw)
                    st.session_state[FINANCE_AUTH_SESSION_KEY] = True
                    st.success("Finance Hub password set and unlocked for this session.")
        else:
            with st.form("update_finance_password_form", clear_on_submit=True):
                current_pw = st.text_input("Current Finance Password", type="password")
                replacement_pw = st.text_input("New Finance Password", type="password")
                confirm_replacement_pw = st.text_input("Confirm New Password", type="password")
                update_pw_submit = st.form_submit_button("Update Finance Password")
            if update_pw_submit:
                if not verify_finance_password(current_pw):
                    st.error("Current password is incorrect.")
                elif len((replacement_pw or "").strip()) < 6:
                    st.error("New password must be at least 6 characters.")
                elif replacement_pw != confirm_replacement_pw:
                    st.error("New passwords do not match.")
                else:
                    set_finance_password(replacement_pw)
                    st.session_state[FINANCE_AUTH_SESSION_KEY] = True
                    st.success("Finance Hub password updated.")

            st.caption(
                "Finance lock remains enabled for every new app session. "
                "You can update the password above."
            )

    st.title(APP_TITLE)
    st.markdown(
        f"""
        <div class="brand-strip">
            <b>{business_name.strip() or default_profile_name}</b> | Profile: {operations_profile} | Theme: {active_theme.title()} | Experience: {experience_mode}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Track invoices, item-level profitability, individual expenses, and monthly/yearly summaries."
    )
    startup_notice = st.session_state.get("startup_restore_notice_data")
    if isinstance(startup_notice, dict) and bool(startup_notice.get("restored", False)):
        restore_source = str(startup_notice.get("source", "local")).strip().lower()
        source_label = "Cloud backup" if restore_source == "cloud" else "Local backup"
        restored_at = str(startup_notice.get("restored_at", "") or "").strip()
        try:
            rows_after = int(startup_notice.get("after_rows", 0) or 0)
        except Exception:
            rows_after = 0
        try:
            rows_added = int(startup_notice.get("rows_added", 0) or 0)
        except Exception:
            rows_added = 0
        banner_text = f"Startup recovery: restored from {source_label}"
        if restored_at:
            banner_text += f" at {restored_at}"
        banner_text += f". Live rows: {rows_after:,}."
        if rows_added > 0:
            banner_text += f" Recovered rows: +{rows_added:,}."
        st.success(banner_text)
        st.caption("Auto-restore ran successfully after app wake/redeploy.")

    alert_window_days = 14
    report_start_month = ""
    st.sidebar.caption(f"Active theme: {active_theme.title()}")

    st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
    sections = [
        "Today Home",
        "Finance Hub",
        "Build Invoice",
        "Client Retention Automation",
        "Deposit Due Tracker",
        "Supplier Re-Rental",
        "Inventory",
        "Mobile & Team",
    ]
    if st.session_state.get("nav_active_section") not in sections:
        st.session_state["nav_active_section"] = sections[0]

    apply_query_section_navigation(sections)

    pending_section = str(st.session_state.get("nav_pending_section", "") or "").strip()
    if pending_section in sections:
        st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
        st.session_state["nav_active_section"] = pending_section
        st.session_state["nav_last_synced_active"] = pending_section
        st.session_state["nav_pending_section"] = ""

    current_active = str(st.session_state.get("nav_active_section", sections[0]))
    if current_active not in sections:
        current_active = sections[0]
        st.session_state["nav_active_section"] = current_active

    if st.session_state.get("nav_sidebar_section") not in sections:
        st.session_state["nav_sidebar_section"] = current_active
    if str(st.session_state.get("nav_sidebar_section", sections[0])) != current_active:
        st.session_state["nav_sidebar_section"] = current_active

    st.session_state["nav_last_synced_active"] = current_active

    def _on_sidebar_change() -> None:
        selected = str(st.session_state.get("nav_sidebar_section", sections[0]))
        if selected in sections:
            st.session_state["nav_active_section"] = selected
            st.session_state["nav_last_synced_active"] = selected

    st.sidebar.selectbox(
        "Go to Section",
        options=sections,
        key="nav_sidebar_section",
        on_change=_on_sidebar_change,
    )

    render_top_nav_bar(current_active)

    active_section = st.session_state.get("nav_active_section", sections[0])
    if active_section == "Today Home":
        st.markdown("**Quick Find**")
        render_global_search_bar(available_sections=sections)

    if active_section == "Today Home":
        render_today_home(
            report_start_month=report_start_month,
            alert_window_days=alert_window_days,
            available_sections=sections,
        )
    elif active_section == "Finance Hub":
        render_finance_hub_section(
            report_start_month=report_start_month,
            alert_window_days=alert_window_days,
            compact_nav=True,
        )
    elif active_section == "Build Invoice":
        render_invoices()
    elif active_section == "Client Retention Automation":
        render_client_retention_automation()
    elif active_section == "Deposit Due Tracker":
        render_deposit_due_tracker(report_start_month=report_start_month)
    elif active_section == "Supplier Re-Rental":
        render_supplier_rerental()
    elif active_section == "Inventory":
        render_inventory()
    elif active_section == "Mobile & Team":
        render_mobile_and_team()


if __name__ == "__main__":
    main()
