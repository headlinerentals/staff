from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd
from pypdf import PdfReader


def _money(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    if cleaned in {"", "-", "."}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> date | None:
    raw = (text or "").strip()
    if not raw:
        return None

    for dayfirst in (False, True):
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=dayfirst)
        if not pd.isna(parsed):
            return parsed.date()
    return None


def _clean_lines(text: str) -> list[str]:
    raw_lines = text.replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw_lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return lines


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _extract_invoice_number(text: str) -> str:
    patterns = [
        r"INVOICE\s*#\s*([A-Za-z0-9-]+)",
        r"Invoice\s*#\s*([A-Za-z0-9-]+)",
        r"Price\s*Quote\s*#\s*([A-Za-z0-9-]+)",
        r"Quote\s*#\s*([A-Za-z0-9-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_event_date(text: str) -> date | None:
    patterns = [
        r"Event Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Event Date:\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        r"Event Date\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Event Date\s+([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = _parse_date(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _extract_event_time(text: str) -> str:
    patterns = [
        r"Event Time:\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm))",
        r"Event Time:\s*([0-9]{1,2}:[0-9]{2})",
        r"Event Time\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm))",
        r"Event Time\s+([0-9]{1,2}:[0-9]{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = pd.to_datetime(match.group(1).strip(), errors="coerce")
        if pd.isna(parsed):
            continue
        return f"{parsed.hour:02d}:{parsed.minute:02d}"
    return "11:00"


def _extract_customer(text: str) -> str:
    customer_match = re.search(
        r"Customer\s*(.*?)\s*Seller",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not customer_match:
        return ""
    segment = customer_match.group(1)
    lines = _clean_lines(segment)
    if not lines:
        return ""
    return ", ".join(lines)


def _extract_total(text: str) -> float:
    patterns = [
        r"Total Price\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Total\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _money(match.group(1))
            if value > 0:
                return value
    return 0.0


def _extract_items(text: str) -> pd.DataFrame:
    lines = _clean_lines(text)
    in_items_section = False
    records: list[dict] = []

    detailed_row = re.compile(
        r"^(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>\d[\d,]*(?:\.\d+)?)\s+(?P<total>\d[\d,]*(?:\.\d+)?)$"
    )
    simple_service = re.compile(
        r"^(?P<desc>[A-Za-z][A-Za-z0-9 &()\/-]+?)\s+(?P<total>\d[\d,]*(?:\.\d+)?)$"
    )

    for line in lines:
        header_hit = (
            "Description" in line
            and "Quantity" in line
            and "Unit Price" in line
        )
        if header_hit:
            in_items_section = True
            continue

        if not in_items_section:
            continue

        if "Total Price" in line:
            break

        if line.startswith("-"):
            continue

        detailed = detailed_row.match(line)
        if detailed:
            desc = detailed.group("desc").strip()
            qty = float(detailed.group("qty"))
            unit = _money(detailed.group("unit"))
            total = _money(detailed.group("total"))
            if qty <= 0:
                continue

            if unit <= 0 and total > 0:
                unit = total / qty

            records.append(
                {
                    "item_name": desc,
                    "item_type": "product",
                    "quantity": qty,
                    "unit_price": unit,
                    "unit_cost": 0.0,
                }
            )
            continue

        service = simple_service.match(line)
        if service:
            desc = service.group("desc").strip()
            total = _money(service.group("total"))
            lowered = desc.lower()
            if total > 0 and (
                "delivery" in lowered
                or "setup" in lowered
                or "set-up" in lowered
                or "collection" in lowered
                or "transport" in lowered
                or "service" in lowered
            ):
                records.append(
                    {
                        "item_name": desc,
                        "item_type": "service",
                        "quantity": 1.0,
                        "unit_price": total,
                        "unit_cost": 0.0,
                    }
                )

    return pd.DataFrame(records)


def parse_invoice_pdf(pdf_bytes: bytes, source_name: str = "uploaded.pdf") -> dict:
    text = _extract_text_from_pdf(pdf_bytes)
    normalized_text = text.replace("\xa0", " ")

    invoice_number = _extract_invoice_number(normalized_text)
    event_date = _extract_event_date(normalized_text)
    event_time = _extract_event_time(normalized_text)
    customer_name = _extract_customer(normalized_text)
    extracted_total = _extract_total(normalized_text)
    items = _extract_items(normalized_text)

    warnings: list[str] = []
    if not invoice_number:
        warnings.append("Invoice number was not detected. Enter it manually.")
    if event_date is None:
        warnings.append("Event date was not detected. Set it manually.")
    if items.empty and extracted_total > 0:
        warnings.append(
            "Line items were not detected. Added a fallback single service item using the total."
        )
        items = pd.DataFrame(
            [
                {
                    "item_name": "Imported Total (manual split needed)",
                    "item_type": "service",
                    "quantity": 1.0,
                    "unit_price": extracted_total,
                    "unit_cost": 0.0,
                }
            ]
        )
    elif items.empty:
        warnings.append("No line items were detected.")

    calculated_total = (
        float((items["quantity"] * items["unit_price"]).sum()) if not items.empty else 0.0
    )
    if extracted_total > 0 and calculated_total > 0:
        delta = abs(extracted_total - calculated_total)
        if delta > 1:
            warnings.append(
                f"Detected total ({extracted_total:,.2f}) does not match line-item total ({calculated_total:,.2f})."
            )

    notes = f"Imported from PDF: {source_name}"
    return {
        "invoice_number": invoice_number,
        "event_date": event_date,
        "event_time": event_time,
        "rental_hours": 24,
        "event_timezone": "America/Jamaica",
        "event_location": "",
        "customer_name": customer_name,
        "customer_phone": "",
        "customer_email": "",
        "contact_detail": "",
        "delivered_to": "",
        "paid_to": "",
        "notes": notes,
        "items": items,
        "detected_total": extracted_total,
        "calculated_total": calculated_total,
        "warnings": warnings,
    }
