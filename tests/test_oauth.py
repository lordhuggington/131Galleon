"""OAuth server tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
