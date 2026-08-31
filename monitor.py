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

DOMAIN_ANCHORS = [
    "program coordinator", "program manager", "project manager", "project coordinator",
    "program associate", "project associate", "operations associate", "small business",
    "grant coordinator", "grant administrator", "community development", "financial inclusion",
    "microfinance", "vsla", "silc", "rural finance", "ngo", "nonprofit", "non-profit",
    "usaid", "public sector", "city of fort worth", "tarrant county", "city of dallas",
    "usajobs", "sba", "m&e", "monitoring and evaluation", "economic development", 
    "social impact", "pslf", "refugee", "resettlement", "caseworker", "economic empowerment",
    # Elite Track Extensions
    "gender-lens investing", "gli", "2x challenge", "2x criteria", "2x global",
    "blended finance", "concessional capital", "climate-smart agribusiness", "rural finance"
]

POSITIVE_WEIGHTS = {
    "gender_lens_and_financial_inclusion": {
        "score": 35,
        "keywords": [
            "gender-lens investing", "gli", "2x challenge", "2x criteria", "2x global",
            "2x qualification", "financial inclusion", "microfinance", "microinsurance", 
            "rural finance", "agricultural finance", "livelihoods", "vulnerable populations"
        ],
    },
    "blended_and_concessional_finance": {
        "score": 30,
        "keywords": [
            "blended finance", "concessional capital", "paid-in capital", "technical assistance",
            "dfi", "development finance institution", "emerging markets", "private capital mobilization",
            "climate finance", "transition finance", "sustainability-linked"
        ],
    },
    "international_development_nodes": {
        "score": 25,
        "keywords": [
            "findev canada", "british international investment", "bii", "global affairs canada",
            "devex", "kore global", "nextbillion", "center for effective philanthropy",
            "chemonics", "dai", "mercy corps", "kiva", "brac", "finca", "reliefweb"
        ],
    },
    "training_and_associate_pipelines": {
        "score": 25,
        "keywords": [
            "program associate", "project associate", "operations associate",
            "entry level", "training provided", "on-the-job training", "paid training",
            "fellowship", "trainee", "associate program", "pathways", "caseworker"
        ],
    },
    "knowledge_management_and_me": {
        "score": 15,
        "keywords": [
            "m&e", "monitoring and evaluation", "learning lab", "knowledge creation",
            "state of the sector", "impact evaluation", "data entry", "budget tracking",
            "excel", "python", "workflow automation", "prompt engineering"
        ],
    },
}

EXCLUDED_KEYWORDS = [
    "construction", "civil engineer", "general contractor", "heavy equipment",
    "superintendent", "building inspector", "estimator", "hvac", "plumbing",
    "software engineer", "software developer", "full stack", "full-stack",
    "backend engineer", "frontend engineer", "devops", "machine learning",
    "commercial bank", "investment banking", "wall street", "wealth management",
    "retail banking", "corporate finance", "mortgage broker", "private equity"
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
    is_training_role: bool = False


# =====================================================================
# SCORING ENGINE & CLEANING
# =====================================================================

def clean_html(raw_html: str) -> str:
    """Strips HTML tags for clean text scoring."""
    cleanr = re.compile("<.*?>")
    return re.sub(cleanr, "", raw_html or "")


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

    # 3. Check for Training / Associate keywords
    training_terms = POSITIVE_WEIGHTS["training_and_associate_pipelines"]["keywords"]
    if any(term in text_corpus for term in training_terms):
        posting.is_training_role = True

    # 4. Positive Keyword Scoring
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
# INGESTION MODULE
# =====================================================================

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
            print(f"      [{source_name}] Retrieved {len(postings)} entries.")
    except Exception as e:
        print(f"      [ERROR] Failed to fetch {source_name}: {str(e)}")
        
    return postings
