from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _csv_origins(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]


class Settings(BaseModel):
    google_maps_api_key: str = ""
    apollo_api_key: str = ""
    hunter_api_key: str = ""
    builtwith_api_key: str = ""
    snov_api_key: str = ""
    sender_email: str = ""
    sender_app_password: str = ""
    sender_name: str = "Softenix Solution"
    reply_to_email: str = ""
    unsubscribe_email: str = ""
    cors_origins: list[str] = Field(default_factory=list)
    public_base_url: str = "http://localhost:8000"
    enable_tracking: bool = False
    require_business_email: bool = True
    max_emails_per_run: int = 20
    auto_send: bool = True

    def require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name, "")]
        if missing:
            raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")


def smtp_is_configured(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    email = (current.sender_email or "").strip()
    password = (current.sender_app_password or "").replace(" ", "")
    if "@" not in email:
        return False
    if not password:
        return False
    lowered = password.lower()
    if "xxxx" in lowered or "your-" in lowered or "changeme" in lowered or "placeholder" in lowered:
        return False
    return True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    sender = os.getenv("SENDER_EMAIL", "").strip()
    return Settings(
        google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY", "").strip(),
        apollo_api_key=os.getenv("APOLLO_API_KEY", "").strip(),
        hunter_api_key=os.getenv("HUNTER_API_KEY", "").strip(),
        builtwith_api_key=os.getenv("BUILTWITH_API_KEY", "").strip(),
        snov_api_key=os.getenv("SNOV_API_KEY", "").strip(),
        sender_email=sender,
        sender_app_password=os.getenv("SENDER_APP_PASSWORD", "").strip(),
        sender_name=os.getenv("SENDER_NAME", "Softenix Solution").strip() or "Softenix Solution",
        reply_to_email=os.getenv("REPLY_TO_EMAIL", sender).strip() or sender,
        unsubscribe_email=os.getenv("UNSUBSCRIBE_EMAIL", sender).strip() or sender,
        cors_origins=_csv_origins(os.getenv("CORS_ORIGINS", "")),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip()
        or "http://localhost:8000",
        enable_tracking=_env_bool("ENABLE_TRACKING", False),
        require_business_email=_env_bool("REQUIRE_BUSINESS_EMAIL", True),
        max_emails_per_run=max(1, int(os.getenv("MAX_EMAILS_PER_RUN", "20"))),
        auto_send=_env_bool("AUTO_SEND", True),
    )
