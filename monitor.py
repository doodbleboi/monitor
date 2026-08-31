import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import requests

# =====================================================================
# CANDIDATE MATCH VECTOR CONFIGURATION (TAILORED FOR DAVID RESETAR)
# Target: Financial Inclusion, Microfinance, Microinsurance, 2X Criteria / GLI
# =====================================================================

DOMAIN_ANCHORS = [
    "program manager", "project manager", "program coordinator", "project coordinator",
    "program officer", "operations manager", "grant manager", "grant administrator",
    "financial inclusion", "microfinance", "microinsurance", "rural finance",
    "blended finance", "gender-lens investing", "gender lens", "gli", "2x challenge",
    "2x criteria", "2x global", "concessional finance", "economic recovery",
    "livelihoods", "impact investing", "development finance", "dfi", "fintech for good",
    "alternative credit", "digital identity", "credit scoring"
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
    posted_date: str
    match_score: int = 0
    matched_reasons: List[str] = field(default_factory=list)


# =====================================================================
# SCORING ENGINE
# =====================================================================

def clean_html(raw_html: str) -> str:
    """Strips HTML tags for clean text scoring."""
    cleanr = re.compile(r"<.*?>")
    return re.sub(cleanr, "", raw_html or "")


def score_job(posting: JobPosting) -> JobPosting:
    text_corpus = f"{posting.title} {posting.description} {posting.location} {posting.organization}".lower()

    # 1. Hard Exclusion Check
    for excluded in EXCLUDED_KEYWORDS:
        if excluded in text_corpus:
            posting.match_score = -100
            posting.matched_reasons.append(f"DISQUALIFIED: Matches excluded term '{excluded}'")
            return posting

    total_score = 0
    reasons = []

    # 2. Domain Anchor Validation
    has_domain_anchor = any(anchor in text_corpus for anchor in DOMAIN_ANCHORS)
    if not has_domain_anchor and posting.source not in ["ReliefWeb API", "2X Global", "Devex"]:
        total_score -= 25
        reasons.append("PENALTY (-25pt): Lacks explicit Development/Inclusion domain anchor")

    # 3. Positive Weighted Category Scoring
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
# INGESTION MODULES
# =====================================================================

def fetch_reliefweb_api() -> List[JobPosting]:
    """Queries ReliefWeb REST API for Financial Inclusion & Economic Recovery listings."""
    postings = []
    url = "https://api.reliefweb.int/v1/jobs"
    params = {
        "appname": "david-career-monitor",
        "query[value]": "financial inclusion OR microfinance OR microinsurance OR economic recovery OR blended finance",
        "limit": 20,
        "profile": "full",
        "sort[]": "date:desc"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=12)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                fields = item.get("fields", {})
                title = fields.get("title", "N/A")
                org_info = fields.get("source", [{}])[0]
                org = org_info.get("name", "International NGO")
                job_url = fields.get("url", "")
                
                # Extract location safely
                cities = fields.get("city", [])
                countries = fields.get("country", [])
                loc = "Remote / International"
                if countries:
                    loc = countries[0].get("name", "Global")
                if cities:
                    loc = f"{cities[0].get('name')}, {loc}"

                body = fields.get("body", "") or fields.get("body-html", "")
                date_str = fields.get("date", {}).get("created", "")

                postings.append(
                    JobPosting(
                        title=title.strip(),
                        organization=org.strip(),
                        source="ReliefWeb API",
                        url=job_url.strip(),
                        location=loc,
                        description=clean_html(body),
                        posted_date=date_str[:10] if date_str else ""
                    )
                )
            print(f"      [ReliefWeb API] Ingested {len(postings)} positions.")
    except Exception as e:
        print(f"      [ERROR] ReliefWeb fetch failed: {str(e)}")

    return postings


def fetch_rss_feed(feed_url: str, source_name: str) -> List[JobPosting]:
    """Parses standard RSS XML feeds reliably."""
    postings = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Monitor/1.0"}

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
                        location="Remote / Global Node",
                        description=clean_html(description),
                        posted_date=pub_date[:16] if pub_date else "",
                    )
                )
            print(f"      [{source_name}] Ingested {len(postings)} entries.")
    except Exception as e:
        print(f"      [ERROR] Failed to fetch {source_name}: {str(e)}")

    return postings


# =====================================================================
# SYSTEM PIPELINE EXECUTION
# =====================================================================

def execution_pipeline():
    print("[*] Initiating International Development & GLI Monitor...")
    all_jobs: List[JobPosting] = []

    # 1. Structured REST API Sources
    all_jobs.extend(fetch_reliefweb_api())

    # 2. Add Valid Remote / Social Impact RSS Feeds
    rss_sources = [
        ("https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss", "WeWorkRemotely Finance"),
        ("https://remotive.com/remote-jobs/feed", "Remotive Feed")
    ]
    for url, source in rss_sources:
        all_jobs.extend(fetch_rss_feed(url, source))

    print(f"\n[*] Processing and scoring {len(all_jobs)} cumulative listings...")
    matched_portfolio: List[JobPosting] = []

    for job in all_jobs:
        scored = score_job(job)
        if scored.match_score >= MATCH_THRESHOLD:
            matched_portfolio.append(scored)

    # Sort matching jobs by match score descending
    matched_portfolio.sort(key=lambda x: x.match_score, reverse=True)

    print(f"\n[+] Pipeline completed. Found {len(matched_portfolio)} high-alignment matches.\n")
    for idx, match in enumerate(matched_portfolio[:5], 1):
        print(f"{idx}. [{match.match_score} pts] {match.title}")
        print(f"   Org: {match.organization} | Location: {match.location} | Source: {match.source}")
        print(f"   URL: {match.url}")
        print(f"   Match Factors: {'; '.join(match.matched_reasons[:2])}")
        print("   " + "-" * 60)


if __name__ == "__main__":
    execution_pipeline()
