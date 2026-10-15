"""GameState -> JSON snapshot (for the Tauri console + replay)."""
from __future__ import annotations

from .state import Entity, GameState, Player


def _entity_json(e: Entity) -> dict:
    return {
        "id": e.id,
        "card_id": e.card_id,
        "name": e.name,
        "cost": e.cost,
        "attack": e.attack,
        "health": e.health,
        "max_health": e.max_health,
        "taunt": e.taunt,
        "divine_shield": e.divine_shield,
        "can_attack": e.can_attack,
        "summoning_sick": e.summoning_sick,
        "zone": e.zone,
        "zone_pos": e.zone_pos,
    }


def _player_json(p: Player) -> dict:
    return {
        "hero_health": p.hero_health,
        "mana": p.mana,
        "max_mana": p.max_mana,
        "deck_count": p.deck_count,
        "hero": _entity_json(p.hero) if p.hero else None,
        "hero_power": _entity_json(p.hero_power) if p.hero_power else None,
        "hand": [_entity_json(c) for c in p.hand],
        "board": [_entity_json(m) for m in p.board],
    }


def state_json(state: GameState) -> dict:
    return {
        "turn": state.turn,
        "step": state.step,
        "current_player": state.current_player,
        "is_my_turn": state.is_my_turn,
        "me": _player_json(state.me),
        "opp": _player_json(state.opp),
        "secrets": [_entity_json(s) for s in state.secrets],
    }
