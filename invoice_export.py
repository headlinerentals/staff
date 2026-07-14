from __future__ import annotations

import base64
import io
import json
import textwrap
import zlib
from pathlib import Path

import pandas as pd
from pypdf import PdfReader, PdfWriter


PRIMARY_COLOR = "#5927e5"
SECONDARY_COLOR = "#a7eaff"
TEXT_COLOR = "#111111"
_FONT_CANDIDATE_CACHE: dict[str, list[str]] = {}


def money(value: float, currency: str = "JM$") -> str:
    return f"{currency}{float(value):,.2f}"


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text if text.lower() not in {"nan", "none"} else ""


def _normalize_document_type(value: object) -> str:
    raw = _safe_text(value).lower().replace("_", " ").replace("-", " ")
    if not raw:
        return "invoice"
    if "quote" in raw:
        return "quote"
    return "invoice"


def _customer_item_label(value: object) -> str:
    label = _safe_text(value)
    normalized = label.lower().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized in {"delivery", "delivery fee"}:
        return "Delivery and Collection"
    if normalized in {"setup", "set up", "set up fee", "setup fee"}:
        return "Setup and Breakdown"
    return label


def _payload_metadata_blob(payload: dict) -> str:
    items = payload.get("items")
    if isinstance(items, pd.DataFrame):
        item_rows = items.copy()
    else:
        item_rows = pd.DataFrame(items if isinstance(items, list) else [])
    if item_rows.empty:
        item_rows = pd.DataFrame(columns=["item_name", "item_type", "quantity", "unit_price", "line_total"])
    for col in ("item_name", "item_type"):
        if col not in item_rows.columns:
            item_rows[col] = ""
    for col in ("quantity", "unit_price", "line_total"):
        if col not in item_rows.columns:
            item_rows[col] = 0.0
    item_rows["quantity"] = pd.to_numeric(item_rows["quantity"], errors="coerce").fillna(0.0)
    item_rows["unit_price"] = pd.to_numeric(item_rows["unit_price"], errors="coerce").fillna(0.0)
    item_rows["line_total"] = pd.to_numeric(item_rows["line_total"], errors="coerce").fillna(0.0)

    serializable = {
        "schema": "headline_invoice_payload_v1",
        "invoice_number": _safe_text(payload.get("invoice_number")),
        "document_type": _normalize_document_type(payload.get("document_type")),
        "order_status": _safe_text(payload.get("order_status") or "confirmed").lower(),
        "event_date": _safe_text(payload.get("event_date")),
        "event_time": _safe_text(payload.get("event_time")),
        "rental_hours": float(pd.to_numeric(payload.get("rental_hours", 24), errors="coerce") or 24.0),
        "event_location": _safe_text(payload.get("event_location")),
        "customer_name": _safe_text(payload.get("customer_name")),
        "customer_phone": _safe_text(payload.get("customer_phone")),
        "customer_email": _safe_text(payload.get("customer_email")),
        "delivered_to": _safe_text(payload.get("delivered_to")),
        "paid_to": _safe_text(payload.get("paid_to")),
        "notes": _safe_text(payload.get("notes")),
        "currency": _safe_text(payload.get("currency") or "JM$"),
        "total": float(pd.to_numeric(payload.get("total", 0.0), errors="coerce") or 0.0),
        "items": item_rows[["item_name", "item_type", "quantity", "unit_price", "line_total"]].to_dict(
            orient="records"
        ),
    }

    packed = json.dumps(serializable, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(zlib.compress(packed, level=9)).decode("ascii")


def _embed_pdf_metadata(pdf_bytes: bytes, payload: dict) -> bytes:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        existing_meta = reader.metadata or {}
        clean_meta: dict[str, str] = {}
        for key, value in existing_meta.items():
            if value is None:
                continue
            meta_key = str(key)
            if not meta_key.startswith("/"):
                meta_key = f"/{meta_key}"
            clean_meta[meta_key] = str(value)

        clean_meta["/HeadlinePayloadEncoding"] = "zlib+base64+json"
        clean_meta["/HeadlinePayload"] = _payload_metadata_blob(payload)
        clean_meta.setdefault("/Producer", "Headline Rentals Finance Hub")

        writer.add_metadata(clean_meta)
        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return out.getvalue()
    except Exception:
        return pdf_bytes


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
    rows["item_name"] = rows["item_name"].apply(_customer_item_label)

    total = float(rows["line_total"].sum())
    document_type = _normalize_document_type(header.get("document_type"))
    payment_status = _safe_text(header.get("payment_status") or "paid_full").lower()
    if payment_status not in {"unpaid", "deposit_paid", "paid_full"}:
        payment_status = "paid_full"
    amount_paid_raw = pd.to_numeric(header.get("amount_paid", 0), errors="coerce")
    amount_paid = float(0.0 if pd.isna(amount_paid_raw) else amount_paid_raw)
    amount_paid = max(0.0, amount_paid)
    if payment_status == "paid_full":
        amount_paid = total
    if payment_status == "deposit_paid":
        if document_type == "quote":
            deposit_due_now = round(total * 0.5, 2)
            balance_due_later = round(max(total - deposit_due_now, 0.0), 2)
        else:
            deposit_due_now = round(min(max(amount_paid, 0.0), total), 2)
            balance_due_later = round(max(total - amount_paid, 0.0), 2)
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
        "document_type": document_type,
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
    if rows <= 8:
        layout_scale = 1.0
    elif rows <= 14:
        layout_scale = 0.94
    elif rows <= 22:
        layout_scale = 0.88
    else:
        layout_scale = 0.82

    def ys(value: int) -> int:
        return max(1, int(round(float(value) * layout_scale)))

    line_h_body = max(28, ys(38))
    line_h_small = max(20, ys(30))
    line_h_block = max(24, ys(34))
    table_header_h = max(44, ys(56))

    height = max(1520, ys(980) + rows * (line_h_body + max(4, ys(6))))
    width = 1600

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    def resolve_logo_candidates(candidate_path: str | Path | None) -> list[Path]:
        candidates: list[Path] = []
        if candidate_path:
            try:
                candidates.append(Path(candidate_path).expanduser())
            except Exception:
                pass
        assets_dirs = [
            Path(__file__).resolve().with_name("assets"),
            Path.cwd() / "assets",
        ]
        for assets_dir in assets_dirs:
            candidates.extend(
                [
                    assets_dir / "headline-rentals-logo.png",
                    assets_dir / "headline-rentals-logo.PNG",
                    assets_dir / "headline-rentals-logo.jpg",
                    assets_dir / "headline-rentals-logo.jpeg",
                    assets_dir / "Headline-Rentals-Logo.png",
                    assets_dir / "headline rentals logo.png",
                    assets_dir / "headline_rentals_logo.png",
                    assets_dir / "logo.jpg",
                    assets_dir / "logo.jpeg",
                    assets_dir / "logo.png",
                    assets_dir / "favicon.png",
                ]
            )
            # Also pick up any custom logo filename placed in assets/.
            for pattern in (
                "*headline*logo*.png",
                "*headline*logo*.PNG",
                "*headline*rent*.png",
                "*headline*rent*.PNG",
                "*logo*.png",
                "*logo*.PNG",
                "*logo*.jpg",
                "*logo*.jpeg",
                "*favicon*.png",
                "*icon*.png",
            ):
                try:
                    candidates.extend(sorted(assets_dir.glob(pattern)))
                except Exception:
                    continue
        for root_dir in [Path(__file__).resolve().parent, Path.cwd()]:
            for pattern in (
                "*headline*logo*.png",
                "*headline*logo*.PNG",
                "*headline*rent*.png",
                "*headline*rent*.PNG",
                "*logo*.png",
                "*logo*.PNG",
                "*favicon*.png",
            ):
                try:
                    candidates.extend(sorted(root_dir.glob(pattern)))
                except Exception:
                    continue
        seen: set[str] = set()
        valid: list[Path] = []
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                if path.exists() and path.is_file():
                    valid.append(path)
            except Exception:
                continue
        return valid

    def font(size: int, bold: bool = False):
        weight_key = "bold" if bold else "regular"
        font_candidates = _FONT_CANDIDATE_CACHE.get(weight_key)
        if font_candidates is None:
            candidates: list[str] = []
            # Deterministic font priority for consistent, clean exports.
            assets_dirs = [
                Path(__file__).resolve().with_name("assets"),
                Path.cwd() / "assets",
                Path(__file__).resolve().parent,
                Path.cwd(),
            ]
            preferred_names = (
                [
                    "HeadlineSans-Bold.ttf",
                    "headline-sans-bold.ttf",
                    "DejaVuSans-Bold.ttf",
                    "Arial Bold.ttf",
                    "Arial-Bold.ttf",
                ]
                if bold
                else [
                    "HeadlineSans-Regular.ttf",
                    "headline-sans-regular.ttf",
                    "DejaVuSans.ttf",
                    "Arial.ttf",
                ]
            )
            for assets_dir in assets_dirs:
                try:
                    if not assets_dir.exists():
                        continue
                    for name in preferred_names:
                        hit = assets_dir / name
                        if hit.exists():
                            candidates.append(str(hit))
                except Exception:
                    continue

            # Pillow bundle can include DejaVu fonts.
            try:
                import PIL  # type: ignore

                pil_fonts = Path(PIL.__file__).resolve().parent / "fonts"
                if pil_fonts.exists():
                    pick = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
                    exact = pil_fonts / pick
                    if exact.exists():
                        candidates.append(str(exact))
                    pattern = "DejaVuSans-Bold*.ttf" if bold else "DejaVuSans*.ttf"
                    for hit in sorted(pil_fonts.glob(pattern)):
                        candidates.append(str(hit))
            except Exception:
                pass

            # System fallbacks.
            candidates.extend(
                [
                    "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    if bold
                    else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
                    if bold
                    else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                    "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
                    "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
                    if bold
                    else "/System/Library/Fonts/Supplemental/Arial.ttf",
                    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
                ]
            )

            # Streamlit KaTeX fonts as late fallback only.
            try:
                import streamlit  # type: ignore

                streamlit_root = Path(streamlit.__file__).resolve().parent
                streamlit_patterns = (
                    ("KaTeX_SansSerif-Bold*.ttf", "KaTeX_Main-Bold*.ttf")
                    if bold
                    else ("KaTeX_SansSerif-Regular*.ttf", "KaTeX_Main-Regular*.ttf")
                )
                for pattern in streamlit_patterns:
                    for hit in sorted(streamlit_root.rglob(pattern)):
                        candidates.append(str(hit))
            except Exception:
                pass

            deduped: list[str] = []
            seen: set[str] = set()
            for token in candidates:
                key = str(token)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(key)
            _FONT_CANDIDATE_CACHE[weight_key] = deduped
            font_candidates = deduped

        for path in font_candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        # Final fallback for hosted environments with limited system fonts.
        # Pillow >=10.1 supports load_default(size=...), which keeps text readable.
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            fallback = ImageFont.load_default()
            if hasattr(fallback, "font_variant"):
                try:
                    return fallback.font_variant(size=size)
                except Exception:
                    pass
            return fallback

    f_title = font(max(40, ys(58)), bold=True)
    f_h1 = font(max(30, ys(42)), bold=True)
    f_h2 = font(max(22, ys(30)), bold=True)
    f_body = font(max(18, ys(26)))
    f_small = font(max(16, ys(22)))

    draw.rectangle((0, 0, width, ys(18)), fill=PRIMARY_COLOR)

    logo_rendered = False
    for resolved_logo_path in resolve_logo_candidates(logo_path):
        try:
            logo = Image.open(resolved_logo_path).convert("RGBA")
            logo.thumbnail((ys(170), ys(170)))
            image.paste(logo, (56, ys(40)), mask=logo)
            logo_rendered = True
            break
        except Exception:
            continue
    if not logo_rendered:
        # Keep invoice brand identity visible even if image file is missing.
        lx, ly = 56, ys(40)
        draw.rounded_rectangle(
            (lx, ly, lx + ys(170), ly + ys(170)),
            radius=ys(20),
            outline=SECONDARY_COLOR,
            width=3,
            fill="#F6FAFF",
        )
        draw.text((lx + ys(24), ly + ys(45)), "HR", fill=PRIMARY_COLOR, font=f_h1)
        draw.text((lx + ys(20), ly + ys(104)), "Rentals", fill=PRIMARY_COLOR, font=f_small)

    doc_type = _normalize_document_type(payload.get("document_type"))
    status = str(payload.get("order_status", "confirmed")).strip().lower()
    title_override = _safe_text(payload.get("document_title_override"))
    doc_title = title_override.upper() if title_override else ("PRICE QUOTE" if doc_type == "quote" else "INVOICE")
    title_bbox = draw.textbbox((0, 0), doc_title, font=f_title)
    title_width = max(0, int(title_bbox[2] - title_bbox[0]))
    title_x = max(56, width - 56 - title_width)
    draw.text((245, ys(58)), payload["business_name"], fill=TEXT_COLOR, font=f_h1)
    draw.text((title_x, ys(58)), doc_title, fill=PRIMARY_COLOR, font=f_title)
    draw.text((width - 440, ys(140)), f"#{payload['invoice_number']}", fill=TEXT_COLOR, font=f_h2)
    draw.text((width - 440, ys(188)), f"Status: {status.upper()}", fill=TEXT_COLOR, font=f_small)

    draw.text((56, ys(240)), "Customer", fill=PRIMARY_COLOR, font=f_h2)
    draw.text((840, ys(240)), "Event", fill=PRIMARY_COLOR, font=f_h2)

    customer_wrap = 48 if layout_scale >= 0.95 else 56
    event_wrap = 44 if layout_scale >= 0.95 else 52

    y_left = ys(294)
    for line in [
        payload["customer_name"],
        f"Phone: {payload['customer_phone']}" if payload["customer_phone"] else "",
        f"Email: {payload['customer_email']}" if payload["customer_email"] else "",
    ]:
        if line:
            for wrapped in textwrap.wrap(line, width=customer_wrap)[:2]:
                draw.text((56, y_left), wrapped, fill=TEXT_COLOR, font=f_body)
                y_left += line_h_body

    y_right = ys(294)
    for line in [
        f"Date: {payload['event_date']}",
        f"Time: {payload['event_time']}",
        f"Duration: {payload['rental_hours']:g}h",
        f"Location: {(payload['event_location'] or payload['delivered_to'])}",
    ]:
        for wrapped in textwrap.wrap(line, width=event_wrap)[:2]:
            draw.text((840, y_right), wrapped, fill=TEXT_COLOR, font=f_body)
            y_right += line_h_body

    seller_block_top = max(y_left, y_right) + ys(24)
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
    block_height = ys(28) + (len(seller_lines) * line_h_block)
    draw.rounded_rectangle(
        (44, seller_block_top, width - 44, seller_block_top + block_height),
        radius=ys(12),
        outline=SECONDARY_COLOR,
        width=3,
        fill="#F8FBFF",
    )
    seller_y = seller_block_top + ys(16)
    for idx, line in enumerate(seller_lines):
        line_font = f_h2 if idx in {0, 4} else f_body
        draw.text((64, seller_y), line, fill=TEXT_COLOR, font=line_font)
        seller_y += line_h_block

    table_top = seller_block_top + block_height + ys(30)
    draw.rectangle((44, table_top, width - 44, table_top + table_header_h), fill=SECONDARY_COLOR)
    table_header_y = table_top + max(8, ys(12))
    draw.text((64, table_header_y), "Description", fill=TEXT_COLOR, font=f_h2)
    draw.text((880, table_header_y), "Qty", fill=TEXT_COLOR, font=f_h2)
    draw.text((1020, table_header_y), f"Unit ({payload['currency']})", fill=TEXT_COLOR, font=f_h2)
    draw.text((1310, table_header_y), f"Total ({payload['currency']})", fill=TEXT_COLOR, font=f_h2)

    y = table_top + table_header_h + max(10, ys(14))
    desc_wrap = 54 if layout_scale >= 0.95 else (62 if layout_scale >= 0.88 else 70)
    if payload["items"].empty:
        draw.text((64, y), "No line items", fill=TEXT_COLOR, font=f_body)
        y += line_h_body

    for _, row in payload["items"].iterrows():
        item_name = _safe_text(row.get("item_name"))
        qty = float(row.get("quantity") or 0.0)
        unit = float(row.get("unit_price") or 0.0)
        line_total = float(row.get("line_total") or 0.0)

        wrapped = textwrap.wrap(item_name, width=desc_wrap) or [item_name]
        draw.text((64, y), wrapped[0], fill=TEXT_COLOR, font=f_body)
        draw.text((890, y), f"{qty:g}", fill=TEXT_COLOR, font=f_body)
        draw.text((1044, y), f"{unit:,.2f}", fill=TEXT_COLOR, font=f_body)
        draw.text((1338, y), f"{line_total:,.2f}", fill=TEXT_COLOR, font=f_body)
        y += line_h_body

        for extra in wrapped[1:3]:
            draw.text((78, y), extra, fill=TEXT_COLOR, font=f_small)
            y += line_h_small
        y += max(4, ys(6))

    y += ys(20)
    draw.line((940, y, width - 56, y), fill=PRIMARY_COLOR, width=max(2, ys(3)))
    y += ys(24)
    draw.text(
        (940, y),
        f"Total Cost: {money(payload['total'], payload['currency'])}",
        fill=PRIMARY_COLOR,
        font=f_h1,
    )

    payment_status = str(payload.get("payment_status", "paid_full")).strip().lower()
    if doc_type == "invoice" and status == "confirmed":
        paid_value = float(pd.to_numeric(payload.get("amount_paid", 0.0), errors="coerce") or 0.0)
        total_value = float(pd.to_numeric(payload.get("total", 0.0), errors="coerce") or 0.0)
        balance_fallback = max(total_value - paid_value, 0.0)
        balance_value = float(
            pd.to_numeric(payload.get("balance_due_later", balance_fallback), errors="coerce")
            or balance_fallback
        )
        balance_value = max(0.0, balance_value)
        y += ys(58)
        draw.text(
            (940, y),
            f"Amount Paid: {money(payload.get('amount_paid', 0.0), payload['currency'])}",
            fill=TEXT_COLOR,
            font=f_h2,
        )
        if payment_status == "deposit_paid":
            y += ys(44)
            draw.text(
                (940, y),
                f"Balance Due Later: {money(payload.get('balance_due_later', 0.0), payload['currency'])}",
                fill=TEXT_COLOR,
                font=f_body,
            )
        elif payment_status == "unpaid":
            y += ys(44)
            draw.text(
                (940, y),
                f"Amount Due Now: {money(payload['total'], payload['currency'])}",
                fill=TEXT_COLOR,
                font=f_body,
            )
        else:
            y += ys(44)
            draw.text(
                (940, y),
                f"Balance Due: {money(balance_value, payload['currency'])}",
                fill=TEXT_COLOR,
                font=f_body,
            )
    elif payment_status == "deposit_paid":
        y += ys(58)
        draw.text(
            (940, y),
            f"Deposit Due Now (50%): {money(payload.get('deposit_due_now', 0.0), payload['currency'])}",
            fill=TEXT_COLOR,
            font=f_h2,
        )

    y += ys(72)
    note = payload["notes"] or "Thank you for choosing Headline Rentals."
    note_wrap = 98 if layout_scale >= 0.95 else 112
    for wrapped in textwrap.wrap(f"Notes: {note}", width=note_wrap)[:3]:
        draw.text((56, y), wrapped, fill=TEXT_COLOR, font=f_small)
        y += line_h_small

    return image


def render_invoice_pdf(payload: dict, logo_path: str | Path | None = None) -> bytes:
    image = _build_invoice_image(payload, logo_path=logo_path).convert("RGB")
    out = io.BytesIO()
    try:
        image.save(
            out,
            format="PDF",
            resolution=300.0,
            quality=95,
            subsampling=0,
            optimize=True,
        )
    except TypeError:
        image.save(out, format="PDF", resolution=300.0)
    out.seek(0)
    return _embed_pdf_metadata(out.getvalue(), payload)


def render_invoice_png(payload: dict, logo_path: str | Path | None = None) -> bytes:
    image = _build_invoice_image(payload, logo_path=logo_path)
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()
