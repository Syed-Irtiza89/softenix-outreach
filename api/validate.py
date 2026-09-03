from __future__ import annotations

import logging
from urllib.parse import urlparse

import dns.resolver
from dns.exception import DNSException
from email_validator import EmailNotValidError, validate_email

log = logging.getLogger("api.validate")

CONSUMER_INBOX_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "aol.com",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
}

DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "10minutemail.com",
    "yopmail.com",
    "trashmail.com",
    "sharklasers.com",
}

CONSUMER_SENDER_DOMAINS = CONSUMER_INBOX_DOMAINS


def normalize_email(address: str) -> str | None:
    try:
        return validate_email(address, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def domain_can_receive_mail(domain: str) -> bool:
    """True if the domain publishes MX records, or an A/AAAA fallback (RFC 5321)."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    resolver.timeout = 3

    for record_type in ("MX", "A", "AAAA"):
        try:
            answers = resolver.resolve(domain, record_type)
            if answers:
                if record_type != "MX":
                    log.info("%s has no MX; accepted %s fallback.", domain, record_type)
                return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            continue
        except DNSException as exc:
            log.warning("DNS lookup failed for %s (%s): %s", domain, record_type, exc)
            continue
    return False


def is_deliverable_email(address: str) -> str | None:
    normalized = normalize_email(address)
    if not normalized or "@" not in normalized:
        return None
    domain = normalized.rsplit("@", 1)[1]
    if domain in DISPOSABLE_DOMAINS:
        return None
    if not domain_can_receive_mail(domain):
        return None
    return normalized


def is_consumer_inbox(address: str) -> bool:
    if "@" not in address:
        return False
    return address.rsplit("@", 1)[1].lower() in CONSUMER_INBOX_DOMAINS


def is_consumer_sender(address: str) -> bool:
    if "@" not in address:
        return False
    return address.rsplit("@", 1)[1].lower() in CONSUMER_SENDER_DOMAINS


def website_host(website: str) -> str:
    host = urlparse(website or "").netloc.lower().removeprefix("www.")
    return host.split(":")[0]


def email_matches_website(address: str, website: str) -> bool:
    if not website or "@" not in address:
        return False
    host = website_host(website)
    domain = address.rsplit("@", 1)[1].lower()
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def pick_outreach_email(
    emails: list[str],
    website: str,
    *,
    require_business_domain: bool,
) -> str | None:
    """Prefer a mailbox on the business website domain. Skip disposable / generic webmail when a site exists."""
    unique: list[str] = []
    seen: set[str] = set()
    for item in emails:
        checked = is_deliverable_email(item) if item else None
        if not checked or checked in seen:
            continue
        seen.add(checked)
        unique.append(checked)
    if not unique:
        return None

    matching = [item for item in unique if email_matches_website(item, website)]
    if matching:
        return matching[0]
    if website and require_business_domain:
        log.info("Dropped %s — no email on the business domain (%s).", unique[0], website)
        return None
    non_consumer = [item for item in unique if not is_consumer_inbox(item)]
    return (non_consumer or unique)[0]

