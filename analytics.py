from __future__ import annotations

import re

import numpy as np
import pandas as pd

from db import fetch_dataframe


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

_INVENTORY_PURCHASE_LABELS = {
    "inventory purchase",
    "inventory purchases",
    "stock purchase",
    "stock purchases",
    "purchase inventory",
    "purchasing new inventory",
    "new inventory purchase",
}


def _inventory_purchase_mask(series: pd.Series) -> pd.Series:
    normalized = series.fillna("").astype(str).str.strip().str.lower()
    return normalized.isin(_INVENTORY_PURCHASE_LABELS)


def _inventory_name_keywords(name: object) -> list[str]:
    raw = str(name or "").strip().lower()
    if not raw:
        return []

    normalized = raw.replace("&", " and ").replace("×", " x ")
    normalized = re.sub(r"\bby\b", " x ", normalized)
    normalized = re.sub(r"[^a-z0-9x\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    tokens = [_NUMBER_WORDS.get(token, token) for token in normalized.split(" ") if token]
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


def _inventory_name_signature(name: object) -> str:
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


def _resolve_inventory_item_key(
    raw_item_name: object,
    inventory_catalog: list[dict[str, object]],
) -> str:
    fallback = str(raw_item_name or "").strip().lower()
    signature = _inventory_name_signature(raw_item_name)
    if not signature:
        return fallback

    for row in inventory_catalog:
        if str(row.get("signature", "")).strip() == signature:
            return str(row.get("item_key", fallback)).strip()

    target_keywords = set(_inventory_name_keywords(raw_item_name))
    if not target_keywords:
        return fallback

    best_item_key = fallback
    best_score = 0.0
    for row in inventory_catalog:
        candidate_keywords = set(row.get("keywords", []) or [])
        score = _inventory_keyword_similarity(target_keywords, candidate_keywords)
        if score > best_score:
            best_score = score
            best_item_key = str(row.get("item_key", fallback)).strip()

    return best_item_key if best_score >= 0.68 else fallback


def _safe_to_period(series: pd.Series) -> pd.Series:
    as_dt = pd.to_datetime(series, errors="coerce")
    return as_dt.dt.to_period("M").astype("string")


def _sort_by_month(df: pd.DataFrame, month_col: str = "month") -> pd.DataFrame:
    if df.empty or month_col not in df.columns:
        return df
    out = df.copy()
    out["_period"] = pd.PeriodIndex(out[month_col], freq="M")
    out = out.sort_values("_period")
    return out.drop(columns="_period")


def _product_group_from_name(item_name: str, item_type: str = "product") -> str:
    name = str(item_name or "").strip().lower()
    if not name:
        return "Uncategorized"
    if str(item_type or "").strip().lower() == "service":
        return "Service"

    keyword_map = [
        ("Tent", ["tent", "canopy"]),
        ("Table", ["table", "cocktail"]),
        ("Chair", ["chair", "seat", "stool"]),
        ("Bounce/Games", ["bounce", "trampoline", "game", "arcade"]),
        ("Machine", ["machine", "popcorn", "snowcone", "cotton candy", "hotdog"]),
        ("Decor/Fabric", ["decor", "cloth", "spandex", "drape", "backdrop"]),
        ("Lighting/Audio", ["light", "speaker", "audio", "sound", "mic"]),
    ]
    for label, keywords in keyword_map:
        if any(token in name for token in keywords):
            return label
    return "Other Product"


def load_invoice_level() -> pd.DataFrame:
    invoices = fetch_dataframe(
        """
        SELECT
            i.id,
            i.invoice_number,
            i.event_date,
            COALESCE(i.customer_name, '') AS customer_name,
            COALESCE(i.payment_status, 'paid_full') AS payment_status,
            COALESCE(i.amount_paid, 0) AS amount_paid,
            COALESCE(i.deposit_balance_enabled, 0) AS deposit_balance_enabled,
            COALESCE(i.payment_notes, '') AS payment_notes,
            COALESCE(SUM(it.quantity * it.unit_price), 0) AS invoice_total,
            COALESCE(SUM(it.quantity * COALESCE(it.unit_cost, 0)), 0) AS item_cost
        FROM invoices i
        LEFT JOIN invoice_items it ON it.invoice_id = i.id
        WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
        GROUP BY i.id
        """
    )
    if invoices.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "invoice_number",
                "event_date",
                "customer_name",
                "payment_status",
                "amount_paid",
                "deposit_balance_enabled",
                "amount_outstanding",
                "payment_notes",
                "payment_reminder",
                "invoice_total",
                "revenue",
                "item_cost",
                "invoice_expenses",
                "gross_profit",
                "net_profit",
                "month",
                "year",
            ]
        )

    linked_expenses = fetch_dataframe(
        """
        SELECT invoice_id AS id, SUM(amount) AS invoice_expenses
        FROM expenses
        WHERE invoice_id IS NOT NULL
          AND lower(COALESCE(expense_kind, 'transaction')) <> 'summary_rollup'
        GROUP BY invoice_id
        """
    )

    merged = invoices.merge(linked_expenses, on="id", how="left")
    merged["invoice_expenses"] = merged["invoice_expenses"].fillna(0.0)
    merged["payment_status"] = (
        merged["payment_status"].fillna("paid_full").astype(str).str.strip().str.lower()
    )
    merged["deposit_balance_enabled"] = pd.to_numeric(
        merged.get("deposit_balance_enabled", 0),
        errors="coerce",
    ).fillna(0).astype(int)
    merged["invoice_total"] = pd.to_numeric(
        merged.get("invoice_total", 0.0), errors="coerce"
    ).fillna(0.0)
    merged["invoice_total"] = merged["invoice_total"].clip(lower=0.0)
    merged["amount_paid"] = pd.to_numeric(merged["amount_paid"], errors="coerce").fillna(0.0)
    inferred_full = (
        merged["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (merged["amount_paid"] <= 0)
    )
    merged.loc[inferred_full, "amount_paid"] = merged.loc[inferred_full, "invoice_total"]
    forced_full = (
        merged["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (merged["amount_paid"] < merged["invoice_total"])
    )
    merged.loc[forced_full, "amount_paid"] = merged.loc[forced_full, "invoice_total"]
    merged["amount_paid"] = merged["amount_paid"].clip(lower=0.0)
    merged["amount_paid"] = np.minimum(merged["amount_paid"], merged["invoice_total"])
    is_fully_paid = merged["payment_status"].isin(["paid_full", "full_paid", "paid"])
    merged["revenue"] = np.where(
        is_fully_paid,
        merged["invoice_total"],
        merged["amount_paid"],
    )
    merged["revenue"] = pd.to_numeric(merged["revenue"], errors="coerce").fillna(0.0).clip(lower=0.0)
    merged["amount_outstanding"] = (merged["invoice_total"] - merged["amount_paid"]).clip(lower=0.0)
    merged["payment_reminder"] = np.where(
        merged["amount_outstanding"] > 0.01,
        "Outstanding balance remains.",
        "Paid in full.",
    )
    # Headline Rentals profitability is revenue-based from rental pricing/base rental.
    # Unit cost is not used in profit calculation.
    merged["gross_profit"] = merged["revenue"]
    merged["net_profit"] = merged["gross_profit"] - merged["invoice_expenses"]
    merged["event_date"] = pd.to_datetime(merged["event_date"], errors="coerce")
    merged["month"] = merged["event_date"].dt.to_period("M").astype("string")
    merged["year"] = merged["event_date"].dt.year
    return merged


def load_expenses() -> pd.DataFrame:
    expenses = fetch_dataframe(
        """
        SELECT
            e.*,
            i.event_date AS linked_event_date
        FROM expenses e
        LEFT JOIN invoices i ON i.id = e.invoice_id
        """
    )
    if expenses.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "expense_date",
                "linked_event_date",
                "finance_date",
                "date_basis",
                "invoice_id",
                "category",
                "expense_kind",
                "vendor",
                "description",
                "amount",
                "month",
                "year",
            ]
        )
    if "expense_kind" not in expenses.columns:
        expenses["expense_kind"] = "transaction"
    expenses["expense_kind"] = expenses["expense_kind"].fillna("transaction").astype(str)
    expenses["expense_date"] = pd.to_datetime(expenses["expense_date"], errors="coerce")
    expenses["linked_event_date"] = pd.to_datetime(
        expenses.get("linked_event_date"),
        errors="coerce",
    )
    use_event_date = expenses["invoice_id"].notna() & expenses["linked_event_date"].notna()
    expenses["date_basis"] = np.where(use_event_date, "event_date", "expense_date")
    expenses["finance_date"] = expenses["expense_date"]
    expenses.loc[use_event_date, "finance_date"] = expenses.loc[use_event_date, "linked_event_date"]
    expenses["month"] = expenses["finance_date"].dt.to_period("M").astype("string")
    expenses["year"] = expenses["finance_date"].dt.year
    return expenses


