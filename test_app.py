"""
Automated tests for ReCampus.   Run with:   python -m unittest -v test_app

They use a throw-away database in a temporary folder, so your real recampus.db
and uploaded images are never touched.
"""
import io
import os
import tempfile
import threading
import unittest

_TMP = tempfile.mkdtemp(prefix="recampus_test_")
os.environ["RECAMPUS_DB"] = os.path.join(_TMP, "test.db")
os.environ["RECAMPUS_UPLOADS"] = os.path.join(_TMP, "uploads")
os.environ["RECAMPUS_SECRET_KEY"] = "test-secret-key"

import app as recampus  # noqa: E402  (must come after the environment variables)

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)   # just enough bytes to look like a PNG


class Client:
    """A tiny wrapper: a Flask test client that sends the CSRF token automatically."""

    def __init__(self):
        self.http = recampus.app.test_client()
        self.csrf = self.http.get("/api/me").get_json()["csrf"]

    def _headers(self):
        return {"X-CSRF-Token": self.csrf}

    def post(self, url, json=None, data=None):
        resp = self.http.post(url, json=json, data=data, headers=self._headers())
        body = resp.get_json()
        if body and "csrf" in body:
            self.csrf = body["csrf"]
        return resp

    def get(self, url):
        return self.http.get(url)


_counter = [0]


def make_user(admin=False):
    _counter[0] += 1
    email = f"student{_counter[0]}@vitstudent.ac.in"
    client = Client()
    resp = client.post("/api/signup", json={"name": f"Student {_counter[0]}", "email": email,
                                            "password": "password123", "confirm": "password123"})
    assert resp.status_code == 201, resp.get_json()
    if admin:
        db = recampus.connect()
        db.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email,))
        db.close()
    client.email = email
    return client


def lost_form(name="Black calculator", priority="Low", **extra):
    form = {"item_name": name, "category": "Electronics", "description": "Casio fx-991", "location": "Library",
            "date_lost": "2026-10-01", "priority": priority, "contact": "me@vitstudent.ac.in"}
    form.update(extra)
    return form


def reset_tables():
    db = recampus.connect()
    for table in ("lost_state_log", "borrow_requests", "lost_reports", "found_reports", "lend_items"):
        db.execute(f"DELETE FROM {table}")
    db.close()


