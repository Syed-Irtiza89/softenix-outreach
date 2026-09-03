from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from api.config import ROOT

DB_PATH = ROOT / "outreach.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("email", name="uq_leads_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    website: Mapped[str] = mapped_column(String(500), default="")
    rating: Mapped[str] = mapped_column(String(16), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    job_title: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False)
    ai_draft: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ServiceCredit(Base):
    __tablename__ = "service_credits"

    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    used_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    month_key: Mapped[str] = mapped_column(String(7), default="", nullable=False)
    day_key: Mapped[str] = mapped_column(String(10), default="", nullable=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(leads)")).fetchall()}
        if "ai_draft" not in cols:
            conn.execute(text("ALTER TABLE leads ADD COLUMN ai_draft TEXT DEFAULT ''"))
        if "contact_name" not in cols:
            conn.execute(text("ALTER TABLE leads ADD COLUMN contact_name VARCHAR(255) DEFAULT ''"))
        if "job_title" not in cols:
            conn.execute(text("ALTER TABLE leads ADD COLUMN job_title VARCHAR(255) DEFAULT ''"))
        if "source" not in cols:
            conn.execute(text("ALTER TABLE leads ADD COLUMN source VARCHAR(64) DEFAULT ''"))
    from api.credits import seed_service_credits

    db = SessionLocal()
    try:
        seed_service_credits(db)
    finally:
        db.close()
