import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
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

MATCH_THRESHOLD = 50  # Minimum score out of 100 to trigger alert


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
            reasons.append(f"{category.upper()} (+{category_score}pt): {', '.join(found[:4])}")

    posting.match_score = min(total_score, 100)
    posting.matched_reasons = reasons
    return posting


# =====================================================================
# INGESTION MODULES
# =====================================================================


def fetch_reliefweb_jobs(limit: int = 40) -> List[JobPosting]:
    """Fetches real-time humanitarian and development listings from ReliefWeb API."""
    url = "https://api.reliefweb.int/v2/jobs?appname=search-monitor&preset=latest"

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

    headers = {"Content-Type": "application/json"}
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


def fetch_generic_rss(
    rss_url: str, source_name: str, org_default: str = "Various"
) -> List[JobPosting]:
    """Fetches job postings from standard RSS feeds (e.g., Idealist / NGO feeds)."""
    postings = []
    req = urllib.request.Request(
        rss_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            tree = ET.fromstring(response.read().decode("utf-8"))
            channel = tree.find("channel")
            if channel is not None:
                for item in channel.findall("item"):
                    title = item.findtext("title", "N/A")
                    link = item.findtext("link", "")
                    description = item.findtext("description", "")
                    pub_date = item.findtext("pubDate", "")

                    postings.append(
                        JobPosting(
                            title=title,
                            organization=org_default,
                            source=source_name,
                            url=link,
                            location="See Listing",
                            description=description,
                            posted_date=pub_date,
                        )
                    )
    except Exception as e:
        print(f"[WARN] RSS Feed ({source_name}) fetch skipped or unavailable: {e}")

    return postings


# =====================================================================
# MAIN EXECUTION PIPELINE
# =====================================================================


def run_job_monitor():
    print("=" * 70)
    print(
        f"RUNNING AUTOMATED JOB MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    print("=" * 70)

    all_postings: List[JobPosting] = []

    # 1. Fetch ReliefWeb API
    print("[1/2] Querying ReliefWeb v2 REST API...")
    rw_jobs = fetch_reliefweb_jobs(limit=50)
    print(f"      Retrieved {len(rw_jobs)} active listings.")
    all_postings.extend(rw_jobs)

    # 2. Add additional feeds if needed
    print("[2/2] Ingesting secondary feeds...")
    # Example placeholder for external feeds
    # idealist_jobs = fetch_generic_rss("https://www.idealist.org/en/feed/jobs", "Idealist RSS")
    # all_postings.extend(idealist_jobs)

    # 3. Score and Filter
    scored_jobs: List[JobPosting] = []
    for job in all_postings:
        scored = score_job(job)
        if scored.match_score >= MATCH_THRESHOLD:
            scored_jobs.append(scored)

    # Sort descending by match score
    scored_jobs.sort(key=lambda x: x.match_score, reverse=True)

    # 4. Display Ranked Output
    print("\n" + "=" * 70)
    print(
        f"MATCH RESULTS: {len(scored_jobs)} High-Probability Opportunities Found (Score >= {MATCH_THRESHOLD})"
    )
    print("=" * 70 + "\n")

    if not scored_jobs:
        print(
            "No listings exceeded the score threshold in this run. Try adjusting MATCH_THRESHOLD."
        )
        return

    for idx, job in enumerate(scored_jobs, 1):
        print(f"[{idx}] MATCH SCORE: {job.match_score}/100")
        print(f"    Title:        {job.title}")
        print(f"    Organization: {job.organization}")
        print(f"    Location:     {job.location}")
        print(f"    Source:       {job.source} | Date: {job.posted_date}")
        print(f"    URL:          {job.url}")
        print("    Match Factors:")
        for reason in job.matched_reasons:
            print(f"      - {reason}")
        print("-" * 70)


if __name__ == "__main__":
    run_job_monitor()