class AuthTests(unittest.TestCase):
    def test_signup_hashes_password_and_signin_works(self):
        user = make_user()
        db = recampus.connect()
        row = db.execute("SELECT password_hash FROM users WHERE email = ?", (user.email,)).fetchone()
        db.close()
        self.assertNotIn("password123", row["password_hash"])
        self.assertTrue(row["password_hash"].startswith("pbkdf2:"))

        fresh = Client()
        self.assertEqual(fresh.post("/api/signin", json={"email": user.email, "password": "wrong-password"}).status_code, 401)
        ok = fresh.post("/api/signin", json={"email": user.email, "password": "password123"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(fresh.get("/api/me").get_json()["user"]["email"], user.email)
        fresh.post("/api/signout")
        self.assertIsNone(fresh.get("/api/me").get_json()["user"])

    def test_signup_rules(self):
        c = Client()
        bad_domain = c.post("/api/signup", json={"name": "A B", "email": "a@gmail.com", "password": "password123", "confirm": "password123"})
        self.assertEqual(bad_domain.status_code, 400)
        short = c.post("/api/signup", json={"name": "A B", "email": "x1@vitstudent.ac.in", "password": "short", "confirm": "short"})
        self.assertEqual(short.status_code, 400)
        mismatch = c.post("/api/signup", json={"name": "A B", "email": "x2@vitstudent.ac.in", "password": "password123", "confirm": "different1"})
        self.assertEqual(mismatch.status_code, 400)
        user = make_user()
        dup = Client().post("/api/signup", json={"name": "A B", "email": user.email.upper(), "password": "password123", "confirm": "password123"})
        self.assertEqual(dup.status_code, 409)

    def test_csrf_required(self):
        c = Client()
        resp = c.http.post("/api/signin", json={"email": "a@vitstudent.ac.in", "password": "x"})   # no token
        self.assertEqual(resp.status_code, 403)

    def test_must_be_signed_in_to_post(self):
        c = Client()
        self.assertEqual(c.post("/api/lost", data=lost_form()).status_code, 401)


class LostAndSchedulerTests(unittest.TestCase):
    def setUp(self):
        reset_tables()
        self.admin = make_user(admin=True)
        self.alice = make_user()
        self.bob = make_user()

    def submit(self, client, **kw):
        resp = client.post("/api/lost", data=lost_form(**kw))
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["item"]

    def test_saved_and_listed_with_image(self):
        form = lost_form("Wallet", "High")
        form["image"] = (io.BytesIO(PNG), "wallet.png")
        resp = self.alice.http.post("/api/lost", data=form, headers=self.alice._headers(), content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 201, resp.get_json())
        item = resp.get_json()["item"]
        self.assertTrue(item["image_url"].startswith("/static/uploads/"))
        self.assertTrue(os.path.exists(os.path.join(recampus.UPLOAD_DIR, os.path.basename(item["image_url"]))))
        listing = Client().get("/api/lost").get_json()["items"]
        self.assertEqual(len(listing), 1)
        self.assertIsNone(listing[0]["contact"], "contact must be hidden from visitors who are not signed in")
        self.assertEqual(self.bob.get("/api/lost").get_json()["items"][0]["contact"], "me@vitstudent.ac.in")

    def test_bad_image_rejected(self):
        form = lost_form()
        form["image"] = (io.BytesIO(b"<?php echo 1; ?>"), "evil.png")
        resp = self.alice.http.post("/api/lost", data=form, headers=self.alice._headers(), content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)
        form["image"] = (io.BytesIO(PNG), "notes.txt")
        resp = self.alice.http.post("/api/lost", data=form, headers=self.alice._headers(), content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_validation(self):
        self.assertEqual(self.alice.post("/api/lost", data=lost_form(priority="Medium")).status_code, 400)
        self.assertEqual(self.alice.post("/api/lost", data=lost_form(date_lost="2999-01-01")).status_code, 400)
        self.assertEqual(self.alice.post("/api/lost", data=lost_form(item_name="")).status_code, 400)
        self.assertEqual(self.alice.post("/api/lost", data=lost_form(category="Spaceships")).status_code, 400)

    def test_priority_then_fcfs_order_and_non_preemption(self):
        a = self.submit(self.alice, item_name="A low first", priority="Low")
        b = self.submit(self.bob, item_name="B low second", priority="Low")
        c = self.submit(self.alice, item_name="C HIGH third", priority="High")
        d = self.submit(self.bob, item_name="D HIGH fourth", priority="High")
        order = [r["item_name"] for r in self.admin.get("/api/scheduler").get_json()["queue"]]
        self.assertEqual(order, ["C HIGH third", "D HIGH fourth", "A low first", "B low second"])

        # first dispatch -> C (High, earliest)
        self.assertEqual(self.admin.post("/api/scheduler/next").status_code, 200)
        state = self.admin.get("/api/scheduler").get_json()
        self.assertEqual(state["running"]["item_name"], "C HIGH third")

        # a NEW High report arrives while C is running: it must NOT interrupt C (non-preemptive)
        self.submit(self.bob, item_name="E HIGH latecomer", priority="High")
        state = self.admin.get("/api/scheduler").get_json()
        self.assertEqual(state["running"]["item_name"], "C HIGH third")
        self.assertEqual(state["queue"][0]["item_name"], "D HIGH fourth")

        # mutual exclusion: starting another while one is Processing is refused
        again = self.admin.post("/api/scheduler/next")
        self.assertEqual(again.status_code, 409)
        self.assertIn("already Processing", again.get_json()["error"])

        # complete -> next in order is D, then E (High FCFS), then A, then B
        served = ["C HIGH third"]
        for _ in range(4):
            self.assertEqual(self.admin.post("/api/scheduler/complete").status_code, 200)
            self.assertEqual(self.admin.post("/api/scheduler/next").status_code, 200)
            served.append(self.admin.get("/api/scheduler").get_json()["running"]["item_name"])
        self.assertEqual(served, ["C HIGH third", "D HIGH fourth", "E HIGH latecomer", "A low first", "B low second"])
        self.admin.post("/api/scheduler/complete")
        self.assertEqual(self.admin.post("/api/scheduler/next").status_code, 404)      # queue empty
        counts = self.admin.get("/api/scheduler").get_json()["counts"]
        self.assertEqual(counts, {"Pending": 0, "Processing": 0, "Completed": 5})

    def test_state_changes_are_saved_in_db(self):
        item = self.submit(self.alice)
        self.admin.post("/api/scheduler/next")
        self.admin.post("/api/scheduler/complete")
        db = recampus.connect()
        status = db.execute("SELECT status FROM lost_reports WHERE id = ?", (item["id"],)).fetchone()["status"]
        log = [(r["from_state"], r["to_state"]) for r in db.execute("SELECT * FROM lost_state_log WHERE report_id = ? ORDER BY id", (item["id"],))]
        db.close()
        self.assertEqual(status, "Completed")
        self.assertEqual(log, [(None, "Pending"), ("Pending", "Processing"), ("Processing", "Completed")])

    def test_permissions(self):
        item = self.submit(self.alice)
        # students cannot run the scheduler
        self.assertEqual(self.alice.post("/api/scheduler/next").status_code, 403)
        self.assertEqual(Client().post("/api/scheduler/next").status_code, 401)
        self.admin.post("/api/scheduler/next")
        # a different student cannot complete Alice's report; Alice (owner) can
        self.assertEqual(self.bob.post(f"/api/lost/{item['id']}/complete").status_code, 403)
        self.assertEqual(self.alice.post(f"/api/lost/{item['id']}/complete").status_code, 200)
        self.assertEqual(self.alice.post(f"/api/lost/{item['id']}/complete").status_code, 409)

    def test_pending_cannot_be_completed_directly(self):
        item = self.submit(self.alice)
        self.assertEqual(self.alice.post(f"/api/lost/{item['id']}/complete").status_code, 409)
        self.assertEqual(self.admin.post(f"/api/lost/{item['id']}/complete").status_code, 409)

    def test_concurrent_next_only_one_wins(self):
        """12 admins press 'Process Next Report' at the same instant. Exactly one may succeed."""
        for i in range(6):
            self.submit(self.alice, item_name=f"Item {i}", priority="High" if i % 2 else "Low")
        admins = [make_user(admin=True) for _ in range(12)]
        results, barrier = [], threading.Barrier(len(admins))

        def press(client):
            barrier.wait()
            results.append(client.post("/api/scheduler/next").status_code)

        threads = [threading.Thread(target=press, args=(c,)) for c in admins]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [200] + [409] * 11, results)
        db = recampus.connect()
        processing = db.execute("SELECT COUNT(*) AS n FROM lost_reports WHERE status = 'Processing'").fetchone()["n"]
        db.close()
        self.assertEqual(processing, 1)

    def test_database_itself_refuses_two_processing_rows(self):
        a, b = self.submit(self.alice), self.submit(self.bob)
        db = recampus.connect()
        db.execute("UPDATE lost_reports SET status = 'Processing' WHERE id = ?", (a["id"],))
        with self.assertRaises(Exception):
            db.execute("UPDATE lost_reports SET status = 'Processing' WHERE id = ?", (b["id"],))
        db.close()


class FoundTests(unittest.TestCase):
    def setUp(self):
        reset_tables()
        self.alice, self.bob, self.admin = make_user(), make_user(), make_user(admin=True)

    def form(self):
        return {"item_name": "Blue umbrella", "category": "Other", "description": "Folded", "location": "Canteen",
                "date_found": "2026-10-02", "contact": "finder@vitstudent.ac.in"}

    def test_flow(self):
        resp = self.alice.post("/api/found", data=self.form())
        self.assertEqual(resp.status_code, 201, resp.get_json())
        item = resp.get_json()["item"]
        self.assertEqual(item["status"], "Pending")
        self.assertEqual(len(Client().get("/api/found").get_json()["items"]), 1)
        self.assertEqual(self.bob.post(f"/api/found/{item['id']}/complete").status_code, 403)
        self.assertEqual(Client().post(f"/api/found/{item['id']}/complete").status_code, 401)
        self.assertEqual(self.alice.post(f"/api/found/{item['id']}/complete").status_code, 200)
        self.assertEqual(self.alice.get("/api/found").get_json()["items"][0]["status"], "Completed")
        self.assertEqual(self.admin.post(f"/api/found/{item['id']}/complete").status_code, 409)

    def test_mine_filter(self):
        self.alice.post("/api/found", data=self.form())
        self.assertEqual(len(self.alice.get("/api/found?mine=1").get_json()["items"]), 1)
        self.assertEqual(len(self.bob.get("/api/found?mine=1").get_json()["items"]), 0)
        self.assertEqual(Client().get("/api/found?mine=1").status_code, 401)


class LendBorrowTests(unittest.TestCase):
    def setUp(self):
        reset_tables()
        self.lender, self.b1, self.b2, self.other, self.admin = (make_user(), make_user(), make_user(), make_user(), make_user(admin=True))
        resp = self.lender.post("/api/lend", data={
            "item_name": "Scientific calculator", "category": "Electronics", "description": "fx-991EX",
            "availability": "Available now", "duration": "1 week", "price": "20", "contact": "lender@vitstudent.ac.in"})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.item = resp.get_json()["item"]

    def request(self, client, item_id=None):
        return client.post("/api/borrow-requests", json={"item_id": item_id or self.item["id"], "message": "pls", "contact": "me@vitstudent.ac.in"})

    def test_listing_visible_and_contact_private(self):
        items = Client().get("/api/lend").get_json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "Available")
        self.assertEqual(items[0]["price"], 20.0)
        self.assertIsNone(items[0]["contact"])
        self.assertEqual(self.lender.get("/api/lend").get_json()["items"][0]["contact"], "lender@vitstudent.ac.in")

    def test_full_resource_lifecycle(self):
        self.assertEqual(self.request(self.b1).status_code, 201)
        self.assertEqual(self.request(self.b1).status_code, 409, "duplicate open request must be refused")
        self.assertEqual(self.request(self.b2).status_code, 201)
        self.assertEqual(self.request(self.lender).status_code, 403, "cannot borrow own item")
        self.assertEqual(self.request(Client()).status_code, 401)

        incoming = self.lender.get("/api/borrow-requests?scope=incoming").get_json()["items"]
        self.assertEqual(len(incoming), 2)
        self.assertEqual(incoming[0]["borrower_contact"], "me@vitstudent.ac.in")
        first, second = incoming[1]["id"], incoming[0]["id"]      # newest first

        # only the lender/admin may decide
        self.assertEqual(self.other.post(f"/api/borrow-requests/{first}/approve").status_code, 403)
        self.assertEqual(self.b1.post(f"/api/borrow-requests/{first}/approve").status_code, 403)

        self.assertEqual(self.lender.post(f"/api/borrow-requests/{first}/approve").status_code, 200)
        self.assertEqual(self.lender.get("/api/lend").get_json()["items"][0]["status"], "Borrowed")
        # the second approval for the same (allocated) resource is refused
        deny = self.lender.post(f"/api/borrow-requests/{second}/approve")
        self.assertEqual(deny.status_code, 409)
        self.assertIn("already allocated", deny.get_json()["error"])
        # nobody can request a Borrowed item
        self.assertEqual(self.request(self.other).status_code, 409)

        # approved borrower sees the lender's contact, the other borrower does not
        mine = self.b1.get("/api/borrow-requests?scope=mine").get_json()["items"][0]
        self.assertEqual((mine["status"], mine["lender_contact"]), ("Approved", "lender@vitstudent.ac.in"))
        self.assertEqual(self.b1.get("/api/lend").get_json()["items"][0]["contact"], "lender@vitstudent.ac.in")
        self.assertIsNone(self.b2.get("/api/lend").get_json()["items"][0]["contact"])

        # return: only lender/admin; resource released
        self.assertEqual(self.b1.post(f"/api/lend/{self.item['id']}/return").status_code, 403)
        self.assertEqual(self.lender.post(f"/api/lend/{self.item['id']}/return").status_code, 200)
        self.assertEqual(self.lender.post(f"/api/lend/{self.item['id']}/return").status_code, 409)
        self.assertEqual(Client().get("/api/lend").get_json()["items"][0]["status"], "Available")
        self.assertEqual(self.b1.get("/api/borrow-requests?scope=mine").get_json()["items"][0]["status"], "Returned")

        # now the waiting request can be approved, and reject works
        self.assertEqual(self.lender.post(f"/api/borrow-requests/{second}/approve").status_code, 200)
        self.assertEqual(self.admin.post(f"/api/lend/{self.item['id']}/return").status_code, 200)   # admin may return too
        self.assertEqual(self.request(self.other).status_code, 201)
        pending = self.lender.get("/api/borrow-requests?scope=incoming").get_json()["items"][0]
        self.assertEqual(self.lender.post(f"/api/borrow-requests/{pending['id']}/reject").status_code, 200)
        self.assertEqual(self.lender.post(f"/api/borrow-requests/{pending['id']}/approve").status_code, 409)

    def test_concurrent_approvals_only_one_wins(self):
        self.request(self.b1)
        self.request(self.b2)
        self.request(self.other)
        ids = [r["id"] for r in self.lender.get("/api/borrow-requests?scope=incoming").get_json()["items"]]
        # lender + admin (+ a second lender session) press Approve on three different requests at once
        sessions = [self.lender, self.admin, self.lender]
        results, barrier = [], threading.Barrier(3)

        def approve(client, rid):
            barrier.wait()
            results.append(client.post(f"/api/borrow-requests/{rid}/approve").status_code)

        threads = [threading.Thread(target=approve, args=(c, rid)) for c, rid in zip(sessions, ids)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [200, 409, 409], results)
        db = recampus.connect()
        approved = db.execute("SELECT COUNT(*) AS n FROM borrow_requests WHERE status = 'Approved'").fetchone()["n"]
        db.close()
        self.assertEqual(approved, 1)

    def test_validation_and_price(self):
        base = {"item_name": "Cable", "category": "Electronics", "description": "USB", "availability": "Available now",
                "duration": "1 day", "contact": "x@vitstudent.ac.in"}
        self.assertEqual(self.lender.post("/api/lend", data={**base, "price": "abc"}).status_code, 400)
        self.assertEqual(self.lender.post("/api/lend", data={**base, "price": "-4"}).status_code, 400)
        free = self.lender.post("/api/lend", data={**base, "price": ""})
        self.assertEqual(free.status_code, 201)
        self.assertIsNone(free.get_json()["item"]["price"])
        self.assertEqual(self.lender.post("/api/lend", data={**base, "duration": "forever"}).status_code, 400)


class MigrationTests(unittest.TestCase):
    def test_old_tables_keep_rows_and_gain_columns(self):
        import sqlite3
        path = os.path.join(_TMP, "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE lost_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL, category TEXT NOT NULL,
                description TEXT NOT NULL, location TEXT NOT NULL, date_lost TEXT NOT NULL);
            INSERT INTO lost_reports (item_name, category, description, location, date_lost)
                VALUES ('Old umbrella', 'Other', 'from the old version', 'Gate', '2026-01-01');
        """)
        conn.close()
        old_path, recampus.DB_PATH = recampus.DB_PATH, path
        try:
            recampus.init_db()
            db = recampus.connect()
            row = db.execute("SELECT * FROM lost_reports").fetchone()
            db.close()
        finally:
            recampus.DB_PATH = old_path
        self.assertEqual(row["item_name"], "Old umbrella")
        self.assertEqual(row["status"], "Pending")
        self.assertEqual(row["priority"], "Low")


class PageTests(unittest.TestCase):
    def test_all_pages_load(self):
        c = recampus.app.test_client()
        for page in ("", "index", "lost", "found", "borrow", "lend", "signin", "signup", "scheduler", "account"):
            url = "/" if page == "" else f"/{page}.html"
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertEqual(c.get("/nothing.html").status_code, 404)
        self.assertEqual(c.get("/static/style.css").status_code, 200)
        self.assertEqual(c.get("/static/script.js").status_code, 200)
        self.assertEqual(c.get("/static/assets/vit-chennai.jpg").status_code, 200)
        self.assertEqual(c.get("/app.py").status_code, 404, "source code must not be downloadable")
        self.assertEqual(c.get("/recampus.db").status_code, 404)


if __name__ == "__main__":
    unittest.main()
