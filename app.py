"""
ReCampus - Flask backend (SQLite database)
==========================================
Run with:   python app.py      then open  http://127.0.0.1:5000

What is in this file (top to bottom):
  1. Configuration
  2. Database helpers (connection, transactions, schema + safe migration)
  3. Small helpers (errors, validation, image upload, login/CSRF checks)
  4. Pages (serves the HTML files in /templates)
  5. Auth API         (sign up, sign in, sign out, who am I)
  6. Lost API         + OS: Non-preemptive Priority Scheduling (with FCFS)
  7. Found API
  8. Lend / Borrow API + OS: exclusive resource allocation
  9. Scheduler dashboard API

OS-concept note (important for the viva):
  Everything OS-related here is an APPLICATION-LEVEL SIMULATION. Lost reports
  are treated like "processes" and lend items like "exclusive resources".
  The real CPU scheduler and kernel are not touched. Mutual exclusion is made
  safe by SQLite transactions (BEGIN IMMEDIATE) plus a UNIQUE index inside the
  database - not by a Python lock, which would only protect one Python process.
"""

import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Flask, abort, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("RECAMPUS_DB", os.path.join(BASE_DIR, "recampus.db"))
UPLOAD_DIR = os.environ.get("RECAMPUS_UPLOADS", os.path.join(BASE_DIR, "static", "uploads"))

ALLOWED_EMAIL_DOMAIN = "@vitstudent.ac.in"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # 5 MB per request
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

CATEGORIES = [
    "Electronics", "Books and notes", "ID cards and wallets", "Keys", "Bags",
    "Clothing and accessories", "Stationery", "Lab equipment", "Other",
]
AVAILABILITY_OPTIONS = ["Available now", "Available from next week", "Weekends only", "Exam season only"]
DURATION_OPTIONS = ["1 day", "3 days", "1 week", "2 weeks", "Until the end of the semester"]

# Which pages exist (file name without .html) -> served from /templates
PAGES = {"index", "lost", "found", "borrow", "lend", "signin", "signup", "scheduler", "account"}


def load_secret_key():
    """Secret key for signing session cookies. Never hard-coded in the source."""
    from_env = os.environ.get("RECAMPUS_SECRET_KEY")
    if from_env:
        return from_env
    key_file = os.path.join(BASE_DIR, "secret_key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as handle:
            stored = handle.read().strip()
            if stored:
                return stored
    new_key = secrets.token_hex(32)
    with open(key_file, "w", encoding="utf-8") as handle:
        handle.write(new_key)
    return new_key


app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(
    SECRET_KEY=load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 512 * 1024,   # image + a little room for the form fields
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SEND_FILE_MAX_AGE_DEFAULT=0,                         # always load the newest CSS/JS while developing
)


# ---------------------------------------------------------------------------
# 2. Database helpers
# ---------------------------------------------------------------------------
def connect():
    """Open a SQLite connection.

    isolation_level=None means "autocommit": Python will NOT start hidden
    transactions, so the explicit BEGIN IMMEDIATE ... COMMIT blocks below are
    the only transactions and behave exactly as written.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")   # wait up to 10 s if another request holds the write lock
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db):
    """BEGIN IMMEDIATE takes SQLite's write lock straight away.

    Two requests can never be inside this block at the same moment: the second
    one waits (busy_timeout) until the first one COMMITs or ROLLs BACK. This is
    the "critical section" of the mutual-exclusion demo.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
    created_at    TEXT NOT NULL
);

-- Each Lost report is a simulated OS "process". Its id is the process id (PID).
-- AUTOINCREMENT guarantees ids only ever go up, so id order == arrival order (FCFS).
CREATE TABLE IF NOT EXISTS lost_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id),
    item_name    TEXT NOT NULL,
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    location     TEXT NOT NULL,
    date_lost    TEXT NOT NULL,
    priority     TEXT NOT NULL DEFAULT 'Low' CHECK (priority IN ('High', 'Low')),
    contact      TEXT NOT NULL,
    image        TEXT,
    status       TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Processing', 'Completed')),
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT
);

-- Every state change of a Lost report is recorded here (Ready -> Running -> Terminated).
CREATE TABLE IF NOT EXISTS lost_state_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES lost_reports(id),
    from_state TEXT,
    to_state   TEXT NOT NULL,
    changed_by INTEGER REFERENCES users(id),
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS found_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id),
    item_name    TEXT NOT NULL,
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    location     TEXT NOT NULL,
    date_found   TEXT NOT NULL,
    contact      TEXT NOT NULL,
    image        TEXT,
    status       TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Completed')),
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