def load_monthly_summary() -> pd.DataFrame:
    invoice_level = load_invoice_level()
    expenses = load_expenses()
    adjustments_raw = fetch_dataframe(
        """
        SELECT month, adjustment_type, amount
        FROM monthly_adjustments
        """
    )
    adjustments = pd.DataFrame(columns=["month", "adjustments"])
    if not adjustments_raw.empty:
        scoped_adjustments = adjustments_raw[
            ~_inventory_purchase_mask(adjustments_raw["adjustment_type"])
        ].copy()
        if not scoped_adjustments.empty:
            adjustments = (
                scoped_adjustments.groupby("month", as_index=False)["amount"]
                .sum()
                .rename(columns={"amount": "adjustments"})
            )

    expense_scope = expenses.copy()
    if not expense_scope.empty:
        expense_scope = expense_scope[~_inventory_purchase_mask(expense_scope["category"])].copy()

    by_invoice_month = pd.DataFrame(
        columns=[
            "month",
            "revenue",
            "cash_collected",
            "outstanding_receivables",
            "item_cost",
            "linked_expenses",
        ]
    )
    if not invoice_level.empty:
        by_invoice_month = (
            invoice_level.dropna(subset=["month"])
            .groupby("month", as_index=False)
            .agg(
                revenue=("revenue", "sum"),
                cash_collected=("amount_paid", "sum"),
                outstanding_receivables=("amount_outstanding", "sum"),
                item_cost=("item_cost", "sum"),
                linked_expenses=("invoice_expenses", "sum"),
            )
        )

    general_expenses = pd.DataFrame(columns=["month", "general_expenses"])
    if not expense_scope.empty:
        general_expenses = (
            expense_scope[
                (expense_scope["invoice_id"].isna())
                & (expense_scope["expense_kind"].str.lower() != "summary_rollup")
            ]
            .dropna(subset=["month"])
            .groupby("month", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "general_expenses"})
        )

    recurring_expenses = pd.DataFrame(columns=["month", "recurring_expenses"])
    summarized_expenses = pd.DataFrame(columns=["month", "summarized_expenses"])
    if not expense_scope.empty:
        recurring_expenses = (
            expense_scope[
                (expense_scope["invoice_id"].isna())
                & (expense_scope["expense_kind"].str.lower() == "recurring_monthly")
                & (expense_scope["month"].notna())
            ]
            .groupby("month", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "recurring_expenses"})
        )
        summarized_categories = {
            "wages",
            "re-rental",
            "petrol",
            "bad debt",
            "unforeseen expense",
        }
        summarized_expenses = (
            expense_scope[
                (expense_scope["expense_kind"].str.lower() == "transaction")
                & (expense_scope["category"].str.lower().isin(summarized_categories))
                & (expense_scope["month"].notna())
            ]
            .groupby("month", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "summarized_expenses"})
        )

    months = pd.concat(
        [
            by_invoice_month[["month"]] if not by_invoice_month.empty else pd.DataFrame(columns=["month"]),
            general_expenses[["month"]] if not general_expenses.empty else pd.DataFrame(columns=["month"]),
            recurring_expenses[["month"]] if not recurring_expenses.empty else pd.DataFrame(columns=["month"]),
            summarized_expenses[["month"]] if not summarized_expenses.empty else pd.DataFrame(columns=["month"]),
            adjustments[["month"]] if not adjustments.empty else pd.DataFrame(columns=["month"]),
        ],
        ignore_index=True,
    ).drop_duplicates()

    if months.empty:
        return pd.DataFrame(
            columns=[
                "month",
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
                "month_label",
                "year",
            ]
        )

    summary = (
        months.merge(by_invoice_month, on="month", how="left")
        .merge(general_expenses, on="month", how="left")
        .merge(recurring_expenses, on="month", how="left")
        .merge(summarized_expenses, on="month", how="left")
        .merge(adjustments, on="month", how="left")
    )
    for col in [
        "revenue",
        "cash_collected",
        "outstanding_receivables",
        "item_cost",
        "linked_expenses",
        "general_expenses",
        "recurring_expenses",
        "summarized_expenses",
        "adjustments",
    ]:
        summary[col] = summary[col].fillna(0.0)

    summary["total_expenses"] = summary["linked_expenses"] + summary["general_expenses"]
    summary["net_profit"] = summary["revenue"] - summary["total_expenses"]
    summary["net_profit_after_adjustments"] = summary["net_profit"] - summary["adjustments"]

    period = pd.PeriodIndex(summary["month"], freq="M")
    summary["month_label"] = period.strftime("%b %Y")
    summary["year"] = period.year
    return _sort_by_month(summary)


