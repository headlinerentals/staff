from __future__ import annotations

import base64
import io
import json
import re
import zlib
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


def _coerce_float(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


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


def _extract_text_via_ocr(pdf_bytes: bytes, max_pages: int = 6) -> tuple[str, str]:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return ("", "OCR fallback unavailable: install `pypdfium2`.")

    try:
        import pytesseract
    except Exception:
        return ("", "OCR fallback unavailable: install `pytesseract`.")

    try:
        document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    except Exception as exc:
        return ("", f"OCR fallback could not open PDF: {exc}")

    texts: list[str] = []
    page_count = min(len(document), max_pages)
    for page_index in range(page_count):
        try:
            page = document.get_page(page_index)
            bitmap = page.render(scale=2.0, rotation=0)
            image = bitmap.to_pil()
            ocr_text = pytesseract.image_to_string(image) or ""
            if ocr_text.strip():
                texts.append(ocr_text)
            page.close()
        except Exception:
            continue
    return ("\n".join(texts), "")


def _extract_embedded_payload(pdf_bytes: bytes) -> dict | None:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        metadata = reader.metadata or {}
        raw_payload = ""
        for key in ("/HeadlinePayload", "HeadlinePayload", "/HRPayload", "HRPayload"):
            value = metadata.get(key) if hasattr(metadata, "get") else None
            if value:
                raw_payload = str(value).strip()
                break
        if not raw_payload:
            return None

        encoding = (
            str(metadata.get("/HeadlinePayloadEncoding", "")).strip().lower()
            if hasattr(metadata, "get")
            else ""
        )
        if encoding == "zlib+base64+json":
            decoded = zlib.decompress(base64.b64decode(raw_payload)).decode("utf-8")
        else:
            try:
                decoded = zlib.decompress(base64.b64decode(raw_payload)).decode("utf-8")
            except Exception:
                decoded = raw_payload
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


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


def _extract_customer_phone(text: str) -> str:
    match = re.search(r"Phone:\s*([0-9+()\- ]{7,})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_customer_email(text: str) -> str:
    match = re.search(r"Email:\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_event_location(text: str) -> str:
    match = re.search(r"Location:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value


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


def _items_from_payload(raw_items: object) -> pd.DataFrame:
    if isinstance(raw_items, pd.DataFrame):
        items = raw_items.copy()
    elif isinstance(raw_items, list):
        items = pd.DataFrame(raw_items)
    else:
        items = pd.DataFrame()
    if items.empty:
        return pd.DataFrame(columns=["item_name", "item_type", "quantity", "unit_price", "unit_cost"])

    for col in ("item_name", "item_type"):
        if col not in items.columns:
            items[col] = ""
    for col in ("quantity", "unit_price", "line_total"):
        if col not in items.columns:
            items[col] = 0.0

    items["item_name"] = items["item_name"].astype(str).str.strip()
    items["item_type"] = (
        items["item_type"].astype(str).str.strip().str.lower().replace("", "product")
    )
    items["item_type"] = items["item_type"].where(
        items["item_type"].isin(["product", "service"]), "product"
    )
    items["quantity"] = pd.to_numeric(items["quantity"], errors="coerce").fillna(0.0)
    items["unit_price"] = pd.to_numeric(items["unit_price"], errors="coerce").fillna(0.0)
    items["line_total"] = pd.to_numeric(items["line_total"], errors="coerce").fillna(0.0)
    missing_price = (items["unit_price"] <= 0) & (items["line_total"] > 0) & (items["quantity"] > 0)
    items.loc[missing_price, "unit_price"] = (
        items.loc[missing_price, "line_total"] / items.loc[missing_price, "quantity"]
    )
    items = items[items["item_name"] != ""].copy()
    items["quantity"] = items["quantity"].where(items["quantity"] > 0, 1.0)
    items["unit_cost"] = 0.0
    return items[["item_name", "item_type", "quantity", "unit_price", "unit_cost"]]


def _parse_from_embedded_payload(payload: dict, source_name: str) -> dict:
    invoice_number = str(payload.get("invoice_number", "") or "").strip()
    event_date = _parse_date(str(payload.get("event_date", "") or ""))
    event_time_raw = str(payload.get("event_time", "") or "11:00").strip()
    event_time_parsed = pd.to_datetime(event_time_raw, errors="coerce")
    event_time = (
        f"{event_time_parsed.hour:02d}:{event_time_parsed.minute:02d}"
        if not pd.isna(event_time_parsed)
        else "11:00"
    )
    rental_hours = _coerce_float(payload.get("rental_hours", 24), default=24.0)
    items = _items_from_payload(payload.get("items"))
    calculated_total = (
        float((items["quantity"] * items["unit_price"]).sum()) if not items.empty else 0.0
    )
    detected_total = _coerce_float(payload.get("total", 0.0), default=0.0)
    if detected_total <= 0 and calculated_total > 0:
        detected_total = calculated_total

    warnings: list[str] = []
    if not invoice_number:
        warnings.append("Invoice number was not detected. Enter it manually.")
    if event_date is None:
        warnings.append("Event date was not detected. Set it manually.")
    if items.empty:
        warnings.append("No line items were detected.")
    if detected_total > 0 and calculated_total > 0 and abs(detected_total - calculated_total) > 1:
        warnings.append(
            f"Detected total ({detected_total:,.2f}) does not match line-item total ({calculated_total:,.2f})."
        )
    warnings.append(f"Imported from embedded PDF data: {source_name}")

    notes_raw = str(payload.get("notes", "") or "").strip()
    notes = f"{notes_raw} | Imported from PDF: {source_name}" if notes_raw else f"Imported from PDF: {source_name}"
    return {
        "invoice_number": invoice_number,
        "event_date": event_date,
        "event_time": event_time,
        "rental_hours": rental_hours if rental_hours > 0 else 24,
        "event_timezone": "America/Jamaica",
        "event_location": str(payload.get("event_location", "") or "").strip(),
        "customer_name": str(payload.get("customer_name", "") or "").strip(),
        "customer_phone": str(payload.get("customer_phone", "") or "").strip(),
        "customer_email": str(payload.get("customer_email", "") or "").strip(),
        "contact_detail": "",
        "delivered_to": str(payload.get("delivered_to", "") or "").strip(),
        "paid_to": str(payload.get("paid_to", "") or "").strip(),
        "notes": notes,
        "items": items,
        "detected_total": detected_total,
        "calculated_total": calculated_total,
        "warnings": warnings,
    }


def parse_invoice_pdf(pdf_bytes: bytes, source_name: str = "uploaded.pdf") -> dict:
    embedded_payload = _extract_embedded_payload(pdf_bytes)
    if embedded_payload:
        return _parse_from_embedded_payload(embedded_payload, source_name)

    text = _extract_text_from_pdf(pdf_bytes)
    ocr_used = False
    ocr_warning = ""
    if len(re.sub(r"\s+", "", text or "")) < 80:
        ocr_text, ocr_error = _extract_text_via_ocr(pdf_bytes)
        if ocr_text.strip():
            text = f"{text}\n{ocr_text}".strip()
            ocr_used = True
        elif ocr_error:
            ocr_warning = ocr_error
    normalized_text = text.replace("\xa0", " ")

    invoice_number = _extract_invoice_number(normalized_text)
    event_date = _extract_event_date(normalized_text)
    event_time = _extract_event_time(normalized_text)
    event_location = _extract_event_location(normalized_text)
    customer_name = _extract_customer(normalized_text)
    customer_phone = _extract_customer_phone(normalized_text)
    customer_email = _extract_customer_email(normalized_text)
    extracted_total = _extract_total(normalized_text)
    items = _extract_items(normalized_text)

    warnings: list[str] = []
    if ocr_used:
        warnings.append("OCR fallback was used for this PDF (scanned/image content detected).")
    if ocr_warning:
        warnings.append(ocr_warning)
    if not normalized_text.strip():
        warnings.append(
            "No readable text was found. This looks like an image/scanned PDF. Quick Intake works best with app-generated PDFs or OCR-ready text PDFs."
        )
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
        "event_location": event_location,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "contact_detail": "",
        "delivered_to": "",
        "paid_to": "",
        "notes": notes,
        "items": items,
        "detected_total": extracted_total,
        "calculated_total": calculated_total,
        "warnings": warnings,
    }
