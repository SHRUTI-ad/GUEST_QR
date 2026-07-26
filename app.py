"""Event Guest QR — staff-only guest list, email QR PDFs, scan check-in."""

from __future__ import annotations

import base64
import io
import json
import os
import re
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


# Logins: admin can import Excel; staff can check-in only
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_PASSWORD_HASH = generate_password_hash(ADMIN_PASSWORD)

STAFF_USERNAME = os.environ.get("STAFF_USERNAME", "staff")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD", "event123")
STAFF_PASSWORD_HASH = generate_password_hash(STAFF_PASSWORD)

EVENT_NAME = "Conference Lunch Meetup"
EVENT_DATE = "Saturday, 25 July 2026 · 12:30 PM"
EVENT_VENUE = "Main Hall, City Convention Center"

# Public base URL embedded inside QR codes (must be reachable by staff phones).
# Example: http://10.197.190.212:5050
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Guest categories (badge color). "Delicate" typo maps to Delegate.
CATEGORIES = ("Delegate", "Faculty", "Organizer", "Pharma")
CATEGORY_COLORS = {
    # RGB 0-1 for PDF + CSS class key
    "Delegate": {"rgb": (0.06, 0.43, 0.34), "css": "cat-delegate"},
    "Faculty": {"rgb": (0.11, 0.31, 0.75), "css": "cat-faculty"},
    "Organizer": {"rgb": (0.71, 0.33, 0.04), "css": "cat-organizer"},
    "Pharma": {"rgb": (0.43, 0.16, 0.75), "css": "cat-pharma"},
}
CATEGORY_ALIASES = {
    "delegate": "Delegate",
    "delicate": "Delegate",  # common typo
    "faculty": "Faculty",
    "organizer": "Organizer",
    "organiser": "Organizer",
    "pharma": "Pharma",
    "pharmaceutical": "Pharma",
}