-- Each lend item is a simulated exclusive resource: held by at most one borrower at a time.
CREATE TABLE IF NOT EXISTS lend_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id),
    item_name    TEXT NOT NULL,
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    image        TEXT,
    price        REAL,
    contact      TEXT NOT NULL,
    availability TEXT NOT NULL DEFAULT 'Available now',
    duration     TEXT NOT NULL DEFAULT '1 week',
    status       TEXT NOT NULL DEFAULT 'Available' CHECK (status IN ('Available', 'Borrowed')),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borrow_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES lend_items(id),
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    message     TEXT,
    contact     TEXT,
    status      TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Returned')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  INTEGER REFERENCES users(id),
    returned_at TEXT
);
"""

# Columns that older versions of a table might be missing. init_db() adds them
# with ALTER TABLE so existing rows are kept (safe migration - nothing is dropped).
MIGRATION_COLUMNS = {
    "users": [("role", "TEXT NOT NULL DEFAULT 'student'"), ("created_at", "TEXT NOT NULL DEFAULT ''")],
    "lost_reports": [
        ("user_id", "INTEGER"), ("priority", "TEXT NOT NULL DEFAULT 'Low'"), ("contact", "TEXT NOT NULL DEFAULT ''"),
        ("image", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'Pending'"), ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("started_at", "TEXT"), ("completed_at", "TEXT"),
    ],
    "found_reports": [
        ("user_id", "INTEGER"), ("contact", "TEXT NOT NULL DEFAULT ''"), ("image", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'Pending'"), ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("completed_at", "TEXT"),
    ],
    "lend_items": [
        ("user_id", "INTEGER"), ("image", "TEXT"), ("price", "REAL"), ("contact", "TEXT NOT NULL DEFAULT ''"),
        ("availability", "TEXT NOT NULL DEFAULT 'Available now'"), ("duration", "TEXT NOT NULL DEFAULT '1 week'"),
        ("status", "TEXT NOT NULL DEFAULT 'Available'"), ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ],
    "borrow_requests": [
        ("message", "TEXT"), ("contact", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'Pending'"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"), ("decided_at", "TEXT"), ("decided_by", "INTEGER"),
        ("returned_at", "TEXT"),
    ],
}


def init_db():
    """Create tables if missing, add missing columns, then create the safety indexes."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = connect()
    try:
        db.executescript(SCHEMA_SQL)

        # --- safe migration: add columns that an older database does not have yet
        for table, columns in MIGRATION_COLUMNS.items():
            existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for column, ddl in columns:
                if column not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

        # --- tidy legacy values so the app logic can trust them
        db.execute("UPDATE lost_reports SET priority = 'Low' WHERE priority IS NULL OR priority NOT IN ('High', 'Low')")
        db.execute("UPDATE lost_reports SET status = 'Pending' WHERE status IS NULL OR status NOT IN ('Pending', 'Processing', 'Completed')")
        db.execute("UPDATE lost_reports SET created_at = datetime('now') WHERE created_at = ''")
        db.execute("UPDATE found_reports SET status = 'Pending' WHERE status IS NULL OR status NOT IN ('Pending', 'Completed')")
        db.execute("UPDATE lend_items SET status = 'Available' WHERE status IS NULL OR status NOT IN ('Available', 'Borrowed')")

        # If an old database somehow has several Processing reports, keep only the
        # oldest one running. (Otherwise the UNIQUE index below could not be built.)
        extra = db.execute(
            "SELECT id FROM lost_reports WHERE status = 'Processing' ORDER BY id LIMIT -1 OFFSET 1"
        ).fetchall()
        for row in extra:
            db.execute("UPDATE lost_reports SET status = 'Pending', started_at = NULL WHERE id = ?", (row["id"],))
        extra = db.execute(
            "SELECT id FROM borrow_requests WHERE status = 'Approved' AND id NOT IN "
            "(SELECT MIN(id) FROM borrow_requests WHERE status = 'Approved' GROUP BY item_id)"
        ).fetchall()
        for row in extra:
            db.execute("UPDATE borrow_requests SET status = 'Rejected' WHERE id = ?", (row["id"],))

        # --- database-level mutual exclusion -------------------------------
        # "At most ONE row may have status = 'Processing'": a UNIQUE index that only
        # covers Processing rows. A second Processing row is rejected by SQLite itself.
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_processing_report "
            "ON lost_reports(status) WHERE status = 'Processing'"
        )
        # "At most ONE Approved (active) borrow request per item" = exclusive resource.
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_active_approval_per_item "
            "ON borrow_requests(item_id) WHERE status = 'Approved'"
        )
        # One open (Pending) request per student per item = no duplicate requests.
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request_per_user_item "
            "ON borrow_requests(item_id, borrower_id) WHERE status = 'Pending'"
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_lost_queue ON lost_reports(status, priority, id)")
    finally:
        db.close()


init_db()


# ---------------------------------------------------------------------------
# 3. Helpers: errors, validation, uploads, auth, CSRF
# ---------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


@app.errorhandler(ApiError)
def handle_api_error(err):
    body = {"ok": False, "error": err.message}
    body.update(err.extra)
    return jsonify(body), err.status


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_err):
    return jsonify({"ok": False, "error": "That upload is too large. Images can be at most 5 MB."}), 413


@app.errorhandler(HTTPException)
def handle_http_error(err):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": err.description or err.name}), err.code
    return err


@app.errorhandler(Exception)
def handle_unexpected(err):
    app.logger.exception("Unexpected error: %s", err)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Something went wrong on the server. Please try again."}), 500
    return "Internal server error", 500


