"""Weekly menu generation with the Claude API, run as a background job."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, timedelta
from typing import Callable

import httpx

from .config import get_config
from .db import connect, now_iso, tx
from . import store

AISLES = ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"]
SLOTS = ("breakfast", "main", "dessert")
API_URL = "https://api.anthropic.com/v1/messages"


class GenerationError(Exception):
    """An error whose message is safe to show to the owner."""


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def build_prompt(settings: dict, week: str, recent_titles: list[str], favourites: list[str],
                 leftovers: list[str], note: str) -> str:
    st = settings
    tue, fri = add_days(week, 1), add_days(week, 4)
    lst = lambda a: "; ".join(a) if a else "none"
    store_txt = f" ({st['store']})" if st.get("store") else ""
    return f"""Plan one week of batch meal prep for a housekeeper who cooks for one adult in Los Angeles on Tuesdays and Fridays. Reply with only the JSON described at the end.

TARGETS: every single portion (breakfast, main and dessert alike) must be {st['kcal']} kcal (within 5%) with at least {st['protein']} g protein.

SESSIONS
- Tuesday prep ({tue}) covers {st['tue']['covers']}: {st['tue']['breakfast']} breakfast portions, {st['tue']['main']} main portions (one recipe), {st['tue']['dessert']} dessert portions.
- Friday prep ({fri}) covers {st['fri']['covers']}: {st['fri']['breakfast']} breakfast portions, {st['fri']['main']} main portions (one recipe), {st['fri']['dessert']} dessert portions.
Breakfast is always an overnight oats variation. The main is one easy batch meal that reheats well (bowls, burritos, pasta, chili, curry, stir-fry, sheet-pan). Dessert is a high-protein sweet (cheesecake pots, brownies, blondies, mousse, pudding, bars). Make Tuesday and Friday clearly different from each other.

RULES
- Quick and simple: each session's cooking fits in about 2.5 hours for one person with normal home kitchen equipment. Only everyday ingredients found at a normal US supermarket{store_txt}.
- Minimise food waste: plan Tuesday and Friday together so they share perishable ingredients; size quantities to standard US pack sizes and use whole packs where you can; prefer frozen or shelf-stable forms for small amounts.
- Use these leftovers from last week first where sensible: {lst(leftovers)}.
- Already in the pantry, so do not put on the shopping list: {st.get('pantry') or 'nothing specified'}.
- Likes: {st.get('likes') or 'no preference'}. Never use: {st.get('dislikes') or 'nothing specified'}.
- Do not repeat these recent recipes: {lst(recent_titles)}.
- Favourites (you may reuse at most one): {lst(favourites)}.
- This week's request from the owner: {note.strip() or 'none'}.
- Food safety: any portion eaten more than 3 days after it was cooked must be frozen on prep day and moved to the fridge the night before. Say so in that recipe's storage and portionNote.
- Ingredient amounts are for the WHOLE batch, in US units with grams in brackets where useful. "kcal" and "protein" are for that whole-batch amount of that ingredient, from standard US nutrition labels. Before answering, check that the kcal total divided by portions hits the target and that protein does too; adjust amounts until both do.
- Steps are short plain sentences a cook can follow: oven temperatures, times, doneness (chicken to 165°F), how to split into portions evenly (by weight), and labelling with the day to eat.

JSON SHAPE (use exactly these keys):
{{"sessions":{{"tue":{{"recipes":{{"breakfast":R,"main":R,"dessert":R}},"timeline":["..."]}},"fri":{{"recipes":{{"breakfast":R,"main":R,"dessert":R}},"timeline":["..."]}}}},
 "shopping":[{{"item":"Boneless skinless chicken breast","buy":"4 lb","aisle":"Meat","for":"tue","stock":false}}],
 "leftovers":["About 150 g Greek yogurt"]}}
