"""
trust_score.py
--------------
Trust Score System
==================

Formula:
    Trust Score = w1*author_credibility
                + w2*citation_score
                + w3*domain_authority
                + w4*recency_score
                + w5*medical_disclaimer

All component scores are normalised to [0, 1].
Final score is in [0, 1].

Weights (sum = 1.0):
    w1  author_credibility       0.25
    w2  citation_score           0.20
    w3  domain_authority         0.25
    w4  recency_score            0.20
    w5  medical_disclaimer       0.10
"""

import math
import re
from datetime import datetime
from urllib.parse import urlparse


# ── Weight configuration ──────────────────────────────────────────────────
WEIGHTS = {
    "author_credibility":       0.25,
    "citation_score":           0.20,
    "domain_authority":         0.25,
    "recency_score":            0.20,
    "medical_disclaimer":       0.10,
}

# ── Domain authority tiers ────────────────────────────────────────────────
HIGH_AUTHORITY_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nature.com",
    "science.org", "thelancet.com", "nejm.org", "bmj.com",
    "jamanetwork.com", "who.int", "cdc.gov", "nih.gov",
    "arxiv.org", "ieee.org", "acm.org", "springer.com", "wiley.com",
}

MED_AUTHORITY_DOMAINS = {
    "towardsdatascience.com", "medium.com", "techcrunch.com",
    "wired.com", "theverge.com", "mit.edu", "stanford.edu",
    "harvard.edu", "bbc.com", "reuters.com", "apnews.com",
    "youtube.com", "youtu.be",
}

LOW_AUTHORITY_SIGNALS = [
    r"blogspot\.", r"wordpress\.com", r"weebly\.com",
    r"wix\.com", r"\.tk$", r"click(bait|here)",
]

# ── Known credible author patterns ────────────────────────────────────────
CREDIBLE_AUTHOR_SIGNALS = [
    r"\bmd\b", r"\bphd\b", r"\bdr\.?\b", r"\bprof\.?\b",
    r"professor", r"researcher", r"scientist",
]

SUSPICIOUS_AUTHOR_SIGNALS = [
    r"admin", r"anonymous", r"unknown", r"staff", r"editor",
    r"[0-9]{3,}",  # names with long number sequences
]

# ── Medical disclaimer patterns ────────────────────────────────────────────
DISCLAIMER_PATTERNS = [
    r"this (article|content|information) is not (a substitute for|intended as) (professional |medical )?advice",
    r"consult (a|your) (doctor|physician|healthcare professional|specialist)",
    r"for (educational|informational) purposes only",
    r"not (intended|meant) to (diagnose|treat|cure|prevent)",
    r"medical disclaimer",
    r"seek (professional|medical) (help|advice|attention)",
]


# ── Component scorers ─────────────────────────────────────────────────────

def _base_author_score(source_type: str) -> float:
    """Baseline credibility by source type."""
    if source_type == "pubmed":
        return 0.80
    if source_type == "youtube":
        return 0.55
    return 0.50


def _split_authors(author: str) -> list[str]:
    """Split author string into entities for average scoring."""
    if ";" in author:
        parts = [p.strip() for p in author.split(";") if p.strip()]
    elif " & " in author:
        parts = [p.strip() for p in author.split(" & ") if p.strip()]
    elif re.search(r"\band\b", author, flags=re.IGNORECASE):
        parts = [p.strip() for p in re.split(r"\band\b", author, flags=re.IGNORECASE) if p.strip()]
    else:
        parts = [author.strip()]
    return parts


def _score_single_author(author_name: str, source_type: str) -> float:
    """Score one author string independently."""
    lower = author_name.lower()
    base = _base_author_score(source_type)
    boost = sum(0.08 for p in CREDIBLE_AUTHOR_SIGNALS if re.search(p, lower))
    penalty = sum(0.15 for p in SUSPICIOUS_AUTHOR_SIGNALS if re.search(p, lower))
    return max(0.0, min(1.0, base + boost - penalty))

