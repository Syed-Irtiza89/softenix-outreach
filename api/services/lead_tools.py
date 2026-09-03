from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from api.config import get_settings
from api.credits import CreditLimitError, consume_credits, remaining_daily
from api.store import upsert_lead
from api.validate import is_deliverable_email

log = logging.getLogger("api.lead_tools")

APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
HUNTER_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
BUILTWITH_URL = "https://api.builtwith.com/v20/api.json"

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)


def _has_real_key(value: str) -> bool:
    key = (value or "").strip()
    if not key:
        return False
    lowered = key.lower()
    return not any(token in lowered for token in ("your-", "changeme", "xxxx", "placeholder"))


def _apollo_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": api_key,
    }


def _host_from_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower().removeprefix("www.")
    return host.split(":")[0]


def _website_from_org(org: dict[str, Any] | None) -> str:
    if not isinstance(org, dict):
        return ""
    for key in ("website_url", "primary_domain", "domain"):
        value = str(org.get(key) or "").strip()
        if value:
            if "://" not in value:
                return f"https://{value.lstrip('/')}"
            return value
    return ""


def _apollo_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        err = str(payload.get("error") or payload.get("message") or "").strip()
    except Exception:
        err = (response.text or "").strip()[:240]
    if response.status_code == 403 and "free plan" in err.lower():
        return (
            "Apollo API key is valid, but people-search is not included on the Free plan. "
            "Email credits in the Apollo website do not unlock the REST API. "
            "A paid Apollo plan is required for search_apollo_leads."
        )
    if response.status_code == 401:
        return "Apollo rejected the API key. Create a new key at Settings > Integrations > API and paste APOLLO_API_KEY in .env."
    return err or f"Apollo HTTP {response.status_code}"


def _person_email(person: dict[str, Any]) -> str:
    for key in ("email", "email_status"):
        value = person.get(key)
        if isinstance(value, str) and "@" in value and value.lower() not in {"unavailable", "unavailable_email"}:
            checked = is_deliverable_email(value)
            if checked:
                return checked
    for item in person.get("email_addresses") or []:
        if not isinstance(item, dict):
            continue
        checked = is_deliverable_email(str(item.get("email") or item.get("value") or ""))
        if checked:
            return checked
    return ""


