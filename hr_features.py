"""Headline Rentals: client acceptance page, celebrations, smart add-ons, delivery day mode,
damage and loss, QR labels, monthly board pack and Ask Headline.

Everything here stores its data in its own hr_* tables. It never edits or deletes rows in the
app's existing tables (invoices, items, expenses, inventory)."""

from __future__ import annotations

import html
import io
import json
import re
import secrets
import urllib.parse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import db
from messaging import whatsapp_link

JM = ZoneInfo("America/Jamaica")
CELEBRATIONS = ["Not set", "Birthday", "Anniversary", "Wedding", "Graduation", "Baby shower",
                "Christening", "Church event", "Corporate", "Other"]
RECURRING_KINDS = {"Birthday", "Anniversary", "Wedding"}
DAMAGE_KINDS = ["Damaged", "Lost", "Needs cleaning"]
DAMAGE_STATUS = ["Open", "In repair", "Fixed", "Charged to client", "Written off"]
FEE_PATTERN = r"delivery|set-up|setup|gct|discount|imported invoice|fee|surcharge|multiplier|deposit"


def now_jm() -> datetime:
    return datetime.now(JM)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def jmd(v: float) -> str:
    v = float(v or 0.0)
    return f"-JM${abs(v):,.2f}" if v < 0 else f"JM${v:,.2f}"


def first_name(name: str) -> str:
    parts = [p for p in str(name or "").replace("(", " ").split() if p[:1].isalpha()]
    return parts[0].title() if parts else "there"


# --------------------------------------------------------------------------- storage
_TABLES = """
CREATE TABLE IF NOT EXISTS hr_quote_links (
    token TEXT PRIMARY KEY, invoice_id INTEGER NOT NULL, created_at TEXT,
    status TEXT NOT NULL DEFAULT 'sent', viewed_at TEXT, accepted_at TEXT, accepted_name TEXT,
    accepted_note TEXT, slip_name TEXT, slip_mime TEXT, slip BLOB, reviewed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hr_celebrations (
    invoice_id INTEGER PRIMARY KEY, invoice_number TEXT, customer_name TEXT, phone TEXT,
    kind TEXT, other TEXT, honoree TEXT, celebration_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS hr_greetings_sent (gkey TEXT PRIMARY KEY, sent_at TEXT);
CREATE TABLE IF NOT EXISTS hr_driver_checks (
    invoice_id INTEGER, stage TEXT, item TEXT, done INTEGER NOT NULL DEFAULT 0, at TEXT,
    PRIMARY KEY (invoice_id, stage, item)
);
CREATE TABLE IF NOT EXISTS hr_driver_stops (
    invoice_id INTEGER, stage TEXT, received_by TEXT, completed_at TEXT, photo BLOB,
    PRIMARY KEY (invoice_id, stage)
);
CREATE TABLE IF NOT EXISTS hr_damage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, invoice_number TEXT, customer_name TEXT,
    item_name TEXT, qty REAL, kind TEXT, charge REAL, note TEXT, status TEXT, created_at TEXT,
    resolved_at TEXT, photo BLOB
);
"""


@st.cache_resource(show_spinner=False)
def _tables_ready(db_path: str) -> bool:
    with db.get_connection() as conn:
        conn.executescript(_TABLES)
    return True


def ensure_tables() -> None:
    try:
        _tables_ready(str(db.DB_PATH))
    except Exception:
        with db.get_connection() as conn:
            conn.executescript(_TABLES)


def q(sql: str, params=()) -> pd.DataFrame:
    ensure_tables()
    try:
        return db.fetch_dataframe(sql, params)
    except Exception:
        _tables_ready.clear()
        ensure_tables()
        return db.fetch_dataframe(sql, params)


def ex(sql: str, params=()) -> None:
    ensure_tables()
    with db.get_connection() as conn:
        conn.execute(sql, params)


def shrink_image(raw: bytes, max_side: int = 1280) -> bytes:
    """Phone photos are big; keep a sharp but small JPEG so backups stay light."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception:
        return raw


# --------------------------------------------------------------------------- 1. quote acceptance
def quote_link_for(invoice_id: int, base_url: str) -> str:
    ensure_tables()
    existing = q("SELECT token FROM hr_quote_links WHERE invoice_id = ? ORDER BY created_at DESC LIMIT 1", (int(invoice_id),))
    if not existing.empty:
        token = str(existing.iloc[0]["token"])
    else:
        token = secrets.token_urlsafe(9)
        ex("INSERT INTO hr_quote_links (token, invoice_id, created_at) VALUES (?, ?, ?)",
           (token, int(invoice_id), now_jm().isoformat()))
    base = (base_url or "https://headlinerentals.streamlit.app").strip().rstrip("/")
    return f"{base}/?accept={token}"


def render_share_acceptance(invoice_id: int, invoice_number: str, customer_name: str, phone: str,
                            base_url: str, key: str) -> None:
    """Staff side: one tap to get the client's acceptance link and a ready WhatsApp message."""
    with st.container(key=f"hr_accept_share_{key}"):
        st.markdown(
            "<div class='hr-h' style='margin-top:6px'>Client acceptance link"
            "<span class='meta'>They view, accept and upload the deposit slip</span></div>",
            unsafe_allow_html=True,
        )
        if st.button("Create acceptance link", key=f"hr_accept_make_{key}", icon=":material/link:",
                     use_container_width=True):
            st.session_state[f"hr_accept_url_{key}"] = quote_link_for(invoice_id, base_url)
        url = st.session_state.get(f"hr_accept_url_{key}")
        if url:
            msg = (f"Hi {first_name(customer_name)}, here is your Headline Rentals quote #{invoice_number}. "
                   f"You can view it, accept it and upload your deposit slip here: {url}")
            st.code(url, language=None)
            c1, c2 = st.columns(2)
            if str(phone or "").strip():
                c1.link_button("Send on WhatsApp", whatsapp_link(phone, msg), use_container_width=True,
                               icon=":material/chat:")
            c2.download_button("Copy message as text file", msg.encode("utf-8"), file_name=f"quote_{invoice_number}_link.txt",
                               use_container_width=True, key=f"hr_accept_txt_{key}")
            st.caption("The PDF and image buttons above still work exactly as before. The link is an extra option.")


