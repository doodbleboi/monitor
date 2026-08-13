import os
import smtplib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List
import requests

# =====================================================================
# CANDIDATE MATCH VECTOR CONFIGURATION
# =====================================================================

# Primary Domain Anchors - Essential non-profit, public sector, & program domains
DOMAIN_ANCHORS = [
    "program coordinator", "program manager", "project manager", "project coordinator",
    "small business", "business development", "grant coordinator", "grant administrator",
    "community development", "financial inclusion", "microfinance", "vsla", "silc",
    "village savings", "rural finance", "ngo", "nonprofit", "non-profit", "usaid",
    "public sector", "city of fort worth", "tarrant county", "city of dallas",
    "city of irving", "city of grapevine", "usajobs", "sba", "hud", "m&e",
    "monitoring and evaluation", "economic development", "social impact", "pslf"
]

POSITIVE_WEIGHTS = {
    "local_transit_corridor": {
        "score": 30,
        "keywords": [
            "fort worth", "grapevine", "irving", "dallas", "tarrant county",
            "dfw", "texas", "tx", "downtown fort worth"
        ],
    },
    "target_role_titles": {
        "score": 35,
        "keywords": [
            "program manager", "project manager", "program coordinator",
            "project coordinator", "program associate", "operations associate",
            "small business specialist", "business development specialist",
            "grant manager", "grant coordinator", "m&e specialist",
            "community outreach coordinator", "compliance specialist"
        ],
    },
    "impact_and_public_sector": {
        "score": 30,
        "keywords": [
            "ngo", "nonprofit", "non-profit", "public sector", "government",
            "usaid", "sba", "small business administration", "hud", "chemonics",
            "mercy corps", "kiva", "brac", "catholic relief services", "giz",
            "financial inclusion", "microfinance", "economic development",
            "community development", "pslf"
        ],
    },
    "operations_tech_enabled": {
        "score": 20,
        "keywords": [
            "excel", "spreadsheet", "data entry", "budget tracking",
            "invoice audit", "reconciliation", "reporting", "dashboard",
            "process improvement", "workflow", "crm", "salesforce", "hubspot",
            "ai tools", "google apps script", "python", "stata"
        ],
    },
    "location_remote": {
        "score": 15,
        "keywords": [
            "remote", "work from home", "telecommute", "us remote", "anywhere"
        ],
    },
}

EXCLUDED_KEYWORDS = [
    # 1. Hard Exclusion: Construction & Civil Engineering
    "construction", "civil engineer", "general contractor", "heavy equipment",
    "superintendent", "building inspector", "estimator", "hvac", "plumbing",
    "carpentry", "site supervisor", "safety officer - construction", "subcontractor",
    
    # 2. Hard Exclusion: Software Engineering & Senior Dev
    "software engineer", "software developer", "full stack", "full-stack",
    "backend engineer", "frontend engineer", "devops", "software architect",
    "machine learning", "react developer", "rails engineer", "data engineer",
    "qa engineer", "solutions architect", "web developer", ".net developer",
    
    # 3. Hard Exclusion: Commercial Sales & Wall St Banking
    "commercial bank", "investment banking", "wall street", "wealth management",
    "retail banking", "corporate finance", "mortgage broker", "private equity",
    "mortgage processor", "inside sales", "account executive dach", "brand designer"
]

MATCH_THRESHOLD = 40  # Clean score threshold for targeted opportunities


@dataclass
class JobPosting:
    title: str
    organization: str
    source: str
    url: str
    location: str
    description: str
    posted_date: str
    match_score: int = 0
    matched_reasons: List[str] = field(default_factory=list)


# =====================================================================
# SCORING ENGINE
# =====================================================================


def score_job(posting: JobPosting) -> JobPosting:
    text_corpus = f"{posting.title} {posting.description} {posting.location} {posting.organization}".lower()

    # 1. Check Hard Exclusion List
    for excluded in EXCLUDED_KEYWORDS:
        if excluded in text_corpus:
            posting.match_score = -100
            posting.matched_reasons.append(f"DISQUALIFIED: Matches excluded term '{excluded}'")
            return posting

    total_score = 0
    reasons = []

    # 2. Check for Domain Anchor Overlap
    has_domain_anchor = any(anchor in text_corpus for anchor in DOMAIN_ANCHORS)
    
    if not has_domain_anchor and posting.source not in ["USAJOBS", "ReliefWeb API"]:
        total_score -= 30
        reasons.append("PENALTY (-30pt): Lacks explicit Program/Public/Non-Profit domain anchor")

    # 3. Positive Keyword Scoring
    for category, config in POSITIVE_WEIGHTS.items():
        found = [kw for kw in config["keywords"] if kw in text_corpus]
        if found:
            category_score = config["score"]
            total_score += category_score
            reasons.append(f"{category.upper()} (+{category_score}pt): {', '.join(found[:4])}")

    posting.match_score = max(0, min(total_score, 100))
    posting.matched_reasons = reasons
    return posting


