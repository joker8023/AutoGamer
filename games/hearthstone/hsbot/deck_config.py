"""Per-deck configuration loaded from data files (not code).

A deck lives in ``games/hearthstone/decks/<deck-id>/`` with three files:

- ``deck.json``    — card list (+ machine-readable strategy knobs).
- ``mulligan.json`` — keep tendencies / combos. Every entry carries a
  ``source`` annotation. No winrates are claimed anywhere: entries come
  from the user's own deck export / play experience, or from cited
  public statistics. When data is missing, the engine falls back to the
  "keep low-cost curve" default rule in ``mulligan.json``.
- ``strategy.md``   — human-readable decision priorities. Also serves as
  context for any future model call.

``DeckConfig`` is the in-memory view. ``decision.py`` takes a
``DeckConfig`` instead of reading module-level tables, so adding a deck
means adding a directory, not editing engine code.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULT_DECK_ID = "mech-paladin-horn"

# Fallback weights, used only when deck.json carries no "weights" section.
# Kept in sync with the values decision.py used before data files existed.
DEFAULT_WEIGHTS = {
    "my_hero": 3.0,
    "opp_hero": 3.0,
    "attack": 1.0,
    "health": 0.9,
    "taunt": 1.5,
    "divine_shield": 1.2,
    "hand_diff": 1.5,
    "board_count": 0.5,
    "unspent_mana": -0.3,
}


@dataclass
class DeckConfig:
    deck_id: str
    name: str
    deck_code: str
    hero: str
    hero_dbfid: int | None
    format: str  # "wild" / "standard"
    deck_cards: list[dict] = field(default_factory=list)
    sideboard: list[dict] = field(default_factory=list)
    mulligan: dict = field(default_factory=dict)
    strategy_text: str = ""
    weights: dict = field(default_factory=dict)
    holds: set[str] = field(default_factory=set)  # hold_for_value card names
    zilliax_save_when_behind: bool = False
    deck_json_path: str = ""

    @property
    def keep_always(self) -> set[str]:
        return set(self.mulligan.get("keep_always", []))

    @property
    def keep_if_coin(self) -> set[str]:
        return set(self.mulligan.get("keep_if_coin", []))

    @property
    def keep_curve_filler(self) -> set[str]:
        return set(self.mulligan.get("keep_curve_filler", []))


def decks_dir(base: str | None = None) -> str:
    """Directory holding ``decks/<deck-id>/``. Defaults to the decks dir
    next to the hsbot package (``games/hearthstone/decks/``)."""
    if base is not None:
        return base
    return os.path.join(os.path.dirname(__file__), "..", "decks")


def list_decks(base: str | None = None) -> list[str]:
    root = decks_dir(base)
    if not os.path.isdir(root):
        return []
    return sorted(
        name
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
        and os.path.exists(os.path.join(root, name, "deck.json"))
    )


def load_deck(deck_id: str, base: str | None = None) -> DeckConfig:
    """Load one deck's data files into a DeckConfig. Raises FileNotFoundError
    for an unknown deck id."""
    root = os.path.join(decks_dir(base), deck_id)
    deck_path = os.path.join(root, "deck.json")
    with open(deck_path, encoding="utf-8") as f:
        deck = json.load(f)
    mulligan: dict = {}
    mulligan_path = os.path.join(root, "mulligan.json")
    if os.path.exists(mulligan_path):
        with open(mulligan_path, encoding="utf-8") as f:
            mulligan = json.load(f)
    strategy_text = ""
    strategy_path = os.path.join(root, "strategy.md")
    if os.path.exists(strategy_path):
        with open(strategy_path, encoding="utf-8") as f:
            strategy_text = f.read()
    strategy = deck.get("strategy", {})
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(deck.get("weights", {}))
    return DeckConfig(
        deck_id=deck_id,
        name=deck.get("name", deck_id),
        deck_code=deck.get("deck_code", ""),
        hero=deck.get("hero", ""),
        hero_dbfid=deck.get("hero_dbfid"),
        format=deck.get("format", "wild"),
        deck_cards=list(deck.get("cards", [])),
        sideboard=list(deck.get("sideboard", [])),
        mulligan=mulligan,
        strategy_text=strategy_text,
        weights=weights,
        holds=set(strategy.get("hold_for_value", [])),
        zilliax_save_when_behind=bool(
            strategy.get("zilliax_save_when_behind", False)
        ),
        deck_json_path=deck_path,
    )
