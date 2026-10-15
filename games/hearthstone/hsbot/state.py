"""Game state model, folded from Power.log events.

Zones: DECK / HAND / PLAY / GRAVEYARD / SETASIDE / SECRET / REMOVEDFROMGAME.
Tags we track: ATK, HEALTH, DAMAGE, COST, TAUNT, DIVINE_SHIELD, STEALTH,
WINDFURY, RUSH, CHARGE, EXHAUSTED, JUST_PLAYED, CONTROLLER, ZONE, ZONE_POSITION.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass
class Entity:
    id: int
    name: str = ""
    card_id: str = ""  # e.g. "GVG_..." / cardId from the log
    controller: int = 0  # 1 = us, 2 = opponent
    zone: str = ""
    zone_pos: int = 0
    tags: dict[str, int] = field(default_factory=dict)

    def tag(self, name: str, default: int = 0) -> int:
        return self.tags.get(name, default)

    @property
    def attack(self) -> int:
        return self.tag("ATK")

    @property
    def health(self) -> int:
        return max(0, self.tag("HEALTH") - self.tag("DAMAGE"))

    @property
    def max_health(self) -> int:
        return self.tag("HEALTH")

    @property
    def cost(self) -> int:
        return self.tag("COST")

    @property
    def is_minion(self) -> bool:
        return self.tag("CARDTYPE") == 4

    @property
    def is_hero(self) -> bool:
        return self.tag("CARDTYPE") == 3

    @property
    def is_hero_power(self) -> bool:
        return self.tag("CARDTYPE") == 2

    @property
    def exhausted(self) -> bool:
        return self.tag("EXHAUSTED") == 1 or self.tag("JUST_PLAYED") == 1

    @property
    def can_attack(self) -> bool:
        return self.is_minion and self.attack > 0 and not self.exhausted


@dataclass
class PlayerState:
    player_id: int = 0
    hero: Entity | None = None
    hero_power: Entity | None = None
    weapon: Entity | None = None
    hand: list[Entity] = field(default_factory=list)
    board: list[Entity] = field(default_factory=list)
    deck_count: int = 30
    mana: int = 0
    max_mana: int = 0
    fatigue: int = 0

    @property
    def hero_health(self) -> int:
        if self.hero is None:
            return 30
        return self.hero.health + self.hero.tag("ARMOR")


@dataclass
class GameState:
    game_id: int = 0
    turn: int = 0
    step: str = ""
    current_player: int = 0
    players: dict[int, PlayerState] = field(default_factory=dict)
    my_id: int = 1
    secrets: list[Entity] = field(default_factory=list)  # opponent secrets
    entities: dict[int, Entity] = field(default_factory=dict)
    # Sim-only bookkeeping (not from the log).
    sim_exhausted: set[int] = field(default_factory=set)
    # Game lifecycle (from the log; see log_parser.py).
    game_over: bool = False
    game_result: str | None = None  # "WIN" / "LOSS" / "TIE" once known
    # Post-mulligan hand (card names) recorded when the mulligan phase
    # ends; used to verify the mulligan kept the expected cards.
    mulligan_receipt: list[str] | None = None

    def __post_init__(self) -> None:
        self.players.setdefault(1, PlayerState(player_id=1))
        self.players.setdefault(2, PlayerState(player_id=2))

    @property
    def me(self) -> PlayerState:
        return self.players[self.my_id]

    @property
    def opp(self) -> PlayerState:
        return self.players[3 - self.my_id]

    @property
    def is_my_turn(self) -> bool:
        return self.current_player == self.my_id

    def clone(self) -> "GameState":
        return copy.deepcopy(self)

    def entity(self, entity_id: int) -> Entity | None:
        return self.entities.get(entity_id)
