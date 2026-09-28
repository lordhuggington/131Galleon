"""The menu brief, the rules Claude follows, and validation of a posted prep session.

Menus are planned in the Claude app and posted back through the MCP connector (app/mcp.py).
Nothing in this module calls a model or talks to the network.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import store
from .config import get_config

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
        # fromisoformat also reads "20260929" and "2026-W40-2"; only the canonical form is a plan key.
        if d.isoformat() != given:
            raise ValueError(given)
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


SAVE_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "week": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                 "description": "Monday of the week, from get_brief."},
        "session": {"type": "string", "enum": ["tue", "fri"]},
        "recipes": {
            "type": "object",
            "properties": {"breakfast": {"$ref": "#/$defs/recipe"},
                           "main": {"$ref": "#/$defs/recipe"},
                           "dessert": {"$ref": "#/$defs/recipe"}},
            "required": ["breakfast", "main", "dessert"],
            "additionalProperties": False,
        },
        "timeline": {"type": "array", "items": {"type": "string"},
                     "description": "4 to 6 lines ordering the session's work."},
        "shopping": {"type": "array", "items": {"$ref": "#/$defs/item"}},
        "leftovers": {"type": "array", "items": {"type": "string"},
                      "description": "Realistic amounts left over after this session."},
    },
    "required": ["week", "session", "recipes", "timeline", "shopping", "leftovers"],
    "additionalProperties": False,
    "$defs": {
        "recipe": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "blurb": {"type": "string", "description": "One short line."},
                "portions": {"type": "integer", "minimum": 1},
                "portionNote": {"type": "string",
                                "description": 'e.g. "9 containers: 3 a day for Wed, Thu, Fri".'},
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "amount": {"type": "string",
                                       "description": "WHOLE BATCH amount, US units with grams in brackets."},
                            "kcal": {"type": "number", "minimum": 0,
                                     "description": "kcal in that whole-batch amount."},
                            "protein": {"type": "number", "minimum": 0,
                                        "description": "grams of protein in that whole-batch amount."},
                        },
                        "required": ["item", "amount", "kcal", "protein"],
                        "additionalProperties": False,
                    },
                },
                "steps": {"type": "array", "items": {"type": "string"}},
                "storage": {"type": "string"},
            },
            "required": ["title", "blurb", "portions", "portionNote", "ingredients", "steps", "storage"],
            "additionalProperties": False,
        },
        "item": {
            "type": "object",
            "properties": {
                "item": {"type": "string",
                         "description": "Worded the way Amazon Fresh sells it, with a real pack size."},
                "buy": {"type": "string", "description": 'How many of that pack to order, e.g. "2 bags".'},
                "aisle": {"type": "string", "enum": list(AISLES)},
                "stock": {"type": "boolean", "description": "true only for long-life items."},
                "search": {"type": "string", "description": "Short Amazon Fresh search phrase, no quantity."},
            },
            "required": ["item", "buy", "aisle", "stock", "search"],
            "additionalProperties": False,
        },
    },
}

SHAPE_PROBLEM = "save_session needs an object with week, session, recipes, timeline, shopping and leftovers."
# Caps: long enough for a real session, short enough that one call can't fill the database.
MAX_TITLE, MAX_BLURB, MAX_NOTE, MAX_STORAGE = 120, 300, 300, 500
MAX_INGREDIENTS, MAX_STEPS, MAX_STEP = 40, 30, 500
MAX_ING_ITEM, MAX_ING_AMOUNT = 120, 120
MAX_TIMELINE, MAX_TIMELINE_LINE = 12, 300
MAX_SHOPPING, MAX_SHOP_ITEM, MAX_SHOP_BUY, MAX_SHOP_SEARCH = 60, 160, 60, 160
MAX_LEFTOVERS, MAX_LEFTOVER_LINE = 20, 160


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float:
    # JSON allows Infinity, NaN and integers far too long for a float; none of those may reach a round().
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _round(x: float) -> int:
    """JavaScript's Math.round (half up), so an error line and the Meals pill can never disagree."""
    return math.floor(x + 0.5)


