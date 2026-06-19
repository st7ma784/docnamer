import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Auth ─────────────────────────────────────────────────────────────────────
# If AUTH_PASSWORD is empty the app runs without authentication (local/dev only).
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

# ── CORS ──────────────────────────────────────────────────────────────────────
# Comma-separated list of allowed origins, e.g. "https://docnamer.example.com"
# Leave unset in local dev (defaults to wildcard).
_origins_raw = os.environ.get("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",") if o.strip()]
# Wildcard is allowed in local dev (no AUTH_PASSWORD set). In production the
# AUTH_PASSWORD guard below catches misconfiguration at startup.
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["*"]
    if os.environ.get("AUTH_PASSWORD"):
        import warnings
        warnings.warn(
            "ALLOWED_ORIGINS is not set but AUTH_PASSWORD is — CORS is open to all origins. "
            "Set ALLOWED_ORIGINS to your deployment URL.",
            stacklevel=1,
        )

# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.2")

# ── Build info ────────────────────────────────────────────────────────────────
IMAGE_TAG = os.environ.get("IMAGE_TAG", "unknown")

# Characters sent to the LLM per document — guards against context overflow.
# llama3.2 has an 8k token context; ~24 000 chars ≈ 6 000 tokens, leaving
# headroom for the prompt template and the JSON response.
LLM_MAX_CHARS = int(os.environ.get("LLM_MAX_CHARS", "24000"))

# ── Storage ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "docnamer.db"
MAIL_CONFIG_PATH = DATA_DIR / "mail_config.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── IMAP bootstrap from environment (optional — can also be set via UI) ───────
# If set, these override whatever is stored in mail_config.json.
IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USERNAME = os.environ.get("IMAP_USERNAME", "")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_USE_SSL = os.environ.get("IMAP_USE_SSL", "true").lower() != "false"
IMAP_MAILBOX = os.environ.get("IMAP_MAILBOX", "INBOX")

# ── Photocopier detection heuristics ─────────────────────────────────────────
PHOTOCOPIER_FROM_KEYWORDS = [
    "scanner", "printer", "copier", "mfp", "scan",
    "noreply", "no-reply", "donotreply", "do-not-reply",
]
PHOTOCOPIER_FROM_DOMAINS = [
    "xerox", "ricoh", "canon", "konica", "sharp",
    "lexmark", "brother", "kyocera", "epson", "toshiba",
]
PHOTOCOPIER_SUBJECT_KEYWORDS = [
    "scan", "scanned", "scanned document", "image from",
    "scanned from", "device", "mfp", "multifunction",
]

# Phrases found in the *body* of scanner emails (the standard MFP cover note),
# e.g. "Device Name: ...", "Device Model: ...", "Attached file is scanned
# image in PDF format". Useful when the From address and subject don't carry
# any of the keywords above (e.g. a generic "copier@yourdomain.org" sender).
PHOTOCOPIER_BODY_KEYWORDS = [
    "scanned image", "scanned document", "device name", "device model",
    "file format: pdf", "scan to email", "resolution:",
]
