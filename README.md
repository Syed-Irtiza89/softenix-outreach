# Softenix Solution — AI Cold Email Outreach

Sends a short, personalized outreach email to each lead in `leads.csv`. Drafts are generated with OpenAI. Sends go through Gmail SMTP with a random 5–10 minute pause between messages so you stay well under typical Gmail burst limits.

Use this only for legitimate B2B outreach to businesses you are offering a real service. Include an opt-out (already appended), honor STOP replies, and keep daily volume low.

## 1. Install

```powershell
cd "c:\Users\hp\OneDrive\Desktop\Cruise"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configure environment

Copy the example file and fill in real values:

```powershell
copy .env.example .env
```

`.env` keys:

| Key | Purpose |
| --- | --- |
| `SENDER_EMAIL` | Your Gmail address |
| `SENDER_APP_PASSWORD` | Gmail App Password (not your normal password) |
| `SENDER_NAME` | From display name |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | Default `gpt-4o-mini` |
| `MAX_EMAILS_PER_RUN` | Hard cap per script run (default 20) |
| `MIN_DELAY_SECONDS` | Minimum pause after a send (default 300) |
| `MAX_DELAY_SECONDS` | Maximum pause after a send (default 600) |
| `REPLY_TO_EMAIL` | Where replies should land |
| `UNSUBSCRIBE_EMAIL` | Mailbox mentioned in the opt-out footer |
| `DRY_RUN` | `true` to draft without sending |

Gmail App Password: turn on 2-Step Verification, then create an app password at [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

## 3. Prepare leads

`leads.csv` must have these headers:

```text
BusinessName,Email,Website,GoogleReviewScore,Observation
```

Replace the sample rows with real leads. Use work emails you collected fairly. Do not buy scraped lists you cannot justify.

## 4. Preview, then send

Always preview first:

```powershell
python outreach.py --dry-run
```

Then send (still capped by `MAX_EMAILS_PER_RUN`):

```powershell
python outreach.py
```

Successful sends are written to `outreach.db` and `success_log.csv`. The same email address is never sent twice.

## Safety notes

- Random delay of 300–600 seconds after every real send.
- Default cap of 20 emails per run. Personal Gmail is not a bulk ESP; stay far below Google’s daily limits.
- Invalid addresses and SMTP errors are logged and skipped; an auth failure stops the run.
- This is not a substitute for CAN-SPAM / PECR / GDPR compliance in your region. Add a physical mailing address if you are emailing US consumers, and do not email people who have opted out.