def normalize_session(args) -> tuple[dict, list[dict]]:
    """Coerce Claude's arguments into the shapes the app stores, and number the shopping list.

    Never truncates: validate_session reports anything too long so Claude can shorten it itself.
    Returns (normalized, normalized["shopping"]) — the second value goes straight to store.save_session.
    """
    if not isinstance(args, dict) or not isinstance(args.get("recipes"), dict) \
            or not isinstance(args.get("timeline"), list) or not isinstance(args.get("shopping"), list) \
            or not isinstance(args.get("leftovers"), list):
        raise MenuError(SHAPE_PROBLEM)
    session = _text(args.get("session"))
    recipes: dict[str, dict] = {}
    for slot in SLOTS:
        r = args["recipes"].get(slot)
        if not isinstance(r, dict):
            continue  # validate_session reports "recipes: <slot> is missing."
        try:
            # float() first, so Infinity, NaN and a 400-digit integer all land in the except.
            portions = int(float(r.get("portions") or 0))
        except (TypeError, ValueError, OverflowError):
            portions = 0
        raw_ings = r.get("ingredients") if isinstance(r.get("ingredients"), list) else []
        raw_steps = r.get("steps") if isinstance(r.get("steps"), list) else []
        recipes[slot] = {
            "title": _text(r.get("title")), "blurb": _text(r.get("blurb")), "portions": portions,
            "portionNote": _text(r.get("portionNote")), "storage": _text(r.get("storage")),
            "ingredients": [{"item": _text(i.get("item")), "amount": _text(i.get("amount")),
                             "kcal": _num(i.get("kcal")), "protein": _num(i.get("protein"))}
                            for i in raw_ings if isinstance(i, dict)],
            "steps": [_text(x) for x in raw_steps],
            "fav": False,  # replacing a session resets its stars; the recipes are new
        }
    prefix = "f" if session == "fri" else "t"  # t01…/f01… — two digits is enough at a 60-item cap
    shopping = []
    for n, i in enumerate(x for x in args["shopping"] if isinstance(x, dict)):
        shopping.append({"id": f"{prefix}{n + 1:02d}", "item": _text(i.get("item")), "buy": _text(i.get("buy")),
                         "aisle": i["aisle"] if i.get("aisle") in AISLES else "Pantry",
                         "stock": bool(i.get("stock")), "search": _text(i.get("search"))})
    normalized = {"week": _text(args.get("week")), "session": session, "recipes": recipes,
                  "timeline": [_text(x) for x in args["timeline"]], "shopping": shopping,
                  "leftovers": [_text(x) for x in args["leftovers"]]}
    return normalized, shopping


def validate_session(normalized: dict, settings: dict) -> list[str]:
    """Every problem with a posted session, one line each, in field order. Empty list means save it."""
    problems: list[str] = []
    week = normalized["week"]
    try:
        d = date.fromisoformat(week)
        # fromisoformat also reads "20261005" and "2026-W41-1"; get_plan would never find those keys.
        if d.isoformat() != week:
            raise ValueError(week)
    except ValueError:
        problems.append(f'week: "{week}" is not a date in YYYY-MM-DD form.')
    else:
        if d.weekday() != 0:
            monday = (d - timedelta(days=d.weekday())).isoformat()
            problems.append(f"week: {week} is a {WEEKDAYS[d.weekday()]}; "
                            f"a week is identified by its Monday ({monday}).")
    if normalized["session"] not in ("tue", "fri"):
        problems.append('session: must be "tue" or "fri".')
    t_kcal, t_protein = int(settings["kcal"]), int(settings["protein"])
    for slot in SLOTS:
        r = normalized["recipes"].get(slot)
        if r is None:
            problems.append(f"recipes: {slot} is missing.")
            continue
        if not r["title"]:
            problems.append(f"{slot}: title is empty.")
        elif len(r["title"]) > MAX_TITLE:
            problems.append(f"{slot}: title is too long ({MAX_TITLE} characters max).")
        if len(r["blurb"]) > MAX_BLURB:
            problems.append(f"{slot}: blurb is too long ({MAX_BLURB} characters max).")
        if len(r["portionNote"]) > MAX_NOTE:
            problems.append(f"{slot}: portionNote is too long ({MAX_NOTE} characters max).")
        if len(r["storage"]) > MAX_STORAGE:
            problems.append(f"{slot}: storage is too long ({MAX_STORAGE} characters max).")
        if r["portions"] < 1:
            problems.append(f"{slot}: portions must be 1 or more.")
        if not r["ingredients"]:
            problems.append(f"{slot}: needs at least one ingredient.")
        elif len(r["ingredients"]) > MAX_INGREDIENTS:
            problems.append(f"{slot}: too many ingredients ({MAX_INGREDIENTS} max).")
        if not r["steps"]:
            problems.append(f"{slot}: needs at least one step.")
        elif len(r["steps"]) > MAX_STEPS:
            problems.append(f"{slot}: too many steps ({MAX_STEPS} max).")
        # Per-item checks stop at the cap: one "too many" line beats two hundred "too long" ones.
        for n, i in enumerate(r["ingredients"][:MAX_INGREDIENTS], start=1):
            if len(i["item"]) > MAX_ING_ITEM:
                problems.append(f"{slot} ingredient {n}: item is too long ({MAX_ING_ITEM} characters max).")
            if len(i["amount"]) > MAX_ING_AMOUNT:
                problems.append(f"{slot} ingredient {n}: amount is too long ({MAX_ING_AMOUNT} characters max).")
            if i["kcal"] < 0:
                problems.append(f"{slot} ingredient {n}: kcal must be a number of 0 or more.")
            if i["protein"] < 0:
                problems.append(f"{slot} ingredient {n}: protein must be a number of 0 or more.")
        for n, step in enumerate(r["steps"][:MAX_STEPS], start=1):
            if len(step) > MAX_STEP:
                problems.append(f"{slot} step {n}: too long ({MAX_STEP} characters max).")
        if r["ingredients"] and r["portions"] >= 1:
            # Identical to frontend/src/lib/macros.ts: macroStatus. Both sides are rounded to whole
            # numbers for the comparison and for the message — by _round, which rounds half up the way
            # Math.round does — so an error line and the Meals pill can never disagree.
            kcal_pp = _round(sum(i["kcal"] for i in r["ingredients"]) / r["portions"])
            protein_pp = _round(sum(i["protein"] for i in r["ingredients"]) / r["portions"])
            if abs(kcal_pp - t_kcal) > t_kcal * 0.07:
                problems.append(f"{slot}: {kcal_pp} kcal per portion, target {t_kcal} ±{round(t_kcal * 0.07)}.")
            if protein_pp < t_protein - 3:
                problems.append(f"{slot}: {protein_pp} g protein per portion, need at least {t_protein - 3}.")
    if len(normalized["timeline"]) > MAX_TIMELINE:
        problems.append(f"timeline: too many lines ({MAX_TIMELINE} max).")
    for n, line in enumerate(normalized["timeline"][:MAX_TIMELINE], start=1):
        if len(line) > MAX_TIMELINE_LINE:
            problems.append(f"timeline line {n}: too long ({MAX_TIMELINE_LINE} characters max).")
    if len(normalized["shopping"]) > MAX_SHOPPING:
        problems.append(f"shopping: too many items ({MAX_SHOPPING} max).")
    for n, i in enumerate(normalized["shopping"][:MAX_SHOPPING], start=1):
        if len(i["item"]) > MAX_SHOP_ITEM:
            problems.append(f"shopping item {n}: item is too long ({MAX_SHOP_ITEM} characters max).")
        if len(i["buy"]) > MAX_SHOP_BUY:
            problems.append(f"shopping item {n}: buy is too long ({MAX_SHOP_BUY} characters max).")
        if len(i["search"]) > MAX_SHOP_SEARCH:
            problems.append(f"shopping item {n}: search is too long ({MAX_SHOP_SEARCH} characters max).")
    if len(normalized["leftovers"]) > MAX_LEFTOVERS:
        problems.append(f"leftovers: too many lines ({MAX_LEFTOVERS} max).")
    for n, line in enumerate(normalized["leftovers"][:MAX_LEFTOVERS], start=1):
        if len(line) > MAX_LEFTOVER_LINE:
            problems.append(f"leftovers line {n}: too long ({MAX_LEFTOVER_LINE} characters max).")
    return problems


