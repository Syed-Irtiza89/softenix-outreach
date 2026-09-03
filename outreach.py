"""
Softenix Solution — AI-personalized cold email outreach (Supabase).

Fetches Pending rows from outreach_leads, drafts a short email with OpenAI,
sends via Gmail SMTP, then marks the row Sent or Failed.

Usage:
    python outreach.py              # send (honors MAX_EMAILS_PER_RUN)
    python outreach.py --dry-run    # draft only; does not send or update rows
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from dotenv import load_dotenv
from email_validator import EmailNotValidError, validate_email
from openai import OpenAI
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parent
TABLE = "outreach_leads"
PENDING_STATUS = "Pending"
SENT_STATUS = "Sent"
FAILED_STATUS = "Failed"

AGENCY_NAME = "Softenix Solution"
AGENCY_PITCH = (
    "We help local businesses with modern websites, booking systems, "
    "and simple automation so they spend less time on admin and more "
    "time serving customers."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("outreach")


@dataclass(frozen=True)
class Settings:
    sender_email: str
    sender_app_password: str
    sender_name: str
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    supabase_url: str
    supabase_key: str
    max_emails_per_run: int
    followup_after_days: int
    min_delay_seconds: int
    max_delay_seconds: int
    reply_to_email: str
    unsubscribe_email: str
    dry_run: bool


@dataclass
class Lead:
    id: str
    business_name: str
    email: str
    website: str
    google_review_score: str
    observation: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_settings(dry_run_override: bool | None = None) -> Settings:
    load_dotenv(ROOT / ".env")

    required = {
        "SENDER_EMAIL": os.getenv("SENDER_EMAIL", "").strip(),
        "SENDER_APP_PASSWORD": os.getenv("SENDER_APP_PASSWORD", "").strip(),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "SUPABASE_URL": os.getenv("SUPABASE_URL", "").strip(),
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing required .env values: {', '.join(missing)}")

    min_delay = int(os.getenv("MIN_DELAY_SECONDS", "300"))
    max_delay = int(os.getenv("MAX_DELAY_SECONDS", "600"))
    if min_delay < 1 or max_delay < min_delay:
        raise SystemExit("MIN_DELAY_SECONDS / MAX_DELAY_SECONDS are invalid.")

    env_dry_run = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
    dry_run = env_dry_run if dry_run_override is None else dry_run_override

    return Settings(
        sender_email=required["SENDER_EMAIL"],
        sender_app_password=required["SENDER_APP_PASSWORD"],
        sender_name=os.getenv("SENDER_NAME", AGENCY_NAME).strip() or AGENCY_NAME,
        openai_api_key=required["OPENAI_API_KEY"],
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
        supabase_url=required["SUPABASE_URL"],
        supabase_key=required["SUPABASE_SERVICE_ROLE_KEY"],
        max_emails_per_run=int(os.getenv("MAX_EMAILS_PER_RUN", "20")),
        followup_after_days=max(1, int(os.getenv("FOLLOWUP_AFTER_DAYS", "3"))),
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
        reply_to_email=os.getenv("REPLY_TO_EMAIL", required["SENDER_EMAIL"]).strip(),
        unsubscribe_email=os.getenv("UNSUBSCRIBE_EMAIL", required["SENDER_EMAIL"]).strip(),
        dry_run=dry_run,
    )


def supabase_client(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_key)


def make_openai_client(settings: Settings) -> OpenAI:
    kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return OpenAI(**kwargs)


def sender_domain(settings: Settings) -> str:
    if "@" in settings.sender_email:
        return settings.sender_email.rsplit("@", 1)[1]
    return "localhost"


def fetch_pending_leads(db: Client, limit: int) -> list[Lead]:
    response = (
        db.table(TABLE)
        .select(
            "id, business_name, email, website, google_review_score, observation, status"
        )
        .eq("status", PENDING_STATUS)
        .order("created_at")
        .limit(limit)
        .execute()
    )
    leads: list[Lead] = []
    for row in response.data or []:
        email = (row.get("email") or "").strip()
        name = (row.get("business_name") or "").strip()
        if not row.get("id") or not name:
            continue
        leads.append(
            Lead(
                id=str(row["id"]),
                business_name=name,
                email=email,
                website=(row.get("website") or "").strip(),
                google_review_score=str(row.get("google_review_score") or "").strip(),
                observation=(row.get("observation") or "").strip(),
            )
        )
    return leads


def mark_sent(db: Client, lead: Lead, subject: str, message_id: str) -> None:
    db.table(TABLE).update(
        {
            "status": SENT_STATUS,
            "sent_at": utc_now(),
            "subject": subject,
            "message_id": message_id,
            "error_log": None,
        }
    ).eq("id", lead.id).execute()


def mark_failed(db: Client, lead: Lead, error: str) -> None:
    db.table(TABLE).update(
        {
            "status": FAILED_STATUS,
            "error_log": error[:2000],
        }
    ).eq("id", lead.id).execute()


def is_valid_email(address: str) -> bool:
    try:
        validate_email(address, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def generate_email(client: OpenAI, model: str, lead: Lead, settings: Settings) -> tuple[str, str]:
    website = lead.website or "none listed"
    system = (
        f"You are a friendly, concise representative of {AGENCY_NAME}, "
        "a small agency that builds websites and light automation for local businesses. "
        "Write one cold outreach email. Rules:\n"
        "- Short: 80 to 130 words in the body.\n"
        "- Conversational, not salesy. No hype, no exclamation marks, no emojis.\n"
        "- Naturally mention the Google review score and the observation as proof you looked them up.\n"
        "- Offer a relevant next step (a 15-minute look at their site or listing), not a hard pitch.\n"
        "- Do not invent facts. Do not claim you already audited them in depth.\n"
        "- Do not include a subject line in the body.\n"
        "- Sign off as a person from Softenix Solution. Do not invent a last name; use first name Alex.\n"
        "- Do not add unsubscribe text; that is appended separately."
    )
    user = (
        f"Business name: {lead.business_name}\n"
        f"Website: {website}\n"
        f"Google review score: {lead.google_review_score or 'not available'}\n"
        f"Observation: {lead.observation or 'none'}\n"
        f"Agency context: {AGENCY_PITCH}\n"
        "Return JSON with keys subject and body only."
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.6,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = response.choices[0].message.content or ""

    try:
        payload = json.loads(content)
        subject = str(payload.get("subject", "")).strip()
        body = str(payload.get("body", "")).strip()
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI returned invalid JSON.") from exc

    if not subject or not body:
        raise ValueError("OpenAI response missing subject or body.")

    footer = (
        f"\n\n—\n"
        f"{settings.sender_name}\n"
        f"If this is not useful, reply STOP or email {settings.unsubscribe_email} "
        "and we will not contact you again."
    )
    if footer.strip() not in body:
        body = body.rstrip() + footer

    return subject, body


def build_message(settings: Settings, lead: Lead, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.sender_name} <{settings.sender_email}>"
    message["To"] = lead.email
    message["Reply-To"] = settings.reply_to_email
    message["Message-ID"] = make_msgid(domain=sender_domain(settings))
    message.set_content(body)
    return message


def send_via_gmail(settings: Settings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(settings.sender_email, settings.sender_app_password)
        server.send_message(message)


def randomized_delay(settings: Settings) -> int:
    return random.randint(settings.min_delay_seconds, settings.max_delay_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Softenix Solution outreach sender (Supabase)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate emails and print them without sending or updating Supabase.",
    )
    return parser.parse_args()


def process_lead(
    lead: Lead,
    settings: Settings,
    db: Client,
    openai_client: OpenAI,
) -> str:
    """
    Returns: sent | failed | auth_error
    """
    if not is_valid_email(lead.email):
        error = f"Invalid email address: {lead.email or '(empty)'}"
        log.error("%s — %s", lead.business_name, error)
        if not settings.dry_run:
            mark_failed(db, lead, error)
        return "failed"

    try:
        subject, body = generate_email(openai_client, settings.openai_model, lead, settings)
    except Exception as exc:
        error = f"AI draft failed: {exc}"
        log.error("%s — %s", lead.business_name, error)
        if not settings.dry_run:
            mark_failed(db, lead, error)
        return "failed"

    log.info("Drafted for %s | subject: %s", lead.business_name, subject)

    if settings.dry_run:
        print("\n" + "=" * 72)
        print(f"ID: {lead.id}")
        print(f"TO: {lead.email}")
        print(f"SUBJECT: {subject}")
        print(body)
        print("=" * 72 + "\n")
        return "sent"

    message = build_message(settings, lead, subject, body)
    try:
        send_via_gmail(settings, message)
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail login failed. Check SENDER_EMAIL and SENDER_APP_PASSWORD.")
        return "auth_error"
    except smtplib.SMTPRecipientsRefused as exc:
        error = f"Recipient refused: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_failed(db, lead, error)
        return "failed"
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_failed(db, lead, error)
        return "failed"
    except OSError as exc:
        error = f"Network error: {exc}"
        log.error("%s — %s", lead.email, error)
        mark_failed(db, lead, error)
        return "failed"

    mark_sent(db, lead, subject, message["Message-ID"] or "")
    log.info("Sent to %s <%s> and marked Sent in Supabase.", lead.business_name, lead.email)
    return "sent"


def main() -> int:
    args = parse_args()
    settings = load_settings(dry_run_override=True if args.dry_run else None)
    db = supabase_client(settings)
    openai_client = make_openai_client(settings)

    leads = fetch_pending_leads(db, settings.max_emails_per_run)
    log.info(
        "Fetched %s Pending lead(s) from %s (limit %s). Dry run: %s.",
        len(leads),
        TABLE,
        settings.max_emails_per_run,
        settings.dry_run,
    )
    if not leads:
        log.info("Nothing to send.")
        return 0

    sent_this_run = 0
    failed = 0

    for index, lead in enumerate(leads):
        result = process_lead(lead, settings, db, openai_client)
        if result == "auth_error":
            log.error("Stopping the run so remaining leads stay Pending.")
            return 1
        if result == "failed":
            failed += 1
            continue

        sent_this_run += 1
        remaining = len(leads) - index - 1
        if remaining > 0 and not settings.dry_run:
            wait_for = randomized_delay(settings)
            log.info(
                "Waiting %s seconds (%.1f minutes) before the next send.",
                wait_for,
                wait_for / 60,
            )
            time.sleep(wait_for)

    log.info("Done. sent=%s failed=%s", sent_this_run, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
