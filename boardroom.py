"""Headline Rentals Boardroom.

An executive, shareholder grade view of the business that sits at the front of
the Finance Hub. Everything here is read only: it never writes to the database.

Design goals
    * Answer the questions an owner gets asked in a board meeting: how big,
      how profitable, how fast are we growing, how safe is the cash, what is
      booked ahead, and where should the next dollar go.
    * Light on bandwidth: one cached data pull, one cached model per period,
      sparklines and ratio bars are inline SVG/HTML (no chart library), and only
      four Plotly charts with the mode bar switched off.
"""

from __future__ import annotations

import html
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import load_expenses, load_invoice_level
from db import fetch_dataframe

# --------------------------------------------------------------------------- #
# Palette: obsidian, champagne gold, and two quiet signal colours.
# --------------------------------------------------------------------------- #
# Headline night palette: deep ink, brand purple marks, cyan light, two quiet signal colours.
INK = "#0E0E1A"
PANEL = "#16152A"
PANEL_2 = "#1C1B33"
LINE = "rgba(255,255,255,0.08)"
TEXT = "#F4F2FF"
MUTED = "#A9A6C4"
GOLD = "#8466FA"          # primary mark colour (kept name so the model code is unchanged)
GOLD_SOFT = "rgba(132,102,250,0.18)"
CYAN = "#3FD0F5"          # light accent for labels on the dark panel
PROFIT = "#1399BD"        # validated against the dark panel with GOLD and the amber cost colour
COST = "#C97D22"
UP = "#5BD69B"
DOWN = "#FF7A7A"
SLATE = "#6B6F8A"
BRAND_GRAD = "linear-gradient(135deg, #5927E5 0%, #4F6EF7 58%, #3FD0F5 100%)"

COST_BUCKET_ORDER = [
    "Supplier re-rentals",
    "Crew & wages",
    "Fleet & fuel",
    "Marketing",
    "Platforms & software",
    "Bad debt",
    "Other operating",
]
COST_OF_SERVICE = {"Supplier re-rentals", "Crew & wages", "Fleet & fuel"}
BUCKET_COLOURS = {
    "Supplier re-rentals": COST,
    "Crew & wages": "#8E9BB5",
    "Fleet & fuel": "#5F6B85",
    "Marketing": "#D17BD9",
    "Platforms & software": PROFIT,
    "Bad debt": "#E06C75",
    "Other operating": "#4A5263",
    "Profit": GOLD,
}

PERIOD_OPTIONS = ["Trailing 12M", "Year to Date", "Last 90 Days", "Calendar Year", "All Time"]
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _compact(value: float, symbol: str = "J$") -> str:
    v = float(value or 0.0)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000_000:
        return f"{sign}{symbol}{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}{symbol}{v / 1_000_000:.2f}M"
    if v >= 10_000:
        return f"{sign}{symbol}{v / 1_000:.0f}K"
    if v >= 1_000:
        return f"{sign}{symbol}{v / 1_000:.1f}K"
    return f"{sign}{symbol}{v:,.0f}"


def _full(value: float, symbol: str = "J$") -> str:
    v = float(value or 0.0)
    return f"-{symbol}{abs(v):,.0f}" if v < 0 else f"{symbol}{v:,.0f}"


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "n/a"
    return f"{value:.{digits}f}%"


def _safe_div(a: float, b: float) -> float | None:
    try:
        if b is None or abs(float(b)) < 1e-9:
            return None
        return float(a) / float(b)
    except Exception:
        return None


def _esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #
def cost_bucket(category: object) -> str:
    c = str(category or "").strip().lower()
    if not c:
        return "Other operating"
    if "re-rent" in c or "rerent" in c or "re rent" in c or "supplier" in c:
        return "Supplier re-rentals"
    if any(k in c for k in ("wage", "labour", "labor", "crew", "staff", "salary", "payroll")):
        return "Crew & wages"
    if any(k in c for k in ("petrol", "fuel", "gas", "toll", "vehicle", "transport", "delivery", "mainten")):
        return "Fleet & fuel"
    if any(k in c for k in ("ads", "advert", "marketing", "influencer", "promo", "social", "campaign")):
        return "Marketing"
    if any(k in c for k in ("shopify", "software", "subscription", "website", "domain", "app", "hosting")):
        return "Platforms & software"
    if "bad debt" in c or "write off" in c or "write-off" in c:
        return "Bad debt"
    return "Other operating"


@st.cache_data(ttl=120, show_spinner=False)
def load_boardroom_data() -> dict[str, pd.DataFrame]:
    """One pull of everything the Boardroom needs. Cached for two minutes."""
    invoices = load_invoice_level()
    expenses = load_expenses()
    if not expenses.empty:
        kind = expenses["expense_kind"].fillna("transaction").astype(str).str.lower()
        expenses = expenses[~kind.isin(["summary_rollup", "recurring_draft"])].copy()
        cat = expenses["category"].fillna("").astype(str).str.lower()
        expenses = expenses[~cat.str.contains("inventory purchase|inventory purchases", regex=True)].copy()
        date_col = "finance_date" if "finance_date" in expenses.columns else "expense_date"
        expenses["when"] = pd.to_datetime(expenses[date_col], errors="coerce")
        expenses["bucket"] = expenses["category"].map(cost_bucket)
        expenses["amount"] = pd.to_numeric(expenses["amount"], errors="coerce").fillna(0.0)

    items = fetch_dataframe(
        """
        SELECT it.invoice_id, it.item_name, COALESCE(it.item_type,'product') AS item_type,
               COALESCE(it.quantity,0) * COALESCE(it.unit_price,0) AS line_total
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        WHERE lower(COALESCE(i.document_type,'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status,'confirmed')) = 'confirmed'
        """
    )
    pipeline = fetch_dataframe(
        """
        SELECT i.id, i.event_date, COALESCE(i.customer_name,'') AS customer_name,
               lower(COALESCE(i.document_type,'invoice')) AS document_type,
               lower(COALESCE(i.order_status,'confirmed')) AS order_status,
               COALESCE(i.amount_paid,0) AS amount_paid,
               COALESCE(i.payment_status,'') AS payment_status,
               COALESCE(SUM(it.quantity * it.unit_price),0) AS total
        FROM invoices i
        LEFT JOIN invoice_items it ON it.invoice_id = i.id
        WHERE date(i.event_date) >= date('now','localtime')
        GROUP BY i.id
        """
    )
    try:
        capex = fetch_dataframe("SELECT purchase_date, item_name, amount FROM inventory_purchases")
    except Exception:
        capex = pd.DataFrame(columns=["purchase_date", "item_name", "amount"])
    try:
        adjustments = fetch_dataframe("SELECT month, amount FROM monthly_adjustments")
    except Exception:
        adjustments = pd.DataFrame(columns=["month", "amount"])

    if not invoices.empty:
        invoices = invoices.copy()
        invoices["event_date"] = pd.to_datetime(invoices["event_date"], errors="coerce")
        invoices = invoices.dropna(subset=["event_date"])
        # Tax collected on behalf of government is not shareholder revenue.
        gct = pd.Series(dtype=float)
        if not items.empty:
            gct_mask = items["item_name"].fillna("").str.lower().str.contains(r"\bgct\b|tax", regex=True)
            gct = items[gct_mask].groupby("invoice_id")["line_total"].sum()
        invoices["gct"] = invoices["id"].map(gct).fillna(0.0)
        share_paid = np.where(
            invoices["invoice_total"] > 0, invoices["revenue"] / invoices["invoice_total"].replace(0, np.nan), 0.0
        )
        invoices["net_revenue"] = (invoices["revenue"] - invoices["gct"] * np.nan_to_num(share_paid)).clip(lower=0)
    if not pipeline.empty:
        pipeline["event_date"] = pd.to_datetime(pipeline["event_date"], errors="coerce")
    if not capex.empty:
        capex["purchase_date"] = pd.to_datetime(capex["purchase_date"], errors="coerce")
        capex["amount"] = pd.to_numeric(capex["amount"], errors="coerce").fillna(0.0)
    return {
        "invoices": invoices,
        "expenses": expenses,
        "items": items,
        "pipeline": pipeline,
        "capex": capex,
        "adjustments": adjustments,
    }


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass
class Window:
    label: str
    start: date
    end: date
    prior_start: date | None
    prior_end: date | None
    prior_label: str