def load_daily_summary() -> pd.DataFrame:
    invoice_level = load_invoice_level()
    expenses = load_expenses()
    expense_scope = expenses.copy()
    if not expense_scope.empty:
        expense_scope = expense_scope[~_inventory_purchase_mask(expense_scope["category"])].copy()

    by_invoice_day = pd.DataFrame(
        columns=[
            "day",
            "revenue",
            "cash_collected",
            "outstanding_receivables",
            "item_cost",
            "linked_expenses",
        ]
    )
    if not invoice_level.empty:
        by_invoice_day = (
            invoice_level.dropna(subset=["event_date"])
            .assign(day=lambda d: d["event_date"].dt.date.astype("string"))
            .groupby("day", as_index=False)
            .agg(
                revenue=("revenue", "sum"),
                cash_collected=("amount_paid", "sum"),
                outstanding_receivables=("amount_outstanding", "sum"),
                item_cost=("item_cost", "sum"),
                linked_expenses=("invoice_expenses", "sum"),
            )
        )

    general_expenses = pd.DataFrame(columns=["day", "general_expenses"])
    recurring_expenses = pd.DataFrame(columns=["day", "recurring_expenses"])
    summarized_expenses = pd.DataFrame(columns=["day", "summarized_expenses"])
    if not expense_scope.empty:
        scoped = expense_scope[expense_scope["finance_date"].notna()].copy()
        if not scoped.empty:
            scoped["day"] = scoped["finance_date"].dt.date.astype("string")
            general_expenses = (
                scoped[
                    (scoped["invoice_id"].isna())
                    & (scoped["expense_kind"].str.lower() != "summary_rollup")
                ]
                .groupby("day", as_index=False)["amount"]
                .sum()
                .rename(columns={"amount": "general_expenses"})
            )
            recurring_expenses = (
                scoped[
                    (scoped["invoice_id"].isna())
                    & (scoped["expense_kind"].str.lower() == "recurring_monthly")
                ]
                .groupby("day", as_index=False)["amount"]
                .sum()
                .rename(columns={"amount": "recurring_expenses"})
            )
            summarized_categories = {
                "wages",
                "re-rental",
                "petrol",
                "bad debt",
                "unforeseen expense",
            }
            summarized_expenses = (
                scoped[
                    (scoped["expense_kind"].str.lower() == "transaction")
                    & (scoped["category"].str.lower().isin(summarized_categories))
                ]
                .groupby("day", as_index=False)["amount"]
                .sum()
                .rename(columns={"amount": "summarized_expenses"})
            )

    day_frames: list[pd.DataFrame] = []
    if not by_invoice_day.empty:
        day_frames.append(by_invoice_day[["day"]])
    if not general_expenses.empty:
        day_frames.append(general_expenses[["day"]])
    if not recurring_expenses.empty:
        day_frames.append(recurring_expenses[["day"]])
    if not summarized_expenses.empty:
        day_frames.append(summarized_expenses[["day"]])
    days = (
        pd.concat(day_frames, ignore_index=True).drop_duplicates()
        if day_frames
        else pd.DataFrame(columns=["day"])
    )

    if days.empty:
        return pd.DataFrame(
            columns=[
                "day",
                "day_label",
                "month",
                "year",
                "revenue",
                "cash_collected",
                "outstanding_receivables",
                "item_cost",
                "linked_expenses",
                "general_expenses",
                "recurring_expenses",
                "summarized_expenses",
                "total_expenses",
                "net_profit",
            ]
        )

    summary = (
        days.merge(by_invoice_day, on="day", how="left")
        .merge(general_expenses, on="day", how="left")
        .merge(recurring_expenses, on="day", how="left")
        .merge(summarized_expenses, on="day", how="left")
    )
    for col in [
        "revenue",
        "cash_collected",
        "outstanding_receivables",
        "item_cost",
        "linked_expenses",
        "general_expenses",
        "recurring_expenses",
        "summarized_expenses",
    ]:
        summary[col] = summary[col].fillna(0.0)

    summary["total_expenses"] = summary["linked_expenses"] + summary["general_expenses"]
    summary["net_profit"] = summary["revenue"] - summary["total_expenses"]

    day_period = pd.to_datetime(summary["day"], errors="coerce")
    summary["day_label"] = day_period.dt.strftime("%Y-%m-%d")
    summary["month"] = day_period.dt.to_period("M").astype("string")
    summary["year"] = day_period.dt.year
    return summary.sort_values("day")


def load_yearly_summary() -> pd.DataFrame:
    monthly = load_monthly_summary()
    if monthly.empty:
        return pd.DataFrame(
            columns=[
                "year",
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
            ]
        )
    return (
        monthly.groupby("year", as_index=False)[
            [
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
            ]
        ]
        .sum()
        .sort_values("year")
    )


def _is_gct_row(item_name: object) -> bool:
    label = str(item_name or "").strip().lower()
    if not label:
        return False
    return label in {
        "gct (15%)",
        "gct",
        "gct 15%",
        "tax (15%)",
        "tax",
    }


def load_tax_pack_invoice_detail() -> pd.DataFrame:
    rows = fetch_dataframe(
        """
        SELECT
            i.id AS invoice_id,
            i.invoice_number,
            i.event_date,
            COALESCE(i.customer_name, '') AS customer_name,
            COALESCE(it.item_name, '') AS item_name,
            COALESCE(it.quantity, 0) AS quantity,
            COALESCE(it.unit_price, 0) AS unit_price
        FROM invoices i
        LEFT JOIN invoice_items it ON it.invoice_id = i.id
        WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
          AND i.event_date IS NOT NULL
        """
    )
    if rows.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "month",
                "month_label",
                "customer_name",
                "subtotal_before_gct",
                "gct_collected",
                "gross_total",
                "gct_enabled",
                "effective_gct_pct",
            ]
        )

    rows["quantity"] = pd.to_numeric(rows["quantity"], errors="coerce").fillna(0.0)
    rows["unit_price"] = pd.to_numeric(rows["unit_price"], errors="coerce").fillna(0.0)
    rows["line_total"] = rows["quantity"] * rows["unit_price"]
    rows["is_gct_row"] = rows["item_name"].apply(_is_gct_row)
    rows["event_date"] = pd.to_datetime(rows["event_date"], errors="coerce")
    rows = rows[rows["event_date"].notna()].copy()
    if rows.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "month",
                "month_label",
                "customer_name",
                "subtotal_before_gct",
                "gct_collected",
                "gross_total",
                "gct_enabled",
                "effective_gct_pct",
            ]
        )

    by_invoice = (
        rows.groupby(
            ["invoice_id", "invoice_number", "event_date", "customer_name"],
            as_index=False,
        )
        .agg(
            subtotal_before_gct=("line_total", lambda s: float(s[~rows.loc[s.index, "is_gct_row"]].sum())),
            gct_collected=("line_total", lambda s: float(s[rows.loc[s.index, "is_gct_row"]].sum())),
        )
        .sort_values(["event_date", "invoice_number"], ascending=[True, True])
    )
    by_invoice["gross_total"] = by_invoice["subtotal_before_gct"] + by_invoice["gct_collected"]
    by_invoice["gct_enabled"] = by_invoice["gct_collected"] > 0.009
    by_invoice["effective_gct_pct"] = np.where(
        by_invoice["subtotal_before_gct"] > 0,
        (by_invoice["gct_collected"] / by_invoice["subtotal_before_gct"]) * 100.0,
        0.0,
    )
    by_invoice["month"] = by_invoice["event_date"].dt.to_period("M").astype("string")
    by_invoice["month_label"] = by_invoice["event_date"].dt.to_period("M").dt.strftime("%b %Y")
    return by_invoice


