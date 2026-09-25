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
        from app import cli
        from app.auth import throttle
        throttle._fails.clear()
        cli.main(["import-seed", str(ROOT / "seed")])
        cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "homeowner", "--password", "homeowner-pass"])
        cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "housekeeper", "--password", "housekeeper-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()

    def login(self, who: str):
        pw = {"owen": "homeowner-pass", "maria": "housekeeper-pass"}[who]
        r = self.c.post("/api/login", json={"username": who, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

    # ---- auth ----
    def test_requires_login(self):
        self.assertEqual(self.c.get("/api/state").status_code, 401)

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            self.assertEqual(self.c.post("/api/login", json={"username": "maria", "password": "nope"}, headers=H).status_code, 401)
        r = self.c.post("/api/login", json={"username": "maria", "password": "housekeeper-pass"}, headers=H)
        self.assertEqual(r.status_code, 429)

    def test_mutations_need_csrf_header(self):
        self.login("owen")
        r = self.c.post("/api/tasks", json={"title": "x"})
        self.assertEqual(r.status_code, 403)

    def test_change_own_password(self):
        self.login("maria")
        r = self.c.put("/api/me/password", json={"current": "wrong", "new": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 400)
        r = self.c.put("/api/me/password", json={"current": "housekeeper-pass", "new": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": "maria", "password": "a-new-password"}, headers=H)
        self.assertEqual(r.status_code, 200)

    # ---- roles ----
    def test_housekeeper_limits(self):
        self.login("maria")
        state = self.c.get("/api/state").json()
        self.assertEqual(state["me"]["role"], "housekeeper")
        self.assertEqual(len(state["tasks"]), 41)
        self.assertEqual(self.c.post("/api/tasks", json={"title": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.c.get("/api/users").status_code, 403)
        self.assertEqual(self.c.post("/api/visits/2026-09-29/extras", json={"title": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.c.post("/api/plans/2026-09-28/generate", json={}, headers=H).status_code, 403)
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertIn("sessions", plan)
        self.assertNotIn("shopping", plan)  # shopping list is homeowner-only

    def test_housekeeper_ticks_and_notes(self):
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

    # ---- homeowner ----
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
        r = self.c.post("/api/users", json={"username": "sam", "displayName": "Sam", "role": "housekeeper", "password": "short"}, headers=H)
        self.assertEqual(r.status_code, 400)
        u = self.c.post("/api/users", json={"username": "sam", "displayName": "Sam", "role": "housekeeper", "password": "long-enough-pw"}, headers=H).json()["user"]
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
