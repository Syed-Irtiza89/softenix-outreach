"""
Lead discovery: Yellow Pages, Google Places, or DuckDuckGo search, then public website email extraction.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import googlemaps
import httpx
import requests
from bs4 import BeautifulSoup, Tag
from duckduckgo_search import DDGS
from email_validator import EmailNotValidError, validate_email

from api.config import get_settings
from api.database import SessionLocal
from api.store import upsert_lead
from api.validate import is_deliverable_email, pick_outreach_email

log = logging.getLogger("api.scraper")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
YP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.yellowpages.com/",
    "Cache-Control": "no-cache",
}
YP_SEARCH_URL = "https://www.yellowpages.com/search"
YP_TIMEOUT = 20.0
STAR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
REQUEST_TIMEOUT = 12
HOMEPAGE_DELAY = (2.0, 4.0)
CONTACT_PAGE_DELAY = (1.5, 3.0)
PLACES_PAGE_DELAY = 2.2
MAX_RESULTS_PER_CITY = 8
DDG_TARGET_PER_CITY = 50
DDG_PAGE_SIZE = 25
TARGET_VALID_LEADS = 20
VALID_SCRAPE_SOURCES = ("auto", "yellowpages", "google_maps", "duckduckgo", "no_website")
AUTO_SOURCE_CHAIN = ("duckduckgo", "google_maps", "yellowpages", "no_website")

CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/about", "/about-us", "/get-in-touch")

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

# Directories and aggregators — never treat these as the business website.
excluded_domains = [
    # General Aggregators
    "yelp.com",
    "yellowpages.com",
    "yp.com",
    "facebook.com",
    "linkedin.com",
    "wikipedia.org",
    "angie.com",
    "angi.com",
    "angieslist.com",
    "homeadvisor.com",
    "mapquest.com",
    # Restaurant Aggregators
    "opentable.com",
    "grubhub.com",
    "ubereats.com",
    "doordash.com",
    "tripadvisor.com",
    "seamles.com",
    "seamless.com",
    # Healthcare Aggregators
    "zocdoc.com",
    "healthgrades.com",
    "vitals.com",
    "webmd.com",
    "caredash.com",
    # General Directory/Review Sites
    "bbb.org",
    "chamberofcommerce.com",
    "groupon.com",
    # Other directories that hide the real business site
    "superpages.com",
    "whitepages.com",
    "thumbtack.com",
    "houzz.com",
    "nextdoor.com",
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "apple.com",
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "yahoo.com",
    "manta.com",
    "dnb.com",
    "citysearch.com",
    "foursquare.com",
    "expertise.com",
    "homeservicespros.com",
    "porch.com",
    "bark.com",
    "trustpilot.com",
    "merchantcircle.com",
    "yellowbook.com",
    "kudzu.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
]

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

TOP_US_GROWTH_CITIES = (
    "Austin, TX",
    "Houston, TX",
    "Dallas, TX",
    "San Antonio, TX",
    "Denver, CO",
    "Phoenix, AZ",
    "Miami, FL",
    "Orlando, FL",
    "Tampa, FL",
    "Atlanta, GA",
    "Charlotte, NC",
    "Raleigh, NC",
    "Nashville, TN",
    "Richmond, VA",
    "Columbus, OH",
    "Indianapolis, IN",
    "Kansas City, MO",
    "Las Vegas, NV",
    "Salt Lake City, UT",
    "Portland, OR",
)

NEARBY_CITIES = {
    "austin": ["Austin, TX", "Round Rock, TX", "Cedar Park, TX", "Pflugerville, TX", "Georgetown, TX", "Kyle, TX"],
    "richmond": ["Richmond, VA", "Henrico, VA", "Chesterfield, VA", "Midlothian, VA", "Mechanicsville, VA"],
    "denver": ["Denver, CO", "Aurora, CO", "Lakewood, CO", "Thornton, CO", "Arvada, CO"],
    "miami": ["Miami, FL", "Miami Beach, FL", "Hialeah, FL", "Coral Gables, FL", "Fort Lauderdale, FL"],
    "phoenix": ["Phoenix, AZ", "Mesa, AZ", "Scottsdale, AZ", "Tempe, AZ", "Glendale, AZ", "Chandler, AZ"],
    "dallas": ["Dallas, TX", "Fort Worth, TX", "Arlington, TX", "Plano, TX", "Irving, TX"],
    "atlanta": ["Atlanta, GA", "Marietta, GA", "Decatur, GA", "Sandy Springs, GA"],
    "nashville": ["Nashville, TN", "Franklin, TN", "Murfreesboro, TN"],
    "charlotte": ["Charlotte, NC", "Concord, NC", "Gastonia, NC"],
    "tampa": ["Tampa, FL", "St. Petersburg, FL", "Clearwater, FL"],
    "raleigh": ["Raleigh, NC", "Durham, NC", "Cary, NC"],
}

US_LOCATION_ALIASES = {
    "us",
    "usa",
    "u.s.",
    "u.s.a.",
    "united states",
    "united states of america",
}

_scrape_running = False
_last_scrape: dict[str, str | int] = {
    "source": "",
    "query": "",
    "saved": 0,
    "skipped": 0,
    "error": "",
}


def scrape_is_running() -> bool:
    return _scrape_running


def last_scrape_status() -> dict[str, str | int]:
    return dict(_last_scrape)


def _set_last_scrape(**kwargs: str | int) -> None:
    _last_scrape.update(kwargs)


def begin_scrape() -> bool:
    global _scrape_running
    if _scrape_running:
        return False
    _scrape_running = True
    return True


def _end_scrape() -> None:
    global _scrape_running
    _scrape_running = False


def _rotate_cities(cities: list[str]) -> list[str]:
    if len(cities) < 2:
        return list(cities)
    shift = datetime.now(timezone.utc).timetuple().tm_yday % len(cities)
    return cities[shift:] + cities[:shift]


def parse_query(query: str) -> tuple[str, str]:
    trimmed = query.strip()
    match = re.match(r"^(.*)\s+in\s+(.+)$", trimmed, re.IGNORECASE)
    if match and match.group(1).strip() and match.group(2).strip():
        return match.group(1).strip(), match.group(2).strip()
    return trimmed, "USA"


def expand_us_cities(location: str) -> list[str]:
    place = (location or "").strip() or "USA"
    if place.lower().rstrip(".") in US_LOCATION_ALIASES:
        return _rotate_cities(list(TOP_US_GROWTH_CITIES))
    return [place]


def _search_locations(location: str) -> list[str]:
    """User's city first, then nearby towns, then other US cities until we can hit 20 leads."""
    place = (location or "").strip() or "USA"
    if place.lower().rstrip(".") in US_LOCATION_ALIASES:
        return _rotate_cities(list(TOP_US_GROWTH_CITIES))
    blob = place.lower()
    queue: list[str] = []
    for key, cities in NEARBY_CITIES.items():
        if key in blob:
            queue.extend(cities)
            break
    if place not in queue:
        queue.insert(0, place)
    for city in TOP_US_GROWTH_CITIES:
        if not any(city.lower() == item.lower() for item in queue):
            queue.append(city)
    return list(dict.fromkeys(queue))


