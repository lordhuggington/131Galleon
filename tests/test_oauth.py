"""OAuth server tests. Run with:  python3 -m unittest discover -s tests"""
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
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL")


class OAuthTest(unittest.TestCase):
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

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def login(self, who: str) -> dict:
        pw = {"owen": "owner-pass", "maria": "staff-pass"}[who]
        r = self.c.post("/api/login", json={"username": who, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

    def register(self, uris: list[str] | None = None, name: str = "Claude") -> str:
        r = self.c.post("/oauth/register", json={"client_name": name, "redirect_uris": uris or [CB]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["client_id"]

    def params(self, client_id: str, **overrides) -> dict:
        """A valid authorize query. The challenge is RFC 7636's example; Task 7 uses a real verifier."""
        p = {"response_type": "code", "client_id": client_id, "redirect_uri": CB,
             "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
             "code_challenge_method": "S256", "state": "xyz123", "scope": "menus"}
        p.update(overrides)
        return p

    def query_of(self, response) -> dict[str, list[str]]:
        self.assertEqual(response.status_code, 302, response.text)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(response.headers["location"]).query)

    def pkce(self) -> tuple[str, str]:
        """(verifier, S256 challenge) — a real pair, exactly as Claude computes it."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return verifier, challenge

    def code_for(self, client_id: str, challenge: str, **overrides) -> str:
        """Sign in as the owner and press Allow. Returns the authorization code from the redirect."""
        self.login("owen")
        p = self.params(client_id, code_challenge=challenge, **overrides)
        return self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "allow"},
                                         follow_redirects=False))["code"][0]

    def dance(self) -> dict:
        """Register, consent and exchange. The token response plus the "client_id" that earned it."""
        client_id = self.register()
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.status_code, 200, r.text)
        return {**r.json(), "client_id": client_id}

    # ---- metadata ----
    def test_protected_resource_metadata(self):
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp"):
            with self.subTest(path=path):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["cache-control"], "no-store")
                self.assertEqual(r.json(), {"resource": "http://testserver/mcp",
                                            "authorization_servers": ["http://testserver"],
                                            "scopes_supported": ["menus"],
                                            "bearer_methods_supported": ["header"],
                                            "resource_name": "Galleon"})
                self.assertTrue(r.json()["resource"].endswith("/mcp"))

    def test_authorization_server_metadata(self):
        r = self.c.get("/.well-known/oauth-authorization-server")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {
            "issuer": "http://testserver",
            "authorization_endpoint": "http://testserver/oauth/authorize",
            "token_endpoint": "http://testserver/oauth/token",
            "registration_endpoint": "http://testserver/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["menus"]})

    # ---- registration ----
    def test_register_returns_a_client(self):
        r = self.c.post("/oauth/register", json={
            "client_name": "Claude", "redirect_uris": [CB],
            "client_uri": "https://claude.ai", "token_endpoint_auth_method": "none",
            "something_else": {"ignored": True}})   # every other RFC 7591 field is accepted and ignored
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertTrue(body["client_id"])
        self.assertIsInstance(body["client_id_issued_at"], int)
        self.assertEqual(body["client_name"], "Claude")
        self.assertEqual(body["redirect_uris"], [CB])
        self.assertEqual(body["token_endpoint_auth_method"], "none")
        self.assertEqual(body["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(body["response_types"], ["code"])

    def test_register_accepts_a_loopback_redirect(self):
        for uri in ("http://localhost:53127/callback", "http://127.0.0.1:8912/callback"):
            with self.subTest(uri=uri):
                self.assertTrue(self.register([uri]))

    def test_register_refuses_a_foreign_redirect(self):
        for uri in ("https://evil.example/cb", "https://claude.ai/api/mcp/auth_callback?next=evil",
                    "http://localhost:1234/cb?x=1", "javascript:alert(1)"):
            with self.subTest(uri=uri):
                r = self.c.post("/oauth/register", json={"client_name": "X", "redirect_uris": [uri]})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_redirect_uri")
                self.assertTrue(r.json()["error_description"])

    def test_register_needs_redirect_uris(self):
        for body in ({"client_name": "X"}, {"client_name": "X", "redirect_uris": []},
                     {"redirect_uris": "https://claude.ai/api/mcp/auth_callback"}):
            with self.subTest(body=body):
                r = self.c.post("/oauth/register", json=body)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_client_metadata")

    def test_register_refuses_a_redirect_uri_that_cannot_be_parsed_safely(self):
        """A header-splitting URI and a port outside 0-65535 are refusals, not a 500."""
        for uri in ("http://localhost:1234/cb\r\nX-Injected: 1", "http://localhost:1234/cb\nX-Injected: 1",
                    "http://localhost:1234/cb\tx", "http://localhost:99999999/cb",
                    "http://127.0.0.1:99999999/cb"):
            with self.subTest(uri=uri):
                r = self.c.post("/oauth/register", json={"client_name": "X", "redirect_uris": [uri]})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_redirect_uri")

    def test_register_keeps_control_characters_out_of_the_log(self):
        """client_name comes from the client: a newline in it must not forge a second log line."""
        from app.db import connect
        name = "Claude\r\nregistered OAuth client forged"
        with self.assertLogs("house_run_sheet", "INFO") as cm:
            r = self.c.post("/oauth/register", json={"client_name": name, "redirect_uris": [CB]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(cm.output), 1)
        self.assertNotIn("\n", cm.output[0])
        self.assertNotIn("\r", cm.output[0])
        self.assertIn("Clauderegistered OAuth client forged", cm.output[0])
        # storage is untouched: the name is echoed back and stored exactly as it was sent
        self.assertEqual(r.json()["client_name"], name)
        conn = connect()
        try:
            stored = conn.execute("SELECT client_name FROM oauth_clients WHERE client_id = ?",
                                  (r.json()["client_id"],)).fetchone()["client_name"]
        finally:
            conn.close()
        self.assertEqual(stored, name)

    def test_register_caps_the_redirect_uri_list(self):
        many = [f"http://127.0.0.1:{5000 + n}/callback" for n in range(9)]
        r = self.c.post("/oauth/register", json={"client_name": "X", "redirect_uris": many})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_client_metadata")
        self.assertEqual(r.json()["error_description"], "redirect_uris may list at most 8 URIs.")
        self.assertTrue(self.register(many[:8]))  # eight is still fine
        long_uri = "http://127.0.0.1:5000/" + "c" * (513 - len("http://127.0.0.1:5000/"))
        self.assertEqual(len(long_uri), 513)
        r = self.c.post("/oauth/register", json={"client_name": "X", "redirect_uris": [long_uri]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_redirect_uri")
        self.assertEqual(r.json()["error_description"], "A redirect URI may be at most 512 characters.")
        self.assertNotIn("ccc", r.json()["error_description"])  # the URI itself is not echoed back

    def test_register_refuses_to_pass_the_client_cap(self):
        from app import oauth
        from app.db import connect, now_iso
        from app.oauth import MAX_CLIENTS

        def count() -> int:
            conn = connect()
            try:
                return conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
            finally:
                conn.close()

        conn = connect()
        try:
            for n in range(MAX_CLIENTS):
                conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) "
                             "VALUES (?, '', '[]', ?)", (f"filler-{n}", now_iso()))
        finally:
            conn.close()
        self.assertEqual(count(), MAX_CLIENTS)
        with self.assertLogs("house_run_sheet", "WARNING") as cm:
            r = self.c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CB]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_client_metadata")
        self.assertEqual(r.json()["error_description"],
                         "This server is not accepting new client registrations right now.")
        self.assertTrue(any("registration" in line for line in cm.output))
        self.assertEqual(count(), MAX_CLIENTS)  # nothing was inserted past the cap
        # one row old enough to sweep makes room for exactly one more registration
        conn = connect()
        try:
            conn.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id = 'filler-0'",
                         (oauth._iso_ago(31 * 86400),))
        finally:
            conn.close()
        self.assertTrue(self.register())
        self.assertEqual(count(), MAX_CLIENTS)

    def test_register_refusals_say_why(self):
        r = self.c.post("/oauth/register", content=b"x" * (64 * 1024 + 1),
                        headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json(), {"error": "invalid_client_metadata",
                                    "error_description": "That registration request is too large."})
        for body in (b"{not json", b"[]", b'"a string"', b"7"):
            with self.subTest(body=body):
                r = self.c.post("/oauth/register", content=body,
                                headers={"content-type": "application/json"})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json(), {"error": "invalid_client_metadata",
                                            "error_description":
                                                "The registration request must be a JSON object."})

    def test_client_uris_survives_a_doctored_row(self):
        """A JSON scalar in redirect_uris must not be iterated character by character."""
        from app import oauth
        for stored in ('"http://127.0.0.1:1/cb"', "7", "null", "{not json"):
            with self.subTest(stored=stored):
                with self.assertLogs("house_run_sheet", "WARNING") as cm:
                    self.assertEqual(oauth._client_uris({"client_id": "c1", "redirect_uris": stored}), [])
                self.assertIn("unreadable redirect_uris", cm.output[0])
                self.assertIn("c1", cm.output[0])

    # ---- the consent page ----
    def test_an_owner_sees_the_consent_page(self):
        client_id = self.register()
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("form-action 'self' https://claude.ai", r.headers["content-security-policy"])
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("Claude wants to read and write Galleon menus", r.text)
        self.assertIn(">Allow</button>", r.text)
        self.assertIn(">Deny</button>", r.text)
        self.assertIn('<script src="/oauth.js" defer></script>', r.text)
        self.assertIn(f'name="client_id" value="{client_id}"', r.text)
        self.assertIn('name="state" value="xyz123"', r.text)
        self.assertIn("Owen", r.text)

    def test_no_cookie_shows_the_sign_in_form(self):
        client_id = self.register()
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('id="phone"', r.text)
        self.assertIn("Text me a code", r.text)
        self.assertIn("Owner? Sign in with a password", r.text)
        self.assertIn('src="/oauth.js"', r.text)
        self.assertNotIn(">Allow</button>", r.text)

    def test_staff_are_refused(self):
        client_id = self.register()
        self.login("maria")
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Only an owner can connect Claude.", r.text)
        self.assertNotIn(">Allow</button>", r.text)
        self.assertIn('id="signOut"', r.text)

    def test_the_consent_page_escapes_everything_it_echoes(self):
        client_id = self.register(name="<script>alert(1)</script>")
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params(client_id, state='"><b>x'))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("<script>alert(1)</script>", r.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", r.text)
        self.assertNotIn('"><b>x', r.text)
        self.assertIn("&quot;&gt;&lt;b&gt;x", r.text)

    def test_an_unknown_client_gets_an_error_page_and_no_redirect(self):
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params("not-a-client"), follow_redirects=False)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn("location", r.headers)
        self.assertIn("the app asking isn't registered here", r.text)
        # a redirect_uri the client never registered is the same class of failure
        client_id = self.register()
        r = self.c.get("/oauth/authorize",
                       params=self.params(client_id, redirect_uri="https://evil.example/cb"),
                       follow_redirects=False)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn("location", r.headers)

    def test_a_loopback_redirect_matches_with_the_port_ignored(self):
        """Claude Code registers one loopback port and listens on another (spec §3)."""
        client_id = self.register(["http://127.0.0.1:53127/callback"])
        self.login("owen")
        presented = "http://127.0.0.1:61990/callback"
        r = self.c.get("/oauth/authorize", params=self.params(client_id, redirect_uri=presented))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Claude wants to read and write Galleon menus", r.text)
        self.assertIn(f'name="redirect_uri" value="{presented}"', r.text)
        verifier, challenge = self.pkce()
        p = self.params(client_id, redirect_uri=presented, code_challenge=challenge)
        r = self.c.post("/oauth/authorize", data={**p, "decision": "allow"}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        self.assertTrue(r.headers["location"].startswith(presented + "?"), r.headers["location"])
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["code"][0]
        self.assertTrue(code)
        # the token request is bound to the URI the code was issued for, not the registered one
        base = {"grant_type": "authorization_code", "code": code, "client_id": client_id,
                "code_verifier": verifier}
        r = self.c.post("/oauth/token", data={**base, "redirect_uri": "http://127.0.0.1:53127/callback"})
        self.assertEqual(r.json()["error"], "invalid_grant", r.text)
        r = self.c.post("/oauth/token", data={**base, "redirect_uri": presented})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["access_token"])
        self.assertTrue(r.json()["refresh_token"])

    def test_a_near_miss_redirect_is_still_refused(self):
        """Only the port is ignored: the hostname, the path and a non-loopback URI all have to match."""
        loopback = self.register(["http://127.0.0.1:53127/callback"])
        hosted = self.register()
        self.login("owen")
        cases = [(loopback, "http://localhost:61990/callback"),     # localhost is not 127.0.0.1
                 (loopback, "http://127.0.0.1:61990/other"),        # another path
                 (loopback, "https://127.0.0.1:61990/callback"),    # another scheme
                 (hosted, CB + "2")]                               # a prefix of the hosted callback
        for client_id, uri in cases:
            with self.subTest(uri=uri):
                r = self.c.get("/oauth/authorize", params=self.params(client_id, redirect_uri=uri),
                               follow_redirects=False)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertNotIn("location", r.headers)
                self.assertIn("the app asking isn't registered here", r.text)
                r = self.c.post("/oauth/authorize",
                                data={**self.params(client_id, redirect_uri=uri), "decision": "allow"},
                                follow_redirects=False)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertNotIn("location", r.headers)

    def test_redirect_matches_ignores_the_port_on_loopback_only(self):
        from app.oauth import _redirect_matches
        loopback = ["http://127.0.0.1:53127/callback"]
        self.assertTrue(_redirect_matches(loopback, "http://127.0.0.1:53127/callback"))
        self.assertTrue(_redirect_matches(loopback, "http://127.0.0.1:1/callback"))
        self.assertTrue(_redirect_matches(loopback, "http://127.0.0.1/callback"))
        self.assertFalse(_redirect_matches(loopback, "http://localhost:53127/callback"))
        self.assertFalse(_redirect_matches(loopback, "http://127.0.0.1:53127/callback?x=1"))
        self.assertFalse(_redirect_matches(loopback, "http://127.0.0.1:53127/callback#f"))
        self.assertFalse(_redirect_matches(loopback, "http://127.0.0.1:53127/callback/"))
        self.assertFalse(_redirect_matches(loopback, "http://evil.example:53127/callback"))
        self.assertFalse(_redirect_matches(loopback, ""))
        self.assertTrue(_redirect_matches([CB], CB))
        self.assertFalse(_redirect_matches([CB], CB + "2"))
        self.assertFalse(_redirect_matches([], "http://127.0.0.1:1/callback"))
        # a registered URI that does not parse is skipped, not a 500
        self.assertFalse(_redirect_matches(["http://[::1/callback"], "http://127.0.0.1:1/callback"))
        self.assertFalse(_redirect_matches(loopback, "http://[::1/callback"))

    def test_a_bad_parameter_redirects_with_an_error(self):
        client_id = self.register()
        self.login("owen")
        cases = [({"response_type": "token"}, "unsupported_response_type"),
                 ({"code_challenge": ""}, "invalid_request"),
                 ({"code_challenge_method": "plain"}, "invalid_request"),
                 ({"scope": "menus admin"}, "invalid_scope")]
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                q = self.query_of(self.c.get("/oauth/authorize", params=self.params(client_id, **overrides),
                                             follow_redirects=False))
                self.assertEqual(q["error"], [expected])
                self.assertEqual(q["state"], ["xyz123"])

    def test_allow_issues_a_code_and_deny_does_not(self):
        from app.auth import token_hash
        from app.db import connect
        client_id = self.register()
        me = self.login("owen")
        p = self.params(client_id)
        q = self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "allow"},
                                      follow_redirects=False))
        code = q["code"][0]
        self.assertTrue(code)
        self.assertEqual(q["state"], ["xyz123"])
        # the row behind that code: bound to this client, this owner and this redirect_uri, and the
        # code itself is stored only as a hash
        conn = connect()
        try:
            rows = conn.execute("SELECT * FROM oauth_codes").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        row = dict(rows[0])
        self.assertEqual(row["code_hash"], token_hash(code))
        self.assertEqual(row["client_id"], client_id)
        self.assertEqual(row["user_id"], me["id"])
        self.assertEqual(row["redirect_uri"], CB)
        self.assertEqual(row["code_challenge"], p["code_challenge"])
        self.assertEqual(row["scope"], "menus")
        self.assertGreater(row["expires_at"], int(time.time()))
        self.assertLessEqual(row["expires_at"], int(time.time()) + 600)
        self.assertNotIn(code, [str(v) for v in row.values()])
        q = self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "deny"},
                                      follow_redirects=False))
        self.assertEqual(q["error"], ["access_denied"])
        self.assertEqual(q["state"], ["xyz123"])
        self.assertNotIn("code", q)

    def test_post_authorize_needs_an_owner_session(self):
        client_id = self.register()
        p = {**self.params(client_id), "decision": "allow"}
        r = self.c.post("/oauth/authorize", data=p, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("Please sign in as an owner first.", r.text)
        self.login("maria")
        r = self.c.post("/oauth/authorize", data=p, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("Only an owner can connect Claude.", r.text)

    def test_post_authorize_checks_sec_fetch_site(self):
        from app.db import connect
        client_id = self.register()
        self.login("owen")
        p = {**self.params(client_id), "decision": "allow"}
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                r = self.c.post("/oauth/authorize", data=p, headers={"sec-fetch-site": site},
                                follow_redirects=False)
                self.assertEqual(r.status_code, 403, r.text)
                self.assertIn("That request didn't come from this page.", r.text)
                self.assertNotIn("location", r.headers)
                conn = connect()
                try:  # no code was issued on the way out
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM oauth_codes").fetchone()[0], 0)
                finally:
                    conn.close()
        # the browser's own value for a form on this page is allowed
        r = self.c.post("/oauth/authorize", data=p, headers={"sec-fetch-site": "same-origin"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)

    # ---- the token endpoint ----
    def test_the_full_dance_returns_tokens(self):
        tokens = self.dance()
        self.assertTrue(tokens["access_token"])
        self.assertTrue(tokens["refresh_token"])
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(tokens["expires_in"], 3600)
        self.assertEqual(tokens["scope"], "menus")

    def test_a_wrong_verifier_burns_the_code(self):
        client_id = self.register()
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": "not-the-verifier"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_grant")
        # the row is deleted, so even the right verifier can't rescue it
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_a_reused_code_is_refused(self):
        client_id = self.register()
        verifier, challenge = self.pkce()
        data = {"grant_type": "authorization_code", "code": self.code_for(client_id, challenge),
                "client_id": client_id, "redirect_uri": CB, "code_verifier": verifier}
        self.assertEqual(self.c.post("/oauth/token", data=data).status_code, 200)
        r = self.c.post("/oauth/token", data=data)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_the_code_is_bound_to_its_client_and_redirect_uri(self):
        client_id = self.register()
        other = self.register(name="Another app")
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        base = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier}
        r = self.c.post("/oauth/token", data={**base, "client_id": other, "redirect_uri": CB})
        self.assertEqual(r.json()["error"], "invalid_grant")
        r = self.c.post("/oauth/token", data={**base, "client_id": client_id,
                                              "redirect_uri": "http://localhost:1/callback"})
        self.assertEqual(r.json()["error"], "invalid_grant")
        # neither attempt burned it: the honest exchange still works
        r = self.c.post("/oauth/token", data={**base, "client_id": client_id, "redirect_uri": CB})
        self.assertEqual(r.status_code, 200, r.text)

    def test_missing_fields_are_invalid_request(self):
        client_id = self.register()
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id})
        self.assertEqual(r.json()["error"], "invalid_request")
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id})
        self.assertEqual(r.json()["error"], "invalid_request")

    def test_a_json_body_and_a_bad_grant_type_are_refused(self):
        client_id = self.register()
        r = self.c.post("/oauth/token", json={"grant_type": "refresh_token", "refresh_token": "x",
                                              "client_id": client_id})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_request")
        self.assertEqual(r.json()["error_description"],
                         "The token endpoint takes application/x-www-form-urlencoded.")
        r = self.c.post("/oauth/token", data={"grant_type": "password", "username": "owen",
                                              "password": "owner-pass"})
        self.assertEqual(r.json()["error"], "unsupported_grant_type")
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x",
                                              "client_id": "never-registered", "redirect_uri": CB,
                                              "code_verifier": "y"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client")

    def test_refresh_rotates_the_pair(self):
        tokens = self.dance()
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        fresh = r.json()
        self.assertNotEqual(fresh["refresh_token"], tokens["refresh_token"])
        self.assertNotEqual(fresh["access_token"], tokens["access_token"])
        self.assertEqual(fresh["expires_in"], 3600)
        self.assertEqual(fresh["scope"], "menus")
        self.assertEqual(fresh["token_type"], "Bearer")
        with self.assertLogs("house_run_sheet", "WARNING") as cm:
            r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": tokens["refresh_token"],
                                                  "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")
        self.assertIn("revoked OAuth token family", cm.output[0])

    def test_reusing_a_rotated_refresh_token_kills_the_family(self):
        from app import oauth
        from app.db import connect
        tokens = self.dance()
        fresh = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": tokens["refresh_token"],
                                                  "client_id": tokens["client_id"]}).json()
        conn = connect()
        try:
            self.assertIsNotNone(oauth.owner_for_bearer(conn, "Bearer " + fresh["access_token"]))
            # presenting the rotated-away token is treated as a leak
            with self.assertLogs("house_run_sheet", "WARNING") as cm:
                r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                      "refresh_token": tokens["refresh_token"],
                                                      "client_id": tokens["client_id"]})
            self.assertEqual(r.json()["error"], "invalid_grant")
            self.assertEqual(len(cm.output), 1)
            self.assertIn("revoked OAuth token family", cm.output[0])
            self.assertNotIn(tokens["refresh_token"], cm.output[0])
            # the access token issued before the kill is dead too
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + fresh["access_token"]))
        finally:
            conn.close()
        # …and the live refresh token goes with the rest of the family
        with self.assertLogs("house_run_sheet", "WARNING") as cm:
            r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": fresh["refresh_token"],
                                                  "client_id": tokens["client_id"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")
        self.assertIn("revoked OAuth token family", cm.output[0])

    # ---- the bearer check ----
    def test_owner_for_bearer_accepts_only_a_live_owner_access_token(self):
        from app import oauth
        from app.auth import token_hash
        from app.db import connect, now_iso
        tokens = self.dance()
        conn = connect()
        try:
            row = oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"])
            self.assertIsNotNone(row)
            self.assertEqual(row["username"], "owen")
            self.assertEqual(row["role"], "owner")
            self.assertIsNotNone(conn.execute("SELECT last_used_at FROM oauth_tokens WHERE token_hash = ?",
                                              (token_hash(tokens["access_token"]),)).fetchone()["last_used_at"])
            self.assertIsNotNone(oauth.owner_for_bearer(conn, "bearer " + tokens["access_token"]))  # case-insensitive
            self.assertIsNone(oauth.owner_for_bearer(conn, tokens["access_token"]))                 # no scheme
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer "))
            self.assertIsNone(oauth.owner_for_bearer(conn, None))
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer nonsense"))
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["refresh_token"]))    # wrong kind
            for name, username, expires_in in (("expired", "owen", -10), ("staff-owned", "maria", 3600)):
                with self.subTest(token=name):
                    conn.execute(
                        "INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, scope, "
                        "expires_at, created_at) VALUES (?, 'access', ?, "
                        "(SELECT id FROM users WHERE username = ?), ?, 'menus', ?, ?)",
                        (token_hash(name), tokens["client_id"], username, f"fam-{name}",
                         int(time.time()) + expires_in, now_iso()))
                    self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + name))
        finally:
            conn.close()

    def test_a_locked_database_does_not_break_a_valid_bearer(self):
        """The last_used_at stamp is best effort: a write failure must not turn a good token into a 500."""
        import sqlite3

        from app import oauth
        from app.db import connect
        tokens = self.dance()

        class NoWrites:
            """Everything reads; every UPDATE fails the way a locked database does."""

            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                if sql.lstrip().upper().startswith("UPDATE"):
                    raise sqlite3.OperationalError("database is locked")
                return self._conn.execute(sql, *args)

        conn = connect()
        try:
            with self.assertLogs("house_run_sheet", "WARNING") as cm:
                row = oauth.owner_for_bearer(NoWrites(conn), "Bearer " + tokens["access_token"])
            self.assertIsNotNone(row)
            self.assertEqual(row["username"], "owen")
            self.assertEqual(len(cm.output), 1)
            self.assertIn(row["family"], cm.output[0])
            self.assertNotIn(tokens["access_token"], cm.output[0])
            self.assertIsNone(conn.execute("SELECT last_used_at FROM oauth_tokens WHERE kind = 'access'")
                              .fetchone()["last_used_at"])
        finally:
            conn.close()

    def test_the_unauthorized_response_carries_the_resource_metadata(self):
        from app import oauth
        r = oauth.unauthorized(False)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(json.loads(r.body), {"error": "unauthorized"})
        r = oauth.unauthorized(True)
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer error="invalid_token", '
                         'resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(json.loads(r.body), {"error": "invalid_token"})

    # ---- the connections list ----
    def test_connections_lists_the_grant(self):
        self.dance()
        r = self.c.get("/api/oauth/connections")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["mcpUrl"], "http://testserver/mcp")
        self.assertEqual(len(body["connections"]), 1)
        entry = body["connections"][0]
        self.assertEqual(entry["clientName"], "Claude")
        self.assertTrue(entry["connectedAt"])
        self.assertIsNone(entry["lastUsedAt"])  # the access token has not been used yet
        self.assertRegex(entry["family"], r"^[A-Za-z0-9_-]{8,64}$")

    def test_disconnecting_revokes_the_whole_family(self):
        from app import oauth
        from app.db import connect
        tokens = self.dance()
        family = self.c.get("/api/oauth/connections").json()["connections"][0]["family"]
        conn = connect()
        try:
            self.assertIsNotNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"]))
            r = self.c.delete(f"/api/oauth/connections/{family}", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"ok": True})
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"]))
        finally:
            conn.close()
        self.assertEqual(self.c.get("/api/oauth/connections").json()["connections"], [])
        # the refresh token was revoked with the family, so presenting it reads as a replay
        with self.assertLogs("house_run_sheet", "WARNING") as cm:
            r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": tokens["refresh_token"],
                                                  "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")
        self.assertIn("revoked OAuth token family", cm.output[0])
        for bad in ("nope", "short", "not-a-real-family"):
            with self.subTest(family=bad):
                r = self.c.delete(f"/api/oauth/connections/{bad}", headers=H)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertEqual(r.json()["error"], "That connection doesn't exist.")

    def test_staff_cannot_touch_connections(self):
        self.dance()
        family = self.c.get("/api/oauth/connections").json()["connections"][0]["family"]
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        r = self.c.get("/api/oauth/connections")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Only an owner can do that.")
        r = self.c.delete(f"/api/oauth/connections/{family}", headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Only an owner can do that.")


if __name__ == "__main__":
    unittest.main()
