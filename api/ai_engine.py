from __future__ import annotations

import html
import json
import logging
import re

import ollama

from api.prompts import SYSTEM_PROMPT, user_email_prompt

log = logging.getLogger("api.ai_engine")

OLLAMA_MODEL = "llama3.2"

COMPLIANCE_FOOTER = (
    "---\n"
    "Minhal | Founder, Softenix Solution\n"
    "Virginia, USA\n"
    "Reply 'No thanks' to opt out."
)
COMPLIANCE_MARKER = "Minhal | Founder, Softenix Solution"

_SIGNATURE_LINE = re.compile(
    r"^(?:-{2,}|—|–|best(?:\s+regards)?|thanks|thank you|regards|"
    r"sincerely|cheers|warmly|all the best|kind regards|"
    r"alex\b.*softenix|syed\b|minhal\b|softenix solution)\b",
    re.IGNORECASE,
)


def strip_generated_footer(body: str) -> str:
    lines = body.strip().splitlines()
    cut = len(lines)

    for i, line in enumerate(lines):
        if COMPLIANCE_MARKER in line:
            cut = i
            if i > 0 and lines[i - 1].strip() in {"---", "--", "—", "–"}:
                cut = i - 1
            break

    start = max(0, cut - 10)
    for i in range(start, cut):
        if _SIGNATURE_LINE.match(lines[i].strip()):
            cut = i
            break

    return "\n".join(lines[:cut]).rstrip()


def with_compliance_footer(body: str) -> str:
    core = strip_generated_footer(body)
    return f"{core}\n\n{COMPLIANCE_FOOTER}"


def compliance_footer_html() -> str:
    lines = html.escape(COMPLIANCE_FOOTER).replace("\n", "<br>")
    return (
        '<p style="margin:24px 0 0 0;font-family:Arial,sans-serif;'
        'font-size:12px;line-height:1.45;color:#888888;">'
        f"{lines}</p>"
    )


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_draft(text: str) -> tuple[str, str] | None:
    blob = text
    if "{" in text and "}" in text:
        blob = text[text.find("{") : text.rfind("}") + 1]
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    if subject and body:
        return subject, body
    return None


def parse_subject_and_body(content: str) -> tuple[str, str]:
    text = _strip_code_fence(content)
    if not text:
        raise ValueError("Ollama returned an empty draft.")

    parsed = _parse_json_draft(text)
    if parsed:
        return parsed

    lines = [line.rstrip() for line in text.splitlines()]
    first = lines[0].strip()
    if first.lower().startswith("subject:"):
        subject = first.split(":", 1)[1].strip()
    else:
        subject = first
    body = "\n".join(lines[1:]).strip()
    if not subject or not body:
        raise ValueError("Ollama draft missing subject or body.")
    return subject, body


def encode_draft(subject: str, body: str) -> str:
    core = strip_generated_footer(body)
    return f"Subject: {subject.strip()}\n\n{core}"


def decode_draft(ai_draft: str) -> tuple[str, str]:
    text = (ai_draft or "").strip()
    if not text:
        return "", ""
    try:
        subject, body = parse_subject_and_body(text)
    except ValueError:
        return "", with_compliance_footer(text)
    return subject, with_compliance_footer(body)


def generate_email_draft(lead_name: str, website: str, rating: str) -> str:
    """Draft a short casual B2B cold email via local Ollama, then append CAN-SPAM footer."""
    from api.scraper import last_scrape_status, parse_query

    last = last_scrape_status()
    niche, location = parse_query(str(last.get("query") or "").strip() or lead_name)
    user_prompt = user_email_prompt(lead_name, website, rating, niche, location)
    has_site = bool((website or "").strip())
    log.info(
        "Generating Ollama draft for %s (%s, %s in %s)",
        lead_name,
        "upgrade existing site" if has_site else "build new site",
        niche,
        location,
    )
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            format="json",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:
        raise RuntimeError(
            "Ollama request failed. Is Ollama running, and have you pulled llama3 "
            f"(`ollama pull llama3`)? {exc}"
        ) from exc

    message = response["message"] if isinstance(response, dict) else getattr(response, "message", {})
    if isinstance(message, dict):
        content = message.get("content") or ""
    else:
        content = getattr(message, "content", "") or ""

    subject, body = parse_subject_and_body(content)
    full_body = with_compliance_footer(body)
    log.info("Draft ready for %s — subject: %s", lead_name, subject)
    return encode_draft(subject, full_body)