@app.after_request
def add_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def current_user():
    """The signed-in user as a dict (read fresh from the DB each request), or None."""
    if "user" in g:
        return g.user
    user = None
    user_id = session.get("user_id")
    if user_id:
        row = get_db().execute("SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if row:
            user = dict(row)
        else:
            session.pop("user_id", None)
    g.user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            raise ApiError("Please sign in to do that.", 401)
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            raise ApiError("Please sign in to do that.", 401)
        if user["role"] != "admin":
            raise ApiError("Only an administrator can run the scheduler.", 403)
        return view(*args, **kwargs)
    return wrapper


def is_admin(user):
    return bool(user) and user["role"] == "admin"


def is_owner(user, row):
    return bool(user) and row["user_id"] == user["id"]


def can_manage(user, row):
    return is_admin(user) or is_owner(user, row)


@app.before_request
def check_csrf():
    """Every state-changing API call must carry the secret token the page got from /api/me.
    A malicious website cannot read that token, so it cannot forge requests for you."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
        expected = session.get("csrf")
        sent = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, sent):
            raise ApiError("Your session expired. Please refresh the page and try again.", 403)


def clean_text(value, label, max_len, required=True, min_len=1):
    value = (value or "").strip()
    if not value:
        if required:
            raise ApiError(f"{label} is required.")
        return None
    if len(value) < min_len:
        raise ApiError(f"{label} must be at least {min_len} characters.")
    if len(value) > max_len:
        raise ApiError(f"{label} must be at most {max_len} characters.")
    return value


def clean_choice(value, label, options):
    value = (value or "").strip()
    if value not in options:
        raise ApiError(f"Please choose a valid {label}.")
    return value


def clean_past_date(value, label):
    value = (value or "").strip()
    if not value:
        raise ApiError(f"{label} is required.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ApiError(f"{label} is not a valid date.")
    if parsed > date.today() + timedelta(days=1):    # +1 day tolerates time-zone differences
        raise ApiError(f"{label} cannot be in the future.")
    if parsed.year < 2000:
        raise ApiError(f"{label} is not a valid date.")
    return parsed.isoformat()


def sniff_image_type(head):
    """Look at the first bytes of the file ("magic numbers") instead of trusting the file name."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(file_storage):
    """Validate and store an optional uploaded image. Returns the new file name or None."""
    if file_storage is None or not file_storage.filename:
        return None
    original = secure_filename(file_storage.filename)
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ApiError("Please upload a PNG, JPG, GIF or WEBP image.")
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    kind = sniff_image_type(head)
    if kind is None:
        raise ApiError("That file does not look like a real image. Please upload a PNG, JPG, GIF or WEBP file.")
    # Never reuse the visitor's file name: a random name cannot clash or escape the folder.
    stored_name = f"{uuid.uuid4().hex}.{kind}"
    file_storage.save(os.path.join(UPLOAD_DIR, stored_name))
    return stored_name


def image_url(name):
    return f"/static/uploads/{name}" if name else None


# --- very small in-memory limiter for failed sign-ins (slows down password guessing)
_failed_logins = {}
_failed_lock = threading.Lock()
MAX_FAILED_LOGINS = 6
LOGIN_WINDOW_SECONDS = 300


def _login_key(email):
    return (request.remote_addr or "?", email)


def too_many_failures(email):
    with _failed_lock:
        stamps = [t for t in _failed_logins.get(_login_key(email), []) if time.time() - t < LOGIN_WINDOW_SECONDS]
        _failed_logins[_login_key(email)] = stamps
        return len(stamps) >= MAX_FAILED_LOGINS


def record_failure(email):
    with _failed_lock:
        _failed_logins.setdefault(_login_key(email), []).append(time.time())


def clear_failures(email):
    with _failed_lock:
        _failed_logins.pop(_login_key(email), None)


# ---------------------------------------------------------------------------
# 4. Pages
# ---------------------------------------------------------------------------
@app.get("/")
def home_page():
    return render_template("index.html")


@app.get("/<page>.html")
def html_page(page):
    if page not in PAGES:
        abort(404)
    return render_template(f"{page}.html")


# ---------------------------------------------------------------------------
# 5. Auth API
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def public_user(user):
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]} if user else None


def new_csrf():
    session["csrf"] = secrets.token_hex(24)
    return session["csrf"]


@app.get("/api/me")
def api_me():
    if not session.get("csrf"):
        new_csrf()
    return jsonify({"ok": True, "user": public_user(current_user()), "csrf": session["csrf"]})


@app.post("/api/signup")
def api_signup():
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), "Name", 60, min_len=2)
    email = clean_text(data.get("email"), "Email", 120).lower()
    password = data.get("password") or ""
    confirm = data.get("confirm") or ""

    if not EMAIL_RE.match(email):
        raise ApiError("Please enter a valid email address.")
    if not email.endswith(ALLOWED_EMAIL_DOMAIN):
        raise ApiError(f"Please use your VIT student email (ending in {ALLOWED_EMAIL_DOMAIN}).")
    if len(password) < 8:
        raise ApiError("Password must be at least 8 characters.")
    if len(password) > 128:
        raise ApiError("Password must be at most 128 characters.")
    if password != confirm:
        raise ApiError("Passwords do not match.")

    db = get_db()
    # The password is hashed (salted PBKDF2-SHA256). The plain password is never stored.
    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    try:
        cursor = db.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'student', ?)",
            (name, email, password_hash, now_utc()),
        )
    except sqlite3.IntegrityError:
        raise ApiError("An account with this email already exists. Try signing in.", 409)

    session.clear()
    session.permanent = True
    session["user_id"] = cursor.lastrowid
    csrf = new_csrf()
    g.pop("user", None)
    return jsonify({"ok": True, "message": "Account created. Welcome to ReCampus!", "user": public_user(current_user()), "csrf": csrf}), 201


