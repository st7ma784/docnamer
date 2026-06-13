"""
Offline IMAP mail service — no cloud auth, no external dependencies.
Uses Python's stdlib imaplib exclusively.

Credentials are loaded in priority order:
  1. Environment variables (IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD, …)
  2. Data-directory JSON file (set via the UI)
"""

import email
import imaplib
import json
import re
import socket
from datetime import datetime, timedelta
from email.header import decode_header as _decode_header

from config import (
    IMAP_HOST, IMAP_MAILBOX, IMAP_PASSWORD, IMAP_PORT, IMAP_USE_SSL,
    IMAP_USERNAME, MAIL_CONFIG_PATH,
    PHOTOCOPIER_BODY_KEYWORDS, PHOTOCOPIER_FROM_DOMAINS,
    PHOTOCOPIER_FROM_KEYWORDS, PHOTOCOPIER_SUBJECT_KEYWORDS,
)


# ── Config persistence ────────────────────────────────────────────────────────

def load_config() -> dict:
    stored = {}
    if MAIL_CONFIG_PATH.exists():
        try:
            stored = json.loads(MAIL_CONFIG_PATH.read_text())
        except Exception:
            stored = {}

    # Env vars win over stored config
    if IMAP_HOST:
        stored.update({
            "host": IMAP_HOST,
            "port": IMAP_PORT,
            "username": IMAP_USERNAME,
            "password": IMAP_PASSWORD,
            "use_ssl": IMAP_USE_SSL,
            "mailbox": IMAP_MAILBOX,
        })
    return stored


def save_config(cfg: dict):
    # Never persist a config sourced entirely from env — env is the authority.
    if IMAP_HOST:
        return
    safe = {k: v for k, v in cfg.items()}
    MAIL_CONFIG_PATH.write_text(json.dumps(safe, indent=2))


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("host") and cfg.get("username") and cfg.get("password"))


# ── Connection ────────────────────────────────────────────────────────────────

_IMAP_TIMEOUT = 30  # seconds — prevents indefinite hang on firewall blackhole

def _connect(cfg: dict) -> imaplib.IMAP4:
    host = cfg["host"]
    port = int(cfg.get("port", 993 if cfg.get("use_ssl", True) else 143))
    if cfg.get("use_ssl", True):
        conn = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.socket().settimeout(_IMAP_TIMEOUT)
    conn.login(cfg["username"], cfg["password"])
    return conn


def test_connection() -> tuple[bool, str]:
    if not is_configured():
        return False, "Mail server not configured"
    try:
        cfg = load_config()
        conn = _connect(cfg)
        # Verify the target mailbox exists
        mailbox = cfg.get("mailbox", "INBOX")
        status, _ = conn.select(mailbox, readonly=True)
        conn.logout()
        if status != "OK":
            return False, f'Mailbox "{mailbox}" not found on server'
        return True, f'Connected to {cfg["host"]} — mailbox "{mailbox}" accessible'
    except imaplib.IMAP4.error as exc:
        return False, f"IMAP error: {exc}"
    except OSError as exc:
        return False, f"Network error: {exc}"


# ── Photocopier heuristics ────────────────────────────────────────────────────

def _decode_str(raw) -> str:
    if raw is None:
        return ""
    parts = _decode_header(str(raw))
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out)


def _is_photocopier(from_str: str, subject_str: str, body_str: str = "") -> bool:
    from_lower = from_str.lower()
    subject_lower = subject_str.lower()
    body_lower = body_str.lower()
    for kw in PHOTOCOPIER_FROM_KEYWORDS:
        if kw in from_lower:
            return True
    for domain in PHOTOCOPIER_FROM_DOMAINS:
        if domain in from_lower:
            return True
    for kw in PHOTOCOPIER_SUBJECT_KEYWORDS:
        if kw in subject_lower:
            return True
    for kw in PHOTOCOPIER_BODY_KEYWORDS:
        if kw in body_lower:
            return True
    return False


# Limit how much body text we scan for keywords — cover notes are short,
# this just avoids decoding/scanning huge HTML bodies on non-scanner emails.
_BODY_SCAN_CHARS = 2000


def _extract_body_text(msg) -> str:
    """Best-effort plain-text extraction of an email's body for keyword matching."""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload[:_BODY_SCAN_CHARS].decode(charset, errors="replace")
        except (LookupError, ValueError):
            return payload[:_BODY_SCAN_CHARS].decode("utf-8", errors="replace")

    # Fall back to a stripped text/html part
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            html = payload[:_BODY_SCAN_CHARS].decode(charset, errors="replace")
        except (LookupError, ValueError):
            html = payload[:_BODY_SCAN_CHARS].decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html)

    return ""


# ── Fetch emails (synchronous — call via run_in_executor) ─────────────────────

def _imap_date(d: str) -> str:
    """Convert YYYY-MM-DD to DD-Mon-YYYY for IMAP SEARCH."""
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b-%Y")


def fetch_scanner_emails_sync(date_from: str, date_to: str) -> tuple[list[dict], int]:
    """Returns (scanner_emails, total_emails_with_attachments_in_range).

    The second value is used to diagnose why 0 scanner emails were found —
    it tells the user whether the mailbox was simply empty or whether emails
    existed but didn't match the photocopier heuristics.
    """
    cfg = load_config()
    conn = _connect(cfg)
    try:
        mailbox = cfg.get("mailbox", "INBOX")
        conn.select(mailbox, readonly=True)

        since = _imap_date(date_from)
        before = _imap_date(
            (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        )

        status, data = conn.search(None, f"SINCE {since} BEFORE {before}")
        if status != "OK" or not data[0]:
            return [], 0

        email_ids = data[0].split()
        scanner_emails = []
        total_with_attachments = 0

        for eid in email_ids:
            status, hdata = conn.fetch(eid, "(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK":
                continue

            msg = email.message_from_bytes(hdata[0][1])
            from_str = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))
            date_str = msg.get("Date", "")

            # Fetch the full message to check for PDF attachments regardless of
            # the photocopier filter — this powers the "emails with PDFs existed
            # but didn't match the filter" diagnostic in the scanner.
            status, mdata = conn.fetch(eid, "(RFC822)")
            if status != "OK":
                continue

            full_msg = email.message_from_bytes(mdata[0][1])
            pdfs = _extract_pdf_attachments(full_msg)
            if not pdfs:
                continue

            total_with_attachments += 1

            body = _extract_body_text(full_msg)
            if not _is_photocopier(from_str, subject, body):
                continue

            scanner_emails.append({
                "id": eid.decode(),
                "from": from_str,
                "subject": subject,
                "date": date_str,
                "attachments": pdfs,
            })

        return scanner_emails, total_with_attachments
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _extract_pdf_attachments(msg) -> list[tuple[str, bytes]]:
    results = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode_str(part.get_filename())
        content_type = part.get_content_type()
        is_pdf = (
            content_type == "application/pdf"
            or (filename and filename.lower().endswith(".pdf"))
        )
        if not is_pdf:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            results.append((filename or "attachment.pdf", payload))
    return results
