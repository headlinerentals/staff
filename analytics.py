from __future__ import annotations

import numpy as np
import pandas as pd

from db import fetch_dataframe


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
            COALESCE(i.payment_notes, '') AS payment_notes,
            COALESCE(SUM(it.quantity * it.unit_price), 0) AS revenue,
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
                "amount_outstanding",
                "payment_notes",
                "payment_reminder",
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
    merged["amount_paid"] = pd.to_numeric(merged["amount_paid"], errors="coerce").fillna(0.0)
    inferred_full = (
        merged["payment_status"].isin(["paid_full", "full_paid", "paid"])
        & (merged["amount_paid"] <= 0)
    )
    merged.loc[inferred_full, "amount_paid"] = merged.loc[inferred_full, "revenue"]
    merged["amount_paid"] = merged["amount_paid"].clip(lower=0.0)
    merged["amount_outstanding"] = (merged["revenue"] - merged["amount_paid"]).clip(lower=0.0)
    merged["payment_reminder"] = np.where(
        merged["amount_outstanding"] > 0.01,
        "Deposit/partial payment logged. Balance still outstanding.",
        "Paid in full.",
    )
    merged["gross_profit"] = merged["revenue"] - merged["item_cost"]
    merged["net_profit"] = merged["gross_profit"] - merged["invoice_expenses"]
    merged["event_date"] = pd.to_datetime(merged["event_date"], errors="coerce")
    merged["month"] = merged["event_date"].dt.to_period("M").astype("string")
    merged["year"] = merged["event_date"].dt.year
    return merged


def load_expenses() -> pd.DataFrame:
    expenses = fetch_dataframe("SELECT * FROM expenses")
    if expenses.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "expense_date",
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
    expenses["month"] = expenses["expense_date"].dt.to_period("M").astype("string")
    expenses["year"] = expenses["expense_date"].dt.year
    return expenses


def load_monthly_summary() -> pd.DataFrame:
    invoice_level = load_invoice_level()
    expenses = load_expenses()
    adjustments = fetch_dataframe(
        """
        SELECT month, SUM(amount) AS adjustments
        FROM monthly_adjustments
        GROUP BY month
        """
    )

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
    if not expenses.empty:
        general_expenses = (
            expenses[
                (expenses["invoice_id"].isna())
                & (expenses["expense_kind"].str.lower() != "summary_rollup")
            ]
            .dropna(subset=["month"])
            .groupby("month", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "general_expenses"})
        )

    recurring_expenses = pd.DataFrame(columns=["month", "recurring_expenses"])
    summarized_expenses = pd.DataFrame(columns=["month", "summarized_expenses"])
    if not expenses.empty:
        recurring_expenses = (
            expenses[
                (expenses["invoice_id"].isna())
                & (expenses["expense_kind"].str.lower() == "recurring_monthly")
                & (expenses["month"].notna())
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
            expenses[
                (expenses["expense_kind"].str.lower() == "transaction")
                & (expenses["category"].str.lower().isin(summarized_categories))
                & (expenses["month"].notna())
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

    summary["total_expenses"] = (
        summary["item_cost"] + summary["linked_expenses"] + summary["general_expenses"]
    )
    summary["net_profit"] = summary["revenue"] - summary["total_expenses"]
    summary["net_profit_after_adjustments"] = summary["net_profit"] - summary["adjustments"]

    period = pd.PeriodIndex(summary["month"], freq="M")
    summary["month_label"] = period.strftime("%b %Y")
    summary["year"] = period.year
    return _sort_by_month(summary)


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

    items["item_revenue"] = items["quantity"] * items["unit_price"]
    items["item_cost"] = items["quantity"] * items["unit_cost"]
    invoice_revenue = (
        items.groupby("invoice_id", as_index=False)["item_revenue"]
        .sum()
        .rename(columns={"item_revenue": "invoice_revenue"})
    )
    items = items.merge(invoice_revenue, on="invoice_id", how="left")
    items = items.merge(linked_expenses, on="invoice_id", how="left")
    items["linked_expenses"] = items["linked_expenses"].fillna(0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        items["allocated_expense"] = np.where(
            items["invoice_revenue"] > 0,
            items["linked_expenses"] * (items["item_revenue"] / items["invoice_revenue"]),
            0.0,
        )

    items["net_profit"] = (
        items["item_revenue"] - items["item_cost"] - items["allocated_expense"]
    )

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


def load_wages_reconciliation() -> pd.DataFrame:
    expenses = load_expenses()
    if expenses.empty:
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

    wages = expenses[
        expenses["category"].fillna("").str.lower() == "wages"
    ].copy()
    if wages.empty:
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

    wages = wages[wages["month"].notna()]
    if wages.empty:
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

    person_level = wages[
        ~wages["vendor"].isin(["Legacy Sheet", "Summary Adjustment", "Monthly Ledger"])
    ]
    summary_topups = wages[
        wages["vendor"].isin(["Legacy Sheet", "Summary Adjustment"])
    ]
    monthly_rollup = wages[
        (wages["vendor"] == "Monthly Ledger")
    ]

    all_months = pd.DataFrame({"month": wages["month"].drop_duplicates()})
    person_by_month = (
        person_level.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_person_level"})
    )
    topup_by_month = (
        summary_topups.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_summary_topups"})
    )
    monthly_rollup_by_month = (
        monthly_rollup.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_monthly_sheet_rollup"})
    )
    total_by_month = (
        wages.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "wages_total_used"})
    )

    out = (
        all_months.merge(person_by_month, on="month", how="left")
        .merge(topup_by_month, on="month", how="left")
        .merge(monthly_rollup_by_month, on="month", how="left")
        .merge(total_by_month, on="month", how="left")
    )
    for col in [
        "wages_person_level",
        "wages_summary_topups",
        "wages_monthly_sheet_rollup",
        "wages_total_used",
    ]:
        out[col] = out[col].fillna(0.0)

    out["month_label"] = pd.PeriodIndex(out["month"], freq="M").strftime("%b %Y")
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

    allocations = allocations.copy()
    allocations["item_key"] = allocations["item_name"].astype(str).str.lower().str.strip()
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

    active["item_key"] = active["item_name"].astype(str).str.lower().str.strip()
    reserved = (
        active.groupby("item_key", as_index=False)["required_qty"]
        .sum()
        .rename(columns={"required_qty": "reserved_now"})
    )

    stock["item_key"] = stock["item_name"].astype(str).str.lower().str.strip()
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
