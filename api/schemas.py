from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

SCRAPE_SOURCES = ("yellowpages", "google_maps", "duckduckgo", "no_website")


class ScrapeQueryRequest(BaseModel):
    query: str = Field(..., min_length=2, examples=["Plumbers in Austin TX"])
    source: str = Field(
        default="auto",
        description='Scraping source: "auto", "duckduckgo", "no_website", "yellowpages", or "google_maps".',
        examples=["auto"],
    )


ScrapeRequest = ScrapeQueryRequest


class ScrapeStartedResponse(BaseModel):
    message: str
    query: str
    source: str = "auto"


class ScrapeLeadsRequest(BaseModel):
    search_query: str = Field(..., min_length=2, examples=["Software Houses"])
    location: str = Field("USA", min_length=2, examples=["Texas"])
    max_results: int = Field(10, ge=1, le=20)


class SavedLead(BaseModel):
    id: int
    business_name: str
    email: EmailStr
    website: str
    rating: str = ""
    contact_name: str = ""
    job_title: str = ""
    source: str = ""
    status: str
    ai_draft: str = ""
    sent_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class GenerateDraftResponse(BaseModel):
    lead_id: int
    subject: str
    body: str
    ai_draft: str


class UpdateDraftRequest(BaseModel):
    lead_id: int
    subject: str = ""
    body: str = ""
    ai_draft: str = ""


class UpdateDraftResponse(BaseModel):
    ok: bool
    lead_id: int
    ai_draft: str


class SendEmailRequest(BaseModel):
    recipient_email: EmailStr
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    lead_id: int | None = None


class SendEmailResponse(BaseModel):
    ok: bool
    message: str
    message_id: str = ""
    queued: bool = False


class StartCampaignRequest(BaseModel):
    lead_ids: list[int] = Field(..., min_length=1)
    dry_run: bool = False


class StartCampaignResponse(BaseModel):
    message: str
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class JobsStatusResponse(BaseModel):
    scrape_running: bool
    campaign_running: bool
    last_scrape_source: str = ""
    last_scrape_saved: int = 0
    last_scrape_skipped: int = 0
    last_scrape_error: str = ""
    auto_send: bool = False
    smtp_ready: bool = False
    sent_today: int = 0
    daily_cap: int = 20
    in_send_window: bool = False
    next_send_window: str = ""
    autosend_error: str = ""
    eastern_now: str = ""


class ServiceCreditSnapshot(BaseModel):
    service: str
    monthly_limit: int
    daily_limit: int
    used_month: int
    used_today: int
    remaining_month: int
    remaining_day: int


class AgentLead(BaseModel):
    contact_name: str = ""
    job_title: str = ""
    email: str = ""
    business_name: str = ""
    website: str = ""
    source: str = ""


class AgentCommandRequest(BaseModel):
    prompt: str = Field(..., min_length=3, examples=["Go to Apollo and get 5 plumbers in Austin TX"])


class AgentCommandResponse(BaseModel):
    message: str
    tool: str = ""
    saved: int = 0
    skipped: int = 0
    leads: list[AgentLead] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    credits: ServiceCreditSnapshot | None = None
    credits_all: list[ServiceCreditSnapshot] = Field(default_factory=list)
