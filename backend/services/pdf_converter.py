import fitz  # PyMuPDF


def convert_pdf_to_pages(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        return [page.get_text("text").strip() for page in doc]
    finally:
        doc.close()


def format_pages_for_llm(pages: list[str]) -> str:
    parts = [f"--- PAGE {i} ---\n{content}" for i, content in enumerate(pages, 1)]
    return "\n\n".join(parts)


def get_page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()
