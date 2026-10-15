"""号角 — Wild Mech Paladin: deck definition + strategy tables.

DEPRECATED compat layer. The source of truth moved to data files under
``games/hearthstone/decks/mech-paladin-horn/`` (see ``hsbot/deck_config.py``).
This module re-exports the same names with the same values so existing
imports keep working. New code should call ``deck_config.load_deck()``
instead of importing from here.

Source of truth for card names/costs/counts: the user's deck export.
Card EFFECTS are deliberately NOT hardcoded here; they come from the
card DB (HearthstoneJSON) + overrides.
"""
from __future__ import annotations

from .deck_config import load_deck

_CONFIG = load_deck("mech-paladin-horn")

DECK_CODE = _CONFIG.deck_code
HERO_DBFID = _CONFIG.hero_dbfid  # Uther Lightbringer (Paladin), from the deck code
FORMAT_WILD = 1

# (name, cost, count, role) — roles drive mulligan + play priority.
# Roles: opener / curve / synergy / removal / draw / finisher / flex
DECK_CARDS: list[tuple[str, int, int, str]] = [
    (c["name"], c["cost"], c["count"], c.get("role", "curve"))
    for c in _CONFIG.deck_cards
]

# Zilliax sideboard (from the deck code): base + modules.
ZILLIAX_MODULES: list[tuple[str, int]] = [
    (entry["name"], entry["cost"]) for entry in _CONFIG.sideboard
]

# --- Mulligan table -----------------------------------------------------
# KEEP_ALWAYS: keep in every opener. KEEP_IF_COIN: keep when going second
# (extra mana lets us curve into them). Everything else is replaced.
MULLIGAN = {
    "keep_always": set(_CONFIG.keep_always),
    "keep_if_coin": set(_CONFIG.keep_if_coin),
    # 3+ cost is thrown unless we already have a 1-2 curve (handled in code).
    "keep_curve_filler": set(_CONFIG.keep_curve_filler),
}

# --- Play strategy ------------------------------------------------------
STRATEGY = {
    # Aggro-midrange mech: tempo > value. Face damage is worth more than
    # the default once we have board control; see decision.py weights.
    "archetype": "mech-tempo",
    "prefer_face_when_board_control": True,
    # Never play these into an empty board if a curve minion is available.
    "hold_for_value": set(_CONFIG.holds),
    # Zilliax is the flex slot: save it when behind (taunt/heal) or when
    # lethal is one rush minion away; never dump it on curve for tempo.
    "zilliax_save_when_behind": _CONFIG.zilliax_save_when_behind,
}
