"""
Duplicate and quality checks for scanned PDFs.

Three problems addressed:
  1. Duplicate PDFs — scanner retried sending, or same job date range run twice.
     Detection: SHA-256 of raw PDF bytes; skip if seen in a previous job.

  2. Blank pages — duplex scanners always produce a blank reverse for one-sided
     documents. These add noise and confuse the LLM page-boundary detection.
     Detection: pages with fewer than BLANK_THRESHOLD characters of text.

  3. Misfed pages — a page goes through the feeder twice, producing two
     consecutive pages with identical content.
     Detection: adjacent pages whose stripped text hashes match.
"""

import hashlib

BLANK_THRESHOLD = 60  # characters; pages below this are treated as blank


def pdf_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_hash(text: str) -> str:
    return hashlib.md5(text.strip().encode()).hexdigest()


def filter_pages(pages: list[str]) -> tuple[list[str], dict[int, int], list[int], list[int]]:
    """Remove blank and misfed pages, returning a clean page list for the LLM.

    Returns:
        filtered   — page texts to send to the LLM
        page_map   — {llm_page_number: original_pdf_page_number}  (both 1-indexed)
        blank      — original PDF page numbers that were blank
        misfed     — original PDF page numbers that were misfeed duplicates
    """
    # Detect blank pages
    blank: list[int] = [
        i + 1 for i, text in enumerate(pages)
        if len(text.strip()) < BLANK_THRESHOLD
    ]

    # Detect misfed pages: second of two consecutive identical non-blank pages
    hashes = [_page_hash(p) for p in pages]
    misfed: list[int] = [
        i + 1                              # 1-indexed
        for i in range(1, len(pages))
        if hashes[i] == hashes[i - 1]
        and len(pages[i].strip()) >= BLANK_THRESHOLD
    ]

    skip = set(blank) | set(misfed)

    filtered: list[str] = []
    page_map: dict[int, int] = {}
    llm_n = 1

    for pdf_n, text in enumerate(pages, 1):
        if pdf_n not in skip:
            filtered.append(text)
            page_map[llm_n] = pdf_n
            llm_n += 1

    return filtered, page_map, blank, misfed


def llm_range_to_pdf(start_llm: int, end_llm: int, page_map: dict[int, int],
                     total_pdf_pages: int) -> tuple[int, int]:
    """Convert LLM page numbers back to original PDF page numbers for slicing.

    If the LLM returns a page number beyond the filtered set (hallucination or
    truncation artefact), clamp to the valid PDF page range rather than
    passing the raw LLM number to PyMuPDF, which would produce an empty slice.
    """
    last_pdf = page_map[max(page_map)] if page_map else total_pdf_pages
    start_pdf = page_map.get(start_llm, min(start_llm, total_pdf_pages))
    end_pdf   = page_map.get(end_llm,   min(end_llm, last_pdf))
    # Ensure start never exceeds end or the document bounds
    start_pdf = min(start_pdf, total_pdf_pages)
    end_pdf   = max(start_pdf, min(end_pdf, total_pdf_pages))
    return start_pdf, end_pdf
