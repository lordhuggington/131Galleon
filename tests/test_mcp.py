"""The MCP endpoint. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = {"x-hrs": "1"}
CB = "https://claude.ai/api/mcp/auth_callback"
JSON_CT = {"content-type": "application/json"}
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL")


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
        from app.auth import throttle
        throttle._fails.clear()
        from app import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["import-seed", str(ROOT / "seed")])
            cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner",
                      "--password", "owner-pass"])
            cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                      "--password", "staff-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()
        self.token = self.connect()["access_token"]

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def connect(self) -> dict:
        """The whole OAuth dance, the way Claude does it. Returns the token response."""
        r = self.c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CB]})
        self.assertEqual(r.status_code, 201, r.text)
        client_id = r.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = self.c.post("/api/login", json={"username": "owen", "password": "owner-pass"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        p = {"response_type": "code", "client_id": client_id, "redirect_uri": CB,
             "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "scope": "menus"}
        r = self.c.post("/oauth/authorize", data={**p, "decision": "allow"}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["code"][0]
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def insert_token(self, username: str, token: str, expires_in: int = 3600) -> None:
        """An access token straight into the table — for cases the consent page will not produce."""
        from app.auth import token_hash
        from app.db import connect, now_iso
        conn = connect()
        try:
            conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) "
                         "VALUES ('direct', 'Claude', '[]', ?) ON CONFLICT(client_id) DO NOTHING", (now_iso(),))
            conn.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, scope, "
                         "expires_at, created_at) VALUES (?, 'access', 'direct', "
                         "(SELECT id FROM users WHERE username = ?), ?, 'menus', ?, ?)",
                         (token_hash(token), username, f"fam-{token}", int(time.time()) + expires_in, now_iso()))
        finally:
            conn.close()

    def rpc(self, body, token: str | None = "", **kwargs):
        """POST one JSON-RPC message. token="" means this test's own owner token; None means no header."""
        headers = dict(JSON_CT)
        use = self.token if token == "" else token
        if use:
            headers["authorization"] = "Bearer " + use
        return self.c.post("/mcp", content=json.dumps(body), headers=headers, **kwargs)

    def recipe(self, title: str, portions: int, kcal: float, protein: float) -> dict:
        return {"title": title, "blurb": "One short line.", "portions": portions,
                "portionNote": f"{portions} containers, one a day.",
                "ingredients": [{"item": "Everything", "amount": "1 batch (1000 g)",
                                 "kcal": kcal * portions, "protein": protein * portions}],
                "steps": ["Cook it.", "Portion it out by weight."], "storage": "Fridge for 3 days."}

    def payload(self, **overrides) -> dict:
        args = {"week": "2026-10-05", "session": "tue",
                "recipes": {"breakfast": self.recipe("Vanilla blueberry overnight oats", 3, 500, 52),
                            "main": self.recipe("Beef burritos", 9, 500, 51),
                            "dessert": self.recipe("Chocolate overnight oats", 3, 500, 49)},
                "timeline": ["Start the oats.", "Brown the beef.", "Roll the burritos.", "Label everything."],
                "shopping": [{"item": "Amazon Grocery 93/7 Ground Beef, 1 lb", "buy": "2 lb", "aisle": "Meat",
                              "stock": False, "search": "Amazon Grocery 93/7 ground beef 1 lb"},
                             {"item": "Mission Carb Balance Tortillas, 8 ct", "buy": "1 pack", "aisle": "Bakery",
                              "stock": False, "search": "Mission Carb Balance flour tortillas"}],
                "leftovers": ["About 170 g Greek yogurt"]}
        args.update(overrides)
        return args

    # ---- the 401 handshake ----
    def test_no_token_starts_the_sign_in(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, token=None)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "unauthorized"})
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_a_bad_token_is_invalid_token(self):
        self.insert_token("maria", "staff-token")
        self.insert_token("owen", "stale-token", expires_in=-10)
        for token in ("garbage", "staff-token", "stale-token"):
            with self.subTest(token=token):
                r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.json(), {"error": "invalid_token"})
                self.assertEqual(r.headers["www-authenticate"],
                                 'Bearer error="invalid_token", '
                                 'resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                                 'scope="menus"')

    # ---- the handshake ----
    def test_initialize_negotiates_the_protocol_version(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "claude-ai", "version": "1"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/json")
        result = r.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")   # a version we know is echoed back
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"], {"name": "Galleon", "title": "Galleon", "version": "1.0.0"})
        self.assertIn("call get_brief first", result["instructions"])
        r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                      "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r.json()["id"], 2)
        self.assertEqual(r.json()["result"]["protocolVersion"], "2025-11-25")  # anything else gets LATEST

    def test_a_notification_gets_202_and_an_empty_body(self):
        r = self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_a_newer_era_client_is_told_which_versions_we_speak(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/list",
                      "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}})
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual(body["id"], 7)
        self.assertEqual(body["error"]["code"], -32600)
        self.assertIn("initialize", body["error"]["message"])
        self.assertIn("2025-11-25", body["error"]["message"])

    def test_an_unknown_protocol_version_header_is_refused_the_same_way(self):
        r = self.c.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": 8, "method": "ping"}),
                        headers={**JSON_CT, "authorization": "Bearer " + self.token,
                                 "mcp-protocol-version": "2026-07-28"})
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual(body["id"], 8)
        self.assertEqual(body["error"]["code"], -32600)
        self.assertIn("initialize", body["error"]["message"])
        r = self.c.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}),
                        headers={**JSON_CT, "authorization": "Bearer " + self.token,
                                 "mcp-protocol-version": "2025-06-18"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"], {})

    def test_ping_answers_an_empty_result(self):
        r = self.rpc({"jsonrpc": "2.0", "id": "p1", "method": "ping"})
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": "p1", "result": {}})

    def test_tools_list_describes_both_tools(self):
        result = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]
        self.assertNotIn("nextCursor", result)
        tools = result["tools"]
        self.assertEqual([t["name"] for t in tools], ["get_brief", "save_session"])
        self.assertEqual([t["title"] for t in tools], ["The household's prep brief", "Save a prep session"])
        self.assertIn("Call this first", tools[0]["description"])
        self.assertIn("call save_session again", tools[1]["description"])
        self.assertEqual(set(tools[0]["inputSchema"]["properties"]), {"date"})
        self.assertEqual(tools[1]["inputSchema"]["required"],
                         ["week", "session", "recipes", "timeline", "shopping", "leftovers"])
        self.assertEqual(set(tools[1]["inputSchema"]["properties"]),
                         set(tools[1]["inputSchema"]["required"]))
        self.assertIs(tools[1]["inputSchema"]["additionalProperties"], False)
        for tool in tools:
            self.assertNotIn("outputSchema", tool)  # deliberately none; structuredContent is still returned

    # ---- tools/call ----
    def test_get_brief_returns_text_and_structured_content(self):
        from app import menu
        r = self.rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": {"date": "2026-09-29"}}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertNotIn("isError", result)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        brief = result["structuredContent"]
        self.assertEqual(brief["week"], "2026-09-28")
        self.assertEqual(brief["session"], "tue")
        self.assertEqual(brief["date"], "2026-09-29")
        self.assertEqual(brief["portions"], {"breakfast": 3, "main": 9, "dessert": 3})
        self.assertEqual(brief["targets"]["kcal"], 500)
        self.assertIn("Every single portion", brief["rules"])
        self.assertEqual(brief["existing"]["titles"]["main"], "Chipotle chicken burrito bowls")
        self.assertEqual(brief["otherSession"]["session"], "fri")
        # no arguments at all is legal: the brief resolves the next prep day itself
        result = self.rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "get_brief"}}).json()["result"]
        self.assertNotIn("isError", result)
        brief = result["structuredContent"]
        self.assertEqual(brief["date"], menu.resolve_prep_date(None))
        self.assertEqual((brief["week"], brief["session"]), menu.week_and_session(brief["date"]))

    def test_get_brief_on_a_wednesday_is_a_tool_error(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": {"date": "2026-09-30"}}})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertEqual(result["content"][0]["text"],
                         "2026-09-30 is a Wednesday. Prep sessions are Tuesdays and Fridays — "
                         "pick one of those.")

    def test_save_session_writes_the_plan(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": self.payload()}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertNotIn("isError", result)
        self.assertIn("Saved Tuesday 6 Oct (week of 5 Oct)", result["content"][0]["text"])
        self.assertIn("Open http://testserver/#meals", result["content"][0]["text"])
        structured = result["structuredContent"]
        self.assertEqual(structured["week"], "2026-10-05")
        self.assertEqual(structured["session"], "tue")
        self.assertEqual(structured["date"], "2026-10-06")
        self.assertEqual(structured["shoppingCount"], 2)
        self.assertEqual(structured["url"], "http://testserver/#meals")
        self.assertEqual(structured["recipes"]["main"], {"title": "Beef burritos", "portions": 9,
                                                        "kcalPerPortion": 500, "proteinPerPortion": 51})
        # …and the app reads it straight back
        plan = self.c.get("/api/plans/2026-10-05").json()["plan"]
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["breakfast"]["title"],
                         "Vanilla blueberry overnight oats")
        self.assertEqual(plan["sessions"]["tue"]["covers"], "Wed, Thu, Fri")
        self.assertEqual(plan["sessions"]["tue"]["leftovers"], ["About 170 g Greek yogurt"])
        self.assertEqual([i["id"] for i in plan["shopping"]], ["t01", "t02"])
        self.assertEqual(plan["source"], "Claude")

    def test_an_off_target_session_saves_nothing(self):
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "save_session", "arguments": self.payload()}})
        bad = self.payload(recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                    "main": self.recipe("Burritos", 9, 612, 51),
                                    "dessert": self.recipe("Pots", 3, 500, 44)})
        r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": bad}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertNotIn("structuredContent", result)
        self.assertEqual(result["content"][0]["text"],
                         "Nothing was saved. Fix these and call save_session again:\n"
                         "main: 612 kcal per portion, target 500 ±35.\n"
                         "dessert: 44 g protein per portion, need at least 47.")
        # the session that was already there is untouched
        plan = self.c.get("/api/plans/2026-10-05").json()["plan"]
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["main"]["title"], "Beef burritos")

    def test_a_wrong_argument_shape_is_a_tool_error(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": {"week": "2026-10-05"}}})
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertEqual(result["content"][0]["text"],
                         "save_session needs an object with week, session, recipes, timeline, "
                         "shopping and leftovers.")

    def test_unknown_tool_and_bad_arguments(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                      "params": {"name": "delete_everything", "arguments": {}}})
        self.assertEqual(r.json()["error"], {"code": -32602, "message": "Unknown tool: delete_everything."})
        r = self.rpc({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": "2026-09-29"}})
        self.assertEqual(r.json()["error"], {"code": -32602, "message": "arguments must be an object."})

    # ---- the envelope ----
    def test_a_malformed_envelope_is_a_json_rpc_error(self):
        r = self.c.post("/mcp", content=b"{not json", headers={**JSON_CT,
                                                               "authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "Parse error."}})
        r = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(r.json()["error"], {"code": -32600,
                                             "message": "This server does not support batched requests."})
        r = self.rpc("just a string")
        self.assertEqual(r.json()["error"]["code"], -32600)
        self.assertEqual(r.json()["error"]["message"], "Invalid request.")
        r = self.rpc({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        self.assertEqual(r.json()["error"]["message"], "Invalid request.")
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "nope"})
        self.assertEqual(r.json()["error"], {"code": -32601, "message": "Method not found: nope."})

    def test_get_and_delete_are_not_allowed(self):
        for method in ("get", "delete"):
            with self.subTest(method=method):
                r = getattr(self.c, method)("/mcp", headers={"authorization": "Bearer " + self.token})
                self.assertEqual(r.status_code, 405)
                self.assertEqual(r.headers["allow"], "POST")
                self.assertEqual(r.json(), {"error": "method_not_allowed"})

    def test_a_huge_body_is_refused(self):
        r = self.c.post("/mcp", content=b"x" * 300_000,
                        headers={**JSON_CT, "authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "Request body too large."}})


if __name__ == "__main__":
    unittest.main()
