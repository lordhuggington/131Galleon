"""Admin command tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff" + b"\x00" * 200


ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR")  # set here; restored in tearDown so the suite stays order-independent


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "cli.db")
        # password_login boots the real app, whose lifespan creates the photos dir — keep it in the temp dir.
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        from app import cli
        self.cli = cli
        self.run_cli(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner",
                      "--password", "owner-pass"])

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, argv: list[str]) -> str:
        """Run an admin command with its output captured, so the suite prints only unittest's own lines.

        argparse's usage text goes to stderr, so that is swallowed too; the messages tests assert on
        travel in SystemExit, not in either stream.
        """
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.cli.main(argv)
        return out.getvalue()

    def password_login(self, username: str, password: str) -> int:
        """Ask the real API whether a password works — that is what the break-glass is for."""
        from starlette.testclient import TestClient
        from app.main import create_app
        with TestClient(create_app()) as c:
            return c.post("/api/login", json={"username": username, "password": password},
                          headers={"x-hrs": "1"}).status_code

    def row(self, username: str):
        from app.db import connect
        conn = connect()
        try:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        finally:
            conn.close()

    def test_create_staff_without_a_password(self):
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--label", "Housekeeper", "--door-code", "4821", "--phone", "(310) 555-1234"])
        r = self.row("maria")
        self.assertEqual(r["role"], "staff")
        self.assertEqual(r["label"], "Housekeeper")
        self.assertEqual(r["door_code"], "4821")
        self.assertEqual(r["phone"], "+13105551234")
        self.assertIsNone(r["password_hash"])

    def test_set_phone_clears_the_password_and_sessions(self):
        from app.auth import create_session
        from app.db import connect
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--password", "staff-pass"])
        conn = connect()
        try:
            create_session(conn, self.row("maria")["id"])
        finally:
            conn.close()
        self.run_cli(["set-phone", "--username", "maria", "--phone", "310 555 1234"])
        r = self.row("maria")
        self.assertEqual(r["phone"], "+13105551234")
        self.assertIsNone(r["password_hash"])
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (r["id"],)).fetchone()[0], 0)
        finally:
            conn.close()
        with self.assertRaises(SystemExit):
            self.run_cli(["set-phone", "--username", "maria", "--phone", "nonsense"])
        with self.assertRaises(SystemExit):  # owen would collide with maria's number
            self.run_cli(["set-phone", "--username", "owen", "--phone", "+13105551234"])

    def test_set_password_works_for_owners_and_for_staff_with_no_phone(self):
        """The break-glass when text messages stop working (spec §6.5, README "Text-message sign-in")."""
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff"])
        self.run_cli(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        from app.auth import verify_password
        self.assertTrue(verify_password("long-enough-pw", self.row("maria")["password_hash"]))
        self.assertEqual(self.password_login("maria", "long-enough-pw"), 200)
        self.run_cli(["set-password", "--username", "owen", "--password", "another-long-pw"])
        self.assertTrue(verify_password("another-long-pw", self.row("owen")["password_hash"]))

    def test_set_password_is_refused_for_staff_who_have_a_phone(self):
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        self.assertEqual(str(caught.exception),
                         "'maria' signs in with a code texted to their phone. Clear their number with "
                         "`set-phone --clear` first if they need a password.")
        self.assertIsNone(self.row("maria")["password_hash"])

    def test_clearing_a_phone_signs_them_out_and_re_opens_the_password_path(self):
        from app.auth import create_session
        from app.db import connect
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        maria = self.row("maria")
        conn = connect()
        try:
            create_session(conn, maria["id"])
        finally:
            conn.close()
        self.run_cli(["set-phone", "--username", "maria", "--clear"])
        self.assertIsNone(self.row("maria")["phone"])
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                                          (maria["id"],)).fetchone()[0], 0)
        finally:
            conn.close()
        self.run_cli(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        self.assertEqual(self.password_login("maria", "long-enough-pw"), 200)

    def test_clearing_a_phone_leaves_an_owners_password_alone(self):
        self.run_cli(["set-phone", "--username", "owen", "--phone", "310 555 1234"])
        self.run_cli(["set-phone", "--username", "owen", "--clear"])
        self.assertIsNone(self.row("owen")["phone"])
        self.assertEqual(self.password_login("owen", "owner-pass"), 200)

    def test_set_phone_needs_either_a_number_or_clear(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(["set-phone", "--username", "owen"])
        self.assertEqual(caught.exception.code, 2)  # argparse usage error

    def test_only_owners_are_prompted_for_a_password(self):
        with mock.patch("getpass.getpass", return_value="prompted-password") as asked:
            self.run_cli(["create-user", "--username", "kate", "--name", "Kate", "--role", "owner"])
            self.assertTrue(asked.called)
            asked.reset_mock()
            self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff"])
            self.assertFalse(asked.called)  # staff sign in by text; there is nothing to ask for
        from app.auth import verify_password
        self.assertTrue(verify_password("prompted-password", self.row("kate")["password_hash"]))
        self.assertIsNone(self.row("maria")["password_hash"])

    def test_staff_who_have_a_phone_keep_no_password_hash(self):
        # login() refuses a password from staff who have a phone, so storing one would leave an
        # unusable credential behind. The CLI still creates them, but says what it ignored;
        # /api/users refuses the request outright.
        out = self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                            "--phone", "310 555 1234", "--password", "staff-pass"])
        self.assertIn("Ignored --password: staff sign in by text.", out)
        self.assertIsNone(self.row("maria")["password_hash"])

    def test_a_door_code_is_four_to_eight_ascii_digits(self):
        for bad in ("123", "123456789", "48a1", "٤٨٢١"):  # the last is Arabic-Indic: a keypad has no such key
            with self.subTest(code=bad):
                with self.assertRaises(SystemExit) as caught:
                    self.run_cli(["create-user", "--username", "dana", "--name", "Dana", "--role", "staff",
                                   "--door-code", bad])
                self.assertEqual(str(caught.exception), "A door code is 4 to 8 digits.")
                self.assertIsNone(self.row("dana"))

    def test_a_duplicate_phone_is_refused_without_echoing_the_number(self):
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(["create-user", "--username", "dana", "--name", "Dana", "--role", "staff",
                           "--phone", "(310) 555-1234"])
        self.assertNotIn("3105551234", str(caught.exception))
        self.assertIsNone(self.row("dana"))


    def count(self, sql: str, *params) -> int:
        from app.db import connect
        conn = connect()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def test_create_user_kind(self):
        self.run_cli(["create-user", "--username", "ondrej", "--name", "Ondrej", "--role", "staff",
                       "--label", "Builder", "--kind", "builder"])
        self.run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff"])
        self.assertEqual(self.row("ondrej")["kind"], "builder")
        self.assertEqual(self.row("maria")["kind"], "housekeeper")  # the default
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(["create-user", "--username", "pat", "--role", "staff", "--kind", "plumber"])
        self.assertEqual(caught.exception.code, 2)  # argparse refuses it

    def test_import_seed_loads_the_jobs_once(self):
        out = self.run_cli(["import-seed", str(ROOT / "seed")])
        self.assertIn("Tasks: 40", out)
        self.assertIn("Jobs: 16", out)
        self.assertNotIn("Meal", out)
        titles = self.titles()
        self.assertEqual(len(titles), 16)
        self.assertTrue(titles[3].startswith("Stairs"))  # job 4 is the stairs, in the seed's order
        out = self.run_cli(["import-seed", str(ROOT / "seed")])
        self.assertIn("Jobs: skipped, table not empty", out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM jobs"), 16)

    def titles(self) -> list[str]:
        from app.db import connect
        conn = connect()
        try:
            return [r[0] for r in conn.execute("SELECT title FROM jobs ORDER BY id")]
        finally:
            conn.close()

    def test_import_seed_only_jobs_leaves_the_tasks_alone(self):
        out = self.run_cli(["import-seed", "--only", "jobs", str(ROOT / "seed")])
        self.assertEqual(out.strip(), "Jobs: 16")
        self.assertEqual(self.count("SELECT COUNT(*) FROM tasks"), 0)
        out = self.run_cli(["import-seed", "--only", "tasks", str(ROOT / "seed")])
        self.assertEqual(out.strip(), "Tasks: 40")

    def test_add_job_photo(self):
        self.run_cli(["import-seed", "--only", "jobs", str(ROOT / "seed")])
        photo = Path(self.tmp.name) / "stairs.jpg"
        photo.write_bytes(JPEG)
        out = self.run_cli(["add-job-photo", "--job", "4", "--file", str(photo), "--caption", "Reference"])
        self.assertIn("Added the photo to job 4.", out)
        from app.db import connect
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM job_photos").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["job_id"], row["caption"], row["bytes"], row["created_by"]),
                         (4, "Reference", len(JPEG), self.row("owen")["id"]))
        self.assertRegex(row["filename"], r"^[0-9a-f]{32}\.jpg$")  # a fresh random name, not stairs.jpg
        self.assertEqual((Path(os.environ["HRS_PHOTOS_DIR"]) / row["filename"]).read_bytes(), JPEG)

    def test_add_job_photo_refusals(self):
        self.run_cli(["import-seed", "--only", "jobs", str(ROOT / "seed")])
        gif = Path(self.tmp.name) / "plan.gif"
        gif.write_bytes(b"GIF89a")
        big = Path(self.tmp.name) / "big.jpg"
        big.write_bytes(b"\xff\xd8\xff" + b"\x00" * (8 * 1024 * 1024))
        good = Path(self.tmp.name) / "ok.jpg"
        good.write_bytes(JPEG)
        for argv, message in ((["--job", "4", "--file", str(gif)], "Only JPEG photos are accepted."),
                              (["--job", "4", "--file", str(big)], "That photo is too large."),
                              (["--job", "99", "--file", str(good)], "No job 99."),
                              (["--job", "4", "--file", str(good) + ".missing"], f"No file {good}.missing."),
                              (["--job", "4", "--file", str(good), "--caption", "x" * 301],
                               "A caption is at most 300 characters.")):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as caught:
                    self.run_cli(["add-job-photo", *argv])
                self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.count("SELECT COUNT(*) FROM job_photos"), 0)
        photos = Path(os.environ["HRS_PHOTOS_DIR"])
        self.assertEqual(list(photos.glob("*.jpg")) if photos.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
