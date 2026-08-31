import os
import re
import json
import asyncio
import smtplib
from dataclasses import dataclass, field, asdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List
import requests
from playwright.async_api import async_playwright, BrowserContext, Page

# =====================================================================
# CONFIGURATION & KEYWORD VECTORS
# =====================================================================

DOMAIN_ANCHORS = [
    "program manager", "project manager", "program coordinator", "project coordinator",
    "program officer", "operations manager", "grant manager", "grant administrator",
    "financial inclusion", "microfinance", "microinsurance", "rural finance",
    "blended finance", "gender-lens investing", "gender lens", "gli", "2x challenge",
    "2x criteria", "2x global", "concessional finance", "economic recovery",
    "livelihoods", "impact investing", "development finance", "dfi", "alternative credit"
]

POSITIVE_WEIGHTS = {
    "gender_lens_and_2x_criteria": {
        "score": 35,
        "keywords": [
            "gender-lens investing", "gender lens", "gli", "2x challenge", "2x criteria",
            "2x global", "2x qualification", "women's economic empowerment",
            "gender mainstreaming", "female entrepreneurship"
        ],
    },
    "microfinance_and_financial_inclusion": {
        "score": 30,
        "keywords": [
            "financial inclusion", "microfinance", "microinsurance", "rural finance",
            "agricultural finance", "vsla", "silc", "credit scoring", "alternative data",
            "underbanked", "unbanked", "digital kyc", "mobile money", "msme lending"
        ],
    },
    "blended_finance_and_dfis": {
        "score": 25,
        "keywords": [
            "blended finance", "concessional capital", "development finance institution",
            "dfi", "private capital mobilization", "technical assistance facility",
            "first-loss", "guarantee facility", "findev", "bii", "fmo", "eib", "ifc"
        ],
    },
    "automation_me_and_data_synthesis": {
        "score": 20,
        "keywords": [
            "monitoring and evaluation", "m&e", "mel", "knowledge management",
            "workflow automation", "python", "data synthesis", "prompt engineering",
            "impact measurement", "financial modeling", "quantitative analysis"
        ],
    },
}

EXCLUDED_KEYWORDS = [
    "construction", "civil engineer", "general contractor", "heavy equipment",
    "superintendent", "building inspector", "estimator", "hvac", "plumbing",
    "software engineer", "software developer", "full stack", "full-stack",
    "backend engineer", "frontend engineer", "devops",
    "commercial bank", "investment banking", "wall street", "wealth management",
    "retail banking", "mortgage broker", "defense contractor", "dod security clearance"
]

MATCH_THRESHOLD = 35

@dataclass
class JobPosting:
    title: str
    organization: str
    source: str
    url: str
    location: str
    description: str
    posted_date: str = ""
    match_score: int = 0
    matched_reasons: List[str] = field(default_factory=list)

def clean_text(raw_text: str) -> str:
    cleanr = re.compile(r"<.*?>")
    cleaned = re.sub(cleanr, "", raw_text or "")
    return re.sub(r"\s+", " ", cleaned).strip()

def score_job(posting: JobPosting) -> JobPosting:
    text_corpus = f"{posting.title} {posting.description} {posting.location} {posting.organization}".lower()

    for excluded in EXCLUDED_KEYWORDS:
        if excluded in text_corpus:
            posting.match_score = -100
            posting.matched_reasons.append(f"Excluded term: {excluded}")
            return posting

    total_score = 0
    reasons = []

    has_anchor = any(anchor in text_corpus for anchor in DOMAIN_ANCHORS)
    if not has_anchor and posting.source not in ["ReliefWeb API", "2X Global", "Devex (Auth)"]:
        total_score -= 25
        reasons.append("Lacks explicit domain anchor (-25pt)")

    for category, config in POSITIVE_WEIGHTS.items():
        found = [kw for kw in config["keywords"] if kw in text_corpus]
        if found:
            category_score = config["score"]
            total_score += category_score
            reasons.append(f"{category.upper()} (+{category_score}pt): {', '.join(found[:3])}")

    posting.match_score = max(0, min(total_score, 100))
    posting.matched_reasons = reasons
    return posting

