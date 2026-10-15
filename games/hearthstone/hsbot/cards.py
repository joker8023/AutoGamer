"""Card database: names + stats, layered.

Layer 1 (preferred): HearthstoneJSON cards.collectible.json.
Layer 2 (fallback): data/deck.json — only what the deck export gives us.

Rule: the engine never hardcodes card effects. Anything not in the data
is UNKNOWN, and the decision search must not gamble on it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Card:
    dbf_id: int
    name: str
    cost: int = 0
    attack: int | None = None
    health: int | None = None
    card_type: str = "UNKNOWN"  # MINION / SPELL / HERO / WEAPON / ...
    race: str | None = None
    keywords: set[str] = field(default_factory=set)  # TAUNT, DIVINE_SHIELD, ...
    text: str = ""
    inferred: bool = False  # True when type/race were guessed from the name
    # Structured effect overrides (from data/overrides.json), e.g.
    # {"effect_damage": 3, "effect_unknown": False}
    effect: dict = field(default_factory=dict)

    @property
    def is_minion(self) -> bool:
        return self.card_type == "MINION"

    @property
    def effect_unknown(self) -> bool:
        return self.effect.get("effect_unknown", True)


class CardDB:
    def __init__(self) -> None:
        self.by_id: dict[int, Card] = {}
        self.by_name: dict[str, Card] = {}

    def add(self, card: Card) -> None:
        self.by_id[card.dbf_id] = card
        self.by_name[card.name] = card

    def get_by_id(self, dbf_id: int) -> Card | None:
        return self.by_id.get(dbf_id)

    def get_by_name(self, name: str) -> Card | None:
        return self.by_name.get(name)

    @classmethod
    def from_hearthstone_json(cls, path: str) -> CardDB:
        """Load the full collectible set from HearthstoneJSON."""
        db = cls()
        with open(path, encoding="utf-8") as f:
            cards = json.load(f)
        for raw in cards:
            db.add(
                Card(
                    dbf_id=int(raw["dbfId"]),
                    name=raw.get("name", ""),
                    cost=int(raw.get("cost", 0)),
                    attack=raw.get("attack"),
                    health=raw.get("health"),
                    card_type=raw.get("type", "UNKNOWN"),
                    race=raw.get("race"),
                    keywords={
                        k
                        for k in ("TAUNT", "DIVINE_SHIELD", "RUSH", "CHARGE", "BATTLECRY",
                                  "DEATHRATTLE", "STEALTH", "WINDFURY", "LIFESTEAL")
                        if raw.get("mechanics", {}).get(k)
                    },
                    text=raw.get("text", ""),
                )
            )
        return db

    @classmethod
    def from_deck_json(cls, path: str, overrides_path: str | None = None) -> CardDB:
        """Fallback: only the cards in our deck, from the deck export."""
        db = cls()
        with open(path, encoding="utf-8") as f:
            deck = json.load(f)
        overrides: dict[str, dict] = {}
        if overrides_path and os.path.exists(overrides_path):
            with open(overrides_path, encoding="utf-8") as f:
                overrides = json.load(f)
        for raw in deck.get("cards", []):
            name = raw.get("name", "")
            db.add(
                Card(
                    dbf_id=int(raw["dbfId"]),
                    name=name,
                    cost=int(raw.get("cost", 0)),
                    attack=raw.get("attack"),
                    health=raw.get("health"),
                    card_type=raw.get("type", "UNKNOWN"),
                    race=raw.get("race"),
                    inferred=raw.get("type_inferred", False),
                    effect=overrides.get(name, {}),
                )
            )
        return db
