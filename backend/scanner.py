import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import database as db

log = logging.getLogger("docnamer.scanner")
from config import OUTPUT_DIR
from services.dedup import filter_pages, llm_range_to_pdf, pdf_hash
from services.llm_service import analyze_document
from services.mail_service import fetch_scanner_emails_sync
from services.pdf_converter import convert_pdf_to_pages, get_page_count
from services.pdf_slicer import build_zip, extract_pages, make_output_filename


def _is_cancelled(job_id: str) -> bool:
    job = db.get_job(job_id)
    return job is not None and job["status"] == "cancelled"


# ── Manual scan job ───────────────────────────────────────────────────────────

async def run_scan_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return

    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    db.update_job_status(job_id, "running")
    db.add_event(job_id, "info", "Connecting to mail server…")

    loop = asyncio.get_running_loop()

    try:
        emails, total_with_attachments = await loop.run_in_executor(
            None, fetch_scanner_emails_sync, job["date_from"], job["date_to"]
        )
    except Exception as exc:
        db.update_job_status(job_id, "failed")
        db.add_event(job_id, "error", f"IMAP connection failed: {exc}")
        return

    db.update_job(job_id, total_emails=len(emails))

    if not emails:
        if total_with_attachments > 0:
            db.add_event(job_id, "warn",
                f"0 scanner emails detected. Found {total_with_attachments} email(s) with PDF "
                f"attachments — none matched photocopier patterns. Check the sender address "
                f"or adjust the keywords in config.py.")
        else:
            db.add_event(job_id, "warn",
                f"No emails with PDF attachments found between {job['date_from']} and "
                f"{job['date_to']}. Check your date range and mailbox name.")
        db.update_job_status(job_id, "completed")
        db.add_event(job_id, "info", "Scan finished with 0 documents.")
        return

    db.add_event(job_id, "info", f"Found {len(emails)} scanner email(s) — processing…")

    try:
        for idx, email_msg in enumerate(emails):
            if _is_cancelled(job_id):
                db.add_event(job_id, "warn", "Scan cancelled.")
                return
            await _process_email(job, email_msg, job_dir)
            db.update_job(job_id, processed_emails=idx + 1)

        if _is_cancelled(job_id):
            return

        zip_path = OUTPUT_DIR / f"{job_id}.zip"
        db.add_event(job_id, "info", "Building download archive…")
        await loop.run_in_executor(None, build_zip, job_dir, zip_path)

        doc_count = db.get_job(job_id)["total_documents"]
        db.update_job_status(job_id, "completed")
        db.add_event(job_id, "success", f"Done — {doc_count} document(s) ready to download.")
    except Exception as exc:
        log.exception("Scan job %s failed unexpectedly", job_id)
        db.update_job_status(job_id, "failed")
        db.add_event(job_id, "error", f"Scan failed unexpectedly: {exc}")


# ── Email / PDF processing ────────────────────────────────────────────────────

async def _process_email(job: dict, email_msg: dict, job_dir: Path):
    subject  = email_msg.get("subject") or "(no subject)"
    from_addr = email_msg.get("from", "unknown")
    db.add_event(job["id"], "info", f"Email: {subject} — from {from_addr}")

    for att_name, att_bytes in email_msg.get("attachments", []):
        await _process_pdf(
            job, email_msg["id"], subject, from_addr,
            email_msg.get("date", ""), att_name, att_bytes, job_dir,
        )


