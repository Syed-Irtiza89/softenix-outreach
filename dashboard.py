"""
Softenix Solution — Streamlit outreach dashboard.

Run:
    streamlit run dashboard.py
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone

import streamlit as st

from outreach import (
    TABLE,
    Lead,
    build_message,
    fetch_pending_leads,
    generate_email,
    is_valid_email,
    load_settings,
    make_openai_client,
    mark_sent,
    send_via_gmail,
    supabase_client,
)

PAGE_TITLE = "Softenix Outreach"
LEAD_COLUMNS = [
    "business_name",
    "email",
    "website",
    "google_review_score",
    "observation",
    "status",
    "sent_at",
    "followup_sent_at",
    "subject",
    "error_log",
    "created_at",
]


@st.cache_resource
def get_runtime():
    settings = load_settings(dry_run_override=False)
    return settings, supabase_client(settings), make_openai_client(settings)


def start_of_local_day_utc() -> str:
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_midnight.astimezone(timezone.utc).isoformat()


def count_rows(db, **filters) -> int:
    query = db.table(TABLE).select("id", count="exact")
    for key, value in filters.items():
        if key.endswith("__gte"):
            query = query.gte(key[:-5], value)
        else:
            query = query.eq(key, value)
    response = query.limit(1).execute()
    return int(response.count or 0)


def load_leads_table(db, status_filter: str) -> list[dict]:
    query = (
        db.table(TABLE)
        .select(",".join(LEAD_COLUMNS))
        .order("created_at", desc=True)
        .limit(500)
    )
    if status_filter != "All":
        query = query.eq("status", status_filter)
    return query.execute().data or []


def lead_to_state(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "business_name": lead.business_name,
        "email": lead.email,
        "website": lead.website,
        "google_review_score": lead.google_review_score,
        "observation": lead.observation,
    }


def state_to_lead(data: dict) -> Lead:
    return Lead(
        id=data["id"],
        business_name=data["business_name"],
        email=data["email"],
        website=data.get("website") or "",
        google_review_score=data.get("google_review_score") or "",
        observation=data.get("observation") or "",
    )


def generate_and_store_draft(lead: Lead, settings, openai_client) -> None:
    subject, body = generate_email(openai_client, settings.openai_model, lead, settings)
    st.session_state.review_lead = lead_to_state(lead)
    st.session_state.draft_subject = subject
    st.session_state.draft_body = body


def load_next_pending(settings, db, openai_client, exclude_id: str | None = None) -> str:
    leads = fetch_pending_leads(db, 5)
    lead = next((item for item in leads if item.id != exclude_id), None)
    if lead is None:
        st.session_state.review_lead = None
        st.session_state.draft_subject = ""
        st.session_state.draft_body = ""
        return "empty"
    generate_and_store_draft(lead, settings, openai_client)
    return "loaded"


def render_metrics(db) -> None:
    total = count_rows(db)
    sent_today = count_rows(db, sent_at__gte=start_of_local_day_utc())
    interested = count_rows(db, status="Interested")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Leads", total)
    col2.metric("Emails Sent Today", sent_today)
    col3.metric("Interested Replies", interested)


def render_leads_table(db) -> None:
    st.subheader("Leads")
    status_options = [
        "All",
        "Pending",
        "Sent",
        "Follow-up 1 Sent",
        "Interested",
        "Not Interested",
        "Meeting Requested",
        "Out of Office",
        "Failed",
        "Spam",
    ]
    left, right = st.columns([2, 1])
    with left:
        status_filter = st.selectbox("Status filter", status_options, index=0)
    with right:
        st.write("")
        st.write("")
        refresh = st.button("Refresh table", use_container_width=True)

    if refresh:
        st.rerun()

    rows = load_leads_table(db, status_filter)
    st.caption(f"{len(rows)} row(s) shown (newest 500).")
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_review(settings, db, openai_client) -> None:
    st.subheader("Review & Send")
    st.caption("One pending lead at a time. Edit the draft, then approve before anything is sent.")
    flash = st.session_state.pop("flash", "")
    if flash:
        st.success(flash)

    if "review_lead" not in st.session_state:
        with st.spinner("Loading the next pending lead and drafting…"):
            try:
                load_next_pending(settings, db, openai_client)
            except Exception as exc:
                st.error(f"Could not load a draft: {exc}")
                return

    lead_data = st.session_state.get("review_lead")
    if not lead_data:
        st.info("No pending leads. Add rows with status Pending in Supabase.")
        if st.button("Check again"):
            with st.spinner("Looking for pending leads…"):
                load_next_pending(settings, db, openai_client)
            st.rerun()
        return

    lead = state_to_lead(lead_data)
    meta1, meta2, meta3 = st.columns(3)
    meta1.markdown(f"**Business**  \n{lead.business_name}")
    meta2.markdown(f"**Email**  \n`{lead.email}`")
    meta3.markdown(f"**Google rating**  \n{lead.google_review_score or '—'}")
    if lead.website:
        st.markdown(f"**Website:** {lead.website}")
    if lead.observation:
        st.markdown(f"**Observation:** {lead.observation}")

    st.text_input("Subject", key="draft_subject")
    st.text_area("Email body", key="draft_body", height=280)

    regen, skip, approve = st.columns(3)
    with regen:
        regenerate = st.button("Regenerate draft", use_container_width=True)
    with skip:
        skip_lead = st.button("Skip (keep Pending)", use_container_width=True)
    with approve:
        approve_send = st.button("Approve & Send", type="primary", use_container_width=True)

    if regenerate:
        with st.spinner("Regenerating draft…"):
            try:
                generate_and_store_draft(lead, settings, openai_client)
            except Exception as exc:
                st.error(f"AI draft failed: {exc}")
                return
        st.rerun()

    if skip_lead:
        with st.spinner("Loading the next pending lead…"):
            result = load_next_pending(settings, db, openai_client, exclude_id=lead.id)
        if result == "empty":
            st.warning("No other pending leads.")
        st.rerun()

    if approve_send:
        subject = (st.session_state.get("draft_subject") or "").strip()
        body = (st.session_state.get("draft_body") or "").strip()
        if not is_valid_email(lead.email):
            st.error(f"Invalid recipient: {lead.email or '(empty)'}")
            return
        if not subject or not body:
            st.error("Subject and body are required.")
            return

        message = build_message(settings, lead, subject, body)
        try:
            with st.spinner(f"Sending to {lead.email}…"):
                send_via_gmail(settings, message)
                mark_sent(db, lead, subject, message["Message-ID"] or "")
        except smtplib.SMTPAuthenticationError:
            st.error("Gmail login failed. Check SENDER_EMAIL and SENDER_APP_PASSWORD.")
            return
        except (smtplib.SMTPException, OSError) as exc:
            st.error(f"Send failed. Lead stays Pending. {exc}")
            return
        except Exception as exc:
            st.error(f"Supabase update failed after send. Check the row for {lead.email}. {exc}")
            return

        st.session_state.flash = f"Sent to {lead.business_name} <{lead.email}>."
        with st.spinner("Loading the next pending lead…"):
            load_next_pending(settings, db, openai_client, exclude_id=lead.id)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon="✉️", layout="wide")
    st.title("Softenix Solution")
    st.caption("Review AI drafts, send one at a time, and watch replies land in Supabase.")

    try:
        settings, db, openai_client = get_runtime()
    except SystemExit as exc:
        st.error(str(exc) or "Missing .env values.")
        st.stop()
    except Exception as exc:
        st.error(f"Could not start: {exc}")
        st.stop()

    render_metrics(db)
    st.divider()
    render_leads_table(db)
    st.divider()
    render_review(settings, db, openai_client)


if __name__ == "__main__":
    main()
