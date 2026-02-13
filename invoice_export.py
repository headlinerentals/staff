from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pandas as pd


PRIMARY_COLOR = "#5927e5"
SECONDARY_COLOR = "#a7eaff"
TEXT_COLOR = "#111111"


def money(value: float, currency: str = "JM$") -> str:
    return f"{currency}{float(value):,.2f}"


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text if text.lower() not in {"nan", "none"} else ""


def build_invoice_payload(
    header: dict,
    items: pd.DataFrame,
    business_name: str,
    currency: str = "JM$",
    bank_info: dict | None = None,
) -> dict:
    rows = items.copy()
    if rows.empty:
        rows = pd.DataFrame(
            columns=["item_name", "item_type", "quantity", "unit_price", "line_total"]
        )

    if "line_total" not in rows.columns:
        rows["line_total"] = (
            pd.to_numeric(rows.get("quantity", 0), errors="coerce").fillna(0.0)
            * pd.to_numeric(rows.get("unit_price", 0), errors="coerce").fillna(0.0)
        )
    rows["quantity"] = pd.to_numeric(rows.get("quantity", 0), errors="coerce").fillna(0.0)
    rows["unit_price"] = pd.to_numeric(rows.get("unit_price", 0), errors="coerce").fillna(0.0)
    rows["line_total"] = pd.to_numeric(rows.get("line_total", 0), errors="coerce").fillna(0.0)

    total = float(rows["line_total"].sum())
    payment_status = _safe_text(header.get("payment_status") or "paid_full").lower()
    if payment_status not in {"unpaid", "deposit_paid", "paid_full"}:
        payment_status = "paid_full"
    amount_paid_raw = pd.to_numeric(header.get("amount_paid", 0), errors="coerce")
    amount_paid = float(0.0 if pd.isna(amount_paid_raw) else amount_paid_raw)
    amount_paid = max(0.0, amount_paid)
    if payment_status == "deposit_paid":
        deposit_due_now = round(total * 0.5, 2)
        balance_due_later = round(max(total - deposit_due_now, 0.0), 2)
    else:
        deposit_due_now = 0.0
        balance_due_later = round(max(total - amount_paid, 0.0), 2)
    bank_defaults = {
        "seller_name": "Headline Event Rentals",
        "seller_address_1": "61 West Main Drive",
        "seller_address_2": "Kingston",
        "bank_account_name": "Headline Event Rentals",
        "bank_account_type": "Scotia Savings Account (JM$)",
        "bank_branch": "HWT",
        "bank_account_number": "909039",
    }
    merged_bank = {**bank_defaults, **(bank_info or {})}
    return {
        "business_name": _safe_text(business_name) or "Headline Rentals",
        "invoice_number": _safe_text(header.get("invoice_number")),
        "document_type": _safe_text(header.get("document_type") or "invoice").lower(),
        "order_status": _safe_text(header.get("order_status") or "confirmed").lower(),
        "event_date": _safe_text(header.get("event_date")),
        "event_time": _safe_text(header.get("event_time")),
        "rental_hours": float(header.get("rental_hours") or 24),
        "event_location": _safe_text(header.get("event_location")),
        "customer_name": _safe_text(header.get("customer_name")),
        "customer_phone": _safe_text(header.get("customer_phone")),
        "customer_email": _safe_text(header.get("customer_email")),
        "delivered_to": _safe_text(header.get("delivered_to")),
        "paid_to": _safe_text(header.get("paid_to")),
        "payment_status": payment_status,
        "amount_paid": amount_paid,
        "payment_notes": _safe_text(header.get("payment_notes")),
        "notes": _safe_text(header.get("notes")),
        "items": rows[["item_name", "item_type", "quantity", "unit_price", "line_total"]].copy(),
        "total": total,
        "deposit_due_now": deposit_due_now,
        "balance_due_later": balance_due_later,
        "currency": currency or "JM$",
        "seller_name": _safe_text(merged_bank.get("seller_name")),
        "seller_address_1": _safe_text(merged_bank.get("seller_address_1")),
        "seller_address_2": _safe_text(merged_bank.get("seller_address_2")),
        "bank_account_name": _safe_text(merged_bank.get("bank_account_name")),
        "bank_account_type": _safe_text(merged_bank.get("bank_account_type")),
        "bank_branch": _safe_text(merged_bank.get("bank_branch")),
        "bank_account_number": _safe_text(merged_bank.get("bank_account_number")),
    }


