"""
blog_scraper.py
---------------
Scrapes 3 blog posts and returns structured JSON objects.

Strategy:
  1. Use newspaper3k for best-effort metadata + article text extraction.
  2. Fall back to BeautifulSoup if newspaper3k fails.
  3. Detect language with langdetect.
  4. Apply topic tagging and content chunking from utils.
  5. Calculate trust score from scoring module.
"""

import json
import re
import sys
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── langdetect ────────────────────────────────────────────────────────────
try:
    from langdetect import detect as _detect
    def detect_language(text: str) -> str:
        try:
            return _detect(text[:500])
        except Exception:
            return "en"
except ImportError:
    def detect_language(text: str) -> str:
        return "en"

# ── newspaper3k ───────────────────────────────────────────────────────────
try:
    from newspaper import Article as NpArticle
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False

# Project-local imports (adjust path when running standalone)
sys.path.insert(0, '..')
from utils.chunking import smart_chunk
from utils.tagging  import auto_tag
from scoring.trust_score import calculate_trust_score


# ── Default blog URLs (replace with any publicly accessible articles) ──────
DEFAULT_BLOG_URLS = [
    "https://realpython.com/python-web-scraping-practical-introduction/",
    "https://www.dataquest.io/blog/web-scraping-python-using-beautiful-soup/",
    "https://www.freecodecamp.org/news/scraping-wikipedia-articles-with-python/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

COUNTRY_CODE_TO_REGION = {
    "US": "North America", "CA": "North America", "MX": "North America",
    "BR": "South America", "AR": "South America", "CL": "South America",
    "GB": "Europe", "UK": "Europe", "IE": "Europe", "FR": "Europe",
    "DE": "Europe", "IT": "Europe", "ES": "Europe", "NL": "Europe",
    "SE": "Europe", "NO": "Europe", "FI": "Europe", "CH": "Europe",
    "PL": "Europe", "PT": "Europe", "TR": "Asia", "IN": "Asia",
    "CN": "Asia", "JP": "Asia", "KR": "Asia", "SG": "Asia",
    "AE": "Asia", "IL": "Asia", "AU": "Oceania", "NZ": "Oceania",
    "ZA": "Africa", "NG": "Africa", "EG": "Africa",
}

NOISE_PATTERNS = [
    r"^table of contents$", r"^share$", r"^show/hide$", r"^remove ads$",
    r"^recommended courses?$", r"^interactive quiz$", r"^frequently asked questions$",
    r"^mark as completed$", r"^related (topics|courses|tutorials)",
    r"subscribe", r"newsletter", r"advertisement", r"commenting tips",
    r"office\s*hours", r"sign-?in", r"all rights reserved", r"cookie policy",
]


def _valid_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    txt = str(value).strip()
    return txt.lower() not in {"", "unknown", "n/a", "none"}


def _first_valid(*values: Optional[str], default: str = "Unknown") -> str:
    for value in values:
        if _valid_value(value):
            return str(value).strip()
    return default


def _normalize_date(raw_value: str) -> str:
    """Normalize supported date strings to YYYY-MM-DD when possible."""
    if not _valid_value(raw_value):
        return "Unknown"

    raw = str(raw_value).strip()

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z", "%b %d, %Y", "%B %d, %Y", "%Y-%b-%d",
    ]
    for candidate in [raw, raw.split("T", 1)[0], raw.split(" ", 1)[0]]:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

    m = re.search(r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if m:
        year, month, day = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    year_match = re.search(r"\b(19|20)\d{2}\b", raw)
    if year_match:
        return f"{int(year_match.group()):04d}-01-01"

    return "Unknown"


def _region_from_locale(locale: str) -> str:
    if not _valid_value(locale):
        return "Unknown"

    cleaned = str(locale).replace("_", "-").strip()
    parts = cleaned.split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2:
        code = parts[-1].upper()
    elif len(cleaned) == 2:
        code = cleaned.upper()
    else:
        code = ""

    return COUNTRY_CODE_TO_REGION.get(code, "Unknown")


def _region_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    if not host:
        return "Unknown"

    known_domains = {
        "realpython.com": "North America",
        "freecodecamp.org": "North America",
        "dataquest.io": "North America",
    }
    for domain, region in known_domains.items():
        if host == domain or host.endswith("." + domain):
            return region

    suffix = host.split(".")[-1].upper()
    if len(suffix) == 2:
        return COUNTRY_CODE_TO_REGION.get(suffix, "Unknown")
    return "Unknown"


def _infer_author_from_text(text: str) -> str:
    """Try to extract an author from early byline text."""
    head = text[:2500]
    m = re.search(r"\bby\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3})\b", head)
    if not m:
        return "Unknown"
    candidate = m.group(1).strip()
    if candidate.lower() in {"the", "you", "we", "python"}:
        return "Unknown"
    return candidate


def _clean_blog_text(text: str) -> str:
    """Remove common navigation/promotional noise from scraped blog text."""
    if not text:
        return ""

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    cleaned = []
    seen = set()

    for line in lines:
        if not line:
            continue
        low = line.lower()
        has_noise = any(re.search(pat, low) for pat in NOISE_PATTERNS)
        if has_noise and len(line) < 140:
            continue
        if line in seen:
            continue
        seen.add(line)
        cleaned.append(line)

    cleaned_text = "\n".join(cleaned).strip()
    if len(cleaned_text) < 250:
        cleaned_text = re.sub(r"\s+", " ", text).strip()

    return cleaned_text[:50000]


# ── BeautifulSoup fallback helpers ────────────────────────────────────────

def _bs_extract(url: str, html: str) -> dict:
    """Metadata/content extraction using BeautifulSoup as a robust fallback."""
    soup = BeautifulSoup(html, "lxml")

    title_tag = (
        soup.find("meta", property="og:title") or
        soup.find("meta", attrs={"name": "title"}) or
        soup.find("meta", attrs={"name": "twitter:title"})
    )
    title = title_tag["content"] if title_tag and title_tag.get("content") else (
        soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown"
    )

    description_tag = (
        soup.find("meta", attrs={"name": "description"}) or
        soup.find("meta", property="og:description") or
        soup.find("meta", attrs={"name": "twitter:description"})
    )
    description = (
        description_tag.get("content", "") if description_tag else ""
    )

    author_tag = (
        soup.find("meta", attrs={"name": "author"}) or
        soup.find("meta", property="article:author") or
        soup.find("meta", attrs={"name": "parsely-author"}) or
        soup.find("meta", attrs={"name": "dc.creator"})
    )
    author = author_tag["content"] if author_tag and author_tag.get("content") else "Unknown"

    if not _valid_value(author):
        author_link = soup.find("a", rel=lambda v: v and "author" in str(v).lower())
        if author_link:
            author = author_link.get_text(" ", strip=True)

    if not _valid_value(author):
        script_author = re.search(
            r'"author"\s*:\s*\{[^\}]*"name"\s*:\s*"([^"]+)"', html
        )
        if script_author:
            author = script_author.group(1).strip()

    date_tag = (
        soup.find("meta", property="article:published_time") or
        soup.find("meta", property="og:published_time") or
        soup.find("meta", attrs={"name": "datePublished"}) or
        soup.find("meta", attrs={"name": "pubdate"}) or
        soup.find("meta", attrs={"name": "parsely-pub-date"}) or
        soup.find("meta", attrs={"name": "date"}) or
        soup.find("time")
    )
    if date_tag:
        pub_date = date_tag.get("content") or date_tag.get("datetime") or date_tag.get_text(strip=True)
    else:
        pub_date = "Unknown"

    if not _valid_value(pub_date):
        script_date = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        if script_date:
            pub_date = script_date.group(1).strip()

    locale_tag = (
        soup.find("meta", property="og:locale") or
        soup.find("meta", attrs={"name": "locale"})
    )
    html_lang = soup.html.get("lang", "") if soup.html else ""
    locale = locale_tag.get("content", "") if locale_tag else html_lang
    region = _region_from_locale(locale)
    if region == "Unknown":
        region = _region_from_url(url)

    # Extract body text (remove script/style/navigation/promotional containers)
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"]):
        tag.decompose()

    noisy_attrs = re.compile(
        r"(nav|menu|footer|sidebar|ad|share|social|related|subscribe|promo|banner|comment)",
        re.IGNORECASE,
    )
    for tag in soup.find_all(attrs={"class": noisy_attrs}):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": noisy_attrs}):
        tag.decompose()

    body = soup.find("article") or soup.find("main") or soup.body
    text = body.get_text(separator="\n", strip=True) if body else ""

    return {
        "title": title,
        "description": description,
        "author": author,
        "published_date": pub_date,
        "region": region,
        "text": text,
    }


def _newspaper_extract(url: str) -> Optional[dict]:
    """Use newspaper3k for extraction."""
    if not NEWSPAPER_AVAILABLE:
        return None
    try:
        art = NpArticle(url)
        art.download()
        art.parse()
        authors = ", ".join(art.authors) if art.authors else "Unknown"
        pub_date = art.publish_date.strftime("%Y-%m-%d") if art.publish_date else "Unknown"
        description = (getattr(art, "meta_description", "") or "").strip()
        return {
            "title": art.title or "Unknown",
            "description": description,
            "author": authors,
            "published_date": pub_date,
            "text": art.text or "",
        }
    except Exception:
        return None


# ── Main scraping function ────────────────────────────────────────────────

def scrape_blog(url: str) -> dict:
    """
    Scrape a single blog post URL and return a structured record.
    """
    print(f"  [Blog] Scraping: {url}")
    record = {
        "source_url":     url,
        "source_type":    "blog",
        "title":          "Unknown",
        "description":    "",
        "author":         "Unknown",
        "published_date": "Unknown",
        "language":       "en",
        "region":         "Unknown",
        "topic_tags":     [],
        "trust_score":    0.0,
        "trust_label":    "Unreliable",
        "content_chunks": [],
    }

    try:
        response_html = ""
        bs_meta = {
            "title": "Unknown",
            "description": "",
            "author": "Unknown",
            "published_date": "Unknown",
            "region": _region_from_url(url),
            "text": "",
        }

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            response_html = resp.text
            bs_meta = _bs_extract(url, response_html)
        except Exception:
            pass

        np_meta = _newspaper_extract(url) or {}

        title = _first_valid(np_meta.get("title"), bs_meta.get("title"), default="Unknown")
        description = _first_valid(np_meta.get("description"), bs_meta.get("description"), default="")
        author = _first_valid(np_meta.get("author"), bs_meta.get("author"), default="Unknown")
        published_date_raw = _first_valid(
            np_meta.get("published_date"),
            bs_meta.get("published_date"),
            default="Unknown",
        )

        content_candidates = [np_meta.get("text", ""), bs_meta.get("text", "")]
        content_text = max(content_candidates, key=lambda t: len(t or "")) or ""
        content_text = _clean_blog_text(content_text)

        if author == "Unknown":
            author = _infer_author_from_text(content_text)

        if not description and content_text:
            description = re.sub(r"\s+", " ", content_text)[:260]

        if not content_text:
            content_text = f"{title}. {description}".strip(". ")

        record["title"] = title
        record["description"] = description
        record["author"] = author
        record["published_date"] = _normalize_date(published_date_raw)
        record["region"] = _first_valid(bs_meta.get("region"), default="Unknown")
        if record["region"] == "Unknown":
            record["region"] = _region_from_url(url)

        record["topic_tags"] = auto_tag(
            title=record["title"],
            description=record["description"],
            content=content_text,
        )

        if content_text:
            record["language"]       = detect_language(content_text)
            record["content_chunks"] = smart_chunk(content_text)

        # Trust score
        ts = calculate_trust_score(
            source_url=url,
            source_type="blog",
            author=record["author"],
            published_date=record["published_date"],
            citation_count=0,
            content=content_text,
        )
        record["trust_score"] = ts["trust_score"]
        record["trust_label"] = ts["trust_label"]
        record["trust_breakdown"] = ts["components"]

    except Exception as e:
        record["error"] = str(e)
        print(f"    ERROR: {e}")

    time.sleep(1)  # polite delay
    return record


def scrape_blogs(urls: list[str] = None) -> list[dict]:
    """Scrape a list of blog URLs (defaults to DEFAULT_BLOG_URLS)."""
    urls = urls or DEFAULT_BLOG_URLS
    return [scrape_blog(url) for url in urls]


# ── CLI entry point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    results = scrape_blogs()
    os.makedirs("../output", exist_ok=True)
    with open("../output/blogs.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} blog records → output/blogs.json")
