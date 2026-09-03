"""
Softenix Solution — FastAPI outreach backend.

Run:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from api.agent import run_agent_command
from api.ai_engine import decode_draft, encode_draft, generate_email_draft
from api.autosend import last_autosend_error, run_auto_send_loop
from api.campaign import (
    begin_campaign,
    campaign_is_running,
    request_stop_campaign,
    run_campaign,
    send_when_business_hours,
)
from api.config import get_settings, smtp_is_configured
from api.credits import CreditLimitError, list_credits
from api.database import SessionLocal, get_db, init_db
from api.hours import is_us_business_hours, next_us_business_open, now_eastern
from api.mailer import TRANSPARENT_GIF, send_email
from api.schemas import (
    AgentCommandRequest,
    AgentCommandResponse,
    GenerateDraftResponse,
    JobsStatusResponse,
    SavedLead,
    ScrapeLeadsRequest,
    ScrapeRequest,
    ScrapeStartedResponse,
    SendEmailRequest,
    SendEmailResponse,
    ServiceCreditSnapshot,
    StartCampaignRequest,
    StartCampaignResponse,
    UpdateDraftRequest,
    UpdateDraftResponse,
)
from api.scraper import (
    VALID_SCRAPE_SOURCES,
    begin_scrape,
    last_scrape_status,
    run_scrape_job_async,
    scrape_is_running,
)
from api.store import count_sent_today, get_by_email, get_by_id, list_leads, mark_opened, mark_sent, save_ai_draft
from api.validate import is_consumer_sender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("api")

settings = get_settings()
init_db()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(run_auto_send_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

CORS_ORIGINS = list(
    dict.fromkeys(
        [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            *settings.cors_origins,
        ]
    )
)

app = FastAPI(
    title="Softenix Outreach API",
    description="Lead scrape, AI agent tools, and Gmail campaign endpoints.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _start_scrape(query: str, source: str, background_tasks: BackgroundTasks) -> ScrapeStartedResponse:
    source = (source or "auto").strip().lower()
    if source not in VALID_SCRAPE_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid source '{source}'. Use one of: {', '.join(VALID_SCRAPE_SOURCES)}."
            ),
        )
    if source == "google_maps":
        key = settings.google_maps_api_key
        if not key or "your-google" in key.lower():
            raise HTTPException(
                status_code=400,
                detail="GOOGLE_MAPS_API_KEY is missing or still a placeholder. Use Auto, or add a real Places API key.",
            )

    if not begin_scrape():
        raise HTTPException(status_code=409, detail="A scrape job is already running.")

    log.info("Scrape source selected: %s — query=%r", source, query)
    background_tasks.add_task(run_scrape_job_async, query, source)
    return ScrapeStartedResponse(message="Scrape started", query=query, source=source)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/jobs", response_model=JobsStatusResponse)
def jobs_status() -> JobsStatusResponse:
    last = last_scrape_status()
    db = SessionLocal()
    try:
        sent_today = count_sent_today(db)
    finally:
        db.close()
    nxt = next_us_business_open()
    return JobsStatusResponse(
        scrape_running=scrape_is_running(),
        campaign_running=campaign_is_running(),
        last_scrape_source=str(last.get("source") or ""),
        last_scrape_saved=int(last.get("saved") or 0),
        last_scrape_skipped=int(last.get("skipped") or 0),
        last_scrape_error=str(last.get("error") or ""),
        auto_send=settings.auto_send,
        smtp_ready=smtp_is_configured(settings),
        sent_today=sent_today,
        daily_cap=settings.max_emails_per_run,
        in_send_window=is_us_business_hours(),
        next_send_window=nxt.strftime("%A %I:%M %p %Z"),
        autosend_error=last_autosend_error(),
        eastern_now=now_eastern().strftime("%A %I:%M %p %Z"),
    )


@app.post("/api/scrape", response_model=ScrapeStartedResponse)
def scrape_leads(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
) -> ScrapeStartedResponse:
    return _start_scrape(request.query.strip(), request.source, background_tasks)


@app.get("/api/credits", response_model=list[ServiceCreditSnapshot])
def fetch_credits(db: Session = Depends(get_db)) -> list[ServiceCreditSnapshot]:
    return [ServiceCreditSnapshot.model_validate(row) for row in list_credits(db)]


@app.post("/api/agent/command", response_model=AgentCommandResponse)
def agent_command(payload: AgentCommandRequest, db: Session = Depends(get_db)) -> AgentCommandResponse:
    prompt = payload.prompt.strip()
    try:
        result = run_agent_command(db, prompt)
    except CreditLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Agent command failed")
        raise HTTPException(status_code=502, detail=f"Agent command failed: {exc}") from exc

    used = result.get("credits")
    return AgentCommandResponse(
        message=str(result.get("message") or "Done."),
        tool=str(result.get("tool") or ""),
        saved=int(result.get("saved") or 0),
        skipped=int(result.get("skipped") or 0),
        leads=result.get("leads") or [],
        tech_stack=list(result.get("tech_stack") or []),
        credits=ServiceCreditSnapshot.model_validate(used) if used else None,
        credits_all=[ServiceCreditSnapshot.model_validate(row) for row in result.get("credits_all") or []],
    )


@app.get("/api/leads", response_model=list[SavedLead])
def fetch_leads(db: Session = Depends(get_db)) -> list[SavedLead]:
    return list_leads(db)


@app.post("/api/generate-draft/{lead_id}", response_model=GenerateDraftResponse)
def create_draft(lead_id: int, db: Session = Depends(get_db)) -> GenerateDraftResponse:
    lead = get_by_id(db, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found.")
    try:
        stored = generate_email_draft(lead.business_name, lead.website or "", lead.rating or "")
    except Exception as exc:
        log.exception("Draft generation failed for lead %s", lead_id)
        raise HTTPException(status_code=502, detail=f"Draft generation failed: {exc}") from exc

    save_ai_draft(db, lead_id, stored)
    subject, body = decode_draft(stored)
    log.info("Saved AI draft for lead %s (%s)", lead_id, lead.business_name)
    return GenerateDraftResponse(lead_id=lead_id, subject=subject, body=body, ai_draft=stored)


@app.post("/api/update-draft", response_model=UpdateDraftResponse)
def update_draft(payload: UpdateDraftRequest, db: Session = Depends(get_db)) -> UpdateDraftResponse:
    lead = get_by_id(db, payload.lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {payload.lead_id} not found.")

    stored = payload.ai_draft.strip() if payload.ai_draft.strip() else encode_draft(payload.subject, payload.body)
    if not stored.strip():
        raise HTTPException(status_code=400, detail="Draft text is empty.")

    save_ai_draft(db, payload.lead_id, stored)
    log.info("Manually updated draft for lead %s", payload.lead_id)
    return UpdateDraftResponse(ok=True, lead_id=payload.lead_id, ai_draft=stored)


@app.post("/api/start-campaign", response_model=StartCampaignResponse)
def start_campaign(
    payload: StartCampaignRequest,
    background_tasks: BackgroundTasks,
) -> StartCampaignResponse:
    if not payload.dry_run:
        try:
            settings.require("sender_email", "sender_app_password")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not begin_campaign():
        raise HTTPException(status_code=409, detail="A campaign is already running.")

    warnings: list[str] = []
    if not payload.dry_run and not is_us_business_hours():
        nxt = next_us_business_open()
        warnings.append(
            f"Outside US business hours. Live sends wait until {nxt.strftime('%A %I:%M %p %Z')}."
        )
    if not payload.dry_run and is_consumer_sender(settings.sender_email):
        warnings.append(
            "You are sending from a free mailbox. Cold mail from Gmail/Yahoo/Outlook often lands in spam "
            "and can get the account limited. Use Google Workspace on your domain with SPF, DKIM, and DMARC."
        )

    log.info(
        "Queueing campaign dry_run=%s for %s lead(s)",
        payload.dry_run,
        len(payload.lead_ids),
    )
    background_tasks.add_task(run_campaign, payload.lead_ids, payload.dry_run)
    message = "Dry-run campaign started" if payload.dry_run else "Campaign started"
    return StartCampaignResponse(message=message, dry_run=payload.dry_run, warnings=warnings)


@app.post("/api/stop-campaign")
def stop_campaign() -> dict[str, str]:
    if not request_stop_campaign():
        return {"message": "No campaign is running."}
    return {"message": "Campaign stop requested."}


@app.post("/api/send-email", response_model=SendEmailResponse)
async def deliver_email(
    payload: SendEmailRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SendEmailResponse:
    try:
        settings.require("sender_email", "sender_app_password")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.lead_id is None:
        existing = get_by_email(db, str(payload.recipient_email))
        if existing is not None:
            payload = payload.model_copy(update={"lead_id": existing.id})

    if payload.lead_id is not None:
        save_ai_draft(db, payload.lead_id, encode_draft(payload.subject, payload.body))

    if not is_us_business_hours():
        opens = next_us_business_open()
        log.info("Waiting for US business hours")
        background_tasks.add_task(send_when_business_hours, payload)
        return SendEmailResponse(
            ok=True,
            queued=True,
            message=(
                "Waiting for US business hours. Email will send at "
                f"{opens.strftime('%A %I:%M %p %Z')}."
            ),
        )

    try:
        result = send_email(settings, payload)
    except smtplib.SMTPAuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Gmail login failed. Check SENDER_EMAIL and SENDER_APP_PASSWORD.",
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise HTTPException(status_code=400, detail=f"Recipient refused: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=502, detail=f"SMTP error: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Network error: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    mark_sent(db, str(payload.recipient_email))
    return result


# --- compatibility aliases for the previous unprefixed routes ---


@app.post("/scrape-leads", response_model=ScrapeStartedResponse)
def scrape_leads_legacy(
    payload: ScrapeLeadsRequest,
    background_tasks: BackgroundTasks,
) -> ScrapeStartedResponse:
    niche = payload.search_query.strip()
    location = payload.location.strip() or "USA"
    query = niche if location.lower() in {"usa", "us"} else f"{niche} in {location}"
    return _start_scrape(query, "yellowpages", background_tasks)


@app.get("/leads", response_model=list[SavedLead])
def fetch_leads_legacy(db: Session = Depends(get_db)) -> list[SavedLead]:
    return list_leads(db)


@app.post("/start-campaign", response_model=StartCampaignResponse)
def start_campaign_legacy(
    payload: StartCampaignRequest,
    background_tasks: BackgroundTasks,
) -> StartCampaignResponse:
    return start_campaign(payload, background_tasks)


@app.post("/send-email", response_model=SendEmailResponse)
async def deliver_email_legacy(
    payload: SendEmailRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SendEmailResponse:
    return await deliver_email(payload, background_tasks, db)


@app.api_route("/track/{lead_id}.gif", methods=["GET", "POST"])
def track_open(lead_id: int, db: Session = Depends(get_db)) -> Response:
    """1x1 GIF pixel. Email clients load this with GET when the message is opened."""
    mark_opened(db, lead_id)
    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )
