"""
Softenix Solution — auto follow-up for unanswered outreach.

Finds outreach_leads where status is Sent and sent_at is older than
FOLLOWUP_AFTER_DAYS (default 3), drafts a short bump, and sends it in
the same thread using In-Reply-To / References when message_id exists.

Usage:
    python followup.py
    python followup.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from openai import OpenAI
from supabase import Client

from outreach import (
    AGENCY_NAME,
    TABLE,
    Settings,
    is_valid_email,
    load_settings,
    make_openai_client,
    randomized_delay,
    send_via_gmail,
    sender_domain,
    supabase_client,
    utc_now,
)

SENT_STATUS = "Sent"
FOLLOWUP_SENT_STATUS = "Follow-up 1 Sent"
FOLLOWUP_FAILED_STATUS = "Follow-up Failed"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("followup")


@dataclass
class FollowupLead:
    id: str
    business_name: str
    email: str
    website: str
    observation: str
    subject: str
    message_id: str
    sent_at: str


def cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def fetch_due_leads(db: Client, settings: Settings) -> list[FollowupLead]:
    response = (
        db.table(TABLE)
        .select(
            "id, business_name, email, website, observation, subject, message_id, sent_at, status"
        )
        .eq("status", SENT_STATUS)
        .lt("sent_at", cutoff_iso(settings.followup_after_days))
        .order("sent_at")
        .limit(settings.max_emails_per_run)
        .execute()
    )
    leads: list[FollowupLead] = []
    for row in response.data or []:
        if not row.get("id"):
            continue
        leads.append(
            FollowupLead(
                id=str(row["id"]),
                business_name=(row.get("business_name") or "").strip(),
                email=(row.get("email") or "").strip(),
                website=(row.get("website") or "").strip(),
                observation=(row.get("observation") or "").strip(),
                subject=(row.get("subject") or "").strip(),
                message_id=(row.get("message_id") or "").strip(),
                sent_at=str(row.get("sent_at") or ""),
            )
        )
    return leads


def mark_followup_sent(db: Client, lead: FollowupLead) -> None:
    db.table(TABLE).update(
        {
            "status": FOLLOWUP_SENT_STATUS,
            "followup_sent_at": utc_now(),
            "error_log": None,
        }
    ).eq("id", lead.id).eq("status", SENT_STATUS).execute()


def mark_followup_failed(db: Client, lead: FollowupLead, error: str) -> None:
    db.table(TABLE).update(
        {
            "status": FOLLOWUP_FAILED_STATUS,
            "error_log": error[:2000],
        }
    ).eq("id", lead.id).execute()


def thread_subject(original: str) -> str:
    original = original.strip() or "Quick note"
    if original.lower().startswith("re:"):
        return original
    return f"Re: {original}"


def as_message_id(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not value.startswith("<"):
        value = f"<{value}>"
    if not value.endswith(">"):
        value = f"{value}>"
    return value


def generate_bump(client: OpenAI, model: str, lead: FollowupLead) -> str:
    system = (
        f"You are Alex at {AGENCY_NAME}. Write a very short, polite follow-up "
        "bump to an unanswered first email. Rules:\n"
        "- 40 to 70 words.\n"
        "- One short paragraph, conversational, no guilt, no hype, no emojis, "
        "no exclamation marks.\n"
        "- Bring the original note back to the top of their inbox.\n"
        "- Do not repeat a full sales pitch. Do not invent that they promised a reply.\n"
        "- Sign off as Alex at Softenix Solution.\n"
        "- Return JSON with a single key: body."
    )
    user = (
        f"Business name: {lead.business_name}\n"
        f"Website: {lead.website or 'none listed'}\n"
        f"Original subject: {lead.subject or 'none'}\n"
        f"Original observation: {lead.observation or 'none'}\n"
    )

    kwargs: dict = {
        "model": model,
        "temperature": 0.5,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # OpenAI supports json_object; some Ollama models do not.
    try:
        response = client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(**kwargs)

    content = (response.choices[0].message.content or "").strip()
    body = content
    try:
        payload = json.loads(content)
        body = str(payload.get("body") or content).strip()
    except json.JSONDecodeError:
        pass

    if not body:
        raise ValueError("Model returned an empty follow-up body.")
    return body


def build_followup_message(
    settings: Settings,
    lead: FollowupLead,
    body: str,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = thread_subject(lead.subject)
    message["From"] = f"{settings.sender_name} <{settings.sender_email}>"
    message["To"] = lead.email
    message["Reply-To"] = settings.reply_to_email
    message["Message-ID"] = make_msgid(domain=sender_domain(settings))

    original_id = as_message_id(lead.message_id)
    if original_id:
        message["In-Reply-To"] = original_id
        message["References"] = original_id

    footer = (
        f"\n\n—\n"
        f"{settings.sender_name}\n"
        f"If this is not useful, reply STOP or email {settings.unsubscribe_email} "
        "and we will not contact you again."
    )
    message.set_content(body.rstrip() + footer)
    return message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send first follow-up to unanswered outreach")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Draft follow-ups without sending or updating Supabase.",
    )
    return parser.parse_args()


def process_lead(
    lead: FollowupLead,
    settings: Settings,
    db: Client,
    openai_client: OpenAI,
) -> str:
    if not is_valid_email(lead.email):
        error = f"Invalid email address: {lead.email or '(empty)'}"
        log.error("%s — %s", lead.business_name, error)
        if not settings.dry_run:
            mark_followup_failed(db, lead, error)
        return "failed"

    try:
        body = generate_bump(openai_client, settings.openai_model, lead)
    except Exception as exc:
        error = f"AI follow-up draft failed: {exc}"
        log.error("%s — %s", lead.business_name, error)
        if not settings.dry_run:
            mark_followup_failed(db, lead, error)
        return "failed"

    subject = thread_subject(lead.subject)
    log.info("Drafted follow-up for %s | subject: %s", lead.business_name, subject)

    if settings.dry_run:
        print("\n" + "=" * 72)
        print(f"ID: {lead.id}")
        print(f"TO: {lead.email}")
        print(f"SUBJECT: {subject}")
        print(f"IN-REPLY-TO: {as_message_id(lead.message_id) or '(none stored — subject-only thread)'}")
        print(body)
        print("=" * 72 + "\n")
        return "sent"

    message = build_followup_message(settings, lead, body)
    try:
        send_via_gmail(settings, message)
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail login failed. Check SENDER_EMAIL and SENDER_APP_PASSWORD.")
        return "auth_error"
    except smtplib.SMTPRecipientsRefused as exc:
        error = f"Recipient refused: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_followup_failed(db, lead, error)
        return "failed"
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_followup_failed(db, lead, error)
        return "failed"
    except OSError as exc:
        error = f"Network error: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_followup_failed(db, lead, error)
        return "failed"

    mark_followup_sent(db, lead)
    log.info(
        "Follow-up sent to %s <%s>. Status set to '%s'.",
        lead.business_name,
        lead.email,
        FOLLOWUP_SENT_STATUS,
    )
    return "sent"


def main() -> int:
    args = parse_args()
    settings = load_settings(dry_run_override=True if args.dry_run else None)
    db = supabase_client(settings)
    openai_client = make_openai_client(settings)

    leads = fetch_due_leads(db, settings)
    log.info(
        "Found %s Sent lead(s) older than %s day(s). Limit %s. Dry run: %s.",
        len(leads),
        settings.followup_after_days,
        settings.max_emails_per_run,
        settings.dry_run,
    )
    if not leads:
        log.info("No follow-ups due.")
        return 0

    sent_this_run = 0
    failed = 0

    for index, lead in enumerate(leads):
        result = process_lead(lead, settings, db, openai_client)
        if result == "auth_error":
            log.error("Stopping the run so remaining leads stay Sent.")
            return 1
        if result == "failed":
            failed += 1
            continue

        sent_this_run += 1
        remaining = len(leads) - index - 1
        if remaining > 0 and not settings.dry_run:
            wait_for = randomized_delay(settings)
            log.info(
                "Waiting %s seconds (%.1f minutes) before the next follow-up.",
                wait_for,
                wait_for / 60,
            )
            time.sleep(wait_for)

    log.info("Done. follow-ups sent=%s failed=%s", sent_this_run, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
