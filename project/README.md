# Multi-Source Data Scraper & Trust Scoring System

A complete pipeline that scrapes structured content from blogs, YouTube, and PubMed,
then evaluates each source's reliability with a multi-factor trust scoring algorithm.

---

## Project Structure

```
project/
├── main.py                    # Orchestrator – runs the full pipeline
├── scraper/
│   ├── blog_scraper.py        # Scrapes 3 blog posts
│   ├── youtube_scraper.py     # Scrapes 2 YouTube videos
│   └── pubmed_scraper.py      # Scrapes 1 PubMed article
├── scoring/
│   └── trust_score.py         # Trust Score algorithm (all components)
├── utils/
│   ├── tagging.py             # Automatic topic tagging (RAKE + domain taxonomy)
│   └── chunking.py            # Content chunking (paragraph / sentence / transcript)
└── output/
    ├── blogs.json
    ├── youtube.json
    ├── pubmed.json
    └── scraped_data.json      # Combined output (all 6 sources)
```

---

## Tools & Libraries

| Library                  | Purpose                                     |
|--------------------------|---------------------------------------------|
| `requests`               | HTTP fetching for blogs and PubMed          |
| `beautifulsoup4` + `lxml`| HTML parsing and metadata extraction        |
| `newspaper3k`            | High-level article extraction for blogs     |
| `youtube-transcript-api` | YouTube transcript retrieval                |
| `langdetect`             | Automatic language detection                |
| `rake-nltk`              | RAKE keyword extraction for topic tagging   |
| Python stdlib (`re`, `xml.etree.ElementTree`) | Regex and XML parsing |

---

## Setup & Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd project

# 2. Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install requests beautifulsoup4 lxml newspaper3k \
            youtube-transcript-api langdetect rake-nltk
```

---

## How to Run

### Full pipeline (default URLs)
```bash
python main.py
```

### Custom URLs
```bash
python main.py \
  --blog-urls https://example.com/blog1 https://example.com/blog2 https://example.com/blog3 \
  --yt-urls   https://youtube.com/watch?v=ID1 https://youtube.com/watch?v=ID2 \
  --pmid      33278961
```

### Individual scrapers
```bash
cd scraper
python blog_scraper.py
python youtube_scraper.py
python pubmed_scraper.py
```

---

## Output Format

Each scraped record follows this schema:

```json
{
  "source_url":     "https://...",
  "source_type":    "blog | youtube | pubmed",
  "author":         "Author Name",
  "published_date": "YYYY-MM-DD",
  "language":       "en",
  "region":         "Unknown",
  "topic_tags":     ["AI", "machine learning", "..."],
  "trust_score":    0.72,
  "trust_label":    "Moderate Trust",
  "trust_breakdown": {
    "author_credibility": 0.5,
    "citation_score":     0.0,
    "domain_authority":   0.65,
    "recency_score":      0.8,
    "medical_disclaimer": 0.5
  },
  "content_chunks": [
    "Paragraph 1...",
    "Paragraph 2..."
  ]
}
```

---

## Scraping Approach

### Blogs
Uses `newspaper3k` as the primary extractor (handles most modern blog platforms).
Falls back to `BeautifulSoup` parsing of `<meta>`, `<article>`, and `<main>` tags when
newspaper3k fails or returns insufficient content.

### YouTube
Uses `youtube-transcript-api` for transcripts. Metadata (channel name, publish date,
description) is extracted via `BeautifulSoup` parsing of YouTube's `application/ld+json`
script blocks, which provide structured JSON-LD data.

### PubMed
Uses the NCBI E-utilities REST API (no API key required for low volume):
- `ESearch` resolves queries or PMIDs
- `EFetch` retrieves full XML records (title, authors, journal, abstract, MeSH terms)
- `ELink` provides a citation count proxy via `pubmed_pubmed_citedin`

---

## Trust Score Design

See `scoring/trust_score.py` and the Short Report for the full algorithm.

**Formula:**
```
Trust Score = 0.25 × author_credibility
            + 0.20 × citation_score
            + 0.25 × domain_authority
            + 0.20 × recency_score
            + 0.10 × medical_disclaimer
            − abuse_penalty
```

**Score interpretation:**

| Range       | Label           |
|-------------|-----------------|
| 0.80 – 1.00 | High Trust      |
| 0.60 – 0.79 | Moderate Trust  |
| 0.40 – 0.59 | Low Trust       |
| 0.00 – 0.39 | Unreliable      |

---

## Limitations

1. **JavaScript-rendered pages** – newspaper3k and requests cannot execute JavaScript.
   Selenium or Playwright would be needed for SPAs.
2. **YouTube transcripts** – auto-generated transcripts may contain errors; some videos
   have transcripts disabled entirely.
3. **PubMed rate limits** – without an NCBI API key, requests are limited to 3/second.
   Add `&api_key=YOUR_KEY` to TOOL_PARAMS for higher throughput.
4. **Citation count proxy** – ELink's `pubmed_pubmed_citedin` covers only PubMed-indexed
   citations, not the full Semantic Scholar / Google Scholar count.
5. **Domain authority** – without access to Moz or Ahrefs APIs, authority is heuristic-based
   (domain allow-lists). A production system should integrate a real DA API.
6. **Language detection** – `langdetect` is probabilistic and may be inaccurate on very
   short texts (< 50 words).