async def _process_pdf(
    job: dict, email_id: str, subject: str, from_addr: str,
    received: str, att_name: str, att_bytes: bytes, job_dir: Path,
):
    loop = asyncio.get_running_loop()

    # ── Duplicate PDF check ───────────────────────────────────────────────────
    content_hash = pdf_hash(att_bytes)
    existing = db.check_pdf_hash(content_hash)
    if existing:
        db.add_event(job["id"], "warn",
            f"Skipping {att_name} — identical PDF already processed in a previous job "
            f"(originally saved as {existing['output_filename']}). "
            f"This is likely a scanner retry or a misfeed re-send.")
        return

    safe_att_name = Path(att_name).name.replace("/", "_").replace("..", "_") or "attachment.pdf"
    raw_pdf = job_dir / f"raw_{email_id}_{safe_att_name}"
    raw_pdf.write_bytes(att_bytes)
    total_pdf_pages = get_page_count(str(raw_pdf))

    db.add_event(job["id"], "info", f"Converting: {att_name} ({total_pdf_pages} pages)")

    try:
        pages = await loop.run_in_executor(None, convert_pdf_to_pages, str(raw_pdf))
    except Exception as exc:
        db.add_event(job["id"], "error", f"PDF conversion failed for {att_name}: {exc}")
        return

    if not pages:
        db.add_event(job["id"], "warn", f"No text extracted from {att_name}")
        return

    # ── Page quality filtering ────────────────────────────────────────────────
    filtered, page_map, blank_pages, misfed_pages = filter_pages(pages)

    if blank_pages:
        db.add_event(job["id"], "info",
            f"Skipped {len(blank_pages)} blank page(s) in {att_name}: {blank_pages}")
    if misfed_pages:
        db.add_event(job["id"], "warn",
            f"Detected {len(misfed_pages)} misfed page(s) in {att_name} — duplicate of "
            f"the preceding page, skipped: {misfed_pages}")

    if not filtered:
        db.add_event(job["id"], "warn",
            f"No usable pages in {att_name} after removing blank/misfed pages")
        return

    db.add_event(job["id"], "info",
        f"Analysing {len(filtered)} of {len(pages)} page(s) with LLM…")

    # ── LLM analysis ─────────────────────────────────────────────────────────
    try:
        segments = await analyze_document(job["client_name"], filtered)
    except Exception as exc:
        db.add_event(job["id"], "error", f"LLM analysis failed for {att_name}: {exc}")
        return

    db.add_event(job["id"], "info",
        f"LLM found {len(segments)} document(s) in {att_name}")

    # ── Slice and save ────────────────────────────────────────────────────────
    first_filename = None
    covered_pages: list[int] = []
    for seg in segments:
        try:
            # Map LLM page numbers → original PDF page numbers
            start_pdf, end_pdf = llm_range_to_pdf(
                seg["start_page"], seg["end_page"], page_map, total_pdf_pages
            )

            filename = make_output_filename(seg["date"], job["client_code"], seg["summary"])
            out_path = job_dir / filename

            counter = 1
            while out_path.exists():
                out_path = job_dir / f"{out_path.stem}_{counter}.pdf"
                counter += 1

            await loop.run_in_executor(
                None, extract_pages,
                str(raw_pdf), str(out_path), start_pdf, end_pdf,
            )

            if not seg["client_name_found"]:
                db.add_event(job["id"], "warn",
                             f"Client name not confirmed in: {out_path.name}")

            db.add_document(
                job_id=job["id"], email_id=email_id, email_subject=subject,
                email_from=from_addr, email_date=received, original_pdf=str(raw_pdf),
                start_page=start_pdf, end_page=end_pdf,
                doc_date=seg["date"], summary=seg["summary"],
                output_filename=out_path.name,
                client_name_found=seg["client_name_found"],
            )

            db.add_event(job["id"], "success", f"Saved: {out_path.name}")
            if first_filename is None:
                first_filename = out_path.name
            covered_pages.extend(range(start_pdf, end_pdf + 1))

        except Exception as exc:
            db.add_event(job["id"], "error", f"Failed to extract segment: {exc}")

    # ── Page coverage check ───────────────────────────────────────────────────
    # Every page sent to the LLM should end up in exactly one output document.
    # Pages missing here were silently dropped; pages appearing twice were
    # duplicated across two output files — both indicate LLM mis-segmentation.
    expected_pages = set(page_map.values())
    covered_set = set(covered_pages)
    missing_pages = sorted(expected_pages - covered_set)
    if missing_pages:
        db.add_event(job["id"], "warn",
            f"{att_name}: page(s) {missing_pages} were not included in any output "
            f"document — the LLM's page ranges may have missed content.")
    duplicate_pages = sorted({p for p in covered_pages if covered_pages.count(p) > 1})
    if duplicate_pages:
        db.add_event(job["id"], "warn",
            f"{att_name}: page(s) {duplicate_pages} appear in more than one output "
            f"document — the LLM's page ranges overlapped.")

    # Register hash after at least one segment was saved successfully
    if first_filename:
        db.register_pdf_hash(content_hash, job["id"], email_id, first_filename)

    # Remove the raw intermediate file — output PDFs are in job_dir without the raw_ prefix
    try:
        raw_pdf.unlink()
    except OSError:
        pass


# ── Watch mode ────────────────────────────────────────────────────────────────

_watch_task: asyncio.Task | None = None


async def watch_loop():
    """Polls the mailbox on a schedule and creates jobs automatically.
    Errors in individual runs are logged but never kill the loop."""
    while True:
        cfg = db.get_watch_config()
        if not cfg.get("enabled"):
            break

        interval = int(cfg.get("interval_minutes", 10)) * 60
        lookback = int(cfg.get("lookback_days", 7))

        today     = datetime.now(timezone.utc).date()
        date_to   = today.isoformat()
        date_from = (today - timedelta(days=lookback)).isoformat()

        db.save_watch_config(last_run_at=datetime.now(timezone.utc).isoformat())

        job_id = db.create_job(cfg["client_name"], cfg["client_code"], date_from, date_to)
        try:
            await run_scan_job(job_id)
        except asyncio.CancelledError:
            raise  # propagate cancellation so stop_watch() works cleanly
        except Exception as exc:
            log.exception("Watch mode scan failed: %s", exc)
            db.update_job_status(job_id, "failed")
            db.add_event(job_id, "error", f"Watch scan failed unexpectedly: {exc}")

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def start_watch():
    global _watch_task
    if _watch_task and not _watch_task.done():
        return
    _watch_task = asyncio.create_task(watch_loop())


def stop_watch():
    global _watch_task
    if _watch_task and not _watch_task.done():
        _watch_task.cancel()
    _watch_task = None
