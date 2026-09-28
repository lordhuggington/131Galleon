"""The menu brief, the rules Claude follows, and validation of a posted prep session.

Menus are planned in the Claude app and posted back through the MCP connector (app/mcp.py).
Nothing in this module calls a model or talks to the network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import store

AISLES = ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"]
SLOTS = ("breakfast", "main", "dessert")
LA = ZoneInfo("America/Los_Angeles")  # present in python:3.12-slim; no tzdata package needed
# Written out rather than taken from strftime("%A"/"%b"), which follows the process locale.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PREP_WEEKDAYS = (1, 4)  # Tuesday, Friday


class MenuError(Exception):
    """A message that goes straight back to Claude as a tool error. One line, no stack trace."""


RULES = """You are writing one batch-prep session for a housekeeper who cooks for one adult in Los Angeles. \
The brief gives you the day, the portion counts, the targets and the household's preferences. Work the \
numbers out in the chat with Owen, then post the finished session with save_session.

- Every single portion - breakfast, main and dessert alike - must hit the brief's kcal target within the \
stated tolerance and reach at least its protein target. The app checks this on save and refuses an \
off-target session.
- Ingredient amounts are for the WHOLE BATCH, in US units with grams in brackets where useful. "kcal" and \
"protein" are for that whole-batch amount of that ingredient, taken from the real product's nutrition \
label or the USDA figure - look them up rather than estimating, and use the exact brand the household buys.
- Before you call save_session, add the ingredients up yourself: total kcal divided by portions, and total \
protein divided by portions, for each of the three recipes. Adjust amounts until both land on target. Never \
post a session you have not added up.
- Quick and simple: the cooking fits in about 2.5 hours for one person with normal home-kitchen equipment. \
Only everyday ingredients found at a normal US supermarket (the brief's "store" if it names one).
- Minimise waste: size quantities to standard US pack sizes and use whole packs where you can; prefer \
frozen or shelf-stable forms for small amounts.
- Use the other session's leftovers first where it makes sense. The brief lists them under "otherSession".
- Do not put anything from the brief's "pantry" list on the shopping list.
- Respect "likes". Never use anything in "dislikes".
- Do not repeat a title from "recentTitles". You may reuse at most one title from "favourites".
- The shopping list is ordered on Amazon Fresh (amazon.com), so word every "item" the way Amazon Fresh \
sells it: brand where it matters and a real pack size, like "Fage Total 0% Greek Yogurt, 32 oz", "Amazon \
Grocery 93/7 Ground Beef, 1 lb" or "Just Bare Chicken Breast Tenderloins, 2 lb". "buy" is how many of that \
pack to order. "search" is a short Amazon Fresh search phrase for that item - brand, product and size, with \
no quantity like "x2" - that lands on the right product.
- "stock" is true only for long-life items that last several weeks (oils, spices, oats, rice, protein \
powder, baking goods). Everything else is false.
- Food safety: any portion eaten more than 3 days after it was cooked must be frozen on prep day and moved \
to the fridge the night before. Say so in that recipe's "storage" and "portionNote".
- Steps are short plain sentences a cook can follow: oven temperatures, times, doneness (chicken to 165F), \
how to split into portions evenly (by weight), and labelling each container with the day to eat it.
- The timeline is 4 to 6 lines ordering the session's work efficiently - things that chill or bake go first.
- "leftovers" is the realistic amounts of anything left over after THIS session, so the next session can \
use them up."""

GET_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "description": "The prep day to plan, a Tuesday or a Friday, as YYYY-MM-DD. Omit for the next one.",
        }
    },
    "required": [],
    "additionalProperties": False,
}


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def today_la() -> date:
    """Owen's today. The container runs UTC, so a UTC date would be a day ahead all evening."""
    return datetime.now(LA).date()


def resolve_prep_date(given: str | None) -> str:
    """The prep day to plan: the next Tuesday or Friday on or after today, or the one asked for."""
    if not given:
        d = today_la()
        while d.weekday() not in PREP_WEEKDAYS:
            d += timedelta(days=1)
        return d.isoformat()
    try:
        d = date.fromisoformat(given)
    except ValueError:
        raise MenuError(f'"{given}" is not a date in YYYY-MM-DD form.')
    if d.weekday() not in PREP_WEEKDAYS:
        raise MenuError(f"{d.isoformat()} is a {WEEKDAYS[d.weekday()]}. "
                        f"Prep sessions are Tuesdays and Fridays — pick one of those.")
    return d.isoformat()


def week_and_session(prep_date: str) -> tuple[str, str]:
    """(Monday of that week, "tue" | "fri") for a prep day."""
    d = date.fromisoformat(prep_date)
    return (d - timedelta(days=d.weekday())).isoformat(), "tue" if d.weekday() == 1 else "fri"


def _titles(session: dict) -> dict[str, str]:
    """{slot: title} for the slots a saved session actually has, in SLOTS order."""
    recipes = session.get("recipes") or {}
    return {slot: str(recipes[slot].get("title") or "") for slot in SLOTS
            if isinstance(recipes.get(slot), dict)}


def brief(conn, date: str | None) -> dict:
    """Everything Claude needs to plan one session. Raises MenuError for a day that is not a prep day."""
    prep = resolve_prep_date(date)
    week, session = week_and_session(prep)
    other = "fri" if session == "tue" else "tue"
    settings = store.get_settings(conn)
    rows = conn.execute("SELECT week, data FROM meal_plans ORDER BY week DESC").fetchall()
    earlier = {r["week"] for r in rows if r["week"] < week}
    earlier = set(sorted(earlier, reverse=True)[:6])  # the six most recent saved weeks before this one
    recent: list[str] = []
    favourites: list[str] = []
    this_week: dict = {}
    for r in rows:  # newest week first
        d = json.loads(r["data"])
        if r["week"] == week:
            this_week = d
        for s in (d.get("sessions") or {}).values():
            for rec in (s.get("recipes") or {}).values():
                title = str((rec or {}).get("title") or "")
                if not title:
                    continue
                if r["week"] in earlier and title not in recent:
                    recent.append(title)
                if rec.get("fav") and title not in favourites:
                    favourites.append(title)
    sessions = this_week.get("sessions") or {}
    mine, theirs = sessions.get(session), sessions.get(other)
    existing = None
    if isinstance(mine, dict) and _titles(mine):
        existing = {"titles": _titles(mine)}
    other_session = None
    if isinstance(theirs, dict) and _titles(theirs):
        other_session = {"session": other, "date": add_days(week, 1 if other == "tue" else 4),
                         "titles": _titles(theirs),
                         "leftovers": [str(x) for x in (theirs.get("leftovers") or [])]}
    return {
        "week": week, "session": session, "date": prep,
        "covers": settings[session]["covers"],
        "portions": {slot: int(settings[session][slot]) for slot in SLOTS},
        "targets": {"kcal": int(settings["kcal"]), "protein": int(settings["protein"]),
                    "tolerance": {"kcalPercent": 7, "proteinBelow": 3}},
        "store": settings.get("store") or "", "likes": settings.get("likes") or "",
        "dislikes": settings.get("dislikes") or "", "pantry": settings.get("pantry") or "",
        "existing": existing, "otherSession": other_session,
        "recentTitles": recent, "favourites": favourites, "rules": RULES,
    }
