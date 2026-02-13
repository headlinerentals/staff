# Headline Rentals Staff App

A lightweight app to manage:
- invoices with line items
- create new invoices from scratch in-app
- invoice builder auto-fee controls:
  - GCT at 15%
  - delivery fee (manual entry)
  - set-up fee
- real invoice payment options:
  - paid in full
  - 50% deposit (auto-calculated after all charges)
  - automatic finance reminders for outstanding balance
- document mode support:
  - `Price Quote` (does not affect Finance Hub/Inventory)
  - `Real Invoice - Pending` (does not affect until confirmed)
  - `Real Invoice - Confirmed` (affects Finance Hub/Inventory)
- expense transactions recorded individually
- dedicated **Supplier Re-Rental** entry section outside Finance Hub lock
- expense types (`transaction`, `recurring monthly`, `summary reference`)
- inventory records with stock movements and rental pricing
- automatic inventory movement entries for confirmed real invoices
- product-level profitability
- supplier-level re-rental reporting
- monthly and yearly summaries
- legacy CSV imports from your current spreadsheets
- PDF quote/invoice intake (auto-extract invoice fields + items)
- PNG/PDF attachments per invoice
- one-click branded invoice downloads (PDF and PNG) with your logo and business name
- one-click share/send center for WhatsApp, Gmail, Messages (SMS), and Instagram
- optional direct API send from app via Gmail SMTP, Twilio SMS/WhatsApp, Meta WhatsApp Cloud API, and Meta Instagram Messaging API
- Finance Hub password lock for confidentiality (profitability, expenses, wages, reports)
- full app remains open; only Finance Hub data is locked until password unlock
- build log for all quotes/invoices with action, person/device, and timestamp
- built-in `Reason Wid Watto (WattBot)` assistant for navigation and operations Q&A
- floating bottom-right WattBot mini chat widget with customizable avatar icon
- subtle WattBot launcher pulse animation (playful, low-distraction)
- WattBot finance privacy: finance answers only after Finance Hub unlock
- inventory pricing list with default rental pricing per item
- staff training arcade in Inventory with multiple challenge games (quiz + match + ops puzzle)
- optional Shopify orders CSV import
- clean high-readability day theme
- persistent business profile settings for stable long-term team use
- simplified mobile-friendly sidebar navigation mode
- mobile-optimized UI:
  - stacked form layout on small screens
  - touch-sized controls and 16px input fonts (better iPhone typing)
  - scrollable tabs and table containers
  - in-page quick section switch for phone workflows

## 1) Setup

```bash
cd "/Users/oshaniwatto/Documents/New project"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Run

```bash
streamlit run app.py
```

## 2.1) Persistent Storage (Deploy)

Use a persistent disk path in hosting and point the app to it with env vars:

```bash
# Recommended (single base directory)
export HR_DATA_DIR=/path/to/persistent-disk/headline-rentals