def _build_invoice_image(payload: dict, logo_path: str | Path | None = None):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Invoice export needs Pillow. Install it with `pip install pillow`.") from exc

    rows = max(len(payload["items"]), 1)
    height = max(1520, 980 + rows * 48)
    width = 1600

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    def font(size: int, bold: bool = False):
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    f_title = font(58, bold=True)
    f_h1 = font(42, bold=True)
    f_h2 = font(30, bold=True)
    f_body = font(26)
    f_small = font(22)

    draw.rectangle((0, 0, width, 18), fill=PRIMARY_COLOR)

    if logo_path:
        path = Path(logo_path)
        if path.exists():
            try:
                logo = Image.open(path).convert("RGBA")
                logo.thumbnail((170, 170))
                image.paste(logo, (56, 40), mask=logo)
            except Exception:
                pass

    doc_type = str(payload.get("document_type", "invoice")).strip().lower()
    status = str(payload.get("order_status", "confirmed")).strip().lower()
    doc_title = "PRICE QUOTE" if doc_type == "quote" else "INVOICE"
    draw.text((245, 58), payload["business_name"], fill=TEXT_COLOR, font=f_h1)
    draw.text((width - 520, 58), doc_title, fill=PRIMARY_COLOR, font=f_title)
    draw.text((width - 440, 140), f"#{payload['invoice_number']}", fill=TEXT_COLOR, font=f_h2)
    draw.text((width - 440, 188), f"Status: {status.upper()}", fill=TEXT_COLOR, font=f_small)

    draw.text((56, 240), "Customer", fill=PRIMARY_COLOR, font=f_h2)
    draw.text((840, 240), "Event", fill=PRIMARY_COLOR, font=f_h2)

    y_left = 294
    for line in [
        payload["customer_name"],
        f"Phone: {payload['customer_phone']}" if payload["customer_phone"] else "",
        f"Email: {payload['customer_email']}" if payload["customer_email"] else "",
    ]:
        if line:
            for wrapped in textwrap.wrap(line, width=48)[:2]:
                draw.text((56, y_left), wrapped, fill=TEXT_COLOR, font=f_body)
                y_left += 38

    y_right = 294
    for line in [
        f"Date: {payload['event_date']}",
        f"Time: {payload['event_time']}",
        f"Duration: {payload['rental_hours']:g}h",
        f"Location: {(payload['event_location'] or payload['delivered_to'])}",
    ]:
        for wrapped in textwrap.wrap(line, width=44)[:2]:
            draw.text((840, y_right), wrapped, fill=TEXT_COLOR, font=f_body)
            y_right += 38

    seller_block_top = max(y_left, y_right) + 24
    seller_lines = [
        f"Seller: {payload.get('seller_name', '')}",
        payload.get("seller_address_1", ""),
        payload.get("seller_address_2", ""),
        "",
        "Banking Info:",
        f"Name: {payload.get('bank_account_name', '')}",
        payload.get("bank_account_type", ""),
        f"Branch: {payload.get('bank_branch', '')}",
        (
            f"Account #{payload.get('bank_account_number', '')}"
            if payload.get("bank_account_number", "")
            else ""
        ),
    ]
    seller_lines = [str(line).strip() for line in seller_lines if str(line).strip()]
    block_height = 28 + (len(seller_lines) * 34)
    draw.rounded_rectangle(
        (44, seller_block_top, width - 44, seller_block_top + block_height),
        radius=12,
        outline=SECONDARY_COLOR,
        width=3,
        fill="#F8FBFF",
    )
    seller_y = seller_block_top + 16
    for idx, line in enumerate(seller_lines):
        line_font = f_h2 if idx in {0, 4} else f_body
        draw.text((64, seller_y), line, fill=TEXT_COLOR, font=line_font)
        seller_y += 34

    table_top = seller_block_top + block_height + 30
    draw.rectangle((44, table_top, width - 44, table_top + 56), fill=SECONDARY_COLOR)
    draw.text((64, table_top + 12), "Description", fill=TEXT_COLOR, font=f_h2)
    draw.text((880, table_top + 12), "Qty", fill=TEXT_COLOR, font=f_h2)
    draw.text((1020, table_top + 12), f"Unit ({payload['currency']})", fill=TEXT_COLOR, font=f_h2)
    draw.text((1310, table_top + 12), f"Total ({payload['currency']})", fill=TEXT_COLOR, font=f_h2)

    y = table_top + 70
    if payload["items"].empty:
        draw.text((64, y), "No line items", fill=TEXT_COLOR, font=f_body)
        y += 38

    for _, row in payload["items"].iterrows():
        item_name = _safe_text(row.get("item_name"))
        qty = float(row.get("quantity") or 0.0)
        unit = float(row.get("unit_price") or 0.0)
        line_total = float(row.get("line_total") or 0.0)

        wrapped = textwrap.wrap(item_name, width=54) or [item_name]
        draw.text((64, y), wrapped[0], fill=TEXT_COLOR, font=f_body)
        draw.text((890, y), f"{qty:g}", fill=TEXT_COLOR, font=f_body)
        draw.text((1044, y), f"{unit:,.2f}", fill=TEXT_COLOR, font=f_body)
        draw.text((1338, y), f"{line_total:,.2f}", fill=TEXT_COLOR, font=f_body)
        y += 38

        for extra in wrapped[1:3]:
            draw.text((78, y), extra, fill=TEXT_COLOR, font=f_small)
            y += 30
        y += 6

    y += 20
    draw.line((940, y, width - 56, y), fill=PRIMARY_COLOR, width=3)
    y += 24
    draw.text(
        (940, y),
        f"Total Cost: {money(payload['total'], payload['currency'])}",
        fill=PRIMARY_COLOR,
        font=f_h1,
    )

    payment_status = str(payload.get("payment_status", "paid_full")).strip().lower()
    if payment_status == "deposit_paid":
        y += 58
        draw.text(
            (940, y),
            f"Deposit Due Now (50%): {money(payload.get('deposit_due_now', 0.0), payload['currency'])}",
            fill=TEXT_COLOR,
            font=f_h2,
        )
    elif payment_status == "unpaid":
        y += 58
        draw.text(
            (940, y),
            f"Amount Due Now: {money(payload['total'], payload['currency'])}",
            fill=TEXT_COLOR,
            font=f_h2,
        )
    else:
        y += 58
        draw.text(
            (940, y),
            f"Amount Paid: {money(payload.get('amount_paid', payload['total']), payload['currency'])}",
            fill=TEXT_COLOR,
            font=f_h2,
        )

    y += 72
    note = payload["notes"] or "Thank you for choosing Headline Rentals."
    for wrapped in textwrap.wrap(f"Notes: {note}", width=98)[:3]:
        draw.text((56, y), wrapped, fill=TEXT_COLOR, font=f_small)
        y += 30

    return image


def render_invoice_pdf(payload: dict, logo_path: str | Path | None = None) -> bytes:
    image = _build_invoice_image(payload, logo_path=logo_path)
    out = io.BytesIO()
    image.save(out, format="PDF", resolution=180.0)
    out.seek(0)
    return out.getvalue()


def render_invoice_png(payload: dict, logo_path: str | Path | None = None) -> bytes:
    image = _build_invoice_image(payload, logo_path=logo_path)
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()
