"""Game state: the folded view of a Hearthstone game.

The LogParser folds Power.log events into this. The Decision engine reads
only this (never raw log lines).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    id: int
    card_id: str = ""
    name: str = ""
    controller: int = 0  # 0 = unknown, 1 = me, 2 = opp
    zone: str = ""  # HAND / PLAY / DECK / GRAVEYARD / SECRET / ...
    zone_pos: int = 0
    tags: dict[str, int] = field(default_factory=dict)

    # -- derived from CARDTYPE tag -------------------------------------
    @property
    def card_type(self) -> int:
        return self.tags.get("CARDTYPE", 0)

    @property
    def is_hero(self) -> bool:
        return self.card_type == 3

    @property
    def is_hero_power(self) -> bool:
        return self.card_type == 2

    @property
    def is_minion(self) -> bool:
        return self.card_type == 4

    # -- combat stats --------------------------------------------------
    @property
    def attack(self) -> int:
        return self.tags.get("ATK", 0)

    @property
    def health(self) -> int:
        return self.tags.get("HEALTH", 0) - self.tags.get("DAMAGE", 0)

    @property
    def max_health(self) -> int:
        return self.tags.get("HEALTH", 0)

    @property
    def cost(self) -> int:
        return self.tags.get("COST", 0)

    @property
    def taunt(self) -> bool:
        return self.tags.get("TAUNT", 0) == 1

    @property
    def divine_shield(self) -> bool:
        return self.tags.get("DIVINE_SHIELD", 0) == 1

    @property
    def can_attack(self) -> bool:
        return self.tags.get("NUM_ATTACKS_THIS_TURN", 0) == 0 and not self.tags.get("EXHAUSTED", 0)

    @property
    def summoning_sick(self) -> bool:
        return self.tags.get("SUMMONING_SICKNESS", 0) == 1


@dataclass
class Player:
    id: int  # 1 = me, 2 = opp
    hero: Entity | None = None
    hero_power: Entity | None = None
    hand: list[Entity] = field(default_factory=list)
    board: list[Entity] = field(default_factory=list)
    mana: int = 0
    max_mana: int = 0
    deck_count: int = 30
    fatigue: int = 0

    @property
    def hero_health(self) -> int:
        if self.hero is None:
            return 30
        return self.hero.health + self.hero.tags.get("ARMOR", 0)


@dataclass
class GameState:
    entities: dict[int, Entity] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=lambda: {1: Player(1), 2: Player(2)})
    turn: int = 0
    step: str = ""
    current_player: int = 0
    my_id: int = 1
    secrets: list[Entity] = field(default_factory=list)

    @property
    def me(self) -> Player:
        return self.players[self.my_id]

    @property
    def opp(self) -> Player:
        return self.players[3 - self.my_id]

    @property
    def is_my_turn(self) -> bool:
        return self.current_player == self.my_id

    def copy(self) -> GameState:
        """Deep-ish copy for the search: entities are copied, players rebuilt."""
        import copy as _copy

        return _copy.deepcopy(self)
