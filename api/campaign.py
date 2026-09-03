from __future__ import annotations

import asyncio
import logging
import os
import random
import smtplib

from api.ai_engine import decode_draft, generate_email_draft
from api.config import get_settings
from api.database import SessionLocal
from api.hours import is_us_business_hours, next_us_business_open, now_eastern, wait_until_us_business_hours
from api.mailer import send_email
from api.schemas import SendEmailRequest
from api.store import count_sent_today, get_by_id, mark_sent, mark_status, save_ai_draft

log = logging.getLogger("api.campaign")

STATUS_SENT = "Sent"
STATUS_OPENED = "Opened"
STATUS_DRY_RUN = "Dry-Run Tested"

_campaign_running = False
_campaign_stop = False


def campaign_is_running() -> bool:
    return _campaign_running


def begin_campaign() -> bool:
    global _campaign_running, _campaign_stop
    if _campaign_running:
        return False
    _campaign_running = True
    _campaign_stop = False
    return True


def request_stop_campaign() -> bool:
    global _campaign_stop
    if not _campaign_running:
        return False
    _campaign_stop = True
    log.info("Campaign stop requested.")
    return True


def _end_campaign() -> None:
    global _campaign_running, _campaign_stop
    _campaign_running = False
    _campaign_stop = False


def _delay_bounds(dry_run: bool) -> tuple[int, int]:
    if dry_run:
        return 2, 5
    low = max(1, int(os.getenv("MIN_DELAY_SECONDS", "300")))
    high = max(low, int(os.getenv("MAX_DELAY_SECONDS", "600")))
    return low, high


async def _wait_for_send_window() -> bool:
    """Pause until US business hours. Returns False if the campaign was stopped."""
    while not is_us_business_hours():
        if _campaign_stop:
            return False
        nxt = next_us_business_open()
        seconds = max(1.0, (nxt - now_eastern()).total_seconds())
        log.info(
            "Waiting for US business hours (next open %s).",
            nxt.strftime("%A %I:%M %p %Z"),
        )
        await asyncio.sleep(min(seconds, 15.0))
    return not _campaign_stop


async def send_when_business_hours(payload: SendEmailRequest) -> None:
    await wait_until_us_business_hours()
    settings = get_settings()
    db = SessionLocal()
    try:
        send_email(settings, payload)
        mark_sent(db, str(payload.recipient_email))
        log.info("Sent queued email to %s after US business hours opened.", payload.recipient_email)
    except smtplib.SMTPException as exc:
        log.error("Queued send to %s failed: %s", payload.recipient_email, exc)
    except OSError as exc:
        log.error("Queued send to %s network error: %s", payload.recipient_email, exc)
    finally:
        db.close()


async def run_campaign(lead_ids: list[int], dry_run: bool = False) -> None:
    settings = get_settings()
    low, high = _delay_bounds(dry_run)
    unique_ids = list(dict.fromkeys(lead_ids))
    mode = "DRY RUN" if dry_run else "LIVE"
    cap = settings.max_emails_per_run
    db_cap = SessionLocal()
    try:
        already_today = 0 if dry_run else count_sent_today(db_cap)
    finally:
        db_cap.close()
    remaining_today = max(0, cap - already_today)
    if not dry_run and remaining_today <= 0:
        log.warning("Campaign stopped: daily cap of %s already reached.", cap)
        _end_campaign()
        return
    if not dry_run:
        unique_ids = unique_ids[:remaining_today]
        log.info("Campaign daily cap %s, already sent today %s, this run %s.", cap, already_today, len(unique_ids))
    log.info("Campaign started (%s) for %s lead(s).", mode, len(unique_ids))
    if not dry_run and not is_us_business_hours():
        nxt = next_us_business_open()
        log.info(
            "Outside US hours (%s). Live sends wait until %s.",
            now_eastern().strftime("%A %I:%M %p %Z"),
            nxt.strftime("%A %I:%M %p %Z"),
        )

    try:
        for index, lead_id in enumerate(unique_ids):
            if _campaign_stop:
                log.info("Campaign stopped before lead id %s.", lead_id)
                break

            dispatched = False
            recipient_email = ""
            business_name = ""
            subject = ""
            body = ""

            db = SessionLocal()
            try:
                lead = get_by_id(db, lead_id)
                if lead is None:
                    log.warning("Campaign skip: lead id %s not found.", lead_id)
                    continue
                if lead.status in {STATUS_SENT, STATUS_OPENED}:
                    log.info("Campaign skip: %s already %s.", lead.email, lead.status)
                    continue
                if dry_run and lead.status == STATUS_DRY_RUN:
                    log.info("Campaign skip: %s already dry-run tested.", lead.email)
                    continue
                if not lead.email:
                    log.warning("Campaign skip: lead id %s has no email.", lead_id)
                    continue

                if lead.ai_draft and lead.ai_draft.strip():
                    subject, body = decode_draft(lead.ai_draft)
                else:
                    stored = generate_email_draft(
                        lead.business_name,
                        lead.website or "",
                        lead.rating or "",
                    )
                    save_ai_draft(db, lead.id, stored)
                    subject, body = decode_draft(stored)

                if not subject or not body:
                    log.error("Campaign skip: empty draft for lead id %s.", lead_id)
                    continue

                recipient_email = lead.email
                business_name = lead.business_name
                if dry_run:
                    mark_status(db, lead.id, STATUS_DRY_RUN, set_sent_at=False)
                    dispatched = True
                    log.info("Dry-run tested %s <%s> — subject: %s", business_name, recipient_email, subject)
            except Exception as exc:
                log.error("Campaign failed for lead id %s: %s", lead_id, exc)
                continue
            finally:
                db.close()

            if dry_run or not recipient_email or not subject or not body:
                pass
            else:
                try:
                    if not await _wait_for_send_window():
                        log.info("Campaign stopped while waiting to send to %s.", recipient_email)
                        break
                    payload = SendEmailRequest(
                        recipient_email=recipient_email,
                        subject=subject,
                        body=body,
                        lead_id=lead_id,
                    )
                    send_email(settings, payload)
                    db = SessionLocal()
                    try:
                        mark_sent(db, recipient_email)
                    finally:
                        db.close()
                    dispatched = True
                    log.info("Campaign sent to %s <%s>.", business_name, recipient_email)
                except smtplib.SMTPAuthenticationError as exc:
                    log.error("Gmail login failed. Stopping live campaign: %s", exc)
                    break
                except Exception as exc:
                    log.error("Campaign failed for lead id %s: %s", lead_id, exc)

            remaining = len(unique_ids) - index - 1
            if remaining > 0 and dispatched and not _campaign_stop:
                wait_for = random.randint(low, high)
                log.info(
                    "Waiting %s seconds (%.1f minutes) before the next campaign dispatch.",
                    wait_for,
                    wait_for / 60,
                )
                await asyncio.sleep(wait_for)
        log.info("Campaign finished (%s).", mode)
    finally:
        _end_campaign()
