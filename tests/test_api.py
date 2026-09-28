"""API tests. Run with:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
H = {"x-hrs": "1"}
JPEG = b"\xff\xd8\xff" + b"\x00" * 200  # only the magic bytes are checked server-side
PHOTO_HEADERS = {**H, "content-type": "image/jpeg"}
# Every variable these tests set. Saved in setUp and restored in tearDown so the suite doesn't depend
# on module order — the way tests/test_sms.py already does it.
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID")


def save_env() -> dict[str, str | None]:
    return {k: os.environ.get(k) for k in ENV_KEYS}


def restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run_cli(argv: list[str]) -> str:
    """Run an admin command with its output captured, so the suite prints only unittest's own lines."""
    from app import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(argv)
    return out.getvalue()


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = save_env()
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"  # the host starlette.testclient uses
        os.environ["TWILIO_ACCOUNT_SID"] = "AC123"
        os.environ["TWILIO_AUTH_TOKEN"] = "tok"
        os.environ["TWILIO_VERIFY_SERVICE_SID"] = "VA123"
        from app.auth import send_throttle, sms_check_throttle, throttle
        throttle._fails.clear()
        sms_check_throttle._fails.clear()
        send_throttle._fails.clear()
        run_cli(["import-seed", str(ROOT / "seed")])
        run_cli(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner", "--password", "owner-pass"])
        run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff", "--password", "staff-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        restore_env(self.saved_env)

    def login(self, who: str):
        pw = {"owen": "owner-pass", "maria": "staff-pass"}[who]
        r = self.c.post("/api/login", json={"username": who, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

    def sms_login(self, phone: str) -> str:
        """Open a session the way a staff member with a phone does. Returns their session cookie."""
        with mock.patch("app.sms.check_verification", return_value=True):
            r = self.c.post("/api/login/sms/check", json={"phone": phone, "code": "123456"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return self.c.cookies["hrs_session"]

    def set_phone(self, username: str, phone: str | None, keep_password: bool = False) -> None:
        """Put a phone number on a user directly, the way an owner or the CLI would. None clears it."""
        from app.db import connect
        conn = connect()
        try:
            conn.execute("UPDATE users SET phone = ? WHERE username = ?", (phone, username))
            if not keep_password:
                conn.execute("UPDATE users SET password_hash = NULL WHERE username = ?", (username,))
        finally:
            conn.close()

    # ---- auth ----
    def test_requires_login(self):
        self.assertEqual(self.c.get("/api/state").status_code, 401)

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            self.assertEqual(self.c.post("/api/login", json={"username": "maria", "password": "nope"}, headers=H).status_code, 401)
        r = self.c.post("/api/login", json={"username": "maria", "password": "staff-pass"}, headers=H)
        self.assertEqual(r.status_code, 429)

    def test_mutations_need_csrf_header(self):
        self.login("owen")
        r = self.c.post("/api/tasks", json={"title": "x"})
        self.assertEqual(r.status_code, 403)

    def test_change_own_password(self):
        self.login("owen")
        r = self.c.put("/api/me/password", json={"current": "wrong", "new": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 400)
        r = self.c.put("/api/me/password", json={"current": "owner-pass", "new": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": "owen", "password": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 200)

    def test_staff_cannot_change_password(self):
        self.login("maria")
        r = self.c.put("/api/me/password", json={"current": "staff-pass", "new": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Staff sign in by text message and don't have a password.")

    def test_me_includes_person_fields(self):
        me = self.login("maria")
        self.assertEqual(me["role"], "staff")
        self.assertEqual(me["label"], "")
        self.assertIsNone(me["phone"])
        self.assertIsNone(me["doorCode"])
        self.assertTrue(me["canSeeMeals"])
        self.assertEqual(self.c.get("/api/me").json()["me"], me)
        self.assertEqual(self.c.get("/api/state").json()["me"], me)

    # ---- sms sign-in ----
    def test_login_options_reports_sms(self):
        self.assertEqual(self.c.get("/api/login/options").json(), {"sms": True})
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": ""}):
            self.assertEqual(self.c.get("/api/login/options").json(), {"sms": False})

    def test_sms_start_unknown_phone_looks_identical(self):
        with mock.patch("app.sms.start_verification") as send:
            r = self.c.post("/api/login/sms/start", json={"phone": "(310) 555-9999"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        send.assert_not_called()

    def test_sms_start_texts_a_known_phone(self):
        self.set_phone("maria", "+13105551234")
        with mock.patch("app.sms.start_verification") as send:
            r = self.c.post("/api/login/sms/start", json={"phone": "310.555.1234"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        send.assert_called_once_with("+13105551234")
        r = self.c.post("/api/login/sms/start", json={"phone": "nope"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Enter a mobile number like (310) 555-1234.")

    def test_sms_start_send_throttle(self):
        self.set_phone("maria", "+13105551234")
        with mock.patch("app.sms.start_verification"):
            for _ in range(3):
                self.assertEqual(self.c.post("/api/login/sms/start", json={"phone": "+13105551234"}, headers=H).status_code, 200)
            r = self.c.post("/api/login/sms/start", json={"phone": "+13105551234"}, headers=H)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json(), {"error": "Too many codes sent to that number. Wait 10 minutes."})

    def test_sms_start_send_throttle_is_identical_for_an_unknown_number(self):
        """Throttle accounting must not distinguish a number on file from one that isn't."""
        with mock.patch("app.sms.start_verification") as send:
            for _ in range(3):
                self.assertEqual(self.c.post("/api/login/sms/start", json={"phone": "+13105559999"}, headers=H).status_code, 200)
            r = self.c.post("/api/login/sms/start", json={"phone": "+13105559999"}, headers=H)
            send.assert_not_called()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json(), {"error": "Too many codes sent to that number. Wait 10 minutes."})

    def test_sms_start_when_twilio_is_not_configured(self):
        with mock.patch.dict(os.environ, {"TWILIO_VERIFY_SERVICE_SID": ""}), mock.patch("app.sms.start_verification") as send:
            r = self.c.post("/api/login/sms/start", json={"phone": "+13105551234"}, headers=H)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"error": "Text-message sign-in isn't set up.", "smsUnavailable": True})
        send.assert_not_called()

    def test_sms_start_hides_twilio_errors(self):
        """A failed send must look like every other start: it only ever happens to a known number."""
        from app.sms import SmsError
        self.set_phone("maria", "+13105551234")
        with mock.patch("app.sms.start_verification", side_effect=SmsError("Couldn't send the text message. Try again in a minute.")) as send:
            r = self.c.post("/api/login/sms/start", json={"phone": "+13105551234"}, headers=H)
        send.assert_called_once_with("+13105551234")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})

    def test_sms_check_signs_in(self):
        self.set_phone("maria", "+13105551234")
        with mock.patch("app.sms.check_verification", return_value=True) as check:
            r = self.c.post("/api/login/sms/check", json={"phone": "(310) 555-1234", "code": "123456"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        check.assert_called_once_with("+13105551234", "123456")
        self.assertEqual(r.json()["me"]["username"], "maria")
        self.assertEqual(r.json()["me"]["phone"], "+13105551234")
        self.assertEqual(self.c.get("/api/me").status_code, 200)  # the cookie works

    def test_sms_check_wrong_code_then_lockout(self):
        self.set_phone("maria", "+13105551234")
        with mock.patch("app.sms.check_verification", return_value=False):
            for _ in range(5):
                r = self.c.post("/api/login/sms/check", json={"phone": "+13105551234", "code": "000000"}, headers=H)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.json()["error"], "That code isn't right or has expired.")
            r = self.c.post("/api/login/sms/check", json={"phone": "+13105551234", "code": "000000"}, headers=H)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"], "Too many failed attempts. Wait 15 minutes and try again.")

    def test_sms_check_unknown_phone_never_calls_twilio(self):
        with mock.patch("app.sms.check_verification") as check:
            r = self.c.post("/api/login/sms/check", json={"phone": "+13105559999", "code": "123456"}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "That code isn't right or has expired.")
        check.assert_not_called()
        r = self.c.post("/api/login/sms/check", json={"phone": "+13105559999", "code": "12"}, headers=H)
        self.assertEqual(r.status_code, 400)

    def test_password_lockout_cannot_block_sms_check(self):
        """Usernames have no charset limit, so the two lockouts must not share a key space."""
        self.set_phone("maria", "+13105551234")
        for _ in range(5):
            r = self.c.post("/api/login", json={"username": "+13105551234", "password": "nope"}, headers=H)
            self.assertEqual(r.status_code, 401)
        with mock.patch("app.sms.check_verification", return_value=True) as check:
            r = self.c.post("/api/login/sms/check", json={"phone": "+13105551234", "code": "123456"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        check.assert_called_once_with("+13105551234", "123456")

    def test_sms_check_refused_when_unconfigured(self):
        """With Twilio unset, check_verification would turn Twilio's 404 into a bogus 'wrong code'."""
        self.set_phone("maria", "+13105551234")
        env = {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_VERIFY_SERVICE_SID": ""}
        with mock.patch.dict(os.environ, env), mock.patch("app.sms.check_verification") as check:
            r = self.c.post("/api/login/sms/check", json={"phone": "+13105551234", "code": "123456"}, headers=H)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"error": "Text-message sign-in isn't set up.", "smsUnavailable": True})
        check.assert_not_called()

    def test_password_login_rules(self):
        # staff with a phone can't use their old password any more
        self.set_phone("maria", "+13105551234", keep_password=True)
        r = self.c.post("/api/login", json={"username": "maria", "password": "staff-pass"}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "Wrong username or password.")
        # a staff member with no phone yet still can (the upgrade must not lock anyone out)
        self.set_phone("maria", None, keep_password=True)
        self.assertEqual(self.c.post("/api/login", json={"username": "maria", "password": "staff-pass"}, headers=H).status_code, 200)
        self.c.post("/api/logout", headers=H)
        # owners always can, phone or not
        self.set_phone("owen", "+13105550000", keep_password=True)
        self.assertEqual(self.c.post("/api/login", json={"username": "owen", "password": "owner-pass"}, headers=H).status_code, 200)
        # a staff member with no password at all can't
        self.c.post("/api/logout", headers=H)
        self.set_phone("maria", "+13105551234")
        self.assertEqual(self.c.post("/api/login", json={"username": "maria", "password": "staff-pass"}, headers=H).status_code, 401)

    # ---- roles ----
    def test_staff_limits(self):
        self.login("maria")
        state = self.c.get("/api/state").json()
        self.assertEqual(state["me"]["role"], "staff")
        self.assertEqual(len(state["tasks"]), 41)
        self.assertEqual(self.c.post("/api/tasks", json={"title": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.c.get("/api/users").status_code, 403)
        self.assertEqual(self.c.post("/api/visits/2026-09-29/extras", json={"title": "x"}, headers=H).status_code, 403)
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertIn("sessions", plan)
        self.assertNotIn("shopping", plan)  # shopping list is owner-only

    def test_menu_generation_is_gone(self):
        """No route left, so the static mount at '/' answers: 405 for a POST it won't serve, 404 for the GET."""
        self.login("owen")
        self.assertEqual(self.c.post("/api/plans/2026-09-28/generate", json={}, headers=H).status_code, 405)
        self.assertEqual(self.c.get("/api/jobs/1").status_code, 404)

    def test_staff_ticks_and_notes(self):
        self.login("maria")
        r = self.c.put("/api/visits/2026-09-29/tasks/t01", json={"done": True}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.c.put("/api/visits/2026-09-29/note", json={"note": "Out of dish soap"}, headers=H)
        v = self.c.get("/api/state").json()["visits"]["2026-09-29"]
        self.assertIn("t01", v["done"])
        self.assertEqual(v["note"], "Out of dish soap")
        self.c.put("/api/visits/2026-09-29/tasks/t01", json={"done": False}, headers=H)
        v = self.c.get("/api/state").json()["visits"]["2026-09-29"]
        self.assertNotIn("t01", v["done"])

    def test_extras_flow(self):
        self.login("owen")
        eid = self.c.post("/api/visits/2026-10-02/extras", json={"title": "Sort hall closet"}, headers=H).json()["id"]
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        self.assertEqual(self.c.patch(f"/api/visits/2026-10-02/extras/{eid}", json={"done": True}, headers=H).status_code, 200)
        extras = self.c.get("/api/state").json()["visits"]["2026-10-02"]["extras"]
        self.assertTrue(extras[eid]["done"])

    # ---- owner ----
    def test_task_crud(self):
        self.login("owen")
        t = self.c.post("/api/tasks", json={"title": "Clean garage fridge", "area": "Garage", "freq": "monthly", "day": "fri"}, headers=H).json()["task"]
        self.assertEqual(t["freq"], "monthly")
        r = self.c.patch(f"/api/tasks/{t['id']}", json={"freq": "yearly"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.c.patch(f"/api/tasks/{t['id']}", json={"notes": "Top shelf first"}, headers=H)
        self.c.delete(f"/api/tasks/{t['id']}", headers=H)
        ids = [x["id"] for x in self.c.get("/api/state").json()["tasks"]]
        self.assertNotIn(t["id"], ids)

    def test_settings_validation(self):
        self.login("owen")
        s = self.c.get("/api/state").json()["settings"]
        s["kcal"] = 99999
        self.assertEqual(self.c.put("/api/settings", json=s, headers=H).status_code, 400)
        s["kcal"] = 550
        s["dislikes"] = "mushrooms"
        r = self.c.put("/api/settings", json=s, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["settings"]["dislikes"], "mushrooms")

    def test_shopping_and_fav(self):
        self.login("owen")
        self.assertEqual(self.c.patch("/api/plans/2026-09-28/shopping/s01", json={"got": True}, headers=H).status_code, 200)
        self.assertEqual(self.c.patch("/api/plans/2026-09-28/recipes/tue/main", json={"fav": True}, headers=H).status_code, 200)
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertTrue(plan["got"]["s01"])
        self.assertTrue(plan["sessions"]["tue"]["recipes"]["main"]["fav"])
        self.assertEqual(self.c.get("/api/plans/2026-09-29").status_code, 400)  # not a Monday

    def test_shopping_search_phrase_survives_a_round_trip(self):
        from app import store
        from app.db import connect, tx
        plan = {"sessions": {}, "leftovers": [], "shopping": [
            {"id": "s01", "item": "Fage Total 0% Greek Yogurt, 32 oz", "buy": "2", "aisle": "Dairy & eggs",
             "for": "both", "stock": False, "search": "Fage Total 0% Greek Yogurt 32 oz"},
            {"id": "s02", "item": "Yellow onions", "buy": "3", "aisle": "Produce", "for": "tue", "stock": False}]}
        conn = connect()
        try:
            with tx(conn):
                store.save_plan(conn, "2026-10-12", plan, source="test", note="", user_id=None)
            saved = store.get_plan(conn, "2026-10-12", include_shopping=True)
        finally:
            conn.close()
        # the second item came without a phrase, the way every imported plan does
        self.assertEqual([i["search"] for i in saved["shopping"]], ["Fage Total 0% Greek Yogurt 32 oz", ""])

    def test_user_admin(self):
        me = self.login("owen")
        r = self.c.post("/api/users", json={"username": "sam", "displayName": "Sam", "role": "staff", "password": "short"}, headers=H)
        self.assertEqual(r.status_code, 400)
        u = self.c.post("/api/users", json={"username": "sam", "displayName": "Sam", "role": "staff", "password": "long-enough-pw"}, headers=H).json()["user"]
        self.assertEqual(self.c.patch(f"/api/users/{me['id']}", json={"active": False}, headers=H).status_code, 400)
        self.assertEqual(self.c.patch(f"/api/users/{u['id']}", json={"active": False}, headers=H).status_code, 200)
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": "sam", "password": "long-enough-pw"}, headers=H)
        self.assertEqual(r.status_code, 401)

    # ---- people ----
    def test_create_person_fields_and_derived_username(self):
        self.login("owen")
        body = {"displayName": "Maria", "role": "staff", "label": "Builder", "phone": "(310) 555-7777",
                "doorCode": "4821", "canSeeMeals": False}
        u = self.c.post("/api/users", json=body, headers=H).json()["user"]
        self.assertEqual(u["username"], "maria-2")  # 'maria' is taken by setUp
        self.assertEqual(u["label"], "Builder")
        self.assertEqual(u["phone"], "+13105557777")
        self.assertEqual(u["doorCode"], "4821")
        self.assertFalse(u["canSeeMeals"])
        self.assertFalse(u["hasPassword"])  # staff need no password
        self.assertTrue(u["active"])
        again = self.c.post("/api/users", json={"displayName": "Maria"}, headers=H).json()["user"]
        self.assertEqual(again["username"], "maria-3")
        self.assertEqual(again["role"], "staff")
        # owners must have a password
        r = self.c.post("/api/users", json={"displayName": "Sam", "role": "owner"}, headers=H)
        self.assertEqual(r.status_code, 400)
        o = self.c.post("/api/users", json={"displayName": "Sam", "role": "owner", "password": "long-enough-pw"},
                        headers=H).json()["user"]
        self.assertEqual(o["username"], "sam")
        self.assertTrue(o["hasPassword"])
        self.assertTrue(o["canSeeMeals"])

    def test_a_password_for_new_staff_with_a_phone_is_refused_not_dropped(self):
        """login() never accepts a password from staff who have a phone, so no hash may be stored —
        and a client that sent one is told, rather than having the field silently thrown away."""
        self.login("owen")
        r = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105558888",
                                            "password": "long-enough-pw"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "They sign in by text, so they don't need a password.")
        u = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105558888"},
                        headers=H).json()["user"]
        self.assertFalse(u["hasPassword"])
        row = [x for x in self.c.get("/api/users").json()["users"] if x["id"] == u["id"]][0]
        self.assertFalse(row["hasPassword"])
        # staff with no phone yet may still be given one, so the v2 upgrade locks nobody out
        v = self.c.post("/api/users", json={"displayName": "Alex", "password": "long-enough-pw"}, headers=H).json()["user"]
        self.assertTrue(v["hasPassword"])

    def test_duplicate_phone_is_a_conflict(self):
        self.login("owen")
        self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105558888"}, headers=H)
        r = self.c.post("/api/users", json={"displayName": "Alex", "phone": "310 555 8888"}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "That phone number is already used by Pat.")
        uid = self.c.post("/api/users", json={"displayName": "Alex"}, headers=H).json()["user"]["id"]
        r = self.c.patch(f"/api/users/{uid}", json={"phone": "+13105558888"}, headers=H)
        self.assertEqual(r.status_code, 409)
        # setting the same number back on the same person is fine
        self.assertEqual(self.c.patch(f"/api/users/{uid}", json={"phone": "+13105550001"}, headers=H).status_code, 200)
        self.assertEqual(self.c.patch(f"/api/users/{uid}", json={"phone": "+13105550001"}, headers=H).status_code, 200)

    def test_a_phone_collides_with_an_inactive_holder_too(self):
        """The UNIQUE index spans inactive rows, so the check has to as well."""
        self.login("owen")
        pat = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105558888"}, headers=H).json()["user"]
        self.assertEqual(self.c.patch(f"/api/users/{pat['id']}", json={"active": False}, headers=H).status_code, 200)
        r = self.c.post("/api/users", json={"displayName": "Alex", "phone": "(310) 555-8888"}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "That phone number is already used by Pat.")

    def test_blank_phones_do_not_collide(self):
        """A missing phone must be SQL NULL, not '' — the UNIQUE index would reject the second ''."""
        self.login("owen")
        for name in ("Pat", "Alex"):
            r = self.c.post("/api/users", json={"displayName": name, "phone": "  "}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(r.json()["user"]["phone"])
        uid = [u for u in self.c.get("/api/users").json()["users"] if u["displayName"] == "Pat"][0]["id"]
        r = self.c.patch(f"/api/users/{uid}", json={"phone": ""}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["user"]["phone"])

    def test_door_code_validation(self):
        me = self.login("owen")
        self.assertEqual(self.c.patch(f"/api/users/{me['id']}", json={"doorCode": "12"}, headers=H).status_code, 400)
        r = self.c.patch(f"/api/users/{me['id']}", json={"doorCode": "12x4"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "A door code is 4 to 8 digits.")
        u = self.c.patch(f"/api/users/{me['id']}", json={"doorCode": "4821"}, headers=H).json()["user"]
        self.assertEqual(u["doorCode"], "4821")
        self.assertEqual(self.c.get("/api/me").json()["me"]["doorCode"], "4821")
        u = self.c.patch(f"/api/users/{me['id']}", json={"doorCode": ""}, headers=H).json()["user"]
        self.assertIsNone(u["doorCode"])

    def test_door_codes_are_ascii_digits(self):
        """\\d would accept Arabic-Indic digits, which no keypad can dial."""
        me = self.login("owen")
        r = self.c.patch(f"/api/users/{me['id']}", json={"doorCode": "٤٨٢١"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "A door code is 4 to 8 digits.")

    def test_explicit_usernames_are_checked(self):
        self.login("owen")
        r = self.c.post("/api/users", json={"displayName": "Pat", "username": "pat space"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Usernames use letters, numbers, dots, dashes or underscores.")
        u = self.c.post("/api/users", json={"displayName": "Pat", "username": "Pat"}, headers=H).json()["user"]
        self.assertEqual(u["username"], "pat")
        r = self.c.post("/api/users", json={"displayName": "Patricia", "username": "pat"}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "That username is taken.")

    def test_setting_a_staff_phone_clears_their_password_and_sessions(self):
        me = self.login("maria")
        self.c.post("/api/logout", headers=H)
        self.login("owen")
        u = self.c.patch(f"/api/users/{me['id']}", json={"phone": "(310) 555-1234"}, headers=H).json()["user"]
        self.assertEqual(u["phone"], "+13105551234")
        self.assertFalse(u["hasPassword"])
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": "maria", "password": "staff-pass"}, headers=H)
        self.assertEqual(r.status_code, 401)

    def test_an_owner_keeps_their_password_when_given_a_phone(self):
        me = self.login("owen")
        u = self.c.patch(f"/api/users/{me['id']}", json={"phone": "+13105550000"}, headers=H).json()["user"]
        self.assertEqual(u["phone"], "+13105550000")
        self.assertTrue(u["hasPassword"])
        self.assertEqual(self.c.get("/api/me").status_code, 200)  # not signed out either
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": "owen", "password": "owner-pass"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_demoting_a_phone_holding_owner_drops_their_password(self):
        """The row ends up staff-with-phone, so the hash it kept as an owner has to go."""
        self.login("owen")
        sam = self.c.post("/api/users", json={"displayName": "Sam", "role": "owner", "phone": "+13105556666",
                                              "password": "long-enough-pw"}, headers=H).json()["user"]
        self.assertTrue(sam["hasPassword"])
        self.c.post("/api/logout", headers=H)
        self.assertEqual(self.c.post("/api/login", json={"username": "sam", "password": "long-enough-pw"},
                                     headers=H).status_code, 200)
        sam_cookie = self.c.cookies["hrs_session"]
        self.login("owen")  # signs owen in again; Sam's session row is still live
        u = self.c.patch(f"/api/users/{sam['id']}", json={"role": "staff"}, headers=H).json()["user"]
        self.assertEqual(u["role"], "staff")
        self.assertFalse(u["hasPassword"])
        row = [x for x in self.c.get("/api/users").json()["users"] if x["id"] == sam["id"]][0]
        self.assertFalse(row["hasPassword"])
        self.c.cookies.set("hrs_session", sam_cookie)
        self.assertEqual(self.c.get("/api/me").status_code, 401)  # signed out everywhere

    def test_resaving_a_staff_members_unchanged_phone_keeps_them_signed_in(self):
        """Spec §5.5's editor submits every field on Save; only a real change may sign anyone out."""
        self.login("owen")
        pat = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+1 (310) 555-1234"},
                          headers=H).json()["user"]
        pat_cookie = self.sms_login("(310) 555-1234")
        self.login("owen")  # Pat's session row is still live
        body = {"displayName": "Pat", "label": "Housekeeper", "phone": "+1 (310) 555-1234",
                "doorCode": "4821", "canSeeMeals": True}
        u = self.c.patch(f"/api/users/{pat['id']}", json=body, headers=H).json()["user"]
        self.assertEqual(u["label"], "Housekeeper")
        self.assertEqual(u["doorCode"], "4821")
        self.assertEqual(u["phone"], "+13105551234")
        self.assertEqual(u["hasPassword"], pat["hasPassword"])
        self.c.cookies.set("hrs_session", pat_cookie)
        self.assertEqual(self.c.get("/api/me").status_code, 200)  # still signed in on their phone

    def test_a_no_op_role_patch_keeps_a_staff_member_signed_in(self):
        self.login("owen")
        pat = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105551234"},
                          headers=H).json()["user"]
        pat_cookie = self.sms_login("+13105551234")
        self.login("owen")
        u = self.c.patch(f"/api/users/{pat['id']}", json={"role": "staff"}, headers=H).json()["user"]
        self.assertEqual(u["role"], "staff")
        self.c.cookies.set("hrs_session", pat_cookie)
        self.assertEqual(self.c.get("/api/me").status_code, 200)

    def test_promoting_a_password_less_staff_member_needs_a_password(self):
        """An owner with no hash and no phone couldn't sign in at all, so the promotion is refused."""
        self.login("owen")
        pat = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105551234"},
                          headers=H).json()["user"]
        self.assertFalse(pat["hasPassword"])
        r = self.c.patch(f"/api/users/{pat['id']}", json={"label": "Builder", "role": "owner"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Give them a password when you make them an owner.")
        row = [x for x in self.c.get("/api/users").json()["users"] if x["id"] == pat["id"]][0]
        self.assertEqual(row["role"], "staff")
        self.assertEqual(row["label"], "")  # the tx rolled the whole PATCH back

    def test_promoting_with_a_password_in_the_same_patch_works(self):
        self.login("owen")
        pat = self.c.post("/api/users", json={"displayName": "Pat", "phone": "+13105551234"},
                          headers=H).json()["user"]
        u = self.c.patch(f"/api/users/{pat['id']}", json={"role": "owner", "password": "long-enough-pw"},
                         headers=H).json()["user"]
        self.assertEqual(u["role"], "owner")
        self.assertTrue(u["hasPassword"])
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": u["username"], "password": "long-enough-pw"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_re_promoting_someone_who_kept_their_password_needs_nothing_extra(self):
        self.login("owen")
        sam = self.c.post("/api/users", json={"displayName": "Sam", "role": "owner",
                                              "password": "long-enough-pw"}, headers=H).json()["user"]
        u = self.c.patch(f"/api/users/{sam['id']}", json={"role": "staff"}, headers=H).json()["user"]
        self.assertTrue(u["hasPassword"])  # no phone, so the hash stays usable
        u = self.c.patch(f"/api/users/{sam['id']}", json={"role": "owner"}, headers=H).json()["user"]
        self.assertEqual(u["role"], "owner")
        self.assertTrue(u["hasPassword"])

    def test_you_cannot_remove_your_own_owner_access(self):
        me = self.login("owen")
        for body in ({"active": False}, {"role": "staff"}):
            r = self.c.patch(f"/api/users/{me['id']}", json=body, headers=H)
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(r.json()["error"], "You can't remove your own owner access.")
        self.assertEqual(self.c.get("/api/me").json()["me"]["role"], "owner")

    def test_an_empty_patch_changes_nothing(self):
        self.login("owen")
        maria = [u for u in self.c.get("/api/users").json()["users"] if u["username"] == "maria"][0]
        r = self.c.patch(f"/api/users/{maria['id']}", json={}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"], maria)

    def test_staff_cannot_manage_people(self):
        me = self.login("maria")
        r = self.c.get("/api/users")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Only an owner can do that.")
        self.assertEqual(self.c.post("/api/users", json={"displayName": "X"}, headers=H).status_code, 403)
        self.assertEqual(self.c.patch(f"/api/users/{me['id']}", json={"label": "X"}, headers=H).status_code, 403)

    def test_staff_see_only_their_own_door_code(self):
        me = self.login("owen")
        self.c.patch(f"/api/users/{me['id']}", json={"doorCode": "99887766"}, headers=H)
        maria = [u for u in self.c.get("/api/users").json()["users"] if u["username"] == "maria"][0]
        self.assertEqual(self.c.patch(f"/api/users/{maria['id']}", json={"doorCode": "4821"}, headers=H).status_code, 200)
        r = self.c.patch(f"/api/users/{maria['id']}", json={"password": "long-enough-pw"}, headers=H)
        self.assertEqual(r.status_code, 400)  # staff have no password to reset
        self.assertEqual(r.json()["error"], "Staff sign in by text message and don't have a password.")
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        state = self.c.get("/api/state")
        self.assertEqual(state.json()["me"]["doorCode"], "4821")
        self.assertNotIn("99887766", state.text)  # never anyone else's code

    def test_owner_sees_meals_whatever_the_column_says(self):
        """me_dict forces canSeeMeals on for owners; user_dict reports the stored column as it is."""
        me = self.login("owen")
        u = self.c.patch(f"/api/users/{me['id']}", json={"canSeeMeals": False}, headers=H).json()["user"]
        self.assertFalse(u["canSeeMeals"])  # the column really was turned off
        self.assertTrue(self.c.get("/api/me").json()["me"]["canSeeMeals"])
        self.assertTrue(self.c.get("/api/state").json()["me"]["canSeeMeals"])
        row = [x for x in self.c.get("/api/users").json()["users"] if x["id"] == me["id"]][0]
        self.assertFalse(row["canSeeMeals"])

    def test_meals_can_be_turned_off_for_a_staff_member(self):
        self.login("owen")
        maria = [u for u in self.c.get("/api/users").json()["users"] if u["username"] == "maria"][0]
        self.c.patch(f"/api/users/{maria['id']}", json={"canSeeMeals": False}, headers=H)
        self.assertEqual(self.c.get("/api/plans/2026-09-28").status_code, 200)  # owners are never gated
        self.c.post("/api/logout", headers=H)
        mine = self.login("maria")
        self.assertFalse(mine["canSeeMeals"])
        r = self.c.get("/api/plans/2026-09-28")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Meals aren't turned on for you.")

    def test_state_hides_the_meal_settings_from_a_gated_staff_member(self):
        """/api/state must not leak what /api/plans refuses: gated people get the defaults, same shape —
        except the prep-coverage days, which the Visit screen shows everyone."""
        from app import store
        self.login("owen")
        s = self.c.get("/api/state").json()["settings"]
        s["likes"] = "kimchi-pancakes"
        s["tue"]["covers"] = "Thu, Fri"
        s["fri"]["covers"] = "Sat, Sun, Mon"
        self.assertEqual(self.c.put("/api/settings", json=s, headers=H).status_code, 200)
        self.assertEqual(self.c.get("/api/state").json()["settings"]["likes"], "kimchi-pancakes")
        maria = [u for u in self.c.get("/api/users").json()["users"] if u["username"] == "maria"][0]
        self.c.patch(f"/api/users/{maria['id']}", json={"canSeeMeals": False}, headers=H)
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        state = self.c.get("/api/state")
        gated = state.json()["settings"]
        self.assertEqual(gated["tue"]["covers"], "Thu, Fri")  # the real days, not the hard-coded ones
        self.assertEqual(gated["fri"]["covers"], "Sat, Sun, Mon")
        expected = store.default_settings()
        expected["tue"]["covers"], expected["fri"]["covers"] = "Thu, Fri", "Sat, Sun, Mon"
        self.assertEqual(gated, expected)  # and nothing else of the household's
        self.assertNotIn("kimchi-pancakes", state.text)
        self.c.post("/api/logout", headers=H)
        self.login("owen")
        self.c.patch(f"/api/users/{maria['id']}", json={"canSeeMeals": True}, headers=H)
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        self.assertEqual(self.c.get("/api/state").json()["settings"]["likes"], "kimchi-pancakes")

    # ---- photos ----
    def test_upload_photo_appears_in_state(self):
        self.login("maria")
        r = self.c.post("/api/visits/2026-09-29/photos?kind=fix&caption=Tap%20drips", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        photo = r.json()["photo"]
        self.assertEqual(photo["kind"], "fix")
        self.assertEqual(photo["caption"], "Tap drips")
        self.assertEqual(photo["by"]["displayName"], "Maria")
        self.assertRegex(photo["url"], r"^/photos/[0-9a-f]{32}\.jpg$")
        stored = Path(os.environ["HRS_PHOTOS_DIR"]) / photo["url"].split("/")[-1]
        self.assertEqual(stored.read_bytes(), JPEG)
        visit = self.c.get("/api/state").json()["visits"]["2026-09-29"]
        self.assertEqual(len(visit["photos"]), 1)
        self.assertEqual(visit["photos"][0], photo)  # list_visits and _photo_dict agree on the shape
        got = self.c.get(photo["url"])
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, JPEG)
        self.assertEqual(got.headers["cache-control"], "private, max-age=604800, immutable")

    def test_upload_with_no_query_string_at_all(self):
        self.login("maria")
        r = self.c.post("/api/visits/2026-09-29/photos", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["photo"]["kind"], "done")  # the default
        self.assertEqual(r.json()["photo"]["caption"], "")

    def test_photo_upload_validation(self):
        self.login("maria")
        r = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=b"GIF89a-not-a-jpeg", headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 415)
        self.assertEqual(r.json()["error"], "Only JPEG photos are accepted.")
        big = b"\xff\xd8\xff" + b"\x00" * (8 * 1024 * 1024)
        r = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=big, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["error"], "That photo is too large.")
        r = self.c.post("/api/visits/2026-09-29/photos?kind=sideways", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/visits/2026-09-29/photos?kind=done&caption=" + "x" * 301, content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=JPEG, headers={"content-type": "image/jpeg"})
        self.assertEqual(r.status_code, 403)  # no X-HRS header

    def oversized_chunks(self):
        """PHOTO_MAX_BYTES + 1 bytes in 1 MB pieces. httpx sends a generator chunked, with no length."""
        from app.api import PHOTO_MAX_BYTES
        block = b"\xff\xd8\xff" + b"\x00" * (1024 * 1024 - 3)
        remaining = PHOTO_MAX_BYTES + 1
        while remaining > 0:
            chunk = block[:remaining]
            remaining -= len(chunk)
            yield chunk

    def test_an_oversized_chunked_upload_is_refused(self):
        """No Content-Length to check, so the cap has to come from the stream as it arrives."""
        self.login("maria")
        r = self.c.post("/api/visits/2026-09-29/photos", content=self.oversized_chunks(), headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["error"], "That photo is too large.")
        self.assertNotIn("content-length", {k.lower() for k in r.request.headers})

    def test_an_anonymous_oversized_upload_is_refused_without_a_session(self):
        """The size gate runs before the session lookup, so a stranger can't make a worker buffer 20 MB."""
        r = self.c.post("/api/visits/2026-09-29/photos", content=self.oversized_chunks(), headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["error"], "That photo is too large.")

    def test_photos_require_a_session(self):
        self.assertEqual(self.c.get("/photos/" + "a" * 32 + ".jpg").status_code, 401)
        r = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 401)
        self.login("maria")
        self.assertEqual(self.c.get("/photos/not-a-real-name.jpg").status_code, 404)
        self.assertEqual(self.c.get("/photos/" + "a" * 32 + ".jpg").status_code, 404)

    def test_photo_names_that_try_to_escape_the_photos_directory(self):
        """Three gates stand in front of the filesystem: the route convertor, PHOTO_NAME_RE, then the row.

        httpx resolves '../' itself, so that one leaves as GET /house.db and never reaches the route; the
        percent-encoded form does arrive at the app. Either way nothing outside the photos folder is served.
        """
        self.login("maria")
        secret = "sqlite-bytes-nobody-should-see"
        (Path(self.tmp.name) / "house.db").write_text(secret)
        for url in ("/photos/../house.db", "/photos/%2e%2e%2fhouse.db",
                    "/photos/" + "a" * 32 + ".JPG", "/photos/" + "a" * 31 + ".jpg"):
            with self.subTest(url=url):
                r = self.c.get(url)
                self.assertEqual(r.status_code, 404)
                self.assertNotIn(secret, r.text)
                self.assertNotEqual(r.headers.get("content-type"), "image/jpeg")

    def test_a_row_without_its_file_is_a_404(self):
        self.login("maria")
        photo = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        (Path(os.environ["HRS_PHOTOS_DIR"]) / photo["url"].split("/")[-1]).unlink()
        r = self.c.get(photo["url"])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "That photo doesn't exist.")

    def test_photo_ids_and_dates_are_checked(self):
        self.login("maria")
        self.assertEqual(self.c.delete("/api/visits/2026-09-29/photos/not-a-number", headers=H).status_code, 404)
        r = self.c.post("/api/visits/2026-13-01/photos?kind=done", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_only_the_uploader_or_an_owner_deletes_a_photo(self):
        self.login("owen")
        mine = self.c.post("/api/visits/2026-09-29/photos?kind=done", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        r = self.c.delete(f"/api/visits/2026-09-29/photos/{mine['id']}", headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "You can only delete your own photos.")
        theirs = self.c.post("/api/visits/2026-09-29/photos?kind=fix", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.assertEqual(self.c.delete(f"/api/visits/2026-09-29/photos/{theirs['id']}", headers=H).status_code, 200)
        self.c.post("/api/logout", headers=H)
        self.login("owen")
        # an owner can delete anyone's, and the file goes with the row
        path = Path(os.environ["HRS_PHOTOS_DIR"]) / mine["url"].split("/")[-1]
        self.assertTrue(path.is_file())
        self.assertEqual(self.c.delete(f"/api/visits/2026-09-29/photos/{mine['id']}", headers=H).status_code, 200)
        self.assertFalse(path.is_file())
        self.assertEqual(self.c.delete(f"/api/visits/2026-09-29/photos/{mine['id']}", headers=H).status_code, 404)
        self.assertEqual(self.c.get("/api/state").json()["visits"].get("2026-09-29", {}).get("photos", []), [])


if __name__ == "__main__":
    unittest.main()
