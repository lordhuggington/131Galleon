"""Builder jobs, staff kinds and who sees what. Run with:  python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from test_api import H, JPEG, PHOTO_HEADERS, ROOT, restore_env, run_cli, save_env

NOT_YOURS = "That isn't part of your run sheet."
PASSWORDS = {"owen": "owner-pass", "maria": "staff-pass", "ondrej": "builder-pass",
             "dana": "dana-pass-1"}  # dana is the second housekeeper tests/test_hours.py adds


class KindTest(unittest.TestCase):
    """Owen is an owner, Maria a housekeeper and Ondrej a builder; the seed's 16 jobs are loaded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = save_env()
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        from app.auth import throttle
        throttle._fails.clear()
        run_cli(["import-seed", str(ROOT / "seed")])
        run_cli(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner", "--password", "owner-pass"])
        run_cli(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff", "--password", "staff-pass"])
        run_cli(["create-user", "--username", "ondrej", "--name", "Ondrej", "--role", "staff", "--label", "Builder",
                 "--kind", "builder", "--password", "builder-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        restore_env(self.saved_env)

    def login(self, who: str) -> dict:
        self.c.post("/api/logout", headers=H)
        r = self.c.post("/api/login", json={"username": who, "password": PASSWORDS[who]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

    def refused(self, r, status: int = 403, message: str = NOT_YOURS) -> None:
        self.assertEqual(r.status_code, status, r.text)
        self.assertEqual(r.json()["error"], message)

    def photo_path(self, photo: dict) -> Path:
        return Path(os.environ["HRS_PHOTOS_DIR"]) / photo["url"].split("/")[-1]


class JobsTest(KindTest):
    # ---- reading ----
    def test_the_seed_jobs_are_listed_in_order(self):
        self.login("ondrej")
        jobs = self.c.get("/api/jobs").json()["jobs"]
        self.assertEqual(len(jobs), 16)
        self.assertEqual([j["id"] for j in jobs], list(range(1, 17)))  # add-job-photo is aimed at 4, 8, 16
        self.assertTrue(jobs[3]["title"].startswith("Stairs"))
        j = jobs[0]
        self.assertEqual(set(j), {"id", "title", "details", "link", "status", "sortOrder", "createdAt", "updatedAt",
                                  "doneAt", "doneBy", "comments", "photos"})
        self.assertEqual((j["status"], j["doneAt"], j["doneBy"], j["comments"], j["photos"]),
                         ("todo", None, None, [], []))
        self.assertEqual(self.c.get("/api/state").json()["jobs"], jobs)

    def test_state_gives_each_kind_only_its_part(self):
        self.login("ondrej")
        state = self.c.get("/api/state").json()
        self.assertEqual(state["me"]["kind"], "builder")
        self.assertEqual((state["tasks"], state["visits"], state["hours"]), ([], {}, []))
        self.assertEqual(len(state["jobs"]), 16)
        self.login("maria")
        state = self.c.get("/api/state").json()
        self.assertEqual(state["jobs"], [])
        self.assertEqual(len(state["tasks"]), 40)
        self.login("owen")
        state = self.c.get("/api/state").json()
        self.assertEqual((len(state["tasks"]), len(state["jobs"])), (40, 16))
        self.assertEqual(state["hours"], [])

    # ---- owner writes ----
    def test_owner_adds_edits_and_deletes_a_job(self):
        self.login("owen")
        job = self.c.post("/api/jobs", json={"title": "  Fix the gate  ", "details": "Hinge is loose.\nTop one.",
                                             "link": "https://example.com/hinge"}, headers=H).json()["job"]
        self.assertEqual(job["title"], "Fix the gate")
        self.assertEqual(job["details"], "Hinge is loose.\nTop one.")
        self.assertEqual(job["sortOrder"], 17)  # goes last
        self.assertEqual(self.c.get("/api/jobs").json()["jobs"][-1]["id"], job["id"])
        edited = self.c.patch(f"/api/jobs/{job['id']}", json={"title": "Fix the side gate", "link": "",
                                                              "sortOrder": 0}, headers=H).json()["job"]
        self.assertEqual((edited["title"], edited["link"], edited["sortOrder"]), ("Fix the side gate", "", 0))
        self.assertEqual(self.c.get("/api/jobs").json()["jobs"][0]["id"], job["id"])  # sort_order, then id
        self.assertGreaterEqual(edited["updatedAt"], job["updatedAt"])
        self.assertEqual(self.c.delete(f"/api/jobs/{job['id']}", headers=H).json(), {"ok": True})
        self.assertNotIn(job["id"], [j["id"] for j in self.c.get("/api/jobs").json()["jobs"]])
        self.refused(self.c.delete(f"/api/jobs/{job['id']}", headers=H), 404, "That job doesn't exist.")
        self.refused(self.c.patch(f"/api/jobs/{job['id']}", json={"status": "done"}, headers=H), 404,
                     "That job doesn't exist.")

    def test_job_validation(self):
        self.login("owen")
        self.refused(self.c.post("/api/jobs", json={"details": "x"}, headers=H), 400, "'title' is required.")
        self.refused(self.c.post("/api/jobs", json={"title": "x" * 201}, headers=H), 400,
                     "'title' is too long (max 200 characters).")
        self.refused(self.c.post("/api/jobs", json={"title": "x", "details": "x" * 4001}, headers=H), 400,
                     "'details' is too long (max 4000 characters).")
        for link in ("javascript:alert(1)", "example.com", "ftp://example.com"):
            with self.subTest(link=link):
                self.refused(self.c.post("/api/jobs", json={"title": "x", "link": link}, headers=H), 400,
                             "A link has to start with http:// or https://.")
        self.refused(self.c.post("/api/jobs", json={"title": "x", "link": "https://" + "x" * 500}, headers=H), 400,
                     "'link' is too long (max 500 characters).")
        self.refused(self.c.patch("/api/jobs/1", json={"status": "finished"}, headers=H), 400,
                     "'status' must be one of: todo, doing, done.")
        self.refused(self.c.patch("/api/jobs/1", json={}, headers=H), 400, "Nothing to change.")
        self.refused(self.c.patch("/api/jobs/999", json={"status": "done"}, headers=H), 404, "That job doesn't exist.")
        self.refused(self.c.patch("/api/jobs/nope", json={"status": "done"}, headers=H), 404, "That job doesn't exist.")
        self.assertEqual(self.c.post("/api/jobs", json={"title": "x"}).status_code, 403)  # no X-HRS

    # ---- status ----
    def test_builder_moves_a_job_along_and_done_is_stamped(self):
        me = self.login("ondrej")
        job = self.c.patch("/api/jobs/2", json={"status": "doing"}, headers=H).json()["job"]
        self.assertEqual((job["status"], job["doneAt"], job["doneBy"]), ("doing", None, None))
        job = self.c.patch("/api/jobs/2", json={"status": "done"}, headers=H).json()["job"]
        self.assertEqual(job["status"], "done")
        self.assertIsNotNone(job["doneAt"])
        self.assertEqual(job["doneBy"], {"id": me["id"], "displayName": "Ondrej"})
        again = self.c.patch("/api/jobs/2", json={"status": "done"}, headers=H).json()["job"]
        self.assertEqual(again["doneAt"], job["doneAt"])  # re-sending done keeps the first stamp
        back = self.c.patch("/api/jobs/2", json={"status": "todo"}, headers=H).json()["job"]
        self.assertEqual((back["doneAt"], back["doneBy"]), (None, None))

    def test_builder_can_only_change_the_status(self):
        self.login("ondrej")
        for body in ({"title": "Mine now"}, {"status": "done", "title": "Mine now"}, {"details": "x"},
                     {"link": ""}, {"sortOrder": 1}, {"whatever": 1}):
            with self.subTest(body=body):
                self.refused(self.c.patch("/api/jobs/1", json=body, headers=H), 403, "Only an owner can do that.")
        job = self.c.get("/api/jobs").json()["jobs"][0]
        self.assertEqual(job["status"], "todo")  # the mixed body changed nothing
        self.refused(self.c.post("/api/jobs", json={"title": "x"}, headers=H), 403, "Only an owner can do that.")
        self.refused(self.c.delete("/api/jobs/1", headers=H), 403, "Only an owner can do that.")

    def test_housekeeper_is_refused_on_every_jobs_route(self):
        self.login("owen")
        cid = self.c.post("/api/jobs/1/comments", json={"body": "x"}, headers=H).json()["comment"]["id"]
        pid = self.c.post("/api/jobs/1/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]["id"]
        self.login("maria")
        self.refused(self.c.get("/api/jobs"))
        self.refused(self.c.patch("/api/jobs/1", json={"status": "done"}, headers=H))
        self.refused(self.c.post("/api/jobs/1/comments", json={"body": "x"}, headers=H))
        self.refused(self.c.delete(f"/api/jobs/1/comments/{cid}", headers=H))
        self.refused(self.c.post("/api/jobs/1/photos", content=JPEG, headers=PHOTO_HEADERS))
        self.refused(self.c.delete(f"/api/jobs/1/photos/{pid}", headers=H))
        # the owner-only ones keep the owner refusal
        self.refused(self.c.post("/api/jobs", json={"title": "x"}, headers=H), 403, "Only an owner can do that.")
        self.refused(self.c.delete("/api/jobs/1", headers=H), 403, "Only an owner can do that.")

    def test_builder_is_refused_on_visit_routes(self):
        self.login("maria")
        photo = self.c.post("/api/visits/2026-09-29/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.login("owen")
        eid = self.c.post("/api/visits/2026-09-29/extras", json={"title": "x"}, headers=H).json()["id"]
        self.login("ondrej")
        self.refused(self.c.put("/api/visits/2026-09-29/tasks/t02", json={"done": True}, headers=H))
        self.refused(self.c.put("/api/visits/2026-09-29/note", json={"note": "x"}, headers=H))
        self.refused(self.c.patch(f"/api/visits/2026-09-29/extras/{eid}", json={"done": True}, headers=H))
        self.refused(self.c.post("/api/visits/2026-09-29/photos", content=JPEG, headers=PHOTO_HEADERS))
        self.refused(self.c.delete(f"/api/visits/2026-09-29/photos/{photo['id']}", headers=H))
        self.refused(self.c.post("/api/visits/2026-09-29/extras", json={"title": "x"}, headers=H), 403,
                     "Only an owner can do that.")
        self.refused(self.c.post("/api/tasks", json={"title": "x"}, headers=H), 403, "Only an owner can do that.")
        self.refused(self.c.get("/api/users"), 403, "Only an owner can do that.")

    # ---- comments ----
    def test_comments_are_added_oldest_first_and_deleted_by_author_or_owner(self):
        owen = self.login("owen")
        mine = self.c.post("/api/jobs/4/comments", json={"body": "  Black railing please.  "},
                           headers=H).json()["comment"]
        self.assertEqual(mine["body"], "Black railing please.")
        self.assertEqual(mine["by"], {"id": owen["id"], "displayName": "Owen", "role": "owner"})
        self.assertEqual(set(mine), {"id", "body", "createdAt", "by"})
        ondrej = self.login("ondrej")
        theirs = self.c.post("/api/jobs/4/comments", json={"body": "Will do."}, headers=H).json()["comment"]
        job = [j for j in self.c.get("/api/jobs").json()["jobs"] if j["id"] == 4][0]
        self.assertEqual(job["comments"], [mine, theirs])
        self.assertEqual(theirs["by"], {"id": ondrej["id"], "displayName": "Ondrej", "role": "staff"})
        self.refused(self.c.delete(f"/api/jobs/4/comments/{mine['id']}", headers=H), 403,
                     "You can only delete your own comments.")
        self.assertEqual(self.c.delete(f"/api/jobs/4/comments/{theirs['id']}", headers=H).json(), {"ok": True})
        self.refused(self.c.delete(f"/api/jobs/4/comments/{theirs['id']}", headers=H), 404,
                     "That comment doesn't exist.")
        other = self.c.post("/api/jobs/4/comments", json={"body": "x"}, headers=H).json()["comment"]
        self.login("owen")  # an owner deletes anyone's
        self.assertEqual(self.c.delete(f"/api/jobs/4/comments/{other['id']}", headers=H).status_code, 200)
        self.refused(self.c.delete(f"/api/jobs/5/comments/{mine['id']}", headers=H), 404,
                     "That comment doesn't exist.")  # a comment only exists under its own job
        self.assertEqual(self.c.delete(f"/api/jobs/4/comments/{mine['id']}", headers=H).status_code, 200)

    def test_comment_validation(self):
        self.login("ondrej")
        self.refused(self.c.post("/api/jobs/1/comments", json={"body": "   "}, headers=H), 400, "'body' is required.")
        self.refused(self.c.post("/api/jobs/1/comments", json={"body": "x" * 2001}, headers=H), 400,
                     "'body' is too long (max 2000 characters).")
        self.refused(self.c.post("/api/jobs/999/comments", json={"body": "x"}, headers=H), 404,
                     "That job doesn't exist.")
        self.refused(self.c.delete("/api/jobs/1/comments/nope", headers=H), 404, "That comment doesn't exist.")

    # ---- photos ----
    def test_job_photos_upload_list_serve_and_delete(self):
        ondrej = self.login("ondrej")
        r = self.c.post("/api/jobs/8/photos?caption=Closet%20before", content=JPEG, headers=PHOTO_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        photo = r.json()["photo"]
        self.assertEqual(set(photo), {"id", "caption", "url", "createdAt", "by"})
        self.assertEqual(photo["caption"], "Closet before")
        self.assertEqual(photo["by"], {"id": ondrej["id"], "displayName": "Ondrej", "role": "staff"})
        self.assertRegex(photo["url"], r"^/photos/[0-9a-f]{32}\.jpg$")
        self.assertEqual(self.photo_path(photo).read_bytes(), JPEG)
        job = [j for j in self.c.get("/api/jobs").json()["jobs"] if j["id"] == 8][0]
        self.assertEqual(job["photos"], [photo])
        got = self.c.get(photo["url"])
        self.assertEqual((got.status_code, got.content), (200, JPEG))
        self.assertEqual(got.headers["cache-control"], "private, max-age=604800, immutable")
        self.login("owen")
        self.assertEqual(self.c.get(photo["url"]).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/jobs/8/photos/{photo['id']}", headers=H).json(), {"ok": True})
        self.assertFalse(self.photo_path(photo).exists())
        self.assertEqual(self.c.get(photo["url"]).status_code, 404)
        self.refused(self.c.delete(f"/api/jobs/8/photos/{photo['id']}", headers=H), 404, "That photo doesn't exist.")

    def test_job_photo_checks_match_visit_photos(self):
        self.login("ondrej")
        self.refused(self.c.post("/api/jobs/1/photos", content=b"GIF89a", headers=PHOTO_HEADERS), 415,
                     "Only JPEG photos are accepted.")
        big = b"\xff\xd8\xff" + b"\x00" * (8 * 1024 * 1024)
        self.refused(self.c.post("/api/jobs/1/photos", content=big, headers=PHOTO_HEADERS), 413,
                     "That photo is too large.")
        self.refused(self.c.post("/api/jobs/1/photos?caption=" + "x" * 301, content=JPEG, headers=PHOTO_HEADERS),
                     400, "'caption' is too long (max 300 characters).")
        self.refused(self.c.post("/api/jobs/999/photos", content=JPEG, headers=PHOTO_HEADERS), 404,
                     "That job doesn't exist.")
        r = self.c.post("/api/jobs/1/photos", content=JPEG, headers={"content-type": "image/jpeg"})
        self.assertEqual(r.status_code, 403)  # no X-HRS header
        self.assertEqual(list(Path(os.environ["HRS_PHOTOS_DIR"]).glob("*.jpg")), [])  # nothing refused was kept

    def test_only_the_uploader_or_an_owner_deletes_a_job_photo(self):
        self.login("owen")
        mine = self.c.post("/api/jobs/1/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.assertEqual(mine["by"]["role"], "owner")
        self.login("ondrej")
        self.refused(self.c.delete(f"/api/jobs/1/photos/{mine['id']}", headers=H), 403,
                     "You can only delete your own photos.")
        theirs = self.c.post("/api/jobs/1/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.assertEqual(self.c.delete(f"/api/jobs/1/photos/{theirs['id']}", headers=H).status_code, 200)
        self.assertTrue(self.photo_path(mine).is_file())

    def test_photos_are_a_404_for_the_wrong_kind_of_person(self):
        self.login("maria")
        visit = self.c.post("/api/visits/2026-09-29/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.login("ondrej")
        job = self.c.post("/api/jobs/1/photos", content=JPEG, headers=PHOTO_HEADERS).json()["photo"]
        self.assertEqual(self.c.get(job["url"]).status_code, 200)
        r = self.c.get(visit["url"])
        self.refused(r, 404, "That photo doesn't exist.")  # 404, not 403: nothing to probe
        self.login("maria")
        self.assertEqual(self.c.get(visit["url"]).status_code, 200)
        self.refused(self.c.get(job["url"]), 404, "That photo doesn't exist.")
        self.login("owen")
        self.assertEqual(self.c.get(visit["url"]).status_code, 200)
        self.assertEqual(self.c.get(job["url"]).status_code, 200)


if __name__ == "__main__":
    unittest.main()
