"""
Softenix Solution — AI Inbox Analyzer.

Connects to Gmail over IMAP, reads UNSEEN messages with BODY.PEEK so they
stay unread until processing succeeds, classifies each reply, and updates a
matching outreach_leads row in Supabase.

Usage:
    python inbox_analyzer.py
    python inbox_analyzer.py --dry-run
"""

from __future__ import annotations

import argparse
import imaplib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage as ParsedMessage
from email.parser import BytesParser
from email.utils import parseaddr

from bs4 import BeautifulSoup
from openai import OpenAI
from supabase import Client

from outreach import (
    TABLE,
    Settings,
    load_settings,
    make_openai_client,
    supabase_client,
    utc_now,
)

CATEGORIES = (
    "Interested",
    "Not Interested",
    "Meeting Requested",
    "Out of Office",
    "Spam",
)

BODY_LIMIT = 6000
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_MAILBOX = "INBOX"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("inbox_analyzer")


@dataclass
class InboxMessage:
    uid: str
    sender_email: str
    sender_name: str
    subject: str
    body: str


def decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def extract_body(message: ParsedMessage) -> str:
    if message.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            content_type = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    payload = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if not isinstance(payload, str):
                continue
            if content_type == "text/plain":
                plain_parts.append(payload)
            elif content_type == "text/html":
                html_parts.append(html_to_text(payload))
        text = "\n\n".join(plain_parts) or "\n\n".join(html_parts)
    else:
        content_type = message.get_content_type()
        try:
            payload = message.get_content()
        except Exception:
            payload = ""
        if not isinstance(payload, str):
            payload = ""
        text = html_to_text(payload) if content_type == "text/html" else payload

    text = re.sub(r"\r\n", "\n", text).strip()
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT] + "\n\n[truncated]"
    return text


def own_addresses(settings: Settings) -> set[str]:
    addresses = {
        settings.sender_email.lower(),
        settings.reply_to_email.lower(),
        settings.unsubscribe_email.lower(),
    }
    return {item for item in addresses if item}


def parse_unread(uid: str, raw: bytes, settings: Settings) -> InboxMessage | None:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    from_name, from_addr = parseaddr(message.get("From", ""))
    from_addr = from_addr.strip().lower()
    if not from_addr:
        log.warning("UID %s has no From address. Leaving unread.", uid)
        return None
    if from_addr in own_addresses(settings):
        log.info("UID %s is from our own mailbox (%s). Skipping.", uid, from_addr)
        return InboxMessage(
            uid=uid,
            sender_email=from_addr,
            sender_name=from_name.strip(),
            subject="__OWN_MAILBOX__",
            body="",
        )

    subject = decode_header_value(message.get("Subject"))
    body = extract_body(message)
    if not body:
        body = "(empty body)"
    return InboxMessage(
        uid=uid,
        sender_email=from_addr,
        sender_name=from_name.strip(),
        subject=subject,
        body=body,
    )


def classify_reply(client: OpenAI, model: str, item: InboxMessage) -> str:
    system = (
        "Analyze this email reply. Categorize it strictly as one of these: "
        "'Interested', 'Not Interested', 'Meeting Requested', 'Out of Office', or 'Spam'.\n"
        "Return JSON with a single key category. No other text.\n"
        "Guidelines:\n"
        "- Interested: wants to learn more, asks a relevant question, or is open to a conversation.\n"
        "- Not Interested: decline, unsubscribe, STOP, not now, already have a vendor.\n"
        "- Meeting Requested: proposes or agrees to a call, meeting, or calendar slot.\n"
        "- Out of Office: auto-reply, vacation, away from office.\n"
        "- Spam: unrelated, automated marketing, bounce noise, or not a human reply."
    )
    user = (
        f"From: {item.sender_name} <{item.sender_email}>\n"
        f"Subject: {item.subject}\n\n"
        f"{item.body}"
    )

    kwargs: dict = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        response = client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(**kwargs)

    content = (response.choices[0].message.content or "").strip()
    category = ""
    try:
        payload = json.loads(content)
        category = str(payload.get("category") or payload.get("label") or "").strip()
    except json.JSONDecodeError:
        category = content.strip().strip('"')

    for option in CATEGORIES:
        if category.lower() == option.lower():
            return option
    raise ValueError(f"Unrecognized category from model: {content[:200]}")


