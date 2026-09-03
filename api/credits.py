from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database import ServiceCredit

CREDIT_PLANS: dict[str, tuple[int, int]] = {
    "apollo": (10_000, 330),
    "hunter": (25, 1),
    "snovio": (50, 2),
    "builtwith": (100, 3),
}


def _period_keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")


def seed_service_credits(db: Session) -> None:
    month_key, day_key = _period_keys()
    for service, (monthly, daily) in CREDIT_PLANS.items():
        row = db.get(ServiceCredit, service)
        if row is None:
            db.add(
                ServiceCredit(
                    service=service,
                    monthly_limit=monthly,
                    daily_limit=daily,
                    used_month=0,
                    used_today=0,
                    month_key=month_key,
                    day_key=day_key,
                )
            )
        else:
            row.monthly_limit = monthly
            row.daily_limit = daily
            _roll_period(row, month_key, day_key)
    db.commit()


def _roll_period(row: ServiceCredit, month_key: str, day_key: str) -> None:
    if row.month_key != month_key:
        row.month_key = month_key
        row.used_month = 0
    if row.day_key != day_key:
        row.day_key = day_key
        row.used_today = 0


def _snapshot(row: ServiceCredit) -> dict[str, int | str]:
    remaining_month = max(0, row.monthly_limit - row.used_month)
    remaining_day = max(0, row.daily_limit - row.used_today)
    return {
        "service": row.service,
        "monthly_limit": row.monthly_limit,
        "daily_limit": row.daily_limit,
        "used_month": row.used_month,
        "used_today": row.used_today,
        "remaining_month": remaining_month,
        "remaining_day": min(remaining_month, remaining_day),
    }


def list_credits(db: Session) -> list[dict[str, int | str]]:
    seed_service_credits(db)
    rows = list(db.scalars(select(ServiceCredit).order_by(ServiceCredit.service)))
    return [_snapshot(row) for row in rows]


def remaining_daily(db: Session, service: str) -> int:
    seed_service_credits(db)
    row = db.get(ServiceCredit, service)
    if row is None:
        return 0
    snap = _snapshot(row)
    return int(snap["remaining_day"])


class CreditLimitError(RuntimeError):
    pass


def consume_credits(db: Session, service: str, amount: int) -> dict[str, int | str]:
    if amount <= 0:
        seed_service_credits(db)
        row = db.get(ServiceCredit, service)
        if row is None:
            raise CreditLimitError(f"Unknown credit service: {service}")
        return _snapshot(row)

    seed_service_credits(db)
    row = db.get(ServiceCredit, service)
    if row is None:
        raise CreditLimitError(f"Unknown credit service: {service}")
    available = int(_snapshot(row)["remaining_day"])
    if amount > available:
        raise CreditLimitError(
            f"{service} daily/monthly credit limit reached "
            f"({available} remaining, need {amount})."
        )
    row.used_today += amount
    row.used_month += amount
    db.commit()
    db.refresh(row)
    return _snapshot(row)
