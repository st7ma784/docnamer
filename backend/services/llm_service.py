import json
import re

import httpx

from config import LLM_BASE_URL, LLM_MAX_CHARS, LLM_MODEL
from services.pdf_converter import format_pages_for_llm

_SYSTEM = (
    "You are a document analysis assistant. "
    "You analyze scanned documents and extract structured information. "
    "Always respond with valid JSON only — no explanations, no markdown fences."
)


def _build_prompt(client_name: str, content: str, truncated: bool) -> str:
    note = "\n[NOTE: Document was truncated due to length — analyse what is present.]\n" if truncated else ""
    return f"""Analyze this scanned document. It may contain multiple individual letters or documents.

Client name to look for: "{client_name}"

Document content (pages separated by "--- PAGE X ---"):{note}
{content}

Identify each individual letter/document and return a JSON array:
[
  {{
    "start_page": <number>,
    "end_page": <number>,
    "date": "<YYYYMMDD, or empty string if unknown>",
    "summary": "<2-6 word lowercase description e.g. 'council tax bill 2024' or 'edf energy bill'>",
    "client_name_found": <true or false>
  }}
]

Rules:
- Be lenient about client name: accept partial matches, OCR noise, abbreviations, initials.
- Use empty string "" for date when you cannot determine it from the document.
- Summaries must be concise and lowercase.
- If the whole document is one letter, return an array with exactly one entry.
- Return ONLY the JSON array, starting with [ and ending with ]."""


def _truncate(pages: list[str]) -> tuple[str, bool]:
    """Format pages for LLM, truncating at LLM_MAX_CHARS."""
    full = format_pages_for_llm(pages)
    if len(full) <= LLM_MAX_CHARS:
        return full, False
    return full[:LLM_MAX_CHARS] + "\n… [truncated]", True


def _parse(raw: str, total_pages: int) -> list[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    def _validate(data) -> list[dict]:
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
        if not isinstance(data, list):
            raise ValueError("not a list")
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            start = max(1, int(item.get("start_page", 1)))
            end = min(total_pages, int(item.get("end_page", total_pages)))
            if start > end:
                end = start
            out.append({
                "start_page": start,
                "end_page": end,
                "date": str(item.get("date") or ""),
                "summary": str(item.get("summary") or "scanned document"),
                "client_name_found": bool(item.get("client_name_found", False)),
            })
        return out or _fallback(total_pages)

    try:
        return _validate(json.loads(raw))
    except Exception:
        pass

    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            return _validate(json.loads(m.group()))
        except Exception:
            pass

    return _fallback(total_pages)


def _fallback(total_pages: int) -> list[dict]:
    return [{
        "start_page": 1,
        "end_page": total_pages,
        "date": "",
        "summary": "scanned document",
        "client_name_found": False,
    }]


async def check_health() -> tuple[bool, str]:
    """Ping Ollama and confirm LLM_MODEL has actually been pulled."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LLM_BASE_URL}/api/tags")
            resp.raise_for_status()
            tags = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as exc:
        return False, f"Ollama unreachable: {exc}"

    if any(tag.startswith(LLM_MODEL) for tag in tags):
        return True, f"{LLM_MODEL} ready ({len(tags)} model(s) pulled)"
    return False, f"{LLM_MODEL} not pulled yet ({len(tags)} other model(s) available)"


async def analyze_document(client_name: str, pages: list[str]) -> list[dict]:
    content, truncated = _truncate(pages)
    prompt = _build_prompt(client_name, content, truncated)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ]

    async with httpx.AsyncClient(timeout=300.0) as client:
        for req_body in [
            {"model": LLM_MODEL, "messages": messages, "temperature": 0.1,
             "response_format": {"type": "json_object"}},
            {"model": LLM_MODEL, "messages": messages, "temperature": 0.1},
        ]:
            try:
                resp = await client.post(
                    f"{LLM_BASE_URL}/v1/chat/completions", json=req_body
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                return _parse(raw, len(pages))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 422) and "response_format" in req_body:
                    continue
                raise

    return _fallback(len(pages))
