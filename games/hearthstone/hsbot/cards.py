"""Card database.

Card stats/effects are DATA, not code. The engine loads them from a
HearthstoneJSON `cards.collectible.json` (https://hearthstonejson.com/)
when available, and falls back to the minimal deck-local table
(`data/deck.json`, which only knows name/cost/count from the deck export).

Any card whose stats are unknown gets attack/health = None and the
decision engine treats it conservatively (see decision.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Card:
    dbf_id: int | None = None
    name: str = ""
    cost: int = 0
    attack: int | None = None  # None = unknown
    health: int | None = None
    type: str = ""  # MINION / SPELL / WEAPON / HERO_POWER
    text: str = ""
    mechanics: set[str] = field(default_factory=set)  # TAUNT, DIVINE_SHIELD, ...
    race: str = ""  # MECHANICAL / MURLOC / DRAGON / BEAST ...
    # Structured effect hints, filled from the card DB / overrides.
    # Only effects the engine understands are listed here; anything else
    # stays "unknown" and the engine will not gamble on it.
    effect_damage: int | None = None  # direct damage to a target
    effect_draw: int = 0
    effect_heal: int = 0
    effect_summon: str = ""  # card id summoned by this card, if any
    effect_unknown: bool = True

    @property
    def is_minion(self) -> bool:
        return self.type == "MINION"

    @property
    def is_spell(self) -> bool:
        return self.type == "SPELL"

    @property
    def is_mech(self) -> bool:
        return self.race == "MECHANICAL"

    @property
    def has_taunt(self) -> bool:
        return "TAUNT" in self.mechanics

    @property
    def has_divine_shield(self) -> bool:
        return "DIVINE_SHIELD" in self.mechanics


class CardDB:
    def __init__(self) -> None:
        self.by_dbfid: dict[int, Card] = {}
        self.by_name: dict[str, Card] = {}

    def add(self, card: Card) -> None:
        if card.dbf_id is not None:
            self.by_dbfid[card.dbf_id] = card
        if card.name:
            self.by_name[card.name] = card

    def get_by_dbfid(self, dbf_id: int) -> Card | None:
        return self.by_dbfid.get(dbf_id)

    def get_by_name(self, name: str) -> Card | None:
        return self.by_name.get(name)

    @classmethod
    def from_hearthstonejson(cls, path: str) -> "CardDB":
        """Load HearthstoneJSON cards.collectible.json (full exact data)."""
        db = cls()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for entry in raw:
            card = Card(
                dbf_id=entry.get("dbfId"),
                name=entry.get("name", ""),
                cost=entry.get("cost", 0),
                attack=entry.get("attack"),
                health=entry.get("health"),
                type=entry.get("type", ""),
                text=entry.get("text", ""),
                mechanics=set(entry.get("mechanics", [])),
                race=entry.get("race", ""),
                effect_unknown=True,  # structured effects need the overrides file
            )
            db.add(card)
        return db

    @classmethod
    def from_deck_json(cls, path: str) -> "CardDB":
        """Minimal fallback: only name/cost/count known from the deck export."""
        db = cls()
        with open(path, encoding="utf-8") as f:
            deck = json.load(f)
        for entry in deck["cards"]:
            card = Card(
                name=entry["name"],
                cost=entry["cost"],
                type=entry.get("type", ""),
                race=entry.get("race", ""),
                effect_unknown=True,
            )
            db.add(card)
        for entry in deck.get("sideboard", []):
            db.add(Card(name=entry["name"], cost=entry["cost"], type="SPELL"))
        return db

    def apply_overrides(self, path: str) -> None:
        """Apply structured effect overrides: {name: {effect_damage, ...}}."""
        with open(path, encoding="utf-8") as f:
            overrides = json.load(f)
        for name, eff in overrides.items():
            card = self.by_name.get(name)
            if card is None:
                continue
            for key, value in eff.items():
                if hasattr(card, key):
                    setattr(card, key, value)
            card.effect_unknown = eff.get("effect_unknown", False)