def resolve_window(choice: str, today: date, year: int | None, first_day: date) -> Window:
    if str(choice).startswith("Month:"):
        per = pd.Period(str(choice).split(":", 1)[1], freq="M")
        start, end = per.start_time.date(), min(per.end_time.date(), today)
        prev = per - 1
        return Window(f"Month of {per.strftime('%B %Y')}", start, end, prev.start_time.date(), prev.end_time.date(),
                      prev.strftime("%B %Y"))
    if choice == "Year to Date":
        start = date(today.year, 1, 1)
        p_start, p_end = date(today.year - 1, 1, 1), today.replace(year=today.year - 1)
        return Window(f"Year to date {today.year}", start, today, p_start, p_end, f"same period {today.year - 1}")
    if choice == "Last 90 Days":
        start = today - timedelta(days=89)
        return Window("Last 90 days", start, today, start - timedelta(days=90), start - timedelta(days=1), "previous 90 days")
    if choice == "Calendar Year":
        y = int(year or today.year)
        end = min(date(y, 12, 31), today) if y == today.year else date(y, 12, 31)
        return Window(f"Calendar year {y}", date(y, 1, 1), end, date(y - 1, 1, 1), date(y - 1, 12, 31), str(y - 1))
    if choice == "All Time":
        return Window("All time", first_day, today, None, None, "")
    start = (pd.Timestamp(today) - pd.DateOffset(months=12) + pd.Timedelta(days=1)).date()
    p_end = start - timedelta(days=1)
    p_start = (pd.Timestamp(p_end) - pd.DateOffset(months=12) + pd.Timedelta(days=1)).date()
    return Window("Trailing 12 months", start, today, p_start, p_end, "prior 12 months")


def _slice(df: pd.DataFrame, col: str, start: date | None, end: date | None) -> pd.DataFrame:
    if df is None or df.empty or start is None or end is None:
        return df.iloc[0:0] if isinstance(df, pd.DataFrame) else pd.DataFrame()
    d = pd.to_datetime(df[col], errors="coerce").dt.date
    return df[(d >= start) & (d <= end)]


def _pnl(inv: pd.DataFrame, exp: pd.DataFrame) -> dict[str, float]:
    revenue = float(inv["net_revenue"].sum()) if not inv.empty else 0.0
    billed = float(inv["invoice_total"].sum()) if not inv.empty else 0.0
    collected = float(inv["amount_paid"].sum()) if not inv.empty else 0.0
    outstanding = float(inv["amount_outstanding"].sum()) if not inv.empty else 0.0
    orders = int((inv["invoice_total"] > 0).sum()) if not inv.empty else 0
    buckets = {b: 0.0 for b in COST_BUCKET_ORDER}
    if not exp.empty:
        for b, amt in exp.groupby("bucket")["amount"].sum().items():
            buckets[b] = buckets.get(b, 0.0) + float(amt)
    cos = sum(v for k, v in buckets.items() if k in COST_OF_SERVICE)
    opex = sum(v for k, v in buckets.items() if k not in COST_OF_SERVICE)
    gross = revenue - cos
    operating = gross - opex
    return {
        "revenue": revenue,
        "billed": billed,
        "collected": collected,
        "outstanding": outstanding,
        "orders": orders,
        "aov": revenue / orders if orders else 0.0,
        "cost_of_service": cos,
        "opex": opex,
        "gross": gross,
        "operating": operating,
        "gross_margin": (_safe_div(gross, revenue) or 0.0) * 100 if revenue else None,
        "operating_margin": (_safe_div(operating, revenue) or 0.0) * 100 if revenue else None,
        **{f"b::{k}": v for k, v in buckets.items()},
    }


