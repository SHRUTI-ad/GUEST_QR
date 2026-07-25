"""Event Guest QR — staff-only guest list, email QR PDFs, scan check-in."""

from __future__ import annotations

import base64
import io
import json
import os
import smtplib
import sqlite3
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

import qrcode
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "guests.db"
EMAIL_CONFIG_PATH = BASE_DIR / "email_config.json"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "event-guest-qr-dev-key")
# Needed on Render so CSS/JS URLs use https (avoids "broken"/unstyled pages)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# Secure cookies on HTTPS hosts (Render). Keep off for local http:// testing.
_public = os.environ.get("PUBLIC_BASE_URL", "")
_on_render = bool(os.environ.get("RENDER"))
app.config["SESSION_COOKIE_SECURE"] = _on_render or _public.startswith("https://")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# Staff login (change before real use)
STAFF_USERNAME = os.environ.get("STAFF_USERNAME", "staff")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD", "event123")
STAFF_PASSWORD_HASH = generate_password_hash(STAFF_PASSWORD)

EVENT_NAME = "Summer Lunch Meetup"
EVENT_DATE = "Saturday, 25 July 2026 · 12:30 PM"
EVENT_VENUE = "Main Hall, City Convention Center"

# Public base URL embedded inside QR codes (must be reachable by staff phones).
# Example: http://10.197.190.212:5050
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

SAMPLE_GUESTS = [
    {
        "name": "Shruti Anandas",
        "email": "shrutianandas123@gmail.com",
        "phone": "9326305627",
        "meal": "Vegetarian",
        "table_no": "T1",
    },
    {
        "name": "Vishal Patel",
        "email": "vickyashokpatel123@gmail.com",
        "phone": "8866325109",
        "meal": "Non-Vegetarian",
        "table_no": "T2",
    },
]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            meal TEXT NOT NULL,
            table_no TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            arrived INTEGER NOT NULL DEFAULT 0,
            arrived_at TEXT,
            lunch_claimed INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            pdf_sent INTEGER NOT NULL DEFAULT 0,
            pdf_sent_at TEXT
        )
        """
    )
    _ensure_column(conn, "guests", "arrived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "guests", "arrived_at", "TEXT")
    _ensure_column(conn, "guests", "pdf_sent", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "guests", "pdf_sent_at", "TEXT")
    _ensure_column(conn, "guests", "dinner_claimed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "guests", "dinner_claimed_at", "TEXT")

    count = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    if count == 0:
        _insert_sample_guests(conn)
    conn.commit()
    conn.close()


def _insert_sample_guests(conn: sqlite3.Connection) -> None:
    for guest in SAMPLE_GUESTS:
        conn.execute(
            """
            INSERT INTO guests (name, email, phone, meal, table_no, token)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                guest["name"],
                guest["email"],
                guest["phone"],
                guest["meal"],
                guest["table_no"],
                uuid.uuid4().hex,
            ),
        )