# Optional overrides
export HR_DB_PATH=/path/to/persistent-disk/headline-rentals/finance_hub.db
export HR_UPLOADS_DIR=/path/to/persistent-disk/headline-rentals/uploads
```

Priority:
1. `HR_DB_PATH` (explicit db file)
2. `HR_DATA_DIR/finance_hub.db`
3. local default (`finance_hub.db` beside code)

For deployment platforms, mount a persistent volume (disk) and set `HR_DATA_DIR` to that mount path.

## 3) Workflow

1. In sidebar, set **Reporting Start Month** to `January 2026`.
2. App uses a fixed **Day** theme for readability.
3. In sidebar, set **Experience Mode** to `Guided Visual` for chart-first learning.
4. Use the in-page **Quick switch (mobile)** dropdown to move sections quickly.
5. In sidebar **Access Control**, set a Finance Hub password (required to open Finance Hub).
6. Use the **Finance Hub** section for dashboard, non-supplier expenses, reports, and invoice profit tables.
7. If Finance lock is enabled, unlock Finance Hub in that section using password.
8. Use sidebar **Reason Wid Watto (WattBot)** for commands like:
   - `go to inventory`
   - `supplier spend`
   - `inventory status`
   - `finance summary` (requires finance unlock)
9. Set your WattBot icon image in sidebar **Business Profile > WattBot Avatar**.
10. Use the **Build Invoice** section only to create/edit quotes/invoices and download/send customer files.
11. Use the **Inventory** section for stock, pricing list, and staff training games.
12. Open **Import Legacy Data** and run full import.
13. In **Build Invoice**, upload a PDF quote/invoice (or paste local file path) and click extract.
14. In Build Invoice, choose clearly between:
   - `Price Quote`
   - `Real Invoice` (then choose `Pending` or `Confirmed`)
15. Fill event/customer/items and save.
16. In **Build Invoice > Auto Fees**, optionally enable:
   - `Add GCT (15%)`
   - `Add Delivery Fee` (manual amount)
   - `Add Set-Up Fee`
17. For real invoices, choose payment terms:
   - `Paid In Full`
   - `50% Deposit (Balance Later)`
18. Save the document; selected fees are added as invoice lines automatically, and deposit/payment status is sent to Finance Hub.
19. Use **Customer Invoice Download Center** in Build Invoice to download a ready-to-send PDF or PNG quote/invoice.
20. In the same Build Invoice section, use **One-Click Share & Send API Center** to:
   - open WhatsApp/Gmail/Messages/Instagram with pre-filled message
   - or send directly via configured APIs
21. Add non-supplier expense transactions in **Finance Hub > Expenses**:
   - `Transaction` for day/invoice-level costs
   - `Recurring Monthly` for fixed monthly tools/services (ChatGPT, Shopify, ads)
   - `Summary Reference` only for rollup tracking (excluded from totals)
22. Add supplier costs in **Supplier Re-Rental** (staff-accessible, still included in Finance Hub totals/reports).
23. Attach PNG/PDF evidence to each quote/invoice in **Build Invoice**.
24. Use **Build Log (Quotes + Invoices)** in Build Invoice to review who created/updated documents.
25. Use **Finance Hub > Reports** for monthly/yearly summary, supplier spend, and wages reconciliation.
26. Open **Finance Hub > Invoice Profit** to track deposit invoices, outstanding balances, and mark balances as paid.
27. Open **Mobile & Team** for phone install steps and team backup downloads.

If you imported data with an older version, run **Run Legacy Cleanup (recommended once)** before re-importing.

## Notes

- Database path supports env overrides:
  - `HR_DB_PATH` (exact file path)
  - `HR_DATA_DIR` (directory containing `finance_hub.db`)
- Uploads path supports env override:
  - `HR_UPLOADS_DIR` (defaults to `<db-folder>/uploads`)
- App favicon path: `assets/headline-rentals-logo.png` (replace this file with your brand logo).
- `net_profit_after_adjustments` subtracts monthly adjustments (for example: inventory purchases).
- Product net profit allocates invoice-linked expenses proportionally by each item's revenue share.
- During monthly CSV import, rollup columns such as `Re-Rental`, `Wages`, and `Petrol (Add By Invoice #)` are imported as `summary reference` entries and excluded from profit totals to avoid double-counting.
- Reports include a wages reconciliation view showing: person-level wages, invoice-summary top-ups, and any monthly-sheet rollup (should remain zero after cleanup/re-import).
- Shopify integration is via CSV export import (safe first step before live API sync).
- Inventory pricing list is powered by each item's `default rental price` and can be exported to CSV.
- Inventory Training Arcade includes:
  - Price Duel Quiz (multiple-choice)
  - Match-Up Arena (item-to-price matching)
  - Ops Escape Puzzle (real inventory/event scenarios)
- API sending requires your own provider credentials in sidebar **Send Integrations (Optional)**.
- Gmail direct send uses SMTP with an app password (recommended: dedicated Gmail account for business sends).
- Quotes and pending real invoices are intentionally excluded from finance and auto-inventory-movement computations.
- Finance privacy is enforced by Finance Hub unlock state; non-finance sections remain open.