def _monthly_frame(inv: pd.DataFrame, exp: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    frame = pd.DataFrame({"period": months})
    rev = (
        inv.assign(period=inv["event_date"].dt.to_period("M")).groupby("period")["net_revenue"].sum()
        if not inv.empty
        else pd.Series(dtype=float)
    )
    orders = (
        inv[inv["invoice_total"] > 0].assign(period=lambda d: d["event_date"].dt.to_period("M")).groupby("period")["id"].count()
        if not inv.empty
        else pd.Series(dtype=float)
    )
    cos = pd.Series(dtype=float)
    opx = pd.Series(dtype=float)
    if not exp.empty:
        e = exp.dropna(subset=["when"]).assign(period=lambda d: d["when"].dt.to_period("M"))
        cos = e[e["bucket"].isin(COST_OF_SERVICE)].groupby("period")["amount"].sum()
        opx = e[~e["bucket"].isin(COST_OF_SERVICE)].groupby("period")["amount"].sum()
    frame["revenue"] = frame["period"].map(rev).fillna(0.0)
    frame["orders"] = frame["period"].map(orders).fillna(0).astype(int)
    frame["cost_of_service"] = frame["period"].map(cos).fillna(0.0)
    frame["opex"] = frame["period"].map(opx).fillna(0.0)
    frame["operating"] = frame["revenue"] - frame["cost_of_service"] - frame["opex"]
    frame["label"] = frame["period"].dt.strftime("%b %y")
    return frame


@st.cache_data(ttl=120, show_spinner=False)
def build_model(choice: str, year: int | None, today_iso: str) -> dict:
    data = load_boardroom_data()
    inv_all: pd.DataFrame = data["invoices"]
    exp_all: pd.DataFrame = data["expenses"]
    today = date.fromisoformat(today_iso)
    past_inv = inv_all[inv_all["event_date"].dt.date <= today] if not inv_all.empty else inv_all
    first_day = past_inv["event_date"].min().date() if not past_inv.empty else today.replace(day=1)
    win = resolve_window(choice, today, year, first_day)

    inv = _slice(inv_all, "event_date", win.start, win.end)
    exp = _slice(exp_all, "when", win.start, win.end) if not exp_all.empty else exp_all
    cur = _pnl(inv, exp)
    prior = None
    if win.prior_start:
        p_inv = _slice(inv_all, "event_date", win.prior_start, win.prior_end)
        p_exp = _slice(exp_all, "when", win.prior_start, win.prior_end) if not exp_all.empty else exp_all
        prior = _pnl(p_inv, p_exp)
        if prior["revenue"] <= 0 and prior["orders"] == 0:
            prior = None

    # Monthly series for the chart: always show at least 12 months of context.
    chart_start = min(win.start, (pd.Timestamp(win.end) - pd.DateOffset(months=11)).date().replace(day=1))
    monthly = _monthly_frame(
        _slice(inv_all, "event_date", chart_start, win.end),
        _slice(exp_all, "when", chart_start, win.end) if not exp_all.empty else exp_all,
        chart_start,
        win.end,
    )
    monthly["in_window"] = monthly["period"].apply(lambda p: p.end_time.date() >= win.start)
    monthly["rolling"] = monthly["revenue"].rolling(3, min_periods=1).mean()

    # Spark series inside the window.
    spark = monthly[monthly["in_window"]]

    # Clients.
    named = inv[inv["customer_name"].fillna("").str.strip() != ""] if not inv.empty else inv
    client_rev = (
        named.assign(client=named["customer_name"].str.strip().str.strip(",.;: ").str.title())
        .groupby("client")["net_revenue"].sum().sort_values(ascending=False)
        if not named.empty
        else pd.Series(dtype=float)
    )
    all_named = inv_all[inv_all["customer_name"].fillna("").str.strip() != ""] if not inv_all.empty else inv_all
    lifetime_orders = (
        all_named.assign(client=all_named["customer_name"].str.strip().str.strip(",.;: ").str.title()).groupby("client")["id"].count()
        if not all_named.empty
        else pd.Series(dtype=int)
    )
    window_clients = list(client_rev.index)
    repeat_clients = int(sum(1 for c in window_clients if lifetime_orders.get(c, 0) >= 2))
    top5_share = _safe_div(float(client_rev.head(5).sum()), cur["revenue"])
    named_share = _safe_div(len(named), len(inv)) if len(inv) else None

    # Seasonality (all history, ex future).
    season = pd.DataFrame()
    if not past_inv.empty:
        season = (
            past_inv.assign(y=past_inv["event_date"].dt.year, m=past_inv["event_date"].dt.month)
            .groupby(["y", "m"])["net_revenue"].sum().unstack(fill_value=0.0)
            .reindex(columns=range(1, 13), fill_value=0.0)
        )

    # Forward book.
    pipe: pd.DataFrame = data["pipeline"]
    book = {"confirmed": 0.0, "confirmed_n": 0, "deposits": 0.0, "pending": 0.0, "pending_n": 0, "quotes": 0.0, "quotes_n": 0}
    book_by_month = pd.DataFrame(columns=["period", "confirmed", "pending"])
    if not pipe.empty:
        p = pipe.copy()
        is_conf = (p["document_type"] == "invoice") & (p["order_status"] == "confirmed")
        is_pend = (p["document_type"] == "invoice") & (p["order_status"] != "confirmed")
        is_quote = p["document_type"] == "quote"
        book.update(
            confirmed=float(p.loc[is_conf, "total"].sum()),
            confirmed_n=int(is_conf.sum()),
            deposits=float(p.loc[is_conf, "amount_paid"].clip(lower=0).sum()),
            pending=float(p.loc[is_pend, "total"].sum()),
            pending_n=int(is_pend.sum()),
            quotes=float(p.loc[is_quote, "total"].sum()),
            quotes_n=int(is_quote.sum()),
        )
        p["period"] = p["event_date"].dt.to_period("M")
        book_by_month = pd.DataFrame(
            {
                "confirmed": p[is_conf].groupby("period")["total"].sum(),
                "pending": p[is_pend | is_quote].groupby("period")["total"].sum(),
            }
        ).fillna(0.0).reset_index()

    # Forecast: seasonal naive blended with recent run rate, next 3 months.
    forecast = []
    monthly_hist = _monthly_frame(past_inv, exp_all.iloc[0:0] if not exp_all.empty else exp_all, first_day, today) if not past_inv.empty else pd.DataFrame()
    recent = monthly_hist.tail(6)["revenue"] if not monthly_hist.empty else pd.Series(dtype=float)
    run_rate = float(recent.mean()) if len(recent) else 0.0
    for k in range(0, 3):
        per = pd.Period(today, "M") + k
        same_months = season[per.month] if not season.empty and per.month in season.columns else pd.Series(dtype=float)
        seasonal = float(same_months[same_months > 0].mean()) if (same_months > 0).any() else None
        base = (0.6 * seasonal + 0.4 * run_rate) if seasonal is not None else run_rate
        booked = float(book_by_month.loc[book_by_month["period"] == per, "confirmed"].sum()) if not book_by_month.empty else 0.0
        base = max(base, booked)
        forecast.append({"period": per, "label": per.strftime("%b %y"), "base": base, "low": max(booked, base * 0.7), "high": base * 1.3, "booked": booked})

    # Capital invested in fleet and payback.
    capex_df: pd.DataFrame = data["capex"]
    capex_window = float(_slice(capex_df, "purchase_date", win.start, win.end)["amount"].sum()) if not capex_df.empty else 0.0
    capex_total = float(capex_df["amount"].sum()) if not capex_df.empty else 0.0

    # Data confidence.
    items: pd.DataFrame = data["items"]
    itemised = 0.0
    if not inv.empty and not items.empty:
        legacy_ids = set(items.loc[items["item_name"].fillna("").str.lower().str.contains("imported invoice revenue"), "invoice_id"])
        itemised = 1 - (inv["id"].isin(legacy_ids).sum() / max(1, len(inv)))
    rev_months = set(monthly.loc[monthly["in_window"] & (monthly["revenue"] > 0), "period"])
    exp_months = set(monthly.loc[monthly["in_window"] & ((monthly["cost_of_service"] + monthly["opex"]) > 0), "period"])
    expense_cover = _safe_div(len(rev_months & exp_months), len(rev_months)) if rev_months else None
    confidence_parts = {
        "Orders with a client name": named_share,
        "Orders with item level detail": itemised if len(inv) else None,
        "Revenue months with costs logged": expense_cover,
    }
    valid_parts = [v for v in confidence_parts.values() if v is not None]
    confidence = float(np.mean(valid_parts)) * 100 if valid_parts else None

    # Product mix (itemised lines only).
    mix = pd.Series(dtype=float)
    if not items.empty and not inv.empty:
        it = items[items["invoice_id"].isin(inv["id"])].copy()
        name = it["item_name"].fillna("").str.lower()
        it = it[~name.str.contains("imported invoice revenue|gct|tax", regex=True)]
        if not it.empty:
            def _group(n: str) -> str:
                n = n.lower()
                if any(k in n for k in ("delivery", "setup", "set-up", "set up", "labour", "multiplier", "surcharge", "fee")):
                    return "Service fees"
                if any(k in n for k in ("tent", "canopy", "marquee")):
                    return "Tents"
                if any(k in n for k in ("table", "chair", "linen", "cover", "sash")):
                    return "Tables, chairs & linen"
                if any(k in n for k in ("glass", "plate", "cutlery", "charger", "decor", "centre", "center")):
                    return "Tableware & decor"
                if any(k in n for k in ("machine", "popcorn", "snow", "hotdog", "cotton", "trampoline", "bounce", "booth", "360")):
                    return "Entertainment & machines"
                return "Other rentals"
            it["group"] = it["item_name"].fillna("").map(_group)
            mix = it.groupby("group")["line_total"].sum().sort_values(ascending=False)

    # Health index (transparent composite, 0 to 100).
    def _band(x: float | None, lo: float, hi: float) -> float | None:
        if x is None:
            return None
        return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))

    growth = _safe_div(cur["revenue"] - prior["revenue"], prior["revenue"]) if prior else None
    health_parts = {
        "Profitability": _band(cur["operating_margin"], 0, 40),
        "Growth": _band((growth or 0) * 100, -20, 30) if growth is not None else None,
        "Cash conversion": _band((_safe_div(cur["collected"], cur["billed"]) or 0) * 100, 70, 100) if cur["billed"] else None,
        "Client spread": _band(100 - (top5_share or 0) * 100, 30, 80) if top5_share is not None else None,
        "Data confidence": _band(confidence, 0, 100) if confidence is not None else None,
    }
    hv = [v for v in health_parts.values() if v is not None]
    health = round(float(np.mean(hv)) * 100) if hv else None

    best = monthly[monthly["in_window"]].sort_values("revenue", ascending=False).head(1)
    return {
        "window": win,
        "cur": cur,
        "prior": prior,
        "growth": growth,
        "monthly": monthly,
        "spark": spark,
        "client_rev": client_rev,
        "client_count": len(window_clients),
        "repeat_clients": repeat_clients,
        "top5_share": top5_share,
        "season": season,
        "book": book,
        "forecast": forecast,
        "run_rate": run_rate,
        "capex_window": capex_window,
        "capex_total": capex_total,
        "confidence": confidence,
        "confidence_parts": confidence_parts,
        "mix": mix,
        "health": health,
        "health_parts": health_parts,
        "best_month": (best["label"].iloc[0], float(best["revenue"].iloc[0])) if not best.empty and float(best["revenue"].iloc[0]) > 0 else None,
        "active_months": int((monthly[monthly["in_window"]]["revenue"] > 0).sum()),
        "window_months": int(monthly["in_window"].sum()),
        "years": sorted({int(y) for y in past_inv["event_date"].dt.year.unique()}, reverse=True) if not past_inv.empty else [today.year],
    }


# --------------------------------------------------------------------------- #
# Narrative and recommendations
# --------------------------------------------------------------------------- #
def chairman_summary(m: dict, sym: str) -> str:
    c, p, w = m["cur"], m["prior"], m["window"]
    if c["revenue"] <= 0:
        return (
            f"No confirmed revenue has been recorded for the {w.label.lower()} yet. "
            "Confirmed orders and logged costs will populate this view automatically."
        )
    if w.label == "All time":
        opener = "Since records began"
    elif w.label.startswith("Month of "):
        opener = f"In {w.label[len('Month of '):]}"
    else:
        opener = f"Over the {w.label.lower()}"
    parts = [f"{opener}, Headline delivered {_full(c['revenue'], sym)} of net revenue across {c['orders']} confirmed events"]
    if m["growth"] is not None:
        direction = "up" if m["growth"] >= 0 else "down"
        parts[0] += f", {direction} {abs(m['growth']) * 100:.0f}% on the {w.prior_label}"
    parts[0] += "."
    if c["operating_margin"] is not None:
        parts.append(
            f"The business kept {_full(c['operating'], sym)} as operating profit, an operating margin of {c['operating_margin']:.0f}%, "
            f"with an average event worth {_full(c['aov'], sym)}."
        )
    rr = c["b::Supplier re-rentals"]
    if c["revenue"] and rr / c["revenue"] > 0.15:
        parts.append(
            f"Supplier re-rentals absorbed {rr / c['revenue'] * 100:.0f}% of revenue, the clearest lever for margin expansion through owned inventory."
        )
    b = m["book"]
    if b["confirmed"] > 0:
        parts.append(f"Looking ahead, {_full(b['confirmed'], sym)} is already confirmed on the books across {b['confirmed_n']} upcoming events.")
    elif m["forecast"]:
        nxt = sum(f["base"] for f in m["forecast"])
        parts.append(f"The indicative outlook for the next quarter is about {_compact(nxt, sym)}, based on seasonality and the recent run rate.")
    return " ".join(parts)