def _niche_variants(niche: str) -> list[str]:
    blob = (niche or "").strip().lower()
    if "plumb" in blob:
        return ["plumbers", "plumbing company", "drain cleaning", "emergency plumber", "water heater repair"]
    if "paint" in blob:
        return ["house painters", "painting contractors", "interior painters", "exterior painters"]
    if "hvac" in blob or "air condition" in blob:
        return ["HVAC", "air conditioning repair", "AC installation", "heating and cooling"]
    if "electric" in blob:
        return ["electricians", "electrical contractor", "emergency electrician"]
    if "roof" in blob:
        return ["roofers", "roofing company", "roof repair"]
    if "landscape" in blob or "lawn" in blob:
        return ["landscapers", "lawn care", "landscaping company"]
    if "contractor" in blob:
        return ["general contractors", "home remodeling", "construction company"]
    if "auto" in blob or "mechanic" in blob:
        return ["auto repair", "car mechanic", "auto shop"]
    if "clean" in blob:
        return ["cleaning service", "house cleaning", "janitorial"]
    if "pest" in blob:
        return ["pest control", "exterminator", "termite control"]
    if "dentist" in blob or "dental" in blob:
        return ["dentists", "dental clinic", "family dentist", "orthodontist"]
    if "restaurant" in blob:
        return [niche, "restaurants", "local restaurant"]
    return [niche.strip() or "local businesses"]


def is_us_address(address: str) -> bool:
    text = address.lower().strip()
    if not text:
        return True
    suffixes = (
        "united states",
        "united states of america",
        "usa",
        "u.s.a.",
        "u.s.a",
        "u.s.",
    )
    if any(text.endswith(suffix) or f", {suffix}" in text for suffix in suffixes):
        return True
    return bool(re.search(r",\s*us$", text))


