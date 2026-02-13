from __future__ import annotations

import base64
import html
import hashlib
import hmac
import math
import mimetypes
import os
import platform
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from analytics import (
    load_event_calendar,
    load_expenses,
    load_inventory_availability_schedule,
    load_inventory_live_status,
    load_inventory_snapshot,
    load_invoice_level,
    load_monthly_summary,
    load_monthly_expense_modes,
    load_product_profitability,
    load_supplier_expenses,
    load_supplier_monthly_expenses,
    load_wages_reconciliation,
    load_yearly_summary,
)
from db import (
    DB_PATH,
    add_expense,
    add_invoice_attachment,
    add_monthly_adjustment,
    cleanup_legacy_double_counts,
    delete_invoice_attachment,
    delete_inventory_item,
    delete_expense,
    delete_invoice,
    get_setting,
    init_db,
    invoice_meta_by_number,
    invoice_export_bundle,
    invoice_options,
    load_invoice_build_log,
    load_invoice_attachments,
    load_notification_log,
    log_invoice_activity,
    mark_notification_sent,
    purge_all_records,
    upcoming_invoices,
    replace_invoice_items,
    set_invoice_payment_status,
    set_setting,
    sync_auto_invoice_inventory_movements,
    update_expense,
    update_inventory_item_values,
    upsert_inventory_item,
    upsert_invoice,
)
from importers import DEFAULT_IMPORT_PATHS, import_all
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
BRAND_LOGO_PATH = Path(__file__).with_name("assets") / "headline-rentals-logo.png"
WATTBOT_AVATAR_PATH = Path(__file__).with_name("assets") / "wattbot-avatar.jpg"
PAGE_ICON = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH.exists() else "📈"
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
    period = pd.Period(start_month, freq="M")
    out = out[out[month_col].notna()]
    return out[pd.PeriodIndex(out[month_col], freq="M") >= period]


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

    for candidate in [WATTBOT_AVATAR_PATH, BRAND_LOGO_PATH]:
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


AUTO_FEE_ITEM_NAMES = {
    "gct (15%)",
    "delivery fee",
    "set-up fee",
    "discount",
    "rental day multiplier",
    "day(s)",
}

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
    inventory = load_inventory_snapshot()
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
    st.session_state["invoice_real_invoice_status_selector"] = "Pending Confirmation (no impact yet)"
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