def search_apollo_leads(db: Session, niche: str, location: str, limit: int = 5) -> dict[str, Any]:
    settings = get_settings()
    if not _has_real_key(settings.apollo_api_key):
        raise RuntimeError(
            "APOLLO_API_KEY is missing. Generate a free Apollo API key after the domain is ready, then add it to .env."
        )

    wanted = max(1, min(int(limit or 5), 25))
    available = remaining_daily(db, "apollo")
    if available <= 0:
        raise CreditLimitError("Apollo daily/monthly credit limit reached.")
    wanted = min(wanted, available)

    titles = [
        "Owner",
        "Founder",
        "President",
        "CEO",
        "Manager",
        niche.strip().title(),
    ]
    payload = {
        "q_keywords": niche.strip(),
        "person_titles": titles,
        "person_locations": [location.strip()],
        "organization_locations": [location.strip()],
        "per_page": wanted,
        "page": 1,
    }

    people: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0) as client:
        try:
            response = client.post(
                APOLLO_SEARCH_URL,
                headers=_apollo_headers(settings.apollo_api_key),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Apollo search failed: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(_apollo_error_message(response))
        body = response.json()
        people = list(body.get("people") or body.get("contacts") or [])
        log.info("Apollo search returned %s people for %s in %s", len(people), niche, location)

    saved_rows: list[dict[str, Any]] = []
    skipped = 0
    credits_used = 0

    with httpx.Client(timeout=30.0) as client:
        for person in people[:wanted]:
            if not isinstance(person, dict):
                skipped += 1
                continue
            enriched = person
            person_id = str(person.get("id") or person.get("person_id") or "")
            if person_id and not _person_email(person):
                try:
                    match = client.post(
                        APOLLO_MATCH_URL,
                        headers=_apollo_headers(settings.apollo_api_key),
                        json={"id": person_id, "reveal_personal_emails": False},
                    )
                    if match.status_code < 400:
                        matched = match.json()
                        if isinstance(matched.get("person"), dict):
                            enriched = matched["person"]
                        credits_used += 1
                    else:
                        log.warning("Apollo match HTTP %s for %s", match.status_code, person_id)
                except httpx.HTTPError as exc:
                    log.warning("Apollo match failed for %s: %s", person_id, exc)

            email = _person_email(enriched)
            if not email:
                skipped += 1
                continue

            org = enriched.get("organization") if isinstance(enriched.get("organization"), dict) else {}
            company = str(
                (org or {}).get("name")
                or enriched.get("organization_name")
                or person.get("organization_name")
                or ""
            ).strip()
            first = str(enriched.get("first_name") or "").strip()
            last = str(enriched.get("last_name") or "").strip()
            contact = " ".join(part for part in (first, last) if part) or str(enriched.get("name") or "").strip()
            title = str(enriched.get("title") or "").strip()
            website = _website_from_org(org if isinstance(org, dict) else None)
            inserted = upsert_lead(
                db,
                business_name=company or contact or email,
                email=email,
                website=website,
                rating="",
                contact_name=contact,
                job_title=title,
                source="apollo",
            )
            if inserted:
                saved_rows.append(
                    {
                        "contact_name": contact,
                        "job_title": title,
                        "email": email,
                        "business_name": company or contact,
                        "website": website,
                        "source": "apollo",
                    }
                )
            else:
                skipped += 1

    if credits_used == 0 and saved_rows:
        credits_used = len(saved_rows)
    credits = consume_credits(db, "apollo", credits_used)
    return {
        "tool": "search_apollo_leads",
        "saved": len(saved_rows),
        "skipped": skipped,
        "leads": saved_rows,
        "credits": credits,
        "message": (
            f"Apollo returned {len(saved_rows)} new lead(s) for {niche} in {location}."
            if saved_rows
            else f"Apollo found no new emails for {niche} in {location}."
        ),
    }


def find_domain_emails(db: Session, domain: str) -> dict[str, Any]:
    settings = get_settings()
    if not _has_real_key(settings.hunter_api_key):
        raise RuntimeError(
            "HUNTER_API_KEY is missing. Generate a free Hunter API key, then add it to .env."
        )
    host = _host_from_url(domain) or domain.strip().lower().removeprefix("www.")
    if not host or not _DOMAIN_RE.match(host):
        raise RuntimeError(f"Invalid domain for Hunter search: {domain!r}")
    if remaining_daily(db, "hunter") <= 0:
        raise CreditLimitError("Hunter daily/monthly credit limit reached (25/month, ~1/day on the free plan).")

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                HUNTER_SEARCH_URL,
                params={"domain": host, "api_key": settings.hunter_api_key},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Hunter request failed: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"Hunter HTTP {response.status_code}: {response.text[:240]}")

    body = response.json()
    data = body.get("data") if isinstance(body, dict) else {}
    company = str((data or {}).get("organization") or host)
    website = f"https://{host}"
    saved_rows: list[dict[str, Any]] = []
    skipped = 0
    for item in (data or {}).get("emails") or []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        email = is_deliverable_email(str(item.get("value") or ""))
        if not email:
            skipped += 1
            continue
        first = str(item.get("first_name") or "").strip()
        last = str(item.get("last_name") or "").strip()
        contact = " ".join(part for part in (first, last) if part)
        title = str(item.get("position") or "").strip()
        inserted = upsert_lead(
            db,
            business_name=company,
            email=email,
            website=website,
            rating="",
            contact_name=contact,
            job_title=title,
            source="hunter",
        )
        if inserted:
            saved_rows.append(
                {
                    "contact_name": contact,
                    "job_title": title,
                    "email": email,
                    "business_name": company,
                    "website": website,
                    "source": "hunter",
                }
            )
        else:
            skipped += 1

    credits = consume_credits(db, "hunter", 1)
    return {
        "tool": "find_domain_emails",
        "saved": len(saved_rows),
        "skipped": skipped,
        "leads": saved_rows,
        "credits": credits,
        "message": (
            f"Hunter found {len(saved_rows)} new email(s) on {host}."
            if saved_rows
            else f"Hunter found no new emails on {host}."
        ),
    }


def check_tech_stack(db: Session, domain: str) -> dict[str, Any]:
    settings = get_settings()
    if not _has_real_key(settings.builtwith_api_key):
        raise RuntimeError(
            "BUILTWITH_API_KEY is missing. Generate a free BuiltWith API key, then add it to .env."
        )
    host = _host_from_url(domain) or domain.strip().lower().removeprefix("www.")
    if not host or not _DOMAIN_RE.match(host):
        raise RuntimeError(f"Invalid domain for BuiltWith lookup: {domain!r}")
    if remaining_daily(db, "builtwith") <= 0:
        raise CreditLimitError("BuiltWith daily/monthly credit limit reached (100/month, ~3/day on the free plan).")

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(BUILTWITH_URL, params={"KEY": settings.builtwith_api_key, "LOOKUP": host})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"BuiltWith request failed: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"BuiltWith HTTP {response.status_code}: {response.text[:240]}")

    body = response.json()
    names: list[str] = []
    results = body.get("Results") if isinstance(body, dict) else None
    if isinstance(results, list):
        for result in results:
            paths = (result or {}).get("Result", {}).get("Paths") if isinstance(result, dict) else None
            if not isinstance(paths, list):
                continue
            for path in paths:
                techs = (path or {}).get("Technologies") if isinstance(path, dict) else None
                if not isinstance(techs, list):
                    continue
                for tech in techs:
                    name = str((tech or {}).get("Name") or "").strip()
                    if name and name not in names:
                        names.append(name)
    credits = consume_credits(db, "builtwith", 1)
    return {
        "tool": "check_tech_stack",
        "saved": 0,
        "skipped": 0,
        "leads": [],
        "tech_stack": names[:40],
        "credits": credits,
        "message": (
            f"BuiltWith found {len(names)} technologies on {host}."
            if names
            else f"BuiltWith returned no technologies for {host}."
        ),
    }