def is_business_site(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return not any(fragment in host for fragment in SKIP_HOST_FRAGMENTS)


def _host_matches_excluded(host: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    if not host:
        return True
    for domain in excluded_domains:
        domain = domain.lower().removeprefix("www.")
        if host == domain or host.endswith("." + domain):
            return True
    labels = set(host.split("."))
    if labels & {"youtube", "facebook", "instagram", "linkedin", "yelp", "wikipedia"}:
        return True
    return False


def is_direct_business_site(url: str) -> bool:
    """True for a company's own site, not a directory or aggregator profile."""
    if not is_business_site(url):
        return False
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return not _host_matches_excluded(host)


def _name_from_search_title(title: str, website: str) -> str:
    name = (title or "").strip()
    for sep in (" | ", " – ", " — ", " - ", " · "):
        if sep in name:
            name = name.split(sep, 1)[0].strip()
            break
    name = re.sub(r"\s+", " ", name).strip(" -|·")
    if len(name) >= 2:
        return name[:120]
    host = urlparse(website).netloc.lower().removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() if host else "Unknown business"


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return el.get_text(" ", strip=True)


def _normalize_website(href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if not host:
        return ""
    if "yellowpages.com" in host or "yp.com" in host:
        return ""
    if parsed.scheme not in {"http", "https"}:
        raw = "https://" + raw.lstrip("/")
        parsed = urlparse(raw)
        if not parsed.netloc:
            return ""
    return raw


def _parse_yp_rating(card: Tag) -> str:
    node = card.select_one(".ratings")
    if node is None:
        return ""
    for attr in ("data-rating", "data-average", "data-avg"):
        value = (node.get(attr) or "").strip()
        if value:
            return value
    class_blob = " ".join(node.get("class") or []).lower()
    for child in node.select("[class]"):
        class_blob += " " + " ".join(child.get("class") or []).lower()
    stars = 0
    for word, number in STAR_WORDS.items():
        if re.search(rf"\b{word}\b", class_blob):
            stars = max(stars, number)
    if stars and re.search(r"\bhalf\b", class_blob):
        return f"{stars}.5"
    if stars:
        return f"{stars:.1f}"
    match = re.search(r"(\d(?:\.\d)?)\s*(?:star|rating)?", _text(node), re.IGNORECASE)
    return match.group(1) if match else ""


def _yp_card_email(card: Tag) -> str:
    for link in card.select("a.email-business, a[href^=mailto]"):
        href = str(link.get("href") or "")
        raw = href.split("?", 1)[0].replace("mailto:", "").strip()
        cleaned = _clean_email(raw) if raw else None
        if cleaned:
            return cleaned
    return ""


def _parse_yp_card(card: Tag) -> dict | None:
    name = _text(card.select_one("a.business-name"))
    if not name:
        return None
    website_el = card.select_one("a.track-visit-website")
    href = ""
    if website_el is not None:
        href = str(website_el.get("href") or "")
    website = _normalize_website(href)
    if website and not is_business_site(website):
        website = ""
    phone = _text(card.select_one(".phone"))
    rating = _parse_yp_rating(card)
    return {
        "business_name": name,
        "website": website,
        "rating": rating,
        "phone": phone,
        "email": _yp_card_email(card),
    }


async def scrape_yellowpages(niche: str, location: str | None = None) -> list[dict]:
    """Fetch one Yellow Pages search page. Keep listings with or without a website.

    Pass a full query in `niche` (e.g. "Plumbers in Austin TX") and omit `location`
    to parse niche/city automatically.
    """
    niche = (niche or "").strip()
    location = (location or "").strip()
    if niche and not location:
        niche, location = parse_query(niche)
    if not niche or not location:
        log.warning("Yellow Pages scrape skipped — niche and location are required.")
        return []

    url = (
        f"{YP_SEARCH_URL}?search_terms={quote_plus(niche)}"
        f"&geo_location_terms={quote_plus(location)}"
    )
    log.info("Yellow Pages search: %s in %s", niche, location)
    try:
        async with httpx.AsyncClient(
            headers=YP_HEADERS,
            timeout=YP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        log.error("Yellow Pages timed out for %s in %s", niche, location)
        return []
    except httpx.HTTPError as exc:
        log.error("Yellow Pages request failed for %s in %s: %s", niche, location, exc)
        return []

    if response.status_code in {401, 403, 429}:
        log.error(
            "Yellow Pages returned HTTP %s (blocked or rate-limited) for %s in %s",
            response.status_code,
            niche,
            location,
        )
        _set_last_scrape(
            error=(
                f"Yellow Pages returned HTTP {response.status_code} and blocked this client. "
                "They treat simple scrapers as bots. Browser impersonation is not used. "
                "Use Google Maps with a real Places API key, or import a CSV of leads."
            )
        )
        return []
    if response.status_code >= 400:
        log.error("Yellow Pages HTTP %s for %s in %s", response.status_code, niche, location)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select("div.result, div.srp-listing, div.v-card")
    if not cards:
        log.warning("Yellow Pages returned no result cards for %s in %s", niche, location)
        return []

    listings: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        parsed = _parse_yp_card(card)
        if parsed is None:
            continue
        key = (parsed["website"] or parsed["business_name"]).lower()
        if key in seen:
            continue
        seen.add(key)
        listings.append(
            {
                "business_name": parsed["business_name"],
                "website": parsed["website"],
                "rating": parsed["rating"],
                "phone": parsed.get("phone") or "",
                "email": parsed.get("email") or "",
            }
        )

    with_site = sum(1 for item in listings if item["website"])
    log.info(
        "Yellow Pages scraped %s businesses (%s with website, %s without) for %s in %s",
        len(listings),
        with_site,
        len(listings) - with_site,
        niche,
        location,
    )
    return listings


async def _collect_yellowpages_listings(query: str) -> list[dict]:
    niche, location = parse_query(query)
    cities = expand_us_cities(location)
    listings: list[dict] = []
    seen: set[str] = set()
    for index, city in enumerate(cities):
        try:
            batch = await scrape_yellowpages(niche, city)
        except Exception:
            log.exception("Yellow Pages scrape crashed for %s in %s", niche, city)
            batch = []
        for item in batch:
            key = (item.get("website") or item.get("business_name") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            listings.append(item)
        if index < len(cities) - 1:
            await asyncio.sleep(1.0)
    log.info("Yellow Pages total listings: %s for %r", len(listings), query)
    return listings


def _search_places(client: googlemaps.Client, query: str, max_results: int) -> list[dict]:
    response = client.places(query=query, language="en", region="us")
    status = response.get("status")
    if status not in {"OK", "ZERO_RESULTS"}:
        message = response.get("error_message") or status
        raise RuntimeError(f"Places Text Search failed: {message}")

    collected: list[dict] = []
    while True:
        collected.extend(response.get("results") or [])
        if len(collected) >= max_results:
            break
        token = response.get("next_page_token")
        if not token:
            break
        time.sleep(PLACES_PAGE_DELAY)
        response = client.places(query=query, page_token=token, language="en", region="us")
        if response.get("status") not in {"OK", "ZERO_RESULTS"}:
            log.warning("Stopped Places pagination: %s", response.get("status"))
            break

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


def _place_details(client: googlemaps.Client, place_id: str) -> dict:
    result = client.place(
        place_id=place_id,
        fields=["name", "website", "rating", "user_ratings_total", "business_status", "formatted_address"],
    )
    if result.get("status") != "OK":
        return {}
    return result.get("result") or {}


def scrape_google_maps(query: str, max_results: int = MAX_RESULTS_PER_CITY) -> list[dict]:
    """Find businesses via the official Google Places API (not Maps HTML / Playwright)."""
    settings = get_settings()
    if not settings.google_maps_api_key or "your-google" in settings.google_maps_api_key.lower():
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing or still a placeholder.")
    try:
        client = googlemaps.Client(key=settings.google_maps_api_key)
    except ValueError as exc:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is invalid. Create a Places API key in Google Cloud.") from exc

    niche, location = parse_query(query)
    cities = expand_us_cities(location)
    listings: list[dict] = []
    seen_place_ids: set[str] = set()

    log.info("Google Maps (Places API) search for %r", query)
    for city in cities:
        places_query = f"{niche} in {city}"
        log.info("Places search: %s", places_query)
        batch = _search_places(client, places_query, max_results)
        for summary in batch:
            place_id = summary.get("place_id")
            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)
            details = _place_details(client, place_id)
            name = (details.get("name") or summary.get("name") or "").strip()
            if not name:
                continue
            address = (details.get("formatted_address") or summary.get("formatted_address") or "").strip()
            if address and not is_us_address(address):
                log.info("Skipped %s — address is outside USA: %s", name, address)
                continue
            website = (details.get("website") or "").strip()
            if website and not is_business_site(website):
                website = ""
            rating = details.get("rating", summary.get("rating"))
            rating_str = f"{rating:.1f}" if isinstance(rating, (int, float)) else str(rating or "").strip()
            listings.append(
                {
                    "business_name": name,
                    "website": website,
                    "rating": rating_str,
                    "email": "",
                }
            )

    log.info("Google Maps (Places) returned %s listings for %r", len(listings), query)
    return listings


LISTICLE_TITLE_RE = re.compile(
    r"(?i)(?:^\d+\s+(?:best|top)\b|^(?:best|top)\s+\d+\b|\bbest\s+.+\s+near\b|\baccording to locals\b)"
)

NICHE_SEARCH_ALIASES = {
    "painters": "house painters",
    "painter": "house painter",
    "painting": "house painting contractors",
}


def _search_niche(niche: str) -> str:
    key = (niche or "").strip().lower()
    return NICHE_SEARCH_ALIASES.get(key, niche.strip())


def _snippet_email(text: str) -> str:
    for raw in _extract_emails_from_text(text or ""):
        cleaned = _clean_email(raw)
        if cleaned:
            return cleaned
    return ""


def _ddg_text(search_query: str, max_results: int) -> list[dict]:
    try:
        rows = list(DDGS().text(search_query, max_results=max_results) or [])
        if rows:
            return rows
    except Exception as exc:
        log.warning("duckduckgo_search empty/failed for %r: %s", search_query, exc)
    try:
        from ddgs import DDGS as DDGSCurrent

        return list(DDGSCurrent().text(search_query, max_results=max_results) or [])
    except Exception as exc:
        log.error("DuckDuckGo search failed for %r: %s", search_query, exc)
        return []


def _parse_ddg_item(item: dict, seen_hosts: set[str], seen_names: set[str]) -> dict | None:
    href = str(item.get("href") or "")
    website = _normalize_website(href)
    host = urlparse(website).netloc.lower().removeprefix("www.") if website else ""
    snippet = f"{item.get('title') or ''} {item.get('body') or ''}"
    listed_email = _snippet_email(snippet)

    if host and (
        host in seen_hosts
        or _host_matches_excluded(host)
        or not is_direct_business_site(website)
        or host.endswith((".gov", ".edu", ".mil"))
    ):
        return None
    path = urlparse(website).path.lower() if website else ""
    if path and ("/best/" in path or path.rstrip("/").endswith("/best")):
        return None

    business_name = str(item.get("title") or "").strip()
    if not business_name or LISTICLE_TITLE_RE.search(business_name):
        return None
    name_key = re.sub(r"\s+", " ", business_name).lower()[:80]
    if name_key in seen_names:
        return None

    # DDG is website-first. Keep a no-website lead only when a public email is in the snippet.
    if not website:
        if not listed_email:
            return None
        seen_names.add(name_key)
        return {
            "business_name": business_name,
            "website": "",
            "rating": "N/A",
            "email": listed_email,
        }

    seen_hosts.add(host)
    seen_names.add(name_key)
    return {
        "business_name": business_name,
        "website": website,
        "rating": "N/A",
        "email": listed_email,
    }


async def scrape_duckduckgo(
    niche: str,
    location: str,
    max_results: int = DDG_TARGET_PER_CITY,
    seen_hosts: set[str] | None = None,
    seen_names: set[str] | None = None,
) -> list[dict]:
    """Find local businesses via DuckDuckGo. Target ~50 unique sites per city."""
    niche = (niche or "").strip()
    location = (location or "").strip()
    if niche and not location:
        niche, location = parse_query(niche)
    if not niche or not location:
        log.warning("DuckDuckGo scrape skipped — niche and location are required.")
        return []

    search_niche = _search_niche(niche)
    queries = [
        f"{search_niche} in {location} official website",
        f"{search_niche} in {location}",
        f"{search_niche} {location} company",
        f"{search_niche} near {location}",
        f"{location} {search_niche} contractor",
        f"{search_niche} {location} contact email",
    ]
    listings: list[dict] = []
    hosts = seen_hosts if seen_hosts is not None else set()
    names = seen_names if seen_names is not None else set()

    for search_query in queries:
        if len(listings) >= max_results:
            break
        log.info("DuckDuckGo search: %r", search_query)
        try:
            results = await asyncio.to_thread(_ddg_text, search_query, DDG_PAGE_SIZE)
        except Exception as exc:
            log.error("DuckDuckGo search failed for %r: %s", search_query, exc)
            continue
        for item in results or []:
            parsed = _parse_ddg_item(item, hosts, names)
            if parsed is None:
                continue
            listings.append(parsed)
            if len(listings) >= max_results:
                break
        if len(listings) < max_results:
            await asyncio.sleep(0.8)

    with_site = sum(1 for item in listings if item.get("website"))
    without_site = len(listings) - with_site
    log.info(
        "DuckDuckGo returned %s businesses (%s with website / %s without) for %s in %s",
        len(listings),
        with_site,
        without_site,
        niche,
        location,
    )
    if not listings:
        _set_last_scrape(error=f"DuckDuckGo found no business sites for {niche} in {location}.")
    return listings


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
OSM_HEADERS = {"User-Agent": "SoftenixOutreach/2.0 (local B2B research)"}
NO_WEBSITE_TARGET = 40
OSM_AROUND_METERS = 14000


def _osm_tag_filters(niche: str) -> list[str]:
    blob = (niche or "").lower()
    if "plumb" in blob:
        return ['["craft"="plumber"]']
    if "paint" in blob:
        return ['["craft"="painter"]']
    if "hvac" in blob or "air condition" in blob or "heating" in blob or "cooling" in blob:
        return ['["craft"="hvac"]', '["craft"="air_conditioning"]']
    if "dentist" in blob or "dental" in blob:
        return ['["amenity"="dentist"]']
    if "clinic" in blob or "doctor" in blob:
        return ['["amenity"="clinic"]', '["amenity"="doctors"]']
    if "restaurant" in blob or "cafe" in blob or "pizza" in blob:
        return ['["amenity"="restaurant"]']
    if "electric" in blob:
        return ['["craft"="electrician"]']
    if "roof" in blob:
        return ['["craft"="roofer"]']
    if "landscap" in blob:
        return ['["craft"="gardener"]', '["shop"="garden_centre"]']
    word = re.sub(r"[^a-z0-9]+", " ", blob).split()
    token = word[0] if word else "shop"
    return [f'["name"~"{re.escape(token)}",i]["craft"]']


def _osm_tags(element: dict) -> dict[str, str]:
    raw = element.get("tags") or {}
    return {str(k): str(v) for k, v in raw.items()}


def _osm_name(tags: dict[str, str]) -> str:
    return (tags.get("name") or tags.get("name:en") or "").strip()


def _osm_listed_website(tags: dict[str, str]) -> str:
    for key in ("website", "contact:website", "url"):
        href = _normalize_website(tags.get(key) or "")
        if href:
            return href
    return ""


def _osm_listed_email(tags: dict[str, str]) -> str:
    for key in ("email", "contact:email"):
        cleaned = _clean_email(tags.get(key) or "")
        if cleaned:
            return cleaned
    return ""


def _nominatim_point(location: str) -> tuple[float, float] | None:
    query = location if "usa" in location.lower() else f"{location}, USA"
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers=OSM_HEADERS,
            timeout=20.0,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        log.error("Nominatim lookup failed for %s: %s", location, exc)
        return None
    if not rows:
        return None
    try:
        return float(rows[0]["lat"]), float(rows[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _overpass_elements(filters: list[str], lat: float, lon: float) -> list[dict]:
    around = f"(around:{OSM_AROUND_METERS},{lat},{lon})"
    clauses = "\n".join(
        f"  node{tag}{around};\n  way{tag}{around};" for tag in filters
    )
    query = f"[out:json][timeout:20];\n(\n{clauses}\n);\nout center tags;"
    last_error = ""
    for url in OVERPASS_URLS:
        try:
            response = httpx.post(
                url,
                data={"data": query},
                headers=OSM_HEADERS,
                timeout=28.0,
            )
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("elements") or [])
        except Exception as exc:
            last_error = str(exc)
            log.warning("Overpass endpoint %s failed: %s", url, exc)
            continue
    log.error("Overpass query failed: %s", last_error)
    return []


def _ddg_confirms_independent_site(name: str, location: str) -> tuple[bool, str]:
    """If DuckDuckGo finds a real business site, they are not a no-website lead.

    Returns (has_independent_site, snippet_email).
    """
    search = f'"{name}" {location} official website'
    results = _ddg_text(search, 8)
    snippet_email = ""
    for item in results or []:
        snippet_email = snippet_email or _snippet_email(
            f"{item.get('title') or ''} {item.get('body') or ''}"
        )
        website = _normalize_website(str(item.get("href") or ""))
        if not website:
            continue
        host = urlparse(website).netloc.lower().removeprefix("www.")
        if host and not _host_matches_excluded(host) and is_direct_business_site(website):
            if not host.endswith((".gov", ".edu", ".mil")):
                return True, snippet_email
    return False, snippet_email


def _nominatim_search_businesses(niche: str, location: str) -> list[dict]:
    """Public OSM search. extratags.website is how we spot businesses with no site."""
    query = f"{_search_niche(niche)} {location}, USA"
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 50,
                "countrycodes": "us",
                "extratags": 1,
                "namedetails": 1,
            },
            headers=OSM_HEADERS,
            timeout=25.0,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        log.error("Nominatim business search failed for %r: %s", query, exc)
        return []
    return list(rows or [])


def _ddg_offline_business_emails(niche: str, location: str, max_results: int = 25) -> list[dict]:
    """Public search for owner emails (often Gmail) when there is no business website."""
    searches = [
        f'{niche} in {location} "@gmail.com"',
        f'{niche} {location} "@yahoo.com" contact',
        f'{niche} owner {location} email -yelp -zocdoc -healthgrades -opentable -grubhub',
        f'{niche} {location} "hotmail.com" OR "outlook.com"',
    ]
    listings: list[dict] = []
    seen_emails: set[str] = set()
    seen_names: set[str] = set()

    for search in searches:
        if len(listings) >= max_results:
            break
        log.info("No-website email search: %r", search)
        results = _ddg_text(search, 15)
        time.sleep(0.7)
        for item in results or []:
            website = _normalize_website(str(item.get("href") or ""))
            host = urlparse(website).netloc.lower().removeprefix("www.") if website else ""
            if host and is_direct_business_site(website) and not host.endswith((".gov", ".edu", ".mil")):
                continue
            snippet = f"{item.get('title') or ''} {item.get('body') or ''}"
            email = _snippet_email(snippet)
            if not email or email in seen_emails:
                continue
            name = _name_from_search_title(str(item.get("title") or ""), website)
            if not name or LISTICLE_TITLE_RE.search(name):
                continue
            name_key = name.lower()
            if name_key in seen_names:
                continue
            seen_emails.add(email)
            seen_names.add(name_key)
            listings.append(
                {
                    "business_name": name,
                    "website": "",
                    "rating": "N/A",
                    "email": email,
                }
            )
            if len(listings) >= max_results:
                break

    log.info(
        "Public email search found %s no-website contacts for %s in %s",
        len(listings),
        niche,
        location,
    )
    return listings


def scrape_no_website(query: str, max_results: int = NO_WEBSITE_TARGET) -> list[dict]:
    """Find operating businesses with no independent website, plus a public email."""
    niche, location = parse_query(query)
    log.info("No-website search: %s in %s", niche, location)

    listings = _ddg_offline_business_emails(niche, location, max_results)
    seen_names = {item["business_name"].lower() for item in listings}

    point = _nominatim_point(location)
    if point is not None and len(listings) < max_results:
        time.sleep(1.0)
        elements = _overpass_elements(_osm_tag_filters(niche), point[0], point[1])
        checked = 0
        for element in elements:
            if len(listings) >= max_results:
                break
            tags = _osm_tags(element)
            name = _osm_name(tags)
            if not name or name.lower() in seen_names or _osm_listed_website(tags):
                continue
            seen_names.add(name.lower())
            osm_email = _osm_listed_email(tags)
            has_site = False
            snippet_email = ""
            if checked < 12:
                checked += 1
                time.sleep(0.5)
                has_site, snippet_email = _ddg_confirms_independent_site(name, location)
            if has_site:
                log.info("Skipped %s — DuckDuckGo found an independent website.", name)
                continue
            email = osm_email or snippet_email
            if not email:
                continue
            listings.append(
                {
                    "business_name": name,
                    "website": "",
                    "rating": "N/A",
                    "email": email,
                }
            )

    if not listings:
        _set_last_scrape(
            error=(
                f"No public emails found for {niche} in {location} without a website. "
                "These owners rarely publish email. A Places API key or a purchased list works better."
            )
        )

    log.info(
        "No-website source returned %s listings (%s with email) for %r",
        len(listings),
        sum(1 for item in listings if item.get("email")),
        query,
    )
    return listings

    log.info(
        "No-website source returned %s listings (%s with an OSM/snippet email) for %r",
        len(listings),
        sum(1 for item in listings if item.get("email")),
        query,
    )
    return listings


def _decode_cloudflare_emails(html: str) -> list[str]:
    found: list[str] = []
    for encoded in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
        try:
            key = int(encoded[:2], 16)
            chars = [chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2)]
            found.append("".join(chars))
        except ValueError:
            continue
    return found


def _extract_emails_from_text(text: str) -> list[str]:
    candidates = EMAIL_RE.findall(text)
    for local, domain, tld in OBFUSCATED_RE.findall(text):
        candidates.append(f"{local}@{domain}.{tld}")
    return candidates


def _clean_email(raw: str) -> str | None:
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


def _pick_best_emails(candidates: list[str], website: str) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        email = _clean_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        cleaned.append(email)
    if not cleaned:
        return []

    site_host = urlparse(website).netloc.lower().removeprefix("www.")

    def score(email: str) -> tuple[int, int]:
        local, domain = email.split("@", 1)
        same_domain = int(site_host.endswith(domain) or domain.endswith(site_host))
        preferred_local = int(local in {"info", "hello", "contact", "office", "admin", "inquiries"})
        return (same_domain, preferred_local)

    cleaned.sort(key=score, reverse=True)
    return cleaned


def _fetch_html(session: requests.Session, url: str) -> str:
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


def _emails_from_html(html: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    found: list[str] = []
    found.extend(_decode_cloudflare_emails(html))
    for link in soup.select("a[href^=mailto]"):
        href = link.get("href", "")
        found.extend(EMAIL_RE.findall(href.split("?", 1)[0]))
    found.extend(_extract_emails_from_text(soup.get_text(" ", strip=True)))
    found.extend(_extract_emails_from_text(html))
    return found


def extract_website_emails(website_url: str) -> list[str]:
    """Pull public emails from homepage and /contact, then keep MX/DNS-valid addresses."""
    if not website_url or not is_business_site(website_url):
        return []

    time.sleep(random.uniform(*HOMEPAGE_DELAY))
    session = requests.Session()
    html = _fetch_html(session, website_url)
    candidates = _emails_from_html(html)

    contact_url = urljoin(website_url.rstrip("/") + "/", "contact")
    if html:
        time.sleep(random.uniform(*CONTACT_PAGE_DELAY))
        candidates.extend(_emails_from_html(_fetch_html(session, contact_url)))
        for path in ("/contact-us", "/about"):
            extra = urljoin(website_url.rstrip("/") + "/", path.lstrip("/"))
            if extra.rstrip("/") == website_url.rstrip("/") or extra.rstrip("/") == contact_url.rstrip("/"):
                continue
            time.sleep(random.uniform(*CONTACT_PAGE_DELAY))
            candidates.extend(_emails_from_html(_fetch_html(session, extra)))

    ranked = _pick_best_emails(candidates, website_url)
    valid: list[str] = []
    for email in ranked:
        checked = is_deliverable_email(email)
        if checked and checked not in valid:
            valid.append(checked)
    if valid:
        log.info("Valid email(s) on %s: %s", website_url, ", ".join(valid[:3]))
    else:
        log.info("No MX-valid public email on %s", website_url)
    return valid


def _persist_listings(listings: list[dict], saved: int, skipped: int, target: int) -> tuple[int, int]:
    """Save valid-email leads until we hit the target. Updates job status as it goes."""
    db = SessionLocal()
    try:
        for listing in listings:
            if saved >= target:
                break
            website = listing.get("website") or ""
            name = listing.get("business_name") or ""
            emails: list[str] = []
            listed = (listing.get("email") or "").strip()
            if listed:
                emails.append(listed)
            if website:
                emails.extend(extract_website_emails(website))
            chosen = pick_outreach_email(
                emails,
                website,
                require_business_domain=get_settings().require_business_email and bool(website),
            )
            if not chosen:
                skipped += 1
                log.info(
                    "Skipped %s — no quality public email (%s)",
                    name,
                    "has website" if website else "no website listed",
                )
                _set_last_scrape(saved=saved, skipped=skipped)
                continue
            created = upsert_lead(
                db,
                business_name=name,
                email=chosen,
                website=website,
                rating=listing.get("rating") or "",
            )
            if created:
                saved += 1
                log.info("Saved lead %s <%s> (%s/%s)", name, chosen, saved, target)
            else:
                log.info("Already saved: %s <%s>", name, chosen)
            _set_last_scrape(saved=saved, skipped=skipped)
    finally:
        db.close()
    return saved, skipped


def _google_maps_ready() -> bool:
    key = get_settings().google_maps_api_key
    return bool(key) and "your-google" not in key.lower()


def _source_chain(source: str) -> list[str]:
    selected = (source or "auto").strip().lower()
    if selected != "auto":
        return [selected]
    chain = [item for item in AUTO_SOURCE_CHAIN if item != "google_maps" or _google_maps_ready()]
    return chain


def _fetch_source_batch(
    source: str,
    variant: str,
    loc: str,
    seen_hosts: set[str],
    seen_names: set[str],
) -> list[dict]:
    if source == "duckduckgo":
        return asyncio.run(
            scrape_duckduckgo(
                variant,
                loc,
                max_results=20,
                seen_hosts=seen_hosts,
                seen_names=seen_names,
            )
        )
    if source == "yellowpages":
        return asyncio.run(scrape_yellowpages(variant, loc))
    if source == "google_maps":
        return scrape_google_maps(f"{variant} in {loc}")
    if source == "no_website":
        return scrape_no_website(f"{variant} in {loc}")
    return []


def run_scrape_job(query: str, source: str = "auto") -> None:
    """Keep scraping until TARGET_VALID_LEADS new emails are saved, rotating cities and sources."""
    _set_last_scrape(source=source, query=query, saved=0, skipped=0, error="")
    try:
        chain = _source_chain(source)
        if not chain or any(item not in VALID_SCRAPE_SOURCES and item != "auto" for item in chain):
            invalid = [item for item in chain if item not in {"duckduckgo", "yellowpages", "google_maps", "no_website"}]
            if invalid:
                log.error("Invalid scrape source %r — aborting job.", invalid)
                _set_last_scrape(error=f"Invalid source: {invalid[0]}")
                return

        log.info(
            "Scrape source selected: %s — query=%r — target %s valid leads",
            " -> ".join(chain),
            query,
            TARGET_VALID_LEADS,
        )
        niche, location = parse_query(query)
        locations = _search_locations(location)
        variants = _niche_variants(niche)
        seen_hosts: set[str] = set()
        seen_names: set[str] = set()
        persist_seen: set[str] = set()
        saved = 0
        skipped = 0
        found_any = False
        used_sources: list[str] = []

        for src in chain:
            if saved >= TARGET_VALID_LEADS:
                break
            before = saved
            log.info("Trying source %s (saved %s/%s)", src, saved, TARGET_VALID_LEADS)
            _set_last_scrape(source=src, saved=saved, skipped=skipped)
            for loc in locations:
                if saved >= TARGET_VALID_LEADS:
                    break
                for variant in variants:
                    if saved >= TARGET_VALID_LEADS:
                        break
                    log.info("Searching %s in %s via %s (saved %s/%s)", variant, loc, src, saved, TARGET_VALID_LEADS)
                    batch = _fetch_source_batch(src, variant, loc, seen_hosts, seen_names)
                    unique: list[dict] = []
                    for item in batch:
                        ident = (
                            urlparse(item.get("website") or "").netloc.lower().removeprefix("www.")
                            or (item.get("business_name") or "").lower()
                        )
                        if not ident or ident in persist_seen:
                            continue
                        persist_seen.add(ident)
                        unique.append(item)
                    if unique:
                        found_any = True
                        saved, skipped = _persist_listings(unique, saved, skipped, TARGET_VALID_LEADS)
            if saved > before:
                used_sources.append(src)
            elif saved < TARGET_VALID_LEADS:
                log.info("%s added 0 new emails — switching source.", src)

        label = " + ".join(used_sources) or chain[0]
        log.info(
            "Scrape finished via %s for %r — saved %s new lead(s), skipped %s.",
            label,
            query,
            saved,
            skipped,
        )
        note = ""
        if saved >= TARGET_VALID_LEADS:
            note = ""
        elif saved == 0 and skipped and found_any:
            note = (
                f"Found listings but none had a public business-domain email "
                f"({skipped} skipped). Try REQUIRE_BUSINESS_EMAIL=false in .env for more leads."
            )
        elif saved == 0 and not found_any:
            note = "No businesses found after rotating US cities and sources."
        elif saved < TARGET_VALID_LEADS:
            note = (
                f"Stopped at {saved}/{TARGET_VALID_LEADS} valid leads after rotating cities/sources. "
                f"{skipped} listings had no usable public email."
            )
        _set_last_scrape(saved=saved, skipped=skipped, error=note, source=label)
    except Exception as exc:
        log.exception("Scrape job failed for %r", query)
        _set_last_scrape(error=str(exc))
    finally:
        _end_scrape()


async def run_scrape_job_async(query: str, source: str = "auto") -> None:
    """Run the sync scrape job off the event loop so the API stays responsive."""
    await asyncio.to_thread(run_scrape_job, query, source)
