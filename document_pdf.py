"""
Headline Event Rentals customer documents (quotes, invoices, receipts) and the crew delivery sheet.

Rendered as real, multi-page PDFs with ReportLab so long orders flow onto extra pages with the
table header repeated, instead of being cut off. PNG copies are made by rasterising the PDF.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent

PURPLE = colors.HexColor("#5927e5")
INK = colors.HexColor("#0E0E1A")
TEXT = colors.HexColor("#1A1A28")
MUTED = colors.HexColor("#52526A")
FEE_GREY = colors.HexColor("#3E3E52")
RULE = colors.HexColor("#E6E6EE")
HEAD_BG = colors.HexColor("#F1EEFE")
HEAD_INK = colors.HexColor("#3B2A9E")
LABEL = colors.HexColor("#5927e5")
CYAN = colors.HexColor("#E3E1EE")
SELLER_BG = colors.HexColor("#F8F8FC")
LOGI_BG = colors.HexColor("#F4F1FF")

TONES = {
    "amber": (colors.HexColor("#FFF4DB"), colors.HexColor("#9A6400")),
    "green": (colors.HexColor("#E3F7EC"), colors.HexColor("#13794A")),
    "red": (colors.HexColor("#FDE8E8"), colors.HexColor("#B42318")),
    "blue": (colors.HexColor("#E6F0FF"), colors.HexColor("#1D4ED8")),
    "grey": (colors.HexColor("#EEEEF3"), colors.HexColor("#55556a")),
}

PAGE_W, PAGE_H = LETTER
MARGIN_X = 0.5 * inch
MARGIN_TOP = 0.42 * inch
MARGIN_BOTTOM = 0.55 * inch
FRAME_PAD = 6  # SimpleDocTemplate frames have 6pt padding on every side
CONTENT_W = PAGE_W - 2 * MARGIN_X - 2 * FRAME_PAD

# ---------------------------------------------------------------- "fit" layout for customer documents
# Quotes, invoices and receipts are laid out on a narrower virtual page and scaled up (FIT_ZOOM) so the
# text is bigger on a phone, and the sheet is trimmed to the exact height of the content, so the PDF
# is the quote and nothing else. Very long orders fall back to normal Letter pages.
FIT_ZOOM = 1.14
FIT_MARGINS = (0.36 * inch, 0.5 * inch, 0.42 * inch)  # side, top, bottom in real points
FIT_MAX_HEIGHT = LETTER[1] * 2.5  # longest single sheet before switching to Letter pages
ZOOM = 1.0
REAL_SIZE = LETTER
BLEED_BAR = False

import threading as _threading

_LAYOUT_LOCK = _threading.RLock()


def _set_layout(zoom: float = 1.0, real_height: float = LETTER[1], margins=None, bleed_bar: bool = False) -> None:
    """Point the module-level page geometry at a (possibly zoomed, possibly custom height) sheet."""
    global ZOOM, REAL_SIZE, BLEED_BAR, PAGE_W, PAGE_H, MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM, CONTENT_W
    ZOOM = float(zoom)
    REAL_SIZE = (LETTER[0], float(real_height))
    BLEED_BAR = bool(bleed_bar)
    PAGE_W, PAGE_H = REAL_SIZE[0] / ZOOM, REAL_SIZE[1] / ZOOM
    mx, mt, mb = margins or (0.5 * inch, 0.42 * inch, 0.55 * inch)
    MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = mx / ZOOM, mt / ZOOM, mb / ZOOM
    CONTENT_W = PAGE_W - 2 * MARGIN_X - 2 * FRAME_PAD

# --------------------------------------------------------------------------- fonts

_FONT_FILES = {
    "Pop": "Poppins-Regular.ttf",
    "Pop-Light": "Poppins-Light.ttf",
    "Pop-Medium": "Poppins-Medium.ttf",
    "Pop-Bold": "Poppins-Bold.ttf",
}
_FONTS_READY: dict[str, str] | None = None


def _font_dirs() -> list[Path]:
    return [
        HERE,
        HERE / "fonts",
        Path.cwd(),
        Path.cwd() / "fonts",
        Path("/usr/share/fonts/truetype/google-fonts"),
    ]


def _fonts() -> dict[str, str]:
    """Register Poppins if available; otherwise fall back to built-in Helvetica."""
    global _FONTS_READY
    if _FONTS_READY is not None:
        return _FONTS_READY
    mapping: dict[str, str] = {}
    for alias, filename in _FONT_FILES.items():
        for folder in _font_dirs():
            path = folder / filename
            if path.exists():
                try:
                    pdfmetrics.registerFont(TTFont(alias, str(path)))
                    mapping[alias] = alias
                except Exception:
                    pass
                break
    fallback = {"Pop": "Helvetica", "Pop-Light": "Helvetica", "Pop-Medium": "Helvetica", "Pop-Bold": "Helvetica-Bold"}
    for alias, fb in fallback.items():
        mapping.setdefault(alias, fb)
    _FONTS_READY = mapping
    return mapping


def F(alias: str) -> str:
    return _fonts()[alias]


def _style(name: str, font: str = "Pop", size: float = 8.9, color=TEXT, leading: float | None = None,
           align=TA_LEFT, **kw) -> ParagraphStyle:
    return ParagraphStyle(
        name, fontName=F(font), fontSize=size, textColor=color,
        leading=leading if leading is not None else size * 1.45, alignment=align, **kw,
    )


def esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def money(value: float, currency: str = "JM$") -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    sign = "-" if number < 0 else ""
    return f"{sign}{currency}{abs(number):,.2f}"


def num(value: float) -> str:
    return f"{float(value or 0.0):,.2f}"


def qty_text(value: float) -> str:
    v = float(value or 0.0)
    return f"{v:g}"


def fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%a %d %b %Y")
    if isinstance(value, date):
        return value.strftime("%a %d %b %Y")
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%a %d %b %Y")
    except ValueError:
        return text


def fmt_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text[:5], "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return text


def fmt_moment(date_value: Any, time_value: Any = "") -> str:
    d = fmt_date(date_value)
    t = fmt_time(time_value)
    if d and t:
        return f"{d}, {t}"
    return d or t


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def add_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 Feb
        return d + timedelta(days=365)


# --------------------------------------------------------------------------- small flowables

class Pill(Flowable):
    """Rounded status label, right aligned."""

    def __init__(self, text: str, tone: str, width: float):
        super().__init__()
        self.text = text.upper()
        self.bg, self.fg = TONES.get(tone, TONES["grey"])
        self.width = width
        self.height = 17

    def draw(self):
        font = F("Pop-Bold")
        size = 7.6
        tw = pdfmetrics.stringWidth(self.text, font, size) + 20
        x = self.width - tw
        self.canv.setFillColor(self.bg)
        self.canv.roundRect(x, 0, tw, self.height, 8.5, stroke=0, fill=1)
        self.canv.setFillColor(self.fg)
        self.canv.setFont(font, size)
        self.canv.drawString(x + 10, 5.4, self.text)


class Band(Flowable):
    """A full-width rounded row: label left, amount right, coloured background."""

    def __init__(self, label: str, value: str, bg, fg, width: float, size: float = 10.5, height: float = 24,
                 shadow: bool = False):
        super().__init__()
        self.label, self.value = label, value
        self.bg, self.fg = bg, fg
        self.width, self.height = width, height
        self.size = size
        self.shadow = shadow

    def draw(self):
        c = self.canv
        if self.shadow:
            c.setFillColor(colors.Color(0.35, 0.15, 0.9, alpha=0.18))
            c.roundRect(0.5, -2, self.width - 1, self.height, 9, stroke=0, fill=1)
        c.setFillColor(self.bg)
        c.roundRect(0, 0, self.width, self.height, 9, stroke=0, fill=1)
        c.setFillColor(self.fg)
        size = self.size
        font = F("Pop-Bold")
        while size > 7 and (pdfmetrics.stringWidth(self.label, font, size)
                            + pdfmetrics.stringWidth(self.value, font, size) + 24 + 16) > self.width:
            size -= 0.25
        c.setFont(font, size)
        base = (self.height - size * 0.72) / 2
        c.drawString(12, base, self.label)
        c.drawRightString(self.width - 12, base, self.value)


class RoundedBox(Flowable):
    """Wraps a flowable in a rounded, filled (optionally outlined) box."""

    def __init__(self, inner: Flowable, width: float, bg, border=None, radius: float = 8,
                 pad_x: float = 12, pad_y: float = 8, dashed: bool = False, min_height: float = 0):
        super().__init__()
        self.inner = inner
        self.width = width
        self.bg, self.border = bg, border
        self.radius = radius
        self.pad_x, self.pad_y = pad_x, pad_y
        self.dashed = dashed
        self.min_height = min_height

    def wrap(self, aw, ah):
        _w, h = self.inner.wrap(self.width - 2 * self.pad_x, ah)
        self.inner_h = h
        self.height = max(h + 2 * self.pad_y, self.min_height)
        return self.width, self.height

    def draw(self):
        c = self.canv
        if self.bg is not None:
            c.setFillColor(self.bg)
        if self.border is not None:
            c.setStrokeColor(self.border)
            c.setLineWidth(0.9)
            if self.dashed:
                c.setDash(3, 2.5)
        c.roundRect(0, 0, self.width, self.height, self.radius,
                    stroke=1 if self.border is not None else 0, fill=1 if self.bg is not None else 0)
        c.setDash()
        self.inner.drawOn(c, self.pad_x, self.height - self.pad_y - self.inner_h)


class PinToBottom(Flowable):
    """Takes up the rest of the page and draws its contents at the very bottom.

    If there is not enough room left, it moves to the next page and sits at the bottom there.
    """

    def __init__(self, flowables: list):
        super().__init__()
        self.box = Table([[flowables]], colWidths=[None])
        self.box.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        self.inner_h = 0.0

    def wrap(self, aw, ah):
        self.box._argW = [aw]
        _w, self.inner_h = self.box.wrap(aw, ah)
        self.width = aw
        # fill whatever is left on the page; slightly under so the frame accepts it
        self.height = max(self.inner_h, ah - 0.5)
        return self.width, self.height

    def split(self, aw, ah):
        return []  # never split: move whole block to the next page instead

    def draw(self):
        self.box.drawOn(self.canv, 0, 0)


class Rule(Flowable):
    def __init__(self, width: float, color=RULE, thickness: float = 0.8, space_before: float = 0, space_after: float = 0):
        super().__init__()
        self.width, self.color, self.thickness = width, color, thickness
        self.space_before, self.space_after = space_before, space_after
        self.height = thickness

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


# --------------------------------------------------------------------------- page decorations

def _make_canvas_class(footer_left: str, stamp_text: str = ""):
    zoom, real_size, bleed = ZOOM, REAL_SIZE, BLEED_BAR

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            kwargs["pagesize"] = real_size
            super().__init__(*args, **kwargs)
            self._saved_pages: list[dict] = []
            self._apply_zoom()

        def _apply_zoom(self):
            if zoom != 1.0:
                self.scale(zoom, zoom)

        def setPageSize(self, size):
            super().setPageSize(real_size)

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()
            self._apply_zoom()

        def save(self):
            total = len(self._saved_pages)
            for idx, state in enumerate(self._saved_pages, start=1):
                self.__dict__.update(state)
                self._decorate(idx, total)
                super().showPage()
            super().save()

        def _decorate(self, page: int, total: int):
            # gradient bar on the first page
            if page == 1:
                steps = 90 if bleed else 60
                if bleed:  # full width strip on the very top edge of the sheet
                    bar_h, bar_x, bar_y = 8 / zoom, 0.0, PAGE_H - 8 / zoom
                    seg = PAGE_W / steps
                else:
                    bar_h, bar_x, bar_y = 5, MARGIN_X + FRAME_PAD, PAGE_H - MARGIN_TOP + 4
                    seg = (PAGE_W - 2 * MARGIN_X - 2 * FRAME_PAD) / steps
                start = (0x59, 0x27, 0xE5)
                mid = (0x4F, 0x8F, 0xF7)
                end = (0x3F, 0xD0, 0xF5)
                for i in range(steps):
                    t = i / (steps - 1)
                    if t < 0.55:
                        k = t / 0.55
                        rgb = [start[j] + (mid[j] - start[j]) * k for j in range(3)]
                    else:
                        k = (t - 0.55) / 0.45
                        rgb = [mid[j] + (end[j] - mid[j]) * k for j in range(3)]
                    self.setFillColorRGB(*(v / 255.0 for v in rgb))
                    self.rect(bar_x + i * seg, bar_y, seg + 0.6, bar_h, stroke=0, fill=1)
                if stamp_text:
                    self.saveState()
                    self.translate(MARGIN_X + CONTENT_W * 0.42, PAGE_H - MARGIN_TOP - 62)
                    self.rotate(12)
                    self.setStrokeColor(colors.Color(0.07, 0.47, 0.29, alpha=0.32))
                    self.setFillColor(colors.Color(0.07, 0.47, 0.29, alpha=0.32))
                    self.setLineWidth(3)
                    font = F("Pop-Bold")
                    w = pdfmetrics.stringWidth(stamp_text, font, 24) + 28
                    self.roundRect(-w / 2, -14, w, 36, 8, stroke=1, fill=0)
                    self.setFont(font, 24)
                    self.drawCentredString(0, -5, stamp_text)
                    self.restoreState()
            self.setFont(F("Pop"), 7.6)
            self.setFillColor(colors.HexColor("#6E6E84"))
            foot_y = MARGIN_BOTTOM - 22 if not bleed else 12 / zoom
            self.drawString(MARGIN_X + FRAME_PAD, foot_y, footer_left)
            if total > 1 or not bleed:
                self.drawRightString(PAGE_W - MARGIN_X - FRAME_PAD, foot_y, f"Page {page} of {total}")

    return NumberedCanvas


# --------------------------------------------------------------------------- header + common blocks

def _logo_flowable(logo_path: str | Path | None, height: float = 80) -> Flowable:
    # Documents use their own logo file when present, so the app's tab icon
    # (headline-rentals-logo.png) can be changed without touching quotes and invoices.
    candidates: list[Path] = [HERE / "invoice-logo.png", Path.cwd() / "invoice-logo.png"]
    if logo_path:
        candidates.append(Path(logo_path))
    candidates += [HERE / "headline-rentals-logo.png", Path.cwd() / "headline-rentals-logo.png"]
    for path in candidates:
        try:
            if path.exists():
                from PIL import Image as PILImage

                with PILImage.open(path) as im:
                    w, h = im.size
                return Image(str(path), width=height * w / max(h, 1), height=height)
        except Exception:
            continue
    return Paragraph("Headline Rentals", _style("logo_fb", "Pop-Bold", 14, PURPLE))


def _header(model: dict, logo_path) -> list:
    right_w = CONTENT_W * 0.55
    title = Paragraph(esc(model["title"]), _style("title", "Pop-Bold", 25, PURPLE, leading=29, align=TA_RIGHT))
    number = Paragraph(esc(model.get("number_label") or f"#{model['number']}"), _style("docno", "Pop-Bold", 13, INK, leading=17, align=TA_RIGHT))
    dates = Paragraph("<br/>".join(esc(x) for x in model.get("date_lines", []) if x),
                      _style("dates", "Pop", 8.8, MUTED, leading=13, align=TA_RIGHT))
    right = [title, number, Spacer(1, 4), Pill(model["status_text"], model["status_tone"], right_w), Spacer(1, 6), dates]
    t = Table([[_logo_flowable(logo_path), right]], colWidths=[CONTENT_W - right_w, right_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [t]


def _h3(text: str) -> Paragraph:
    return Paragraph(esc(text).upper(), _style("h3", "Pop-Bold", 8.2, LABEL, leading=12))


def _customer_event(model: dict) -> list:
    body = _style("kv", "Pop", 9.8, TEXT, leading=14.6)
    cust = model.get("customer", {})
    cust_lines = [esc(cust.get("name", ""))]
    if cust.get("phone"):
        cust_lines.append(f"Phone: {esc(cust['phone'])}")
    if cust.get("email"):
        cust_lines.append(f"Email: {esc(cust['email'])}")
    if cust_lines and cust_lines[0]:
        cust_lines[0] = f"<font name='{F('Pop-Bold')}'>{cust_lines[0]}</font>"
    left = [_h3("Customer"), Spacer(1, 3), Paragraph("<br/>".join(x for x in cust_lines if x), body)]

    ev = model.get("event", {})
    ev_lines = []
    if ev.get("when"):
        ev_lines.append(f"Date: {esc(ev['when'])}")
    if ev.get("duration"):
        ev_lines.append(f"Duration: {esc(ev['duration'])}")
    if ev.get("location"):
        ev_lines.append(f"Location: {esc(ev['location'])}")
    col_w = CONTENT_W * 0.46
    right = [_h3("Event"), Spacer(1, 3), Paragraph("<br/>".join(ev_lines), body)]
    logi = model.get("logistics_lines") or []
    if logi:
        lp = Paragraph("<br/>".join(f"<font name='{F('Pop-Bold')}'>{esc(k)}:</font> {esc(v)}" for k, v in logi),
                       _style("logi", "Pop", 9.4, TEXT, leading=14))
        right += [Spacer(1, 7), RoundedBox(lp, col_w, LOGI_BG, radius=8, pad_x=11, pad_y=8)]
    t = Table([[left, right]], colWidths=[CONTENT_W - col_w, col_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [t]


def _seller_box(model: dict) -> list:
    bank = model.get("bank", {})
    body = _style("sel", "Pop", 9.6, TEXT, leading=14.2)
    heading = _style("selh", "Pop-Bold", 8.2, LABEL, leading=12)
    seller_name = bank.get("seller_name") or "Headline Event Rentals"
    left = [
        Paragraph("PAY TO", heading),
        Spacer(1, 3),
        Paragraph(f"<font name='{F('Pop-Bold')}'>{esc(seller_name)}</font>", body),
        Paragraph("<br/>".join(esc(x) for x in [bank.get("seller_address_1"), bank.get("seller_address_2")] if x), body),
    ]
    lines = [f"<font name='{F('Pop-Bold')}'>Name:</font> {esc(bank.get('bank_account_name', ''))}"]
    if bank.get("bank_account_type"):
        lines.append(esc(bank["bank_account_type"]))
    if bank.get("bank_branch"):
        lines.append(f"Branch: {esc(bank['bank_branch'])}")
    if bank.get("bank_account_number"):
        lines.append(f"Account #{esc(bank['bank_account_number'])}")
    right = [Paragraph("BANKING DETAILS", heading), Spacer(1, 3), Paragraph("<br/>".join(lines), body)]
    inner_w = CONTENT_W - 32
    inner = Table([[left, right]], colWidths=[inner_w * 0.5, inner_w * 0.5])
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [RoundedBox(inner, CONTENT_W, SELLER_BG, border=CYAN, radius=10, pad_x=16, pad_y=12)]


def _items_table(items: list[dict]) -> Table:
    head = _style("th", "Pop-Bold", 8.0, HEAD_INK)
    head_r = _style("thr", "Pop-Bold", 8.0, HEAD_INK, align=TA_RIGHT)
    head_c = ParagraphStyle("thc", parent=head, alignment=1)
    name_st = _style("iname", "Pop-Medium", 9.9, INK, leading=13.4)
    note_st = _style("inote", "Pop", 8.4, MUTED, leading=11.4)
    cell_r = _style("cellr", "Pop", 9.7, TEXT, align=TA_RIGHT, leading=13.4)
    cell_c = ParagraphStyle("cellc", parent=cell_r, alignment=1)
    total_r = _style("totr", "Pop-Bold", 9.7, INK, align=TA_RIGHT, leading=13.4)

    data = [[Paragraph("ITEM", head), Paragraph("QTY", head_c), Paragraph("UNIT (JM$)", head_r),
             Paragraph("TOTAL (JM$)", head_r)]]
    for it in items:
        desc = [Paragraph(esc(it["name"]), name_st)]
        if it.get("note"):
            desc.append(Paragraph(esc(it["note"]), note_st))
        data.append([desc, Paragraph(qty_text(it["qty"]), cell_c), Paragraph(num(it["unit"]), cell_r),
                     Paragraph(num(it["total"]), total_r)])
    widths = [CONTENT_W * 0.46, CONTENT_W * 0.12, CONTENT_W * 0.21, CONTENT_W * 0.21]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 7.5), ("BOTTOMPADDING", (0, 0), (-1, 0), 7.5),
        ("TOPPADDING", (0, 1), (-1, -1), 6.5), ("BOTTOMPADDING", (0, 1), (-1, -1), 6.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("LINEBELOW", (0, 1), (-1, -1), 0.7, RULE),
    ]
    t.setStyle(TableStyle(style))
    try:
        t.setStyle(TableStyle([("ROUNDEDCORNERS", [8, 8, 0, 0])]))
    except Exception:
        pass
    return t


def _totals_block(model: dict, width: float) -> list:
    cur = model.get("currency", "JM$")
    out: list = []

    def row(label: str, value: str, font: str, size: float, color, top_pad: float = 1.6, bottom_pad: float = 1.6,
            split: float = 0.58):
        t = Table([[Paragraph(esc(label), _style("tl", font, size, color)),
                    Paragraph(esc(value), _style("tv", font, size, color, align=TA_RIGHT))]],
                  colWidths=[width * split, width * (1 - split)])
        t.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), top_pad), ("BOTTOMPADDING", (0, 0), (-1, -1), bottom_pad),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    out.append(row("Subtotal", money(model["subtotal"], cur), "Pop-Bold", 10.2, INK, top_pad=2, bottom_pad=5))
    for label, amount in model.get("fee_rows", []):
        out.append(row(label, money(amount, cur), "Pop", 9.2, FEE_GREY, top_pad=2.2, bottom_pad=2.2))
    if model.get("discount", 0) > 0.004:
        bg, fg = TONES["green"]
        out.append(Spacer(1, 2))
        out.append(Band(model.get("discount_label", "You save"), f"-{money(model['discount'], cur)}", bg, fg, width,
                        size=8.3, height=18))
        out.append(Spacer(1, 2))
    if model.get("gct") is not None:
        out.append(row("GCT (15%)", money(model["gct"], cur), "Pop", 9.2, FEE_GREY, top_pad=2.2, bottom_pad=2.2))
    out.append(Spacer(1, 6))
    out.append(Rule(width, PURPLE, 1.8))
    out.append(Spacer(1, 8))
    out.append(row("Total Cost", money(model["total"], cur), "Pop-Bold", 15.5 if model["total"] < 10_000_000 else 13, PURPLE,
                   top_pad=0, bottom_pad=5, split=0.36))
    for kind, label, amount in model.get("pay_lines", []):
        if kind == "now":
            out += [Spacer(1, 5), Band(label, money(amount, cur), PURPLE, colors.white, width, size=11, height=31, shadow=True), Spacer(1, 4)]
        elif kind == "due":
            bg, fg = TONES["red"]
            out += [Spacer(1, 5), Band(label, money(amount, cur), bg, fg, width, size=11, height=30), Spacer(1, 3)]
        elif kind == "zero":
            bg, fg = TONES["green"]
            out += [Spacer(1, 5), Band(label, money(amount, cur), bg, fg, width, size=11, height=30), Spacer(1, 3)]
        else:
            out.append(row(label, money(amount, cur), "Pop-Bold", 9.6, INK, split=0.5, top_pad=3))
    return out


def _bottom(model: dict) -> list:
    left_w = CONTENT_W * 0.44
    right_w = CONTENT_W * 0.5
    gap = CONTENT_W - left_w - right_w
    pb = model.get("paybox")
    if pb:
        bg, fg = TONES.get(pb.get("tone", "blue"), TONES["blue"])
        inner = [Paragraph(esc(pb["title"]), _style("pbt", "Pop-Bold", 10.2, fg, leading=14.5)),
                 Spacer(1, 2),
                 Paragraph(esc(pb["text"]), _style("pbx", "Pop", 9.2, fg, leading=13.6))]
        holder = Table([[inner]], colWidths=[left_w - 26])
        holder.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        left = RoundedBox(holder, left_w, bg, radius=10, pad_x=13, pad_y=12)
    else:
        left = Spacer(1, 1)
    totals = _totals_block(model, right_w)
    t = Table([[left, "", totals]], colWidths=[left_w, gap, right_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [t]


def _terms(model: dict) -> list:
    out = [Spacer(1, 16), Rule(CONTENT_W), Spacer(1, 10)]
    if model.get("terms"):
        out.append(Paragraph(esc(model["terms"]), _style("terms", "Pop", 8.4, MUTED, leading=12.8)))
    if model.get("notes"):
        out.append(Spacer(1, 4))
        out.append(Paragraph(f"Notes: {esc(model['notes'])}", _style("notes", "Pop", 8.4, MUTED, leading=12.8)))
    out.append(Spacer(1, 8))
    out.append(Paragraph("Thank you for choosing Headline Event Rentals.", _style("thanks", "Pop-Bold", 10, PURPLE)))
    return [KeepTogether(out)]


def render_customer_document_pdf(model: dict, logo_path: str | Path | None = None) -> bytes:
    """model: see invoice_export.build_document_model.

    Renders on one sheet trimmed to the content (no empty paper). Orders too long for that
    fall back to standard Letter pages.
    """
    with _LAYOUT_LOCK:
        try:
            # 1) probe on a tall sheet to measure how much height the content needs
            _set_layout(FIT_ZOOM, FIT_MAX_HEIGHT, FIT_MARGINS, bleed_bar=True)
            _pdf, pages, used = _render_customer_document_once(model, logo_path)
            if pages == 1:
                real_h = (used + MARGIN_TOP + MARGIN_BOTTOM + 2 * FRAME_PAD) * ZOOM + 4
                real_h = max(real_h, LETTER[0] * 0.9)
                _set_layout(FIT_ZOOM, real_h, FIT_MARGINS, bleed_bar=True)
                pdf, pages, _used = _render_customer_document_once(model, logo_path)
                if pages == 1:
                    return pdf
            # 2) long order: classic Letter pages
            _set_layout(1.0, LETTER[1])
            pdf, _pages, _used = _render_customer_document_once(model, logo_path, top_extra=14)
            return pdf
        finally:
            _set_layout(1.0, LETTER[1])


def _render_customer_document_once(model: dict, logo_path, top_extra: float = 0) -> tuple[bytes, int, float]:
    """Returns (pdf bytes, page count, content height used on the last page in virtual points)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=(PAGE_W, PAGE_H), leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP + top_extra, bottomMargin=MARGIN_BOTTOM,
        title=f"{model.get('title', '')} #{model.get('number', '')}", author="Headline Event Rentals",
    )
    lowest = {"y": None}

    def _after(_flowable):
        frame = getattr(doc, "frame", None)
        if frame is not None:
            lowest["y"] = frame._y

    doc.afterFlowable = _after
    story: list = []
    story += _header(model, logo_path)
    story.append(Spacer(1, 16))
    story += _customer_event(model)
    story.append(Spacer(1, 16))
    story += _seller_box(model)
    story.append(Spacer(1, 18))
    story.append(_items_table(model.get("items", [])))
    story.append(Spacer(1, 16))
    story.append(KeepTogether(_bottom(model)))
    story += _terms(model)
    canvas_cls = _make_canvas_class(model.get("footer", ""), model.get("stamp", ""))
    doc.build(story, canvasmaker=canvas_cls)
    frame_top = PAGE_H - MARGIN_TOP - top_extra - FRAME_PAD
    used = frame_top - (lowest["y"] if lowest["y"] is not None else MARGIN_BOTTOM)
    return buf.getvalue(), int(doc.page), float(used)


