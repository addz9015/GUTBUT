"""
pubmed_scraper.py
-----------------
Scrapes 1 PubMed article using NCBI E-utilities (no API key required
for low-volume requests; add &api_key=YOUR_KEY to increase rate limits).

Strategy:
  1. Use ESearch to resolve a query/PMID to a canonical UID.
  2. Use EFetch with rettype=xml to pull full structured metadata.
  3. Parse XML with ElementTree – no third-party XML library needed.
  4. Extract title, authors, journal, abstract, year, citation count
     (via ELink for related citations as a proxy).
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

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

sys.path.insert(0, '..')
from utils.chunking import smart_chunk
from utils.tagging  import auto_tag
from scoring.trust_score import calculate_trust_score


# ── NCBI E-utilities base URL ─────────────────────────────────────────────
EUTILS_BASE  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_PARAMS  = {"tool": "data_scraping_assignment", "email": "student@example.com"}
HEADERS      = {"User-Agent": "DataScrapingAssignment/1.0 (student project)"}

# ── Default PMID / query ──────────────────────────────────────────────────
DEFAULT_PMID = "33278961"   # "Artificial intelligence in medicine" review article

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

COUNTRY_TO_REGION = {
    "united states": "North America", "usa": "North America", "canada": "North America",
    "mexico": "North America", "brazil": "South America", "argentina": "South America",
    "united kingdom": "Europe", "england": "Europe", "ireland": "Europe",
    "france": "Europe", "germany": "Europe", "italy": "Europe", "spain": "Europe",
    "netherlands": "Europe", "sweden": "Europe", "norway": "Europe", "switzerland": "Europe",
    "india": "Asia", "china": "Asia", "japan": "Asia", "korea": "Asia", "singapore": "Asia",
    "australia": "Oceania", "new zealand": "Oceania",
    "south africa": "Africa", "nigeria": "Africa", "egypt": "Africa",
}


def _normalize_pubmed_date(year: str, month: str, day: str) -> str:
    """Build a normalized YYYY-MM-DD date from PubMed date parts."""
    if not year or not year.isdigit():
        return "Unknown"

    month_clean = (month or "01").strip().lower()
    if month_clean.isdigit():
        month_num = int(month_clean)
    else:
        month_num = MONTH_MAP.get(month_clean[:3], 1)

    day_clean = (day or "01").strip()
    day_num = int(day_clean) if day_clean.isdigit() else 1

    return f"{int(year):04d}-{month_num:02d}-{day_num:02d}"


def _infer_region_from_affiliations(affiliations: list[str]) -> str:
    if not affiliations:
        return "Unknown"
    blob = " ".join(affiliations).lower()
    for country, region in COUNTRY_TO_REGION.items():
        if country in blob:
            return region
    return "Unknown"


# ── NCBI API helpers ──────────────────────────────────────────────────────

def _fetch(endpoint: str, params: dict) -> Optional[str]:
    params.update(TOOL_PARAMS)
    try:
        resp = requests.get(
            f"{EUTILS_BASE}/{endpoint}",
            params=params,
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    NCBI request failed: {e}")
        return None


def _query_to_pmid(query: str) -> Optional[str]:
    """Use ESearch to convert a query string → first PMID."""
    xml_text = _fetch("esearch.fcgi", {"db": "pubmed", "term": query,
                                        "retmax": "1", "retmode": "xml"})
    if not xml_text:
        return None
    root = ET.fromstring(xml_text)
    id_el = root.find(".//Id")
    return id_el.text.strip() if id_el is not None else None


def _fetch_article_xml(pmid: str) -> Optional[ET.Element]:
    """Use EFetch to retrieve full XML record for a PMID."""
    xml_text = _fetch("efetch.fcgi", {
        "db": "pubmed", "id": pmid, "rettype": "xml", "retmode": "xml"
    })
    if not xml_text:
        return None
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def _get_citation_proxy(pmid: str) -> int:
    """
    Use ELink to get a rough citation count (articles that cite this one).
    Returns 0 on failure or if the count is unavailable.
    """
    xml_text = _fetch("elink.fcgi", {
        "dbfrom": "pubmed", "db": "pubmed",
        "id": pmid, "linkname": "pubmed_pubmed_citedin", "retmode": "xml"
    })
    if not xml_text:
        return 0
    try:
        root = ET.fromstring(xml_text)
        ids = root.findall(".//LinkSetDb/Link/Id")
        return len(ids)
    except Exception:
        return 0


def _parse_article(root: ET.Element, pmid: str) -> dict:
    """Parse PubMed XML element tree into a flat dict."""
    article = root.find(".//PubmedArticle")
    if article is None:
        return {}

    # Title
    title_el = article.find(".//ArticleTitle")
    title = title_el.text or "Unknown" if title_el is not None else "Unknown"
    title = re.sub(r'\s+', ' ', title).strip()

    # Authors
    author_elements = article.findall(".//AuthorList/Author")
    authors = []
    for a in author_elements:
        last  = a.findtext("LastName", "")
        first = a.findtext("ForeName", "")
        if last:
            authors.append(f"{last} {first}".strip())
        else:
            collective = a.findtext("CollectiveName", "")
            if collective:
                authors.append(collective)
    author_str = "; ".join(authors) if authors else "Unknown"

    # Journal
    journal = article.findtext(".//Journal/Title") or \
              article.findtext(".//MedlineJournalInfo/MedlineTA") or "Unknown"

    # Publication date
    pub_date_el = article.find(".//PubDate")
    if pub_date_el is not None:
        year  = pub_date_el.findtext("Year", "")
        month = pub_date_el.findtext("Month", "01")
        day   = pub_date_el.findtext("Day", "01")
        pub_date = _normalize_pubmed_date(year, month, day)
    else:
        pub_date = "Unknown"

    # Abstract
    abstract_texts = article.findall(".//AbstractText")
    abstract_parts = []
    for ab in abstract_texts:
        label = ab.get("Label", "")
        text  = ab.text or ""
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = " ".join(abstract_parts).strip()

    affiliations = [
        aff.text.strip()
        for aff in article.findall(".//AffiliationInfo/Affiliation")
        if aff is not None and aff.text
    ]
    region = _infer_region_from_affiliations(affiliations)

    # MeSH keywords
    mesh_terms = [m.text for m in article.findall(".//MeshHeadingList//DescriptorName") if m.text]
    keywords   = [k.text for k in article.findall(".//KeywordList/Keyword") if k.text]
    all_tags   = list(set(mesh_terms + keywords))[:15]

    return {
        "pmid":        pmid,
        "title":       title,
        "author":      author_str,
        "journal":     journal,
        "published_date": pub_date,
        "abstract":    abstract,
        "region":      region,
        "tags":        all_tags,
    }


# ── Main scraping function ────────────────────────────────────────────────

def scrape_pubmed(pmid_or_query: str = None) -> dict:
    """
    Scrape a PubMed article by PMID or search query.
    Returns a structured record.
    """
    pmid_or_query = pmid_or_query or DEFAULT_PMID
    print(f"  [PubMed] Fetching: {pmid_or_query}")

    # Resolve query to PMID if needed
    pmid = pmid_or_query if pmid_or_query.isdigit() else _query_to_pmid(pmid_or_query)
    if not pmid:
        return {"source_type": "pubmed", "error": "Could not resolve PMID"}

    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    record = {
        "source_url":     url,
        "source_type":    "pubmed",
        "title":          "Unknown",
        "description":    "",
        "abstract":       "",
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
        root = _fetch_article_xml(pmid)
        if root is None:
            record["error"] = "XML fetch failed"
            return record

        parsed = _parse_article(root, pmid)

        record["title"]          = parsed.get("title", "Unknown")
        record["author"]         = parsed.get("author", "Unknown")
        record["published_date"] = parsed.get("published_date", "Unknown")
        record["region"]         = parsed.get("region", "Unknown")
        record["journal"]        = parsed.get("journal", "Unknown")
        record["pmid"]           = pmid
        abstract                 = parsed.get("abstract", "")
        title                    = record["title"]
        record["abstract"]       = abstract
        record["description"]    = abstract[:280]

        if abstract:
            record["language"]       = detect_language(abstract)
            record["content_chunks"] = smart_chunk(abstract)

        # Merge MeSH tags with auto-tagging
        mesh_tags   = parsed.get("tags", [])
        auto_tagged = auto_tag(title=title, content=abstract)
        combined    = list(dict.fromkeys(mesh_tags + auto_tagged))[:15]
        record["topic_tags"] = combined

        # Citation count proxy
        time.sleep(0.5)  # NCBI rate-limit courtesy
        citation_count = _get_citation_proxy(pmid)

        ts = calculate_trust_score(
            source_url=url,
            source_type="pubmed",
            author=record["author"],
            published_date=record["published_date"],
            citation_count=citation_count,
            content=abstract,
        )
        record["trust_score"]      = ts["trust_score"]
        record["trust_label"]      = ts["trust_label"]
        record["trust_breakdown"]  = ts["components"]
        record["citation_count"]   = citation_count

    except Exception as e:
        record["error"] = str(e)
        print(f"    ERROR: {e}")

    return record


if __name__ == "__main__":
    import os
    result = scrape_pubmed()
    os.makedirs("../output", exist_ok=True)
    with open("../output/pubmed.json", "w", encoding="utf-8") as f:
        json.dump([result], f, indent=2, ensure_ascii=False)
    print(f"\nSaved PubMed record → output/pubmed.json")