_DUMMY_HASH = generate_password_hash("not-a-real-password", method="pbkdf2:sha256")


@app.post("/api/signin")
def api_signin():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise ApiError("Please enter your email and password.")
    if too_many_failures(email):
        raise ApiError("Too many failed attempts. Please wait a few minutes and try again.", 429)

    row = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # Always run one hash check so "unknown email" and "wrong password" take similar time.
    valid = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
    if not row or not valid:
        record_failure(email)
        raise ApiError("Incorrect email or password.", 401)

    clear_failures(email)
    session.clear()
    session.permanent = True
    session["user_id"] = row["id"]
    csrf = new_csrf()
    g.pop("user", None)
    return jsonify({"ok": True, "message": "Signed in successfully.", "user": public_user(current_user()), "csrf": csrf})


@app.post("/api/signout")
def api_signout():
    session.clear()
    return jsonify({"ok": True, "message": "You have been signed out.", "csrf": new_csrf()})


# ---------------------------------------------------------------------------
# 6. Lost API  +  OS Scheduling (Non-preemptive Priority, FCFS for ties)
# ---------------------------------------------------------------------------
# Lower number = served first. High (0) beats Low (1). Equal priority -> smaller id first,
# and a smaller id means the report was submitted earlier (FCFS).
QUEUE_ORDER_SQL = "ORDER BY CASE priority WHEN 'High' THEN 0 ELSE 1 END, id ASC"


def log_transition(db, report_id, from_state, to_state, user_id):
    db.execute(
        "INSERT INTO lost_state_log (report_id, from_state, to_state, changed_by, changed_at) VALUES (?, ?, ?, ?, ?)",
        (report_id, from_state, to_state, user_id, now_utc()),
    )


def lost_to_dict(row, viewer, queue_position=None):
    return {
        "id": row["id"],
        "pid": row["id"],
        "item_name": row["item_name"],
        "category": row["category"],
        "description": row["description"],
        "location": row["location"],
        "date_lost": row["date_lost"],
        "priority": row["priority"],
        "status": row["status"],
        "image_url": image_url(row["image"]),
        "reporter": row["owner_name"] or "Unknown student",
        # Contact details are only sent to signed-in students.
        "contact": row["contact"] if viewer else None,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "queue_position": queue_position,
        "is_mine": is_owner(viewer, row),
        "can_complete": row["status"] == "Processing" and can_manage(viewer, row),
    }


LOST_SELECT = (
    "SELECT l.*, COALESCE(u.name, 'Unknown student') AS owner_name "
    "FROM lost_reports l LEFT JOIN users u ON u.id = l.user_id "
)


def queue_positions(db):
    rows = db.execute(f"SELECT id FROM lost_reports WHERE status = 'Pending' {QUEUE_ORDER_SQL}").fetchall()
    return {row["id"]: index + 1 for index, row in enumerate(rows)}


def scheduler_summary(db):
    running = db.execute("SELECT id, item_name, priority FROM lost_reports WHERE status = 'Processing'").fetchone()
    pending = db.execute("SELECT COUNT(*) AS n FROM lost_reports WHERE status = 'Pending'").fetchone()["n"]
    return {"running": dict(running) if running else None, "pending_count": pending}


@app.get("/api/lost")
def api_lost_list():
    viewer = current_user()
    db = get_db()
    sql, params = LOST_SELECT, []
    if request.args.get("mine"):
        if not viewer:
            raise ApiError("Please sign in to see your reports.", 401)
        sql += "WHERE l.user_id = ? "
        params.append(viewer["id"])
    rows = db.execute(sql + "ORDER BY l.id DESC", params).fetchall()
    positions = queue_positions(db)
    return jsonify({
        "ok": True,
        "items": [lost_to_dict(r, viewer, positions.get(r["id"])) for r in rows],
        "scheduler": scheduler_summary(db),
        "can_schedule": is_admin(viewer),
    })


@app.post("/api/lost")
@login_required
def api_lost_create():
    viewer = current_user()
    form = request.form
    item_name = clean_text(form.get("item_name"), "Item name", 100)
    category = clean_choice(form.get("category"), "category", CATEGORIES)
    description = clean_text(form.get("description"), "Description", 1000)
    location = clean_text(form.get("location"), "Location", 120)
    date_lost = clean_past_date(form.get("date_lost"), "Date lost")
    priority = clean_choice(form.get("priority"), "priority", ["High", "Low"])
    contact = clean_text(form.get("contact"), "Contact information", 120)
    image = save_image(request.files.get("image"))

    db = get_db()
    with transaction(db):
        cursor = db.execute(
            "INSERT INTO lost_reports (user_id, item_name, category, description, location, date_lost, priority, "
            "contact, image, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)",
            (viewer["id"], item_name, category, description, location, date_lost, priority, contact, image, now_utc()),
        )
        # New process enters the READY queue (Pending). It never interrupts a Processing one.
        log_transition(db, cursor.lastrowid, None, "Pending", viewer["id"])
    row = db.execute(LOST_SELECT + "WHERE l.id = ?", (cursor.lastrowid,)).fetchone()
    position = queue_positions(db).get(cursor.lastrowid)
    return jsonify({
        "ok": True,
        "message": f"Lost report saved as PID-{cursor.lastrowid:03d} ({priority} priority). It is waiting in the Pending queue.",
        "item": lost_to_dict(row, viewer, position),
    }), 201