def wattbot_run_auto_quote(prompt_text: str) -> tuple[str, bool]:
    draft = parse_auto_quote_prompt(prompt_text)
    apply_auto_quote_draft(draft)
    st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
    st.session_state["nav_active_section"] = "Build Invoice"
    st.session_state["nav_sidebar_section"] = "Build Invoice"
    st.session_state["nav_quick_section"] = "Build Invoice"
    st.session_state["nav_last_synced_active"] = "Build Invoice"

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
    is_night = False
    bg_color = "#0b1220" if is_night else "#f3f4f6"
    surface_color = "#111827" if is_night else "#ffffff"
    sidebar_color = "#0f172a" if is_night else "#eef2f7"
    text_color = "#f8fafc" if is_night else "#111827"
    text_muted = "#cbd5e1" if is_night else "#475569"
    border_color = "rgba(203, 213, 225, 0.26)" if is_night else "#d1d5db"
    shadow_color = "rgba(2, 6, 23, 0.38)" if is_night else "rgba(15, 23, 42, 0.08)"
    accent_soft = "rgba(147, 197, 253, 0.58)" if is_night else "rgba(37, 99, 235, 0.45)"
    accent_bg = "rgba(15, 23, 42, 0.36)" if is_night else "rgba(241, 245, 249, 0.9)"
    chip_bg = "rgba(30, 41, 59, 0.72)" if is_night else "#f1f5f9"
    input_bg = "#0b1325" if is_night else "#ffffff"
    surface_alt = "#0f172a" if is_night else "#f8fafc"
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

        [data-testid="stAppViewContainer"] > .main {
            background-color: var(--bg-color) !important;
            background-image: linear-gradient(180deg, var(--accent-bg), transparent 280px);
            color: var(--text-color) !important;
        }
        [data-testid="stAppViewBlockContainer"] {
            max-width: 1220px;
            padding-top: 0.95rem;
            padding-bottom: 5.4rem;
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
            [data-testid="stAppViewBlockContainer"] {
                padding-left: 0.72rem;
                padding-right: 0.72rem;
                padding-top: 0.55rem;
                padding-bottom: 5.9rem;
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
            div[data-testid="stHorizontalBlock"] {
                display: flex;
                flex-wrap: wrap;
                gap: 0.55rem;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
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
                min-width: min(92vw, 360px);
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


def enforce_mobile_entry_layout() -> None:
    components.html(
        """
        <script>
        (() => {
            if (window.__hrMobileEntryLayoutApplied) {
                return;
            }
            window.__hrMobileEntryLayoutApplied = true;

            const ua = navigator.userAgent || "";
            const isMobile =
                /Android|iPhone|iPad|iPod|Mobile/i.test(ua) || window.innerWidth <= 860;
            if (!isMobile) {
                return;
            }

            const closeSidebarIfOpen = () => {
                try {
                    const rootDoc = window.parent?.document || document;
                    const closeBtn =
                        rootDoc.querySelector('button[aria-label="Close sidebar"]') ||
                        rootDoc.querySelector('[data-testid="stSidebarCollapseButton"]');
                    if (closeBtn) {
                        closeBtn.click();
                    }
                } catch (err) {
                    // no-op: best effort only
                }
            };

            closeSidebarIfOpen();
            setTimeout(closeSidebarIfOpen, 160);
            setTimeout(closeSidebarIfOpen, 600);
        })();
        </script>
        """,
        height=0,
    )


def render_kpi(label: str, value: str) -> None:
    st.markdown(
        f"""
        <div class="dashboard-card">
            <div class="small-label">{label}</div>
            <div class="value-label">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def current_experience_mode() -> str:
    return str(st.session_state.get("experience_mode", "Guided Visual"))


def style_plotly(fig) -> None:
    is_night = False
    axis_color = "rgba(203, 213, 225, 0.24)" if is_night else "rgba(51, 65, 85, 0.18)"
    font_color = "#f8fafc" if is_night else "#111827"
    plot_bg = "rgba(15, 23, 42, 0.18)" if is_night else "rgba(255,255,255,0.82)"
    colorway = (
        ["#60a5fa", "#34d399", "#f59e0b", "#f87171", "#a78bfa"]
        if is_night
        else ["#1d4ed8", "#059669", "#d97706", "#dc2626", "#7c3aed"]
    )
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


def render_dashboard_storyboard(
    monthly: pd.DataFrame,
    categories: pd.DataFrame,
    products: pd.DataFrame,
    upcoming: pd.DataFrame,
    report_start_month: str,
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
            <div class="insight-chip"><b>Reporting Start:</b> {report_start_month}</div>
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
        "- Navigation commands: `go to build invoice`, `open supplier re-rental`, `open inventory`, `open finance`, `open client retention`\n"
        "- Operational commands: `inventory status`, `pricing list`, `supplier spend`, `quote vs order`, `ops brief`\n"
        "- Auto quote: `auto quote customer: John, date: 2026-03-21, time: 11am, items: 10x10 tent x2, chairs x60, delivery 10000, setup 5000, gct on`\n"
        "- Conversation commands: `tell me a joke`, `motivate me`, `joke and motivation`, `what should I focus on today`\n"
        "- Finance command: `finance summary` (only after unlock)\n"
        f"- Privacy: {finance_note}"
    )


def wattbot_detect_section(prompt_text: str, available_sections: list[str]) -> str | None:
    normalized = (prompt_text or "").strip().lower()
    aliases: list[tuple[str, str]] = [
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
        ("import", "Import Legacy Data"),
        ("legacy", "Import Legacy Data"),
        ("shopify", "Import Legacy Data"),
        ("mobile", "Mobile & Team"),
        ("team", "Mobile & Team"),
    ]
    for token, section in aliases:
        if token in normalized and section in available_sections:
            return section
    return None


def wattbot_inventory_text() -> str:
    stock = load_inventory_snapshot()
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
    stock = load_inventory_snapshot()
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

    stock = load_inventory_snapshot()
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
            st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
            st.session_state["nav_active_section"] = target_section
            st.session_state["nav_sidebar_section"] = target_section
            st.session_state["nav_quick_section"] = target_section
            st.session_state["nav_last_synced_active"] = target_section
            return (f"Opening {target_section}.", True)

    if any(token in lowered for token in ["retention queue", "client retention", "follow-up queue", "followup queue"]):
        target_section = "Client Retention Automation"
        if target_section in available_sections:
            st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
            st.session_state["nav_active_section"] = target_section
            st.session_state["nav_sidebar_section"] = target_section
            st.session_state["nav_quick_section"] = target_section
            st.session_state["nav_last_synced_active"] = target_section
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
            st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
            st.session_state["nav_active_section"] = target_section
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
            "Use `Confirmed Order - Pending` for awaiting confirmation (still no impact). "
            "Use `Confirmed Order - Confirmed` to update inventory and finance.",
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
            bottom: max(16px, env(safe-area-inset-bottom));
            z-index: 1200;
        }}
        div[data-testid="stPopover"] > div > button {{
            width: 82px;
            height: 82px;
            min-height: 82px;
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
                width: 72px;
                height: 72px;
                min-height: 72px;
            }}
        }}
    </style>
    <script>
    (() => {{
        if (window.__wattbotDragInit) return;
        window.__wattbotDragInit = true;
        const KEY_X = "wattbot_right_x";
        const KEY_Y = "wattbot_right_y";
        const init = () => {{
            const popover = document.querySelector('div[data-testid="stPopover"]');
            if (!popover || popover.dataset.dragReady === "1") return;
            popover.dataset.dragReady = "1";
            const button = popover.querySelector("button");
            if (!button) return;

            popover.style.position = "fixed";
            popover.style.zIndex = "1200";
            popover.style.touchAction = "none";

            const savedX = window.localStorage.getItem(KEY_X);
            const savedY = window.localStorage.getItem(KEY_Y);
            if (savedX && savedY) {{
                popover.style.left = savedX + "px";
                popover.style.top = savedY + "px";
                popover.style.right = "auto";
                popover.style.bottom = "auto";
            }}

            let dragging = false;
            let moved = false;
            let startX = 0;
            let startY = 0;
            let baseLeft = 0;
            let baseTop = 0;

            const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
            const applyPosition = (nextLeft, nextTop) => {{
                popover.style.left = nextLeft + "px";
                popover.style.top = nextTop + "px";
                popover.style.right = "auto";
                popover.style.bottom = "auto";
            }};
            const onMovePoint = (clientX, clientY) => {{
                const dx = clientX - startX;
                const dy = clientY - startY;
                if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
                const nextLeft = clamp(baseLeft + dx, 8, window.innerWidth - popover.offsetWidth - 8);
                const nextTop = clamp(baseTop + dy, 8, window.innerHeight - popover.offsetHeight - 8);
                applyPosition(nextLeft, nextTop);
            }};
            const onPointerMove = (e) => {{
                if (!dragging) return;
                onMovePoint(e.clientX, e.clientY);
            }};
            const onPointerUp = () => {{
                if (!dragging) return;
                dragging = false;
                button.style.cursor = "grab";
                const rect = popover.getBoundingClientRect();
                window.localStorage.setItem(KEY_X, String(Math.round(rect.left)));
                window.localStorage.setItem(KEY_Y, String(Math.round(rect.top)));
                window.removeEventListener("pointermove", onPointerMove);
                window.removeEventListener("pointerup", onPointerUp);
                window.removeEventListener("pointercancel", onPointerUp);
                if (moved) {{
                    popover.dataset.justDragged = "1";
                    window.setTimeout(() => {{
                        popover.dataset.justDragged = "0";
                    }}, 220);
                }}
            }};

            button.addEventListener("pointerdown", (e) => {{
                if (e.button !== undefined && e.button !== 0) return;
                dragging = true;
                moved = false;
                startX = e.clientX;
                startY = e.clientY;
                const rect = popover.getBoundingClientRect();
                baseLeft = rect.left;
                baseTop = rect.top;
                button.style.cursor = "grabbing";
                window.addEventListener("pointermove", onPointerMove);
                window.addEventListener("pointerup", onPointerUp);
                window.addEventListener("pointercancel", onPointerUp);
                e.preventDefault();
            }}, {{ passive: false }});

            const clampToViewport = () => {{
                const rect = popover.getBoundingClientRect();
                if (!rect.width || !rect.height) return;
                const nextLeft = clamp(rect.left, 8, window.innerWidth - rect.width - 8);
                const nextTop = clamp(rect.top, 8, window.innerHeight - rect.height - 8);
                applyPosition(nextLeft, nextTop);
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


def save_invoice(form_data: dict, raw_items: pd.DataFrame) -> int:
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

    previous_meta = invoice_meta_by_number(form_data["invoice_number"])
    invoice_id = upsert_invoice(
        invoice_number=form_data["invoice_number"],
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
        payment_notes=form_data.get("payment_notes", ""),
        notes=form_data["notes"],
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
        invoice_number=form_data["invoice_number"],
        action_type=action_type,
        document_type=normalized_doc_type,
        order_status=normalized_status,
        actor_name=form_data.get("created_by", ""),
        device_name=form_data.get("source_device", ""),
        notes=action_note,
    )
    return invoice_id


def invoice_label_map() -> dict[str, int | None]:
    invoices = invoice_options(include_quotes=False, confirmed_only=True)
    label_to_id = {"Not linked to invoice": None}
    for _, row in invoices.iterrows():
        date_part = row["event_date"] if row["event_date"] else "No Date"
        time_part = row["event_time"] if row.get("event_time", "") else ""
        label = (
            f"{row['invoice_number']} | "
            f"{date_part}{' ' + time_part if time_part else ''} | "
            f"{row['customer_name'] if row['customer_name'] else 'No Customer'}"
        )
        label_to_id[label] = int(row["id"])
    return label_to_id


def invoice_choice_map() -> dict[str, int]:
    invoices = invoice_options()
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
    for col in [
        "revenue",
        "amount_paid",
        "amount_outstanding",
        "item_cost",
        "invoice_expenses",
        "net_profit",
    ]:
        table[col] = table[col].map(money)
    table["payment_status"] = table["payment_status"].map(
        {
            "unpaid": "UNPAID",
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
                "revenue",
                "amount_paid",
                "amount_outstanding",
                "payment_status",
                "item_cost",
                "invoice_expenses",
                "net_profit",
                "payment_reminder",
            ]
        ],
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
            "These are currently deposit/partial payments."
        )
        pending_show = pending.copy()
        pending_show["event_date"] = pending_show["event_date"].dt.date.astype("string")
        pending_show["revenue"] = pending_show["revenue"].map(money)
        pending_show["amount_paid"] = pending_show["amount_paid"].map(money)
        pending_show["amount_outstanding"] = pending_show["amount_outstanding"].map(money)
        st.dataframe(
            pending_show[
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
            revenue_total = float(current_row["revenue"])
            if action == "Mark as Fully Paid":
                new_paid = revenue_total
            else:
                if float(additional_payment) <= 0:
                    st.error("Additional payment must be greater than 0.")
                    return
                new_paid = min(revenue_total, current_paid + float(additional_payment))
            new_status = "paid_full" if new_paid >= revenue_total - 0.01 else "deposit_paid"
            set_invoice_payment_status(
                invoice_id=selected_id,
                payment_status=new_status,
                amount_paid=float(new_paid),
                payment_notes=payment_note.strip(),
            )
            st.success("Invoice payment status updated.")
            st.rerun()

    st.markdown("---")
    st.markdown("**Manage Invoices (Edit/Delete)**")
    invoice_records = invoice_options()
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
    status_options = ["Pending", "Confirmed", "Cancelled"]
    status_lookup = {"pending": 0, "confirmed": 1, "cancelled": 2}
    status_index = status_lookup.get(status_value, 1)
    payment_options = ["Unpaid", "Deposit Paid", "Paid Full"]
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
        edited_rental_hours = j3.number_input(
            "Rental Hours",
            min_value=1.0,
            max_value=240.0,
            step=1.0,
            value=float(invoice_header.get("rental_hours", DEFAULT_EVENT_HOURS) or DEFAULT_EVENT_HOURS),
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
            status_map = {"Pending": "pending", "Confirmed": "confirmed", "Cancelled": "cancelled"}
            payment_map = {"Unpaid": "unpaid", "Deposit Paid": "deposit_paid", "Paid Full": "paid_full"}

            clean_items = normalize_invoice_items_df(edited_items_table)
            clean_items = clean_items[
                (clean_items["item_name"].str.strip() != "")
                & (clean_items["quantity"] > 0)
            ].copy()
            clean_items = clean_items[
                ["item_name", "item_type", "quantity", "unit_price", "unit_cost"]
            ].copy()

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
                    "payment_status": payment_map[edited_payment_label],
                    "amount_paid": float(edited_amount_paid),
                    "payment_notes": edited_payment_notes,
                    "notes": edited_notes,
                },
                clean_items,
            )
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
                st.success(f"Invoice deleted: {deleted.get('invoice_number', '')}")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete invoice: {exc}")


def render_finance_hub(
    report_start_month: str,
    alert_window_days: int,
    compact_nav: bool = False,
) -> None:
    st.subheader("Finance Hub")
    st.caption(
        "Finance-only workspace for dashboard insights, expenses, reports, and invoice profitability."
    )

    finance_views = [
        "Dashboard",
        "Expenses",
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
    elif active_view == "Reports":
        render_reports(report_start_month=report_start_month)
    else:
        render_invoice_profit_table()


def render_finance_danger_zone() -> None:
    st.markdown("---")
    with st.expander("Owner Danger Zone: Reset All Records", expanded=False):
        st.warning(
            "Permanent action: this clears invoices, invoice items, expenses, monthly adjustments, "
            "inventory, inventory movements, notification log, build log, and invoice attachments."
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
        st.subheader("Finance Hub Setup")
        st.warning(
            "Set a Finance Hub password to protect wages, profit, expenses, and reports."
        )
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
        render_finance_danger_zone()
        return

    st.subheader("Finance Hub Locked")
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
    st.subheader("Dashboard")
    experience = current_experience_mode()

    monthly = apply_start_month(load_monthly_summary(), report_start_month)
    invoice_level = apply_start_month(load_invoice_level(), report_start_month)
    products = load_product_profitability()
    expenses_view = apply_start_month(load_expenses(), report_start_month)
    if not expenses_view.empty:
        expenses_view = expenses_view[
            expenses_view["expense_kind"].str.lower() != "summary_rollup"
        ]
    categories = (
        expenses_view.groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
        if not expenses_view.empty
        else pd.DataFrame(columns=["category", "amount"])
    )
    upcoming = upcoming_invoices(days_ahead=alert_window_days)

    if monthly.empty:
        st.info("No data yet. Add invoices/expenses or run the importer.")
    else:
        latest = monthly.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_kpi("Latest Month Revenue", money(float(latest["revenue"])))
        with c2:
            render_kpi("Latest Month Expenses", money(float(latest["total_expenses"])))
        with c3:
            render_kpi("Latest Month Net Profit", money(float(latest["net_profit"])))
        with c4:
            render_kpi(
                "After Adjustments",
                money(float(latest["net_profit_after_adjustments"])),
            )

        p1, p2 = st.columns(2)
        with p1:
            render_kpi("Latest Month Cash Collected", money(float(latest.get("cash_collected", 0.0))))
        with p2:
            render_kpi(
                "Latest Month Outstanding",
                money(float(latest.get("outstanding_receivables", 0.0))),
            )

        trend = monthly[
            ["month_label", "revenue", "total_expenses", "net_profit_after_adjustments"]
        ].rename(
            columns={
                "revenue": "Revenue",
                "total_expenses": "Expenses",
                "net_profit_after_adjustments": "Net Profit (After Adjustments)",
            }
        )
        fig = px.line(
            trend,
            x="month_label",
            y=["Revenue", "Expenses", "Net Profit (After Adjustments)"],
            markers=True,
            labels={
                "value": "Amount (JMD)",
                "month_label": "Month",
                "variable": "Metric",
            },
            title=f"Monthly Financial Trend (from {report_start_month})",
        )
        style_plotly(fig)
        st.plotly_chart(fig, use_container_width=True)

        if not invoice_level.empty:
            outstanding = invoice_level[invoice_level["amount_outstanding"] > 0.01].copy()
            if not outstanding.empty:
                st.warning(
                    f"Payment reminder: {len(outstanding)} confirmed order(s) still have outstanding balances."
                )
                outstanding_show = outstanding.copy().sort_values("event_date", ascending=True)
                outstanding_show["event_date"] = outstanding_show["event_date"].dt.date.astype("string")
                outstanding_show["revenue"] = outstanding_show["revenue"].map(money)
                outstanding_show["amount_paid"] = outstanding_show["amount_paid"].map(money)
                outstanding_show["amount_outstanding"] = outstanding_show["amount_outstanding"].map(money)
                st.dataframe(
                    outstanding_show[
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
                    hide_index=True,
                    use_container_width=True,
                )

    if experience != "Data Dense":
        st.markdown("**Visual Storyboard**")
        st.caption(
            "A chart-first view designed for quick understanding and easier pattern spotting."
        )
        render_dashboard_storyboard(
            monthly=monthly,
            categories=categories,
            products=products,
            upcoming=upcoming,
            report_start_month=report_start_month,
        )

    st.markdown(f"**Upcoming Events (Next {alert_window_days} Days)**")
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
        st.dataframe(
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
            hide_index=True,
            use_container_width=True,
        )

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("**Top Product Profitability**")
        if products.empty:
            st.caption("No invoice items yet.")
        else:
            display = products.copy()
            display["revenue"] = display["revenue"].map(money)
            display["direct_cost"] = display["direct_cost"].map(money)
            display["allocated_expenses"] = display["allocated_expenses"].map(money)
            display["net_profit"] = display["net_profit"].map(money)
            display["margin_pct"] = display["margin_pct"].map(lambda x: f"{x:,.1f}%")
            st.dataframe(display.head(10), use_container_width=True, hide_index=True)

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
                title=f"Expense Breakdown (from {report_start_month})",
            )
            style_plotly(donut)
            st.plotly_chart(donut, use_container_width=True)


def render_invoices() -> None:
    st.subheader("Build Invoice")
    st.caption(
        "Invoice-only workspace: create customer invoices, generate downloadable files, and attach invoice documents."
    )

    history = invoice_options()
    default_actor = ""
    detected_device = current_device_name()
    customer_profiles = sorted(
        {
            str(name).strip()
            for name in history["customer_name"].tolist()
            if str(name).strip()
        }
    ) if not history.empty else []
    inventory = load_inventory_snapshot()
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
        "invoice_real_invoice_status_selector": "Confirmed (impacts Finance/Inventory)",
        "invoice_created_by_input": default_actor,
        "invoice_delivered_to_input": "",
        "invoice_paid_to_input": "",
        "invoice_notes_input": "",
        "invoice_apply_gct_input": True,
        "invoice_delivery_manual_amount_input": 0.0,
        "invoice_setup_fee_input": 0.0,
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
        "invoice_export_selected_id": None,
        "invoice_last_saved_id": None,
    }
    for key, default in state_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default

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
        st.session_state["invoice_real_invoice_status_selector"] = "Confirmed (impacts Finance/Inventory)"
        st.session_state["invoice_delivered_to_input"] = ""
        st.session_state["invoice_paid_to_input"] = ""
        st.session_state["invoice_notes_input"] = ""
        st.session_state["invoice_apply_gct_input"] = True
        st.session_state["invoice_delivery_manual_amount_input"] = 0.0
        st.session_state["invoice_setup_fee_input"] = 0.0
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
        st.session_state["invoice_last_saved_id"] = None
        st.toast("New blank invoice ready.")
        st.rerun()

    c1, c2 = st.columns([1, 1.25])
    selected_customer_profile = c1.selectbox(
        "Customer Profile",
        options=["Manual Entry"] + customer_profiles,
        key="customer_profile_selector",
    )
    if (
        selected_customer_profile != "Manual Entry"
        and not st.session_state.get("invoice_customer_name_input", "").strip()
    ):
        st.session_state["invoice_customer_name_input"] = selected_customer_profile

    template_item = c2.selectbox(
        "Quick Add Line Item Template",
        options=[""] + sorted(inventory_templates.keys()),
        key="invoice_template_item_selector",
    )
    if template_item:
        q1, q2, q3 = st.columns(3)
        template_qty = q1.number_input(
            "Template Qty",
            min_value=1.0,
            step=1.0,
            value=1.0,
            key="invoice_template_qty",
        )
        template_price = q2.number_input(
            "Template Unit Price (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(inventory_templates.get(template_item, {}).get("unit_price", 0.0)),
            key="invoice_template_price",
        )
        template_type = q3.selectbox(
            "Template Type",
            options=["product", "service"],
            key="invoice_template_type",
        )
        if st.button("Add Template Item", key="invoice_template_add_btn"):
            row = {
                "item_name": template_item,
                "item_type": template_type,
                "quantity": float(template_qty),
                "unit_price": float(template_price),
            }
            current = st.session_state["invoice_items_editor_data"].copy()
            st.session_state["invoice_items_editor_data"] = pd.concat(
                [current, pd.DataFrame([row])],
                ignore_index=True,
            )
            st.session_state["invoice_items_editor_seed"] += 1
            st.success(f"Added template item: {template_item}")

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

    def _apply_parsed_invoice(parsed: dict) -> None:
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

    if extract_upload and uploaded_pdf is not None:
        try:
            parsed = parse_invoice_pdf(uploaded_pdf.getvalue(), uploaded_pdf.name)
            _apply_parsed_invoice(parsed)
            st.success("PDF extracted and form pre-filled.")
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
                _apply_parsed_invoice(parsed)
                st.success("PDF extracted and form pre-filled.")
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

    st.markdown("**Document Type**")
    mode_col1, mode_col2 = st.columns([1.2, 1.8])
    document_type_choice = mode_col1.radio(
        "Build As",
        options=["Price Quote", "Confirmed Order"],
        key="invoice_document_type_selector",
        horizontal=True,
    )
    if document_type_choice == "Price Quote":
        real_invoice_status = "Pending Confirmation (no impact yet)"
        mode_col2.caption("Quote mode: no Finance/Inventory impact.")
        document_mode = "Price Quote (no impact)"
        st.info(
            "You are building a PRICE QUOTE. This will not affect Finance Hub or Inventory."
        )
    else:
        mode_col2.info(
            "Confirmed Orders can affect prices/totals in Finance Hub. Set status below."
        )
        real_invoice_status = mode_col2.selectbox(
            "Confirmed Order Status",
            options=[
                "Confirmed (impacts Finance/Inventory)",
                "Pending Confirmation (no impact yet)",
            ],
            key="invoice_real_invoice_status_selector",
        )
        document_mode = (
            "Confirmed Order - Confirmed (impacts Finance/Inventory)"
            if real_invoice_status == "Confirmed (impacts Finance/Inventory)"
            else "Confirmed Order - Pending Confirmation"
        )
        if real_invoice_status == "Confirmed (impacts Finance/Inventory)":
            st.success(
                "You are building a CONFIRMED ORDER (CONFIRMED). This will affect Finance Hub prices/totals and update Inventory now."
            )
        else:
            st.warning(
                "You are building a CONFIRMED ORDER (PENDING). It will not affect Finance Hub/Inventory until status is switched to Confirmed."
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

    st.markdown("**Items**")
    st.caption(
        "As you edit Qty and Unit Cost, totals below update automatically. "
        "Day(s) multiplier is added as a separate line."
    )
    mobile_ctrl_a, mobile_ctrl_b, mobile_ctrl_c = st.columns(3)
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

    items_editor_source = default_items.copy()
    items_editor_source["quantity"] = pd.to_numeric(
        items_editor_source.get("quantity", 0), errors="coerce"
    ).fillna(0.0)
    items_editor_source["unit_price"] = pd.to_numeric(
        items_editor_source.get("unit_price", 0), errors="coerce"
    ).fillna(0.0)
    items_editor_source["base_rental"] = (
        items_editor_source["quantity"] * items_editor_source["unit_price"]
    ).round(2)
    items_editor_source = items_editor_source[
        ["item_name", "item_type", "quantity", "unit_price", "base_rental"]
    ]
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
    if not items_editor_source.empty:
        row_labels = []
        for idx, row in items_editor_source.reset_index(drop=True).iterrows():
            row_name = str(row.get("item_name", "")).strip() or "(new item)"
            row_labels.append(f"{idx + 1}. {row_name}")

        row_pick_col, quick_ops_col = st.columns([1.35, 2.2])
        picked_row_label = row_pick_col.selectbox(
            "Quick Row",
            options=row_labels,
            key="invoice_items_quick_row_label",
            help="Mobile quick controls for Qty and Unit Cost.",
        )
        picked_row_idx = max(0, row_labels.index(picked_row_label))

        def _apply_quick_edit(kind: str) -> None:
            rows_now = normalize_invoice_items_df(st.session_state.get("invoice_items_editor_data", pd.DataFrame()))
            if rows_now.empty or picked_row_idx >= len(rows_now):
                return
            if kind == "qty_minus":
                rows_now.at[picked_row_idx, "quantity"] = max(
                    0.0,
                    float(pd.to_numeric(rows_now.at[picked_row_idx, "quantity"], errors="coerce") or 0.0) - 1.0,
                )
            elif kind == "qty_plus":
                rows_now.at[picked_row_idx, "quantity"] = max(
                    0.0,
                    float(pd.to_numeric(rows_now.at[picked_row_idx, "quantity"], errors="coerce") or 0.0) + 1.0,
                )
            elif kind == "cost_minus":
                rows_now.at[picked_row_idx, "unit_price"] = max(
                    0.0,
                    float(pd.to_numeric(rows_now.at[picked_row_idx, "unit_price"], errors="coerce") or 0.0) - 100.0,
                )
            elif kind == "cost_plus":
                rows_now.at[picked_row_idx, "unit_price"] = max(
                    0.0,
                    float(pd.to_numeric(rows_now.at[picked_row_idx, "unit_price"], errors="coerce") or 0.0) + 100.0,
                )
            elif kind == "remove_selected":
                rows_now = rows_now.drop(index=picked_row_idx).reset_index(drop=True)
                if rows_now.empty:
                    rows_now = pd.DataFrame(
                        [{"item_name": "", "item_type": "product", "quantity": 1.0, "unit_price": 0.0}]
                    )
            st.session_state["invoice_items_editor_data"] = rows_now[
                ["item_name", "item_type", "quantity", "unit_price"]
            ].copy()
            st.session_state["invoice_items_editor_seed"] += 1
            st.rerun()

        q1, q2, q3, q4, q5 = quick_ops_col.columns(5)
        if q1.button("Qty -1", key="invoice_items_quick_qty_minus"):
            _apply_quick_edit("qty_minus")
        if q2.button("Qty +1", key="invoice_items_quick_qty_plus"):
            _apply_quick_edit("qty_plus")
        if q3.button("Cost -100", key="invoice_items_quick_cost_minus"):
            _apply_quick_edit("cost_minus")
        if q4.button("Cost +100", key="invoice_items_quick_cost_plus"):
            _apply_quick_edit("cost_plus")
        if q5.button("Remove Row", key="invoice_items_quick_remove_row"):
            _apply_quick_edit("remove_selected")

    items = st.data_editor(
        items_editor_source,
        num_rows="dynamic",
        use_container_width=True,
        key=editor_key,
        disabled=["base_rental"],
        column_config={
            "item_name": st.column_config.TextColumn("Item Name"),
            "item_type": st.column_config.SelectboxColumn(
                "Type", options=["product", "service"]
            ),
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0),
            "unit_price": st.column_config.NumberColumn(
                "Unit Cost", min_value=0.0, step=100.0
            ),
            "base_rental": st.column_config.NumberColumn(
                "Base Rental",
                min_value=0.0,
                step=100.0,
                format="%.2f",
            ),
        },
    )
    items = normalize_invoice_items_df(items)
    st.session_state["invoice_items_editor_data"] = items[
        ["item_name", "item_type", "quantity", "unit_price"]
    ].copy()
    if item_name_suggestions:
        st.caption(
            "Inventory suggestions: "
            + ", ".join(item_name_suggestions[:12])
            + ("..." if len(item_name_suggestions) > 12 else "")
        )

    st.markdown("**Auto Fees (Delivery, Set-Up, Discount, GCT)**")
    st.caption(
        "Enter Delivery/Set-Up manually, optionally apply discount, then toggle GCT."
    )

    preview_items = remove_auto_fee_rows(items)
    preview_items = preview_items[
        (preview_items["item_name"].str.strip() != "")
        & (preview_items["quantity"] > 0)
    ].copy()
    base_subtotal = float((preview_items["quantity"] * preview_items["unit_price"]).sum())
    day_multiplier_amount = round(base_subtotal * float(max(0, rental_days - 1)), 2)
    adjusted_rental_subtotal = round(base_subtotal + day_multiplier_amount, 2)

    fee0, fee1, fee2, fee3 = st.columns([1, 1, 1, 1])
    apply_gct = fee0.checkbox(
        "Add GCT (15%)",
        key="invoice_apply_gct_input",
        help="If enabled, GCT is calculated on (Adjusted Rental + Delivery + Set-Up - Discount).",
    )
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
    discount_mode = fee3.selectbox(
        "Discount Type",
        options=["No Discount", "Discount %", "Discount Amount (JMD)"],
        key="invoice_discount_mode_input",
    )
    discount_percent_input = 0.0
    discount_amount_input = 0.0
    if discount_mode == "Discount %":
        discount_percent_input = st.number_input(
            "Discount (%)",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            key="invoice_discount_percent_input",
        )
    elif discount_mode == "Discount Amount (JMD)":
        discount_amount_input = st.number_input(
            "Discount Amount (JMD)",
            min_value=0.0,
            step=100.0,
            key="invoice_discount_amount_input",
        )
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

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Base Rental (Qty x Unit Price)", money(base_subtotal))
    p2.metric("Day(s) Multiplier", f"x{rental_days}")
    p3.metric("Day Multiplier Amount", money(day_multiplier_amount))
    p4.metric("Adjusted Rental", money(adjusted_rental_subtotal))
    p5, p6, p7, p8 = st.columns(4)
    p5.metric("Delivery", money(delivery_amount))
    p6.metric("Set-Up", money(setup_amount))
    p7.metric("Discount", money(discount_amount))
    p8.metric("GCT (15%)", money(gct_amount))
    st.caption(f"Pre-Discount Total (Adjusted Rental + Delivery + Set-Up): {money(pre_discount_total)}")
    st.metric("Estimated Total", money(estimated_total))
    if rental_days > 1:
        st.info(
            f"Day multiplier active: +{money(day_multiplier_amount)} for {rental_days} day(s). "
            "Unit prices stay unchanged; delivery/set-up/GCT are separate."
        )
    if discount_amount > 0:
        st.info(f"Discount applied: {money(discount_amount)} (reduces total before GCT).")

    st.markdown("**Payment Terms**")
    is_quote_mode = document_mode == "Price Quote (no impact)"
    real_payment_terms = st.radio(
        "Payment Option",
        options=["Paid In Full", "50% Deposit (Balance Later)"],
        key="invoice_real_payment_terms_input",
        horizontal=True,
        help="Deposit is calculated from final total after rental, delivery, set-up, discount, and GCT.",
    )
    payment_note = st.text_input(
        "Payment Note (optional)",
        key="invoice_additional_payment_note_input",
        placeholder="e.g. Deposit received via bank transfer",
    )
    if is_quote_mode:
        st.caption(
            "Quote mode: payment terms are for customer communication only and do not affect Finance Hub/Inventory."
        )

    if real_payment_terms == "50% Deposit (Balance Later)":
        paid_now_amount = round(float(estimated_total) * 0.5, 2)
        outstanding_amount = round(float(estimated_total) - paid_now_amount, 2)
        st.warning(
            f"Deposit mode selected: deposit due now {money(paid_now_amount)}."
        )
        st.markdown(
            f"**Deposit Due Now (50%)**: {money(paid_now_amount)}"
        )
    else:
        paid_now_amount = float(estimated_total)
        outstanding_amount = 0.0
        st.info(f"Full payment mode selected: {money(paid_now_amount)} will be logged as paid.")

    submitted = st.button("Save Invoice", key="invoice_save_button")

    if submitted:
        if not invoice_number.strip():
            st.error("Invoice number is required.")
        else:
            try:
                base_items = normalize_invoice_items_df(items)
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

                if document_mode == "Price Quote (no impact)":
                    document_type = "quote"
                    order_status = "pending"
                    payment_status = (
                        "deposit_paid"
                        if real_payment_terms == "50% Deposit (Balance Later)"
                        else "paid_full"
                    )
                    amount_paid_value = float(paid_now_amount)
                    payment_notes_value = payment_note.strip()
                elif document_mode == "Confirmed Order - Pending Confirmation":
                    document_type = "invoice"
                    order_status = "pending"
                    payment_status = (
                        "deposit_paid"
                        if real_payment_terms == "50% Deposit (Balance Later)"
                        else "paid_full"
                    )
                    amount_paid_value = float(paid_now_amount)
                    payment_notes_value = payment_note.strip()
                else:
                    document_type = "invoice"
                    order_status = "confirmed"
                    payment_status = (
                        "deposit_paid"
                        if real_payment_terms == "50% Deposit (Balance Later)"
                        else "paid_full"
                    )
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
                        "payment_notes": payment_notes_value,
                        "notes": notes,
                    },
                    items_to_save,
                )
                if document_type == "quote":
                    st.success(f"Price quote {invoice_number.strip()} saved (no finance/inventory impact).")
                elif order_status == "confirmed":
                    st.success(
                        f"Confirmed order {invoice_number.strip()} saved. "
                        "Inventory movement entries were added automatically."
                    )
                else:
                    st.success(f"Pending confirmed order {invoice_number.strip()} saved (no impact until confirmed).")
                st.session_state["invoice_last_saved_id"] = int(saved_invoice_id)
                st.session_state["invoice_export_selected_id"] = int(saved_invoice_id)
                level, message = invoice_due_message(event_date)
                if document_type == "invoice" and order_status == "confirmed":
                    st.toast(f"Invoice {invoice_number.strip()} saved. {message}")
                    if level == "warning":
                        st.warning(f"Upcoming Event Alert: {message}")
                    else:
                        st.info(f"Event Timeline: {message}")
                else:
                    st.info(
                        "This document is not yet impacting Finance Hub/Inventory until it is a confirmed order."
                    )
                st.caption(
                    f"Auto fees saved -> Day Multiplier: {money(day_multiplier_amount)} | "
                    f"Delivery: {money(delivery_amount)} | "
                    f"Set-Up: {money(setup_amount)} | Discount: {money(discount_amount)} | GCT: {money(gct_amount)}"
                )
                if (
                    document_type == "invoice"
                    and payment_status == "deposit_paid"
                    and float(outstanding_amount) > 0
                ):
                    st.warning(
                        f"Finance reminder: deposit logged ({money(amount_paid_value)})."
                    )
                st.session_state["invoice_parse_warnings"] = []
            except Exception as exc:
                st.error(f"Could not save invoice: {exc}")

    latest_saved_id = st.session_state.get("invoice_last_saved_id")
    if latest_saved_id is not None:
        st.markdown("**Quick Actions: Last Saved Invoice**")
        st.caption("No selector needed. These actions target the invoice you just saved.")
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
            latest_doc_label = (
                "Price Quote"
                if str(latest_payload.get("document_type", "invoice")).strip().lower() == "quote"
                else "Invoice"
            )
            latest_logo = str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH.exists() else None
            latest_pdf_bytes = render_invoice_pdf(latest_payload, logo_path=latest_logo)
            latest_png_bytes = render_invoice_png(latest_payload, logo_path=latest_logo)
            latest_file_stub = invoice_download_filename(
                customer_name=str(latest_payload.get("customer_name", "") or ""),
                invoice_number=str(latest_payload.get("invoice_number", "") or ""),
                document_label=latest_doc_label,
            )

            q1, q2, q3 = st.columns([1, 1, 1.6])
            q1.download_button(
                "Quick Download PDF",
                data=latest_pdf_bytes,
                file_name=f"{latest_file_stub}.pdf",
                mime="application/pdf",
                key=f"quick_invoice_pdf_{int(latest_saved_id)}",
            )
            q2.download_button(
                "Quick Download PNG",
                data=latest_png_bytes,
                file_name=f"{latest_file_stub}.png",
                mime="image/png",
                key=f"quick_invoice_png_{int(latest_saved_id)}",
            )
            q3.markdown(
                f"**Invoice:** {latest_payload.get('invoice_number', '')}  \n"
                f"**Total:** {money(float(latest_payload.get('total', 0.0)))}"
            )
            if str(latest_payload.get("payment_status", "paid_full")).strip().lower() == "deposit_paid":
                q3.caption(
                    "Deposit Due Now (50%): "
                    f"{money(float(latest_payload.get('deposit_due_now', 0.0)))}"
                )

            latest_country_code = (
                get_delivery_setting("default_country_code", "1").strip() or "1"
            )
            resolved_latest_contact = resolve_contact_channels(
                customer_phone=str(latest_payload.get("customer_phone", "")).strip(),
                customer_email=str(latest_payload.get("customer_email", "")).strip(),
                contact_detail=str(latest_header.get("contact_detail", "")).strip(),
            )
            latest_phone_default = str(resolved_latest_contact.get("phone", "")).strip()
            latest_email_default = str(resolved_latest_contact.get("email", "")).strip()
            latest_subject = (
                f"{latest_payload.get('business_name', 'Headline Rentals')} "
                f"{latest_doc_label} #{latest_payload.get('invoice_number', '')}"
            )
            latest_extra_note = ""
            if str(latest_payload.get("payment_status", "paid_full")).strip().lower() == "deposit_paid":
                latest_extra_note = (
                    "Deposit Due Now (50%): "
                    f"{latest_payload.get('currency', 'JM$')}{float(latest_payload.get('deposit_due_now', 0.0)):,.2f}"
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
        except Exception as exc:
            st.error(f"Quick actions unavailable for latest invoice: {exc}")
    else:
        st.caption("Save an invoice to unlock quick PDF/PNG download and quick send buttons.")

    st.markdown("---")
    st.markdown("**Attach Files (PNG/PDF) to Invoices**")
    inv = invoice_options()
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

    st.markdown("---")
    st.markdown("**Build Log (Quotes + Invoices)**")
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


def render_expenses() -> None:
    st.subheader("Expense Transactions")
    st.caption(
        "Record each expense individually. Use Expense Type to separate recurring monthly costs from transaction-level costs."
    )
    st.info("Supplier re-rental entries are now managed in the separate `Supplier Re-Rental` section.")

    category_options = [
        "Wages",
        "Petrol",
        "Bad Debt",
        "Unforeseen Expense",
        "Marketing",
        "Shopify",
        "Google Ads",
        "Facebook Ads",
        "Utilities",
        "Inventory Purchase",
        "Other",
    ]
    labels = invoice_label_map()
    options = list(labels.keys())

    with st.form("expense_form", clear_on_submit=True):
        a1, a2, a3, a4 = st.columns(4)
        expense_date = a1.date_input("Expense Date", value=date.today())
        amount = a2.number_input("Amount (JMD) *", min_value=0.0, step=100.0)
        category = a3.selectbox(
            "Category *",
            category_options,
        )
        expense_kind_label = a4.selectbox(
            "Expense Type *",
            [
                "Transaction (invoice/day level)",
                "Recurring Monthly (ChatGPT/Ads/Shopify)",
                "Summary Reference (roll-up only)",
            ],
        )

        b1, b2 = st.columns(2)
        vendor = b1.text_input("Vendor / Person")
        description = b2.text_input("Description")

        link_label = st.selectbox("Link to Invoice (optional)", options)
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
                add_expense(
                    expense_date=expense_date.isoformat(),
                    amount=float(amount),
                    category=category,
                    invoice_id=labels[link_label],
                    expense_kind=kind_map[expense_kind_label],
                    vendor=vendor,
                    description=description,
                )
                st.success("Expense recorded.")
            except Exception as exc:
                st.error(f"Could not add expense: {exc}")

    st.markdown("---")
    st.markdown("**Add Monthly Adjustment (e.g. new inventory purchase)**")
    with st.form("adjustment_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        month_value = c1.date_input("Month", value=date.today().replace(day=1))
        adjustment_type = c2.text_input("Adjustment Type", value="Inventory Purchase")
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
                st.success("Monthly adjustment recorded.")
            except Exception as exc:
                st.error(f"Could not add adjustment: {exc}")

    st.markdown("---")
    st.markdown("**Recent Expenses**")
    expenses = load_expenses()
    if expenses.empty:
        st.info("No expenses yet.")
        return

    shown = expenses.sort_values("expense_date", ascending=False).copy()
    shown["expense_date"] = shown["expense_date"].dt.date.astype("string")
    shown["amount"] = shown["amount"].map(money)
    st.dataframe(
        shown[
            [
                "id",
                "expense_date",
                "invoice_id",
                "category",
                "expense_kind",
                "vendor",
                "description",
                "amount",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")
    st.markdown("**Manage Expenses (Edit/Delete)**")
    expense_records = load_expenses().sort_values("expense_date", ascending=False).copy()
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
    reverse_invoice_labels = {value: key for key, value in labels.items()}
    selected_invoice_label = reverse_invoice_labels.get(current_invoice_id, "Not linked to invoice")
    invoice_default_index = (
        invoice_option_labels.index(selected_invoice_label)
        if selected_invoice_label in invoice_option_labels
        else 0
    )

    kind_option_labels = [
        "Transaction (invoice/day level)",
        "Recurring Monthly (ChatGPT/Ads/Shopify)",
        "Summary Reference (roll-up only)",
    ]
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
    kind_default_index = kind_option_labels.index(kind_default_label)

    current_category = str(selected_expense.get("category", "") or "").strip() or "Other"
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
            "Link to Invoice",
            options=invoice_option_labels,
            index=invoice_default_index,
        )

        save_edit = st.form_submit_button("Save Expense Changes")

    if save_edit:
        if edited_amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                update_expense(
                    expense_id=selected_expense_id,
                    expense_date=edited_date.isoformat(),
                    amount=float(edited_amount),
                    category=edited_category,
                    invoice_id=labels[edited_link_label],
                    expense_kind=kind_to_value[edited_kind_label],
                    vendor=edited_vendor,
                    description=edited_description,
                )
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
                st.success("Expense deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete expense: {exc}")


def render_supplier_rerental() -> None:
    st.subheader("Supplier Re-Rental")
    st.caption(
        "Staff can log supplier re-rental costs here. Entries automatically feed Finance Hub expenses and reports."
    )

    labels = invoice_label_map()
    options = list(labels.keys())

    with st.form("supplier_rerental_form", clear_on_submit=True):
        a1, a2, a3 = st.columns(3)
        expense_date = a1.date_input("Expense Date", value=date.today())
        vendor = a2.text_input("Supplier Name *", placeholder="Supplier / Vendor")
        amount = a3.number_input("Amount (JMD) *", min_value=0.0, step=100.0)

        b1, b2 = st.columns(2)
        link_label = b1.selectbox("Link to Invoice (optional)", options)
        description = b2.text_input(
            "Description",
            placeholder="Items or services supplied",
        )

        submit_rerental = st.form_submit_button("Add Supplier Re-Rental")

    if submit_rerental:
        if not vendor.strip():
            st.error("Supplier name is required.")
        elif amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                add_expense(
                    expense_date=expense_date.isoformat(),
                    amount=float(amount),
                    category="Re-Rental",
                    invoice_id=labels[link_label],
                    expense_kind="transaction",
                    vendor=vendor.strip(),
                    description=description.strip(),
                )
                st.success("Supplier re-rental expense recorded.")
            except Exception as exc:
                st.error(f"Could not add supplier re-rental expense: {exc}")

    st.markdown("---")
    month_key = f"{date.today().year:04d}-{date.today().month:02d}"
    expenses = load_expenses()
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
        st.dataframe(
            show[
                [
                    "expense_date",
                    "invoice_id",
                    "vendor",
                    "description",
                    "amount",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    with right:
        st.markdown("**Supplier Totals**")
        supplier_totals = load_supplier_expenses()
        if supplier_totals.empty:
            st.caption("No supplier totals yet.")
        else:
            supplier_view = supplier_totals.copy()
            supplier_view["amount"] = supplier_view["amount"].map(money)
            st.dataframe(
                supplier_view,
                hide_index=True,
                use_container_width=True,
            )


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


def render_inventory_training_arcade(stock: pd.DataFrame, live_status: pd.DataFrame) -> None:
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
        original_qty_by_id = {
            int(row["id"]): float(row["current_quantity"])
            for _, row in editor_source.iterrows()
            if pd.notna(row["id"])
        }
        source_ids = set(original_qty_by_id.keys())
        editor_source = editor_source.set_index("id", drop=True)
        editor_source.index.name = None
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

            for idx, row in edited_rows.iterrows():
                idx_token = str(idx).strip()
                row_id = int(idx_token) if idx_token.isdigit() else None
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
    st.subheader("Inventory")
    st.caption(
        "Track stock levels, rental pricing, and movement history with a simpler workflow."
    )
    st.caption(
        "Stock is updated automatically from `Confirmed Order - Confirmed` using each event's rental length "
        "(for example, a 24-hour rental reduces available stock during that window, then returns automatically)."
    )

    stock = load_inventory_snapshot()
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

    live_status = load_inventory_live_status(reference_time=availability_reference)

    with st.form("inventory_item_form", clear_on_submit=True):
        st.markdown("**Add / Update Inventory Item**")
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
        "and see Stock Quantity vs Available @ Check Time."
    )
    st.markdown("---")
    if stock.empty:
        st.info("No inventory items yet.")
        render_inventory_training_arcade(stock=stock, live_status=live_status)
        return

    render_inventory_training_arcade(stock=stock, live_status=live_status)


def render_reports(report_start_month: str) -> None:
    st.subheader("Reports")
    experience = current_experience_mode()
    monthly = apply_start_month(load_monthly_summary(), report_start_month)
    yearly = load_yearly_summary()
    products = load_product_profitability()
    supplier_totals = load_supplier_expenses()
    supplier_monthly = load_supplier_monthly_expenses()
    wages_recon = load_wages_reconciliation()
    expense_modes = load_monthly_expense_modes()
    invoices = load_invoice_level()

    if monthly.empty:
        st.info("No report data yet.")
        return

    if not yearly.empty:
        yearly = yearly[yearly["year"] >= int(report_start_month[:4])]
    if not supplier_monthly.empty:
        supplier_monthly = apply_start_month(supplier_monthly, report_start_month)
    if not wages_recon.empty:
        wages_recon = apply_start_month(wages_recon, report_start_month)
    if not expense_modes.empty:
        expense_modes = apply_start_month(expense_modes, report_start_month)

    year_values = sorted([int(y) for y in monthly["year"].dropna().unique()])
    selected_years = st.multiselect(
        "Filter Year(s)",
        options=year_values,
        default=year_values,
    )
    if selected_years:
        monthly = monthly[monthly["year"].isin(selected_years)]
        yearly = yearly[yearly["year"].isin(selected_years)]
        invoices = invoices[invoices["year"].isin(selected_years)]

    if experience != "Data Dense":
        st.markdown("**Visual Lab**")
        st.caption("Interactive visuals for fast comparison, trend reading, and decision support.")
        v1, v2 = st.columns([1.3, 1])
        with v1:
            viz_monthly = monthly.copy().sort_values("month")
            viz_monthly["profit_gap"] = (
                viz_monthly["revenue"] - viz_monthly["total_expenses"]
            )
            overview_fig = px.bar(
                viz_monthly,
                x="month_label",
                y=["revenue", "total_expenses", "net_profit_after_adjustments"],
                barmode="group",
                title="Monthly Performance Compare",
                labels={"value": "Amount (JMD)", "month_label": "Month", "variable": "Metric"},
            )
            style_plotly(overview_fig)
            st.plotly_chart(overview_fig, use_container_width=True)

        with v2:
            viz_monthly = monthly.copy().sort_values("month")
            viz_monthly["cumulative_profit"] = viz_monthly["net_profit_after_adjustments"].cumsum()
            cumulative = px.line(
                viz_monthly,
                x="month_label",
                y="cumulative_profit",
                markers=True,
                title="Year-to-Date Cumulative Profit",
                labels={"cumulative_profit": "Cumulative Profit (JMD)", "month_label": "Month"},
            )
            cumulative.update_traces(line={"color": PRIMARY_COLOR, "width": 4})
            style_plotly(cumulative)
            st.plotly_chart(cumulative, use_container_width=True)

        v3, v4 = st.columns([1.15, 1.15])
        with v3:
            if not expense_modes.empty:
                exp = expense_modes.copy().sort_values("month")
                exp_fig = px.area(
                    exp,
                    x="month_label",
                    y=[
                        "recurring_monthly",
                        "summarized_from_transactions",
                        "other_expenses_used",
                    ],
                    title="Expense Composition Over Time",
                    labels={"value": "Amount (JMD)", "month_label": "Month", "variable": "Expense Mode"},
                )
                style_plotly(exp_fig)
                st.plotly_chart(exp_fig, use_container_width=True)
            else:
                st.markdown(
                    "<div class='hint-card'>Expense composition chart appears once expense rows are available.</div>",
                    unsafe_allow_html=True,
                )

        with v4:
            if not invoices.empty:
                inv = invoices.dropna(subset=["net_profit"]).copy()
                inv["profit_band"] = inv["net_profit"].apply(
                    lambda x: "Profit" if float(x) >= 0 else "Loss"
                )
                invoice_fig = px.box(
                    inv,
                    x="year",
                    y="net_profit",
                    color="profit_band",
                    color_discrete_map={"Profit": "#2EAF7D", "Loss": "#E05D5D"},
                    points="all",
                    title="Invoice Profit Distribution",
                    labels={"year": "Year", "net_profit": "Invoice Net Profit (JMD)"},
                )
                style_plotly(invoice_fig)
                st.plotly_chart(invoice_fig, use_container_width=True)
            else:
                st.markdown(
                    "<div class='hint-card'>Invoice profit distribution appears when invoice-level data exists.</div>",
                    unsafe_allow_html=True,
                )

    st.markdown("**Monthly Summary**")
    monthly_show = monthly.copy()
    for col in [
        "revenue",
        "cash_collected",
        "outstanding_receivables",
        "item_cost",
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
    st.dataframe(
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
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("**Yearly Summary**")
    if yearly.empty:
        st.caption("No yearly rows yet.")
    else:
        yearly_show = yearly.copy()
        for col in [
            "revenue",
            "cash_collected",
            "outstanding_receivables",
            "item_cost",
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
        st.dataframe(yearly_show, hide_index=True, use_container_width=True)

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
                f"{len(pending_payments)} invoice(s) still have deposit/partial payment only."
            )
            st.dataframe(
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
                hide_index=True,
                use_container_width=True,
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
        st.dataframe(
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
            hide_index=True,
            use_container_width=True,
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
        st.dataframe(product_show, hide_index=True, use_container_width=True)

    st.markdown("**Supplier Expenses (Re-Rental)**")
    if supplier_totals.empty:
        st.caption("No supplier-level re-rental expenses yet.")
    else:
        supplier_show = supplier_totals.copy()
        supplier_show["amount"] = supplier_show["amount"].map(money)
        st.dataframe(supplier_show, hide_index=True, use_container_width=True)

    if not supplier_monthly.empty:
        supplier_filtered = supplier_monthly.copy()
        if selected_years:
            month_year = pd.PeriodIndex(supplier_filtered["month"], freq="M").year
            supplier_filtered = supplier_filtered[month_year.isin(selected_years)]

        top_suppliers = (
            supplier_totals.head(6)["vendor"].tolist()
            if not supplier_totals.empty
            else []
        )
        if top_suppliers:
            supplier_filtered = supplier_filtered[
                supplier_filtered["vendor"].isin(top_suppliers)
            ]

        if not supplier_filtered.empty:
            supplier_fig = px.bar(
                supplier_filtered,
                x="month_label",
                y="amount",
                color="vendor",
                barmode="stack",
                title="Monthly Supplier Spend (Top Re-Rental Suppliers)",
                labels={"month_label": "Month", "amount": "Amount (JMD)", "vendor": "Supplier"},
            )
            style_plotly(supplier_fig)
            st.plotly_chart(supplier_fig, use_container_width=True)

    st.markdown("**Wages Reconciliation (Person -> Invoice -> Month)**")
    if wages_recon.empty:
        st.caption("No wages records yet.")
    else:
        wages_filtered = wages_recon.copy()
        if selected_years:
            recon_year = pd.PeriodIndex(wages_filtered["month"], freq="M").year
            wages_filtered = wages_filtered[recon_year.isin(selected_years)]

        show = wages_filtered.copy()
        show = show.rename(
            columns={
                "wages_person_level": "Person-Level Wages",
                "wages_summary_topups": "Invoice Summary Top-Ups",
                "wages_monthly_sheet_rollup": "Monthly Sheet Rollup (should be 0)",
                "wages_total_used": "Total Wages Used",
            }
        )
        for col in [
            "Person-Level Wages",
            "Invoice Summary Top-Ups",
            "Monthly Sheet Rollup (should be 0)",
            "Total Wages Used",
        ]:
            show[col] = show[col].map(money)
        st.dataframe(
            show[
                [
                    "month_label",
                    "Person-Level Wages",
                    "Invoice Summary Top-Ups",
                    "Monthly Sheet Rollup (should be 0)",
                    "Total Wages Used",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("**Download Current Monthly Summary**")
    csv_data = monthly.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Monthly Summary CSV",
        data=csv_data,
        file_name="monthly_summary.csv",
        mime="text/csv",
    )


def render_importer() -> None:
    st.subheader("Import Legacy CSV Data")
    st.caption(
        "Loads your current sheets into the app database. "
        "Monthly rollup columns (like Re-Rental/Wages/Petrol) are imported as summary references (excluded from totals) to avoid double-counting. "
        "You can also import Shopify orders from Shopify's exported orders CSV."
    )

    if st.button("Run Legacy Cleanup (recommended once)", type="secondary"):
        try:
            cleanup = cleanup_legacy_double_counts()
            st.success("Cleanup complete.")
            st.json(cleanup)
        except Exception as exc:
            st.error(f"Cleanup failed: {exc}")

    paths = {}
    for name, default in DEFAULT_IMPORT_PATHS.items():
        paths[name] = st.text_input(name, value=default, key=f"path_{name}")

    if st.button("Run Full Import", type="primary"):
        missing = []
        for name, raw_path in paths.items():
            path = raw_path.strip()
            if name == "Shopify Orders CSV (optional)" and not path:
                continue
            if not path or not Path(path).exists():
                missing.append(name)
        if missing:
            st.error("Some files were not found:")
            for entry in missing:
                st.write(f"- {entry}: `{paths[entry]}`")
            return

        try:
            result = import_all(paths)
            st.success("Import completed.")
            st.json(result)
        except Exception as exc:
            st.error(f"Import failed: {exc}")

    st.markdown("---")
    st.markdown("**Data Backup**")
    if DB_PATH.exists():
        st.download_button(
            "Download Database Backup (.db)",
            data=DB_PATH.read_bytes(),
            file_name="finance_hub_backup.db",
            mime="application/octet-stream",
        )
    else:
        st.caption("Database file not created yet.")


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
    st.subheader("Client Retention Automation")
    st.caption(
        "Auto-queue post-event follow-ups (thank-you + review) and send quickly."
    )

    default_review = get_profile_setting("google_review_link", CLIENT_REVIEW_LINK_DEFAULT)
    default_delay = int(float(get_setting("retention.followup_delay_hours", "1") or 1))

    with st.expander("Retention Settings", expanded=False):
        s1, s2 = st.columns(2)
        review_link = s1.text_input(
            "Google Review Link",
            value=default_review,
            key="retention_review_link_input",
        )
        followup_delay_hours = int(
            s2.number_input(
                "Follow-up Delay After Event (hours)",
                min_value=0,
                max_value=168,
                value=max(0, default_delay),
                step=1,
                key="retention_followup_delay_input",
            )
        )
        if st.button("Save Retention Settings", key="save_retention_settings_btn"):
            set_profile_setting("google_review_link", review_link.strip() or CLIENT_REVIEW_LINK_DEFAULT)
            set_setting("retention.followup_delay_hours", str(int(followup_delay_hours)))
            st.success("Retention settings saved.")

    review_link = get_profile_setting("google_review_link", CLIENT_REVIEW_LINK_DEFAULT)
    followup_delay_hours = int(float(get_setting("retention.followup_delay_hours", str(default_delay)) or default_delay))

    events = build_event_schedule(load_event_calendar())
    if events.empty:
        st.info("No completed events available yet.")
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
    events["followup_due_at"] = events["event_end"].apply(
        lambda dt: dt.astimezone(tz_jm) + timedelta(hours=float(followup_delay_hours))
    )
    events["is_sent"] = events["invoice_id"].apply(
        lambda invoice_id: (int(invoice_id), "post_event_followup") in sent_pairs
    )
    events["is_due"] = events["followup_due_at"].apply(lambda dt: now_jm >= dt)
    events["queue_status"] = events.apply(
        lambda row: "Sent" if bool(row["is_sent"]) else ("Due Now" if bool(row["is_due"]) else "Upcoming"),
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
    k2.metric("Upcoming", int((events["queue_status"] == "Upcoming").sum()))
    k3.metric("Sent", int((events["queue_status"] == "Sent").sum()))

    st.success(
        f"Queue updated automatically from completed events using a {followup_delay_hours}-hour delay rule."
    )

    status_filter = st.selectbox(
        "Filter Queue",
        options=["Due Now", "Upcoming", "Sent", "All"],
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

    queue = queue.sort_values(["queue_status", "followup_due_at", "event_end"], ascending=[True, True, False])

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
    for _, row in queue.head(120).iterrows():
        customer_name = str(row.get("customer_name", "") or "").strip()
        invoice_number = str(row.get("invoice_number", "") or "").strip()
        due_label = row["followup_due_at"].astimezone(tz_jm).strftime("%Y-%m-%d %I:%M %p")
        queue_status = str(row.get("queue_status", "Upcoming"))
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
                f"Event ended: {row['event_end'].astimezone(tz_jm).strftime('%Y-%m-%d %I:%M %p')} | "
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
    st.subheader("Deposit Due Tracker")
    st.caption(
        "Track deposit/partial-payment balances, due dates, and overdue follow-up actions."
    )

    if not can_view_finance_data():
        st.warning("Finance Hub is locked. Unlock Finance Hub to access deposit due tracker details.")
        return

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

    invoice_level = apply_start_month(load_invoice_level(), report_start_month)
    if invoice_level.empty:
        st.info("No confirmed invoice payment records yet.")
        return

    tracker = invoice_level[invoice_level["amount_outstanding"] > 0.01].copy()
    if tracker.empty:
        st.success("No outstanding balances. All confirmed orders are paid in full.")
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
        show["revenue"] = show["revenue"].map(money)
        show["amount_paid"] = show["amount_paid"].map(money)
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
                    "revenue",
                    "amount_paid",
                    "amount_outstanding",
                    "payment_status",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown("**Update Deposit / Balance**")
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

    with st.form("deposit_tracker_update_form", clear_on_submit=True):
        action = st.radio(
            "Action",
            options=["Add Payment", "Mark Paid Full"],
            horizontal=True,
            key="deposit_tracker_action",
        )
        additional_payment = st.number_input(
            "Payment Amount (JMD)",
            min_value=0.0,
            step=100.0,
            value=float(selected_row["amount_outstanding"]) if action == "Mark Paid Full" else 0.0,
            disabled=(action == "Mark Paid Full"),
            key="deposit_tracker_add_payment_amount",
        )
        payment_note = st.text_input(
            "Payment Note (optional)",
            value="",
            key="deposit_tracker_payment_note",
        )
        submit_payment = st.form_submit_button("Apply Payment Update")

    if submit_payment:
        try:
            current_paid = float(selected_row["amount_paid"])
            revenue_total = float(selected_row["revenue"])
            if action == "Mark Paid Full":
                new_paid = revenue_total
            else:
                if float(additional_payment) <= 0:
                    st.error("Enter a payment amount greater than 0.")
                    return
                new_paid = min(revenue_total, current_paid + float(additional_payment))
            new_status = "paid_full" if new_paid >= revenue_total - 0.01 else "deposit_paid"
            set_invoice_payment_status(
                invoice_id=selected_id,
                payment_status=new_status,
                amount_paid=float(new_paid),
                payment_notes=payment_note.strip(),
            )
            st.success("Payment status updated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not update payment: {exc}")


def render_mobile_and_team() -> None:
    st.subheader("Mobile & Team")
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


def main() -> None:
    init_db()
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
    enforce_mobile_entry_layout()

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
        current_avatar = wattbot_avatar_data_uri()
        st.markdown("**WattBot Avatar (optional)**")
        if current_avatar:
            st.markdown(
                f"<img src='{current_avatar}' width='72' height='72' style='border-radius:50%;object-fit:cover;border:2px solid #a7eaff;'/>",
                unsafe_allow_html=True,
            )
        wattbot_avatar_file = st.file_uploader(
            "Upload WattBot icon image",
            type=["png", "jpg", "jpeg", "webp"],
            key="profile_wattbot_avatar_upload",
            help="This image is used for the bottom-right WattBot widget.",
        )
        avatar_col1, avatar_col2 = st.columns(2)
        if avatar_col1.button("Save WattBot Avatar", key="save_wattbot_avatar_btn"):
            if wattbot_avatar_file is None:
                st.error("Upload an image first.")
            else:
                raw = wattbot_avatar_file.getvalue()
                mime = wattbot_avatar_file.type or "image/jpeg"
                set_profile_setting("wattbot_avatar_data_uri", bytes_to_data_uri(raw, mime))
                try:
                    WATTBOT_AVATAR_PATH.write_bytes(raw)
                except Exception:
                    pass
                st.success("WattBot avatar updated.")
                st.rerun()
        if avatar_col2.button("Reset Avatar", key="reset_wattbot_avatar_btn"):
            set_profile_setting("wattbot_avatar_data_uri", "")
            st.success("WattBot avatar reset to default.")
            st.rerun()
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
    report_start_date = st.sidebar.date_input(
        "Reporting Start Month",
        value=date(2026, 1, 1),
        help="Set this to January 2026 to track your full 2026 year cleanly.",
    )
    alert_window_days = 14
    report_start_month = f"{report_start_date.year:04d}-{report_start_date.month:02d}"
    st.sidebar.caption(f"Active theme: {active_theme.title()}")

    st.session_state["nav_mode_selector"] = "Sidebar Menu (Mobile Friendly)"
    sections = [
        "Finance Hub",
        "Build Invoice",
        "Client Retention Automation",
        "Deposit Due Tracker",
        "Supplier Re-Rental",
        "Inventory",
        "Import Legacy Data",
        "Mobile & Team",
    ]
    if st.session_state.get("nav_active_section") not in sections:
        st.session_state["nav_active_section"] = sections[0]
    if st.session_state.get("nav_sidebar_section") not in sections:
        st.session_state["nav_sidebar_section"] = st.session_state["nav_active_section"]
    if st.session_state.get("nav_quick_section") not in sections:
        st.session_state["nav_quick_section"] = st.session_state["nav_active_section"]

    current_active = str(st.session_state.get("nav_active_section", sections[0]))
    if current_active not in sections:
        current_active = sections[0]
        st.session_state["nav_active_section"] = current_active

    last_synced = str(st.session_state.get("nav_last_synced_active", ""))
    if last_synced != current_active:
        st.session_state["nav_sidebar_section"] = current_active
        st.session_state["nav_quick_section"] = current_active
        st.session_state["nav_last_synced_active"] = current_active

    def _on_sidebar_change() -> None:
        selected = str(st.session_state.get("nav_sidebar_section", sections[0]))
        if selected not in sections:
            return
        st.session_state["nav_active_section"] = selected
        st.session_state["nav_quick_section"] = selected
        st.session_state["nav_last_synced_active"] = selected

    def _on_quick_change() -> None:
        selected = str(st.session_state.get("nav_quick_section", sections[0]))
        if selected not in sections:
            return
        st.session_state["nav_active_section"] = selected
        st.session_state["nav_sidebar_section"] = selected
        st.session_state["nav_last_synced_active"] = selected

    render_wattbot_panel(
        available_sections=sections,
        report_start_month=report_start_month,
        alert_window_days=alert_window_days,
    )

    active_section = st.sidebar.selectbox(
        "Go to Section",
        options=sections,
        key="nav_sidebar_section",
        on_change=_on_sidebar_change,
    )
    st.caption("Quick switch works in both sidebar and in-page selector (mobile + desktop).")
    st.selectbox(
        f"Section (Current: {active_section})",
        options=sections,
        key="nav_quick_section",
        on_change=_on_quick_change,
    )

    active_section = st.session_state.get("nav_active_section", sections[0])
    if active_section == "Finance Hub":
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
    elif active_section == "Import Legacy Data":
        render_importer()
    elif active_section == "Mobile & Team":
        render_mobile_and_team()


if __name__ == "__main__":
    main()
