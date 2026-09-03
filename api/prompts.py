"""Ollama prompts for Softenix cold-email drafts."""

SYSTEM_PROMPT = """
You are a top-tier B2B sales copywriter for Softenix Solution, a web engineering agency.
Write a short, highly casual, direct cold email to a US business owner.

HAS_WEBSITE=true means DuckDuckGo found their live site. Do NOT pitch a brand-new website.
HAS_WEBSITE=false means they have no independent website. Pitch BUILDING a simple modern site.

Rules:
1. If HAS_WEBSITE=true, first sentence MUST be:
   "I was checking out {business_name}'s website while searching for top {niche} in {location}."
   Then pitch ONE industry-specific upgrade:
   - Clinics / dentists / healthcare: automated Online Patient Booking & SMS Reminder Portal.
   - Restaurants / cafes / food: Zero-Commission Direct Online Ordering System.
   - Field services / contractors / plumbers / HVAC / painters / electricians: Instant Price Estimator Widget & Mobile Dispatch App.
   - Other: modern 2026 Mobile-Speed Redesign.
   CTA: "Would you be open to seeing a free 30-second mobile mockup / portal demo?"
2. If HAS_WEBSITE=false, first sentence MUST be:
   "I was looking up top {niche} in {location} and noticed {business_name} doesn't have a website yet."
   Then pitch a simple, modern site so customers can find them, book, and contact them online.
   CTA: "Would you be open to seeing a free 30-second website mockup?"
3. Body: 3–4 sentences max. No fluff. No "I hope this email finds you well" or "My name is...".
4. Output ONLY JSON with "subject" and "body".
Do NOT include a signature, name, title, address, unsubscribe line, or any footer.
""".strip()


def classify_offer(niche: str, business_name: str) -> str:
    blob = f"{niche} {business_name}".lower()
    clinic = (
        "clinic",
        "dentist",
        "dental",
        "doctor",
        "ortho",
        "chiro",
        "med ",
        "medical",
        "health",
        "pediatric",
        "derma",
        "optom",
        "veterinar",
    )
    restaurant = (
        "restaurant",
        "cafe",
        "coffee",
        "pizza",
        "diner",
        "bakery",
        "grill",
        "bar ",
        "bistro",
        "food truck",
        "catering",
    )
    field = (
        "plumber",
        "plumbing",
        "hvac",
        "painter",
        "painting",
        "electrician",
        "roofing",
        "contractor",
        "landscap",
        "heating",
        "cooling",
        "garage door",
        "handyman",
        "pest",
        "cleaning",
        "locksmith",
    )
    if any(word in blob for word in clinic):
        return "Clinics/Dentists: pitch an automated Online Patient Booking & SMS Reminder Portal."
    if any(word in blob for word in restaurant):
        return "Restaurants: pitch a Zero-Commission Direct Online Ordering System."
    if any(word in blob for word in field):
        return (
            "Field services/contractors: pitch an Instant Price Estimator Widget "
            "& Mobile Dispatch App."
        )
    return "General business: pitch a modern 2026 Mobile-Speed Redesign."


def user_email_prompt(
    business_name: str,
    website: str,
    rating: str,
    niche: str,
    location: str,
) -> str:
    name = (business_name or "your business").strip()
    niche_label = (niche or "local businesses").strip()
    location_label = (location or "your area").strip()
    has_website = bool((website or "").strip())
    if has_website:
        opening = (
            f"I was checking out {name}'s website while searching for top "
            f"{niche_label} in {location_label}."
        )
        offer = classify_offer(niche_label, name)
        site = website.strip()
        flag = "true (they already have a site — upgrade it, do not sell a new one)"
    else:
        opening = (
            f"I was looking up top {niche_label} in {location_label} and noticed "
            f"{name} doesn't have a website yet."
        )
        offer = (
            "They have NO website. Pitch BUILDING a simple, modern, high-converting "
            "website so customers can find and contact them."
        )
        site = "No website listed"
        flag = "false"

    return f"""
Business Name: {name}
Website: {site}
Rating: {rating or "not available"} stars
Niche: {niche_label}
Location: {location_label}
HAS_WEBSITE: {flag}

Required opening sentence (use verbatim as sentence 1):
{opening}

Offer to pitch:
{offer}

Write a targeted US cold email. Body: 3–4 sentences max. JSON only.
""".strip()