def complete_lost_report(db, report_id, viewer):
    """Processing -> Completed (the running 'process' terminates). Runs as one transaction."""
    with transaction(db):
        row = db.execute("SELECT * FROM lost_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise ApiError("That report does not exist.", 404)
        if not can_manage(viewer, row):
            raise ApiError("Only the person who made this report, or an administrator, can complete it.", 403)
        if row["status"] != "Processing":
            if row["status"] == "Completed":
                raise ApiError("This report is already Completed.", 409)
            raise ApiError("Only the report that is currently Processing can be completed. "
                           "A Pending report must first be started by the scheduler.", 409)
        db.execute("UPDATE lost_reports SET status = 'Completed', completed_at = ? WHERE id = ?", (now_utc(), report_id))
        log_transition(db, report_id, "Processing", "Completed", viewer["id"])
    return row


@app.post("/api/lost/<int:report_id>/complete")
@login_required
def api_lost_complete(report_id):
    row = complete_lost_report(get_db(), report_id, current_user())
    return jsonify({"ok": True, "message": f"PID-{row['id']:03d} \"{row['item_name']}\" is now Completed."})


# ---------------------------------------------------------------------------
# 7. Found API
# ---------------------------------------------------------------------------
def found_to_dict(row, viewer):
    return {
        "id": row["id"],
        "item_name": row["item_name"],
        "category": row["category"],
        "description": row["description"],
        "location": row["location"],
        "date_found": row["date_found"],
        "status": row["status"],
        "image_url": image_url(row["image"]),
        "reporter": row["owner_name"] or "Unknown student",
        "contact": row["contact"] if viewer else None,
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "is_mine": is_owner(viewer, row),
        "can_complete": row["status"] == "Pending" and can_manage(viewer, row),
    }


FOUND_SELECT = (
    "SELECT f.*, COALESCE(u.name, 'Unknown student') AS owner_name "
    "FROM found_reports f LEFT JOIN users u ON u.id = f.user_id "
)


@app.get("/api/found")
def api_found_list():
    viewer = current_user()
    sql, params = FOUND_SELECT, []
    if request.args.get("mine"):
        if not viewer:
            raise ApiError("Please sign in to see your reports.", 401)
        sql += "WHERE f.user_id = ? "
        params.append(viewer["id"])
    rows = get_db().execute(sql + "ORDER BY f.id DESC", params).fetchall()
    return jsonify({"ok": True, "items": [found_to_dict(r, viewer) for r in rows]})


@app.post("/api/found")
@login_required
def api_found_create():
    viewer = current_user()
    form = request.form
    item_name = clean_text(form.get("item_name"), "Item name", 100)
    category = clean_choice(form.get("category"), "category", CATEGORIES)
    description = clean_text(form.get("description"), "Description", 1000)
    location = clean_text(form.get("location"), "Found location", 120)
    date_found = clean_past_date(form.get("date_found"), "Date found")
    contact = clean_text(form.get("contact"), "Contact information", 120)
    image = save_image(request.files.get("image"))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO found_reports (user_id, item_name, category, description, location, date_found, contact, image, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)",
        (viewer["id"], item_name, category, description, location, date_found, contact, image, now_utc()),
    )
    row = db.execute(FOUND_SELECT + "WHERE f.id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"ok": True, "message": "Found report saved. Thank you for helping a fellow student!",
                    "item": found_to_dict(row, viewer)}), 201


