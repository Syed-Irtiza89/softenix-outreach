from __future__ import annotations

import asyncio
import logging

from api.campaign import begin_campaign, campaign_is_running, run_campaign
from api.config import get_settings, smtp_is_configured
from api.database import SessionLocal
from api.hours import is_us_business_hours, next_us_business_open, now_eastern
from api.store import count_sent_today, list_unsent_lead_ids

log = logging.getLogger("api.autosend")

_last_error = ""


def last_autosend_error() -> str:
    return _last_error


def _set_error(message: str) -> None:
    global _last_error
    _last_error = message


async def run_auto_send_loop() -> None:
    settings = get_settings()
    log.info(
        "Auto-send loop started (enabled=%s, cap=%s/day, US hours Mon-Fri 8:30-4:30 Eastern).",
        settings.auto_send,
        settings.max_emails_per_run,
    )
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            log.info("Auto-send loop stopped.")
            raise
        except Exception:
            log.exception("Auto-send tick failed.")
            _set_error("Auto-send hit an unexpected error. Check the API log.")
        await asyncio.sleep(20)


async def _tick() -> None:
    settings = get_settings()
    if not settings.auto_send:
        return
    if not smtp_is_configured(settings):
        _set_error(
            "AUTO_SEND is on, but SENDER_EMAIL / SENDER_APP_PASSWORD are missing or still placeholders. "
            "Add a real Gmail App Password, then restart the API."
        )
        log.warning(_last_error)
        await asyncio.sleep(40)
        return
    if campaign_is_running():
        return

    db = SessionLocal()
    try:
        sent_today = count_sent_today(db)
        remaining = max(0, settings.max_emails_per_run - sent_today)
        pending_ids = list_unsent_lead_ids(db, remaining or 1)
    finally:
        db.close()

    if remaining <= 0:
        _set_error("")
        nxt = next_us_business_open()
        log.info(
            "Daily cap reached (%s/%s). Next window %s.",
            sent_today,
            settings.max_emails_per_run,
            nxt.strftime("%A %I:%M %p %Z"),
        )
        await asyncio.sleep(60)
        return

    if not pending_ids:
        _set_error("Auto-send is on, but there are no unsent leads with email addresses.")
        return

    if not is_us_business_hours():
        nxt = next_us_business_open()
        _set_error(
            f"Auto-send waiting for US business hours. Next open {nxt.strftime('%A %I:%M %p %Z')} "
            f"(now {now_eastern().strftime('%A %I:%M %p %Z')})."
        )
        return

    ids = pending_ids[:remaining]
    if not begin_campaign():
        return
    _set_error("")
    log.info("Auto-send dispatching %s lead(s). Already sent today: %s/%s.", len(ids), sent_today, settings.max_emails_per_run)
    await run_campaign(ids, False)