def problem_report(problems: list[str]) -> str:
    """What save_session says when it refuses. Nothing is saved when this is returned."""
    return "Nothing was saved. Fix these and call save_session again:\n" + "\n".join(problems)


def session_data(normalized: dict, settings: dict) -> dict:
    """The JSON stored at meal_plans.data["sessions"][session]. Only call this once it validates."""
    week, session = normalized["week"], normalized["session"]
    return {"date": add_days(week, 1 if session == "tue" else 4),
            "covers": settings[session]["covers"],
            "recipes": {slot: dict(normalized["recipes"][slot]) for slot in SLOTS},
            "timeline": list(normalized["timeline"]),
            "leftovers": list(normalized["leftovers"])}


def save_result(week: str, session: str, data: dict, shopping: list[dict]) -> tuple[str, dict]:
    """(summary text, structuredContent) for a saved session."""
    url = f"{get_config().public_url}/#meals"
    d = date.fromisoformat(data["date"])
    w = date.fromisoformat(week)
    recipes, parts = {}, []
    for slot in SLOTS:
        r = data["recipes"][slot]
        kcal = _round(sum(i["kcal"] for i in r["ingredients"]) / r["portions"])
        protein = _round(sum(i["protein"] for i in r["ingredients"]) / r["portions"])
        recipes[slot] = {"title": r["title"], "portions": r["portions"],
                         "kcalPerPortion": kcal, "proteinPerPortion": protein}
        parts.append(f"{r['title']} ({r['portions']} × {kcal} kcal / {protein} g)")
    text = (f"Saved {WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} "
            f"(week of {w.day} {MONTHS[w.month - 1]}): {', '.join(parts)}. "
            f"{len(shopping)} shopping items. Open {url}")
    structured = {"week": week, "session": session, "date": data["date"], "recipes": recipes,
                  "shoppingCount": len(shopping), "url": url}
    return text, structured
