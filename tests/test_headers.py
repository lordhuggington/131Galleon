"""Cache headers from SecurityHeaders: a deploy must never strand a browser on a stale page."""
from __future__ import annotations

import unittest

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from app.main import SecurityHeaders


def _page(request):
    return HTMLResponse("<!doctype html><title>x</title>")


def _asset(request):
    if request.path_params["name"] == "missing.css":
        return PlainTextResponse("Not Found", status_code=404)
    return Response("body{}", media_type="text/css")


def _photo(request):
    return Response(b"\xff\xd8\xff", media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=604800, immutable"})


def _api(request):
    return JSONResponse({"ok": True})


class CacheHeaderTest(unittest.TestCase):
    def setUp(self):
        app = Starlette(routes=[Route("/", _page), Route("/assets/{name}", _asset),
                                Route("/photos/{name}", _photo), Route("/api/state", _api)])
        self.c = TestClient(SecurityHeaders(app))

    def test_page_must_be_revalidated(self):
        self.assertEqual(self.c.get("/").headers["cache-control"], "no-cache")

    def test_hashed_asset_is_immutable(self):
        self.assertEqual(self.c.get("/assets/index-abc.css").headers["cache-control"],
                         "public, max-age=31536000, immutable")

    def test_missing_asset_is_not_cached_long(self):
        r = self.c.get("/assets/missing.css")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("immutable", r.headers.get("cache-control", ""))

    def test_handler_choice_is_kept(self):
        self.assertEqual(self.c.get("/photos/a.jpg").headers.get_list("cache-control"),
                         ["private, max-age=604800, immutable"])

    def test_api_is_no_store(self):
        self.assertEqual(self.c.get("/api/state").headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
