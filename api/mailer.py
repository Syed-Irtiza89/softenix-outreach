from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from api.ai_engine import COMPLIANCE_FOOTER, compliance_footer_html, with_compliance_footer
from api.config import Settings
from api.hours import is_us_business_hours, now_eastern
from api.schemas import SendEmailRequest, SendEmailResponse
from api.validate import is_consumer_sender

log = logging.getLogger("api.mailer")

TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


def tracking_pixel_url(base_url: str, lead_id: int) -> str:
    return f"{base_url.rstrip('/')}/track/{lead_id}.gif"


def tracking_is_live(settings: Settings) -> bool:
    if not settings.enable_tracking:
        return False
    host = (settings.public_base_url or "").lower()
    return "localhost" not in host and "127.0.0.1" not in host


def text_to_html(body: str, lead_id: int | None, settings: Settings) -> str:
    core = body.strip()
    if COMPLIANCE_FOOTER in core:
        core = core.replace(COMPLIANCE_FOOTER, "").rstrip()
    paragraphs: list[str] = []
    for block in core.split("\n\n"):
        if not block.strip():
            continue
        paragraphs.append(
            '<p style="margin:0 0 12px 0;font-family:Arial,sans-serif;'
            'font-size:15px;line-height:1.5;color:#111;">'
            f"{html.escape(block).replace(chr(10), '<br>')}</p>"
        )
    if not paragraphs:
        paragraphs.append("<p></p>")
    pixel = ""
    if lead_id is not None and tracking_is_live(settings):
        src = html.escape(tracking_pixel_url(settings.public_base_url, lead_id), quote=True)
        pixel = f'<img src="{src}" width="1" height="1" alt="" />'
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:16px;">'
        f"{''.join(paragraphs)}{compliance_footer_html()}{pixel}</body></html>"
    )


def send_email(settings: Settings, payload: SendEmailRequest) -> SendEmailResponse:
    if not is_us_business_hours():
        raise RuntimeError(
            "Refusing to send outside US business hours "
            f"(current Eastern time: {now_eastern().strftime('%A %I:%M %p %Z')})."
        )
    domain = settings.sender_email.rsplit("@", 1)[-1] if "@" in settings.sender_email else "localhost"
    if is_consumer_sender(settings.sender_email):
        log.warning(
            "Sending cold email from %s. Free inboxes (Gmail/Yahoo/Outlook) are often filtered "
            "or limited. Use Google Workspace on your own domain with SPF, DKIM, and DMARC.",
            settings.sender_email,
        )
    plain = with_compliance_footer(payload.body)
    html_body = text_to_html(plain, payload.lead_id, settings)

    message = EmailMessage()
    message["Subject"] = payload.subject.strip()
    message["From"] = f"{settings.sender_name} <{settings.sender_email}>"
    message["To"] = str(payload.recipient_email)
    message["Reply-To"] = settings.reply_to_email
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=domain)
    unsub = settings.unsubscribe_email or settings.sender_email
    if unsub:
        message["List-Unsubscribe"] = f"<mailto:{unsub}?subject=unsubscribe>"
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")

    log.info("SMTP send via Gmail to %s — %s", payload.recipient_email, payload.subject.strip())
    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(settings.sender_email, settings.sender_app_password)
        server.send_message(message)

    return SendEmailResponse(
        ok=True,
        message=f"Sent to {payload.recipient_email}",
        message_id=message["Message-ID"] or "",
    )