def recommendations(m: dict, sym: str) -> list[tuple[str, str, str]]:
    """Returns (tone, title, body). tone is 'gold', 'up' or 'down'."""
    c = m["cur"]
    out: list[tuple[str, str, str]] = []
    rev = c["revenue"]
    if rev > 0:
        rr = c["b::Supplier re-rentals"]
        if rr / rev > 0.15:
            out.append((
                "gold",
                "Own what you keep renting",
                f"{_full(rr, sym)} went to suppliers this period ({rr / rev * 100:.0f}% of revenue). Log the item on each re-rental and the app can rank which lines pay back fastest if bought.",
            ))
        mk = c["b::Marketing"]
        if mk > 0:
            roms = rev / mk
            tone = "up" if roms >= 5 else "down"
            out.append((tone, "Marketing return", f"Every {sym}1 of marketing sat alongside {sym}{roms:,.1f} of revenue. Tag each order with its lead source to prove which channel earns it."))
        if m["top5_share"] is not None and m["top5_share"] > 0.4:
            out.append(("down", "Client concentration", f"Your top five clients are {m['top5_share'] * 100:.0f}% of named revenue. Shareholders read that as risk; a corporate retainer push spreads it."))
        if m["window_months"] >= 6 and m["active_months"] / max(1, m["window_months"]) < 0.75:
            quiet = m["window_months"] - m["active_months"]
            out.append(("gold", "Quiet months", f"{quiet} of {m['window_months']} months had no confirmed revenue. A low season package (schools, churches, corporate) can smooth the curve."))
        coll = _safe_div(c["collected"], c["billed"])
        if coll is not None and coll < 0.95 and c["outstanding"] > 0:
            out.append(("down", "Cash still in the field", f"{_full(c['outstanding'], sym)} is billed but not collected. The Deposit Due Tracker is the fastest route to closing it."))
    if m["confidence"] is not None and m["confidence"] < 80:
        weakest = min(((k, v) for k, v in m["confidence_parts"].items() if v is not None), key=lambda kv: kv[1], default=None)
        if weakest:
            out.append(("gold", "Sharpen the numbers", f"Data confidence is {m['confidence']:.0f}/100. Weakest area: {weakest[0].lower()} ({weakest[1] * 100:.0f}%). Fixing it makes every chart here investor grade."))
    if not out:
        out.append(("up", "On course", "Margins, cash and client spread are all within healthy ranges for this period."))
    return out[:5]


# --------------------------------------------------------------------------- #
# Visual atoms (pure HTML/SVG, zero chart library cost)
# --------------------------------------------------------------------------- #
def _svg_spark(values: list[float], colour: str = GOLD, w: int = 120, h: int = 34) -> str:
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    step = w / (len(vals) - 1)
    pts = [(i * step, h - 3 - (v - lo) / rng * (h - 6)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{h} " + line + f" {w},{h}"
    gid = f"g{abs(hash((tuple(vals), colour))) % 10_000_000}"
    return (
        f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' preserveAspectRatio='none' aria-hidden='true'>"
        f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0' stop-color='{colour}' stop-opacity='0.35'/><stop offset='1' stop-color='{colour}' stop-opacity='0'/></linearGradient></defs>"
        f"<polygon points='{area}' fill='url(#{gid})'/>"
        f"<polyline points='{line}' fill='none' stroke='{colour}' stroke-width='1.6' stroke-linejoin='round' stroke-linecap='round'/>"
        f"<circle cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='2.4' fill='{colour}'/></svg>"
    )


def _delta_chip(cur: float | None, prev: float | None, *, pts: bool = False, invert: bool = False) -> str:
    if cur is None or prev is None:
        return ""
    if pts:
        diff = cur - prev
        txt = f"{'+' if diff >= 0 else ''}{diff:.1f} pts"
        good = diff >= 0
    else:
        if abs(prev) < 1e-9:
            return ""
        ch = (cur - prev) / abs(prev) * 100
        txt = f"{'+' if ch >= 0 else ''}{ch:.0f}%"
        good = ch >= 0
    if invert:
        good = not good
    cls = "br-up" if good else "br-down"
    arrow = "&#9650;" if (cur - prev) >= 0 else "&#9660;"
    return f"<span class='br-delta {cls}'>{arrow} {txt}</span>"


def _kpi_tile(label: str, value: str, delta_html: str, foot: str, spark: str = "") -> str:
    return (
        "<div class='br-kpi'>"
        f"<div class='br-kpi-label'>{_esc(label)}</div>"
        f"<div class='br-kpi-value'>{value}</div>"
        f"<div class='br-kpi-meta'>{delta_html}<span class='br-kpi-foot'>{_esc(foot)}</span></div>"
        f"<div class='br-kpi-spark'>{spark}</div>"
        "</div>"
    )


def _gauge(score: int | None) -> str:
    if score is None:
        return ""
    r = 52
    circ = 2 * math.pi * r
    filled = circ * score / 100
    colour = UP if score >= 70 else ("#F0B54B" if score >= 45 else DOWN)
    return (
        "<svg viewBox='0 0 130 130' width='124' height='124' aria-label='Health index'>"
        f"<circle cx='65' cy='65' r='{r}' fill='none' stroke='rgba(255,255,255,0.07)' stroke-width='9'/>"
        f"<circle cx='65' cy='65' r='{r}' fill='none' stroke='{colour}' stroke-width='9' stroke-linecap='round' "
        f"stroke-dasharray='{filled:.1f} {circ:.1f}' transform='rotate(-90 65 65)'/>"
        f"<text x='65' y='67' text-anchor='middle' class='br-gauge-num'>{score}</text>"
        "<text x='65' y='87' text-anchor='middle' class='br-gauge-sub'>of 100</text></svg>"
    )


# --------------------------------------------------------------------------- #
# Charts (Plotly, dark, minimal)
# --------------------------------------------------------------------------- #
def _base_layout(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Plus Jakarta Sans, Avenir Next, Segoe UI, sans-serif", "color": MUTED, "size": 12},
        margin={"l": 8, "r": 8, "t": 40, "b": 8},
        showlegend=True,
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0, "bgcolor": "rgba(0,0,0,0)", "font": {"color": MUTED}},
        hoverlabel={"bgcolor": PANEL_2, "bordercolor": GOLD, "font": {"color": TEXT, "family": "Plus Jakarta Sans, sans-serif"}},
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=LINE, tickfont={"color": MUTED})
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)", zeroline=False, tickfont={"color": MUTED})
    return fig


def chart_revenue_engine(m: dict, sym: str) -> go.Figure:
    mf = m["monthly"]
    colours = [GOLD if inw else "rgba(132,102,250,0.28)" for inw in mf["in_window"]]
    fig = go.Figure()
    fig.add_bar(
        x=mf["label"], y=mf["revenue"], name="Net revenue", marker={"color": colours, "line": {"width": 0}},
        hovertemplate=f"%{{x}}<br>Revenue {sym}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=mf["label"], y=mf["operating"], name="Operating profit", mode="lines+markers",
        line={"color": CYAN, "width": 2}, marker={"size": 6},
        hovertemplate=f"%{{x}}<br>Operating profit {sym}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=mf["label"], y=mf["rolling"], name="3 month average", mode="lines",
        line={"color": "rgba(244,241,234,0.55)", "width": 1.4, "dash": "dot"},
        hovertemplate=f"%{{x}}<br>3M avg {sym}%{{y:,.0f}}<extra></extra>",
    )
    _base_layout(fig, 330)
    fig.update_yaxes(tickprefix=sym, tickformat="~s")
    return fig


