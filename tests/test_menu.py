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

    def recipe(self, title: str, portions: int, kcal: float, protein: float) -> dict:
        """One recipe whose whole-batch numbers divide exactly into kcal and protein per portion."""
        return {"title": title, "blurb": "One short line.", "portions": portions,
                "portionNote": f"{portions} containers, one a day.",
                "ingredients": [{"item": "Everything", "amount": "1 batch (1000 g)",
                                 "kcal": kcal * portions, "protein": protein * portions}],
                "steps": ["Cook it.", "Portion it out by weight."], "storage": "Fridge for 3 days."}

    def payload(self, **overrides) -> dict:
        """A valid save_session argument object. Override any top-level key."""
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

    def seed_payload(self, session: str = "tue") -> dict:
        """A save_session argument object built from the real seed week, so it is realistic prose."""
        seed = json.loads((ROOT / "seed" / "plans" / "2026-09-28.json").read_text())
        s = seed["sessions"][session]
        return {
            "week": "2026-09-28", "session": session,
            "recipes": {slot: {"title": r["title"], "blurb": r["blurb"], "portions": r["portions"],
                               "portionNote": r["portionNote"], "storage": r["storage"], "steps": r["steps"],
                               "ingredients": [{"item": i["item"], "amount": i["amount"],
                                                "kcal": i["kcal"], "protein": i["protein"]}
                                               for i in r["ingredients"]]}
                        for slot, r in s["recipes"].items()},
            "timeline": s["timeline"],
            "shopping": [{"item": i["item"], "buy": i["buy"], "aisle": i["aisle"], "stock": i["stock"],
                          "search": i.get("search", "")}
                         for i in seed["shopping"] if i["for"] in (session, "both")],
            "leftovers": ["About 170 g Greek yogurt"],
        }

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
        # Python accepts "20260929" and "2026-W40-2"; only the canonical form is a week or prep key.
        with self.assertRaises(menu.MenuError) as cm:
            menu.resolve_prep_date("20260929")
        self.assertEqual(str(cm.exception), '"20260929" is not a date in YYYY-MM-DD form.')

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

    # ---- normalization ----
    def test_normalize_trims_coerces_and_numbers_the_shopping_list(self):
        from app import menu, store
        args = self.payload(session="fri", shopping=[
            {"item": "  Fage Total 0% Greek Yogurt, 32 oz  ", "buy": " 1 ", "aisle": "Deli counter",
             "stock": "yes", "search": " Fage Total 0% 32 oz "},
            {"item": "Old-fashioned oats", "buy": "1 bag", "aisle": "Pantry", "stock": True, "search": "oats"}])
        args["recipes"]["main"]["portions"] = "9"          # a string, the way a sloppy client might send it
        normalized, shopping = menu.normalize_session(args)
        self.assertIs(shopping, normalized["shopping"])    # the same list, ready for store.save_session
        self.assertEqual([i["id"] for i in shopping], ["f01", "f02"])  # f… for Friday, t… for Tuesday
        self.assertEqual(shopping[0]["item"], "Fage Total 0% Greek Yogurt, 32 oz")
        self.assertEqual(shopping[0]["buy"], "1")
        self.assertEqual(shopping[0]["aisle"], "Pantry")   # an unknown aisle is coerced, not refused
        self.assertIs(shopping[0]["stock"], True)
        self.assertEqual(shopping[0]["search"], "Fage Total 0% 32 oz")
        self.assertEqual(normalized["recipes"]["main"]["portions"], 9)
        self.assertIs(normalized["recipes"]["main"]["fav"], False)
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])

    def test_normalize_rejects_a_wrong_shape(self):
        from app import menu
        expected = "save_session needs an object with week, session, recipes, timeline, shopping and leftovers."
        for bad in ("not an object", self.payload(recipes=[]), self.payload(timeline="four lines"),
                    self.payload(shopping={}), self.payload(leftovers=None)):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(menu.MenuError) as cm:
                    menu.normalize_session(bad)
                self.assertEqual(str(cm.exception), expected)

    # ---- validation ----
    def test_a_good_session_from_the_seed_has_no_problems(self):
        from app import menu, store
        normalized, _ = menu.normalize_session(self.seed_payload("tue"))
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])
        normalized, _ = menu.normalize_session(self.seed_payload("fri"))
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])

    def test_off_target_kcal_and_protein_are_reported(self):
        from app import menu, store
        args = self.payload(recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                     "main": self.recipe("Burritos", 9, 612, 51),
                                     "dessert": self.recipe("Pots", 3, 500, 44)})
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        self.assertEqual(problems, ["main: 612 kcal per portion, target 500 ±35.",
                                    "dessert: 44 g protein per portion, need at least 47."])

    def test_a_protein_mean_of_exactly_46_5_rounds_up_like_the_meals_pill(self):
        """Math.round sends 46.5 up to 47, so the pill is green; Python's round() would say 46 and redden it."""
        from app import menu, store
        args = self.payload(recipes={"breakfast": self.recipe("Half-batch oats", 2, 500, 46.5),
                                     "main": self.recipe("Beef burritos", 9, 500, 51),
                                     "dessert": self.recipe("Chocolate overnight oats", 3, 500, 49)})
        self.assertEqual(args["recipes"]["breakfast"]["ingredients"][0]["protein"], 93.0)  # 93 g over 2 portions
        normalized, _ = menu.normalize_session(args)
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])

    def test_missing_and_empty_fields_are_reported(self):
        from app import menu, store
        args = self.payload()
        del args["recipes"]["dessert"]
        args["recipes"]["breakfast"]["title"] = "   "
        args["recipes"]["main"]["portions"] = 0
        args["recipes"]["main"]["ingredients"] = []
        args["recipes"]["main"]["steps"] = []
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        for line in ("recipes: dessert is missing.", "breakfast: title is empty.",
                     "main: portions must be 1 or more.", "main: needs at least one ingredient.",
                     "main: needs at least one step."):
            self.assertIn(line, problems)

    def test_length_caps_are_reported(self):
        from app import menu, store
        args = self.payload(
            timeline=[f"Line {n}." for n in range(13)],
            # the 61st item is over the per-item cap too, so the slice below is doing real work
            shopping=[{"item": "x" * 200 if n == 60 else f"Item {n}", "buy": "1", "aisle": "Pantry",
                       "stock": False, "search": f"item {n}"}
                      for n in range(61)],
            leftovers=[f"About {n} g of something" for n in range(21)])
        args["recipes"]["main"]["title"] = "B" * 121
        args["recipes"]["dessert"]["portions"] = 41
        args["recipes"]["dessert"]["ingredients"] = [{"item": "Thing", "amount": "1 g", "kcal": 500, "protein": 51}
                                                    for _ in range(41)]
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        for line in ("main: title is too long (120 characters max).",
                     "dessert: too many ingredients (40 max).",
                     "timeline: too many lines (12 max).",
                     "shopping: too many items (60 max).",
                     "leftovers: too many lines (20 max)."):
            self.assertIn(line, problems)
        # one "too many" line, not one per item over the cap
        self.assertEqual(len([p for p in problems if p.startswith("shopping item ")]), 0)

    def test_hostile_numbers_become_problem_lines_not_crashes(self):
        """json.loads accepts Infinity, NaN, 1e400 and 400-digit integers; none may escape as an exception (R16)."""
        from app import menu, store
        settings = store.get_settings(self.conn)
        kcal_line = "breakfast: 0 kcal per portion, target 500 ±35."       # the ingredient contributes 0.0 kcal
        portions_line = "breakfast: portions must be 1 or more."           # an unusable count falls back to 0
        huge = "9" * 400                                                   # a valid JSON integer, far beyond a float
        cases = [("kcal", "Infinity", kcal_line), ("kcal", "NaN", kcal_line), ("kcal", "1e400", kcal_line),
                 ("kcal", '"Infinity"', kcal_line), ("kcal", huge, kcal_line),
                 ("portions", "Infinity", portions_line), ("portions", huge, portions_line)]
        for field, literal, expected in cases:
            with self.subTest(field=field, value=literal[:20]):
                args = self.payload()
                if field == "kcal":
                    args["recipes"]["breakfast"]["ingredients"][0]["kcal"] = -12345
                else:
                    args["recipes"]["breakfast"]["portions"] = -12345
                # Patch the JSON text, then parse it, so this runs the wire path an MCP request really takes.
                wire = json.dumps(args).replace("-12345", literal)
                normalized, _ = menu.normalize_session(json.loads(wire))
                problems = menu.validate_session(normalized, settings)
                self.assertIsInstance(problems, list)
                self.assertEqual(problems, [expected])

    def test_a_week_that_is_not_a_monday_is_reported(self):
        from app import menu, store
        normalized, _ = menu.normalize_session(self.payload(week="2026-09-30"))
        self.assertIn("week: 2026-09-30 is a Wednesday; a week is identified by its Monday (2026-09-28).",
                      menu.validate_session(normalized, store.get_settings(self.conn)))
        normalized, _ = menu.normalize_session(self.payload(week="2026-13-01"))
        self.assertIn('week: "2026-13-01" is not a date in YYYY-MM-DD form.',
                      menu.validate_session(normalized, store.get_settings(self.conn)))
        # Python reads "20261005" as a date, but get_plan would never find that week key.
        normalized, _ = menu.normalize_session(self.payload(week="20261005"))
        self.assertIn('week: "20261005" is not a date in YYYY-MM-DD form.',
                      menu.validate_session(normalized, store.get_settings(self.conn)))

    def test_several_problems_are_listed_under_one_heading(self):
        from app import menu, store
        args = self.payload(week="2026-09-30", session="wed",
                            recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                     "main": self.recipe("Burritos", 9, 612, 51),
                                     "dessert": self.recipe("Pots", 3, 500, 44)})
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        self.assertEqual(menu.problem_report(problems), (
            "Nothing was saved. Fix these and call save_session again:\n"
            "week: 2026-09-30 is a Wednesday; a week is identified by its Monday (2026-09-28).\n"
            'session: must be "tue" or "fri".\n'
            "main: 612 kcal per portion, target 500 ±35.\n"
            "dessert: 44 g protein per portion, need at least 47."))

    # ---- saving ----
    def test_save_session_leaves_the_other_session_alone(self):
        from app import menu, store
        from app.db import tx
        settings = store.get_settings(self.conn)
        week = "2026-10-05"
        fri = self.payload(week=week, session="fri",
                           recipes={"breakfast": self.recipe("Berry oats", 4, 500, 52),
                                    "main": self.recipe("Beef chilli", 12, 500, 51),
                                    "dessert": self.recipe("Cheesecake pots", 4, 500, 49)},
                           leftovers=["About 80 g Parmesan"])
        normalized, shopping = menu.normalize_session(fri)
        self.assertEqual(menu.validate_session(normalized, settings), [])
        with tx(self.conn):
            store.save_session(self.conn, week, "fri", menu.session_data(normalized, settings), shopping, None)
        self.assertEqual([i["id"] for i in shopping], ["f01", "f02"])
        self.conn.execute("UPDATE shopping_items SET got = 1 WHERE week = ? AND id = 'f01'", (week,))

        tue, tue_shopping = menu.normalize_session(self.payload(week=week))
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(tue, settings), tue_shopping, None)

        plan = store.get_plan(self.conn, week, include_shopping=True)
        self.assertEqual(sorted(plan["sessions"]), ["fri", "tue"])
        self.assertEqual(plan["sessions"]["fri"]["recipes"]["main"]["title"], "Beef chilli")
        self.assertEqual(plan["sessions"]["fri"]["leftovers"], ["About 80 g Parmesan"])
        self.assertEqual(plan["sessions"]["fri"]["covers"], "Sat, Sun, Mon, Tue")
        self.assertEqual(plan["sessions"]["fri"]["date"], "2026-10-09")
        self.assertEqual(plan["sessions"]["tue"]["date"], "2026-10-06")
        self.assertEqual({i["id"] for i in plan["shopping"]}, {"f01", "f02", "t01", "t02"})
        self.assertTrue(plan["got"].get("f01"))     # Friday's tick survived Tuesday's save
        self.assertFalse(plan["got"].get("t01"))    # new rows start unticked
        self.assertEqual(plan["source"], "Claude")

    def test_saving_a_session_twice_replaces_it_and_clears_the_stars(self):
        from app import menu, store
        from app.db import tx
        settings = store.get_settings(self.conn)
        week = "2026-10-05"
        first, first_shopping = menu.normalize_session(self.payload(week=week))
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(first, settings), first_shopping, None)
        with tx(self.conn):
            self.assertTrue(store.set_recipe_fav(self.conn, week, "tue", "main", True))
        again = self.payload(week=week,
                             recipes={"breakfast": self.recipe("Peach oats", 3, 500, 52),
                                      "main": self.recipe("Chicken bowls", 9, 500, 51),
                                      "dessert": self.recipe("Brownie pots", 3, 500, 49)},
                             shopping=[{"item": "Just Bare Chicken Breast Tenderloins, 2 lb", "buy": "1 bag",
                                        "aisle": "Meat", "stock": False,
                                        "search": "Just Bare chicken breast tenderloins 2 lb"}])
        second, second_shopping = menu.normalize_session(again)
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(second, settings), second_shopping, None)
        plan = store.get_plan(self.conn, week, include_shopping=True)
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["main"]["title"], "Chicken bowls")
        self.assertIs(plan["sessions"]["tue"]["recipes"]["main"]["fav"], False)  # new recipes, no old stars
        self.assertEqual([i["id"] for i in plan["shopping"]], ["t01"])

    def test_save_session_clears_a_pre_connector_shared_item(self):
        """Seed weeks mark shared items for: "both"; re-planning either session drops them (ruling R1)."""
        from app import menu, store
        from app.db import tx
        settings = store.get_settings(self.conn)
        before = store.get_plan(self.conn, "2026-09-28", include_shopping=True)["shopping"]
        self.assertTrue(any(i["for"] == "both" for i in before))
        fri_ids = {i["id"] for i in before if i["for"] == "fri"}
        normalized, shopping = menu.normalize_session(self.payload(week="2026-09-28"))
        with tx(self.conn):
            store.save_session(self.conn, "2026-09-28", "tue", menu.session_data(normalized, settings), shopping, None)
        after = store.get_plan(self.conn, "2026-09-28", include_shopping=True)["shopping"]
        self.assertEqual({i["id"] for i in after}, fri_ids | {"t01", "t02"})
        self.assertFalse(any(i["for"] == "both" for i in after))

    def test_save_result_summarises_the_session(self):
        from app import menu, store
        settings = store.get_settings(self.conn)
        normalized, shopping = menu.normalize_session(self.payload(week="2026-09-28"))
        text, structured = menu.save_result("2026-09-28", "tue", menu.session_data(normalized, settings), shopping)
        self.assertEqual(text, "Saved Tuesday 29 Sep (week of 28 Sep): Vanilla blueberry overnight oats "
                               "(3 × 500 kcal / 52 g), Beef burritos (9 × 500 kcal / 51 g), "
                               "Chocolate overnight oats (3 × 500 kcal / 49 g). 2 shopping items. "
                               "Open http://testserver/#meals")
        self.assertEqual(structured, {
            "week": "2026-09-28", "session": "tue", "date": "2026-09-29",
            "recipes": {"breakfast": {"title": "Vanilla blueberry overnight oats", "portions": 3,
                                      "kcalPerPortion": 500, "proteinPerPortion": 52},
                        "main": {"title": "Beef burritos", "portions": 9,
                                 "kcalPerPortion": 500, "proteinPerPortion": 51},
                        "dessert": {"title": "Chocolate overnight oats", "portions": 3,
                                    "kcalPerPortion": 500, "proteinPerPortion": 49}},
            "shoppingCount": 2, "url": "http://testserver/#meals"})


if __name__ == "__main__":
    unittest.main()