where R = {{"title":"...","blurb":"one short line","portions":9,"portionNote":"9 containers: 3 a day for Wed, Thu, Fri","ingredients":[{{"item":"Chicken breast","amount":"1.8 kg (4 lb)","kcal":2160,"protein":405}}],"steps":["..."],"storage":"..."}}
- timeline: 4 to 6 lines ordering the session's work efficiently (things that chill or bake go first).
- shopping: everything to buy for both sessions, merged across recipes, with the exact pack size to order in "buy". "aisle" is one of {", ".join(AISLES)}. "for" is "tue", "fri" or "both" (the first session that needs it; "both" if both use it). "stock": true only for long-life items that last several weeks (oils, spices, oats, rice, protein powder, baking goods).
- leftovers: realistic amounts of anything left after both sessions."""


def extract_json(text: str):
    """Parse the model reply: whole text, else a ``` fence, else first '{' to last '}'."""
    t = text.strip()
    for candidate in (t,):
        try:
            return json.loads(candidate)
        except ValueError:
            pass
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            pass
    raise GenerationError("The menu came back incomplete. Try again, or add a shorter request.")


def normalize_plan(data, week: str, settings: dict) -> dict:
    bad = GenerationError("The menu came back in an unexpected shape. Try again.")
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        raise bad
    sessions = {}
    for k in ("tue", "fri"):
        s = data["sessions"].get(k)
        if not isinstance(s, dict) or not isinstance(s.get("recipes"), dict):
            raise bad
        recipes = {}
        for slot in SLOTS:
            r = s["recipes"].get(slot)
            if not isinstance(r, dict) or not r.get("title") or not isinstance(r.get("ingredients"), list) or not isinstance(r.get("steps"), list):
                raise bad
            try:
                portions = int(r.get("portions") or 0)
            except (TypeError, ValueError):
                portions = 0
            recipes[slot] = {
                "title": str(r["title"]), "blurb": str(r.get("blurb", "")),
                "portions": portions or int(settings[k][slot]),
                "portionNote": str(r.get("portionNote", "")), "storage": str(r.get("storage", "")), "fav": False,
                "ingredients": [{"item": str(i.get("item", "")), "amount": str(i.get("amount", "")),
                                 "kcal": _num(i.get("kcal")), "protein": _num(i.get("protein"))}
                                for i in r["ingredients"] if isinstance(i, dict)],
                "steps": [str(x) for x in r["steps"]],
            }
        sessions[k] = {"date": add_days(week, 1 if k == "tue" else 4), "covers": settings[k]["covers"],
                       "recipes": recipes, "timeline": [str(x) for x in (s.get("timeline") or []) if x]}
    shopping = []
    for n, i in enumerate(x for x in (data.get("shopping") or []) if isinstance(x, dict)):
        shopping.append({"id": f"s{n + 1:02d}", "item": str(i.get("item", "")), "buy": str(i.get("buy", "")),
                         "aisle": i.get("aisle") if i.get("aisle") in AISLES else "Pantry",
                         "for": i.get("for") if i.get("for") in ("tue", "fri", "both") else "both",
                         "stock": bool(i.get("stock"))})
    leftovers = [str(x) for x in (data.get("leftovers") or [])]
    return {"sessions": sessions, "shopping": shopping, "leftovers": leftovers}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def call_model(prompt: str, on_text: Callable[[str], bool], transport: httpx.BaseTransport | None = None) -> str:
    """Stream a reply from the Claude API. on_text(full_text_so_far) returns False to cancel."""
    cfg = get_config()
    if not cfg.anthropic_api_key:
        raise GenerationError("Menu generation isn't set up: add ANTHROPIC_API_KEY to the server's environment.")
    headers = {"x-api-key": cfg.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    # Claude 5 models default to adaptive thinking, which on this prompt spends the whole output
    # budget thinking before any JSON is written; the menu needs those tokens for the JSON.
    body = {"model": cfg.anthropic_model, "max_tokens": 16000, "stream": True, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}]}
    text, stop_reason = "", None
    timeout = httpx.Timeout(connect=15, read=180, write=30, pool=15)
    with httpx.Client(timeout=timeout, transport=transport) as client:
        with client.stream("POST", API_URL, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                resp.read()
                raise GenerationError(_api_error_message(resp.status_code, resp.text))
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    text += ev["delta"].get("text", "")
                    if not on_text(text):
                        raise _Cancelled()
                elif t == "message_delta":
                    stop_reason = ev.get("delta", {}).get("stop_reason") or stop_reason
                elif t == "error":
                    raise GenerationError(_api_error_message(0, json.dumps(ev.get("error", {}))))
    if stop_reason == "max_tokens":
        raise GenerationError("The menu was too long and got cut off. Try again, or ask for simpler recipes.")
    return text


def _api_error_message(status: int, body: str) -> str:
    if status == 401:
        return "The Claude API key was rejected. Check ANTHROPIC_API_KEY on the server."
    if status == 429 or "rate_limit" in body:
        return "The Claude API is rate-limiting requests right now. Try again in a few minutes."
    if status == 529 or "overloaded" in body:
        return "Claude is busy right now. Try again in a few minutes."
    if status == 400 and "credit" in body.lower():
        return "Your Anthropic account is out of credit."
    if status == 400 and "thinking.type.disabled" in body:
        return ("This model can't run with thinking switched off. Set ANTHROPIC_MODEL to claude-sonnet-5 "
                "(the default) or another model that allows it.")
    return f"The Claude API returned an error ({status or 'stream'}). Try again."


class _Cancelled(Exception):
    pass


def recent_context(conn, week: str) -> tuple[list[str], list[str], list[str]]:
    rows = conn.execute("SELECT week, data FROM meal_plans ORDER BY week DESC").fetchall()
    recent, favs, leftovers = [], [], []
    earlier = {r["week"] for r in rows if r["week"] < week}
    earlier = set(sorted(earlier, reverse=True)[:6])
    for r in rows:
        d = json.loads(r["data"])
        for s in (d.get("sessions") or {}).values():
            for rec in (s.get("recipes") or {}).values():
                if r["week"] in earlier:
                    recent.append(rec.get("title", ""))
                if rec.get("fav") and rec.get("title") not in favs:
                    favs.append(rec["title"])
        if r["week"] == add_days(week, -7):
            leftovers = d.get("leftovers") or []
    return [t for t in recent if t], favs, leftovers


def start_job(conn, week: str, note: str, user_id: int) -> int:
    cur = conn.execute("INSERT INTO ai_jobs (week, status, created_at, created_by) VALUES (?, 'running', ?, ?)",
                       (week, now_iso(), user_id))
    job_id = cur.lastrowid
    threading.Thread(target=run_job, args=(job_id, week, note, user_id), daemon=True, name=f"menu-job-{job_id}").start()
    return job_id


def run_job(job_id: int, week: str, note: str, user_id: int) -> None:
    conn = connect()
    last = [0.0]

    def on_text(text: str) -> bool:
        now = time.monotonic()
        if now - last[0] < 1.5:
            return True
        last[0] = now
        titles = re.findall(r'"title"\s*:\s*"([^"]+)"', text)
        conn.execute("UPDATE ai_jobs SET progress_chars = ?, titles = ? WHERE id = ?", (len(text), json.dumps(titles), job_id))
        status = conn.execute("SELECT status FROM ai_jobs WHERE id = ?", (job_id,)).fetchone()["status"]
        return status != "cancelling"

    try:
        settings = store.get_settings(conn)
        recent, favs, leftovers = recent_context(conn, week)
        prompt = build_prompt(settings, week, recent, favs, leftovers, note)
        text = call_model(prompt, on_text)
        plan = normalize_plan(extract_json(text), week, settings)
        with tx(conn):
            store.save_plan(conn, week, plan, source="Claude", note=note.strip(), user_id=user_id)
        conn.execute("UPDATE ai_jobs SET status = 'done', progress_chars = ?, finished_at = ? WHERE id = ?",
                     (len(text), now_iso(), job_id))
    except _Cancelled:
        conn.execute("UPDATE ai_jobs SET status = 'cancelled', finished_at = ? WHERE id = ?", (now_iso(), job_id))
    except GenerationError as e:
        conn.execute("UPDATE ai_jobs SET status = 'error', error = ?, finished_at = ? WHERE id = ?", (str(e), now_iso(), job_id))
    except httpx.HTTPError:
        conn.execute("UPDATE ai_jobs SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
                     ("Couldn't reach the Claude API. Check the server's internet connection and try again.", now_iso(), job_id))
    except Exception:  # noqa: BLE001 - never leave a job stuck in 'running'
        import logging
        logging.getLogger(__name__).exception("menu job %s failed", job_id)
        conn.execute("UPDATE ai_jobs SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
                     ("Something went wrong while writing the menu. Try again.", now_iso(), job_id))
    finally:
        conn.close()