@app.post("/api/found/<int:report_id>/complete")
@login_required
def api_found_complete(report_id):
    viewer = current_user()
    db = get_db()
    with transaction(db):
        row = db.execute("SELECT * FROM found_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise ApiError("That report does not exist.", 404)
        if not can_manage(viewer, row):
            raise ApiError("Only the person who made this report, or an administrator, can complete it.", 403)
        if row["status"] == "Completed":
            raise ApiError("This report is already Completed.", 409)
        db.execute("UPDATE found_reports SET status = 'Completed', completed_at = ? WHERE id = ?", (now_utc(), report_id))
    return jsonify({"ok": True, "message": f"\"{row['item_name']}\" is marked Completed."})


# ---------------------------------------------------------------------------
# 8. Lend / Borrow API  +  OS exclusive resource allocation
# ---------------------------------------------------------------------------
LEND_SELECT = """
SELECT i.*, COALESCE(u.name, 'Unknown student') AS owner_name,
       (SELECT COUNT(*) FROM borrow_requests r WHERE r.item_id = i.id AND r.status = 'Pending') AS pending_count,
       (SELECT bu.name FROM borrow_requests r JOIN users bu ON bu.id = r.borrower_id
         WHERE r.item_id = i.id AND r.status = 'Approved') AS holder_name
FROM lend_items i LEFT JOIN users u ON u.id = i.user_id
"""


def lend_to_dict(row, viewer, approved_item_ids, my_open_requests):
    manager = can_manage(viewer, row)
    # The lender's contact is private: only the lender, an admin, or the borrower
    # whose request was approved can see it.
    can_see_contact = manager or row["id"] in approved_item_ids
    return {
        "id": row["id"],
        "item_name": row["item_name"],
        "category": row["category"],
        "description": row["description"],
        "price": row["price"],
        "availability": row["availability"],
        "duration": row["duration"],
        "status": row["status"],
        "image_url": image_url(row["image"]),
        "lender": row["owner_name"],
        "contact": row["contact"] if can_see_contact else None,
        "created_at": row["created_at"],
        "is_mine": is_owner(viewer, row),
        "can_return": row["status"] == "Borrowed" and manager,
        "my_request_status": my_open_requests.get(row["id"]),
        "pending_count": row["pending_count"] if manager else None,
        "holder_name": row["holder_name"] if manager else None,
    }


def viewer_request_maps(db, viewer):
    if not viewer:
        return set(), {}
    rows = db.execute(
        "SELECT item_id, status FROM borrow_requests WHERE borrower_id = ? AND status IN ('Pending', 'Approved')",
        (viewer["id"],),
    ).fetchall()
    approved = {r["item_id"] for r in rows if r["status"] == "Approved"}
    open_requests = {r["item_id"]: r["status"] for r in rows}
    return approved, open_requests


@app.get("/api/lend")
def api_lend_list():
    viewer = current_user()
    db = get_db()
    sql, params = LEND_SELECT, []
    if request.args.get("mine"):
        if not viewer:
            raise ApiError("Please sign in to see your listings.", 401)
        sql += "WHERE i.user_id = ? "
        params.append(viewer["id"])
    rows = db.execute(sql + "ORDER BY i.id DESC", params).fetchall()
    approved, open_requests = viewer_request_maps(db, viewer)
    return jsonify({"ok": True, "items": [lend_to_dict(r, viewer, approved, open_requests) for r in rows]})


@app.post("/api/lend")
@login_required
def api_lend_create():
    viewer = current_user()
    form = request.form
    item_name = clean_text(form.get("item_name"), "Item name", 100)
    category = clean_choice(form.get("category"), "category", CATEGORIES)
    description = clean_text(form.get("description"), "Description", 1000)
    availability = clean_choice(form.get("availability"), "availability", AVAILABILITY_OPTIONS)
    duration = clean_choice(form.get("duration"), "lending duration", DURATION_OPTIONS)
    contact = clean_text(form.get("contact"), "Contact information", 120)

    price_text = (form.get("price") or "").strip()
    price = None
    if price_text:
        try:
            price = round(float(price_text), 2)
        except ValueError:
            raise ApiError("Price must be a number (leave it empty if the item is free).")
        if price < 0 or price > 100000:
            raise ApiError("Price must be between 0 and 100000.")
        if price == 0:
            price = None
    image = save_image(request.files.get("image"))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO lend_items (user_id, item_name, category, description, image, price, contact, availability, "
        "duration, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Available', ?)",
        (viewer["id"], item_name, category, description, image, price, contact, availability, duration, now_utc()),
    )
    row = db.execute(LEND_SELECT + "WHERE i.id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"ok": True, "message": "Your item is listed and now visible on the Borrow page.",
                    "item": lend_to_dict(row, viewer, set(), {})}), 201


