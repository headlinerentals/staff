from __future__ import annotations

import base64
import json
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Iterable


def normalize_phone_digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def normalize_whatsapp_to(value: str, default_country_code: str = "1") -> str:
    digits = normalize_phone_digits(value)
    if not digits:
        return ""
    if not digits.startswith(default_country_code) and len(digits) <= 10:
        return f"{default_country_code}{digits}"
    return digits


def normalize_sms_to(value: str, default_country_code: str = "+1") -> str:
    digits = normalize_phone_digits(value)
    if not digits:
        return ""
    if value.strip().startswith("+"):
        return f"+{digits}"
    if len(digits) <= 10:
        return f"{default_country_code}{digits}"
    return f"+{digits}"


def build_invoice_message(
    *,
    business_name: str,
    invoice_number: str,
    document_label: str = "Invoice",
    event_date: str,
    event_time: str,
    total_display: str,
    review_link: str = "",
    extra_note: str = "",
) -> str:
    lines = [
        f"Hi, this is {business_name}.",
        f"{(document_label or 'Invoice').strip()} #{invoice_number}",
        f"Event Date/Time: {event_date} {event_time}",
        f"Total: {total_display}",
    ]
    if extra_note.strip():
        lines.append(extra_note.strip())
    if review_link.strip():
        lines.append(f"Google Review: {review_link.strip()}")
    lines.append("Thank you for choosing us.")
    return "\n".join(lines)


def whatsapp_link(phone: str, text: str) -> str:
    to_phone = normalize_whatsapp_to(phone)
    return f"https://wa.me/{to_phone}?text={urllib.parse.quote_plus(text or '')}"


def gmail_compose_link(to_email: str, subject: str, body: str) -> str:
    params = urllib.parse.urlencode(
        {
            "view": "cm",
            "fs": 1,
            "to": to_email or "",
            "su": subject or "",
            "body": body or "",
        }
    )
    return f"https://mail.google.com/mail/?{params}"


def sms_link(phone: str, text: str) -> str:
    to_phone = normalize_sms_to(phone)
    return f"sms:{to_phone}?&body={urllib.parse.quote_plus(text or '')}"


def instagram_dm_link(username: str) -> str:
    user = (username or "").strip().lstrip("@")
    if not user:
        return "https://instagram.com"
    return f"https://ig.me/m/{urllib.parse.quote(user)}"


def send_email_smtp(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    attachments: Iterable[tuple[str, bytes, str]] = (),
) -> None:
    if not smtp_host.strip():
        raise ValueError("SMTP host is required.")
    if not to_email.strip():
        raise ValueError("Recipient email is required.")

    msg = EmailMessage()
    msg["Subject"] = subject or "Invoice"
    msg["From"] = from_email.strip() or smtp_user.strip()
    msg["To"] = to_email.strip()
    msg.set_content(body or "")

    for filename, data, mime_type in attachments:
        maintype, subtype = "application", "octet-stream"
        if "/" in (mime_type or ""):
            maintype, subtype = mime_type.split("/", 1)
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    with smtplib.SMTP(smtp_host.strip(), int(smtp_port)) as server:
        server.starttls()
        if smtp_user.strip():
            server.login(smtp_user.strip(), smtp_password or "")
        server.send_message(msg)


def _post_form(
    *,
    url: str,
    form_fields: dict[str, str],
    basic_auth_user: str,
    basic_auth_password: str,
) -> dict:
    encoded = urllib.parse.urlencode(form_fields).encode("utf-8")
    req = urllib.request.Request(url=url, data=encoded, method="POST")
    token_raw = f"{basic_auth_user}:{basic_auth_password}".encode("utf-8")
    token = base64.b64encode(token_raw).decode("ascii")
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def send_twilio_message(
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    body: str,
) -> dict:
    sid = (account_sid or "").strip()
    token = (auth_token or "").strip()
    if not sid or not token:
        raise ValueError("Twilio SID and Auth Token are required.")
    if not from_number.strip() or not to_number.strip():
        raise ValueError("Both sender and recipient numbers are required.")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    return _post_form(
        url=url,
        form_fields={
            "From": from_number.strip(),
            "To": to_number.strip(),
            "Body": body or "",
        },
        basic_auth_user=sid,
        basic_auth_password=token,
    )


def _post_json(
    *,
    url: str,
    payload: dict,
    bearer_token: str,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {bearer_token.strip()}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def send_whatsapp_cloud_text(
    *,
    access_token: str,
    phone_number_id: str,
    to_phone_digits: str,
    message_text: str,
    api_version: str = "v23.0",
) -> dict:
    if not access_token.strip() or not phone_number_id.strip():
        raise ValueError("WhatsApp Cloud token and phone number ID are required.")
    if not to_phone_digits.strip():
        raise ValueError("Recipient WhatsApp number is required.")

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone_digits,
        "type": "text",
        "text": {"body": message_text or ""},
    }
    return _post_json(url=url, payload=payload, bearer_token=access_token)


def send_instagram_dm_meta(
    *,
    access_token: str,
    ig_business_account_id: str,
    recipient_ig_user_id: str,
    message_text: str,
    api_version: str = "v23.0",
) -> dict:
    if not access_token.strip() or not ig_business_account_id.strip():
        raise ValueError("Instagram access token and business account ID are required.")
    if not recipient_ig_user_id.strip():
        raise ValueError("Instagram recipient user ID is required.")

    url = f"https://graph.facebook.com/{api_version}/{ig_business_account_id}/messages"
    payload = {
        "messaging_product": "instagram",
        "recipient": {"id": recipient_ig_user_id.strip()},
        "message": {"text": message_text or ""},
    }
    return _post_json(url=url, payload=payload, bearer_token=access_token)
