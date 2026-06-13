#!/usr/bin/env python3
"""
Generate a sample multi-letter "scan" PDF and email it to a test mailbox,
mimicking the cover note produced by office MFPs/photocopiers — so
DocNamer's scanner detection and LLM segmentation can be demoed without a
physical copier.

The generated PDF contains two separate "letters" (a council tax bill and
an energy bill) plus a blank duplex page in between, so a demo run also
shows off blank-page filtering and multi-document splitting.

Example (Gmail, with an app password):

    python scripts/send_test_scan.py \\
        --smtp-host smtp.gmail.com --smtp-port 587 \\
        --smtp-user you@gmail.com --smtp-password "xxxx xxxx xxxx xxxx" \\
        --to you@gmail.com \\
        --client-name "Riverside Church" --client-code RIVCH001

Requires PyMuPDF (already in backend/requirements.txt):
    pip install pymupdf
"""

import argparse
import smtplib
import tempfile
from email.message import EmailMessage
from pathlib import Path

import fitz  # PyMuPDF


COVER_NOTE = """Reply to: Copier <{from_addr}>
Device Name: {device_name}
Device Model: BP-60C45
Location: 21 ALCESTER ROAD, BIRMINGHAM, WEST MIDLANDS, B13 8AR

File Format: PDF (High)
Resolution: 200dpi x 200dpi

Attached file is scanned image in PDF format.
"""


def _write_lines(page, lines, start_y=72, fontsize=11, line_height=18):
    y = start_y
    for line in lines:
        page.insert_text((72, y), line, fontsize=fontsize)
        y += line_height


def build_demo_pdf(path: Path, client_name: str):
    """Two letters + a blank duplex page, just like a real scanner batch."""
    doc = fitz.open()

    # ── Letter 1: Council Tax bill, 2 pages ──────────────────────────────
    p = doc.new_page()
    _write_lines(p, [
        "Birmingham City Council",
        "Council Tax Bill 2024/2025",
        "",
        f"Account holder: {client_name}",
        "Account ref: 123456789",
        "Date of issue: 31 March 2024",
        "",
        "Your annual council tax for 2024/2025 is shown below.",
    ])
    p = doc.new_page()
    _write_lines(p, [
        "Page 2 of 2",
        "",
        "Payment schedule",
        "April:  GBP 150.00",
        "May:    GBP 150.00",
        "June:   GBP 150.00",
        "",
        f"{client_name} — please contact us if you have any questions.",
    ])

    # ── Blank duplex page (common scanner artefact) ──────────────────────
    doc.new_page()

    # ── Letter 2: Energy bill, 1 page ─────────────────────────────────────
    p = doc.new_page()
    _write_lines(p, [
        "EDF Energy",
        "Final Bill",
        "",
        f"Customer: {client_name}",
        "Date: 05 April 2024",
        "",
        "Amount due: GBP 87.42",
        "Thank you for being an EDF Energy customer.",
    ])

    doc.save(str(path))
    doc.close()


def send_email(args, pdf_path: Path):
    from_addr = args.from_addr or f"copier@{args.smtp_user.split('@', 1)[-1]}"
    device_name = args.device_name or args.client_name.upper()

    msg = EmailMessage()
    msg["Subject"] = f"Scan from {device_name}"
    msg["From"] = f"Copier <{from_addr}>"
    msg["To"] = args.to
    msg.set_content(COVER_NOTE.format(from_addr=from_addr, device_name=device_name))

    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="scan.pdf",
    )

    if args.smtp_port == 465:
        smtp_cls = smtplib.SMTP_SSL
    else:
        smtp_cls = smtplib.SMTP

    with smtp_cls(args.smtp_host, args.smtp_port) as server:
        if args.smtp_port != 465:
            server.starttls()
        server.login(args.smtp_user, args.smtp_password)
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smtp-host", required=True, help="SMTP server, e.g. smtp.gmail.com")
    parser.add_argument("--smtp-port", type=int, default=587, help="587 (STARTTLS) or 465 (SSL)")
    parser.add_argument("--smtp-user", required=True, help="SMTP login (also used as auth)")
    parser.add_argument("--smtp-password", required=True, help="SMTP password / app password")
    parser.add_argument("--to", required=True, help="Mailbox to send the test scan to")
    parser.add_argument("--from-addr", default="", help="Override the From address shown in the cover note")
    parser.add_argument("--client-name", default="Riverside Church", help="Name to embed in the fake letters")
    parser.add_argument("--client-code", default="RIVCH001", help="Client code (for your DocNamer job config)")
    parser.add_argument("--device-name", default="", help="Override the 'Device Name' shown in the cover note")
    parser.add_argument("--keep-pdf", action="store_true", help="Don't delete the generated PDF afterwards")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "scan.pdf"
        build_demo_pdf(pdf_path, args.client_name)

        if args.keep_pdf:
            keep_path = Path.cwd() / "demo_scan.pdf"
            keep_path.write_bytes(pdf_path.read_bytes())
            print(f"Saved a copy to {keep_path}")

        send_email(args, pdf_path)

    print(f"Sent test scan to {args.to}")
    print(f"In DocNamer, use client name '{args.client_name}' and code '{args.client_code}'.")


if __name__ == "__main__":
    main()
