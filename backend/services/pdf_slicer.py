import re
import zipfile
from pathlib import Path

import fitz  # PyMuPDF


def sanitize(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:60]


def make_output_filename(doc_date: str, client_code: str, summary: str) -> str:
    date_part = doc_date if (doc_date and len(doc_date) == 8 and doc_date.isdigit()) else "00000000"
    return f"{date_part}-{client_code.upper()}-{sanitize(summary)}.pdf"


def extract_pages(input_pdf: str, output_pdf: str, start_page: int, end_page: int):
    """Extract 1-indexed page range from input_pdf into output_pdf."""
    src = fitz.open(input_pdf)
    try:
        dst = fitz.open()
        try:
            dst.insert_pdf(src, from_page=start_page - 1, to_page=end_page - 1)
            dst.save(output_pdf)
        finally:
            dst.close()
    finally:
        src.close()


def build_zip(job_dir: Path, zip_path: Path) -> Path:
    """Zip all named PDFs in job_dir into zip_path and return it."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for pdf in sorted(job_dir.glob("*.pdf")):
            if not pdf.name.startswith("raw_"):
                zf.write(pdf, pdf.name)
    return zip_path