# =====================================================================
# PLAYWRIGHT SCRAPERS
# =====================================================================

async def scrape_devex(context: BrowserContext) -> List[JobPosting]:
    jobs = []
    page = await context.new_page()
    email = os.getenv("DEVEX_EMAIL")
    password = os.getenv("DEVEX_PASSWORD")

    try:
        target_url = "https://www.devex.com/jobs/search?filter%5Bkeywords%5D%5B%5D=financial%20inclusion"
        await page.goto(target_url, wait_until="domcontentloaded")

        if "login" in page.url and email and password:
            await page.fill('input[type="email"], input[name="email"]', email)
            await page.fill('input[type="password"], input[name="password"]', password)
            await page.click('button[type="submit"], input[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await page.goto(target_url, wait_until="domcontentloaded")

        await page.wait_for_selector(".job-card, .job-item, [data-qa='job-card']", timeout=8000)
        cards = await page.query_selector_all(".job-card, .job-item, [data-qa='job-card']")

        for card in cards[:15]:
            title_elem = await card.query_selector("h3, .title, [data-qa='job-title']")
            org_elem = await card.query_selector(".organization, .company, [data-qa='job-organization']")
            loc_elem = await card.query_selector(".location, [data-qa='job-location']")
            link_elem = await card.query_selector("a[href*='/jobs/']")
            desc_elem = await card.query_selector(".summary, .description, p")

            title = clean_text(await title_elem.inner_text()) if title_elem else "N/A"
            org = clean_text(await org_elem.inner_text()) if org_elem else "Devex Partner"
            loc = clean_text(await loc_elem.inner_text()) if loc_elem else "Remote / Global Node"
            desc = clean_text(await desc_elem.inner_text()) if desc_elem else ""
            
            url = "https://www.devex.com"
            if link_elem:
                href = await link_elem.get_attribute("href")
                if href:
                    url = href if href.startswith("http") else f"https://www.devex.com{href}"

            jobs.append(JobPosting(title=title, organization=org, source="Devex (Auth)", url=url, location=loc, description=desc))
    except Exception as e:
        print(f"[-] Devex scrape notice: {str(e)}")
    finally:
        await page.close()
    return jobs

async def scrape_2xglobal(context: BrowserContext) -> List[JobPosting]:
    jobs = []
    page = await context.new_page()
    email = os.getenv("TWO_X_GLOBAL_EMAIL")
    password = os.getenv("TWO_X_GLOBAL_PASSWORD")

    try:
        portal_url = "https://www.2xglobal.org/member-portal/opportunities"
        await page.goto(portal_url, wait_until="domcontentloaded")

        if ("login" in page.url or await page.query_selector('input[type="password"]')) and email and password:
            await page.fill('input[type="email"], input[name="email"], input[name="username"]', email)
            await page.fill('input[type="password"], input[name="password"]', password)
            await page.click('button[type="submit"], input[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await page.goto(portal_url, wait_until="domcontentloaded")

        await page.wait_for_selector(".opportunity-item, .post-item, .card", timeout=6000)
        items = await page.query_selector_all(".opportunity-item, .post-item, .card")

        for item in items[:15]:
            title_elem = await item.query_selector("h2, h3, .title")
            org_elem = await item.query_selector(".organization, .meta-org, .author")
            link_elem = await item.query_selector("a")
            body_elem = await item.query_selector(".content, .body, p")

            title = clean_text(await title_elem.inner_text()) if title_elem else "GLI Project Mandate"
            org = clean_text(await org_elem.inner_text()) if org_elem else "2X Global Member Fund"
            desc = clean_text(await body_elem.inner_text()) if body_elem else ""

            url = "https://www.2xglobal.org"
            if link_elem:
                href = await link_elem.get_attribute("href")
                if href:
                    url = href if href.startswith("http") else f"https://www.2xglobal.org{href}"

            jobs.append(JobPosting(title=title, organization=org, source="2X Global", url=url, location="Global / Remote", description=desc))
    except Exception as e:
        print(f"[-] 2X Global scrape notice: {str(e)}")
    finally:
        await page.close()
    return jobs

def fetch_reliefweb() -> List[JobPosting]:
    jobs = []
    url = "https://api.reliefweb.int/v1/jobs"
    params = {
        "appname": "career-monitor",
        "query[value]": "financial inclusion OR microfinance OR microinsurance OR blended finance",
        "limit": 20,
        "profile": "full",
        "sort[]": "date:desc"
    }
    try:
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                f = item.get("fields", {})
                org = f.get("source", [{}])[0].get("name", "International NGO")
                countries = f.get("country", [])
                loc = countries[0].get("name", "Global") if countries else "Remote / International"
                jobs.append(JobPosting(
                    title=f.get("title", "N/A"),
                    organization=org,
                    source="ReliefWeb API",
                    url=f.get("url", ""),
                    location=loc,
                    description=clean_text(f.get("body", "") or f.get("body-html", ""))
                ))
    except Exception as e:
        print(f"[-] ReliefWeb error: {str(e)}")
    return jobs

# =====================================================================
# EMAIL DISPATCH
# =====================================================================

def send_email_digest(matches: List[JobPosting]):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL")

    if not sender or not password or not recipient:
        print("[-] Email secrets not fully configured. Skipping dispatch.")
        return

    if not matches:
        print("[*] No matches above threshold today. No email dispatched.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Daily Pipeline: {len(matches)} Microfinance & GLI Matches"
    msg["From"] = sender
    msg["To"] = recipient

    rows = ""
    for j in matches:
        reasons_html = "".join(f"<li>{r}</li>" for r in j.matched_reasons)
        rows += f"""
        <div style="margin-bottom: 20px; padding: 15px; border-left: 4px solid #0056b3; background-color: #f8f9fa;">
            <h3 style="margin: 0 0 6px 0; color: #111;">
                <a href="{j.url}" style="color: #0056b3; text-decoration: none;">{j.title}</a>
            </h3>
            <p style="margin: 0 0 8px 0; font-size: 14px; color: #555;">
                <strong>{j.organization}</strong> | {j.location} | <em>{j.source}</em> | <strong>Score: {j.match_score}/100</strong>
            </p>
            <ul style="margin: 0; padding-left: 20px; font-size: 13px; color: #333;">
                {reasons_html}
            </ul>
        </div>
        """

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
            <h2>Daily Financial Inclusion &amp; GLI Opportunities</h2>
            <p>Found <strong>{len(matches)}</strong> high-yield roles exceeding the matching threshold:</p>
            {rows}
        </body>
    </html>
    """
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        print(f"[+] Successfully sent digest with {len(matches)} matches to {recipient}.")
    except Exception as e:
        print(f"[-] SMTP send failed: {str(e)}")

# =====================================================================
# MAIN WORKFLOW
# =====================================================================

async def main():
    all_jobs = []
    
    # 1. API fetch
    all_jobs.extend(fetch_reliefweb())

    # 2. Authenticated Playwright scraping
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        
        devex_jobs = await scrape_devex(context)
        two_x_jobs = await scrape_2xglobal(context)
        
        all_jobs.extend(devex_jobs)
        all_jobs.extend(two_x_jobs)
        
        await context.close()
        await browser.close()

    # 3. Score & Filter
    matched = [score_job(j) for j in all_jobs if score_job(j).match_score >= MATCH_THRESHOLD]
    matched.sort(key=lambda x: x.match_score, reverse=True)

    print(f"[*] Processed {len(all_jobs)} listings. Found {len(matched)} matching roles.")

    # 4. Save JSON and send digest
    with open("authenticated_listings.json", "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in matched], f, indent=2)

    send_email_digest(matched)

if __name__ == "__main__":
    asyncio.run(main())
