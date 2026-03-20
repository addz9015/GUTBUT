"""
main.py
-------
Orchestrates the full pipeline:
  1. Scrape 3 blogs
  2. Scrape 2 YouTube videos
  3. Scrape 1 PubMed article
  4. Save individual + combined JSON output files

Usage:
    python main.py

Optional CLI args:
    --blog-urls   url1 url2 url3
    --yt-urls     url1 url2
    --pmid        33278961
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent))

from scraper.blog_scraper    import scrape_blogs
from scraper.youtube_scraper import scrape_videos
from scraper.pubmed_scraper  import scrape_pubmed

OUTPUT_DIR = Path(__file__).parent / "output"


def parse_args():
    parser = argparse.ArgumentParser(description="Multi-Source Data Scraper")
    parser.add_argument(
        "--blog-urls", nargs="+", default=None,
        help="Three blog post URLs to scrape"
    )
    parser.add_argument(
        "--yt-urls", nargs="+", default=None,
        help="Two YouTube video URLs to scrape"
    )
    parser.add_argument(
        "--pmid", default=None,
        help="PubMed article PMID or search query"
    )
    return parser.parse_args()


def save_json(data, filename: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  ✓ Saved: {path}")
    return path


def print_summary(records: list[dict]):
    print("\n" + "=" * 60)
    print("SCRAPING SUMMARY")
    print("=" * 60)
    for r in records:
        src   = r.get("source_type", "?").upper()
        url   = r.get("source_url", "N/A")[:60]
        score = r.get("trust_score", "N/A")
        label = r.get("trust_label", "")
        error = r.get("error", "")
        status = f"✓ Trust: {score} ({label})" if not error else f"✗ Error: {error}"
        print(f"  [{src}] {url}…")
        print(f"          {status}")
    print("=" * 60)


def main():
    args = parse_args()

    print("\n🚀 Starting Multi-Source Scraper\n")
    all_records = []

    # ── Blogs ─────────────────────────────────────────────────────────────
    print("── Blogs ────────────────────────────────────────────")
    blog_records = scrape_blogs(args.blog_urls)
    save_json(blog_records, "blogs.json")
    all_records.extend(blog_records)

    # ── YouTube ───────────────────────────────────────────────────────────
    print("\n── YouTube ──────────────────────────────────────────")
    yt_records = scrape_videos(args.yt_urls)
    save_json(yt_records, "youtube.json")
    all_records.extend(yt_records)

    # ── PubMed ────────────────────────────────────────────────────────────
    print("\n── PubMed ───────────────────────────────────────────")
    pubmed_record = scrape_pubmed(args.pmid)
    save_json([pubmed_record], "pubmed.json")
    all_records.append(pubmed_record)

    # ── Combined output ───────────────────────────────────────────────────
    print("\n── Saving combined output ───────────────────────────")
    save_json(all_records, "scraped_data.json")

    print_summary(all_records)
    print(f"\n✅ Done. All files saved in: {OUTPUT_DIR.resolve()}\n")


if __name__ == "__main__":
    main()