@app.post("/api/lend/<int:item_id>/return")
@login_required
def api_lend_return(item_id):
    """RELEASE the resource: Borrowed -> Available. One atomic transaction."""
    viewer = current_user()
    db = get_db()
    with transaction(db):
        item = db.execute("SELECT * FROM lend_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            raise ApiError("That item does not exist.", 404)
        if not can_manage(viewer, item):
            raise ApiError("Only the lender, or an administrator, can mark this item as returned.", 403)
        if item["status"] != "Borrowed":
            raise ApiError("This item is not currently borrowed.", 409)
        db.execute(
            "UPDATE borrow_requests SET status = 'Returned', returned_at = ? WHERE item_id = ? AND status = 'Approved'",
            (now_utc(), item_id),
        )
        db.execute("UPDATE lend_items SET status = 'Available' WHERE id = ?", (item_id,))
    return jsonify({"ok": True, "message": f"\"{item['item_name']}\" was returned and is Available again."})


def request_to_dict(row, viewer, scope):
    manager = is_admin(viewer) or row["lender_id"] == viewer["id"]
    data = {
        "id": row["id"],
        "item_id": row["item_id"],
        "item_name": row["item_name"],
        "item_image_url": image_url(row["item_image"]),
        "item_category": row["item_category"],
        "item_status": row["item_status"],
        "status": row["status"],
        "message": row["message"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "returned_at": row["returned_at"],
        "borrower": row["borrower_name"],
        "lender": row["lender_name"],
        "can_approve": row["status"] == "Pending" and manager,
        "can_reject": row["status"] == "Pending" and manager,
        "can_return": row["status"] == "Approved" and manager,
        # Borrower's handover contact goes to the lender/admin only.
        "borrower_contact": row["contact"] if scope == "incoming" else None,
        # Lender's contact goes to the borrower only after approval.
        "lender_contact": row["lender_contact"] if (scope == "mine" and row["status"] == "Approved") else None,
    }
    return data


REQUEST_SELECT = """
SELECT r.*, i.item_name, i.image AS item_image, i.category AS item_category, i.status AS item_status,
       i.user_id AS lender_id, i.contact AS lender_contact,
       COALESCE(lu.name, 'Unknown student') AS lender_name, COALESCE(bu.name, 'Unknown student') AS borrower_name
FROM borrow_requests r
JOIN lend_items i ON i.id = r.item_id
LEFT JOIN users lu ON lu.id = i.user_id
LEFT JOIN users bu ON bu.id = r.borrower_id
"""


@app.get("/api/borrow-requests")
@login_required
def api_requests_list():
    viewer = current_user()
    scope = request.args.get("scope", "mine")
    if scope not in ("mine", "incoming"):
        raise ApiError("Unknown scope.")
    db = get_db()
    if scope == "mine":
        rows = db.execute(REQUEST_SELECT + "WHERE r.borrower_id = ? ORDER BY r.id DESC", (viewer["id"],)).fetchall()
    elif is_admin(viewer):
        rows = db.execute(REQUEST_SELECT + "ORDER BY r.id DESC").fetchall()
    else:
        rows = db.execute(REQUEST_SELECT + "WHERE i.user_id = ? ORDER BY r.id DESC", (viewer["id"],)).fetchall()
    return jsonify({"ok": True, "items": [request_to_dict(r, viewer, scope) for r in rows]})


@app.post("/api/borrow-requests")
@login_required
def api_request_create():
    viewer = current_user()
    data = request.get_json(silent=True) or {}
    try:
        item_id = int(data.get("item_id"))
    except (TypeError, ValueError):
        raise ApiError("Please choose an item to borrow.")
    message = clean_text(data.get("message"), "Message", 300, required=False)
    contact = clean_text(data.get("contact"), "Contact for hand-over", 120)

    db = get_db()
    with transaction(db):
        item = db.execute("SELECT * FROM lend_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            raise ApiError("That item does not exist.", 404)
        if item["user_id"] == viewer["id"]:
            raise ApiError("You cannot borrow your own item.", 403)
        if item["status"] != "Available":
            raise ApiError("This item is currently Borrowed. Please check again after it is returned.", 409)
        existing = db.execute(
            "SELECT status FROM borrow_requests WHERE item_id = ? AND borrower_id = ? AND status IN ('Pending', 'Approved')",
            (item_id, viewer["id"]),
        ).fetchone()
        if existing:
            raise ApiError("You already have an open request for this item.", 409)
        try:
            cursor = db.execute(
                "INSERT INTO borrow_requests (item_id, borrower_id, message, contact, status, created_at) "
                "VALUES (?, ?, ?, ?, 'Pending', ?)",
                (item_id, viewer["id"], message, contact, now_utc()),
            )
        except sqlite3.IntegrityError:
            raise ApiError("You already have an open request for this item.", 409)
    return jsonify({"ok": True, "message": f"Request sent for \"{item['item_name']}\". The lender will approve or reject it.",
                    "request_id": cursor.lastrowid}), 201


@app.post("/api/borrow-requests/<int:request_id>/approve")
@login_required
def api_request_approve(request_id):
    """ALLOCATE the resource to one borrower. Everything below is one atomic transaction."""
    viewer = current_user()
    db = get_db()
    try:
        with transaction(db):
            req = db.execute(
                "SELECT r.*, i.user_id AS lender_id, i.status AS item_status, i.item_name "
                "FROM borrow_requests r JOIN lend_items i ON i.id = r.item_id WHERE r.id = ?", (request_id,)
            ).fetchone()
            if not req:
                raise ApiError("That request does not exist.", 404)
            if not (is_admin(viewer) or req["lender_id"] == viewer["id"]):
                raise ApiError("Only the lender, or an administrator, can approve this request.", 403)
            if req["status"] != "Pending":
                raise ApiError(f"This request is already {req['status']}.", 409)
            # Is the resource free? (checked inside the lock, so no one can sneak in between check and update)
            if req["item_status"] != "Available":
                raise ApiError("This item is already allocated to another borrower. "
                               "Approve a new request after it has been returned.", 409)
            db.execute(
                "UPDATE borrow_requests SET status = 'Approved', decided_at = ?, decided_by = ? WHERE id = ?",
                (now_utc(), viewer["id"], request_id),
            )
            db.execute("UPDATE lend_items SET status = 'Borrowed' WHERE id = ?", (req["item_id"],))
    except sqlite3.IntegrityError:
        # The UNIQUE index (one Approved request per item) is the last line of defence.
        raise ApiError("This item is already allocated to another borrower.", 409)
    return jsonify({"ok": True, "message": f"Approved. \"{req['item_name']}\" is now Borrowed."})


@app.post("/api/borrow-requests/<int:request_id>/reject")
@login_required
def api_request_reject(request_id):
    viewer = current_user()
    db = get_db()
    with transaction(db):
        req = db.execute(
            "SELECT r.*, i.user_id AS lender_id, i.item_name FROM borrow_requests r "
            "JOIN lend_items i ON i.id = r.item_id WHERE r.id = ?", (request_id,)
        ).fetchone()
        if not req:
            raise ApiError("That request does not exist.", 404)
        if not (is_admin(viewer) or req["lender_id"] == viewer["id"]):
            raise ApiError("Only the lender, or an administrator, can reject this request.", 403)
        if req["status"] != "Pending":
            raise ApiError(f"This request is already {req['status']}.", 409)
        db.execute(
            "UPDATE borrow_requests SET status = 'Rejected', decided_at = ?, decided_by = ? WHERE id = ?",
            (now_utc(), viewer["id"], request_id),
        )
    return jsonify({"ok": True, "message": "Request rejected."})


# ---------------------------------------------------------------------------
# 9. Scheduler dashboard API
# ---------------------------------------------------------------------------
@app.get("/api/scheduler")
def api_scheduler_state():
    viewer = current_user()
    db = get_db()

    running = db.execute(LOST_SELECT + "WHERE l.status = 'Processing'").fetchone()
    pending = db.execute(
        LOST_SELECT + "WHERE l.status = 'Pending' "
        "ORDER BY CASE l.priority WHEN 'High' THEN 0 ELSE 1 END, l.id ASC"
    ).fetchall()
    completed = db.execute(LOST_SELECT + "WHERE l.status = 'Completed' ORDER BY l.completed_at DESC, l.id DESC LIMIT 10").fetchall()
    counts = {
        row["status"]: row["n"]
        for row in db.execute("SELECT status, COUNT(*) AS n FROM lost_reports GROUP BY status")
    }
    log_rows = db.execute(
        "SELECT g.*, l.item_name, l.priority, COALESCE(u.name, 'System') AS actor "
        "FROM lost_state_log g JOIN lost_reports l ON l.id = g.report_id "
        "LEFT JOIN users u ON u.id = g.changed_by ORDER BY g.id DESC LIMIT 15"
    ).fetchall()
    resources = db.execute(
        "SELECT i.id, i.item_name, i.status, "
        "(SELECT COUNT(*) FROM borrow_requests r WHERE r.item_id = i.id AND r.status = 'Pending') AS waiting, "
        "(SELECT bu.name FROM borrow_requests r JOIN users bu ON bu.id = r.borrower_id "
        "  WHERE r.item_id = i.id AND r.status = 'Approved') AS holder "
        "FROM lend_items i ORDER BY i.status DESC, i.id DESC LIMIT 12"
    ).fetchall()

    return jsonify({
        "ok": True,
        "can_schedule": is_admin(viewer),
        "viewer_role": viewer["role"] if viewer else None,
        "counts": {"Pending": counts.get("Pending", 0), "Processing": counts.get("Processing", 0),
                   "Completed": counts.get("Completed", 0)},
        "running": lost_to_dict(running, viewer) if running else None,
        "queue": [lost_to_dict(r, viewer, index + 1) for index, r in enumerate(pending)],
        "completed": [lost_to_dict(r, viewer) for r in completed],
        "log": [
            {"report_id": r["report_id"], "item_name": r["item_name"], "priority": r["priority"],
             "from_state": r["from_state"], "to_state": r["to_state"], "actor": r["actor"], "at": r["changed_at"]}
            for r in log_rows
        ],
        "resources": [
            {"id": r["id"], "item_name": r["item_name"], "status": r["status"], "waiting": r["waiting"],
             "holder": r["holder"] if viewer else None}
            for r in resources
        ],
    })


@app.post("/api/scheduler/next")
@admin_required
def api_scheduler_next():
    """'Process Next Report' = the dispatcher picks the next process from the ready queue.

    Non-preemptive: it only runs when NOTHING is Processing. A running report is never interrupted.
    """
    viewer = current_user()
    db = get_db()
    try:
        with transaction(db):                       # <-- critical section (write lock held)
            running = db.execute("SELECT id, item_name FROM lost_reports WHERE status = 'Processing'").fetchone()
            if running:
                raise ApiError(
                    f"Mutual exclusion: PID-{running['id']:03d} \"{running['item_name']}\" is already Processing. "
                    "Mark it Completed before starting another report.", 409)
            nxt = db.execute(f"SELECT id, item_name, priority FROM lost_reports WHERE status = 'Pending' {QUEUE_ORDER_SQL} LIMIT 1").fetchone()
            if not nxt:
                raise ApiError("The Pending queue is empty. There is nothing to process.", 404)
            db.execute(
                "UPDATE lost_reports SET status = 'Processing', started_at = ? WHERE id = ? AND status = 'Pending'",
                (now_utc(), nxt["id"]),
            )
            log_transition(db, nxt["id"], "Pending", "Processing", viewer["id"])
    except sqlite3.IntegrityError:
        # The UNIQUE index refused a second Processing row: someone else won the race.
        raise ApiError("Mutual exclusion: another report was started at the same moment. Refresh to see it.", 409)
    return jsonify({
        "ok": True,
        "message": f"Dispatched PID-{nxt['id']:03d} \"{nxt['item_name']}\" ({nxt['priority']} priority): Pending -> Processing.",
    })


@app.post("/api/scheduler/complete")
@admin_required
def api_scheduler_complete():
    """'Mark Completed' for whichever report is currently Processing (the process terminates)."""
    db = get_db()
    running = db.execute("SELECT id FROM lost_reports WHERE status = 'Processing'").fetchone()
    if not running:
        raise ApiError("No report is currently Processing.", 409)
    row = complete_lost_report(db, running["id"], current_user())
    return jsonify({"ok": True, "message": f"PID-{row['id']:03d} \"{row['item_name']}\": Processing -> Completed."})


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 127.0.0.1 = only this computer can open the site. debug=True reloads the server when you edit
    # a Python file; set it to False when you are not editing code.
    app.run(host="127.0.0.1", port=5000, debug=True)
