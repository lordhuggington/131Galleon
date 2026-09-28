"""The menu brief, the rules Claude follows, and validation of a posted prep session.

Menus are planned in the Claude app and posted back through the MCP connector (app/mcp.py).
Nothing in this module calls a model or talks to the network.
"""
from __future__ import annotations

from datetime import date, timedelta

AISLES = ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"]
SLOTS = ("breakfast", "main", "dessert")


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()