def chart_bridge(m: dict, sym: str) -> go.Figure:
    c = m["cur"]
    short = {
        "Supplier re-rentals": "Suppliers", "Crew & wages": "Crew", "Fleet & fuel": "Fleet",
        "Marketing": "Marketing", "Platforms & software": "Platforms", "Bad debt": "Bad debt", "Other operating": "Other",
    }
    labels = ["Revenue"]
    values = [c["revenue"]]
    measures = ["absolute"]
    for b in COST_BUCKET_ORDER:
        v = c[f"b::{b}"]
        if v > 0:
            labels.append(short.get(b, b))
            values.append(-v)
            measures.append("relative")
    labels.append("Profit")
    values.append(0)
    measures.append("total")
    fig = go.Figure(
        go.Waterfall(
            x=labels, y=values, measure=measures,
            connector={"line": {"color": "rgba(255,255,255,0.12)", "width": 1}},
            increasing={"marker": {"color": UP}},
            decreasing={"marker": {"color": COST}},
            totals={"marker": {"color": GOLD if c["operating"] >= 0 else DOWN}},
            hovertemplate=f"%{{x}}<br>{sym}%{{y:,.0f}}<extra></extra>",
            textposition="outside",
            text=[_compact(abs(v) if v else c["operating"], sym) for v in values],
            textfont={"color": TEXT, "size": 11},
        )
    )
    _base_layout(fig, 330)
    fig.update_layout(showlegend=False)
    fig.update_yaxes(tickprefix=sym, tickformat="~s")
    return fig


def chart_seasonality(m: dict, sym: str) -> go.Figure | None:
    s: pd.DataFrame = m["season"]
    if s.empty:
        return None
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    z = s.values
    fig = go.Figure(
        go.Heatmap(
            z=z, x=months, y=[str(y) for y in s.index],
            colorscale=[[0, "rgba(255,255,255,0.03)"], [0.0001, "#241D4A"], [0.5, "#5B3FD0"], [1, "#B7A6FF"]],
            showscale=False, xgap=3, ygap=3,
            hovertemplate=f"%{{x}} %{{y}}<br>{sym}%{{z:,.0f}}<extra></extra>",
        )
    )
    _base_layout(fig, 60 + 46 * len(s.index))
    fig.update_layout(showlegend=False)
    fig.update_yaxes(showgrid=False, autorange="reversed", type="category")
    return fig


def chart_outlook(m: dict, sym: str) -> go.Figure:
    fc = m["forecast"]
    x = [f["label"] for f in fc]
    fig = go.Figure()
    fig.add_bar(x=x, y=[f["booked"] for f in fc], name="Already booked", marker={"color": GOLD},
                hovertemplate=f"%{{x}}<br>Booked {sym}%{{y:,.0f}}<extra></extra>")
    fig.add_bar(x=x, y=[max(0.0, f["base"] - f["booked"]) for f in fc], name="Expected to win",
                marker={"color": "rgba(132,102,250,0.22)", "line": {"color": GOLD, "width": 1}},
                hovertemplate=f"%{{x}}<br>Expected {sym}%{{y:,.0f}}<extra></extra>")
    fig.add_scatter(
        x=x, y=[f["high"] for f in fc], mode="markers", name="Upside",
        marker={"symbol": "line-ew", "size": 22, "line": {"color": "rgba(244,241,234,0.5)", "width": 2}},
        hovertemplate=f"%{{x}}<br>Upside {sym}%{{y:,.0f}}<extra></extra>",
    )
    _base_layout(fig, 300)
    fig.update_layout(barmode="stack")
    fig.update_yaxes(tickprefix=sym, tickformat="~s")
    return fig


