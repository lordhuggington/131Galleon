"""The brief, the prep-day calendar and session validation. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ENV_KEYS = ("HRS_DB_PATH", "HRS_PUBLIC_URL")


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "menu.db")
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
        from app import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["import-seed", str(ROOT / "seed")])  # migrates, then loads seed/plans/2026-09-28.json
        from app.db import connect
        self.conn = connect()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write_plan(self, week: str, titles: dict[str, dict[str, str]], leftovers: list[str] | None = None) -> None:
        """Write a minimal saved week straight into meal_plans. titles = {"tue": {"main": "Chilli"}}."""
        from app import menu
        from app.db import now_iso
        sessions = {
            session: {"date": menu.add_days(week, 1 if session == "tue" else 4), "covers": "", "timeline": [],
                      "leftovers": list(leftovers or []),
                      "recipes": {slot: {"title": t, "portions": 1, "ingredients": [], "steps": [], "fav": False}
                                  for slot, t in slot_titles.items()}}
            for session, slot_titles in titles.items()}
        self.conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at) VALUES (?, ?, 'test', '', ?)",
                          (week, json.dumps({"sessions": sessions}), now_iso()))

    # ---- the prep-day calendar ----
    def test_resolve_prep_date_finds_the_next_tuesday_or_friday(self):
        from app import menu
        cases = [(date(2026, 9, 28), "2026-09-29", "2026-09-28", "tue"),   # Monday -> that Tuesday
                 (date(2026, 9, 29), "2026-09-29", "2026-09-28", "tue"),   # Tuesday -> today counts
                 (date(2026, 9, 30), "2026-10-02", "2026-09-28", "fri"),   # Wednesday -> that Friday
                 (date(2026, 10, 3), "2026-10-06", "2026-10-05", "tue")]   # Saturday -> the next Tuesday
        for today, prep, week, session in cases:
            with self.subTest(today=today.isoformat()), mock.patch("app.menu.today_la", return_value=today):
                self.assertEqual(menu.resolve_prep_date(None), prep)
                self.assertEqual(menu.week_and_session(prep), (week, session))

    def test_resolve_prep_date_keeps_a_tuesday_or_friday_it_is_given(self):
        from app import menu
        with mock.patch("app.menu.today_la", return_value=date(2026, 9, 28)):
            self.assertEqual(menu.resolve_prep_date("2026-10-16"), "2026-10-16")  # a Friday three weeks out

    def test_resolve_prep_date_rejects_a_day_that_is_not_a_prep_day(self):
        from app import menu
        with self.assertRaises(menu.MenuError) as cm:
            menu.resolve_prep_date("2026-09-30")
        self.assertEqual(str(cm.exception),
                         "2026-09-30 is a Wednesday. Prep sessions are Tuesdays and Fridays — pick one of those.")

    def test_resolve_prep_date_rejects_a_non_date(self):
        from app import menu
        with self.assertRaises(menu.MenuError) as cm:
            menu.resolve_prep_date("2026-13-01")
        self.assertEqual(str(cm.exception), '"2026-13-01" is not a date in YYYY-MM-DD form.')

    # ---- the brief ----
    def test_brief_reads_the_settings_and_the_date(self):
        from app import menu
        with mock.patch("app.menu.today_la", return_value=date(2026, 9, 28)):
            b = menu.brief(self.conn, None)
        self.assertEqual(b["week"], "2026-09-28")
        self.assertEqual(b["session"], "tue")
        self.assertEqual(b["date"], "2026-09-29")
        self.assertEqual(b["covers"], "Wed, Thu, Fri")
        self.assertEqual(b["portions"], {"breakfast": 3, "main": 9, "dessert": 3})
        self.assertEqual(b["targets"], {"kcal": 500, "protein": 50,
                                        "tolerance": {"kcalPercent": 7, "proteinBelow": 3}})
        self.assertEqual(b["store"], "")
        self.assertEqual(b["likes"], "")
        self.assertEqual(b["dislikes"], "")
        self.assertEqual(b["pantry"], "")
        self.assertEqual(b["rules"], menu.RULES)

    def test_brief_reports_the_saved_session_and_the_other_one(self):
        from app import menu
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(b["existing"]["titles"]["main"], "Chipotle chicken burrito bowls")
        self.assertEqual(list(b["existing"]["titles"]), ["breakfast", "main", "dessert"])
        self.assertEqual(b["otherSession"]["session"], "fri")
        self.assertEqual(b["otherSession"]["date"], "2026-10-02")
        self.assertEqual(b["otherSession"]["titles"]["breakfast"],
                         "Chocolate peanut butter banana overnight oats")
        self.assertIn("About 170 g Greek yogurt", b["otherSession"]["leftovers"])
        # a week nothing is saved for has neither
        empty = menu.brief(self.conn, "2026-10-06")
        self.assertIsNone(empty["existing"])
        self.assertIsNone(empty["otherSession"])

    def test_brief_lists_six_earlier_weeks_of_titles(self):
        from app import menu
        for week in ("2026-09-21", "2026-09-14", "2026-09-07", "2026-08-31", "2026-08-24",
                     "2026-08-17", "2026-08-10"):
            self.write_plan(week, {"tue": {"main": f"Main {week}"}})
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(len(b["recentTitles"]), 6)                      # six weeks, newest first
        self.assertEqual(b["recentTitles"][0], "Main 2026-09-21")
        self.assertEqual(b["recentTitles"][-1], "Main 2026-08-17")
        self.assertNotIn("Main 2026-08-10", b["recentTitles"])           # the seventh week is dropped
        self.assertNotIn("Chipotle chicken burrito bowls", b["recentTitles"])  # its own week never counts

    def test_brief_lists_favourites(self):
        from app import store
        from app import menu
        from app.db import tx
        with tx(self.conn):
            store.set_recipe_fav(self.conn, "2026-09-28", "tue", "main", True)
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(b["favourites"], ["Chipotle chicken burrito bowls"])


if __name__ == "__main__":
    unittest.main()