# =====================================================================
# INGESTION MODULES
# =====================================================================


def clean_html(raw_html: str) -> str:
    """Strips HTML tags for clean text scoring."""
    cleanr = re.compile("<.*?>")
    return re.sub(cleanr, "", raw_html or "")


def fetch_rss_feed(feed_url: str, source_name: str) -> List[JobPosting]:
    """Parses standard RSS XML feeds reliably."""
    postings = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }

    try:
        resp = requests.get(feed_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            xml_data = re.sub(r'xmlns="[^"]+"', "", resp.text)
            root = ET.fromstring(xml_data)

            items = root.findall(".//item") or root.findall(".//entry")
            for item in items:
                title = item.findtext("title") or "N/A"
                link = item.findtext("link") or item.findtext("guid") or ""
                description = item.findtext("description") or item.findtext("summary") or item.findtext("content") or ""
                pub_date = item.findtext("pubDate") or item.findtext("updated") or ""

                postings.append(
                    JobPosting(
                        title=title.strip(),
                        organization=source_name,
                        source=source_name,
                        url=link.strip(),
                        location="Remote / DFW",
                        description=clean_html(description),
                        posted_date=pub_date[:16] if pub_date else "",
                    )
                )
            print(f"      [{source_name}] Retrieved {len(postings)} listings.")
        else:
            print(f"      [{source_name}] HTTP Status {resp.status_code}")
    except Exception as e:
        print(f"      [{source_name}] Ingestion failed: {e}")

    return postings


def fetch_usajobs_feed() -> List[JobPosting]:
    """Queries USAJOBS public Search API for non-construction Program/Project/Small Business roles in DFW & Remote."""
    postings = []
    # Search keywords tailored for public administration, small business, and program management
    keywords = ["Program Manager", "Project Manager", "Small Business", "Grant Specialist"]
    
    headers = {
        "User-Agent": "dresetar@gmail.com",
        "Accept": "application/json"
    }

    for kw in keywords:
        url = f"https://data.usajobs.gov/api/search?Keyword={kw}&LocationName=Fort Worth, Texas"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("SearchResult", {}).get("SearchResultItems", [])
                for item in items:
                    matched = item.get("MatchedObjectDescriptor", {})
                    title = matched.get("PositionTitle", "N/A")
                    org = matched.get("OrganizationName", "US Federal Government")
                    job_url = matched.get("PositionURI", "")
                    
                    loc_arr = matched.get("PositionLocation", [])
                    loc_name = loc_arr[0].get("LocationName", "Fort Worth, TX") if loc_arr else "Fort Worth, TX"
                    summary = clean_html(matched.get("UserArea", {}).get("Details", {}).get("JobSummary", ""))
                    pub_date = matched.get("PublicationStartDate", "")[:10]

                    postings.append(
                        JobPosting(
                            title=title,
                            organization=org,
                            source="USAJOBS",
                            url=job_url,
                            location=loc_name,
                            description=summary,
                            posted_date=pub_date,
                        )
                    )
        except Exception as e:
            print(f"      [USAJOBS] Query failed for '{kw}': {e}")

    print(f"      [USAJOBS] Total retrieved federal listings: {len(postings)}")
    return postings


def fetch_remotive_api() -> List[JobPosting]:
    """Queries Remotive's public remote jobs API."""
    postings = []
    url = "https://remotive.com/api/remote-jobs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            for job in jobs:
                title = job.get("title", "N/A")
                company = job.get("company_name", "Unknown")
                job_url = job.get("url", "")
                location = job.get("candidate_required_location", "Remote")
                description = clean_html(job.get("description", ""))
                pub_date = job.get("publication_date", "")[:10]

                postings.append(
                    JobPosting(
                        title=title,
                        organization=company,
                        source="Remotive API",
                        url=job_url,
                        location=location,
                        description=description,
                        posted_date=pub_date,
                    )
                )
            print(f"      [Remotive API] Retrieved {len(postings)} listings.")
        else:
            print(f"      [Remotive API] HTTP Status {resp.status_code}")
    except Exception as e:
        print(f"      [Remotive API] Ingestion failed: {e}")

    return postings


def fetch_reliefweb_api() -> List[JobPosting]:
    """Queries ReliefWeb API with clean parameters."""
    postings = []
    url = "https://api.reliefweb.int/v2/jobs?appname=job-search-monitor&limit=50&preset=latest"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("data", []):
                fields = item.get("fields", {})
                title = fields.get("title", "N/A")
                body = clean_html(fields.get("body", ""))
                job_url = fields.get("url", "")
                sources = fields.get("source", [])
                org = sources[0].get("name", "Unknown") if sources else "Unknown"
                countries = fields.get("country", [])
                loc_list = [c.get("name", "") for c in countries] if countries else ["Remote / Global"]
                created_date = fields.get("date", {}).get("created", "")[:10]

                postings.append(
                    JobPosting(
                        title=title,
                        organization=org,
                        source="ReliefWeb API",
                        url=job_url,
                        location=", ".join(loc_list),
                        description=body,
                        posted_date=created_date,
                    )
                )
            print(f"      [ReliefWeb API] Retrieved {len(postings)} listings.")
        else:
            print(f"      [ReliefWeb API] HTTP Status {resp.status_code}")
    except Exception as e:
        print(f"      [ReliefWeb API] Ingestion failed: {e}")

    return postings


def fetch_all_sources() -> List[JobPosting]:
    """Aggregates listings across Federal, Local Transit, Remote, and Non-Profit sources."""
    all_jobs = []

    print("  -> Querying USAJOBS Federal Search...")
    all_jobs.extend(fetch_usajobs_feed())

    print("  -> Querying Remotive Remote Jobs API...")
    all_jobs.extend(fetch_remotive_api())

    print("  -> Querying WeWorkRemotely RSS Feed...")
    all_jobs.extend(fetch_rss_feed("https://weworkremotely.com/remote-jobs.rss", "WeWorkRemotely"))

    print("  -> Querying ReliefWeb API...")
    all_jobs.extend(fetch_reliefweb_api())

    return all_jobs


# =====================================================================
# EMAIL NOTIFICATION DISPATCHER
# =====================================================================


def send_email_alert(matched_jobs: List[JobPosting]):
    """Sends an email alert via Gmail SMTP using environment variables."""
    sender_email = os.environ.get("SENDER_EMAIL", "dresetar@gmail.com")
    sender_password = os.environ.get("SENDER_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL", "raveonette85@gmail.com")

    if not sender_password:
        print("\n[INFO] SENDER_PASSWORD secret not set in environment. Skipping email dispatch.")
        return

    print(f"\n[EMAIL] Dispatching alert to {recipient_email} for {len(matched_jobs)} match(es)...")

    body_lines = [
        f"Automated Job Search Monitor identified {len(matched_jobs)} targeted opportunity(ies):\n",
        "=" * 70,
        "",
    ]

    for idx, job in enumerate(matched_jobs, 1):
        body_lines.append(f"[{idx}] {job.title} — {job.organization}")
        body_lines.append(f"    Source:      {job.source}")
        body_lines.append(f"    Location:    {job.location}")
        body_lines.append(f"    Match Score: {job.match_score}/100")
        body_lines.append(f"    URL:         {job.url}")
        body_lines.append("    Match Factors:")
        for reason in job.matched_reasons:
            body_lines.append(f"      - {reason}")
        body_lines.append("-" * 70)

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = f"🚨 Target Job Alert: {len(matched_jobs)} DFW & Remote Position(s) Found"
    msg.attach(MIMEText("\n".join(body_lines), "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print(f"[EMAIL] Alert successfully sent to {recipient_email}!")
    except Exception as e:
        print(f"[ERROR] Failed to send email alert: {e}")


# =====================================================================
# MAIN EXECUTION PIPELINE
# =====================================================================


def run_job_monitor():
    print("=" * 70)
    print(f"RUNNING TARGETED JOB MONITOR (DFW / FEDERAL / REMOTE) — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # 1. Fetch Listings
    print("[1/2] Fetching job listings across feeds...")
    raw_postings = fetch_all_sources()
    print(f"      Total retrieved raw listings: {len(raw_postings)}")

    # 2. Score Listings
    print("[2/2] Running scoring and filtering engine...")
    scored_jobs: List[JobPosting] = []
    seen_urls = set()

    for job in raw_postings:
        if job.url in seen_urls:
            continue
        seen_urls.add(job.url)

        scored = score_job(job)
        if scored.match_score >= MATCH_THRESHOLD:
            scored_jobs.append(scored)

    # Sort descending by match score
    scored_jobs.sort(key=lambda x: x.match_score, reverse=True)

    # 3. Output Results
    print("\n" + "=" * 70)
    print(f"RESULTS: {len(scored_jobs)} High-Probability Matches Found (Score >= {MATCH_THRESHOLD})")
    print("=" * 70 + "\n")

    if not scored_jobs:
        print("No new listings met the score threshold during this run.")
        return

    for idx, job in enumerate(scored_jobs, 1):
        print(f"[{idx}] SCORE: {job.match_score}/100 | {job.title} ({job.organization}) [{job.source}]")
        print(f"    Location: {job.location}")
        print(f"    URL:      {job.url}")
        print("-" * 70)

    # 4. Dispatch Email Alert
    send_email_alert(scored_jobs)


if __name__ == "__main__":
    run_job_monitor()