def find_lead(db: Client, sender_email: str) -> dict | None:
    response = (
        db.table(TABLE)
        .select("id, business_name, email, status")
        .ilike("email", sender_email)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def update_lead_status(db: Client, lead: dict, category: str) -> None:
    db.table(TABLE).update(
        {
            "status": category,
            "error_log": None,
        }
    ).eq("id", lead["id"]).execute()
    log.info(
        "Updated %s <%s> from '%s' to '%s' at %s.",
        lead.get("business_name"),
        lead.get("email"),
        lead.get("status"),
        category,
        utc_now(),
    )


def connect_imap(settings: Settings) -> tuple[imaplib.IMAP4_SSL, str]:
    host = os.getenv("IMAP_HOST", DEFAULT_IMAP_HOST).strip() or DEFAULT_IMAP_HOST
    mailbox = os.getenv("IMAP_MAILBOX", DEFAULT_IMAP_MAILBOX).strip() or DEFAULT_IMAP_MAILBOX
    client = imaplib.IMAP4_SSL(host, 993)
    try:
        client.login(settings.sender_email, settings.sender_app_password)
    except imaplib.IMAP4.error as exc:
        client.logout()
        raise SystemExit(
            f"IMAP login failed. Enable IMAP in Gmail and use an App Password. ({exc})"
        ) from exc
    status, _ = client.select(mailbox, readonly=False)
    if status != "OK":
        client.logout()
        raise SystemExit(f"Could not open mailbox {mailbox}.")
    return client, mailbox


def fetch_unread_uids(imap: imaplib.IMAP4_SSL, limit: int) -> list[str]:
    status, data = imap.uid("SEARCH", None, "UNSEEN")
    if status != "OK":
        raise RuntimeError(f"IMAP SEARCH failed: {status}")
    uids = (data[0] or b"").decode("utf-8", errors="replace").split()
    return uids[:limit]


def fetch_raw(imap: imaplib.IMAP4_SSL, uid: str) -> bytes:
    # BODY.PEEK leaves the message unseen until we STORE \Seen ourselves.
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK" or not data or data[0] is None:
        raise RuntimeError(f"IMAP FETCH failed for UID {uid}")
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    raise RuntimeError(f"IMAP FETCH returned no body for UID {uid}")


def mark_seen(imap: imaplib.IMAP4_SSL, uid: str) -> None:
    status, _ = imap.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    if status != "OK":
        raise RuntimeError(f"Could not mark UID {uid} as read.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify unread Gmail replies and update Supabase leads")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify messages without updating Supabase or marking them read.",
    )
    return parser.parse_args()


def process_uid(
    imap: imaplib.IMAP4_SSL,
    uid: str,
    settings: Settings,
    db: Client,
    openai_client: OpenAI,
) -> str:
    """
    Returns: updated | unmatched | skipped | failed
    """
    try:
        raw = fetch_raw(imap, uid)
        item = parse_unread(uid, raw, settings)
    except Exception as exc:
        log.error("UID %s — could not parse message: %s", uid, exc)
        return "failed"

    if item is None:
        return "skipped"

    if item.subject == "__OWN_MAILBOX__":
        if not settings.dry_run:
            mark_seen(imap, uid)
        return "skipped"

    try:
        category = classify_reply(openai_client, settings.openai_model, item)
    except Exception as exc:
        log.error("UID %s from %s — classification failed: %s", uid, item.sender_email, exc)
        return "failed"

    log.info("UID %s | %s | %s | %s", uid, item.sender_email, item.subject or "(no subject)", category)

    lead = find_lead(db, item.sender_email)
    if not lead:
        log.info("No outreach_leads row for %s. Category was %s.", item.sender_email, category)
        if not settings.dry_run:
            mark_seen(imap, uid)
        return "unmatched"

    if settings.dry_run:
        print(
            f"[dry-run] would set {lead.get('business_name')} "
            f"<{lead.get('email')}> -> {category}"
        )
        return "updated"

    try:
        update_lead_status(db, lead, category)
    except Exception as exc:
        log.error("UID %s — Supabase update failed: %s", uid, exc)
        return "failed"

    try:
        mark_seen(imap, uid)
    except Exception as exc:
        log.error(
            "Lead %s was updated to %s but IMAP mark-read failed: %s",
            lead.get("id"),
            category,
            exc,
        )
        return "failed"

    return "updated"


def main() -> int:
    args = parse_args()
    settings = load_settings(dry_run_override=True if args.dry_run else None)
    db = supabase_client(settings)
    openai_client = make_openai_client(settings)
    imap, mailbox = connect_imap(settings)

    try:
        uids = fetch_unread_uids(imap, settings.max_emails_per_run)
        log.info(
            "Mailbox %s: %s unread message(s) this run (limit %s). Dry run: %s.",
            mailbox,
            len(uids),
            settings.max_emails_per_run,
            settings.dry_run,
        )
        if not uids:
            return 0

        counts = {"updated": 0, "unmatched": 0, "skipped": 0, "failed": 0}
        for uid in uids:
            result = process_uid(imap, uid, settings, db, openai_client)
            counts[result] = counts.get(result, 0) + 1

        log.info(
            "Done. updated=%s unmatched=%s skipped=%s failed=%s",
            counts["updated"],
            counts["unmatched"],
            counts["skipped"],
            counts["failed"],
        )
        return 0 if counts["failed"] == 0 else 2
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
