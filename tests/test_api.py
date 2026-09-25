"""API tests. Run with:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
H = {"x-hrs": "1"}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["TWILIO_ACCOUNT_SID"] = "AC123"
        os.environ["TWILIO_AUTH_TOKEN"] = "tok"
        os.environ["TWILIO_VERIFY_SERVICE_SID"] = "VA123"
        from app import cli
        from app.auth import send_throttle, sms_check_throttle, throttle
        throttle._fails.clear()
        sms_check_throttle._fails.clear()
        send_throttle._fails.clear()
        cli.main(["import-seed", str(ROOT / "seed")])
        cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner", "--password", "owner-pass"])
        cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff", "--password", "staff-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID"):
            os.environ.pop(k, None)

    def login(self, who: str):
        pw = {"owen": "owner-pass", "maria": "staff-pass"}[who]
        r = self.c.post("/api/login", json={"username": who, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

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
        self.assertEqual(self.c.post("/api/plans/2026-09-28/generate", json={}, headers=H).status_code, 403)
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertIn("sessions", plan)
        self.assertNotIn("shopping", plan)  # shopping list is owner-only

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

    # ---- AI generation (Claude API mocked) ----
    def test_generate_menu(self):
        self.login("owen")
        seed = json.loads((ROOT / "seed" / "plans" / "2026-09-28.json").read_text())
        reply = json.dumps({"sessions": seed["sessions"], "shopping": seed["shopping"], "leftovers": seed["leftovers"]})

        def fake_call(prompt, on_text):
            assert "Do not repeat these recent recipes" in prompt
            on_text(reply[:100])
            return "```json\n" + reply + "\n```"

        with mock.patch("app.ai.call_model", side_effect=fake_call):
            r = self.c.post("/api/plans/2026-10-05/generate", json={"note": "Asian flavours"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            job = r.json()["jobId"]
            for _ in range(50):
                j = self.c.get(f"/api/jobs/{job}").json()
                if j["status"] != "running":
                    break
                time.sleep(0.1)
        self.assertEqual(j["status"], "done", j)
        plan = self.c.get("/api/plans/2026-10-05").json()["plan"]
        self.assertEqual(plan["sessions"]["tue"]["date"], "2026-10-06")
        self.assertEqual(plan["note"], "Asian flavours")
        self.assertGreater(len(plan["shopping"]), 10)

    def test_generate_bad_reply(self):
        self.login("owen")
        with mock.patch("app.ai.call_model", return_value="Sorry, no JSON here"):
            job = self.c.post("/api/plans/2026-10-05/generate", json={}, headers=H).json()["jobId"]
            for _ in range(50):
                j = self.c.get(f"/api/jobs/{job}").json()
                if j["status"] != "running":
                    break
                time.sleep(0.1)
        self.assertEqual(j["status"], "error")
        self.assertIn("incomplete", j["error"])


if __name__ == "__main__":
    unittest.main()


class StreamParsingTest(unittest.TestCase):
    """call_model against a fake Claude API that streams server-sent events."""

    def setUp(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"

    def _transport(self, events, status=200):
        import httpx
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)

        def handler(request):
            assert request.headers["x-api-key"] == "test-key"
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(status, text=body if status == 200 else '{"error":{"type":"rate_limit_error"}}')
        return httpx.MockTransport(handler)

    def test_collects_text(self):
        from app.ai import call_model
        ev = [{"type": "message_start"},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '{"a":'}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " 1}"}},
              {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
              {"type": "message_stop"}]
        seen = []
        out = call_model("hi", lambda t: seen.append(t) or True, transport=self._transport(ev))
        self.assertEqual(out, '{"a": 1}')
        self.assertEqual(seen[-1], '{"a": 1}')

    def test_truncated_reply_is_an_error(self):
        from app.ai import GenerationError, call_model
        ev = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "{"}},
              {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}]
        with self.assertRaises(GenerationError):
            call_model("hi", lambda t: True, transport=self._transport(ev))

    def test_rate_limit_message(self):
        from app.ai import GenerationError, call_model
        with self.assertRaises(GenerationError) as cm:
            call_model("hi", lambda t: True, transport=self._transport([], status=429))
        self.assertIn("rate-limiting", str(cm.exception))
