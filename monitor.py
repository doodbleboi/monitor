import os
import re
import json
import asyncio
import smtplib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List
import requests
from playwright.async_api import async_playwright, BrowserContext, Page

# =====================================================================
# CANDIDATE MATCH VECTOR CONFIGURATION
# =====================================================================

DOMAIN_ANCHORS = [
    "program manager", "project manager", "program coordinator", "project coordinator",
    "program officer", "operations manager", "grant manager", "grant administrator",
    "financial inclusion", "microfinance", "microinsurance", "rural finance",
    "blended finance", "gender-lens investing", "gender lens", "gli", "2x challenge",
    "2x criteria", "2x global", "concessional finance", "economic recovery",
    "livelihoods", "impact investing", "development finance", "dfi", "alternative credit",
    "digital identity", "credit scoring", "economic development", "cash transfer"
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

MATCH_THRESHOLD = 30

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
        total_score -= 20
        reasons.append("Lacks explicit domain anchor (-20pt)")

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
# INGESTION ENGINES
# =====================================================================

def fetch_reliefweb() -> List[JobPosting]:
    """Queries ReliefWeb REST API with standard parameters."""
    jobs = []
    url = "https://api.reliefweb.int/v1/jobs"
    params = {
        "appname": "david-monitor-pipeline",
        "query[value]": "finance OR microfinance OR inclusion OR cash",
        "limit": 30,
        "profile": "full",
        "sort[]": "date:desc"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                f = item.get("fields", {})
                org = f.get("source", [{}])[0].get("name", "International NGO")
                countries = f.get("country", [])
                loc = countries[0].get("name", "Global") if countries else "Remote / International"
                body = f.get("body", "") or f.get("body-html", "")
                jobs.append(JobPosting(
                    title=clean_text(f.get("title", "N/A")),
                    organization=clean_text(org),
                    source="ReliefWeb API",
                    url=f.get("url", "https://reliefweb.int"),
                    location=loc,
                    description=clean_text(body)[:4000],
                    posted_date=f.get("date", {}).get("created", "")[:10]
                ))
            print(f"[+] ReliefWeb API: Ingested {len(jobs)} records.")
    except Exception as e:
        print(f"[-] ReliefWeb fetch failed: {str(e)}")
    return jobs

def fetch_rss_feed(feed_url: str, source_name: str) -> List[JobPosting]:
    """Fallback XML RSS parser for international remote feeds."""
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(feed_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            xml_data = re.sub(r'xmlns="[^"]+"', "", resp.text)
            root = ET.fromstring(xml_data)
            items = root.findall(".//item") or root.findall(".//entry")
            for item in items:
                title = item.findtext("title") or "N/A"
                link = item.findtext("link") or item.findtext("guid") or ""
                desc = item.findtext("description") or item.findtext("summary") or ""
                pub_date = item.findtext("pubDate") or item.findtext("updated") or ""

                jobs.append(JobPosting(
                    title=clean_text(title),
                    organization=source_name,
                    source=source_name,
                    url=link.strip(),
                    location="Remote / Global",
                    description=clean_text(desc)[:4000],
                    posted_date=pub_date[:16] if pub_date else ""
                ))
            print(f"[+] RSS Feed ({source_name}): Ingested {len(jobs)} records.")
    except Exception as e:
        print(f"[-] RSS Feed ({source_name}) notice: {str(e)}")
    return jobs

async def scrape_devex_resilient(context: BrowserContext) -> List[JobPosting]:
    jobs = []
    page = await context.new_page()
    email = os.getenv("DEVEX_EMAIL")
    password = os.getenv("DEVEX_PASSWORD")

    try:
        target_url = "https://www.devex.com/jobs/search"
        await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)

        if "login" in page.url and email and password:
            await page.fill('input[type="email"], input[name="email"]', email)
            await page.fill('input[type="password"], input[name="password"]', password)
            await page.click('button[type="submit"], input[type="submit"]')
            await page.wait_for_timeout(3000)
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)

        # Resilient element discovery using anchor links
        await page.wait_for_timeout(4000)
        link_elements = await page.query_selector_all("a[href*='/jobs/']")
        seen_links = set()

        for link in link_elements:
            href = await link.get_attribute("href")
            text = clean_text(await link.inner_text())
            if href and "/jobs/" in href and text and len(text) > 5 and href not in seen_links:
                seen_links.add(href)
                full_url = href if href.startswith("http") else f"https://www.devex.com{href}"
                jobs.append(JobPosting(
                    title=text,
                    organization="Devex Node",
                    source="Devex (Auth)",
                    url=full_url,
                    location="Global / Remote",
                    description=""
                ))
        print(f"[+] Devex Resilient Scraper: Extracted {len(jobs)} listings.")
    except Exception as e:
        print(f"[-] Devex scrape notice: {str(e)}")
    finally:
        await page.close()
    return jobs

# =====================================================================
# EMAIL DISPATCH
# =====================================================================

def send_email_digest(matches: List[JobPosting], total_ingested: int, top_unfiltered: List[JobPosting]):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL")

    print(f"[*] Email Config Status: SENDER={'Set' if sender else 'MISSING'} | RECIPIENT={'Set' if recipient else 'MISSING'} | PASSWORD={'Set' if password else 'MISSING'}")

    if not sender or not password or not recipient:
        print("[-] Missing email credentials. Skipping SMTP send.")
        return

    msg = MIMEMultipart("alternative")

    if not matches:
        msg["Subject"] = f"🧪 [Pipeline Health Check] {total_ingested} Ingested (0 Threshold Matches)"
        rows = "<p style='color: #c0392b;'><strong>No jobs met the 30-point match threshold today.</strong> Showing top 5 raw listings captured:</p>"
        for j in top_unfiltered[:5]:
            rows += f"""
            <div style="margin-bottom: 12px; padding: 12px; border-left: 4px solid #7f8c8d; background-color: #f4f6f7;">
                <strong><a href="{j.url}" style="color: #2c3e50; text-decoration: none;">{j.title}</a></strong><br>
                <span>{j.organization} | {j.location} | Source: {j.source} | Score: {j.match_score}/100</span>
            </div>
            """
    else:
        msg["Subject"] = f"🎯 Daily Pipeline: {len(matches)} Microfinance & GLI Matches"
        rows = ""
        for j in matches:
            reasons_html = "".join(f"<li>{r}</li>" for r in j.matched_reasons)
            rows += f"""
            <div style="margin-bottom: 18px; padding: 15px; border-left: 4px solid #16a085; background-color: #f9fbfb;">
                <h3 style="margin: 0 0 6px 0;">
                    <a href="{j.url}" style="color: #16a085; text-decoration: none;">{j.title}</a>
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
        <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #333; max-width: 680px;">
            <h2>Daily Financial Inclusion &amp; GLI Pipeline</h2>
            <p>Total raw listings evaluated: <strong>{total_ingested}</strong> | Qualified matches: <strong>{len(matches)}</strong></p>
            <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
            {rows}
        </body>
    </html>
    """
    msg.attach(MIMEText(html_body, "html"))

    try:
        print(f"[*] Dispatching digest to {recipient} via smtp.gmail.com...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        print(f"[+] SUCCESS: Digest successfully delivered to {recipient}.")
    except Exception as e:
        print(f"[-] SMTP ERROR: Failed to dispatch -> {str(e)}")

# =====================================================================
# MAIN PIPELINE
# =====================================================================

async def main():
    print("[*] Starting Monitor Pipeline...")
    all_jobs: List[JobPosting] = []

    # 1. Ingest ReliefWeb REST API
    all_jobs.extend(fetch_reliefweb())

    # 2. Ingest Remote Feeds
    all_jobs.extend(fetch_rss_feed("https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss", "WeWorkRemotely"))
    all_jobs.extend(fetch_rss_feed("https://remotive.com/remote-jobs/feed", "Remotive"))

    # 3. Ingest Authenticated Playwright Scrapers
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        
        devex_results = await scrape_devex_resilient(context)
        all_jobs.extend(devex_results)
        
        await context.close()
        await browser.close()

    # 4. Score and Rank
    scored_all = [score_job(j) for j in all_jobs]
    scored_all.sort(key=lambda x: x.match_score, reverse=True)
    matched = [j for j in scored_all if j.match_score >= MATCH_THRESHOLD]

    print(f"\n[*] Pipeline Summary: Ingested {len(all_jobs)} items. Matches >= {MATCH_THRESHOLD} pts: {len(matched)}")

    with open("authenticated_listings.json", "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in scored_all], f, indent=2)

    # 5. Send Digest
    send_email_digest(matched, len(all_jobs), scored_all)

if __name__ == "__main__":
    asyncio.run(main())
