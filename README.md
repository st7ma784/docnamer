# DocNamer

Automatically names scanned documents received via email. Detects emails sent by photocopiers/scanners, converts PDFs to text, uses a local LLM to identify individual letters within a pile, and saves each one as `YYYYMMDD-CLIENTCODE-Summary.pdf`.

**Entirely offline** — no cloud services, no telemetry, no data leaves your network.

## How it works

1. **Configure** — enter client name, code, and a date range.
2. **Connect** — enter your IMAP server details (connects directly; nothing goes to a third party).
3. **Process** — DocNamer finds emails from scanners/photocopiers, downloads PDFs, and sends them through:
   - **PyMuPDF** — extracts text page-by-page (all local)
   - **Ollama LLM** — identifies letter boundaries, dates, summaries, checks the client name appears (runs on your hardware)
   - **PyMuPDF** — slices the PDF into named chunks
4. **Download** — grab individual PDFs or a zip of all.

Output filenames: `20240331-RMASS1234-Council_tax_2024-2025.pdf`

---

## Quick start — Docker Compose

```bash
cp .env.example .env
# Edit .env — at minimum set IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD
# (or leave blank and enter credentials in the web UI instead)

docker compose up -d

# Pull the LLM model — one-time download, ~2 GB, stays on your machine
docker compose exec ollama ollama pull llama3.2

# Open the app
open http://localhost:8000
```

No Azure account, no Microsoft login, no internet connection required after the model is downloaded.

> **Demoing this to someone?** See [`docs/DEMO.md`](docs/DEMO.md) for a
> step-by-step runbook, including a script that emails a fake "scan" to a
> test mailbox so you don't need a physical photocopier.

---

## IMAP compatibility

DocNamer connects to your mail server directly over IMAP. This works with:

| Server | Notes |
|--------|-------|
| On-premise Exchange | Enable IMAP in Exchange Admin Center |
| Exchange Online / Office 365 | Enable IMAP in M365 admin, use an app password |
| Dovecot / Postfix / Zimbra | Works out of the box |
| Any IMAP-compliant server | Standard port 993 (SSL) or 143 (STARTTLS) |

Use an **app password** rather than your main account password where your server supports it.

Credentials are stored in `data/mail_config.json` on the server — never sent anywhere else. If you supply them via environment variables (`.env` / K8s Secret), they are never written to disk.

---

## Helm / Rancher GitOps

```bash
helm install docnamer ./helm/docnamer \
  --set imap.host=mail.example.com \
  --set imap.username=scanner@example.com \
  --set imap.password=YOUR_PASSWORD \
  --set ingress.enabled=true \
  --set "ingress.hosts[0].host=docnamer.example.com" \
  --set "ingress.hosts[0].paths[0].path=/" \
  --set "ingress.hosts[0].paths[0].pathType=Prefix"
```

The Ollama init container pulls `llama3.2` automatically on first boot. Subsequent restarts are instant — the model is cached on the PVC.

### Rancher Fleet (GitOps)

Commit this repo and point Fleet at it with `fleet.yaml` at the root (already included). Deliver IMAP credentials via a Rancher Secret rather than storing them in Git:

```bash
# Create the secret in your cluster
kubectl create secret generic docnamer-imap-credentials \
  --from-literal=IMAP_HOST=mail.example.com \
  --from-literal=IMAP_USERNAME=scanner@example.com \
  --from-literal=IMAP_PASSWORD=your-password \
  -n docnamer
```

Then reference it in `fleet.yaml` under `helm.valuesFrom`.

---

## Using vLLM instead of Ollama

Set in `.env`:

```env
LLM_BASE_URL=http://your-vllm-host:8000
LLM_MODEL=mistralai/Mistral-7B-Instruct-v0.2
```

Both expose an OpenAI-compatible `/v1/chat/completions` endpoint.

---

## Large documents

DocNamer truncates document text at `LLM_MAX_CHARS` (default 24 000 chars ≈ 6 000 tokens) before sending to the LLM. This protects against context-window overflow with large PDFs. If you use a model with a larger context window (e.g. llama3.1:70b with 128k context), raise the limit:

```env
LLM_MAX_CHARS=100000
```

The truncation note is included in the prompt so the LLM knows to work with what it has.

---

## Photocopier detection

DocNamer identifies scanner emails by checking the sender and subject for keywords:

- **Sender keywords**: `scanner`, `printer`, `copier`, `mfp`, `noreply`, …
- **Sender domains**: `xerox`, `ricoh`, `canon`, `konica`, `kyocera`, …
- **Subject keywords**: `scan`, `scanned document`, `image from`, …

Tune these lists in `backend/config.py`.

---

## Local development (no Docker)

```bash
cd backend
pip install -r requirements.txt

# Run Ollama separately: https://ollama.com/download
ollama pull llama3.2

# Symlink frontend as static files
ln -sf ../frontend static

DATA_DIR=./data uvicorn main:app --reload
```

---

## File layout

```
docnamer/
├── backend/
│   ├── main.py              # FastAPI app & routes
│   ├── scanner.py           # Background scan job
│   ├── database.py          # SQLite helpers
│   ├── config.py            # Environment-based settings
│   └── services/
│       ├── mail_service.py  # IMAP (stdlib imaplib — no cloud auth)
│       ├── pdf_converter.py # PDF → page text (PyMuPDF)
│       ├── llm_service.py   # LLM analysis with context truncation
│       └── pdf_slicer.py    # PDF page extraction & zip building
├── frontend/                # Vanilla JS SPA (no build step)
├── helm/docnamer/           # Helm chart for Kubernetes / Rancher
├── docs/DEMO.md             # Demo / quick-start runbook
├── scripts/send_test_scan.py # Emails a fake "scan" for demos (no copier needed)
├── fleet.yaml               # Rancher Fleet GitOps entrypoint
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Data security

- All document processing happens on your own hardware
- IMAP credentials are stored locally in `data/mail_config.json` (or only in memory when supplied via env vars)
- The LLM runs locally via Ollama — document text never leaves your network
- No analytics, telemetry, or external API calls of any kind