def render_client_acceptance_page(token: str, *, bundle, payload_builder, png_renderer, pdf_renderer,
                                  bank: dict, logo_path) -> None:
    """Public page a client opens from the link. Shows only their own quote."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], .st-key-hr_nav, .hr-topbar { display: none !important; }
        .hr-client-page { max-width: 760px; margin: 0 auto; }
        .hr-cp-hero { border-radius: 24px; padding: 22px; color: #fff; margin-bottom: 14px;
            background: radial-gradient(120% 140% at 100% 0%, rgba(63,208,245,.28), rgba(63,208,245,0) 50%),
                        radial-gradient(90% 120% at 0% 100%, rgba(89,39,229,.6), rgba(89,39,229,0) 60%), #0E0E1A; }
        .hr-cp-hero .e { font-size: 11.5px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; color: #8FE3FA; }
        .hr-cp-hero .t { font-family: 'Poppins', sans-serif; font-weight: 600; font-size: 28px; line-height: 1.15; margin: 6px 0 4px; }
        .hr-cp-hero .s { color: #C9C7D9; font-size: 14px; }
        .hr-cp-bank { background: #fff; border: 1px solid #E9E7F2; border-radius: 18px; padding: 14px 16px; font-size: 14.5px; line-height: 1.6; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    link = q("SELECT * FROM hr_quote_links WHERE token = ?", (str(token),))
    if link.empty:
        st.error("This quote link is not valid. Please contact Headline Rentals for a new one.")
        return
    row = link.iloc[0]
    invoice_id = int(row["invoice_id"])
    if not row.get("viewed_at"):
        ex("UPDATE hr_quote_links SET viewed_at = ? WHERE token = ?", (now_jm().isoformat(), str(token)))
    try:
        header, items = bundle(invoice_id)
    except Exception:
        st.error("We could not find this quote. Please contact Headline Rentals.")
        return
    payload = payload_builder(header, items)
    number = str(header.get("invoice_number", "") or "")
    name = str(header.get("customer_name", "") or "")
    total = float(payload.get("total", 0.0) or 0.0)
    st.markdown(
        f"<div class='hr-client-page'><div class='hr-cp-hero'><div class='e'>Headline Event Rentals</div>"
        f"<div class='t'>Your quote #{esc(number)}</div>"
        f"<div class='s'>Prepared for {esc(name or 'you')} · Total {esc(jmd(total))}</div></div></div>",
        unsafe_allow_html=True,
    )
    try:
        png = _cached_png(invoice_id, png_renderer, payload)
        st.image(png, use_container_width=True)
    except Exception:
        st.info("Your quote preview is loading slowly. You can download the PDF below.")
    try:
        st.download_button("Download PDF", data=pdf_renderer(payload), file_name=f"Headline_Quote_{number}.pdf",
                           mime="application/pdf", use_container_width=True, key="hr_cp_pdf")
    except Exception:
        pass

    if str(row.get("status")) == "accepted":
        when = str(row.get("accepted_at") or "")[:16].replace("T", " ")
        st.success(f"Accepted by {row.get('accepted_name') or 'you'} on {when}. Thank you! We will confirm your booking shortly.")
        return

    st.markdown("<div class='hr-h'>Accept your quote</div>", unsafe_allow_html=True)
    with st.form("hr_cp_accept"):
        full_name = st.text_input("Your full name", value=name)
        slip = st.file_uploader("Deposit slip or screenshot (optional now, you can send it later)",
                                type=["png", "jpg", "jpeg", "webp", "pdf"])
        note = st.text_area("Anything we should know? (optional)", height=80)
        agree = st.checkbox("I accept this quote and the terms shown on it")
        sent = st.form_submit_button("Accept quote", type="primary", use_container_width=True)
    if sent:
        if not agree or not full_name.strip():
            st.error("Please add your name and tick the box to accept.")
        else:
            slip_bytes, slip_name, slip_mime = None, None, None
            if slip is not None:
                raw = slip.getvalue()
                if len(raw) > 8 * 1024 * 1024:
                    st.error("That file is over 8MB. Please send a smaller photo or screenshot.")
                    return
                slip_mime = slip.type or "application/octet-stream"
                slip_bytes = raw if slip_mime == "application/pdf" else shrink_image(raw, 1600)
                slip_name = slip.name
            ex("UPDATE hr_quote_links SET status='accepted', accepted_at=?, accepted_name=?, accepted_note=?, "
               "slip_name=?, slip_mime=?, slip=? WHERE token=?",
               (now_jm().isoformat(), full_name.strip(), note.strip(), slip_name, slip_mime, slip_bytes, str(token)))
            st.success("Thank you! Your quote is accepted. Headline Rentals will confirm your booking shortly.")
            st.balloons()
    st.markdown(
        f"<div class='hr-cp-bank'><b>Deposit details</b><br>{esc(bank.get('bank_account_name',''))}<br>"
        f"{esc(bank.get('bank_account_type',''))} · Branch {esc(bank.get('bank_branch',''))}<br>"
        f"Account #{esc(bank.get('bank_account_number',''))}</div>",
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=600, show_spinner=False, max_entries=20, hash_funcs={dict: lambda d: json.dumps(d, sort_keys=True, default=str)})
def _cached_png(invoice_id: int, _renderer, payload: dict) -> bytes:
    return _renderer(payload)


def nice_when(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
    except Exception:
        return str(iso or "")[:16].replace("T", " ")


def accepted_quotes(unreviewed_only: bool = True) -> pd.DataFrame:
    sql = ("SELECT l.token, l.invoice_id, l.accepted_at, l.accepted_name, l.accepted_note, l.slip_name, l.slip_mime, "
           "l.slip, l.reviewed, i.invoice_number, i.customer_name, i.customer_phone, "
           "lower(COALESCE(i.document_type,'invoice')) AS document_type "
           "FROM hr_quote_links l JOIN invoices i ON i.id = l.invoice_id WHERE l.status = 'accepted'")
    if unreviewed_only:
        sql += " AND l.reviewed = 0"
    return q(sql + " ORDER BY l.accepted_at DESC")


def render_accepted_quotes_panel(open_quote_cb) -> None:
    acc = accepted_quotes(True)
    if acc.empty:
        return
    st.markdown(f"<div class='hr-h'>Accepted by clients<span class='meta'>{len(acc)} to confirm</span></div>",
                unsafe_allow_html=True)
    for _, r in acc.iterrows():
        when = nice_when(r["accepted_at"])
        slip_txt = "Deposit slip attached" if r["slip_name"] else "No slip yet"
        st.markdown(
            f"<div class='hr-row'><div class='ic ic-green'>✓</div><div class='tx'>"
            f"<div class='t1'>{esc(r['customer_name'] or r['accepted_name'])} accepted quote #{esc(r['invoice_number'])}</div>"
            f"<div class='t2'>{esc(when)} · {slip_txt}{(' · ' + esc(r['accepted_note'])) if r['accepted_note'] else ''}</div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.container(key=f"hr_btnrow_acc_{r['token']}").columns(3)
        if r["slip_name"] and r["slip"] is not None:
            c1.download_button("Deposit slip", data=bytes(r["slip"]), file_name=str(r["slip_name"]),
                               mime=str(r["slip_mime"] or "application/octet-stream"),
                               key=f"hr_acc_slip_{r['token']}", use_container_width=True)
        if c2.button("Confirm", key=f"hr_acc_open_{r['token']}", type="primary", use_container_width=True):
            open_quote_cb(int(r["invoice_id"]))
        if c3.button("Mark done", key=f"hr_acc_done_{r['token']}", use_container_width=True):
            ex("UPDATE hr_quote_links SET reviewed = 1 WHERE token = ?", (str(r["token"]),))
            st.rerun()
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- 2. smart add-ons
@st.cache_data(ttl=600, show_spinner=False)
def _baskets() -> tuple[dict, dict, dict]:
    items = q("SELECT invoice_id, item_name, quantity, unit_price FROM invoice_items")
    if items.empty:
        return {}, {}, {}
    items["name"] = items["item_name"].fillna("").astype(str).str.strip()
    items = items[(items["name"] != "") & ~items["name"].str.lower().str.contains(FEE_PATTERN, regex=True)]
    items["key"] = items["name"].str.lower()
    baskets = items.groupby("invoice_id")["key"].apply(lambda s: sorted(set(s))).to_dict()
    display = items.groupby("key")["name"].agg(lambda s: s.value_counts().index[0]).to_dict()
    typical = {k: {"qty": float(g["quantity"].median() or 1), "price": float(g["unit_price"].median() or 0)}
               for k, g in items.groupby("key")}
    return baskets, display, {"typical": typical}


def addon_suggestions(current_names: list[str], limit: int = 3) -> list[dict]:
    baskets, display, extra = _baskets()
    if not baskets:
        return []
    have = {str(n).strip().lower() for n in current_names if str(n).strip()}
    if not have:
        return []
    scores: dict[str, tuple[float, int]] = {}
    for anchor in have:
        with_anchor = [b for b in baskets.values() if anchor in b]
        if len(with_anchor) < 2:
            continue
        counts: dict[str, int] = {}
        for b in with_anchor:
            for other in b:
                if other not in have:
                    counts[other] = counts.get(other, 0) + 1
        for other, n in counts.items():
            conf = n / len(with_anchor)
            if n >= 2 and conf >= 0.3 and conf > scores.get(other, (0, 0))[0]:
                scores[other] = (conf, len(with_anchor))
    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    typical = extra.get("typical", {})
    return [{"key": k, "name": display.get(k, k), "share": round(v[0] * 100),
             "qty": max(1.0, round(typical.get(k, {}).get("qty", 1))),
             "price": typical.get(k, {}).get("price", 0.0)} for k, v in ranked]


# --------------------------------------------------------------------------- 3. celebrations
def save_celebration(invoice_id: int, invoice_number: str, customer_name: str, phone: str,
                     kind: str, other: str, honoree: str, cel_date) -> None:
    if not invoice_id or not kind or kind == "Not set":
        return
    d = cel_date.isoformat() if isinstance(cel_date, date) else (str(cel_date) if cel_date else "")
    ex("INSERT INTO hr_celebrations (invoice_id, invoice_number, customer_name, phone, kind, other, honoree, celebration_date, updated_at) "
       "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET invoice_number=excluded.invoice_number, "
       "customer_name=excluded.customer_name, phone=excluded.phone, kind=excluded.kind, other=excluded.other, "
       "honoree=excluded.honoree, celebration_date=excluded.celebration_date, updated_at=excluded.updated_at",
       (int(invoice_id), str(invoice_number or ""), str(customer_name or ""), str(phone or ""), kind,
        str(other or ""), str(honoree or ""), d, now_jm().isoformat()))


def load_celebration(invoice_id: int) -> dict | None:
    r = q("SELECT * FROM hr_celebrations WHERE invoice_id = ?", (int(invoice_id),))
    return None if r.empty else r.iloc[0].to_dict()


def _next_occurrence(d: date, today: date) -> date:
    for year in (today.year, today.year + 1):
        try:
            cand = d.replace(year=year)
        except ValueError:
            cand = date(year, 3, 1)
        if cand >= today:
            return cand
    return d


def greeting_text(kind: str, customer: str, honoree: str, holiday: str = "") -> str:
    who = first_name(customer)
    hon = str(honoree or "").strip()
    if kind == "Birthday":
        target = hon if hon and first_name(hon).lower() != first_name(customer).lower() else ""
        if target:
            return (f"Hi {who}, wishing {target} a very happy birthday from all of us at Headline Rentals! "
                    f"We loved being part of the celebration. Have a wonderful day.")
        return (f"Happy birthday, {who}! From all of us at Headline Rentals, we hope your day is as bright "
                f"as the celebrations we have shared with you. Enjoy every moment.")
    if kind in ("Anniversary", "Wedding"):
        return (f"Happy anniversary, {who}! Headline Rentals is honoured to have been part of your story. "
                f"Wishing you many more years of love and celebration.")
    if holiday:
        return (f"Hi {who}, everyone at Headline Rentals wishes you and your family a happy {holiday}! "
                f"Thank you for celebrating with us. We look forward to the next one.")
    return f"Hi {who}, warm wishes from Headline Rentals!"


def upcoming_greetings(holidays: pd.DataFrame, clients: pd.DataFrame, days_ahead: int = 3) -> list[dict]:
    """Birthdays and anniversaries in the next few days, plus holidays close at hand. Already sent ones are hidden."""
    today = now_jm().date()
    horizon = today + timedelta(days=days_ahead)
    sent = set(q("SELECT gkey FROM hr_greetings_sent")["gkey"].astype(str))
    out: list[dict] = []
    cel = q("SELECT * FROM hr_celebrations WHERE celebration_date IS NOT NULL AND celebration_date != ''")
    for _, r in cel.iterrows():
        if str(r["kind"]) not in RECURRING_KINDS:
            continue
        try:
            d0 = date.fromisoformat(str(r["celebration_date"])[:10])
        except Exception:
            continue
        nxt = _next_occurrence(d0, today)
        if not (today <= nxt <= horizon):
            continue
        kind = "Anniversary" if r["kind"] == "Wedding" else str(r["kind"])
        person = re.sub(r"[^a-z0-9]+", "-", str(r["honoree"] or r["customer_name"] or "").strip().lower()).strip("-")
        gkey = f"cel:{kind}:{person}:{nxt.isoformat()}"
        if gkey in sent or any(o["gkey"] == gkey for o in out):
            continue
        out.append({"gkey": gkey, "kind": kind, "when": nxt, "customer": r["customer_name"], "phone": r["phone"],
                    "title": f"{kind}: {r['honoree'] or r['customer_name']}",
                    "text": greeting_text(kind, r["customer_name"], r["honoree"])})
    if isinstance(holidays, pd.DataFrame) and not holidays.empty:
        hol = holidays.copy()
        hol["d"] = pd.to_datetime(hol["holiday_date"], errors="coerce").dt.date
        for _, h in hol.dropna(subset=["d"]).iterrows():
            if not (today <= h["d"] <= horizon):
                continue
            name = str(h["holiday_name"]).strip() or "holiday"
            gkey = f"hol:{h['d'].isoformat()}"
            if gkey in sent:
                continue
            recips = []
            if isinstance(clients, pd.DataFrame) and not clients.empty:
                for _, c in clients.head(25).iterrows():
                    if str(c.get("phone", "")).strip():
                        recips.append({"name": c["name"], "phone": c["phone"], "text": greeting_text("", c["name"], "", name)})
            out.append({"gkey": gkey, "kind": "Holiday", "when": h["d"], "title": name, "recipients": recips,
                        "text": greeting_text("", "{first name}", "", name)})
    return sorted(out, key=lambda g: g["when"])


def mark_greeting_sent(gkey: str) -> None:
    ex("INSERT OR REPLACE INTO hr_greetings_sent (gkey, sent_at) VALUES (?, ?)", (gkey, now_jm().isoformat()))


def render_greetings(holidays: pd.DataFrame, clients: pd.DataFrame) -> None:
    items = upcoming_greetings(holidays, clients)
    if not items:
        return
    st.markdown(f"<div class='hr-h'>Celebrations<span class='meta'>{len(items)} coming up</span></div>",
                unsafe_allow_html=True)
    today = now_jm().date()
    for g in items[:4]:
        days = (g["when"] - today).days
        when = "Today" if days == 0 else ("Tomorrow" if days == 1 else g["when"].strftime("%A"))
        icon = {"Birthday": "B", "Anniversary": "A", "Holiday": "H"}.get(g["kind"], "C")
        tone = {"Birthday": "ic-purple", "Anniversary": "ic-amber", "Holiday": "ic-cyan"}.get(g["kind"], "ic-purple")
        sub = (f"{when} · {len(g.get('recipients', []))} clients with a phone number" if g["kind"] == "Holiday"
               else f"{when} · {esc(g['customer'])}")
        st.markdown(
            f"<div class='hr-row'><div class='ic {tone}'>{icon}</div><div class='tx'><div class='t1'>{esc(g['title'])}</div>"
            f"<div class='t2'>{sub}</div></div></div>", unsafe_allow_html=True)
        with st.expander("Message ready to send", expanded=False):
            if g["kind"] == "Holiday":
                st.caption("Tap a name to open WhatsApp with the message filled in.")
                for i, r in enumerate(g.get("recipients", [])[:25]):
                    st.link_button(f"{r['name']}", whatsapp_link(r["phone"], r["text"]), use_container_width=True)
            else:
                st.text_area("Message", value=g["text"], key=f"hr_greet_txt_{g['gkey']}", height=110,
                             label_visibility="collapsed")
                if str(g.get("phone") or "").strip():
                    st.link_button("Send on WhatsApp", whatsapp_link(g["phone"], g["text"]),
                                   use_container_width=True, icon=":material/chat:")
            if st.button("Mark as sent", key=f"hr_greet_done_{g['gkey']}", use_container_width=True):
                mark_greeting_sent(g["gkey"])
                st.rerun()
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- 4. delivery day mode
def _parse_logi(raw) -> dict:
    try:
        d = json.loads(str(raw or "") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def driver_stops(day: date) -> list[dict]:
    rows = q(
        "SELECT i.id, i.invoice_number, COALESCE(i.customer_name,'') AS customer_name, "
        "COALESCE(i.customer_phone, i.contact_detail, '') AS phone, COALESCE(i.event_location,'') AS venue, "
        "i.event_date, COALESCE(i.event_time,'') AS event_time, COALESCE(i.logistics_json,'') AS logistics_json, "
        "COALESCE(i.amount_paid,0) AS amount_paid, COALESCE(i.payment_status,'') AS payment_status, "
        "COALESCE((SELECT SUM(quantity*unit_price) FROM invoice_items it WHERE it.invoice_id=i.id),0) AS total "
        "FROM invoices i WHERE lower(COALESCE(i.document_type,'invoice'))='invoice' "
        "AND lower(COALESCE(i.order_status,'confirmed'))='confirmed'"
    )
    stops: list[dict] = []
    for _, r in rows.iterrows():
        lg = _parse_logi(r["logistics_json"])
        pickup = str(lg.get("method", "")).lower() == "pickup"
        ev = str(r["event_date"] or "")[:10]
        out_d = str(lg.get("delivery_date") or ev)[:10]
        back_d = str(lg.get("collection_date") or "")[:10]
        if not back_d and ev:
            try:
                back_d = (date.fromisoformat(ev) + timedelta(days=1)).isoformat()
            except Exception:
                back_d = ""
        outstanding = 0.0 if str(r["payment_status"]).lower() == "paid_full" else max(float(r["total"]) - float(r["amount_paid"]), 0.0)
        base = {"invoice_id": int(r["id"]), "number": str(r["invoice_number"]), "customer": r["customer_name"],
                "phone": r["phone"], "venue": "Store pickup" if pickup else r["venue"],
                "access": str(lg.get("access_notes", "") or ""), "onsite": str(lg.get("onsite_contact", "") or ""),
                "collect": outstanding, "pickup": pickup}
        if out_d == day.isoformat():
            stops.append({**base, "stage": "pickup" if pickup else "delivery",
                          "time": str(lg.get("delivery_time") or r["event_time"] or "")[:5]})
        if back_d == day.isoformat():
            stops.append({**base, "stage": "return" if pickup else "collection",
                          "time": str(lg.get("collection_time") or "")[:5]})
    return sorted(stops, key=lambda s: (s["time"] or "99:99", s["number"]))


def _stop_items(invoice_id: int) -> list[tuple[str, float]]:
    its = q("SELECT item_name, quantity FROM invoice_items WHERE invoice_id = ?", (int(invoice_id),))
    if its.empty:
        return []
    its["item_name"] = its["item_name"].fillna("").astype(str).str.strip()
    its = its[(its["item_name"] != "") & ~its["item_name"].str.lower().str.contains(FEE_PATTERN, regex=True)]
    return [(r["item_name"], float(r["quantity"] or 0)) for _, r in its.iterrows() if float(r["quantity"] or 0) > 0]


def _check_state(invoice_id: int, stage: str) -> dict:
    r = q("SELECT item, done FROM hr_driver_checks WHERE invoice_id = ? AND stage = ?", (int(invoice_id), stage))
    return {str(a): bool(b) for a, b in zip(r["item"], r["done"])} if not r.empty else {}


def _set_check(invoice_id: int, stage: str, item: str, key: str) -> None:
    done = 1 if st.session_state.get(key) else 0
    ex("INSERT INTO hr_driver_checks (invoice_id, stage, item, done, at) VALUES (?,?,?,?,?) "
       "ON CONFLICT(invoice_id, stage, item) DO UPDATE SET done=excluded.done, at=excluded.at",
       (int(invoice_id), stage, item, done, now_jm().isoformat()))


@st.fragment
def render_stop(stop: dict, crew_pdf_cb) -> None:
    """One delivery or collection. A fragment, so ticking items only refreshes this card (fast on phones)."""
    stage = stop["stage"]
    label = {"delivery": "Delivery", "collection": "Collection", "pickup": "Store pickup", "return": "Return to store"}[stage]
    checks = {"delivery": ("Loaded", "Delivered"), "collection": ("Collected", "Back in store"),
              "pickup": ("Packed", "Handed over"), "return": ("Returned", "Checked")}[stage]
    done_row = q("SELECT * FROM hr_driver_stops WHERE invoice_id = ? AND stage = ?", (stop["invoice_id"], stage))
    complete = not done_row.empty and bool(done_row.iloc[0]["completed_at"])
    maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(str(stop["venue"] or ""))
    collect = (f"<div class='hr-drv-collect'>Collect {esc(jmd(stop['collect']))} before setting up</div>"
               if stop["collect"] > 0.01 and stage in ("delivery", "pickup") else "")
    status = "<span class='hr-drv-done'>Done</span>" if complete else ""
    access_html = f"<div class='ac'>Access: {esc(stop['access'])}</div>" if stop["access"] else ""
    onsite_html = f"<div class='ac'>On site: {esc(stop['onsite'])}</div>" if stop["onsite"] else ""
    st.markdown(
        f"<div class='hr-drv'><div class='top'><div class='tm'>{esc(stop['time'] or '--:--')}</div>"
        f"<div class='who'><div class='k'>{esc(label)} · #{esc(stop['number'])} {status}</div>"
        f"<div class='nm'>{esc(stop['customer'] or 'Customer')}</div>"
        f"<div class='vn'>{esc(stop['venue'] or 'No address on file')}</div></div></div>"
        f"{access_html}{onsite_html}{collect}</div>",
        unsafe_allow_html=True,
    )
    b1, b2, b3 = st.container(key=f"hr_btnrow_drv_{stage}_{stop['invoice_id']}").columns(3)
    if stop["venue"] and stage in ("delivery", "collection"):
        b1.link_button("Directions", maps, use_container_width=True, icon=":material/near_me:")
    if str(stop["phone"]).strip():
        digits = "".join(ch for ch in str(stop["phone"]) if ch.isdigit() or ch == "+")
        b2.link_button("Call", f"tel:{digits}", use_container_width=True, icon=":material/call:")
    try:
        pdf, stub = crew_pdf_cb(stop["invoice_id"])
        b3.download_button("Crew sheet", pdf, file_name=f"{stub}.pdf", mime="application/pdf",
                           use_container_width=True, key=f"hr_drv_pdf_{stage}_{stop['invoice_id']}")
    except Exception:
        pass
    items = _stop_items(stop["invoice_id"])
    state = {c: _check_state(stop["invoice_id"], f"{stage}:{c}") for c in checks}
    with st.expander(f"Item checklist · {len(items)} line{'s' if len(items) != 1 else ''}", expanded=not complete):
        if not items:
            st.caption("This order has no itemised lines (older import). Use the crew sheet.")
        for idx, (name, qty) in enumerate(items):
            c0, c1, c2 = st.columns([2.2, 1, 1])
            c0.markdown(f"**{esc(name)}** × {qty:g}")
            for col, chk in zip((c1, c2), checks):
                k = f"hr_chk_{stop['invoice_id']}_{stage}_{chk}_{idx}"
                if k not in st.session_state:
                    st.session_state[k] = state[chk].get(name, False)
                col.checkbox(chk, key=k, on_change=_set_check, args=(stop["invoice_id"], f"{stage}:{chk}", name, k))
    with st.expander("Finish this stop", expanded=False):
        received = st.text_input("Received by (name)", key=f"hr_drv_recv_{stage}_{stop['invoice_id']}")
        photo = st.file_uploader("Photo of the setup or the items", type=["jpg", "jpeg", "png", "webp"],
                                 key=f"hr_drv_photo_{stage}_{stop['invoice_id']}")
        if st.button("Mark stop complete", key=f"hr_drv_done_{stage}_{stop['invoice_id']}", type="primary",
                     use_container_width=True):
            blob = shrink_image(photo.getvalue()) if photo is not None else None
            ex("INSERT INTO hr_driver_stops (invoice_id, stage, received_by, completed_at, photo) VALUES (?,?,?,?,?) "
               "ON CONFLICT(invoice_id, stage) DO UPDATE SET received_by=excluded.received_by, "
               "completed_at=excluded.completed_at, photo=COALESCE(excluded.photo, hr_driver_stops.photo)",
               (stop["invoice_id"], stage, received.strip(), now_jm().isoformat(), blob))
            st.toast(f"#{stop['number']} {label.lower()} marked complete.")
            st.rerun(scope="fragment")
    with st.expander("Report damage or a missing item", expanded=False):
        render_damage_form(f"drv_{stage}_{stop['invoice_id']}", stop["invoice_id"], stop["number"],
                           stop["customer"], [n for n, _ in items])
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)


def render_driver_mode(crew_pdf_cb) -> None:
    today = now_jm().date()
    pick = st.segmented_control("Day", ["Today", "Tomorrow", "Pick a date"], default="Today",
                                key="hr_drv_day", label_visibility="collapsed")
    if pick == "Tomorrow":
        day = today + timedelta(days=1)
    elif pick == "Pick a date":
        day = st.date_input("Date", value=today, key="hr_drv_date")
    else:
        day = today
    stops = driver_stops(day)
    st.markdown(f"<div class='hr-h'>{esc(day.strftime('%A %d %B'))}<span class='meta'>{len(stops)} stop"
                f"{'s' if len(stops) != 1 else ''}</span></div>", unsafe_allow_html=True)
    if not stops:
        st.markdown("<div class='hr-empty'>No deliveries or collections on this day.</div>", unsafe_allow_html=True)
        return
    for s in stops:
        render_stop(s, crew_pdf_cb)


# --------------------------------------------------------------------------- 5. damage and loss
def render_damage_form(key: str, invoice_id: int | None, invoice_number: str, customer: str, item_names: list[str]) -> None:
    with st.form(f"hr_dmg_form_{key}", clear_on_submit=True):
        c1, c2 = st.columns([2, 1])
        if item_names:
            item = c1.selectbox("Item", options=item_names)
        else:
            item = c1.text_input("Item")
        qty = c2.number_input("How many", min_value=1.0, value=1.0, step=1.0)
        c3, c4 = st.columns(2)
        kind = c3.selectbox("What happened", DAMAGE_KINDS)
        charge = c4.number_input("Charge to client (JMD)", min_value=0.0, value=0.0, step=500.0)
        note = st.text_input("Note")
        photo = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"])
        ok = st.form_submit_button("Save report", type="primary", use_container_width=True)
    if ok and str(item or "").strip():
        blob = shrink_image(photo.getvalue()) if photo is not None else None
        ex("INSERT INTO hr_damage (invoice_id, invoice_number, customer_name, item_name, qty, kind, charge, note, status, created_at, photo) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           (invoice_id, invoice_number or "", customer or "", str(item).strip(), float(qty), kind, float(charge),
            note.strip(), "Open", now_jm().isoformat(), blob))
        st.success(f"Saved: {qty:g} × {item} ({kind.lower()}).")


def render_damage_panel(inventory_names: list[str]) -> None:
    log = q("SELECT id, invoice_number, customer_name, item_name, qty, kind, charge, note, status, created_at, "
            "photo IS NOT NULL AS has_photo FROM hr_damage ORDER BY created_at DESC")
    open_log = log[log["status"].isin(["Open", "In repair"])] if not log.empty else log
    month = now_jm().strftime("%Y-%m")
    month_charges = float(log.loc[log["created_at"].astype(str).str.startswith(month), "charge"].sum()) if not log.empty else 0.0
    out_of_service = float(open_log["qty"].sum()) if not open_log.empty else 0.0
    st.markdown(
        f"<div class='hr-stat-grid'>"
        f"<div class='hr-tile'><div class='lbl'>Open reports</div><div class='val'>{len(open_log)}</div><div class='sub tone-muted'>damaged, lost or in repair</div></div>"
        f"<div class='hr-tile'><div class='lbl'>Out of service</div><div class='val'>{out_of_service:g}</div><div class='sub tone-amber'>pieces not rentable</div></div>"
        f"<div class='hr-tile'><div class='lbl'>Charged this month</div><div class='val'>{esc(jmd(month_charges))}</div><div class='sub tone-muted'>to clients</div></div>"
        f"</div>", unsafe_allow_html=True)
    with st.expander("Report damage or loss", expanded=False):
        render_damage_form("stock", None, "", "", inventory_names)
    for _, r in open_log.iterrows():
        st.markdown(
            f"<div class='hr-row'><div class='ic {'ic-red' if r['kind'] == 'Lost' else 'ic-amber'}'>{esc(str(r['kind'])[:1])}</div>"
            f"<div class='tx'><div class='t1'>{float(r['qty']):g} × {esc(r['item_name'])} · {esc(r['kind'])}</div>"
            f"<div class='t2'>{('#' + esc(r['invoice_number']) + ' · ') if r['invoice_number'] else ''}{esc(r['customer_name'])}"
            f"{(' · charge ' + esc(jmd(r['charge']))) if float(r['charge'] or 0) > 0 else ''}"
            f"{(' · ' + esc(r['note'])) if r['note'] else ''}</div></div></div>", unsafe_allow_html=True)
        c1, c2 = st.container(key=f"hr_btnrow_dmg_{r['id']}").columns([2, 1])
        new_status = c1.selectbox("Status", DAMAGE_STATUS, index=DAMAGE_STATUS.index(r["status"]) if r["status"] in DAMAGE_STATUS else 0,
                                  key=f"hr_dmg_status_{r['id']}", label_visibility="collapsed")
        if c2.button("Update", key=f"hr_dmg_upd_{r['id']}", use_container_width=True):
            resolved = now_jm().isoformat() if new_status not in ("Open", "In repair") else None
            ex("UPDATE hr_damage SET status = ?, resolved_at = ? WHERE id = ?", (new_status, resolved, int(r["id"])))
            st.rerun()
    if not log.empty:
        with st.expander("Full damage and loss history", expanded=False):
            show = log.drop(columns=["has_photo"]).copy()
            st.dataframe(show, hide_index=True, use_container_width=True)


def out_of_service_by_item() -> dict:
    r = q("SELECT lower(item_name) AS k, SUM(qty) AS n FROM hr_damage WHERE status IN ('Open','In repair') GROUP BY 1")
    return dict(zip(r["k"], r["n"])) if not r.empty else {}


# --------------------------------------------------------------------------- 6. QR labels
def qr_label_sheet_pdf(inventory: pd.DataFrame, base_url: str) -> bytes:
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    try:
        import document_pdf as D
        f_reg, f_bold = D.F("Pop"), D.F("Pop-Bold")
    except Exception:
        f_reg, f_bold = "Helvetica", "Helvetica-Bold"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    W, H = LETTER
    cols, rows = 3, 5
    mx, my = 0.4 * inch, 0.5 * inch
    cw, ch = (W - 2 * mx) / cols, (H - 2 * my) / rows
    base = (base_url or "https://headlinerentals.streamlit.app").rstrip("/")
    items = inventory.reset_index(drop=True)
    for i, (_, it) in enumerate(items.iterrows()):
        if i and i % (cols * rows) == 0:
            c.showPage()
        slot = i % (cols * rows)
        x = mx + (slot % cols) * cw
        y = H - my - (slot // cols + 1) * ch
        c.setStrokeColorRGB(0.88, 0.87, 0.94)
        c.setDash(2, 3)
        c.roundRect(x + 4, y + 4, cw - 8, ch - 8, 8, stroke=1, fill=0)
        c.setDash()
        url = f"{base}/?scan={int(it['id'])}"
        w = QrCodeWidget(url)
        b = w.getBounds()
        size = min(cw, ch) * 0.58
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(w)
        renderPDF.draw(d, c, x + (cw - size) / 2, y + ch - size - 14)
        c.setFillColorRGB(0.055, 0.055, 0.1)
        c.setFont(f_bold, 10)
        c.drawCentredString(x + cw / 2, y + 30, str(it["item_name"])[:34])
        c.setFillColorRGB(0.35, 0.15, 0.9)
        c.setFont(f_reg, 7.5)
        sku = str(it.get("sku") or "").strip()
        c.drawCentredString(x + cw / 2, y + 18, f"HEADLINE RENTALS{(' · ' + sku) if sku and sku != 'None' else ''} · ID {int(it['id'])}")
    c.save()
    return buf.getvalue()


def render_scan_card(item_id: int, live_status: pd.DataFrame, inventory: pd.DataFrame) -> None:
    it = inventory[inventory["id"].astype(int) == int(item_id)]
    if it.empty:
        st.warning("That label is for an item that is no longer in your stock list.")
        return
    it = it.iloc[0]
    name = str(it["item_name"])
    live = live_status[live_status["item_name"].astype(str).str.lower() == name.lower()] if not live_status.empty else live_status
    free = float(live.iloc[0]["usable_now"]) if not live.empty else float(it.get("current_quantity", 0) or 0)
    owned = float(it.get("current_quantity", 0) or 0)
    oos = out_of_service_by_item().get(name.lower(), 0.0)
    st.markdown(
        f"<div class='hr-client'><div class='top'><div class='av'>QR</div><div class='who'>"
        f"<div class='nm'>{esc(name)}</div><div class='ln'>Scanned label · ID {int(it['id'])}</div></div></div>"
        f"<div class='stats'><div><b>{free:g}</b><span>free right now</span></div>"
        f"<div><b>{owned:g}</b><span>owned</span></div>"
        f"<div><b>{oos:g}</b><span>out of service</span></div></div>"
        f"<div class='fav'><span class='lbl'>Rents for</span><span class='chip'>{esc(jmd(it.get('default_rental_price', 0)))}</span></div></div>",
        unsafe_allow_html=True)
    with st.expander("Report damage or loss for this item", expanded=False):
        render_damage_form(f"scan_{item_id}", None, "", "", [name])


# --------------------------------------------------------------------------- 7. monthly board pack
def last_month_token() -> str:
    first = now_jm().date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def render_board_pack_card(business_name: str, logo_path) -> None:
    token = last_month_token()
    label = datetime.strptime(token, "%Y-%m").strftime("%B %Y")
    st.markdown(
        f"<div class='hr-row'><div class='ic ic-ink'>B</div><div class='tx'><div class='t1'>{esc(label)} board pack is ready</div>"
        f"<div class='t2'>Last month's numbers, summary and actions as a PDF</div></div></div>", unsafe_allow_html=True)
    try:
        from boardroom import _board_pack_cached
        pdf = _board_pack_cached(f"Month:{token}", None, now_jm().date().isoformat(), "JM$", business_name,
                                 str(logo_path) if logo_path else "")
        st.download_button(f"Download {label} board pack", pdf, file_name=f"Board_Pack_{token}.pdf",
                           mime="application/pdf", use_container_width=True, key="hr_boardpack_dl", type="primary")
    except Exception as exc:
        st.caption(f"Board pack not available yet: {exc}")


# --------------------------------------------------------------------------- 8. Ask Headline
MONTHS = {m.lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                               "August", "September", "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def _find_date(text: str) -> date | None:
    t = text.lower()
    today = now_jm().date()
    if "today" in t:
        return today
    if "tomorrow" in t:
        return today + timedelta(days=1)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})", t) or re.search(r"([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?", t)
    if m:
        a, b = m.group(1), m.group(2)
        day_s, mon_s = (a, b) if a.isdigit() else (b, a)
        mon = MONTHS.get(mon_s) or MONTHS.get(mon_s[:3])
        if mon:
            try:
                d = date(today.year, mon, int(day_s))
                return d if d >= today - timedelta(days=1) else date(today.year + 1, mon, int(day_s))
            except ValueError:
                return None
    return None


def ask_headline(question: str, *, invoice_level: pd.DataFrame, live_status_at, fallback) -> str:
    text = (question or "").strip()
    t = text.lower()
    if not text:
        return "Ask me about payments, stock on a date, upcoming events or quotes."
    # Who owes / has X paid
    if any(k in t for k in ("paid", "owe", "balance", "outstanding")):
        inv = invoice_level.copy() if isinstance(invoice_level, pd.DataFrame) else pd.DataFrame()
        if inv.empty:
            return "There are no confirmed orders to check yet."
        name_hits = []
        stop_words = {"who", "owes", "owe", "money", "has", "paid", "the", "and", "balance", "outstanding",
                      "one", "does", "still", "what", "how", "much", "did", "pay"}
        words = set(re.findall(r"[a-z']+", t)) - stop_words
        for raw in sorted(set(inv["customer_name"].fillna("").astype(str)), key=len, reverse=True):
            parts = [p for p in re.findall(r"[a-z']+", raw.lower()) if len(p) >= 3 and p not in stop_words]
            if raw.strip() and any(p in words for p in parts[:2]):
                name_hits.append(raw)
        if name_hits:
            rows = inv[inv["customer_name"].isin(name_hits[:3])].sort_values("event_date")
            lines = []
            for _, r in rows.tail(4).iterrows():
                owed = float(r["amount_outstanding"] or 0)
                state = "paid in full" if owed <= 0.01 else f"owes {jmd(owed)}"
                lines.append(f"#{r['invoice_number']} ({str(r['event_date'])[:10]}): {r['customer_name']} {state}.")
            return "\n".join(lines)
        owing = inv[inv["amount_outstanding"] > 0.01].sort_values("amount_outstanding", ascending=False)
        if owing.empty:
            return "Everyone is paid up. No balances outstanding."
        lines = [f"{len(owing)} order(s) owe {jmd(owing['amount_outstanding'].sum())} in total:"]
        for _, r in owing.head(5).iterrows():
            lines.append(f"#{r['invoice_number']} {r['customer_name'] or 'No name'}: {jmd(r['amount_outstanding'])}")
        return "\n".join(lines)
    # What is free on a date
    if any(k in t for k in ("free", "available", "availability", "in stock")):
        d = _find_date(t) or now_jm().date()
        status = live_status_at(d)
        if status is None or status.empty:
            return "Your stock list is empty, so I cannot check availability yet."
        lines = [f"Free on {d.strftime('%a %d %b %Y')} at 11am:"]
        for _, r in status.sort_values("item_name").iterrows():
            lines.append(f"{r['item_name']}: {float(r['usable_now']):g} of {float(r['current_quantity']):g}")
        return "\n".join(lines[:16])
    # Next events
    if any(k in t for k in ("next event", "upcoming", "this week", "schedule", "deliveries")):
        stops = []
        for i in range(0, 8):
            d = now_jm().date() + timedelta(days=i)
            for s in driver_stops(d):
                stops.append(f"{d.strftime('%a %d %b')} {s['time'] or ''} {s['stage']}: #{s['number']} {s['customer']}".strip())
        return "\n".join(["Next 7 days:"] + stops[:10]) if stops else "Nothing booked for delivery or collection in the next 7 days."
    if "accepted" in t or "quote" in t and "waiting" in t:
        acc = accepted_quotes(True)
        if acc.empty:
            return "No client acceptances waiting for you."
        return "\n".join(f"#{r['invoice_number']} accepted by {r['accepted_name']}" for _, r in acc.iterrows())
    return fallback(text)


def render_ask_headline(answer_fn) -> None:
    hist = st.session_state.setdefault("hr_ask_history", [])
    with st.container(key="hr_ask"):
        st.markdown("<div class='hr-ask-h'><span class='spark'></span><b>Ask Headline</b>"
                    "<span class='s'>Payments, stock on a date, the week ahead</span></div>", unsafe_allow_html=True)
        with st.form("hr_ask_form", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1])
            qtext = c1.text_input("Ask", placeholder="Has Mel paid? What is free on 12 April?", label_visibility="collapsed")
            go = c2.form_submit_button("Ask", type="primary", use_container_width=True)
        chips = ["Who owes money?", "What is free tomorrow?", "Deliveries this week"]
        cc = st.columns(len(chips))
        picked = None
        for chip, col in zip(chips, cc):
            if col.button(chip, key=f"hr_ask_chip_{chip}", use_container_width=True):
                picked = chip
        ask = picked or (qtext if go else "")
        if ask:
            try:
                reply = answer_fn(ask)
            except Exception as exc:
                reply = f"I could not answer that one ({exc})."
            hist.append(("you", ask))
            hist.append(("hl", reply))
            st.session_state["hr_ask_history"] = hist[-8:]
        for who, msg in st.session_state["hr_ask_history"][-4:]:
            cls = "me" if who == "you" else "hl"
            st.markdown(f"<div class='hr-bubble {cls}'>{esc(msg).replace(chr(10), '<br>')}</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- Pipeline (Orders)
def _ring(pct: float, size: int = 46) -> str:
    pct = max(0.0, min(100.0, float(pct or 0)))
    r = 18
    circ = 2 * 3.14159 * r
    col = "#13794A" if pct >= 99.5 else ("#5927E5" if pct > 0 else "#C9C7D9")
    return (f"<svg viewBox='0 0 46 46' width='{size}' height='{size}' aria-label='{pct:.0f}% paid'>"
            f"<circle cx='23' cy='23' r='{r}' fill='none' stroke='#EFEDF6' stroke-width='5'/>"
            f"<circle cx='23' cy='23' r='{r}' fill='none' stroke='{col}' stroke-width='5' stroke-linecap='round' "
            f"stroke-dasharray='{circ * pct / 100:.1f} {circ:.1f}' transform='rotate(-90 23 23)'/>"
            f"<text x='23' y='27' text-anchor='middle' font-size='11' font-weight='700' fill='#0E0E1A'>{pct:.0f}%</text></svg>")


@st.cache_data(ttl=30, show_spinner=False)
def pipeline_frame() -> pd.DataFrame:
    df = q(
        "SELECT i.id, i.invoice_number, COALESCE(i.customer_name,'') AS customer_name, "
        "COALESCE(i.customer_phone, i.contact_detail, '') AS phone, i.event_date, COALESCE(i.event_location,'') AS venue, "
        "lower(COALESCE(i.document_type,'invoice')) AS doc, lower(COALESCE(i.order_status,'confirmed')) AS status, "
        "COALESCE(i.amount_paid,0) AS paid, lower(COALESCE(i.payment_status,'')) AS pay_status, "
        "COALESCE((SELECT SUM(quantity*unit_price) FROM invoice_items it WHERE it.invoice_id=i.id),0) AS total, "
        "(SELECT status FROM hr_quote_links l WHERE l.invoice_id=i.id ORDER BY created_at DESC LIMIT 1) AS link_status, "
        "(SELECT created_at FROM hr_quote_links l WHERE l.invoice_id=i.id ORDER BY created_at DESC LIMIT 1) AS link_at "
        "FROM invoices i"
    )
    if df.empty:
        return df
    today = now_jm().date()
    df["ev"] = pd.to_datetime(df["event_date"], errors="coerce").dt.date
    df["future"] = df["ev"].apply(lambda d: d is None or pd.isna(d) or d >= today)
    df["owed"] = df.apply(lambda r: 0.0 if r["pay_status"] == "paid_full" else max(float(r["total"]) - float(r["paid"]), 0.0), axis=1)
    df["pct"] = df.apply(lambda r: 100.0 if r["owed"] <= 0.01 else (float(r["paid"]) / float(r["total"]) * 100 if r["total"] else 0.0), axis=1)

    def stage(r) -> str:
        if r["doc"] == "quote":
            if not r["future"]:
                return "expired"
            return "accepted" if r["link_status"] == "accepted" else "quote"
        if r["status"] != "confirmed":
            return "other"
        if not r["future"]:
            return "owed_past" if r["owed"] > 0.01 else "done"
        return "deposit" if r["owed"] > 0.01 else "ready"
    df["stage"] = df.apply(stage, axis=1)
    return df


PIPE_STAGES = [
    ("quote", "Quotes out"), ("accepted", "Accepted"), ("deposit", "Money due"),
    ("ready", "Paid, coming up"), ("owed_past", "Owed after event"),
]


def nudge_text(r) -> str:
    who = first_name(r["customer_name"])
    when = r["ev"].strftime("%A %d %B") if isinstance(r["ev"], date) else "your event"
    if r["stage"] in ("quote",):
        return (f"Hi {who}, just checking you received your Headline Rentals quote #{r['invoice_number']} for {when}. "
                f"Happy to adjust anything. Dates fill up quickly, so let us know when you are ready to secure it.")
    return (f"Hi {who}, a friendly reminder from Headline Rentals: {jmd(r['owed'])} is due on order #{r['invoice_number']} "
            f"for {when}. Thank you!")


def render_pipeline(open_quote_cb, crew_pdf_cb) -> None:
    df = pipeline_frame()
    if df.empty:
        return
    counts = {k: int((df["stage"] == k).sum()) for k, _ in PIPE_STAGES}
    values = {k: float(df.loc[df["stage"] == k, "owed" if k in ("deposit", "owed_past") else "total"].sum()) for k, _ in PIPE_STAGES}
    pills = "".join(
        f"<div class='hr-pipe-pill'><span class='n'>{counts[k]}</span><span class='l'>{esc(lbl)}</span>"
        f"<span class='v'>{esc(jmd(values[k]).replace('.00', ''))}</span></div>"
        for k, lbl in PIPE_STAGES)
    st.markdown(f"<div class='hr-pipe'>{pills}</div>", unsafe_allow_html=True)
    default = next((k for k, _ in PIPE_STAGES if counts[k]), "quote")
    pick = st.segmented_control("Stage", [k for k, _ in PIPE_STAGES], format_func=lambda k: dict(PIPE_STAGES)[k],
                                default=default, key="hr_pipe_stage", label_visibility="collapsed") or default
    rows = df[df["stage"] == pick].sort_values("ev", na_position="last")
    if rows.empty:
        st.markdown("<div class='hr-empty'>Nothing in this stage right now.</div>", unsafe_allow_html=True)
        return
    today = now_jm().date()
    for _, r in rows.head(15).iterrows():
        days = (r["ev"] - today).days if isinstance(r["ev"], date) else None
        when = r["ev"].strftime("%a %d %b") if isinstance(r["ev"], date) else "No date"
        soon = f" · in {days} day{'s' if days != 1 else ''}" if days is not None and 0 <= days <= 14 else ""
        waiting = ""
        if pick == "quote" and r["link_at"]:
            try:
                age = (now_jm() - datetime.fromisoformat(str(r["link_at"]))).days
                waiting = f" · link sent {age} day{'s' if age != 1 else ''} ago" if age >= 1 else " · link sent today"
            except Exception:
                waiting = ""
        money_txt = f"{jmd(r['owed'])} still owed" if r["owed"] > 0.01 and r["doc"] == "invoice" else jmd(r["total"])
        st.markdown(
            f"<div class='hr-pcard'>{_ring(r['pct'])}<div class='tx'><div class='t1'>{esc(r['customer_name'] or 'No name')} "
            f"<span class='num'>#{esc(r['invoice_number'])}</span></div>"
            f"<div class='t2'>{esc(when)}{soon}{waiting}{(' · ' + esc(r['venue'])) if r['venue'] else ''}</div></div>"
            f"<div class='amt'>{esc(money_txt)}</div></div>", unsafe_allow_html=True)
        c1, c2 = st.container(key=f"hr_btnrow_pipe_{r['id']}").columns(2)
        if str(r["phone"]).strip() and pick in ("quote", "deposit", "owed_past"):
            c1.link_button("Send a nudge" if pick == "quote" else "Payment reminder",
                           whatsapp_link(r["phone"], nudge_text(r)), use_container_width=True, icon=":material/chat:")
        if r["doc"] == "quote":
            if c2.button("Open quote", key=f"hr_pipe_open_{r['id']}", use_container_width=True):
                open_quote_cb(int(r["id"]))
        else:
            try:
                pdf, stub = crew_pdf_cb(int(r["id"]))
                c2.download_button("Crew sheet", pdf, file_name=f"{stub}.pdf", mime="application/pdf",
                                   key=f"hr_pipe_crew_{r['id']}", use_container_width=True)
            except Exception:
                pass
    if len(rows) > 15:
        st.caption(f"Showing the next 15 of {len(rows)}. The full tracker is below.")


def quotes_waiting(min_days: int = 2) -> pd.DataFrame:
    df = pipeline_frame()
    if df.empty:
        return df
    w = df[(df["stage"] == "quote") & df["link_at"].notna()].copy()
    if w.empty:
        return w

    def age(x):
        try:
            return (now_jm() - datetime.fromisoformat(str(x))).days
        except Exception:
            return 0
    w["age"] = w["link_at"].apply(age)
    return w[w["age"] >= min_days]


# --------------------------------------------------------------------------- Command centre (Today)
def morning_brief(stops: list, owed_today: float, waiting_n: int, accepted_n: int, greet_n: int) -> str:
    bits = []
    if stops:
        d = sum(1 for s in stops if s["stage"] in ("delivery", "pickup"))
        c = len(stops) - d
        part = []
        if d:
            part.append(f"{d} drop off{'s' if d != 1 else ''}")
        if c:
            part.append(f"{c} collection{'s' if c != 1 else ''}")
        bits.append(" and ".join(part) + " today")
    if owed_today > 0.01:
        bits.append(f"{jmd(owed_today).replace('.00', '')} to collect on the road")
    if accepted_n:
        bits.append(f"{accepted_n} quote{'s' if accepted_n != 1 else ''} accepted by clients")
    if waiting_n:
        bits.append(f"{waiting_n} quote{'s' if waiting_n != 1 else ''} waiting on a reply")
    if greet_n:
        bits.append(f"{greet_n} celebration{'s' if greet_n != 1 else ''} to send wishes for")
    if not bits:
        return "A clear day. A good moment to follow up quotes or plan next month's stock."
    sentence = bits[0] if len(bits) == 1 else ", ".join(bits[:-1]) + " and " + bits[-1]
    return sentence[0].upper() + sentence[1:] + "."


def target_ring_html(actual: float, target: float, label: str, sub: str) -> str:
    pct = 0.0 if target <= 0 else min(100.0, actual / target * 100)
    r = 52
    circ = 2 * 3.14159 * r
    return (
        f"<div class='hr-target'><svg viewBox='0 0 130 130' width='112' height='112' aria-label='{pct:.0f}% of target'>"
        f"<circle cx='65' cy='65' r='{r}' fill='none' stroke='rgba(255,255,255,.12)' stroke-width='11'/>"
        f"<circle cx='65' cy='65' r='{r}' fill='none' stroke='url(#hrg)' stroke-width='11' stroke-linecap='round' "
        f"stroke-dasharray='{circ * pct / 100:.1f} {circ:.1f}' transform='rotate(-90 65 65)'/>"
        f"<defs><linearGradient id='hrg' x1='0' x2='1'><stop offset='0' stop-color='#8466FA'/><stop offset='1' stop-color='#3FD0F5'/></linearGradient></defs>"
        f"<text x='65' y='70' text-anchor='middle' font-size='26' font-weight='700' fill='#fff'>{pct:.0f}%</text></svg>"
        f"<div class='tx'><div class='k'>{esc(label)}</div><div class='v'>{esc(jmd(actual).replace('.00',''))}</div>"
        f"<div class='s'>{esc(sub)}</div></div></div>")
