"""
Softenix Solution — Google Places lead extractor.

Uses the official Google Places API (not Maps HTML scraping) to find
local businesses, then visits each business website with polite delays
to pick up a publicly listed email.

Usage:
    python extract_leads.py "Dental clinics in Karachi"
    python extract_leads.py "Real Estate agents in Texas" --max-results 40
    python extract_leads.py "Plumbers in Austin" --skip-email
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import googlemaps
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from email_validator import EmailNotValidError, validate_email
import os

ROOT = Path(__file__).resolve().parent
LEADS_CSV = ROOT / "leads.csv"
CSV_FIELDS = ["BusinessName", "Email", "Website", "GoogleReviewScore", "Observation"]

USER_AGENT = (
    "Mozilla/5.0 (compatible; SoftenixLeadResearch/1.0; "
    "+https://softenixsolution.com/bot)"
)
REQUEST_TIMEOUT = 12
HOMEPAGE_DELAY = (2.0, 4.0)
CONTACT_PAGE_DELAY = (1.5, 3.0)
PLACES_PAGE_DELAY = 2.2

CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/get-in-touch",
)

SKIP_HOST_FRAGMENTS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "maps.google.",
    "goo.gl",
    "bit.ly",
    "wa.me",
    "yelp.com",
    "tripadvisor.",
)

JUNK_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "privacy",
    "legal",
    "abuse",
    "postmaster",
    "webmaster",
    "mailer-daemon",
    "sentry",
    "wix",
}

JUNK_DOMAINS = {
    "sentry.io",
    "wixpress.com",
    "cloudflare.com",
    "example.com",
    "example.org",
    "schema.org",
    "googleapis.com",
    "w3.org",
    "github.com",
    "gravatar.com",
}

EMAIL_RE = re.compile(
    r"""
    (?<![\w.+-])
    ([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24})
    (?![\w.+-])
    """,
    re.IGNORECASE | re.VERBOSE,
)

OBFUSCATED_RE = re.compile(
    r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+)\s*([A-Z]{2,24})",
    re.IGNORECASE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract_leads")


@dataclass
class PlaceLead:
    business_name: str
    website: str
    rating: str
    review_count: int
    email: str
    observation: str


def load_api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise SystemExit("Missing GOOGLE_MAPS_API_KEY in .env")
    return key


def polite_sleep(bounds: tuple[float, float]) -> None:
    time.sleep(random.uniform(*bounds))


def search_places(client: googlemaps.Client, query: str, max_results: int) -> list[dict]:
    """Text Search + pagination. Google returns up to 20 per page, 60 total."""
    collected: list[dict] = []
    response = client.places(query=query, language="en", region="us")
    status = response.get("status")
    if status not in {"OK", "ZERO_RESULTS"}:
        raise SystemExit(f"Places Text Search failed: {status} {response.get('error_message', '')}")

    while True:
        collected.extend(response.get("results") or [])
        log.info("Places page loaded. Running total: %s", len(collected))
        if len(collected) >= max_results:
            break
        token = response.get("next_page_token")
        if not token:
            break
        # Google requires a short wait before next_page_token becomes valid.
        time.sleep(PLACES_PAGE_DELAY)
        response = client.places(query=query, page_token=token, language="en", region="us")
        if response.get("status") not in {"OK", "ZERO_RESULTS"}:
            log.warning("Stopped pagination: %s", response.get("status"))
            break

    # De-dupe by place_id while preserving order.
    unique: list[dict] = []
    seen: set[str] = set()
    for item in collected:
        place_id = item.get("place_id")
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        unique.append(item)
        if len(unique) >= max_results:
            break
    return unique


def place_details(client: googlemaps.Client, place_id: str) -> dict:
    result = client.place(
        place_id=place_id,
        fields=[
            "name",
            "website",
            "rating",
            "user_ratings_total",
            "formatted_address",
            "business_status",
            "url",
        ],
    )
    if result.get("status") != "OK":
        return {}
    return result.get("result") or {}


def is_business_site(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return not any(fragment in host for fragment in SKIP_HOST_FRAGMENTS)


def decode_cloudflare_emails(html: str) -> list[str]:
    """Decode Cloudflare email protection (data-cfemail)."""
    found: list[str] = []
    for encoded in re.findall(r"data-cfemail=\"([0-9a-fA-F]+)\"", html):
        try:
            key = int(encoded[:2], 16)
            chars = [chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2)]
            found.append("".join(chars))
        except ValueError:
            continue
    return found


def extract_emails_from_text(text: str) -> list[str]:
    candidates = EMAIL_RE.findall(text)
    for local, domain, tld in OBFUSCATED_RE.findall(text):
        candidates.append(f"{local}@{domain}.{tld}")
    return candidates


def clean_email(raw: str) -> str | None:
    address = raw.strip().rstrip(".,;:)>\"'")
    address = address.replace("%20", "").replace("mailto:", "")
    if address.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
        return None
    try:
        valid = validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return None
    normalized = valid.normalized.lower()
    local, domain = normalized.split("@", 1)
    if local in JUNK_LOCAL_PARTS or domain in JUNK_DOMAINS:
        return None
    if len(local) > 64:
        return None
    return normalized


def pick_best_email(candidates: list[str], website: str) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        email = clean_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        cleaned.append(email)
    if not cleaned:
        return ""

    site_host = urlparse(website).netloc.lower().removeprefix("www.")

    def score(email: str) -> tuple[int, int]:
        local, domain = email.split("@", 1)
        same_domain = int(site_host.endswith(domain) or domain.endswith(site_host))
        preferred_local = int(local in {"info", "hello", "contact", "office", "admin", "inquiries"})
        return (same_domain, preferred_local)

    cleaned.sort(key=score, reverse=True)
    return cleaned[0]


def fetch_html(session: requests.Session, url: str) -> str:
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            allow_redirects=True,
        )
        if response.status_code >= 400:
            log.debug("HTTP %s for %s", response.status_code, url)
            return ""
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower() and not url.lower().endswith((".html", ".htm", "/")):
            return ""
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.RequestException as exc:
        log.warning("Could not fetch %s: %s", url, exc)
        return ""


def emails_from_html(html: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    found: list[str] = []
    found.extend(decode_cloudflare_emails(html))
    for link in soup.select("a[href^=mailto]"):
        href = link.get("href", "")
        found.extend(EMAIL_RE.findall(href.split("?", 1)[0]))
    found.extend(extract_emails_from_text(soup.get_text(" ", strip=True)))
    found.extend(extract_emails_from_text(html))
    return found


def discover_contact_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    keywords = ("contact", "about", "get-in-touch", "reach-us")
    for link in soup.select("a[href]"):
        href = (link.get("href") or "").strip()
        label = (link.get_text(" ", strip=True) or "").lower()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        path = urlparse(absolute).path.lower()
        if any(word in path or word in label for word in keywords):
            if urlparse(absolute).netloc == urlparse(base_url).netloc:
                urls.append(absolute.split("#", 1)[0])

    for path in CONTACT_PATHS:
        urls.append(urljoin(base_url, path))

    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
        if len(unique) >= 4:
            break
    return unique


def scrape_website_email(session: requests.Session, website: str) -> str:
    """Visit the business site and a couple of contact-style pages; return the best email."""
    html = fetch_html(session, website)
    candidates = emails_from_html(html)
    best = pick_best_email(candidates, website)
    if best:
        return best

    if not html:
        return ""

    for contact_url in discover_contact_urls(html, website):
        if contact_url.rstrip("/") == website.rstrip("/"):
            continue
        polite_sleep(CONTACT_PAGE_DELAY)
        page_html = fetch_html(session, contact_url)
        candidates.extend(emails_from_html(page_html))
        best = pick_best_email(candidates, website)
        if best:
            return best
    return ""


def build_observation(website: str, email: str, rating: str, review_count: int) -> str:
    if not website:
        return "No website listed on Google Maps"
    if not is_business_site(website):
        return "Google listing points to a social profile, not a business website"
    if not email:
        return "Website listed, but no public contact email was found"
    if review_count < 10:
        return f"Has a site, but only {review_count} Google review(s)"
    if rating and float(rating) >= 4.5:
        return f"Strong Google rating ({rating}) with {review_count} reviews; worth a closer look at the site"
    return f"Google rating {rating or 'n/a'} from {review_count} reviews"


def read_existing_leads(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_leads(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def merge_leads(existing: list[dict[str, str]], incoming: list[PlaceLead], replace: bool) -> list[dict[str, str]]:
    if replace:
        existing = []

    seen_names = {row.get("BusinessName", "").strip().lower() for row in existing}
    seen_emails = {row.get("Email", "").strip().lower() for row in existing if row.get("Email")}
    merged = list(existing)

    for lead in incoming:
        name_key = lead.business_name.strip().lower()
        email_key = lead.email.strip().lower()
        if name_key in seen_names:
            continue
        if email_key and email_key in seen_emails:
            continue
        merged.append(
            {
                "BusinessName": lead.business_name,
                "Email": lead.email,
                "Website": lead.website,
                "GoogleReviewScore": lead.rating,
                "Observation": lead.observation,
            }
        )
        seen_names.add(name_key)
        if email_key:
            seen_emails.add(email_key)
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract local business leads via Google Places API")
    parser.add_argument("query", help='Search query, e.g. "Dental clinics in Karachi"')
    parser.add_argument("--max-results", type=int, default=20, help="Max places to keep (default 20, Google caps at 60)")
    parser.add_argument("--output", default=str(LEADS_CSV), help="CSV path (default: leads.csv)")
    parser.add_argument("--skip-email", action="store_true", help="Do not visit websites to find emails")
    parser.add_argument("--replace", action="store_true", help="Overwrite leads.csv instead of merging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    max_results = max(1, min(args.max_results, 60))
    client = googlemaps.Client(key=load_api_key())

    log.info("Searching Places for: %s (max %s)", args.query, max_results)
    places = search_places(client, args.query, max_results)
    if not places:
        log.warning("No places returned.")
        return 0

    session = requests.Session()
    leads: list[PlaceLead] = []

    for index, summary in enumerate(places, start=1):
        details = place_details(client, summary["place_id"])
        name = (details.get("name") or summary.get("name") or "").strip()
        if not name:
            continue

        website = (details.get("website") or "").strip()
        rating = details.get("rating", summary.get("rating"))
        rating_str = f"{rating:.1f}" if isinstance(rating, (int, float)) else str(rating or "").strip()
        review_count = int(details.get("user_ratings_total") or summary.get("user_ratings_total") or 0)

        email = ""
        if website and is_business_site(website) and not args.skip_email:
            log.info("(%s/%s) %s — checking %s", index, len(places), name, website)
            polite_sleep(HOMEPAGE_DELAY)
            email = scrape_website_email(session, website)
            if email:
                log.info("Found email for %s: %s", name, email)
            else:
                log.info("No public email found for %s", name)
        elif not website:
            log.info("(%s/%s) %s — no website on Google listing", index, len(places), name)
        else:
            log.info("(%s/%s) %s — skipped website scrape", index, len(places), name)

        observation = build_observation(website, email, rating_str, review_count)
        leads.append(
            PlaceLead(
                business_name=name,
                website=website if is_business_site(website) else "",
                rating=rating_str,
                review_count=review_count,
                email=email,
                observation=observation,
            )
        )

    output_path = Path(args.output)
    existing = read_existing_leads(output_path)
    merged = merge_leads(existing, leads, replace=args.replace)
    write_leads(output_path, merged)

    with_email = sum(1 for lead in leads if lead.email)
    log.info(
        "Wrote %s row(s) to %s (%s new this run, %s with email).",
        len(merged),
        output_path.name,
        len(leads),
        with_email,
    )
    log.info("Leads without email still need a manual contact before outreach.py can send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