def normalize_category(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Delegate"
    mapped = CATEGORY_ALIASES.get(raw.lower())
    if mapped:
        return mapped
    # Title-case exact match against known list
    for cat in CATEGORIES:
        if raw.lower() == cat.lower():
            return cat
    return "Delegate"


SAMPLE_GUESTS = [
    # Delegate
    {
        "name": "Shruti Anandas",
        "email": "shrutianandas123@gmail.com",
        "phone": "9326305627",
        "specialty": "General Medicine",
        "city": "Ahmedabad",
        "designation": "Delegate",
        "meal": "Vegetarian",
    },
    {
        "name": "Vishal Patel",
        "email": "vickyashokpatel123@gmail.com",
        "phone": "8866325109",
        "specialty": "Critical Care Medicine",
        "city": "Mehsana",
        "designation": "Delegate",
        "meal": "Non-Vegetarian",
    },
    {
        "name": "Aarav Sharma",
        "email": "aarav.sharma.test@example.com",
        "phone": "9876543210",
        "specialty": "Cardiology",
        "city": "Surat",
        "designation": "Delegate",
        "meal": "Vegetarian",
    },
    # Faculty
    {
        "name": "Rohan Mehta",
        "email": "rohan.mehta.test@example.com",
        "phone": "9988776655",
        "specialty": "Orthopedics",
        "city": "Rajkot",
        "designation": "Faculty",
        "meal": "Vegan",
    },
    {
        "name": "Dr. Neha Kapoor",
        "email": "neha.kapoor.test@example.com",
        "phone": "9900112233",
        "specialty": "Cardiology",
        "city": "Ahmedabad",
        "designation": "Faculty",
        "meal": "Vegetarian",
    },
    {
        "name": "Dr. Sameer Joshi",
        "email": "sameer.joshi.test@example.com",
        "phone": "9911223344",
        "specialty": "Neurology",
        "city": "Vadodara",
        "designation": "Faculty",
        "meal": "Non-Vegetarian",
    },
    # Organizer
    {
        "name": "Ananya Gupta",
        "email": "ananya.gupta.test@example.com",
        "phone": "9811122233",
        "specialty": "Event Ops",
        "city": "Ahmedabad",
        "designation": "Organizer",
        "meal": "Vegetarian",
    },
    {
        "name": "Karan Desai",
        "email": "karan.desai.test@example.com",
        "phone": "9822233344",
        "specialty": "Registration Desk",
        "city": "Ahmedabad",
        "designation": "Organizer",
        "meal": "Non-Vegetarian",
    },
    {
        "name": "Pooja Shah",
        "email": "pooja.shah.test@example.com",
        "phone": "9833344455",
        "specialty": "Hospitality",
        "city": "Gandhinagar",
        "designation": "Organizer",
        "meal": "Vegetarian",
    },
    # Pharma
    {
        "name": "Meera Nair",
        "email": "meera.nair.test@example.com",
        "phone": "9844455566",
        "specialty": "Medical Affairs",
        "city": "Mumbai",
        "designation": "Pharma",
        "meal": "Vegan",
    },
    {
        "name": "Rahul Verma",
        "email": "rahul.verma.test@example.com",
        "phone": "9855566677",
        "specialty": "Sales",
        "city": "Pune",
        "designation": "Pharma",
        "meal": "Non-Vegetarian",
    },
    {
        "name": "Isha Trivedi",
        "email": "isha.trivedi.test@example.com",
        "phone": "9866677788",
        "specialty": "Marketing",
        "city": "Ahmedabad",
        "designation": "Pharma",
        "meal": "Vegetarian",
    },
]

SAMPLE_XLSX = BASE_DIR / "guests_categories_sample.xlsx"
if not SAMPLE_XLSX.exists():
    SAMPLE_XLSX = BASE_DIR / "guests_badge_sample.xlsx"
if not SAMPLE_XLSX.exists():
    SAMPLE_XLSX = BASE_DIR / "guests_sample.xlsx"


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
    _ensure_column(conn, "guests", "email_acked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "guests", "email_acked_at", "TEXT")
    _ensure_column(conn, "guests", "specialty", "TEXT DEFAULT ''")
    _ensure_column(conn, "guests", "city", "TEXT DEFAULT ''")
    _ensure_column(conn, "guests", "designation", "TEXT DEFAULT ''")

    count = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    if count == 0:
        _insert_sample_guests(conn)
    conn.commit()
    conn.close()


def _guest_insert_values(guest: dict) -> tuple:
    return (
        guest["name"],
        guest["email"],
        guest.get("phone") or "",
        guest.get("meal") or "Vegetarian",
        guest.get("table_no") or "",
        guest.get("specialty") or "",
        guest.get("city") or "",
        normalize_category(guest.get("designation") or guest.get("category")),
        uuid.uuid4().hex,
    )


def _insert_sample_guests(conn: sqlite3.Connection) -> None:
    for guest in SAMPLE_GUESTS:
        conn.execute(
            """
            INSERT INTO guests
            (name, email, phone, meal, table_no, specialty, city, designation, token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _guest_insert_values(guest),
        )


def replace_guests_from_rows(rows: list[dict]) -> int:
    conn = get_db()
    conn.execute("DELETE FROM guests")
    for guest in rows:
        conn.execute(
            """
            INSERT INTO guests
            (name, email, phone, meal, table_no, specialty, city, designation, token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _guest_insert_values(guest),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    conn.close()
    return count


def read_guests_from_excel(file_storage) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(file_storage, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        raise RuntimeError("Excel file is empty.")

    normalized = [str(h or "").strip().lower() for h in header]
    aliases = {
        "name": {"name", "guest", "guest name"},
        "email": {"email", "email id", "mail"},
        "phone": {"phone", "mobile", "contact", "phone number"},
        "specialty": {"specialty", "speciality", "field", "department"},
        "city": {"city", "place", "location"},
        "designation": {
            "designation",
            "role",
            "category",
            "type",
            "group",
            "badge",
        },
        "meal": {"meal", "meal preference", "preference", "food"},
    }

    def find_col(keys: set[str]) -> int | None:
        for i, name in enumerate(normalized):
            if name in keys:
                return i
        return None

    idx = {key: find_col(vals) for key, vals in aliases.items()}
    if idx["name"] is None or idx["email"] is None:
        raise RuntimeError(
            "Excel must have Name and Email columns "
            "(optional: Phone, Specialty, City, Designation, Meal)."
        )

    def cell(row, key: str, default: str = "") -> str:
        i = idx[key]
        if i is None:
            return default
        return str(row[i] or "").strip() or default

    guests: list[dict] = []
    for row in rows_iter:
        if not row:
            continue
        name = cell(row, "name")
        email = cell(row, "email")
        if not name and not email:
            continue
        if not name or not email or "@" not in email:
            continue
        guests.append(
            {
                "name": name,
                "email": email,
                "phone": cell(row, "phone"),
                "specialty": cell(row, "specialty"),
                "city": cell(row, "city"),
                "designation": normalize_category(cell(row, "designation", "Delegate")),
                "meal": cell(row, "meal", "Vegetarian"),
                "table_no": "",
            }
        )
    wb.close()
    if not guests:
        raise RuntimeError("No valid guest rows found in Excel.")
    if len(guests) > 2000:
        raise RuntimeError("Too many rows (max 2000). Split the file.")
    return guests


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            flash("Only admin can import guest data.", "bad")
            return redirect(url_for("home"))
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


def resolve_guest_code(raw: str) -> sqlite3.Row | None:
    """Resolve full token, check-in URL, or short badge ID (first 8 of token)."""
    value = (raw or "").strip()
    if not value:
        return None

    match = re.search(r"/checkin/([a-fA-F0-9]{32})", value)
    if match:
        return get_guest_by_token(match.group(1).lower())

    code = value.replace(" ", "").lower()
    if re.fullmatch(r"[a-f0-9]{32}", code):
        return get_guest_by_token(code)

    # Short ID printed on PDF (e.g. 9168B65B)
    if re.fullmatch(r"[a-f0-9]{6,12}", code):
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM guests WHERE lower(substr(token, 1, ?)) = ?",
            (len(code), code),
        ).fetchall()
        conn.close()
        if len(rows) == 1:
            return rows[0]
        return None

    return None


def public_url(endpoint: str, **values) -> str:
    path = url_for(endpoint, **values)
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{path}"
    return request.url_root.rstrip("/") + path


def guest_qr_url(token: str) -> str:
    """One QR / one guest ID — used for both lunch and dinner check-in."""
    return public_url("checkin", token=token)


def make_qr_image(data: str):
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def _guest_field(guest: sqlite3.Row, key: str, default: str = "") -> str:
    try:
        value = guest[key]
    except (KeyError, IndexError):
        return default
    return (str(value).strip() if value is not None else "") or default


def category_style(guest: sqlite3.Row | dict) -> dict:
    if isinstance(guest, dict):
        cat = normalize_category(guest.get("designation"))
    else:
        cat = normalize_category(_guest_field(guest, "designation", "Delegate"))
    return {"name": cat, **CATEGORY_COLORS[cat]}


def build_ticket_pdf(guest: sqlite3.Row) -> io.BytesIO:
    """Badge-style PDF: name, specialty, city, category color + one shared QR."""
    badge_w, badge_h = 105 * mm, 160 * mm
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(badge_w, badge_h))

    style = category_style(guest)
    accent = style["rgb"]
    accent_dark = tuple(max(0.0, c - 0.12) for c in accent)
    ink = (0.12, 0.10, 0.16)

    # Background
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, badge_w, badge_h, fill=1, stroke=0)

    # Header band (category color)
    pdf.setFillColorRGB(*accent)
    pdf.rect(0, badge_h - 38 * mm, badge_w, 38 * mm, fill=1, stroke=0)

    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(badge_w / 2, badge_h - 14 * mm, EVENT_NAME)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawCentredString(badge_w / 2, badge_h - 21 * mm, EVENT_VENUE)
    pdf.drawCentredString(badge_w / 2, badge_h - 28 * mm, EVENT_DATE)

    # Guest details (no table number)
    name = _guest_field(guest, "name").upper()
    specialty = _guest_field(guest, "specialty").upper()
    city = _guest_field(guest, "city").upper()
    designation = style["name"].upper()
    code = (_guest_field(guest, "token")[:8] or "GUEST").upper()

    pdf.setFillColorRGB(*ink)
    pdf.setFont("Helvetica-Bold", 14)
    # Wrap long names
    max_chars = 22
    if len(name) <= max_chars:
        pdf.drawCentredString(badge_w / 2, badge_h - 52 * mm, name)
        detail_y = badge_h - 62 * mm
    else:
        pdf.drawCentredString(badge_w / 2, badge_h - 50 * mm, name[:max_chars])
        pdf.drawCentredString(badge_w / 2, badge_h - 57 * mm, name[max_chars: max_chars * 2])
        detail_y = badge_h - 67 * mm

    pdf.setFont("Helvetica", 9)
    if specialty:
        pdf.drawCentredString(badge_w / 2, detail_y, specialty)
        detail_y -= 6 * mm
    if city:
        pdf.drawCentredString(badge_w / 2, detail_y, city)
        detail_y -= 7 * mm

    # One shared guest ID + QR (works for lunch and dinner)
    pdf.setFillColorRGB(*accent)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(badge_w / 2, detail_y, code)

    qr_size = 42 * mm
    qr_y = 28 * mm
    qr_x = (badge_w - qr_size) / 2
    qr_img = make_qr_image(guest_qr_url(guest["token"]))
    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    pdf.drawImage(
        ImageReader(qr_buffer),
        qr_x,
        qr_y,
        width=qr_size,
        height=qr_size,
        mask="auto",
    )
    pdf.setFillColorRGB(*accent_dark)
    pdf.setFont("Helvetica", 7)
    pdf.drawCentredString(badge_w / 2, qr_y - 5 * mm, "Scan for lunch & dinner")

    # Footer band with category
    pdf.setFillColorRGB(*accent_dark)
    pdf.rect(0, 0, badge_w, 16 * mm, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(badge_w / 2, 6 * mm, designation)

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


def _ack_url(guest: sqlite3.Row) -> str:
    return public_url("acknowledge", token=guest["token"])


def _email_body_text(guest: sqlite3.Row) -> str:
    ack = _ack_url(guest)
    return (
        f"Hi {guest['name']},\n\n"
        f"Please find your badge PDF attached for {EVENT_NAME}.\n"
        f"{EVENT_DATE}\n{EVENT_VENUE}\n\n"
        "Your badge has one QR (guest ID). "
        "Show the same QR at lunch and dinner counters.\n\n"
        "Please confirm you received this pass by opening this link:\n"
        f"{ack}\n\n"
        "See you there!\n"
    )


def _email_body_html(guest: sqlite3.Row) -> str:
    ack = _ack_url(guest)
    return f"""
    <p>Hi {guest['name']},</p>
    <p>Please find your badge PDF attached for <strong>{EVENT_NAME}</strong>.</p>
    <p>{EVENT_DATE}<br/>{EVENT_VENUE}</p>
    <p>Your badge has <strong>one QR</strong> (guest ID).
       Show the same QR at lunch and dinner counters.</p>
    <p>
      <a href="{ack}"
         style="display:inline-block;background:#5b2d8e;color:#fff;padding:12px 18px;
                border-radius:8px;text-decoration:none;font-weight:600;">
        I received my QR pass
      </a>
    </p>
    <p style="color:#666;font-size:13px;">If the button does not work, open:<br/>{ack}</p>
    """


def _send_via_resend(cfg: dict, guest: sqlite3.Row, pdf_bytes: bytes, filename: str) -> None:
    payload = {
        "from": f"{cfg['from_name']} <{cfg['from_email']}>",
        "to": [guest["email"]],
        "subject": f"Your QR pass - {EVENT_NAME}",
        "text": _email_body_text(guest),
        "html": _email_body_html(guest),
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


def _smtp_send_message(
    host: str, port: int, user: str, password: str, msg: EmailMessage
) -> None:
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)


def _send_via_smtp(cfg: dict, guest: sqlite3.Row, pdf_bytes: bytes, filename: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Your QR pass - {EVENT_NAME}"
    msg["From"] = f"{cfg.get('from_name', 'Event Desk')} <{cfg['from_email']}>"
    msg["To"] = guest["email"]
    msg.set_content(_email_body_text(guest))
    msg.add_alternative(_email_body_html(guest), subtype="html")
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

    # Try configured port, then the other common Gmail port
    ports_to_try = [port]
    for alt in (465, 587):
        if alt not in ports_to_try:
            ports_to_try.append(alt)

    last_error: Exception | None = None
    for try_port in ports_to_try:
        try:
            _smtp_send_message(host, try_port, user, password, msg)
            return
        except smtplib.SMTPAuthenticationError as exc:
            raise RuntimeError(
                "Gmail login failed. Use a 16-character Google App Password "
                "(Google Account -> Security -> 2-Step Verification -> App passwords). "
                "Do not use your normal Gmail password."
            ) from exc
        except (smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
            last_error = exc
            continue

    raise RuntimeError(
        "Could not connect to Gmail SMTP (ports 587/465 blocked or network issue). "
        "Try: turn OFF office VPN, use home/mobile hotspot, then retry. "
        f"Technical: {last_error}"
    ) from last_error


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
    role = session.get("role")
    return {
        "event_name": EVENT_NAME,
        "event_date": EVENT_DATE,
        "event_venue": EVENT_VENUE,
        "email_ready": email_is_configured(),
        "staff_logged_in": bool(session.get("user")),
        "is_admin": role == "admin",
        "user_role": role,
        "categories": CATEGORIES,
        "category_colors": CATEGORY_COLORS,
        "normalize_category": normalize_category,
        "category_style": category_style,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(
            ADMIN_PASSWORD_HASH, password
        ):
            session["user"] = True
            session["role"] = "admin"
            nxt = request.args.get("next") or url_for("home")
            return redirect(nxt)
        if username == STAFF_USERNAME and check_password_hash(
            STAFF_PASSWORD_HASH, password
        ):
            session["user"] = True
            session["role"] = "staff"
            nxt = request.args.get("next") or url_for("home")
            return redirect(nxt)
        error = "Invalid username or password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    q = (request.args.get("q") or "").strip()
    raw_cat = (request.args.get("category") or "").strip()
    category = (
        normalize_category(raw_cat)
        if raw_cat and raw_cat.lower() != "all"
        else ""
    )
    status = (request.args.get("status") or "").strip().lower()
    if status not in {"", "all", "arrived", "lunch", "dinner"}:
        status = ""
    if status == "all":
        status = ""

    conn = get_db()
    all_guests = conn.execute("SELECT * FROM guests ORDER BY id").fetchall()
    category_counts = {
        cat: sum(1 for g in all_guests if normalize_category(g["designation"]) == cat)
        for cat in CATEGORIES
    }

    # Counts + list scoped to selected category (if any)
    scoped = list(all_guests)
    if category:
        scoped = [
            g for g in scoped if normalize_category(g["designation"]) == category
        ]

    arrived = sum(1 for g in scoped if g["arrived"])
    lunch_claimed = sum(1 for g in scoped if g["lunch_claimed"])
    dinner_claimed = sum(1 for g in scoped if g["dinner_claimed"])

    guests = scoped
    if status == "arrived":
        guests = [g for g in guests if g["arrived"]]
    elif status == "lunch":
        guests = [g for g in guests if g["lunch_claimed"]]
    elif status == "dinner":
        guests = [g for g in guests if g["dinner_claimed"]]

    if q:
        needle = q.lower()
        guests = [
            g
            for g in guests
            if needle in (g["name"] or "").lower()
            or needle in (g["email"] or "").lower()
            or needle in (g["phone"] or "").lower()
            or needle in (g["specialty"] or "").lower()
            or needle in (g["city"] or "").lower()
            or needle in (g["designation"] or "").lower()
            or needle in (g["meal"] or "").lower()
        ]
    conn.close()

    return render_template(
        "home.html",
        guests=guests,
        arrived=arrived,
        lunch_claimed=lunch_claimed,
        dinner_claimed=dinner_claimed,
        total=len(all_guests),
        scoped_total=len(scoped),
        shown=len(guests),
        q=q,
        category=category,
        status=status,
        category_counts=category_counts,
    )


@app.route("/upload-excel", methods=["POST"])
@admin_required
def upload_excel():
    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Please choose an Excel (.xlsx) file.", "bad")
        return redirect(url_for("home"))
    if not file.filename.lower().endswith(".xlsx"):
        flash("Only .xlsx files are supported.", "bad")
        return redirect(url_for("home"))
    try:
        rows = read_guests_from_excel(file)
        count = replace_guests_from_rows(rows)
        flash(
            f"Imported {count} guest(s) from Excel. Previous list was replaced.",
            "ok",
        )
    except Exception as exc:  # noqa: BLE001
        flash(f"Excel upload failed: {exc}", "bad")
    return redirect(url_for("home"))


@app.route("/sample-excel")
@admin_required
def sample_excel():
    if SAMPLE_XLSX.exists():
        return send_file(
            SAMPLE_XLSX,
            as_attachment=True,
            download_name="guests_sample.xlsx",
        )
    flash("Sample Excel file is missing on the server.", "bad")
    return redirect(url_for("home"))


@app.route("/ack/<token>")
def acknowledge(token: str):
    """Guest clicks from email — no staff login required."""
    guest = get_guest_by_token(token)
    if guest is None:
        return render_template("ack.html", guest=None, already=False), 404

    already = bool(guest["email_acked"])
    if not already:
        conn = get_db()
        conn.execute(
            """
            UPDATE guests
            SET email_acked = 1, email_acked_at = ?
            WHERE token = ?
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), token),
        )
        conn.commit()
        conn.close()
        guest = get_guest_by_token(token)

    return render_template("ack.html", guest=guest, already=already)


@app.route("/pdf/<token>")
@login_required
def pdf_ticket(token: str):
    guest = get_guest_by_token(token)
    if guest is None:
        abort(404)
    pdf_buffer = build_ticket_pdf(guest)
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
        pdf_buffer = build_ticket_pdf(guest)
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
                pdf_buffer = build_ticket_pdf(guest)
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
    focus_meal = (request.args.get("meal") or request.form.get("meal") or "").lower()
    if focus_meal not in {"lunch", "dinner"}:
        focus_meal = ""

    if guest is None:
        return (
            render_template(
                "checkin.html", guest=None, status="invalid", focus_meal=focus_meal
            ),
            404,
        )

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
        focus_meal=focus_meal,
    )


@app.route("/scan", methods=["GET", "POST"])
@login_required
def scan():
    if request.method == "POST":
        guest = resolve_guest_code(request.form.get("code", ""))
        if guest:
            return redirect(url_for("checkin", token=guest["token"]))
        flash(
            "No guest found for that ID. Use the 8-character code on the badge "
            "(e.g. 9168B65B), or paste the full QR link.",
            "error",
        )
        return redirect(url_for("scan"))
    return render_template("scan.html")


@app.route("/health")
def health():
    return {"status": "ok"}


# Runs for both `python app.py` and gunicorn (Render / production)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
