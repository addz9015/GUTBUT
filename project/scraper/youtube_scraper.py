"""
youtube_scraper.py
------------------
Scrapes 2 YouTube videos and returns structured JSON objects.

Strategy:
  - Use youtube-transcript-api for transcripts.
  - Use yt-dlp (if available) or youtube-transcript-api metadata fallback
    for channel name, publish date, description.
  - Use requests + BeautifulSoup as a final fallback for metadata.
  - Chunk transcripts into 60-second windows via chunk_transcript().
"""

import json
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from langdetect import detect as _detect
    def detect_language(text: str) -> str:
        try:
            return _detect(text[:500])
        except Exception:
            return "en"
except ImportError:
    def detect_language(_: str) -> str:
        return "en"

try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
    YT_API_AVAILABLE = True
except ImportError:
    YT_API_AVAILABLE = False

sys.path.insert(0, '..')
from utils.chunking import chunk_transcript, smart_chunk
from utils.tagging  import auto_tag
from scoring.trust_score import calculate_trust_score


# ── Default YouTube video IDs ─────────────────────────────────────────────
DEFAULT_VIDEO_URLS = [
    "https://www.youtube.com/watch?v=aircAruvnKk",   # 3Blue1Brown: Neural networks
    "https://www.youtube.com/watch?v=rfscVS0vtbw",   # freeCodeCamp: Python
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

COUNTRY_CODE_TO_REGION = {
    "US": "North America", "CA": "North America", "MX": "North America",
    "BR": "South America", "AR": "South America",
    "GB": "Europe", "UK": "Europe", "IE": "Europe", "FR": "Europe",
    "DE": "Europe", "IT": "Europe", "ES": "Europe", "NL": "Europe",
    "SE": "Europe", "NO": "Europe", "FI": "Europe", "CH": "Europe",
    "IN": "Asia", "CN": "Asia", "JP": "Asia", "KR": "Asia",
    "SG": "Asia", "AE": "Asia", "IL": "Asia",
    "AU": "Oceania", "NZ": "Oceania",
    "ZA": "Africa", "NG": "Africa", "EG": "Africa",
}


def _region_from_locale(locale: str) -> str:
    if not locale:
        return "Unknown"
    cleaned = str(locale).replace("_", "-").strip()
    if len(cleaned) == 2:
        code = cleaned.upper()
    else:
        parts = cleaned.split("-")
        code = parts[-1].upper() if len(parts) > 1 and len(parts[-1]) == 2 else ""
    return COUNTRY_CODE_TO_REGION.get(code, "Unknown")


def _region_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    suffix = host.split(".")[-1].upper() if host else ""
    if len(suffix) == 2:
        return COUNTRY_CODE_TO_REGION.get(suffix, "Unknown")
    return "Unknown"


def _normalize_date(raw_value: str) -> str:
    if not raw_value or str(raw_value).strip().lower() in {"", "unknown", "n/a"}:
        return "Unknown"

    raw = str(raw_value).strip()

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%b %d, %Y", "%B %d, %Y"]
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


def _extract_video_id(url: str) -> str:
    """Extract YouTube video ID from a URL."""
    patterns = [
        r"(?:v=|/)([0-9A-Za-z_-]{11})",
        r"youtu\.be/([0-9A-Za-z_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""


def _scrape_yt_metadata_bs(url: str) -> dict:
    """
    Fallback metadata extraction using BeautifulSoup on the YouTube page.
    Extracts from <meta> and <script> tags.
    """
    meta = {
        "title": "Unknown",
        "channel": "Unknown",
        "published_date": "Unknown",
        "description": "",
        "region": _region_from_url(url),
    }
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        html_lang = soup.html.get("lang", "") if soup.html else ""
        lang_region = _region_from_locale(html_lang)
        if lang_region != "Unknown":
            meta["region"] = lang_region

        og_locale = soup.find("meta", property="og:locale")
        if og_locale:
            locale_region = _region_from_locale(og_locale.get("content", ""))
            if locale_region != "Unknown":
                meta["region"] = locale_region

        og_title = soup.find("meta", property="og:title")
        if og_title:
            meta["title"] = og_title.get("content", "Unknown")

        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            meta["description"] = og_desc.get("content", "")

        # Channel & date often live in JSON-LD
        scripts = soup.find_all("script", type="application/ld+json")
        for s in scripts:
            try:
                import json as _json
                parsed = _json.loads(s.string or "{}")
                payloads = parsed if isinstance(parsed, list) else [parsed]

                for data in payloads:
                    if not isinstance(data, dict):
                        continue

                    author_data = data.get("author", {})
                    if isinstance(author_data, dict):
                        meta["channel"] = author_data.get("name", meta["channel"])
                    elif isinstance(author_data, list) and author_data and isinstance(author_data[0], dict):
                        meta["channel"] = author_data[0].get("name", meta["channel"])

                    upload_date = data.get("uploadDate", "")
                    if upload_date:
                        meta["published_date"] = upload_date

                    description = data.get("description", "")
                    if description:
                        meta["description"] = description

                    lang_region = _region_from_locale(data.get("inLanguage", ""))
                    if lang_region != "Unknown":
                        meta["region"] = lang_region
            except Exception:
                pass

        regions_allowed = soup.find("meta", itemprop="regionsAllowed")
        if regions_allowed and regions_allowed.get("content"):
            first_code = regions_allowed.get("content", "").split(",")[0].strip()
            allowed_region = _region_from_locale(first_code)
            if allowed_region != "Unknown":
                meta["region"] = allowed_region

        # Last resort for channel
        if meta["channel"] == "Unknown":
            itemprop = soup.find(itemprop="author")
            if itemprop:
                name = itemprop.find(itemprop="name")
                if name:
                    meta["channel"] = name.get("content", "Unknown")

    except Exception as e:
        meta["error"] = str(e)

    meta["published_date"] = _normalize_date(meta.get("published_date", "Unknown"))

    return meta


def _get_transcript(video_id: str) -> tuple[list[dict], str]:
    """
    Returns (transcript_entries, full_text).
    Falls back to empty if transcript unavailable.
    """
    if not YT_API_AVAILABLE or not video_id:
        return [], ""

    try:
        entries = YouTubeTranscriptApi.get_transcript(video_id)
        full_text = " ".join(e["text"] for e in entries)
        return entries, full_text
    except (TranscriptsDisabled, NoTranscriptFound):
        return [], ""
    except Exception:
        return [], ""


def scrape_video(url: str) -> dict:
    """Scrape a single YouTube video and return a structured record."""
    print(f"  [YouTube] Scraping: {url}")

    video_id = _extract_video_id(url)
    record = {
        "source_url":     url,
        "source_type":    "youtube",
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
        # Metadata
        meta = _scrape_yt_metadata_bs(url)
        record["author"]         = meta.get("channel", "Unknown")
        record["published_date"] = _normalize_date(meta.get("published_date", "Unknown"))
        record["region"]         = meta.get("region", "Unknown") or _region_from_url(url)
        description              = re.sub(r"\s+", " ", meta.get("description", "")).strip()
        title                    = meta.get("title", "").replace(" - YouTube", "").strip()
        record["title"]          = title or "Unknown"
        record["description"]    = description

        # Transcript
        entries, transcript_text = _get_transcript(video_id)

        full_content = f"{title} {description} {transcript_text}"

        if transcript_text:
            record["language"]       = detect_language(transcript_text)
            record["content_chunks"] = chunk_transcript(entries) if entries else smart_chunk(transcript_text)
        elif description:
            record["language"]       = detect_language(description)
            record["content_chunks"] = smart_chunk(description)
        elif title:
            record["content_chunks"] = smart_chunk(title)
        else:
            record["content_chunks"] = []

        tag_text = transcript_text or description or title
        record["topic_tags"] = auto_tag(title=title, description=description, content=tag_text)

        ts = calculate_trust_score(
            source_url=url,
            source_type="youtube",
            author=record["author"],
            published_date=record["published_date"],
            citation_count=0,
            content=full_content,
        )
        record["trust_score"]     = ts["trust_score"]
        record["trust_label"]     = ts["trust_label"]
        record["trust_breakdown"] = ts["components"]

    except Exception as e:
        record["error"] = str(e)
        print(f"    ERROR: {e}")

    time.sleep(1)
    return record


def scrape_videos(urls: list[str] = None) -> list[dict]:
    urls = urls or DEFAULT_VIDEO_URLS
    return [scrape_video(url) for url in urls]


if __name__ == "__main__":
    import os
    results = scrape_videos()
    os.makedirs("../output", exist_ok=True)
    with open("../output/youtube.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} YouTube records → output/youtube.json")
