"""
tagging.py
----------
Automatic topic tagging using RAKE (Rapid Automatic Keyword Extraction)
with a curated domain-keyword map for semantic enrichment.
"""

import re
from rake_nltk import Rake


FALLBACK_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "about",
    "your", "you", "are", "was", "were", "have", "has", "had", "will",
    "can", "all", "our", "their", "its", "not", "but", "how", "why",
    "what", "when", "where", "who", "which", "using", "use", "guide",
    "video", "article", "tutorial", "introduction",
}


# ── Domain taxonomy ─────────────────────────────────────────────────────────
DOMAIN_MAP: dict[str, list[str]] = {
    "AI": ["artificial intelligence", "machine learning", "deep learning",
           "neural network", "llm", "gpt", "transformer", "ai model"],
    "NLP": ["natural language processing", "nlp", "text classification",
            "sentiment analysis", "named entity", "tokenization", "bert"],
    "Computer Vision": ["image recognition", "object detection", "cnn",
                        "convolutional", "yolo", "image segmentation"],
    "Healthcare": ["healthcare", "medical", "clinical", "patient", "hospital",
                   "diagnosis", "treatment", "therapy", "disease"],
    "Mental Health": ["mental health", "depression", "anxiety", "stress",
                      "psychological", "psychiatric", "therapy", "wellbeing"],
    "Data Science": ["data science", "data analysis", "pandas", "numpy",
                     "visualization", "eda", "statistics", "regression"],
    "Cybersecurity": ["cybersecurity", "security", "vulnerability", "malware",
                      "encryption", "firewall", "phishing", "breach"],
    "Web Scraping": ["web scraping", "scraping", "crawler", "beautifulsoup",
                     "selenium", "playwright", "requests", "scraper"],
    "Research": ["research", "study", "survey", "analysis", "findings",
                 "methodology", "experiment", "hypothesis", "peer-reviewed"],
    "Finance": ["finance", "investment", "stock", "market", "trading",
                "cryptocurrency", "blockchain", "economy", "revenue"],
}


def _normalize(text: str) -> str:
    return text.lower().strip()


def _fallback_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """Return simple fallback keywords when domain/RAKE extraction is empty."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", text)
    keywords = []
    seen = set()
    for tok in tokens:
        low = tok.lower()
        if low in FALLBACK_STOPWORDS:
            continue
        if low in seen:
            continue
        seen.add(low)
        keywords.append(tok)
        if len(keywords) >= max_keywords:
            break
    return keywords


def extract_rake_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """Extract top keywords from text using RAKE."""
    if not text or len(text.strip()) < 30:
        return []
    try:
        r = Rake(min_length=1, max_length=3)
        r.extract_keywords_from_text(text)
        ranked = r.get_ranked_phrases()[:max_keywords]
        # Filter noise: keep only alphabetic multi-char phrases
        return [kw for kw in ranked if re.search(r'[a-zA-Z]{3,}', kw)]
    except Exception:
        return []


def map_to_domain_tags(text: str) -> list[str]:
    """Return high-level domain tags based on keyword presence."""
    lower_text = _normalize(text)
    matched = []
    for domain, keywords in DOMAIN_MAP.items():
        if any(kw in lower_text for kw in keywords):
            matched.append(domain)
    return matched


def auto_tag(title: str = "", description: str = "", content: str = "",
             max_rake: int = 8) -> list[str]:
    """
    Combine domain taxonomy matching with RAKE extraction.
    Returns a deduplicated, cleaned list of topic tags.
    """
    combined = f"{title} {description} {content[:3000]}"  # cap content for speed
    domain_tags = map_to_domain_tags(combined)
    rake_tags = extract_rake_keywords(combined, max_keywords=max_rake)

    all_tags = domain_tags + rake_tags
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for tag in all_tags:
        norm = _normalize(tag)
        if norm not in seen:
            seen.add(norm)
            unique.append(tag)

    if not unique:
        unique = _fallback_keywords(f"{title} {description} {content[:500]}")

    if not unique:
        unique = ["general"]

    return unique[:15]  # cap final list