def reset_guests_to_sample() -> int:
    """Clear guest list and load SAMPLE_GUESTS (for test data updates)."""
    conn = get_db()
    conn.execute("DELETE FROM guests")
    _insert_sample_guests(conn)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    conn.close()
    return count


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("staff"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def get_guest_by_token(token: str) -> sqlite3.Row | None:
    conn = get_db()
    guest = conn.execute(
        "SELECT * FROM guests WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    return guest


def get_guest_by_id(guest_id: int) -> sqlite3.Row | None:
    conn = get_db()
    guest = conn.execute(
        "SELECT * FROM guests WHERE id = ?", (guest_id,)
    ).fetchone()
    conn.close()
    return guest


def qr_target_url(token: str) -> str:
    """URL encoded in QR — for staff phones after login, not for guests to type."""
    path = url_for("checkin", token=token)
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{path}"
    return request.url_root.rstrip("/") + path


def make_qr_image(data: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def build_ticket_pdf(guest: sqlite3.Row, qr_url: str) -> io.BytesIO:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setFillColorRGB(0.08, 0.18, 0.28)
    pdf.rect(0, height - 45 * mm, width, 45 * mm, fill=1, stroke=0)

    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(20 * mm, height - 22 * mm, EVENT_NAME)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(20 * mm, height - 30 * mm, "Entry & Lunch Pass")

    pdf.setFillColorRGB(0.1, 0.1, 0.1)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(20 * mm, height - 65 * mm, guest["name"])

    lines = [
        f"Meal: {guest['meal']}",
        f"Table: {guest['table_no']}",
        f"Event: {EVENT_DATE}",
        f"Venue: {EVENT_VENUE}",
    ]
    y = height - 80 * mm
    pdf.setFont("Helvetica", 12)
    for line in lines:
        pdf.drawString(20 * mm, y, line)
        y -= 8 * mm

    qr_img = make_qr_image(qr_url)
    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    pdf.drawImage(
        ImageReader(qr_buffer),
        (width - 55 * mm) / 2,
        55 * mm,
        width=55 * mm,
        height=55 * mm,
        mask="auto",
    )

    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColorRGB(0.15, 0.15, 0.15)
    pdf.drawCentredString(width / 2, 42 * mm, "Show this QR at lunch and dinner")
    pdf.setFont("Helvetica", 9)
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    pdf.drawCentredString(
        width / 2,
        34 * mm,
        "One personal QR — valid for 1 lunch + 1 dinner. Do not share.",
    )

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def load_email_config() -> dict | None:
    """Prefer env vars (Render), fall back to local email_config.json."""
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    if resend_key:
        return {
            "provider": "resend",
            "api_key": resend_key,
            "from_email": os.environ.get(
                "SMTP_FROM_EMAIL", "onboarding@resend.dev"
            ).strip(),
            "from_name": os.environ.get("SMTP_FROM_NAME", "Event Desk").strip(),
        }

    env_user = os.environ.get("SMTP_USER", "").strip()
    env_pass = os.environ.get("SMTP_PASSWORD", "").strip().replace(" ", "")
    if env_user and env_pass:
        port_raw = os.environ.get("SMTP_PORT", "587").strip() or "587"
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise RuntimeError(f"SMTP_PORT must be a number, got {port_raw!r}") from exc
        return {
            "provider": "smtp",
            "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
            "smtp_port": port,
            "smtp_user": env_user,
            "smtp_password": env_pass,
            "from_email": os.environ.get("SMTP_FROM_EMAIL", env_user).strip(),
            "from_name": os.environ.get("SMTP_FROM_NAME", "Event Desk").strip(),
        }
    if EMAIL_CONFIG_PATH.exists():
        with EMAIL_CONFIG_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        data["provider"] = data.get("provider", "smtp")
        if data.get("smtp_password"):
            data["smtp_password"] = str(data["smtp_password"]).replace(" ", "")
        return data
    return None


def email_is_configured() -> bool:
    try:
        return load_email_config() is not None
    except Exception:  # noqa: BLE001
        return False


def _email_body(guest: sqlite3.Row) -> str:
    return (
        f"Hi {guest['name']},\n\n"
        f"Please find your QR pass attached for {EVENT_NAME}.\n"
        f"{EVENT_DATE}\n{EVENT_VENUE}\n\n"
        "Show the same QR at lunch and again at dinner.\n"
        "You do not need to open any link.\n\n"
        "See you there!\n"
    )


def _send_via_resend(cfg: dict, guest: sqlite3.Row, pdf_bytes: bytes, filename: str) -> None:
    payload = {
        "from": f"{cfg['from_name']} <{cfg['from_email']}>",
        "to": [guest["email"]],
        "subject": f"Your QR pass - {EVENT_NAME}",
        "text": _email_body(guest),
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            # Required by Resend — missing User-Agent causes 403 error 1010
            "User-Agent": "EventGuestQR/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Resend HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Resend error {exc.code}: {detail}") from exc


def _send_via_smtp(cfg: dict, guest: sqlite3.Row, pdf_bytes: bytes, filename: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Your QR pass - {EVENT_NAME}"
    msg["From"] = f"{cfg.get('from_name', 'Event Desk')} <{cfg['from_email']}>"
    msg["To"] = guest["email"]
    msg.set_content(_email_body(guest))
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )

    host = cfg["smtp_host"]
    port = int(cfg["smtp_port"])
    user = cfg["smtp_user"]
    password = str(cfg["smtp_password"]).replace(" ", "")

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=45, context=context) as server:
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "Gmail login failed. Use a 16-char App Password (no spaces), "
            "not your normal Gmail password."
        ) from exc
    except (smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            "Could not connect to Gmail SMTP. Render free plans often block "
            "port 587. Add RESEND_API_KEY instead (https://resend.com), or "
            "download PDF and send manually."
        ) from exc


def send_pdf_email(guest: sqlite3.Row, pdf_buffer: io.BytesIO) -> None:
    cfg = load_email_config()
    if not cfg:
        raise RuntimeError(
            "Email not configured. Set SMTP_USER + SMTP_PASSWORD, or RESEND_API_KEY."
        )

    pdf_bytes = pdf_buffer.getvalue()
    filename = f"{guest['name'].replace(' ', '_')}_pass.pdf"
    provider = cfg.get("provider", "smtp")

    if provider == "resend":
        _send_via_resend(cfg, guest, pdf_bytes, filename)
    else:
        _send_via_smtp(cfg, guest, pdf_bytes, filename)


def mark_pdf_sent(guest_id: int) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE guests SET pdf_sent = 1, pdf_sent_at = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), guest_id),
    )
    conn.commit()
    conn.close()


