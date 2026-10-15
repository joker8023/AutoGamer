"""JSON serialization of GameState for the Tauri sidecar / replay output."""
from __future__ import annotations

from .state import Entity, GameState


def entity_to_dict(ent: Entity | None) -> dict | None:
    if ent is None:
        return None
    return {
        "id": ent.id,
        "name": ent.name,
        "card_id": ent.card_id,
        "controller": ent.controller,
        "zone": ent.zone,
        "attack": ent.attack,
        "health": ent.health,
        "max_health": ent.max_health,
        "cost": ent.cost,
        "taunt": bool(ent.tag("TAUNT")),
        "divine_shield": bool(ent.tag("DIVINE_SHIELD")),
        "stealth": bool(ent.tag("STEALTH")),
        "exhausted": ent.exhausted,
    }


def player_to_dict(player, my_id: int) -> dict:
    return {
        "player_id": player.player_id,
        "is_me": player.player_id == my_id,
        "hero": entity_to_dict(player.hero),
        "hero_health": player.hero_health,
        "hero_power": entity_to_dict(player.hero_power),
        "weapon": entity_to_dict(player.weapon),
        "hand": [entity_to_dict(e) for e in player.hand],
        "board": [entity_to_dict(e) for e in player.board],
        "mana": player.mana,
        "max_mana": player.max_mana,
        "deck_count": player.deck_count,
    }


def state_to_dict(state: GameState) -> dict:
    return {
        "turn": state.turn,
        "step": state.step,
        "current_player": state.current_player,
        "is_my_turn": state.is_my_turn,
        "my_id": state.my_id,
        "me": player_to_dict(state.me, state.my_id),
        "opp": player_to_dict(state.opp, state.my_id),
        "secrets": [entity_to_dict(e) for e in state.secrets],
    }


def state_signature(state: GameState) -> tuple:
    """Cheap change detection for replay dedup."""
    snap = state_to_dict(state)
    me, opp = snap["me"], snap["opp"]
    return (
        snap["turn"], snap["step"], snap["current_player"],
        me["mana"], me["hero_health"], opp["hero_health"],
        tuple((c["id"], c["name"]) for c in me["hand"]),
        tuple((c["id"], c["attack"], c["health"]) for c in me["board"]),
        tuple((c["id"], c["attack"], c["health"]) for c in opp["board"]),
    )
