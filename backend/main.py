import asyncio
import base64
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi import BackgroundTasks
from pydantic import BaseModel

import database as db
from config import AUTH_PASSWORD, AUTH_USERNAME, ALLOWED_ORIGINS, OUTPUT_DIR
from scanner import run_scan_job, start_watch, stop_watch
from services import mail_service
from services.pdf_slicer import build_zip

log = logging.getLogger("docnamer")


# ── Lifespan (startup / graceful shutdown) ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    db.recover_stuck_jobs()
    cfg = db.get_watch_config()
    if cfg.get("enabled") and cfg.get("client_name") and cfg.get("client_code"):
        log.info("Resuming watch mode from persisted config")
        start_watch()
    log.info("DocNamer started")
    yield
    log.info("DocNamer shutting down")
    stop_watch()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="DocNamer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)


# ── HTTP Basic Auth middleware ─────────────────────────────────────────────────
# Skipped entirely when AUTH_PASSWORD is not set (local / dev mode).

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not AUTH_PASSWORD:
        return await call_next(request)

    # Health probe path is exempt so load-balancers don't need credentials
    if request.url.path == "/healthz":
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded   = base64.b64decode(auth[6:]).decode()
            user, pwd = decoded.split(":", 1)
            user_ok = secrets.compare_digest(user.encode(), AUTH_USERNAME.encode())
            pass_ok = secrets.compare_digest(pwd.encode(),  AUTH_PASSWORD.encode())
            if user_ok and pass_ok:
                return await call_next(request)
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="DocNamer"'},
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def health():
    return {"status": "ok", "mail_configured": mail_service.is_configured()}


# ── Mail configuration ────────────────────────────────────────────────────────

class MailConfig(BaseModel):
    host: str
    port: int = 993
    username: str
    password: str = ""
    use_ssl: bool = True
    mailbox: str = "INBOX"


@app.get("/mail/config")
def get_mail_config():
    cfg = mail_service.load_config()
    return {k: v for k, v in cfg.items() if k != "password"} | {"password_set": bool(cfg.get("password"))}


@app.post("/mail/config")
def set_mail_config(payload: MailConfig):
    data = payload.model_dump()
    if not data["password"]:
        data["password"] = mail_service.load_config().get("password", "")
    mail_service.save_config(data)
    return {"status": "saved"}


@app.get("/mail/status")
def mail_status():
    return {"configured": mail_service.is_configured()}


@app.post("/mail/test")
async def test_mail():
    loop = asyncio.get_running_loop()
    ok, message = await loop.run_in_executor(None, mail_service.test_connection)
    return {"ok": ok, "message": message}


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    client_name: str
    client_code: str
    date_from: str
    date_to: str


@app.post("/jobs", status_code=201)
async def create_job(payload: JobCreate, background_tasks: BackgroundTasks):
    if not mail_service.is_configured():
        raise HTTPException(status_code=400, detail="Mail server not configured")
    job_id = db.create_job(
        payload.client_name, payload.client_code,
        payload.date_from, payload.date_to,
    )
    background_tasks.add_task(run_scan_job, job_id)
    return {"job_id": job_id}


@app.get("/jobs")
def list_jobs():
    return db.list_jobs()


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/jobs/{job_id}")
def cancel_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a {job['status']} job")
    db.update_job_status(job_id, "cancelled")
    db.add_event(job_id, "warn", "Job cancelled by user")
    return {"status": "cancelled"}


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    async def stream():
        last_id  = 0
        terminal = ("completed", "failed", "cancelled")
        try:
            while True:
                if await request.is_disconnected():
                    break
                for ev in db.get_events_since(job_id, last_id):
                    yield f"data: {json.dumps(dict(ev))}\n\n"
                    last_id = ev["id"]
                current = db.get_job(job_id)
                if current["status"] in terminal:
                    yield f"data: {json.dumps({'type': 'done', 'status': current['status'], 'total_documents': current['total_documents']})}\n\n"
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/jobs/{job_id}/documents")
def get_documents(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return db.get_documents(job_id)


def _safe_file(job_id: str, filename: str) -> Path:
    """Resolve path and guarantee it stays within the job output directory."""
    base   = (OUTPUT_DIR / job_id).resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base) + "/"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return target


@app.get("/jobs/{job_id}/documents/{filename}/download")
def download_document(job_id: str, filename: str):
    path = _safe_file(job_id, filename)
    return FileResponse(path, filename=filename, media_type="application/pdf")


@app.get("/jobs/{job_id}/download-all")
async def download_all(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir  = OUTPUT_DIR / job_id
    zip_path = OUTPUT_DIR / f"{job_id}.zip"

    if not zip_path.exists():
        # Write to a temp file then rename atomically to avoid serving a
        # partially-written zip if two requests arrive simultaneously.
        tmp_path = zip_path.with_suffix(".zip.tmp")
        await asyncio.get_running_loop().run_in_executor(None, build_zip, job_dir, tmp_path)
        tmp_path.replace(zip_path)

    friendly = f"{job['client_code']}-{job['date_from']}-to-{job['date_to']}.zip"
    return FileResponse(zip_path, filename=friendly, media_type="application/zip")


# ── Watch mode ────────────────────────────────────────────────────────────────

class WatchConfig(BaseModel):
    enabled: bool
    client_name: str = ""
    client_code: str = ""
    interval_minutes: int = 10
    lookback_days: int = 7


@app.get("/watch")
def get_watch():
    return db.get_watch_config()


@app.post("/watch")
def set_watch(payload: WatchConfig):
    if payload.enabled and (not payload.client_name or not payload.client_code):
        raise HTTPException(400, "client_name and client_code are required")
    if payload.enabled and not mail_service.is_configured():
        raise HTTPException(400, "Mail server must be configured first")

    db.save_watch_config(
        enabled=int(payload.enabled),
        client_name=payload.client_name,
        client_code=payload.client_code,
        interval_minutes=payload.interval_minutes,
        lookback_days=payload.lookback_days,
    )
    if payload.enabled:
        start_watch()
    else:
        stop_watch()
    return db.get_watch_config()


# ── Static frontend ───────────────────────────────────────────────────────────

_static = Path("static")
if _static.exists():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
