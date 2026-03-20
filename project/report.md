# Short Report: Multi-Source Data Scraper & Trust Scoring System

---

## 1. Scraping Strategy

The pipeline is split into three independent scrapers, each tuned to the structure of
its target platform.

**Blog Scraper.** The primary tool is `newspaper3k`, a library built specifically for
news and blog article extraction. It handles author detection, publication date parsing,
and content isolation automatically. For sites that block newspaper3k or return thin
content, a BeautifulSoup fallback inspects `<meta>` Open Graph tags
(`og:title`, `article:published_time`, `article:author`), then extracts body text from
the `<article>` or `<main>` element after stripping scripts, styles, ads, and navigation.
A 1-second delay between requests prevents server overload and respects polite scraping
conventions.

**YouTube Scraper.** YouTube does not offer a public API for metadata without a key, so
the scraper parses the page's `application/ld+json` block, which YouTube embeds as
structured JSON-LD data. This yields the channel name, upload date, and description
without authentication. Transcripts are retrieved separately using `youtube-transcript-api`,
which accesses the same caption data used by YouTube's own subtitle feature.

**PubMed Scraper.** NCBI's E-utilities REST API provides free structured access to all
PubMed records. `ESearch` converts a query or PMID into a canonical UID, `EFetch` returns
full XML with all metadata fields, and `ELink` retrieves a citation count proxy through
the `pubmed_pubmed_citedin` link type. The XML is parsed with Python's built-in
`xml.etree.ElementTree`, keeping the dependency count low.

---

## 2. Topic Tagging Method

Topic tagging combines two complementary approaches.

**Domain Taxonomy Matching.** A curated dictionary maps high-level topics (AI, NLP,
Healthcare, Cybersecurity, Finance, etc.) to a list of associated keywords. The combined
text of the title, description, and content is searched for each keyword. Matches yield
human-readable, consistent top-level tags that are useful for filtering and categorisation.

**RAKE Keyword Extraction.** RAKE (Rapid Automatic Keyword Extraction) operates without
a pre-trained model, making it fast and language-agnostic. It scores candidate phrases
based on word co-occurrence within phrase boundaries, favouring multi-word terms that
appear rarely across the document. The top 8 RAKE phrases are appended to the tag list
after deduplication, adding specific long-tail keywords that the taxonomy may miss.

The final tag list is capped at 15 entries to avoid bloat, with domain tags listed first
since they are more semantically stable.

---

## 3. Trust Score Algorithm

The trust score is a weighted sum of five normalised component scores, minus an abuse
prevention penalty, clamped to [0, 1].

```
Trust Score = 0.25 × author_credibility
            + 0.20 × citation_score
            + 0.25 × domain_authority
            + 0.20 × recency_score
            + 0.10 × medical_disclaimer_presence
            − abuse_prevention_penalty
```

**author_credibility [0–1].** Starts at a baseline determined by source type (PubMed = 0.80,
YouTube = 0.55, Blog = 0.50). A +0.08 boost is applied for each credibility signal
detected in the author name (Dr., PhD, Prof., Researcher). Suspicious signals
(admin, anonymous, long numeric suffixes) subtract 0.15 each. A missing author
yields 0.20 as a strong penalty.

**citation_score [0–1].** Applies logarithmic normalisation: `log10(count + 1) / 3`.
This gives a score of 0 for zero citations, 0.33 for 10 citations, 0.67 for 100,
and ~1.0 for 1000+. Logarithmic scaling prevents high-citation outliers from
dominating the score.

**domain_authority [0–1].** A three-tier heuristic: high-authority domains
(PubMed, Nature, CDC, arXiv, IEEE) receive 0.90; medium-authority domains
(Medium, TechCrunch, BBC, YouTube) receive 0.65; domains matching low-authority
regex patterns (blogspot, wix, .tk TLDs) receive 0.25; unknown domains default to 0.45.

**recency_score [0–1].** A stepped decay based on content age: content published within
6 months scores 1.00, declining to 0.85 at 1 year, 0.65 at 3 years, 0.30 at 10 years.
This reflects the importance of timeliness, especially for medical and technical content.

**medical_disclaimer_presence [0 or 1].** Checks the full content for 6 disclaimer
patterns using regex ("not a substitute for professional advice", "consult your doctor",
"for informational purposes only", etc.). PubMed articles automatically receive 1.0 since
formal academic papers carry inherent epistemic markers. Non-medical content that lacks
a disclaimer receives 0.5 (neutral), while medical content without any disclaimer receives 0.0.

---

## 4. Edge Case Handling

| Edge Case | Handling Strategy |
|-----------|-------------------|
| **Missing author** | Score defaults to 0.20; `trust_label` reflects the penalty |
| **Missing publish date** | Recency score defaults to 0.30; flagged in output as "Unknown" |
| **Unavailable transcript** | Falls back to video description for chunking and tagging |
| **Multiple authors** | Author string is split into individual authors and credibility is averaged across all authors |
| **Non-English content** | `langdetect` identifies the language; stored in `language` field; RAKE handles multi-language |
| **Very short content** | Sentence-based chunker activates when paragraph chunking yields fewer than 2 chunks |
| **Fake/anonymous medical author** | `abuse_prevention_penalty` adds 0.15 when anonymous author + medical content detected |
| **SEO spam signals** | Regex patterns for "buy now", "miracle cure", "guaranteed" trigger a 0.15 penalty |
| **Keyword stuffing** | Words appearing in >8% of total word count trigger a 0.10 penalty |
| **Outdated information** | Recency score drops to 0.10 for content older than 10 years |
| **Low-authority blogspot/wix domain** | Domain authority capped at 0.25; abuse penalty adds further 0.10 |

**Abuse Prevention Summary.** The `abuse_prevention_penalty` function evaluates four
independent signals: SEO spam language in content (+0.15), low-authority domain
patterns (+0.10), anonymous authorship on medical content (+0.15), and keyword stuffing
above the 8% threshold (+0.10). The total penalty is capped at 0.40 to prevent a single
signal from zeroing out a score, preserving score granularity for borderline cases.