def score_author_credibility(author: str, source_type: str = "") -> float:
    """
    Estimate author credibility.

    Rules:
    - Author matches credibility signals (Dr., PhD, etc.) → boost
    - Author matches suspicious signals → penalise
    - Multiple authors → average of individual scores
    - Missing author → 0.20 (heavy penalty)
    """
    if not author or author.strip().lower() in {"", "unknown", "n/a", "none"}:
        return 0.20  # missing author penalty

    authors = _split_authors(author)
    if not authors:
        return 0.20

    scores = [_score_single_author(name, source_type) for name in authors]
    return sum(scores) / len(scores)


def score_citations(citation_count: int) -> float:
    """
    Normalise citation count using logarithmic scaling.
    0 citations → 0.0 | 1000+ citations → ~1.0
    """
    if citation_count <= 0:
        return 0.0
    # log10(1000) ≈ 3, cap at 1.0
    return min(1.0, math.log10(citation_count + 1) / 3.0)


def score_domain_authority(source_url: str) -> float:
    """
    Heuristic domain authority score.

    - High authority domains → 0.90
    - Medium authority domains → 0.65
    - Low-authority signals (regex) → 0.25
    - Unknown → 0.45
    """
    if not source_url:
        return 0.40

    try:
        domain = urlparse(source_url).netloc.lower()
        domain = domain.replace("www.", "")
    except Exception:
        return 0.40

    if any(hd in domain for hd in HIGH_AUTHORITY_DOMAINS):
        return 0.90

    if any(md in domain for md in MED_AUTHORITY_DOMAINS):
        return 0.65

    if any(re.search(sig, domain) for sig in LOW_AUTHORITY_SIGNALS):
        return 0.25

    return 0.45  # neutral unknown domain


def score_recency(published_date: str) -> float:
    """
    Decay score based on content age.

    Age (years)   Score
    ----------    -----
    < 0.5         1.00
    1             0.85
    2             0.70
    3             0.55
    5             0.40
    10+           0.10
    Unknown       0.30
    """
    if not published_date or published_date.strip().lower() in {"", "unknown", "n/a"}:
        return 0.30

    raw_date = published_date.strip()
    pub_dt = None

    # Fast path for ISO dates/timestamps.
    try:
        iso_date = raw_date.replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(iso_date)
        if pub_dt.tzinfo is not None:
            pub_dt = pub_dt.replace(tzinfo=None)
    except ValueError:
        pub_dt = None

    # Try multiple date formats.
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
        "%B %d, %Y", "%b %d, %Y", "%Y-%b-%d", "%Y-%B-%d", "%Y",
    ]
    candidates = [raw_date]
    if "T" in raw_date:
        candidates.append(raw_date.split("T", 1)[0])
    if " " in raw_date:
        candidates.append(raw_date.split(" ", 1)[0])

    for candidate in candidates:
        if pub_dt is not None:
            break
        for fmt in formats:
            try:
                pub_dt = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue

    if pub_dt is None:
        # Try parsing a month-only date.
        try:
            pub_dt = datetime.strptime(raw_date[:7], "%Y-%m")
        except ValueError:
            pub_dt = None

    if pub_dt is None:
        # Try just extracting a year
        year_match = re.search(r'\b(19|20)\d{2}\b', raw_date)
        if year_match:
            try:
                pub_dt = datetime(int(year_match.group()), 1, 1)
            except Exception:
                return 0.30
        else:
            return 0.30

    now = datetime.now()
    age_years = (now - pub_dt).days / 365.25

    if age_years < 0.5:
        return 1.00
    elif age_years < 1:
        return 0.90
    elif age_years < 2:
        return 0.80
    elif age_years < 3:
        return 0.65
    elif age_years < 5:
        return 0.50
    elif age_years < 10:
        return 0.30
    else:
        return 0.10


def score_medical_disclaimer(content: str, source_type: str = "") -> float:
    """
    Returns 1.0 if a medical disclaimer is present, 0.5 for non-medical
    sources (disclaimer not expected), 0.0 otherwise.
    """
    if source_type == "pubmed":
        # Academic papers are inherently formal; no lay disclaimer expected
        return 1.0

    if not content:
        return 0.0

    lower_content = content.lower()
    for pattern in DISCLAIMER_PATTERNS:
        if re.search(pattern, lower_content):
            return 1.0

    # If content discusses medical topics but has no disclaimer → penalty
    medical_keywords = ["diagnos", "treatment", "symptom", "disease",
                        "medication", "therapy", "clinical"]
    is_medical_content = any(kw in lower_content for kw in medical_keywords)

    return 0.0 if is_medical_content else 0.5


