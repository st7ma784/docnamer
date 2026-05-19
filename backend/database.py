import sqlite3
import uuid
from datetime import datetime, timezone

from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode allows concurrent reads alongside a write, preventing
    # "database is locked" errors when multiple requests hit SQLite at once.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                client_code TEXT NOT NULL,
                date_from TEXT NOT NULL,
                date_to TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                total_emails INTEGER DEFAULT 0,
                processed_emails INTEGER DEFAULT 0,
                total_documents INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                email_id TEXT NOT NULL,
                email_subject TEXT,
                email_from TEXT,
                email_date TEXT,
                original_pdf TEXT NOT NULL,
                start_page INTEGER NOT NULL,
                end_page INTEGER NOT NULL,
                doc_date TEXT NOT NULL,
                summary TEXT NOT NULL,
                output_filename TEXT NOT NULL,
                client_name_found INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            -- SHA-256 hashes of every PDF we have successfully processed.
            -- Used to skip duplicate attachments (scanner retries, overlapping job date ranges).
            CREATE TABLE IF NOT EXISTS pdf_hashes (
                hash TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                email_id TEXT NOT NULL,
                output_filename TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Single-row table (id always = 1) holding the watch-mode configuration.
            CREATE TABLE IF NOT EXISTS watch_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                client_name TEXT NOT NULL DEFAULT '',
                client_code TEXT NOT NULL DEFAULT '',
                interval_minutes INTEGER NOT NULL DEFAULT 10,
                lookback_days INTEGER NOT NULL DEFAULT 7,
                last_run_at TEXT
            );
            INSERT OR IGNORE INTO watch_config (id) VALUES (1);
        """)


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(client_name: str, client_code: str, date_from: str, date_to: str) -> str:
    job_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, client_name, client_code, date_from, date_to, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, client_name, client_code, date_from, date_to, "pending", _now()),
        )
    return job_id


def update_job(job_id: str, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def update_job_status(job_id: str, status: str, **extra):
    fields = {"status": status, **extra}
    if status in ("completed", "failed"):
        fields["completed_at"] = _now()
    update_job(job_id, **fields)


def get_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def add_event(job_id: str, level: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?,?,?,?)",
            (job_id, level, message, _now()),
        )


def get_events_since(job_id: str, since_id: int = 0) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
            (job_id, since_id),
        ).fetchall()
        return [dict(r) for r in rows]


def add_document(
    job_id: str,
    email_id: str,
    email_subject: str,
    email_from: str,
    email_date: str,
    original_pdf: str,
    start_page: int,
    end_page: int,
    doc_date: str,
    summary: str,
    output_filename: str,
    client_name_found: bool,
) -> str:
    doc_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO documents
               (id, job_id, email_id, email_subject, email_from, email_date,
                original_pdf, start_page, end_page, doc_date, summary,
                output_filename, client_name_found, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id, job_id, email_id, email_subject, email_from, email_date,
                original_pdf, start_page, end_page, doc_date, summary,
                output_filename, int(client_name_found), _now(),
            ),
        )
        conn.execute(
            "UPDATE jobs SET total_documents = total_documents + 1 WHERE id = ?", (job_id,)
        )
    return doc_id


def get_documents(job_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE job_id = ? ORDER BY doc_date, summary",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Startup recovery ─────────────────────────────────────────────────────────

def recover_stuck_jobs():
    """Mark jobs that were mid-flight when the server last stopped as failed.
    Without this they stay 'running' forever and the UI has no way to clear them."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('running', 'pending')"
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO job_events (job_id, level, message, created_at) VALUES (?,?,?,?)",
                (row["id"], "warn", "Job interrupted — server was restarted", _now()),
            )
        conn.execute(
            "UPDATE jobs SET status = 'failed', completed_at = ? "
            "WHERE status IN ('running', 'pending')",
            (_now(),),
        )


# ── PDF deduplication ─────────────────────────────────────────────────────────

def check_pdf_hash(content_hash: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pdf_hashes WHERE hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None


def register_pdf_hash(content_hash: str, job_id: str, email_id: str, output_filename: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pdf_hashes (hash, job_id, email_id, output_filename, created_at) "
            "VALUES (?,?,?,?,?)",
            (content_hash, job_id, email_id, output_filename, _now()),
        )


# ── Watch mode ────────────────────────────────────────────────────────────────

def get_watch_config() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM watch_config WHERE id = 1").fetchone()
        return dict(row) if row else {}


def save_watch_config(**fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE watch_config SET {sets} WHERE id = 1", list(fields.values()))
