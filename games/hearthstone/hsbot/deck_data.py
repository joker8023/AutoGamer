"""Deck data: the 号角 deck (Wild, Mech Paladin).

Source of truth: the deck code the user pasted.
`data/deck.json` was produced by decoding it (see deckstring.py).
"""
from __future__ import annotations

import json
import os

from .cards import CardDB
from .deckstring import decode_deckstring

DECKSTRING = (
    "AAEBAaToAgaHrgOftwP5pATHpAb0qged3QcMpfUCkrUE1L0E2tMEhKUF2dAFuf4Fn+EG4vEGv/kGupYHmvwHAAEE1/4Cnd0H87MGx6QG9rMGx6QGku8Gx6QGAAA="
)


def load_deck_db(base_dir: str | None = None) -> CardDB:
    """Load the card DB for our deck (deck.json fallback layer)."""
    base = base_dir or os.path.dirname(os.path.abspath(__file__))
    deck_path = os.path.join(base, "..", "data", "deck.json")
    overrides_path = os.path.join(base, "..", "data", "overrides.json")
    return CardDB.from_deck_json(deck_path, overrides_path)


def deck_card_names(base_dir: str | None = None) -> list[str]:
    base = base_dir or os.path.dirname(os.path.abspath(__file__))
    deck_path = os.path.join(base, "..", "data", "deck.json")
    with open(deck_path, encoding="utf-8") as f:
        deck = json.load(f)
    return [c["name"] for c in deck.get("cards", [])]


def verify_deckstring() -> dict:
    """Decode the deck code and sanity-check it against deck.json."""
    decoded = decode_deckstring(DECKSTRING)
    names = deck_card_names()
    return {
        "version": decoded.version,
        "format": decoded.format,
        "is_wild": decoded.is_wild,
        "heroes": decoded.heroes,
        "total_cards": decoded.total_cards,
        "unique_cards": len(decoded.cards),
        "sideboard": decoded.sideboard,
        "deck_json_cards": len(names),
    }