# ── Abuse prevention ───────────────────────────────────────────────────────

def abuse_prevention_penalty(source_url: str, content: str,
                              author: str, source_type: str) -> float:
    """
    Returns an additive penalty in [0, 0.40] that is subtracted from
    the raw trust score to prevent gaming.

    Checks:
    1. SEO spam signals in content
    2. Suspicious domain patterns
    3. Fake/anonymous author for medical content
    4. Keyword stuffing
    """
    penalty = 0.0
    content_lower = (content or "").lower()
    author_lower = (author or "").lower()

    # 1. SEO spam signals
    seo_spam_patterns = [
        r"(buy now|click here|limited offer|act now|free download){2,}",
        r"(guaranteed|100%\s+safe|miracle cure)",
    ]
    for pat in seo_spam_patterns:
        if re.search(pat, content_lower):
            penalty += 0.15
            break

    # 2. Suspicious domain
    try:
        domain = urlparse(source_url).netloc.lower()
        for sig in LOW_AUTHORITY_SIGNALS:
            if re.search(sig, domain):
                penalty += 0.10
                break
    except Exception:
        pass

    # 3. Fake/anonymous author + medical content
    medical_terms = ["diagnos", "cure", "treatment", "symptom", "prescription"]
    is_medical = any(t in content_lower for t in medical_terms)
    no_author = not author or author_lower in {"unknown", "anonymous", "admin", ""}
    if is_medical and no_author:
        penalty += 0.15

    # 4. Keyword stuffing (same word repeated too often)
    words = re.findall(r'\b[a-z]{4,}\b', content_lower)
    if words:
        from collections import Counter
        word_freq = Counter(words)
        top_word, top_count = word_freq.most_common(1)[0]
        if top_count / len(words) > 0.08:  # >8% single word → stuffing
            penalty += 0.10

    return min(0.40, penalty)


# ── Master scorer ──────────────────────────────────────────────────────────

def calculate_trust_score(
    source_url: str = "",
    source_type: str = "",
    author: str = "",
    published_date: str = "",
    citation_count: int = 0,
    content: str = "",
) -> dict:
    """
    Compute the weighted trust score and return a detailed breakdown.

    Returns:
        {
            "trust_score": float (0–1, 2 d.p.),
            "components": { ... each sub-score ... },
            "penalty": float,
            "explanation": str
        }
    """
    components = {
        "author_credibility": score_author_credibility(author, source_type),
        "citation_score":     score_citations(citation_count),
        "domain_authority":   score_domain_authority(source_url),
        "recency_score":      score_recency(published_date),
        "medical_disclaimer": score_medical_disclaimer(content, source_type),
    }

    raw_score = sum(WEIGHTS[k] * v for k, v in components.items())
    penalty   = abuse_prevention_penalty(source_url, content, author, source_type)
    final     = round(max(0.0, min(1.0, raw_score - penalty)), 2)

    # Human-readable label
    if final >= 0.80:
        label = "High Trust"
    elif final >= 0.60:
        label = "Moderate Trust"
    elif final >= 0.40:
        label = "Low Trust"
    else:
        label = "Unreliable"

    explanation = (
        f"Score: {final} ({label}) | "
        f"Author={components['author_credibility']:.2f}, "
        f"Citations={components['citation_score']:.2f}, "
        f"Domain={components['domain_authority']:.2f}, "
        f"Recency={components['recency_score']:.2f}, "
        f"Disclaimer={components['medical_disclaimer']:.2f}, "
        f"Abuse penalty=-{penalty:.2f}"
    )

    return {
        "trust_score": final,
        "trust_label": label,
        "components": {k: round(v, 3) for k, v in components.items()},
        "penalty": round(penalty, 3),
        "explanation": explanation,
    }
