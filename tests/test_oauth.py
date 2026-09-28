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
        client_id = self.register()
        self.login("owen")
        p = self.params(client_id)
        q = self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "allow"},
                                      follow_redirects=False))
        self.assertTrue(q["code"][0])
        self.assertEqual(q["state"], ["xyz123"])
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
        client_id = self.register()
        self.login("owen")
        p = {**self.params(client_id), "decision": "allow"}
        r = self.c.post("/oauth/authorize", data=p, headers={"sec-fetch-site": "cross-site"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("That request didn't come from this page.", r.text)
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
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_reusing_a_rotated_refresh_token_kills_the_family(self):
        tokens = self.dance()
        fresh = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": tokens["refresh_token"],
                                                  "client_id": tokens["client_id"]}).json()
        # presenting the rotated-away token is treated as a leak
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")
        # …and it takes the rest of the family with it
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": fresh["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")

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


if __name__ == "__main__":
    unittest.main()
