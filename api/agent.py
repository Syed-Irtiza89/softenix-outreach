from __future__ import annotations

import json
import logging
import re
from typing import Any

import ollama
from sqlalchemy.orm import Session

from api.ai_engine import OLLAMA_MODEL
from api.credits import list_credits
from api.services.lead_tools import check_tech_stack, find_domain_emails, search_apollo_leads

log = logging.getLogger("api.agent")

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_apollo_leads",
            "description": "Find decision-maker names and emails from Apollo for a US niche and city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "niche": {"type": "string", "description": "Business type, e.g. plumbers, roofers, dentists"},
                    "location": {"type": "string", "description": "US city and state, e.g. Austin TX"},
                    "limit": {"type": "integer", "description": "How many people to fetch (1-25)", "default": 5},
                },
                "required": ["niche", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_domain_emails",
            "description": "Find public emails on a company domain using Hunter.io.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Company domain such as acmeplumbing.com"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tech_stack",
            "description": "Inspect which technologies a website uses via BuiltWith.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Website domain such as example.com"},
                },
                "required": ["domain"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are Softenix Outreach's lead-finder agent. Choose exactly one tool. "
    "Use search_apollo_leads for people/emails by niche and city (Apollo). "
    "Use find_domain_emails when the user gives a domain and wants emails (Hunter). "
    "Use check_tech_stack when the user wants website technologies (BuiltWith). "
    "Never invent API results. If the city is missing, default location to United States."
)

_DOMAIN_RE = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\b(\d{1,2})\b")
_IN_RE = re.compile(r"\b(?:in|from|near)\s+([A-Za-z][A-Za-z0-9 .,'-]{1,40})$", re.IGNORECASE)


def _tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _heuristic_intent(prompt: str) -> dict[str, Any]:
    text = prompt.strip()
    lowered = text.lower()
    domain_match = _DOMAIN_RE.search(text)
    domain = domain_match.group(1).rstrip(".,") if domain_match else ""
    limit_match = _LIMIT_RE.search(text)
    limit = int(limit_match.group(1)) if limit_match else 5
    limit = max(1, min(limit, 25))

    if any(token in lowered for token in ("builtwith", "tech stack", "technologies", "what cms")):
        if not domain:
            raise RuntimeError("Name a domain to inspect, e.g. check tech stack of acme.com")
        return {"name": "check_tech_stack", "arguments": {"domain": domain}}

    if any(token in lowered for token in ("hunter", "emails on", "emails for", "domain-search", "find emails")):
        if not domain:
            raise RuntimeError("Name a domain for Hunter, e.g. find emails for acme.com")
        return {"name": "find_domain_emails", "arguments": {"domain": domain}}

    location = "United States"
    in_match = _IN_RE.search(text.replace(" from Apollo", "").replace(" from apollo", ""))
    if in_match:
        location = in_match.group(1).strip(" .")
        location = re.sub(r"\s+from\s+apollo.*$", "", location, flags=re.IGNORECASE).strip()

    niche = text
    niche = re.sub(r"https?://\S+", "", niche)
    niche = re.sub(
        r"\b(go to apollo|from apollo|apollo|extract|get|find|search|leads?|emails?)\b",
        " ",
        niche,
        flags=re.IGNORECASE,
    )
    niche = re.sub(r"\b(in|from|near)\s+" + re.escape(location) + r"\b", " ", niche, flags=re.IGNORECASE)
    niche = re.sub(r"\d+", " ", niche)
    niche = re.sub(r"\s+", " ", niche).strip(" .,-") or "local businesses"
    return {"name": "search_apollo_leads", "arguments": {"niche": niche, "location": location, "limit": limit}}


def parse_agent_intent(prompt: str) -> dict[str, Any]:
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=AGENT_TOOLS,
        )
    except Exception as exc:
        log.warning("Ollama tool-call failed (%s); using keyword parser.", exc)
        return _heuristic_intent(prompt)

    message = response["message"] if isinstance(response, dict) else getattr(response, "message", {})
    tool_calls = []
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
    else:
        tool_calls = getattr(message, "tool_calls", None) or []
        content = getattr(message, "content", "") or ""

    if tool_calls:
        first = tool_calls[0]
        fn = first.get("function") if isinstance(first, dict) else getattr(first, "function", None)
        if isinstance(fn, dict):
            name = str(fn.get("name") or "")
            args = _tool_args(fn.get("arguments"))
        else:
            name = str(getattr(fn, "name", "") if fn is not None else "")
            args = _tool_args(getattr(fn, "arguments", {}) if fn is not None else {})
        if name:
            return {"name": name, "arguments": args}

    blob = content.strip()
    if "{" in blob and "}" in blob:
        try:
            parsed = json.loads(blob[blob.find("{") : blob.rfind("}") + 1])
            if isinstance(parsed, dict) and (parsed.get("name") or parsed.get("tool")):
                return {
                    "name": str(parsed.get("name") or parsed.get("tool")),
                    "arguments": parsed.get("arguments") or parsed,
                }
        except json.JSONDecodeError:
            pass
    return _heuristic_intent(prompt)


def run_agent_command(db: Session, prompt: str) -> dict[str, Any]:
    intent = parse_agent_intent(prompt)
    name = str(intent.get("name") or "")
    args = intent.get("arguments") if isinstance(intent.get("arguments"), dict) else {}
    log.info("Agent chose %s with %s", name, args)

    if name == "search_apollo_leads":
        result = search_apollo_leads(
            db,
            niche=str(args.get("niche") or "local businesses"),
            location=str(args.get("location") or "United States"),
            limit=int(args.get("limit") or 5),
        )
    elif name == "find_domain_emails":
        result = find_domain_emails(db, str(args.get("domain") or ""))
    elif name == "check_tech_stack":
        result = check_tech_stack(db, str(args.get("domain") or ""))
    else:
        raise RuntimeError(f"Unknown tool '{name}'. Try: get 5 plumbers in Austin TX from Apollo.")

    result["credits_all"] = list_credits(db)
    return result
