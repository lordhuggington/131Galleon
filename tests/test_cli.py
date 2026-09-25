"""Admin command tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "cli.db")
        from app import cli
        self.cli = cli
        cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner", "--password", "owner-pass"])

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv: list[str]) -> str:
        """Run an admin command with its output captured, so the suite prints only unittest's own lines."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
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
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
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
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--password", "staff-pass"])
        conn = connect()
        try:
            create_session(conn, self.row("maria")["id"])
        finally:
            conn.close()
        self.cli.main(["set-phone", "--username", "maria", "--phone", "310 555 1234"])
        r = self.row("maria")
        self.assertEqual(r["phone"], "+13105551234")
        self.assertIsNone(r["password_hash"])
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (r["id"],)).fetchone()[0], 0)
        finally:
            conn.close()
        with self.assertRaises(SystemExit):
            self.cli.main(["set-phone", "--username", "maria", "--phone", "nonsense"])
        with self.assertRaises(SystemExit):  # owen would collide with maria's number
            self.cli.main(["set-phone", "--username", "owen", "--phone", "+13105551234"])

    def test_set_password_works_for_owners_and_for_staff_with_no_phone(self):
        """The break-glass when text messages stop working (spec §6.5, README "Text-message sign-in")."""
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff"])
        self.cli.main(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        from app.auth import verify_password
        self.assertTrue(verify_password("long-enough-pw", self.row("maria")["password_hash"]))
        self.assertEqual(self.password_login("maria", "long-enough-pw"), 200)
        self.cli.main(["set-password", "--username", "owen", "--password", "another-long-pw"])
        self.assertTrue(verify_password("another-long-pw", self.row("owen")["password_hash"]))

    def test_set_password_is_refused_for_staff_who_have_a_phone(self):
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        with self.assertRaises(SystemExit) as caught:
            self.cli.main(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        self.assertEqual(str(caught.exception),
                         "'maria' signs in with a code texted to their phone. Clear their number with "
                         "`set-phone --clear` first if they need a password.")
        self.assertIsNone(self.row("maria")["password_hash"])

    def test_clearing_a_phone_signs_them_out_and_re_opens_the_password_path(self):
        from app.auth import create_session
        from app.db import connect
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        maria = self.row("maria")
        conn = connect()
        try:
            create_session(conn, maria["id"])
        finally:
            conn.close()
        self.cli.main(["set-phone", "--username", "maria", "--clear"])
        self.assertIsNone(self.row("maria")["phone"])
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                                          (maria["id"],)).fetchone()[0], 0)
        finally:
            conn.close()
        self.cli.main(["set-password", "--username", "maria", "--password", "long-enough-pw"])
        self.assertEqual(self.password_login("maria", "long-enough-pw"), 200)

    def test_clearing_a_phone_leaves_an_owners_password_alone(self):
        self.cli.main(["set-phone", "--username", "owen", "--phone", "310 555 1234"])
        self.cli.main(["set-phone", "--username", "owen", "--clear"])
        self.assertIsNone(self.row("owen")["phone"])
        self.assertEqual(self.password_login("owen", "owner-pass"), 200)

    def test_set_phone_needs_either_a_number_or_clear(self):
        with self.assertRaises(SystemExit) as caught:
            self.cli.main(["set-phone", "--username", "owen"])
        self.assertEqual(caught.exception.code, 2)  # argparse usage error

    def test_only_owners_are_prompted_for_a_password(self):
        with mock.patch("getpass.getpass", return_value="prompted-password") as asked:
            self.cli.main(["create-user", "--username", "kate", "--name", "Kate", "--role", "owner"])
            self.assertTrue(asked.called)
            asked.reset_mock()
            self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff"])
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
                    self.cli.main(["create-user", "--username", "dana", "--name", "Dana", "--role", "staff",
                                   "--door-code", bad])
                self.assertEqual(str(caught.exception), "A door code is 4 to 8 digits.")
                self.assertIsNone(self.row("dana"))

    def test_a_duplicate_phone_is_refused_without_echoing_the_number(self):
        self.cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                       "--phone", "310 555 1234"])
        with self.assertRaises(SystemExit) as caught:
            self.cli.main(["create-user", "--username", "dana", "--name", "Dana", "--role", "staff",
                           "--phone", "(310) 555-1234"])
        self.assertNotIn("3105551234", str(caught.exception))
        self.assertIsNone(self.row("dana"))


if __name__ == "__main__":
    unittest.main()
