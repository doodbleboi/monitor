import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

# =====================================================================
# CANDIDATE MATCH VECTOR CONFIGURATION
# =====================================================================

POSITIVE_WEIGHTS = {
    "domain_microfinance": {
        "score": 30,
        "keywords": [
            "financial inclusion",
            "microfinance",
            "vsla",
            "silc",
            "village savings",
            "community loans",
            "credit scoring",
            "underwriting",
            "capital deployment",
            "microloan",
            "rural finance",
        ],
    },
    "technical_automation": {
        "score": 25,
        "keywords": [
            "python",
            "apps script",
            "google apps script",
            "sql",
            "sas",
            "stata",
            "prompt engineering",
            "ai-assisted",
            "automation",
            "dashboard",
            "kpi",
            "m&e",
            "monitoring and evaluation",
            "data analytics",
        ],
    },
    "location_remote": {
        "score": 25,
        "keywords": [
            "remote",
            "work from home",
            "telecommute",
            "us remote",
            "dallas",
            "fort worth",
            "texas",
            "dfw",
        ],
    },
    "impact_sector": {
        "score": 20,
        "keywords": [
            "ngo",
            "nonprofit",
            "non-profit",
            "usaid",
            "chemonics",
            "dai",
            "mercy corps",
            "kiva",
            "brac",
            "catholic relief services",
            "giz",
            "accelerator",
            "grant",
        ],
    },
}

EXCLUDED_KEYWORDS = [
    "commercial bank",
    "investment banking",
    "wall street",
    "wealth management",
    "retail banking",
    "corporate finance",
    "mortgage broker",
    "private equity associate",
]

MATCH_THRESHOLD = 50  # Minimum match score out of 100 to trigger alert


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

    # 1. Check Exclusion List
    for excluded in EXCLUDED_KEYWORDS:
        if excluded in text_corpus:
            posting.match_score = -100
            posting.matched_reasons.append(
                f"DISQUALIFIED: Matches excluded term '{excluded}'"
            )
            return posting

    total_score = 0
    reasons = []

    # 2. Positive Keyword Matching
    for category, config in POSITIVE_WEIGHTS.items():
        found = [kw for kw in config["keywords"] if kw in text_corpus]
        if found:
            category_score = config["score"]
            total_score += category_score
            reasons.append(
                f"{category.upper()} (+{category_score}pt): {', '.join(found[:4])}"
            )

    posting.match_score = min(total_score, 100)
    posting.matched_reasons = reasons
    return posting


# =====================================================================
# INGESTION MODULES
# =====================================================================


def fetch_reliefweb_jobs(limit: int = 50) -> List[JobPosting]:
    """Fetches real-time listings from ReliefWeb API with custom User-Agent headers."""
    url = "https://api.reliefweb.int/v2/jobs?appname=job-search-monitor&preset=latest"

    payload = {
        "limit": limit,
        "fields": {
            "include": [
                "title",
                "body",
                "source",
                "url",
                "country",
                "date",
                "type",
            ]
        },
        "query": {
            "value": "financial inclusion OR microfinance OR data OR automation OR program coordinator"
        },
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (JobSearchMonitor/1.0)",
    }
    
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )

    postings = []
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            for item in data.get("data", []):
                fields = item.get("fields", {})
                title = fields.get("title", "N/A")
                body = fields.get("body", "")
                job_url = fields.get("url", "")
                sources = fields.get("source", [])
                org = sources[0].get("name", "Unknown") if sources else "Unknown"

                countries = fields.get("country", [])
                loc_list = (
                    [c.get("name", "") for c in countries]
                    if countries
                    else ["Remote / Unspecified"]
                )
                location = ", ".join(loc_list)
                created_date = fields.get("date", {}).get("created", "")

                postings.append(
                    JobPosting(
                        title=title,
                        organization=org,
                        source="ReliefWeb API",
                        url=job_url,
                        location=location,
                        description=body,
                        posted_date=created_date[:10] if created_date else "",
                    )
                )
    except Exception as e:
        print(f"[ERROR] ReliefWeb Ingestion Failed: {e}")

    return postings


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

    # Construct Email Content
    body_lines = [
        f"Automated Job Search Monitor identified {len(matched_jobs)} high-match opportunity(ies):\n",
        "=" * 70,
        "",
    ]

    for idx, job in enumerate(matched_jobs, 1):
        body_lines.append(f"[{idx}] {job.title} — {job.organization}")
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
    msg["Subject"] = f"🚨 Job Search Alert: {len(matched_jobs)} High Match Position(s) Found"
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
    print(f"RUNNING AUTOMATED JOB MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # 1. Fetch Listings
    print("[1/2] Fetching job listings from ReliefWeb API...")
    raw_postings = fetch_reliefweb_jobs(limit=50)
    print(f"      Retrieved {len(raw_postings)} active listings.")

    # 2. Score Listings
if __name__ == "__main__":
    run_job_monitor()
