"""
Softenix Solution — CrewAI email draft (research + copy).

Researcher Agent visits the company URL and writes a 3-bullet brief.
Copywriter Agent turns that brief into a 3-sentence cold email.

Usage:
    python crew_email.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Type
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent

# Change this URL to research a different company.
TARGET_URL = "https://www.notion.so"

AGENCY_NAME = "Softenix Solution"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SoftenixResearch/1.0; +https://softenixsolution.com)"
)
MAX_PAGE_CHARS = 8000
REQUEST_TIMEOUT = 15


class VisitWebsiteInput(BaseModel):
    url: str = Field(..., description="Public company website URL to visit.")


class VisitWebsiteTool(BaseTool):
    name: str = "visit_website"
    description: str = (
        "Visit a company website and return the visible page text. "
        "Use this before writing any research bullets."
    )
    args_schema: Type[BaseModel] = VisitWebsiteInput

    def _run(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return f"Invalid URL: {url}"

        try:
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return f"Could not fetch {url}: {exc}"

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > MAX_PAGE_CHARS:
            text = text[:MAX_PAGE_CHARS] + "\n\n[truncated]"
        if not text.strip():
            return f"The page at {url} had no readable text."
        return f"URL: {response.url}\n\n{text}"


def make_llm() -> LLM:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY in .env")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    kwargs: dict = {"model": model, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return LLM(**kwargs)


def build_crew(llm: LLM) -> Crew:
    researcher = Agent(
        role="Researcher Agent",
        goal=(
            "Visit the target company website and produce a short, factual brief "
            "that a copywriter can use for outreach."
        ),
        backstory=(
            "You research local and SaaS companies for Softenix Solution. "
            "You only write what the website actually supports. You never invent "
            "clients, metrics, or product names."
        ),
        tools=[VisitWebsiteTool()],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    copywriter = Agent(
        role="Copywriter Agent",
        goal=(
            "Write a highly personalized 3-sentence cold email offering custom "
            "software and web development from Softenix Solution."
        ),
        backstory=(
            f"You write for {AGENCY_NAME}. Your emails are short, specific, and "
            "conversational. You pitch custom software or web work only when it "
            "matches a pain point from the research brief."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    research_task = Task(
        description=(
            "Visit this company website: {url}\n"
            "Use the visit_website tool. Then write exactly 3 bullet points:\n"
            "1) What the company does.\n"
            "2) Who they appear to serve.\n"
            "3) One or two plausible operational pain points that custom software "
            "or a better website could address.\n"
            "If the site cannot be read, say so clearly and do not invent details."
        ),
        expected_output="Exactly 3 factual bullet points based on the live website.",
        agent=researcher,
    )

    copy_task = Task(
        description=(
            "Using only the Researcher Agent's 3-bullet brief for {url}, write a "
            "highly personalized cold email from Softenix Solution.\n"
            "Rules:\n"
            "- Exactly 3 sentences in the body.\n"
            "- Mention something specific from the brief.\n"
            "- Pitch custom software or web development as a light next step, "
            "not a hard sell.\n"
            "- No hype, no exclamation marks, no emojis.\n"
            "- Include a subject line on the first line as: Subject: ...\n"
            "- Sign the body as Alex at Softenix Solution."
        ),
        expected_output=(
            "A subject line plus a 3-sentence email body ready to send."
        ),
        agent=copywriter,
        context=[research_task],
    )

    return Crew(
        agents=[researcher, copywriter],
        tasks=[research_task, copy_task],
        process=Process.sequential,
        verbose=True,
    )


def run_crew(url: str) -> str:
    crew = build_crew(make_llm())
    result = crew.kickoff(inputs={"url": url})
    return str(result).strip()


def main() -> int:
    print(f"Researching: {TARGET_URL}\n")
    draft = run_crew(TARGET_URL)
    print("\n" + "=" * 72)
    print("FINAL EMAIL DRAFT")
    print("=" * 72)
    print(draft)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
