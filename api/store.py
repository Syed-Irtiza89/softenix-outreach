from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.database import Lead
from api.hours import EASTERN


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_by_email(db: Session, email: str) -> Lead | None:
    return db.scalars(select(Lead).where(func.lower(Lead.email) == email.lower())).first()


def get_by_id(db: Session, lead_id: int) -> Lead | None:
    return db.get(Lead, lead_id)


def list_leads(db: Session) -> list[Lead]:
    return list(db.scalars(select(Lead).order_by(Lead.id.desc())))


def upsert_lead(
    db: Session,
    *,
    business_name: str,
    email: str,
    website: str,
    rating: str,
    contact_name: str = "",
    job_title: str = "",
    source: str = "",
) -> bool:
    """Insert a new email. Existing rows are left unchanged. Returns True if inserted."""
    normalized = email.strip().lower()
    if get_by_email(db, normalized):
        return False
    db.add(
        Lead(
            business_name=business_name.strip(),
            email=normalized,
            website=(website or "").strip(),
            rating=(rating or "").strip(),
            contact_name=(contact_name or "").strip(),
            job_title=(job_title or "").strip(),
            source=(source or "").strip(),
            status="Pending",
            ai_draft="",
        )
    )
    db.commit()
    return True


def save_ai_draft(db: Session, lead_id: int, ai_draft: str) -> Lead | None:
    lead = get_by_id(db, lead_id)
    if lead is None:
        return None
    lead.ai_draft = ai_draft
    db.commit()
    db.refresh(lead)
    return lead


def mark_status(db: Session, lead_id: int, status: str, *, set_sent_at: bool = False) -> Lead | None:
    lead = get_by_id(db, lead_id)
    if lead is None:
        return None
    lead.status = status
    if set_sent_at:
        lead.sent_at = utcnow()
    db.commit()
    db.refresh(lead)
    return lead


def mark_sent(db: Session, email: str) -> Lead | None:
    lead = get_by_email(db, str(email))
    if lead is None:
        return None
    lead.status = "Sent"
    lead.sent_at = utcnow()
    db.commit()
    db.refresh(lead)
    return lead


def count_sent_today(db: Session) -> int:
    now = datetime.now(EASTERN)
    start_local = EASTERN.localize(datetime.combine(now.date(), time.min))
    start_utc = start_local.astimezone(timezone.utc)
    total = db.scalar(
        select(func.count())
        .select_from(Lead)
        .where(Lead.status.in_(("Sent", "Opened")), Lead.sent_at >= start_utc)
    )
    return int(total or 0)


def list_unsent_lead_ids(db: Session, limit: int) -> list[int]:
    cap = max(1, int(limit))
    rows = db.scalars(
        select(Lead.id)
        .where(
            Lead.status.notin_(("Sent", "Opened")),
            Lead.email != "",
        )
        .order_by(Lead.id.asc())
        .limit(cap)
    )
    return list(rows)


def mark_opened(db: Session, lead_id: int) -> Lead | None:
    lead = get_by_id(db, lead_id)
    if lead is None:
        return None
    if lead.status != "Sent":
        return lead
    lead.status = "Opened"
    db.commit()
    db.refresh(lead)
    return lead