@app.context_processor
def inject_event():
    return {
        "event_name": EVENT_NAME,
        "event_date": EVENT_DATE,
        "event_venue": EVENT_VENUE,
        "email_ready": email_is_configured(),
        "staff_logged_in": bool(session.get("staff")),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("staff"):
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == STAFF_USERNAME and check_password_hash(
            STAFF_PASSWORD_HASH, password
        ):
            session["staff"] = True
            nxt = request.args.get("next") or url_for("home")
            return redirect(nxt)
        error = "Invalid staff username or password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    conn = get_db()
    guests = conn.execute("SELECT * FROM guests ORDER BY id").fetchall()
    arrived = sum(1 for g in guests if g["arrived"])
    lunch_claimed = sum(1 for g in guests if g["lunch_claimed"])
    dinner_claimed = sum(1 for g in guests if g["dinner_claimed"])
    sent = sum(1 for g in guests if g["pdf_sent"])
    conn.close()
    return render_template(
        "home.html",
        guests=guests,
        arrived=arrived,
        lunch_claimed=lunch_claimed,
        dinner_claimed=dinner_claimed,
        sent=sent,
        total=len(guests),
    )


@app.route("/reset-guests", methods=["POST"])
@login_required
def reset_guests():
    count = reset_guests_to_sample()
    flash(f"Guest list reset — loaded {count} test guest(s).", "ok")
    return redirect(url_for("home"))


@app.route("/pdf/<token>")
@login_required
def pdf_ticket(token: str):
    guest = get_guest_by_token(token)
    if guest is None:
        abort(404)
    pdf_buffer = build_ticket_pdf(guest, qr_target_url(token))
    filename = f"{guest['name'].replace(' ', '_')}_pass.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/send/<int:guest_id>", methods=["POST"])
@login_required
def send_one(guest_id: int):
    try:
        guest = get_guest_by_id(guest_id)
        if guest is None:
            flash("Guest not found.", "bad")
            return redirect(url_for("home"))
        pdf_buffer = build_ticket_pdf(guest, qr_target_url(guest["token"]))
        send_pdf_email(guest, pdf_buffer)
        mark_pdf_sent(guest_id)
        flash(f"QR PDF sent to {guest['name']} ({guest['email']}).", "ok")
    except Exception as exc:  # noqa: BLE001 — show setup errors to staff
        flash(f"Could not send email: {exc}", "bad")
    return redirect(url_for("home"))


@app.route("/send-all", methods=["POST"])
@login_required
def send_all():
    try:
        conn = get_db()
        guests = conn.execute("SELECT * FROM guests ORDER BY id").fetchall()
        conn.close()

        ok_count = 0
        errors = []
        for guest in guests:
            try:
                pdf_buffer = build_ticket_pdf(guest, qr_target_url(guest["token"]))
                send_pdf_email(guest, pdf_buffer)
                mark_pdf_sent(guest["id"])
                ok_count += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{guest['name']}: {exc}")

        if ok_count:
            flash(f"Sent QR PDFs to {ok_count} guest(s).", "ok")
        if errors:
            flash("Some failed - " + " | ".join(errors[:3]), "bad")
        if not ok_count and not errors:
            flash("No guests to email.", "bad")
    except Exception as exc:  # noqa: BLE001
        flash(f"Send-all failed: {exc}", "bad")
    return redirect(url_for("home"))


def _mark_arrived_if_needed(conn: sqlite3.Connection, guest: sqlite3.Row, token: str, now: str) -> None:
    if not guest["arrived"]:
        conn.execute(
            "UPDATE guests SET arrived = 1, arrived_at = ? WHERE token = ?",
            (now, token),
        )


@app.route("/checkin/<token>", methods=["GET", "POST"])
@login_required
def checkin(token: str):
    guest = get_guest_by_token(token)
    if guest is None:
        return render_template("checkin.html", guest=None, status="invalid"), 404

    message = None
    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()

        if action == "arrive":
            if guest["arrived"]:
                message = "Guest was already marked arrived."
            else:
                conn.execute(
                    "UPDATE guests SET arrived = 1, arrived_at = ? WHERE token = ?",
                    (now, token),
                )
                message = "Guest marked as arrived."
        elif action == "lunch":
            if guest["lunch_claimed"]:
                message = "Lunch was already claimed."
            else:
                _mark_arrived_if_needed(conn, guest, token, now)
                conn.execute(
                    "UPDATE guests SET lunch_claimed = 1, claimed_at = ? WHERE token = ?",
                    (now, token),
                )
                message = "Lunch marked as claimed."
        elif action == "dinner":
            if guest["dinner_claimed"]:
                message = "Dinner was already claimed."
            else:
                _mark_arrived_if_needed(conn, guest, token, now)
                conn.execute(
                    """
                    UPDATE guests
                    SET dinner_claimed = 1, dinner_claimed_at = ?
                    WHERE token = ?
                    """,
                    (now, token),
                )
                message = "Dinner marked as claimed."
        else:
            message = "Unknown action."

        conn.commit()
        conn.close()
        guest = get_guest_by_token(token)

    if guest["lunch_claimed"] and guest["dinner_claimed"]:
        status = "claimed"
    elif guest["lunch_claimed"] or guest["dinner_claimed"] or guest["arrived"]:
        status = "arrived"
    else:
        status = "ok"

    return render_template(
        "checkin.html",
        guest=guest,
        status=status,
        message=message,
    )


@app.route("/scan")
@login_required
def scan():
    return render_template("scan.html")


@app.route("/health")
def health():
    return {"status": "ok"}


# Runs for both `python app.py` and gunicorn (Render / production)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