# --------------------------------------------------------------------------- #
# Board pack PDF
# --------------------------------------------------------------------------- #
def build_board_pack_pdf(m: dict, sym: str, business_name: str, logo_path: Path | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    ink = colors.HexColor(INK)
    gold = colors.HexColor("#5927E5")
    grey = colors.HexColor("#52526A")
    try:
        import document_pdf as _D
        f_reg, f_bold = _D.F("Pop"), _D.F("Pop-Bold")
    except Exception:
        f_reg, f_bold = "Helvetica", "Helvetica-Bold"

    pdf.setFillColor(ink)
    pdf.rect(0, H - 52 * mm, W, 52 * mm, stroke=0, fill=1)
    steps = 60
    for i in range(steps):
        t = i / (steps - 1)
        r = 0x59 + (0x3F - 0x59) * t; g = 0x27 + (0xD0 - 0x27) * t; b = 0xE5 + (0xF5 - 0xE5) * t
        pdf.setFillColorRGB(r / 255, g / 255, b / 255)
        pdf.rect(i * W / steps, H - 52 * mm, W / steps + 0.5, 1.6 * mm, stroke=0, fill=1)
    if logo_path and Path(logo_path).exists():
        try:
            pdf.drawImage(str(logo_path), 18 * mm, H - 40 * mm, width=22 * mm, height=22 * mm, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass
    pdf.setFillColor(colors.white)
    pdf.setFont(f_bold, 20)
    pdf.drawString(46 * mm, H - 26 * mm, f"{business_name}  |  Board Pack")
    pdf.setFillColor(colors.HexColor("#8FE3FA"))
    pdf.setFont(f_reg, 11)
    pdf.drawString(46 * mm, H - 34 * mm, f"{m['window'].label}  ({m['window'].start:%d %b %Y} to {m['window'].end:%d %b %Y})")
    pdf.setFillColor(colors.HexColor("#C9CDD6"))
    pdf.setFont(f_reg, 9)
    pdf.drawString(46 * mm, H - 41 * mm, f"Prepared {datetime.now():%d %B %Y}  |  Confidential")

    c, p = m["cur"], m["prior"]
    tiles = [
        ("Net revenue", _compact(c["revenue"], sym)),
        ("Operating profit", _compact(c["operating"], sym)),
        ("Operating margin", _pct(c["operating_margin"], 0)),
        ("Confirmed events", f"{c['orders']:,}"),
        ("Average event", _compact(c["aov"], sym)),
        ("Health index", f"{m['health']}/100" if m["health"] is not None else "n/a"),
    ]
    y = H - 62 * mm
    tw = (W - 36 * mm - 10 * mm) / 3
    for i, (lab, val) in enumerate(tiles):
        col, row = i % 3, i // 3
        x0 = 18 * mm + col * (tw + 5 * mm)
        y0 = y - row * 24 * mm
        pdf.setStrokeColor(colors.HexColor("#E5E7EB"))
        pdf.setFillColor(colors.HexColor("#F8F8FC"))
        pdf.roundRect(x0, y0 - 18 * mm, tw, 19 * mm, 3 * mm, stroke=1, fill=1)
        pdf.setFillColor(grey)
        pdf.setFont(f_reg, 8.5)
        pdf.drawString(x0 + 4 * mm, y0 - 5.5 * mm, lab.upper())
        pdf.setFillColor(ink)
        pdf.setFont(f_bold, 16)
        pdf.drawString(x0 + 4 * mm, y0 - 14 * mm, val)

    # Summary paragraph.
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    style = ParagraphStyle("b", fontName=f_reg, fontSize=10.5, leading=15, textColor=ink)
    head = ParagraphStyle("h", fontName=f_bold, fontSize=12, leading=16, textColor=ink)
    y_cursor = y - 2 * 24 * mm - 4 * mm
    for title, body in (("Chairman's summary", chairman_summary(m, sym)),):
        ph = Paragraph(title, head)
        _, hh = ph.wrap(W - 36 * mm, 50)
        ph.drawOn(pdf, 18 * mm, y_cursor - hh)
        y_cursor -= hh + 2 * mm
        pb = Paragraph(_esc(body), style)
        _, bh = pb.wrap(W - 36 * mm, 200)
        pb.drawOn(pdf, 18 * mm, y_cursor - bh)
        y_cursor -= bh + 7 * mm

    # Income statement.
    rows = [["Income statement", "This period", "Prior period", "% of revenue"]]

    def _row(label: str, key: str, sign: int = 1) -> None:
        cv = c[key] * sign
        pv = (p[key] * sign) if p else None
        sv = (_safe_div(c[key], c["revenue"]) or 0) * 100
        share = (_pct(sv, 1) if 0 < abs(sv) < 1 else _pct(sv, 0)) if c["revenue"] else ""
        rows.append([label, _full(cv, sym), _full(pv, sym) if pv is not None else "", share])

    _row("Net revenue (ex GCT)", "revenue")
    for b in COST_BUCKET_ORDER:
        if b in COST_OF_SERVICE:
            _row(f"   {b}", f"b::{b}", -1)
    _row("Gross profit", "gross")
    for b in COST_BUCKET_ORDER:
        if b not in COST_OF_SERVICE:
            _row(f"   {b}", f"b::{b}", -1)
    _row("Operating profit", "operating")
    t = Table(rows, colWidths=[70 * mm, 36 * mm, 36 * mm, 30 * mm])
    bold_rows = [i for i, r in enumerate(rows) if r[0] in ("Net revenue (ex GCT)", "Gross profit", "Operating profit")]
    ts = [
        ("FONT", (0, 0), (-1, -1), f_reg, 9.5),
        ("FONT", (0, 0), (-1, 0), f_bold, 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), gold),
        ("BACKGROUND", (0, 0), (-1, 0), ink),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in bold_rows:
        ts.append(("FONT", (0, i), (-1, i), f_bold, 9.5))
        ts.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F1EEFE")))
    t.setStyle(TableStyle(ts))
    _, th = t.wrap(W, H)
    t.drawOn(pdf, 18 * mm, y_cursor - th)
    y_cursor -= th + 8 * mm

    ph = Paragraph("Where we act next", head)
    _, hh = ph.wrap(W - 36 * mm, 50)
    if y_cursor - hh < 40 * mm:
        pdf.showPage()
        y_cursor = H - 20 * mm
    ph.drawOn(pdf, 18 * mm, y_cursor - hh)
    y_cursor -= hh + 2 * mm
    for _, title, body in recommendations(m, sym):
        pb = Paragraph(f"<b>{_esc(title)}.</b> {_esc(body)}", style)
        _, bh = pb.wrap(W - 36 * mm, 200)
        if y_cursor - bh < 18 * mm:
            pdf.showPage()
            y_cursor = H - 20 * mm
        pb.drawOn(pdf, 18 * mm, y_cursor - bh)
        y_cursor -= bh + 3 * mm

    pdf.setFillColor(grey)
    pdf.setFont(f_reg, 7.5)
    pdf.drawString(18 * mm, 10 * mm, "Revenue is shown net of GCT. Forecasts are indicative and based on seasonality and recent run rate.")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


@st.cache_data(ttl=300, show_spinner=False, max_entries=8)
def _board_pack_cached(choice: str, year: int | None, today_iso: str, sym: str, business_name: str, logo: str) -> bytes:
    m = build_model(choice, year, today_iso)
    return build_board_pack_pdf(m, sym, business_name, Path(logo) if logo else None)


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
BOARDROOM_CSS = f"""
<style>
.st-key-hr_boardroom {{
  background: radial-gradient(900px 380px at 92% -12%, rgba(63,208,245,0.16), transparent 60%),
              radial-gradient(800px 420px at -8% 110%, rgba(89,39,229,0.32), transparent 60%), {INK};
  border: 1px solid rgba(132,102,250,0.22);
  border-radius: 28px;
  padding: 28px 28px 22px 28px;
  box-shadow: 0 30px 80px rgba(11,13,18,0.35);
  color: {TEXT};
  box-sizing: border-box; max-width: 100%; overflow: hidden;
}}
.st-key-hr_boardroom * {{ color: inherit; box-sizing: border-box; }}
.br-grid > * {{ min-width: 0; }}
.st-key-hr_boardroom [data-testid="stMarkdownContainer"] p {{ color: {MUTED}; }}
.st-key-hr_boardroom label, .st-key-hr_boardroom [data-testid="stWidgetLabel"] p {{ color: {MUTED} !important; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }}
.st-key-hr_boardroom [data-baseweb="select"] > div {{ background: {PANEL_2} !important; border-color: {LINE} !important; color: {TEXT} !important; }}
.st-key-hr_boardroom [data-baseweb="select"] svg {{ fill: {MUTED}; }}
.st-key-hr_boardroom button[data-variant="segmented_control"] {{
  background: {PANEL_2} !important; border: 1px solid {LINE} !important; color: {MUTED} !important; font-weight: 600;
  transition: all .2s ease;
}}
.st-key-hr_boardroom button[data-variant="segmented_control"] p {{ color: {MUTED} !important; font-size: 13px; }}
.st-key-hr_boardroom button[data-variant="segmented_control"]:hover {{ border-color: rgba(132,102,250,0.6) !important; }}
.st-key-hr_boardroom button[data-variant="segmented_control"][aria-checked="true"] {{
  background: {BRAND_GRAD} !important; border-color: transparent !important;
  box-shadow: 0 6px 18px rgba(89,39,229,0.35);
}}
.st-key-hr_boardroom button[data-variant="segmented_control"][aria-checked="true"] p {{ color: #FFFFFF !important; }}
.st-key-hr_boardroom [data-testid="stButtonGroup"] {{ margin: 14px 0 4px; }}
.st-key-hr_boardroom [data-testid="stButtonGroup"] [role="radiogroup"] {{ flex-wrap: wrap; gap: 8px; }}
.st-key-hr_boardroom [data-testid="stButtonGroup"] button {{ border-radius: 999px !important; padding: 4px 16px !important; min-height: 34px; }}
.st-key-hr_boardroom [data-testid="stBaseButton-segmented_controlActive"] * {{ color: #FFFFFF !important; }}
.st-key-hr_boardroom [data-testid="stButtonGroup"] {{ background: transparent !important; padding: 0 !important; width: auto !important; }}
.st-key-hr_boardroom .stDownloadButton button {{
  background: {BRAND_GRAD} !important; color: #FFFFFF !important;
  border: 0 !important; border-radius: 999px !important; font-weight: 700 !important; letter-spacing: .02em;
  box-shadow: 0 8px 24px rgba(89,39,229,0.35);
}}
.st-key-hr_boardroom .stDownloadButton button * {{ color: #FFFFFF !important; }}
.st-key-hr_boardroom [data-testid="stExpander"] {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 18px; }}
.st-key-hr_boardroom [data-testid="stExpander"] summary * {{ color: {TEXT} !important; }}
.br-eyebrow {{ font: 700 11px/1 'Plus Jakarta Sans',sans-serif; letter-spacing: .32em; text-transform: uppercase; color: {CYAN}; }}
.br-title {{ font: 700 34px/1.1 'Poppins','Plus Jakarta Sans',sans-serif; letter-spacing: -.02em; color: {TEXT}; margin: 10px 0 6px; }}
.br-sub {{ color: {MUTED}; font-size: 14px; }}
.br-hero {{ display: grid; grid-template-columns: 1fr auto; gap: 28px; align-items: center; margin: 6px 0 18px; }}
.br-letter {{ background: linear-gradient(180deg, {PANEL}, {INK}); border: 1px solid {LINE}; border-left: 2px solid {CYAN};
  border-radius: 18px; padding: 18px 22px; font-size: 15.5px; line-height: 1.65; color: {TEXT}; }}
.br-letter b {{ color: {CYAN}; font-weight: 600; letter-spacing: .14em; font-size: 11px; text-transform: uppercase; display: block; margin-bottom: 8px; }}
.br-health {{ display: flex; gap: 16px; align-items: center; background: {PANEL}; border: 1px solid {LINE}; border-radius: 18px; padding: 12px 18px 12px 12px; }}
.br-gauge-num {{ font: 700 34px 'Poppins',sans-serif; fill: {TEXT}; }}
.br-gauge-sub {{ font: 500 10px 'Plus Jakarta Sans',sans-serif; fill: {MUTED}; letter-spacing: .1em; }}
.br-health-list {{ font-size: 12px; color: {MUTED}; min-width: 170px; }}
.br-health-list div {{ display: flex; justify-content: space-between; gap: 12px; padding: 2px 0; }}
.br-health-list span:last-child {{ color: {TEXT}; font-variant-numeric: tabular-nums; }}
.br-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; margin: 8px 0 22px; }}
.br-kpi {{ position: relative; background: linear-gradient(180deg, {PANEL_2}, {PANEL}); border: 1px solid {LINE}; border-radius: 20px;
  padding: 16px 18px 6px; overflow: hidden; transition: transform .25s ease, border-color .25s ease; }}
.br-kpi:hover {{ transform: translateY(-2px); border-color: rgba(132,102,250,0.55); }}
.br-kpi-label {{ font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: {MUTED}; }}
.br-kpi-value {{ font: 700 30px/1.15 'Poppins','Plus Jakarta Sans',sans-serif; color: {TEXT}; margin-top: 8px; font-variant-numeric: tabular-nums; letter-spacing: -.01em; }}
.br-kpi-meta {{ display: flex; gap: 8px; align-items: center; margin-top: 6px; min-height: 22px; flex-wrap: wrap; }}
.br-kpi-foot {{ font-size: 11.5px; color: {MUTED}; }}
.br-kpi-spark {{ margin: 8px -18px 0; opacity: .95; }}
.br-delta {{ font-size: 11.5px; font-weight: 700; padding: 3px 8px; border-radius: 999px; font-variant-numeric: tabular-nums; }}
.br-up {{ color: {UP}; background: rgba(91,214,155,0.12); }}
.br-down {{ color: {DOWN}; background: rgba(255,122,122,0.12); }}
.br-flat {{ color: {MUTED}; background: rgba(255,255,255,0.06); }}
.br-h {{ display: flex; justify-content: space-between; align-items: baseline; margin: 6px 2px 8px; gap: 12px; }}
.br-h .br-h4 {{ font: 600 17px 'Poppins','Plus Jakarta Sans',sans-serif; color: {TEXT}; margin: 0; letter-spacing: -.01em; }}
.br-h span {{ font-size: 12px; color: {MUTED}; }}
.st-key-hr_boardroom [data-testid="stPlotlyChart"] {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 20px; padding: 10px 8px 4px; }}
.br-card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 20px; padding: 18px 20px; height: 100%; }}
.br-stack {{ display: flex; height: 16px; border-radius: 999px; overflow: hidden; background: rgba(255,255,255,0.05); margin: 10px 0 14px; }}
.br-stack div {{ height: 100%; }}
.br-legend {{ display: grid; grid-template-columns: 1fr auto; gap: 7px 14px; font-size: 13px; }}
.br-legend .k {{ display: flex; align-items: center; gap: 9px; color: {MUTED}; }}
.br-legend .k i {{ width: 9px; height: 9px; border-radius: 3px; display: inline-block; }}
.br-legend .v {{ color: {TEXT}; text-align: right; font-variant-numeric: tabular-nums; }}
.br-bar-row {{ display: grid; grid-template-columns: 140px 1fr 78px; gap: 10px; align-items: center; font-size: 13px; padding: 5px 0; }}
.br-bar-row .n {{ color: {TEXT}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.br-bar-row .t {{ height: 8px; border-radius: 99px; background: rgba(255,255,255,0.05); overflow: hidden; }}
.br-bar-row .t div {{ height: 100%; border-radius: 99px; background: linear-gradient(90deg, #5927E5, {GOLD}); }}
.br-bar-row .v {{ color: {MUTED}; text-align: right; font-variant-numeric: tabular-nums; }}
.br-mini {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }}
.br-mini div {{ background: rgba(255,255,255,0.03); border: 1px solid {LINE}; border-radius: 14px; padding: 10px 12px; }}
.br-mini small {{ display: block; font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: {MUTED}; }}
.br-mini strong {{ font: 700 18px 'Poppins',sans-serif; color: {TEXT}; }}
.br-recs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
.br-rec {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 18px; padding: 16px 18px; position: relative; }}
.br-rec::before {{ content: ""; position: absolute; left: 0; top: 16px; bottom: 16px; width: 2px; border-radius: 2px; background: var(--tone); }}
.br-rec h5 {{ margin: 0 0 6px; padding: 0; font: 600 14.5px 'Poppins',sans-serif; color: {TEXT} !important; }}
.st-key-hr_btnrow_wa_boardroom button, .st-key-hr_btnrow_wa_boardroom a {{ background: {PANEL_2} !important; border: 1px solid rgba(132,102,250,0.35) !important; }}
.st-key-hr_btnrow_wa_boardroom button p, .st-key-hr_btnrow_wa_boardroom a p, .st-key-hr_btnrow_wa_boardroom span {{ color: {TEXT} !important; }}
.br-rec p {{ margin: 0; font-size: 13px; line-height: 1.55; color: {MUTED} !important; }}
.br-foot {{ font-size: 11.5px; color: {SLATE}; margin-top: 16px; }}
@media (max-width: 900px) {{
  .st-key-hr_boardroom {{ padding: 18px 14px; border-radius: 22px; }}
  .br-grid {{ grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }}
  .br-hero {{ grid-template-columns: 1fr; }}
  .br-title {{ font-size: 26px; }}
  .br-kpi-value {{ font-size: 22px; }}
  .br-bar-row {{ grid-template-columns: 100px 1fr 64px; }}
  .br-mini {{ grid-template-columns: repeat(3, 1fr); gap: 6px; }}
  .br-mini div {{ padding: 8px; }}
  .br-mini strong {{ font-size: 15px; }}
  .br-card {{ margin-bottom: 12px; padding: 16px; }}
  .br-kpi {{ padding: 14px 14px 4px; }}
  .br-kpi-spark {{ margin: 6px -14px 0; }}
  .br-letter {{ font-size: 14.5px; padding: 16px; }}
}}
</style>
"""


def _section_head(title: str, note: str = "") -> None:
    st.markdown(f"<div class='br-h'><div class='br-h4'>{_esc(title)}</div><span>{_esc(note)}</span></div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render_boardroom(
    *,
    today: date,
    currency_symbol: str = "J$",
    business_name: str = "Headline Event Rentals",
    logo_path: Path | None = None,
) -> None:
    sym = (currency_symbol or "J$").strip() or "J$"
    st.markdown(BOARDROOM_CSS, unsafe_allow_html=True)

    with st.container(key="hr_boardroom"):
        top_l = st.container()
        top_r = st.container()
        with top_l:
            st.markdown(
                "<div class='br-eyebrow'>Boardroom</div>"
                f"<div class='br-title'>{_esc(business_name)}</div>"
                "<div class='br-sub'>The numbers your shareholders ask for, on one page.</div>",
                unsafe_allow_html=True,
            )
        with top_r:
            seg = getattr(st, "segmented_control", None)
            if seg is not None:
                choice = seg("Period", PERIOD_OPTIONS, default="Trailing 12M", key="br_period", label_visibility="collapsed")
            else:
                choice = st.radio("Period", PERIOD_OPTIONS, horizontal=True, key="br_period", label_visibility="collapsed")
            choice = choice or "Trailing 12M"

        year = None
        if choice == "Calendar Year":
            years = build_model("All Time", None, today.isoformat())["years"]
            year = st.selectbox("Year", years, key="br_year")

        m = build_model(choice, year, today.isoformat())
        c, p = m["cur"], m["prior"]
        w = m["window"]
        sp = m["spark"]

        # Hero: chairman's summary + health index.
        health_rows = "".join(
            f"<div><span>{_esc(k)}</span><span>{'n/a' if v is None else round(v * 100)}</span></div>"
            for k, v in m["health_parts"].items()
        )
        gauge = _gauge(m["health"])
        health_html = (
            f"<div class='br-health'>{gauge}<div class='br-health-list'><div style='color:{CYAN};letter-spacing:.14em;font-size:10.5px;"
            f"text-transform:uppercase'><span>Health index</span><span></span></div>{health_rows}</div></div>"
            if gauge
            else ""
        )
        st.markdown(
            f"<div class='br-hero'><div class='br-letter'><b>Chairman's summary &middot; {_esc(w.label)}</b>{_esc(chairman_summary(m, sym))}</div>{health_html}</div>",
            unsafe_allow_html=True,
        )

        # KPI wall.
        prev = (lambda k: p[k] if p else None)
        coll_rate = _safe_div(c["collected"], c["billed"])
        prev_coll = _safe_div(p["collected"], p["billed"]) if p else None
        tiles = [
            _kpi_tile("Net revenue", _compact(c["revenue"], sym), _delta_chip(c["revenue"], prev("revenue")),
                      f"vs {w.prior_label}" if p else "ex GCT", _svg_spark(sp["revenue"].tolist())),
            _kpi_tile("Operating profit", _compact(c["operating"], sym), _delta_chip(c["operating"], prev("operating")),
                      "after all running costs", _svg_spark(sp["operating"].tolist(), UP)),
            _kpi_tile("Operating margin", _pct(c["operating_margin"], 0),
                      _delta_chip(c["operating_margin"], prev("operating_margin"), pts=True) if p and c["operating_margin"] is not None else "",
                      f"gross margin {_pct(c['gross_margin'], 0)}"),
            _kpi_tile("Confirmed events", f"{c['orders']:,}", _delta_chip(c["orders"], prev("orders")),
                      f"{m['active_months']} of {m['window_months']} months active", _svg_spark(sp["orders"].tolist(), "#C9CDD6")),
            _kpi_tile("Average event value", _compact(c["aov"], sym), _delta_chip(c["aov"], prev("aov")), "net revenue per event"),
            _kpi_tile("Cash conversion", _pct((coll_rate or 0) * 100, 0) if coll_rate is not None else "n/a",
                      _delta_chip((coll_rate or 0) * 100, (prev_coll * 100) if prev_coll is not None else None, pts=True) if p and coll_rate is not None else "",
                      f"{_compact(c['outstanding'], sym)} still to collect"),
            _kpi_tile("Forward book", _compact(m["book"]["confirmed"], sym), "",
                      f"{m['book']['confirmed_n']} confirmed  |  {_compact(m['book']['deposits'], sym)} deposits held"),
            _kpi_tile("Annual run rate", _compact(m["run_rate"] * 12, sym), "", "last 6 months, annualised"),
        ]
        st.markdown(f"<div class='br-grid'>{''.join(tiles)}</div>", unsafe_allow_html=True)

        # Row 1: revenue engine + profit bridge.
        a, b = st.columns([1.35, 1], gap="medium")
        with a:
            best = m["best_month"]
            _section_head("Revenue engine", f"best month {best[0]} at {_compact(best[1], sym)}" if best else "")
            st.plotly_chart(chart_revenue_engine(m, sym), use_container_width=True, config=PLOTLY_CONFIG, key="br_engine")
        with b:
            _section_head("Profit bridge", "from revenue to what we keep")
            st.plotly_chart(chart_bridge(m, sym), use_container_width=True, config=PLOTLY_CONFIG, key="br_bridge")

        # Row 2: where every 100 goes + clients.
        a, b = st.columns([1, 1], gap="medium")
        with a:
            rev = c["revenue"]
            shares = []
            if rev > 0:
                for bkt in COST_BUCKET_ORDER:
                    v = c[f"b::{bkt}"]
                    if v > 0:
                        shares.append((bkt, v / rev * 100))
                shares.append(("Profit", max(0.0, c["operating"] / rev * 100)))
            stack = "".join(
                f"<div title='{_esc(k)} {s:.1f}' style='width:{min(100, s):.2f}%;background:{BUCKET_COLOURS.get(k, SLATE)}'></div>" for k, s in shares
            )
            legend = "".join(
                f"<div class='k'><i style='background:{BUCKET_COLOURS.get(k, SLATE)}'></i>{_esc(k)}</div><div class='v'>{sym}{s:,.1f}</div>"
                for k, s in shares
            )
            capex_note = (
                f"<div class='br-mini'><div><small>Fleet investment</small><strong>{_compact(m['capex_window'], sym)}</strong></div>"
                f"<div><small>Gross margin</small><strong>{_pct(c['gross_margin'], 0)}</strong></div>"
                f"<div><small>Supplier reliance</small><strong>{_pct((_safe_div(c['b::Supplier re-rentals'], rev) or 0) * 100, 0) if rev else 'n/a'}</strong></div></div>"
            )
            st.markdown(
                f"<div class='br-card'><div class='br-h' style='margin:0'><div class='br-h4'>Of every {sym}100 we earn</div><span>{_esc(w.label.lower())}</span></div>"
                f"<div class='br-stack'>{stack}</div><div class='br-legend'>{legend or '<span>No revenue in this period.</span>'}</div>{capex_note}</div>",
                unsafe_allow_html=True,
            )
        with b:
            cr: pd.Series = m["client_rev"]
            top = cr.head(6)
            mx = float(top.max()) if not top.empty else 1.0
            rows = "".join(
                f"<div class='br-bar-row'><div class='n'>{_esc(n)}</div><div class='t'><div style='width:{v / mx * 100:.1f}%'></div></div><div class='v'>{_compact(v, sym)}</div></div>"
                for n, v in top.items()
            ) or "<div class='br-sub'>Add client names to confirmed orders to unlock client analytics.</div>"
            repeat_rate = _safe_div(m["repeat_clients"], m["client_count"])
            st.markdown(
                f"<div class='br-card'><div class='br-h' style='margin:0 0 6px'><div class='br-h4'>Client franchise</div><span>top clients by net revenue</span></div>{rows}"
                f"<div class='br-mini'><div><small>Named clients</small><strong>{m['client_count']}</strong></div>"
                f"<div><small>Repeat clients</small><strong>{_pct((repeat_rate or 0) * 100, 0) if repeat_rate is not None else 'n/a'}</strong></div>"
                f"<div><small>Top 5 share</small><strong>{_pct((m['top5_share'] or 0) * 100, 0) if m['top5_share'] is not None else 'n/a'}</strong></div></div></div>",
                unsafe_allow_html=True,
            )

        # Row 3: outlook + seasonality.
        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
        a, b = st.columns([1, 1.25], gap="medium")
        with a:
            nxt = sum(f["base"] for f in m["forecast"])
            _section_head("Next 90 days", f"indicative {_compact(nxt, sym)}  |  {_compact(m['book']['pending'] + m['book']['quotes'], sym)} in quotes and pending")
            st.plotly_chart(chart_outlook(m, sym), use_container_width=True, config=PLOTLY_CONFIG, key="br_outlook")
        with b:
            _section_head("Seasonality map", "net revenue by month, every year on record")
            sfig = chart_seasonality(m, sym)
            if sfig is not None:
                st.plotly_chart(sfig, use_container_width=True, config=PLOTLY_CONFIG, key="br_season")
            mix: pd.Series = m["mix"]
            if not mix.empty:
                tot = float(mix.sum()) or 1.0
                chips = " ".join(
                    f"<span class='br-delta br-flat' style='margin:0 6px 6px 0;display:inline-block'>{_esc(k)} {v / tot * 100:.0f}%</span>"
                    for k, v in mix.items()
                )
                st.markdown(f"<div style='margin-top:10px'><span class='br-kpi-label'>Itemised revenue mix</span><div style='margin-top:8px'>{chips}</div></div>", unsafe_allow_html=True)

        # Row 4: recommendations.
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _section_head("Where we act next", "generated from this period's numbers")
        tone_map = {"gold": CYAN, "up": UP, "down": DOWN}
        recs = "".join(
            f"<div class='br-rec' style='--tone:{tone_map.get(t, GOLD)}'><h5>{_esc(title)}</h5><p>{_esc(body)}</p></div>"
            for t, title, body in recommendations(m, sym)
        )
        st.markdown(f"<div class='br-recs'>{recs}</div>", unsafe_allow_html=True)

        # Footer: board pack + method.
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        f1, f2 = st.columns([1, 2], vertical_alignment="center")
        with f1:
            try:
                pdf_bytes = _board_pack_cached(choice, year, today.isoformat(), sym, business_name,
                                               str(logo_path) if logo_path else "")
                st.download_button(
                    "Download board pack (PDF)",
                    data=pdf_bytes,
                    file_name=f"Board_Pack_{w.start:%Y%m%d}_{w.end:%Y%m%d}.pdf",
                    mime="application/pdf",
                    key="br_board_pack",
                    use_container_width=True,
                )
            except Exception as exc:  # pragma: no cover
                st.caption(f"Board pack unavailable: {exc}")
        with f2:
            conf = m["confidence"]
            st.markdown(
                f"<div class='br-foot'>Data confidence {('n/a' if conf is None else f'{conf:.0f}/100')}. Revenue is net of GCT and recognised on event date. "
                "Cost of service covers supplier re-rentals, crew and fleet. Fleet purchases are treated as investment, not running cost. "
                "Forecasts blend same month history with the recent run rate and never fall below what is already booked.</div>",
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        try:
            import hr_features as _hr
            _hr.render_send_pack_to_whatsapp(
                pdf_bytes, f"Board_Pack_{w.start:%Y%m%d}_{w.end:%Y%m%d}.pdf",
                _hr.board_pack_message(m, w.label, sym), "boardroom")
        except Exception:
            pass
