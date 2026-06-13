# DocNamer — Demo Quick Start

A repeatable runbook for spinning up DocNamer to demo the "zero touch
inbox scanner" flow — without needing a physical photocopier.

---

## 1. Prerequisites

- Docker + Docker Compose
- ~4 GB free disk for the Ollama model (one-time download)
- A test mailbox you can send/receive to over IMAP (a throwaway Gmail
  account with an [app password](https://myaccount.google.com/apppasswords)
  works well — IMAP and SMTP are both enabled by default)

---

## 2. Start the stack

```bash
git clone <this repo> docnamer && cd docnamer
cp .env.example .env
docker compose up -d

# One-time: pull the LLM model (~2 GB)
docker compose exec ollama ollama pull llama3.2
```

Open **http://localhost:8000**.

Check the stack is healthy:

```bash
curl http://localhost:8000/healthz
# {"status":"ok","mail_configured":false}
```

---

## 3. Configure DocNamer (Step 1 — Configure)

On the **Configure** tab, enter:

- **Client name**: `Riverside Church`
- **Client code**: `RIVCH001`
- **Date range**: today → today (widen later if your test email lands
  outside this window)

---

## 4. Connect the mailbox (Step 2 — Connect)

Enter your test mailbox's IMAP details:

| Field | Gmail example |
|---|---|
| Host | `imap.gmail.com` |
| Port | `993` |
| Username | `you@gmail.com` |
| Password | app password |
| SSL | ✓ |
| Mailbox | `INBOX` |

Click **Test connection** — you should see "Connected to … mailbox INBOX
accessible".

---

## 5. Send a fake "scan" email

No copier needed — `scripts/send_test_scan.py` builds a small multi-page
PDF (a council tax bill + an energy bill, with a blank duplex page between
them, just like a real scanner batch) and emails it to your test mailbox
with a realistic MFP cover note:

```text
Reply to: Copier <copier@yourdomain.com>
Device Name: RIVERSIDE CHURCH
Device Model: BP-60C45
...
Attached file is scanned image in PDF format.
```

Run it (from the repo root, with `pymupdf` installed — it's already in
`backend/requirements.txt`):

```bash
pip install pymupdf
python scripts/send_test_scan.py \
  --smtp-host smtp.gmail.com --smtp-port 587 \
  --smtp-user you@gmail.com --smtp-password "xxxx xxxx xxxx xxxx" \
  --to you@gmail.com \
  --client-name "Riverside Church" --client-code RIVCH001
```

Give it 10–30 seconds to arrive.

---

## 6. Run a scan (Step 3 — Process)

Click **Start scan**. You should see, live:

1. `Connecting to mail server…`
2. `Found 1 scanner email(s) — processing…`
3. `Converting: scan.pdf (4 pages)`
4. `Skipped 1 blank page(s) … : [3]`
5. `Analysing 3 of 4 page(s) with LLM…`
6. `LLM found 2 document(s) in scan.pdf`
7. `Saved: 20240331-RIVCH001-council_tax_bill_2024-2025.pdf`
8. `Saved: 20240405-RIVCH001-edf_energy_bill.pdf`
9. `Done — 2 document(s) ready to download.`

Then **Download All** to grab the zip, or go to the **Results** tab to
download each PDF individually.

---

## 7. Demo watch mode (optional)

On the **Process** tab, enable **Watch mode**:

- Interval: `1` minute (for the demo — use 10+ in real deployments)
- Lookback: `1` day

Send a second test email with the script, then wait up to a minute — a
new job appears automatically in the **Results** tab with no manual
"Start scan" click. This is the "zero touch" behaviour: once configured,
new scanner emails are picked up and named on their own schedule.

Turn watch mode off again when you're done, or it'll keep polling the
mailbox every minute.

---

## 8. Resetting between demos

To wipe all jobs, documents and the watch config but keep the downloaded
Ollama model (so you don't have to re-pull 2 GB):

```bash
docker compose down
docker volume ls | grep docnamer   # find the data volume name (project-prefixed)
docker volume rm <project>_docnamer_data
docker compose up -d
```

To wipe **everything** including the model:

```bash
docker compose down -v
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "0 scanner emails detected. Found N email(s) with PDF attachments — none matched photocopier patterns." | Your test email's From/subject/body didn't match `PHOTOCOPIER_*` keywords in `backend/config.py`. The bundled `send_test_scan.py` cover note is designed to match `PHOTOCOPIER_BODY_KEYWORDS` (`device model`, `scanned image`, …) even if the From address doesn't. |
| "No emails with PDF attachments found between …" | Date range doesn't cover when the test email arrived — widen it, or check the mailbox/folder name. |
| LLM step hangs or errors | `docker compose logs ollama` — make sure `ollama pull llama3.2` finished and the container has enough memory (4 GB+ recommended). |
| "Skipping … identical PDF already processed" | You're re-sending the exact same generated PDF — `send_test_scan.py` produces byte-identical output each run. Re-running the demo against the same mailbox/date range will dedupe by design; use `docker compose down -v` (step 8) to reset, or change `--client-name` to vary the PDF content. |
