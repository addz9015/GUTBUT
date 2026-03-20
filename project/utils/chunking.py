"""
chunking.py
-----------
Splits long text content into smaller, meaningful chunks
for downstream processing (RAG, LLMs, storage, etc.).
"""

import re


def chunk_by_paragraph(text: str, min_len: int = 80) -> list[str]:
    """
    Split text on blank lines (paragraph boundaries).
    Paragraphs shorter than min_len are merged into the previous chunk.
    """
    if not text or not text.strip():
        return []

    raw = re.split(r'\n\s*\n', text.strip())
    chunks = []

    for para in raw:
        cleaned = para.strip().replace('\n', ' ')
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        if not cleaned:
            continue
        if chunks and len(cleaned) < min_len:
            chunks[-1] += ' ' + cleaned
        else:
            chunks.append(cleaned)

    return chunks


def chunk_by_sentences(text: str, max_sentences: int = 5) -> list[str]:
    """
    Fallback chunker: splits by sentence boundaries and groups
    max_sentences sentences into one chunk.
    """
    if not text or not text.strip():
        return []

    sentence_endings = re.compile(r'(?<=[.!?])\s+')
    sentences = [s.strip() for s in sentence_endings.split(text.strip()) if s.strip()]

    chunks = []
    for i in range(0, len(sentences), max_sentences):
        chunk = ' '.join(sentences[i:i + max_sentences])
        if chunk:
            chunks.append(chunk)

    return chunks


def smart_chunk(text: str, min_para_len: int = 80, max_sentences: int = 5) -> list[str]:
    """
    Tries paragraph chunking first; falls back to sentence chunking
    when the text has no clear paragraph structure.
    """
    para_chunks = chunk_by_paragraph(text, min_len=min_para_len)
    if len(para_chunks) >= 2:
        return para_chunks
    return chunk_by_sentences(text, max_sentences=max_sentences)


def chunk_transcript(transcript_entries: list[dict]) -> list[str]:
    """
    Chunks a YouTube transcript (list of {text, start, duration} dicts)
    into ~60-second windows.
    """
    if not transcript_entries:
        return []

    chunks = []
    current_chunk = []
    window_start = transcript_entries[0].get('start', 0)

    for entry in transcript_entries:
        current_chunk.append(entry.get('text', '').strip())
        elapsed = entry.get('start', 0) - window_start

        if elapsed >= 60:
            chunks.append(' '.join(current_chunk))
            current_chunk = []
            window_start = entry.get('start', 0)

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    return [c for c in chunks if c]