def load_tax_pack_monthly() -> pd.DataFrame:
    detail = load_tax_pack_invoice_detail()
    if detail.empty:
        return pd.DataFrame(
            columns=[
                "month",
                "month_label",
                "invoice_count",
                "gct_enabled_invoices",
                "subtotal_before_gct",
                "gct_collected",
                "gross_total",
                "effective_gct_pct",
            ]
        )

    out = (
        detail.groupby(["month", "month_label"], as_index=False)
        .agg(
            invoice_count=("invoice_id", "nunique"),
            gct_enabled_invoices=("gct_enabled", "sum"),
            subtotal_before_gct=("subtotal_before_gct", "sum"),
            gct_collected=("gct_collected", "sum"),
            gross_total=("gross_total", "sum"),
        )
        .sort_values("month")
    )
    out["effective_gct_pct"] = np.where(
        out["subtotal_before_gct"] > 0,
        (out["gct_collected"] / out["subtotal_before_gct"]) * 100.0,
        0.0,
    )
    out["gct_enabled_invoices"] = out["gct_enabled_invoices"].astype(int)
    return out


def load_budget_vs_actual() -> pd.DataFrame:
    monthly = load_monthly_summary()
    budgets = fetch_dataframe(
        """
        SELECT
            month,
            COALESCE(revenue_target, 0) AS revenue_target,
            COALESCE(expense_target, 0) AS expense_target,
            COALESCE(notes, '') AS notes,
            updated_at
        FROM monthly_budget_targets
        """
    )
    if monthly.empty and budgets.empty:
        return pd.DataFrame(
            columns=[
                "month",
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
        )

    base_months = pd.DataFrame(columns=["month"])
    if not monthly.empty:
        base_months = pd.concat([base_months, monthly[["month"]]], ignore_index=True)
    if not budgets.empty:
        base_months = pd.concat([base_months, budgets[["month"]]], ignore_index=True)
    base_months = base_months.dropna(subset=["month"]).drop_duplicates()
    if base_months.empty:
        return pd.DataFrame(
            columns=[
                "month",
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
        )

    monthly_actual = (
        monthly[
            [
                "month",
                "month_label",
                "revenue",
                "total_expenses",
            ]
        ]
        .rename(
            columns={
                "revenue": "revenue_actual",
                "total_expenses": "expense_actual",
            }
        )
        if not monthly.empty
        else pd.DataFrame(columns=["month", "month_label", "revenue_actual", "expense_actual"])
    )

    out = (
        base_months.merge(monthly_actual, on="month", how="left")
        .merge(budgets[["month", "revenue_target", "expense_target", "notes"]], on="month", how="left")
    )
    month_label_fallback = pd.Series(
        pd.PeriodIndex(out["month"], freq="M").strftime("%b %Y"),
        index=out.index,
    )
    out["month_label"] = out["month_label"].fillna(month_label_fallback)
    for col in ["revenue_target", "expense_target", "revenue_actual", "expense_actual"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    out["revenue_variance"] = out["revenue_actual"] - out["revenue_target"]
    out["expense_variance"] = out["expense_target"] - out["expense_actual"]
    out["revenue_variance_pct"] = np.where(
        out["revenue_target"] > 0,
        (out["revenue_variance"] / out["revenue_target"]) * 100,
        0.0,
    )
    out["expense_variance_pct"] = np.where(
        out["expense_target"] > 0,
        (out["expense_variance"] / out["expense_target"]) * 100,
        0.0,
    )
    out["notes"] = out["notes"].fillna("")
    return _sort_by_month(out)


def load_wages_period_summary(period: str = "M") -> pd.DataFrame:
    period_token = (period or "M").strip().upper()
    if period_token not in {"D", "W", "M", "Y"}:
        period_token = "M"

    empty_cols = [
        "period_start",
        "period_label",
        "wages_person_level",
        "wages_summary_topups",
        "wages_monthly_sheet_rollup",
        "wages_total_used",
    ]
    expenses = load_expenses()
    if expenses.empty:
        return pd.DataFrame(columns=empty_cols)

    wages = expenses[
        expenses["category"].fillna("").str.lower() == "wages"
    ].copy()
    if wages.empty:
        return pd.DataFrame(columns=empty_cols)

    wages = wages[wages["finance_date"].notna()].copy()
    if wages.empty:
        return pd.DataFrame(columns=empty_cols)

    wages["finance_date"] = pd.to_datetime(wages["finance_date"], errors="coerce")
    wages = wages[wages["finance_date"].notna()].copy()
    if wages.empty:
        return pd.DataFrame(columns=empty_cols)

    if period_token == "D":
        wages["period_start"] = wages["finance_date"].dt.normalize()
        wages["period_label"] = wages["period_start"].dt.strftime("%Y-%m-%d")
    elif period_token == "W":
        wages["period_start"] = (
            wages["finance_date"] - pd.to_timedelta(wages["finance_date"].dt.weekday, unit="D")
        ).dt.normalize()
        wages["period_end"] = wages["period_start"] + pd.to_timedelta(6, unit="D")
        wages["period_label"] = (
            wages["period_start"].dt.strftime("%Y-%m-%d")
            + " to "
            + wages["period_end"].dt.strftime("%Y-%m-%d")
        )
    elif period_token == "Y":
        wages["period_start"] = wages["finance_date"].dt.to_period("Y").dt.start_time
        wages["period_label"] = wages["period_start"].dt.strftime("%Y")
    else:
        wages["period_start"] = wages["finance_date"].dt.to_period("M").dt.start_time
        wages["period_label"] = wages["period_start"].dt.strftime("%b %Y")

    person_level = wages[
        ~wages["vendor"].isin(["Legacy Sheet", "Summary Adjustment", "Monthly Ledger"])
    ]
    summary_topups = wages[
        wages["vendor"].isin(["Legacy Sheet", "Summary Adjustment"])
    ]
    monthly_rollup = wages[
        wages["vendor"] == "Monthly Ledger"
    ]

    period_base = wages[["period_start", "period_label"]].drop_duplicates()
    person_by_period = (
        person_level.groupby(["period_start", "period_label"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_person_level"})
    )
    topup_by_period = (
        summary_topups.groupby(["period_start", "period_label"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_summary_topups"})
    )
    monthly_rollup_by_period = (
        monthly_rollup.groupby(["period_start", "period_label"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_monthly_sheet_rollup"})
    )
    total_by_period = (
        wages.groupby(["period_start", "period_label"], as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_total_used"})
    )

    out = (
        period_base.merge(person_by_period, on=["period_start", "period_label"], how="left")
        .merge(topup_by_period, on=["period_start", "period_label"], how="left")
        .merge(monthly_rollup_by_period, on=["period_start", "period_label"], how="left")
        .merge(total_by_period, on=["period_start", "period_label"], how="left")
    )
    for col in [
        "wages_person_level",
        "wages_summary_topups",
        "wages_monthly_sheet_rollup",
        "wages_total_used",
    ]:
        out[col] = out[col].fillna(0.0)
    return out.sort_values("period_start")


def load_monthly_expense_modes() -> pd.DataFrame:
    expenses = load_expenses()
    if expenses.empty:
        return pd.DataFrame(
            columns=[
                "month",
                "month_label",
                "recurring_monthly",
                "summarized_from_transactions",
                "summary_reference_rollups",
                "other_expenses_used",
                "total_used",
            ]
        )

    scoped = expenses[expenses["month"].notna()].copy()
    if not scoped.empty:
        scoped = scoped[~_inventory_purchase_mask(scoped["category"])].copy()
    if scoped.empty:
        return pd.DataFrame(
            columns=[
                "month",
                "month_label",
                "recurring_monthly",
                "summarized_from_transactions",
                "summary_reference_rollups",
                "other_expenses_used",
                "total_used",
            ]
        )

    summarized_categories = {
        "wages",
        "re-rental",
        "petrol",
        "bad debt",
        "unforeseen expense",
    }

    recurring = scoped[scoped["expense_kind"].str.lower() == "recurring_monthly"]
    summarized = scoped[
        (scoped["expense_kind"].str.lower() == "transaction")
        & (scoped["category"].str.lower().isin(summarized_categories))
    ]
    summary_reference = scoped[scoped["expense_kind"].str.lower() == "summary_rollup"]
    used_scope = scoped[scoped["expense_kind"].str.lower() != "summary_rollup"]
    other = used_scope.drop(recurring.index.union(summarized.index), errors="ignore")

    month_base = pd.DataFrame({"month": scoped["month"].drop_duplicates()})
    recurring_monthly = (
        recurring.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "recurring_monthly"})
    )
    summarized_from_transactions = (
        summarized.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "summarized_from_transactions"})
    )
    summary_reference_rollups = (
        summary_reference.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "summary_reference_rollups"})
    )
    other_expenses = (
        other.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "other_expenses_used"})
    )

    out = (
        month_base.merge(recurring_monthly, on="month", how="left")
        .merge(summarized_from_transactions, on="month", how="left")
        .merge(summary_reference_rollups, on="month", how="left")
        .merge(other_expenses, on="month", how="left")
    )
    for col in [
        "recurring_monthly",
        "summarized_from_transactions",
        "summary_reference_rollups",
        "other_expenses_used",
    ]:
        out[col] = out[col].fillna(0.0)
    out["total_used"] = (
        out["recurring_monthly"]
        + out["summarized_from_transactions"]
        + out["other_expenses_used"]
    )
    out["month_label"] = pd.PeriodIndex(out["month"], freq="M").strftime("%b %Y")
    return _sort_by_month(out)


def load_product_profitability() -> pd.DataFrame:
    items = fetch_dataframe(
        """
        SELECT
            it.id,
            it.invoice_id,
            i.invoice_number,
            i.event_date,
            COALESCE(i.payment_status, 'paid_full') AS payment_status,
            COALESCE(i.amount_paid, 0) AS amount_paid,
            it.item_name,
            it.item_type,
            it.quantity,
            it.unit_price,
            COALESCE(it.unit_cost, 0) AS unit_cost
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
        """
    )
    if items.empty:
        return pd.DataFrame(
            columns=[
                "item_name",
                "quantity",
                "revenue",
                "direct_cost",
                "allocated_expenses",
                "net_profit",
                "margin_pct",
            ]
        )

    linked_expenses = fetch_dataframe(
        """
        SELECT invoice_id, SUM(amount) AS linked_expenses
        FROM expenses
        WHERE invoice_id IS NOT NULL
          AND lower(COALESCE(expense_kind, 'transaction')) <> 'summary_rollup'
        GROUP BY invoice_id
        """
    )

    items["payment_status"] = (
        items["payment_status"].fillna("paid_full").astype(str).str.strip().str.lower()
    )
    items["amount_paid"] = pd.to_numeric(items["amount_paid"], errors="coerce").fillna(0.0)
    items["item_revenue_raw"] = items["quantity"] * items["unit_price"]
    items["item_cost"] = 0.0
    invoice_revenue = (
        items.groupby("invoice_id", as_index=False)["item_revenue_raw"]
        .sum()
        .rename(columns={"item_revenue_raw": "invoice_revenue"})
    )
    items = items.merge(invoice_revenue, on="invoice_id", how="left")
    inferred_full = (
        items["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (items["amount_paid"] <= 0)
    )
    items.loc[inferred_full, "amount_paid"] = items.loc[inferred_full, "invoice_revenue"]
    forced_full = (
        items["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (items["amount_paid"] < items["invoice_revenue"])
    )
    items.loc[forced_full, "amount_paid"] = items.loc[forced_full, "invoice_revenue"]
    items["amount_paid"] = items["amount_paid"].clip(lower=0.0)
    items["amount_paid"] = np.minimum(items["amount_paid"], items["invoice_revenue"].fillna(0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        items["recognition_ratio"] = np.where(
            items["payment_status"].isin(["paid_full", "full_paid", "paid"]),
            1.0,
            np.where(
                items["invoice_revenue"] > 0,
                items["amount_paid"] / items["invoice_revenue"],
                0.0,
            ),
        )
    items["recognition_ratio"] = (
        pd.to_numeric(items["recognition_ratio"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    )
    items["item_revenue"] = items["item_revenue_raw"] * items["recognition_ratio"]
    items = items.merge(linked_expenses, on="invoice_id", how="left")
    items["linked_expenses"] = items["linked_expenses"].fillna(0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        items["allocated_expense"] = np.where(
            items["invoice_revenue"] > 0,
            items["linked_expenses"] * (items["item_revenue"] / items["invoice_revenue"]),
            0.0,
        )

    items["net_profit"] = items["item_revenue"] - items["allocated_expense"]

    product_summary = (
        items.groupby("item_name", as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            revenue=("item_revenue", "sum"),
            direct_cost=("item_cost", "sum"),
            allocated_expenses=("allocated_expense", "sum"),
            net_profit=("net_profit", "sum"),
        )
        .sort_values("net_profit", ascending=False)
    )
    product_summary["margin_pct"] = np.where(
        product_summary["revenue"] > 0,
        (product_summary["net_profit"] / product_summary["revenue"]) * 100,
        0.0,
    )
    return product_summary


def load_product_type_profitability() -> pd.DataFrame:
    items = fetch_dataframe(
        """
        SELECT
            it.invoice_id,
            COALESCE(i.payment_status, 'paid_full') AS payment_status,
            COALESCE(i.amount_paid, 0) AS amount_paid,
            it.item_name,
            COALESCE(it.item_type, 'product') AS item_type,
            COALESCE(it.quantity, 0) AS quantity,
            COALESCE(it.unit_price, 0) AS unit_price,
            COALESCE(it.unit_cost, 0) AS unit_cost
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
        """
    )
    if items.empty:
        return pd.DataFrame(
            columns=[
                "product_group",
                "quantity",
                "revenue",
                "direct_cost",
                "allocated_expenses",
                "net_profit",
                "margin_pct",
            ]
        )

    linked_expenses = fetch_dataframe(
        """
        SELECT invoice_id, SUM(amount) AS linked_expenses
        FROM expenses
        WHERE invoice_id IS NOT NULL
          AND lower(COALESCE(expense_kind, 'transaction')) <> 'summary_rollup'
        GROUP BY invoice_id
        """
    )

    items["quantity"] = pd.to_numeric(items["quantity"], errors="coerce").fillna(0.0)
    items["unit_price"] = pd.to_numeric(items["unit_price"], errors="coerce").fillna(0.0)
    items["unit_cost"] = pd.to_numeric(items["unit_cost"], errors="coerce").fillna(0.0)
    items["payment_status"] = (
        items["payment_status"].fillna("paid_full").astype(str).str.strip().str.lower()
    )
    items["amount_paid"] = pd.to_numeric(items["amount_paid"], errors="coerce").fillna(0.0)
    items["item_revenue_raw"] = items["quantity"] * items["unit_price"]
    items["item_cost"] = 0.0
    items["product_group"] = items.apply(
        lambda row: _product_group_from_name(row.get("item_name", ""), row.get("item_type", "product")),
        axis=1,
    )

    invoice_revenue = (
        items.groupby("invoice_id", as_index=False)["item_revenue_raw"]
        .sum()
        .rename(columns={"item_revenue_raw": "invoice_revenue"})
    )
    items = items.merge(invoice_revenue, on="invoice_id", how="left")
    inferred_full = (
        items["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (items["amount_paid"] <= 0)
    )
    items.loc[inferred_full, "amount_paid"] = items.loc[inferred_full, "invoice_revenue"]
    forced_full = (
        items["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (items["amount_paid"] < items["invoice_revenue"])
    )
    items.loc[forced_full, "amount_paid"] = items.loc[forced_full, "invoice_revenue"]
    items["amount_paid"] = items["amount_paid"].clip(lower=0.0)
    items["amount_paid"] = np.minimum(items["amount_paid"], items["invoice_revenue"].fillna(0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        items["recognition_ratio"] = np.where(
            items["payment_status"].isin(["paid_full", "full_paid", "paid"]),
            1.0,
            np.where(
                items["invoice_revenue"] > 0,
                items["amount_paid"] / items["invoice_revenue"],
                0.0,
            ),
        )
    items["recognition_ratio"] = (
        pd.to_numeric(items["recognition_ratio"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    )
    items["item_revenue"] = items["item_revenue_raw"] * items["recognition_ratio"]
    items = items.merge(linked_expenses, on="invoice_id", how="left")
    items["linked_expenses"] = items["linked_expenses"].fillna(0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        items["allocated_expense"] = np.where(
            items["invoice_revenue"] > 0,
            items["linked_expenses"] * (items["item_revenue"] / items["invoice_revenue"]),
            0.0,
        )

    items["net_profit"] = items["item_revenue"] - items["allocated_expense"]

    grouped = (
        items.groupby("product_group", as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            revenue=("item_revenue", "sum"),
            direct_cost=("item_cost", "sum"),
            allocated_expenses=("allocated_expense", "sum"),
            net_profit=("net_profit", "sum"),
        )
        .sort_values("net_profit", ascending=False)
    )
    grouped["margin_pct"] = np.where(
        grouped["revenue"] > 0,
        (grouped["net_profit"] / grouped["revenue"]) * 100,
        0.0,
    )
    return grouped


def load_expense_breakdown_by_category() -> pd.DataFrame:
    expenses = load_expenses()
    if expenses.empty:
        return pd.DataFrame(columns=["category", "amount"])
    expenses = expenses[expenses["expense_kind"].str.lower() != "summary_rollup"]
    if expenses.empty:
        return pd.DataFrame(columns=["category", "amount"])
    return (
        expenses.groupby("category", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
    )


def load_supplier_expenses() -> pd.DataFrame:
    expenses = load_expenses()
    if expenses.empty:
        return pd.DataFrame(columns=["vendor", "amount"])

    suppliers = expenses[
        (expenses["category"].str.lower() == "re-rental")
        & (expenses["expense_kind"].str.lower() == "transaction")
        & (expenses["vendor"].fillna("").str.strip() != "")
        & (~expenses["vendor"].isin(["Legacy Sheet", "Summary Adjustment", "Monthly Ledger"]))
    ]
    if suppliers.empty:
        return pd.DataFrame(columns=["vendor", "amount"])

    return (
        suppliers.groupby("vendor", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
    )


def load_supplier_monthly_expenses() -> pd.DataFrame:
    expenses = load_expenses()
    if expenses.empty:
        return pd.DataFrame(columns=["month", "month_label", "vendor", "amount"])

    suppliers = expenses[
        (expenses["category"].str.lower() == "re-rental")
        & (expenses["expense_kind"].str.lower() == "transaction")
        & (expenses["vendor"].fillna("").str.strip() != "")
        & (~expenses["vendor"].isin(["Legacy Sheet", "Summary Adjustment", "Monthly Ledger"]))
        & (expenses["month"].notna())
    ]
    if suppliers.empty:
        return pd.DataFrame(columns=["month", "month_label", "vendor", "amount"])

    out = (
        suppliers.groupby(["month", "vendor"], as_index=False)["amount"]
        .sum()
        .sort_values(["month", "amount"], ascending=[True, False])
    )
    out["month_label"] = pd.PeriodIndex(out["month"], freq="M").strftime("%b %Y")
    return out


def load_supplier_performance_ranking() -> pd.DataFrame:
    expenses = load_expenses()
    invoice_level = load_invoice_level()
    if expenses.empty or invoice_level.empty:
        return pd.DataFrame(
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

    suppliers = expenses[
        (expenses["category"].fillna("").str.lower() == "re-rental")
        & (expenses["expense_kind"].fillna("").str.lower() == "transaction")
        & (expenses["vendor"].fillna("").str.strip() != "")
        & (~expenses["vendor"].isin(["Legacy Sheet", "Summary Adjustment", "Monthly Ledger"]))
    ].copy()
    if suppliers.empty:
        return pd.DataFrame(
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

    invoice_revenue = (
        invoice_level[["id", "revenue"]]
        .rename(columns={"id": "invoice_id", "revenue": "invoice_revenue"})
        .copy()
    )
    invoice_revenue["invoice_revenue"] = pd.to_numeric(
        invoice_revenue["invoice_revenue"], errors="coerce"
    ).fillna(0.0)

    supplier_base = (
        suppliers.groupby("vendor", as_index=False)
        .agg(
            supplier_spend=("amount", "sum"),
            transaction_count=("id", "count"),
        )
        .sort_values("supplier_spend", ascending=False)
    )

    vendor_invoice_pairs = suppliers[
        suppliers["invoice_id"].notna()
    ][["vendor", "invoice_id"]].drop_duplicates()

    linked_events = (
        vendor_invoice_pairs.groupby("vendor", as_index=False)["invoice_id"]
        .nunique()
        .rename(columns={"invoice_id": "linked_event_count"})
    )

    revenue_pairs = vendor_invoice_pairs.merge(invoice_revenue, on="invoice_id", how="left")
    revenue_pairs["invoice_revenue"] = revenue_pairs["invoice_revenue"].fillna(0.0)
    linked_revenue = (
        revenue_pairs.groupby("vendor", as_index=False)["invoice_revenue"]
        .sum()
        .rename(columns={"invoice_revenue": "linked_revenue"})
    )

    out = (
        supplier_base.merge(linked_events, on="vendor", how="left")
        .merge(linked_revenue, on="vendor", how="left")
    )
    out["linked_event_count"] = out["linked_event_count"].fillna(0).astype(int)
    out["linked_revenue"] = out["linked_revenue"].fillna(0.0)
    out["avg_spend_per_event"] = np.where(
        out["linked_event_count"] > 0,
        out["supplier_spend"] / out["linked_event_count"],
        out["supplier_spend"],
    )
    out["margin_impact_pct"] = np.where(
        out["linked_revenue"] > 0,
        (out["supplier_spend"] / out["linked_revenue"]) * 100,
        0.0,
    )
    return out.sort_values("supplier_spend", ascending=False)


def load_wages_reconciliation() -> pd.DataFrame:
    monthly = load_wages_period_summary("M")
    if monthly.empty:
        return pd.DataFrame(
            columns=[
                "month",
                "month_label",
                "wages_person_level",
                "wages_summary_topups",
                "wages_monthly_sheet_rollup",
                "wages_total_used",
            ]
        )
    out = monthly.copy()
    out["month"] = out["period_start"].dt.to_period("M").astype("string")
    out["month_label"] = out["period_label"]
    out = out[
        [
            "month",
            "month_label",
            "wages_person_level",
            "wages_summary_topups",
            "wages_monthly_sheet_rollup",
            "wages_total_used",
        ]
    ].copy()
    return _sort_by_month(out)


def load_inventory_snapshot() -> pd.DataFrame:
    items = fetch_dataframe(
        """
        SELECT
            i.id,
            i.sku,
            i.item_name,
            i.category,
            i.unit,
            i.current_quantity,
            i.reorder_level,
            i.default_rental_price,
            i.default_unit_cost,
            i.unit_weight_kg,
            i.active
        FROM inventory_items i
        ORDER BY i.item_name ASC
        """
    )
    if items.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "sku",
                "item_name",
                "category",
                "unit",
                "current_quantity",
                "reorder_level",
                "default_rental_price",
                "default_unit_cost",
                "unit_weight_kg",
                "active",
                "status",
            ]
        )

    demand = fetch_dataframe(
        """
        SELECT
            lower(trim(ii.item_name)) AS item_key,
            SUM(ii.quantity) AS total_rented_qty
        FROM invoice_items ii
        JOIN invoices i ON i.id = ii.invoice_id
        WHERE lower(ii.item_type) = 'product'
          AND lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
        GROUP BY lower(trim(item_name))
        """
    )
    if not demand.empty:
        items["item_key"] = items["item_name"].str.lower().str.strip()
        items = items.merge(demand, on="item_key", how="left")
        items = items.drop(columns=["item_key"])
    else:
        items["total_rented_qty"] = 0.0
    items["total_rented_qty"] = items["total_rented_qty"].fillna(0.0)

    items["status"] = np.where(
        items["current_quantity"] <= 0,
        "Out of Stock",
        np.where(
            items["current_quantity"] <= items["reorder_level"],
            "Low Stock",
            "In Stock",
        ),
    )
    return items


def load_inventory_upcoming_demand(days_ahead: int = 30) -> pd.DataFrame:
    demand = fetch_dataframe(
        """
        SELECT
            date(i.event_date) AS event_date,
            ii.item_name,
            SUM(ii.quantity) AS required_qty
        FROM invoices i
        JOIN invoice_items ii ON ii.invoice_id = i.id
        WHERE lower(ii.item_type) = 'product'
          AND lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
          AND date(i.event_date) >= date('now', 'localtime')
          AND date(i.event_date) <= date('now', 'localtime', '+' || ? || ' day')
        GROUP BY date(i.event_date), ii.item_name
        ORDER BY date(i.event_date), ii.item_name
        """,
        (int(days_ahead),),
    )
    if demand.empty:
        return pd.DataFrame(
            columns=[
                "event_date",
                "item_name",
                "required_qty",
                "current_quantity",
                "shortfall",
            ]
        )

    stock = load_inventory_snapshot()[
        ["item_name", "current_quantity"]
    ].copy()
    stock["item_key"] = stock["item_name"].str.lower().str.strip()

    demand["item_key"] = demand["item_name"].str.lower().str.strip()
    demand = demand.merge(
        stock[["item_key", "current_quantity"]],
        on="item_key",
        how="left",
    )
    demand = demand.drop(columns=["item_key"])
    demand["current_quantity"] = demand["current_quantity"].fillna(0.0)
    demand["shortfall"] = np.maximum(
        demand["required_qty"] - demand["current_quantity"],
        0.0,
    )
    return demand


def _compose_event_datetimes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["event_time"] = out["event_time"].fillna("11:00").astype(str).str.strip()
    out.loc[out["event_time"] == "", "event_time"] = "11:00"
    out["rental_hours"] = pd.to_numeric(out["rental_hours"], errors="coerce").fillna(24.0)
    out.loc[out["rental_hours"] <= 0, "rental_hours"] = 24.0

    out["start_dt"] = pd.to_datetime(
        out["event_date"].astype(str) + " " + out["event_time"].astype(str),
        errors="coerce",
    )
    out["end_dt"] = out["start_dt"] + pd.to_timedelta(out["rental_hours"], unit="h")
    return out


def load_event_calendar() -> pd.DataFrame:
    events = fetch_dataframe(
        """
        SELECT
            i.id AS invoice_id,
            i.invoice_number,
            i.event_date,
            COALESCE(i.event_time, '11:00') AS event_time,
            COALESCE(i.rental_hours, 24) AS rental_hours,
            COALESCE(i.event_timezone, 'America/Jamaica') AS event_timezone,
            COALESCE(NULLIF(i.event_location, ''), i.delivered_to, '') AS event_location,
            COALESCE(i.customer_name, '') AS customer_name,
            COALESCE(i.customer_phone, '') AS customer_phone,
            COALESCE(i.customer_email, '') AS customer_email,
            COALESCE(i.contact_detail, '') AS contact_detail,
            COALESCE(i.notes, '') AS notes,
            COALESCE(SUM(it.quantity * it.unit_price), 0) AS revenue
        FROM invoices i
        LEFT JOIN invoice_items it ON it.invoice_id = i.id
        WHERE i.event_date IS NOT NULL
          AND lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
        GROUP BY i.id
        ORDER BY date(i.event_date) ASC, COALESCE(i.event_time, '11:00') ASC
        """
    )
    if events.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "event_time",
                "rental_hours",
                "event_timezone",
                "event_location",
                "customer_name",
                "customer_phone",
                "customer_email",
                "contact_detail",
                "notes",
                "revenue",
                "equipment_summary",
                "start_dt",
                "end_dt",
            ]
        )

    lines = fetch_dataframe(
        """
        SELECT
            invoice_id,
            item_name,
            SUM(quantity) AS quantity
        FROM invoice_items
        GROUP BY invoice_id, item_name
        """
    )
    if not lines.empty:
        lines["summary_line"] = lines.apply(
            lambda row: f"{row['item_name']} x{float(row['quantity']):g}",
            axis=1,
        )
        equip = (
            lines.groupby("invoice_id", as_index=False)["summary_line"]
            .agg(", ".join)
            .rename(columns={"summary_line": "equipment_summary"})
        )
        events = events.merge(equip, on="invoice_id", how="left")
    else:
        events["equipment_summary"] = ""

    events["equipment_summary"] = events["equipment_summary"].fillna("")
    return _compose_event_datetimes(events)


def load_event_product_allocations() -> pd.DataFrame:
    allocations = fetch_dataframe(
        """
        SELECT
            i.id AS invoice_id,
            i.invoice_number,
            i.event_date,
            COALESCE(i.event_time, '11:00') AS event_time,
            COALESCE(i.rental_hours, 24) AS rental_hours,
            COALESCE(i.event_timezone, 'America/Jamaica') AS event_timezone,
            COALESCE(NULLIF(i.event_location, ''), i.delivered_to, '') AS event_location,
            COALESCE(i.customer_name, '') AS customer_name,
            ii.item_name,
            SUM(ii.quantity) AS required_qty
        FROM invoices i
        JOIN invoice_items ii ON ii.invoice_id = i.id
        WHERE i.event_date IS NOT NULL
          AND lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
          AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
          AND lower(COALESCE(ii.item_type, 'product')) = 'product'
        GROUP BY
            i.id,
            i.invoice_number,
            i.event_date,
            i.event_time,
            i.rental_hours,
            i.event_timezone,
            i.event_location,
            i.delivered_to,
            i.customer_name,
            ii.item_name
        ORDER BY date(i.event_date) ASC, COALESCE(i.event_time, '11:00') ASC
        """
    )
    if allocations.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "event_time",
                "rental_hours",
                "event_timezone",
                "event_location",
                "customer_name",
                "item_name",
                "required_qty",
                "start_dt",
                "end_dt",
            ]
        )
    return _compose_event_datetimes(allocations)


def load_inventory_availability_schedule() -> pd.DataFrame:
    allocations = load_event_product_allocations()
    if allocations.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "event_time",
                "event_location",
                "customer_name",
                "item_name",
                "required_qty",
                "stock_quantity",
                "other_overlapping_qty",
                "concurrent_total_qty",
                "available_before_this",
                "available_with_this_event",
                "shortfall",
                "start_dt",
                "end_dt",
            ]
        )

    stock = load_inventory_snapshot()[["item_name", "current_quantity"]].copy()
    stock["item_key"] = stock["item_name"].astype(str).str.lower().str.strip()
    inventory_catalog = [
        {
            "item_key": str(row["item_key"]),
            "signature": _inventory_name_signature(row["item_name"]),
            "keywords": _inventory_name_keywords(row["item_name"]),
        }
        for _, row in stock.iterrows()
        if str(row.get("item_key", "")).strip()
    ]

    allocations = allocations.copy()
    allocations["item_key"] = allocations["item_name"].apply(
        lambda value: _resolve_inventory_item_key(value, inventory_catalog)
    )
    allocations = allocations.merge(
        stock[["item_key", "current_quantity"]],
        on="item_key",
        how="left",
    )
    allocations["stock_quantity"] = allocations["current_quantity"].fillna(0.0)
    allocations = allocations.drop(columns=["current_quantity"])

    allocations = allocations[allocations["start_dt"].notna()].copy()
    if allocations.empty:
        return pd.DataFrame(
            columns=[
                "invoice_id",
                "invoice_number",
                "event_date",
                "event_time",
                "event_location",
                "customer_name",
                "item_name",
                "required_qty",
                "stock_quantity",
                "other_overlapping_qty",
                "concurrent_total_qty",
                "available_before_this",
                "available_with_this_event",
                "shortfall",
                "start_dt",
                "end_dt",
            ]
        )

    rows: list[dict] = []
    for _, group in allocations.groupby("item_key", sort=False):
        g = group.sort_values(["start_dt", "end_dt", "invoice_id"]).reset_index(drop=True)
        starts = g["start_dt"].to_numpy()
        ends = g["end_dt"].to_numpy()
        required = pd.to_numeric(g["required_qty"], errors="coerce").fillna(0.0).to_numpy()
        stock_qty = float(g["stock_quantity"].iloc[0])

        for idx in range(len(g)):
            overlap_mask = (starts < ends[idx]) & (ends > starts[idx])
            concurrent_total = float(required[overlap_mask].sum())
            current_req = float(required[idx])
            other_overlap = max(concurrent_total - current_req, 0.0)
            available_before = stock_qty - other_overlap
            available_with = stock_qty - concurrent_total
            shortfall = max(-available_with, 0.0)

            base = g.iloc[idx].to_dict()
            base["required_qty"] = current_req
            base["stock_quantity"] = stock_qty
            base["other_overlapping_qty"] = other_overlap
            base["concurrent_total_qty"] = concurrent_total
            base["available_before_this"] = available_before
            base["available_with_this_event"] = available_with
            base["shortfall"] = shortfall
            rows.append(base)

    out = pd.DataFrame(rows)
    out = out.drop(columns=["item_key"], errors="ignore")
    return out.sort_values(["start_dt", "item_name", "invoice_number"]).reset_index(drop=True)


def load_inventory_live_status(reference_time: pd.Timestamp | None = None) -> pd.DataFrame:
    stock = load_inventory_snapshot().copy()
    if stock.empty:
        return pd.DataFrame(
            columns=[
                "item_name",
                "current_quantity",
                "reserved_now",
                "usable_now",
                "unit",
                "status",
            ]
        )

    allocations = load_event_product_allocations()
    stock["item_key"] = stock["item_name"].astype(str).str.lower().str.strip()
    inventory_catalog = [
        {
            "item_key": str(row["item_key"]),
            "signature": _inventory_name_signature(row["item_name"]),
            "keywords": _inventory_name_keywords(row["item_name"]),
        }
        for _, row in stock.iterrows()
        if str(row.get("item_key", "")).strip()
    ]
    now_ts = reference_time if reference_time is not None else pd.Timestamp.now()
    if allocations.empty:
        stock["reserved_now"] = 0.0
        stock["usable_now"] = stock["current_quantity"]
        return stock[
            [
                "item_name",
                "current_quantity",
                "reserved_now",
                "usable_now",
                "unit",
                "status",
            ]
        ]

    active = allocations[
        (allocations["start_dt"] <= now_ts) & (allocations["end_dt"] > now_ts)
    ].copy()
    if active.empty:
        stock["reserved_now"] = 0.0
        stock["usable_now"] = stock["current_quantity"]
        return stock[
            [
                "item_name",
                "current_quantity",
                "reserved_now",
                "usable_now",
                "unit",
                "status",
            ]
        ]

    active["item_key"] = active["item_name"].apply(
        lambda value: _resolve_inventory_item_key(value, inventory_catalog)
    )
    reserved = (
        active.groupby("item_key", as_index=False)["required_qty"]
        .sum()
        .rename(columns={"required_qty": "reserved_now"})
    )

    stock = stock.merge(reserved, on="item_key", how="left")
    stock["reserved_now"] = stock["reserved_now"].fillna(0.0)
    stock["usable_now"] = stock["current_quantity"] - stock["reserved_now"]
    return stock[
        [
            "item_name",
            "current_quantity",
            "reserved_now",
            "usable_now",
            "unit",
            "status",
        ]
    ]
