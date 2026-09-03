from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

import pytz

log = logging.getLogger("api.hours")

EASTERN = pytz.timezone("US/Eastern")
OPEN = time(8, 30)
CLOSE = time(16, 30)


def now_eastern() -> datetime:
    return datetime.now(EASTERN)


def is_us_business_hours(moment: datetime | None = None) -> bool:
    current = (moment or now_eastern()).astimezone(EASTERN)
    if current.weekday() >= 5:
        return False
    clock = current.time()
    return OPEN <= clock < CLOSE


def next_us_business_open(moment: datetime | None = None) -> datetime:
    current = (moment or now_eastern()).astimezone(EASTERN)
    if is_us_business_hours(current):
        return current

    today_open = EASTERN.localize(datetime.combine(current.date(), OPEN))
    if current.weekday() < 5 and current < today_open:
        return today_open

    day = current.date() + timedelta(days=1)
    while True:
        if day.weekday() < 5:
            return EASTERN.localize(datetime.combine(day, OPEN))
        day += timedelta(days=1)


async def wait_until_us_business_hours() -> None:
    """Pause until 8:30 AM–4:30 PM, Monday–Friday, US Eastern Time."""
    while not is_us_business_hours():
        nxt = next_us_business_open()
        seconds = max(1.0, (nxt - now_eastern()).total_seconds())
        log.info("Waiting for US business hours")
        await asyncio.sleep(min(seconds, 3600.0))