# --------------------------------------------------------------------------- crew sheet

class CheckBox(Flowable):
    def __init__(self, size: float = 9):
        super().__init__()
        self.width = self.height = size

    def draw(self):
        self.canv.setStrokeColor(colors.HexColor("#9a9aae"))
        self.canv.setLineWidth(1)
        self.canv.roundRect(0, 0, self.width, self.height, 2, stroke=1, fill=0)


def render_crew_sheet_pdf(sheet: dict, logo_path: str | Path | None = None) -> bytes:
    with _LAYOUT_LOCK:
        _set_layout(1.0, LETTER[1])
        return _render_crew_sheet_once(sheet, logo_path)


def _render_crew_sheet_once(sheet: dict, logo_path: str | Path | None = None) -> bytes:
    """
    sheet keys: number, status_text, status_tone, printed, customer_name, customer_phone, onsite_contact,
    venue, event_when, access_notes, is_pickup, out_when, back_when, out_details (list[str]),
    back_details (list[str]), collect_amount, show_payment, route_lines (list[str]), items [{name, note, qty}].
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP + 14, bottomMargin=MARGIN_BOTTOM,
        title=f"Crew Delivery Sheet #{sheet.get('number', '')}", author="Headline Event Rentals",
    )
    header_model = {
        "title": "CREW DELIVERY SHEET",
        "number": sheet.get("number", ""),
        "number_label": f"Order #{sheet.get('number', '')}",
        "status_text": sheet.get("status_text", ""),
        "status_tone": sheet.get("status_tone", "grey"),
        "date_lines": [f"Printed: {sheet.get('printed', '')}"],
    }
    story: list = []
    hdr = _header(header_model, logo_path)
    # header shows "Order #" rather than "#"
    story += hdr
    story.append(Spacer(1, 10))

    half = (CONTENT_W - 9) / 2
    h4 = lambda t, c=PURPLE: Paragraph(esc(t), _style("ch4", "Pop-Bold", 9.0, c, leading=12.5))
    big = _style("cbig", "Pop-Bold", 10.6, INK, leading=14)
    body = _style("cbody", "Pop", 8.2, TEXT, leading=11.8)

    def card(title, big_text, lines, bg=None, border=colors.HexColor("#E4E4EF"), title_color=PURPLE, width=half,
             big_style=big, body_style=body):
        parts = [h4(title, title_color)]
        if big_text:
            parts.append(Paragraph(esc(big_text), big_style))
        lines = [x for x in lines if x]
        if lines:
            parts.append(Paragraph("<br/>".join(esc(x) for x in lines), body_style))
        inner = Table([[parts]], colWidths=[width - 26])
        inner.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        return RoundedBox(inner, width, bg if bg is not None else colors.white, border=border, radius=8, pad_x=13, pad_y=7)

    out_word, back_word = ("Store Pickup", "Return to Store") if sheet.get("is_pickup") else ("Delivery", "Collection")
    cards = [
        ("Customer", sheet.get("customer_name", ""), [
            f"Phone: {sheet['customer_phone']}" if sheet.get("customer_phone") else "",
            f"On-site contact: {sheet['onsite_contact']}" if sheet.get("onsite_contact") else "",
        ], {}),
        ("Venue", sheet.get("venue", ""), [
            f"Event: {sheet['event_when']}" if sheet.get("event_when") else "",
            f"Access: {sheet['access_notes']}" if sheet.get("access_notes") else "",
        ], {}),
        (out_word, sheet.get("out_when", ""), sheet.get("out_details", []), {}),
        (back_word, sheet.get("back_when", ""), sheet.get("back_details", []), {}),
    ]
    collect = float(sheet.get("collect_amount", 0.0) or 0.0)
    if sheet.get("show_payment", True):
        if collect > 0.004:
            bg, fg = TONES["red"]
            when = "pickup" if sheet.get("is_pickup") else "delivery"
            cards.append((f"Collect payment on {when}", money(collect), [
                "Cash or bank transfer. Get payment before setting up." if not sheet.get("is_pickup")
                else "Cash or bank transfer. Get payment before releasing items."],
                {"bg": bg, "border": colors.HexColor("#F7C6C6"), "title_color": fg,
                 "big_style": _style("cbigr", "Pop-Bold", 14, fg, leading=19),
                 "body_style": _style("cbodyr", "Pop", 8.2, fg, leading=11.8)}))
        else:
            bg, fg = TONES["green"]
            cards.append(("Payment", "Fully paid. Nothing to collect.", [],
                          {"bg": bg, "border": colors.HexColor("#BFE8D1"), "title_color": fg,
                           "big_style": _style("cbigg", "Pop-Bold", 10.6, fg, leading=14)}))
    route_lines = [x for x in sheet.get("route_lines", []) if x]
    if route_lines:
        cards.append(("Route", "", route_lines, {}))

    rows = []
    i = 0
    while i < len(cards):
        if i + 1 < len(cards):
            a, b = cards[i], cards[i + 1]
            rows.append([card(a[0], a[1], a[2], **a[3]), card(b[0], b[1], b[2], **b[3])])
            i += 2
        else:
            a = cards[i]
            rows.append([card(a[0], a[1], a[2], width=CONTENT_W, **a[3]), ""])
            i += 1
    grid = Table(rows, colWidths=[half + 9, half])
    span_cmds = [("SPAN", (0, r), (1, r)) for r, row in enumerate(rows) if row[1] == ""]
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ] + span_cmds))
    story.append(grid)
    story.append(Spacer(1, 2))

    head = _style("cth", "Pop-Bold", 8.4, INK)
    head_c = ParagraphStyle("cthc", parent=head, alignment=1)
    name_st = _style("ciname", "Pop-Medium", 8.7, INK, leading=11.4)
    note_st = _style("cinote", "Pop", 7.4, colors.HexColor("#7a7a8c"), leading=9.6)
    qty_st = _style("ciq", "Pop-Bold", 8.8, INK, align=1)
    data = [[Paragraph("Item", head), Paragraph("Qty", head_c), Paragraph("Loaded", head_c),
             Paragraph("Delivered" if not sheet.get("is_pickup") else "Released", head_c),
             Paragraph("Collected" if not sheet.get("is_pickup") else "Returned", head_c)]]
    for it in sheet.get("items", []):
        desc = [Paragraph(esc(it["name"]), name_st)]
        if it.get("note"):
            desc.append(Paragraph(esc(it["note"]), note_st))
        data.append([desc, Paragraph(qty_text(it["qty"]), qty_st), CheckBox(), CheckBox(), CheckBox()])
    widths = [CONTENT_W * 0.46, CONTENT_W * 0.1, CONTENT_W * 0.148, CONTENT_W * 0.148, CONTENT_W * 0.144]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, RULE),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    notes_inner = Paragraph("Damage / missing items notes", _style("dn", "Pop-Bold", 8.6, INK))
    story.append(RoundedBox(notes_inner, CONTENT_W, None, border=colors.HexColor("#c9c9d6"), radius=8,
                            pad_x=13, pad_y=8, dashed=True, min_height=112))

    sign_w = (CONTENT_W - 26) / 2
    lab = _style("slab", "Pop", 7.4, MUTED, leading=10)

    class SignLine(Flowable):
        def __init__(self, width):
            super().__init__()
            self.width, self.height = width, 36

        def draw(self):
            self.canv.setStrokeColor(colors.HexColor("#6b6b80"))
            self.canv.setLineWidth(0.9)
            self.canv.line(0, 0, self.width, 0)

    who = "pickup" if sheet.get("is_pickup") else "delivery"
    back = "return" if sheet.get("is_pickup") else "collection"
    sign_table = Table(
        [[SignLine(sign_w), "", SignLine(sign_w)],
         [Paragraph(f"Signature and date on {who} (items received in good condition)", lab), "",
          Paragraph(f"Signature and date on {back} (items returned)", lab)]],
        colWidths=[sign_w, 26, sign_w],
    )
    sign_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    # sign-off always sits at the bottom of the (last) page
    story.append(PinToBottom([
        Spacer(1, 14), Rule(CONTENT_W, PURPLE, 1.6), Spacer(1, 7),
        Paragraph("Customer sign-off", _style("so", "Pop-Bold", 9.2, PURPLE)),
        sign_table,
    ]))
    canvas_cls = _make_canvas_class(
        f"Headline Event Rentals · Crew Delivery Sheet #{sheet.get('number', '')} · Internal, not for customer"
    )
    doc.build(story, canvasmaker=canvas_cls)
    return buf.getvalue()


def pdf_to_png(pdf_bytes: bytes, scale: float = 3.5) -> bytes:
    """Rasterise every page and stack them vertically into one PNG."""
    import pypdfium2 as pdfium
    from PIL import Image as PILImage

    pdf = pdfium.PdfDocument(pdf_bytes)
    pages = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            pages.append(page.render(scale=scale).to_pil().convert("RGB"))
    finally:
        pdf.close()
    if not pages:
        raise RuntimeError("Empty PDF")
    gap = 12
    width = max(p.width for p in pages)
    height = sum(p.height for p in pages) + gap * (len(pages) - 1)
    sheet = PILImage.new("RGB", (width, height), (225, 225, 232) if len(pages) > 1 else (255, 255, 255))
    y = 0
    for p in pages:
        sheet.paste(p, (0, y))
        y += p.height + gap
    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()
