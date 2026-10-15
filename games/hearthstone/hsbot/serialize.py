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
        "cardtype": ent.tag("CARDTYPE"),
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


def entity_from_dict(d: dict | None) -> Entity | None:
    """Inverse of entity_to_dict. Round-trip limits (documented):
    zone_pos is lost (0), armor is folded into health (reconstructed with
    ARMOR=0), JUST_PLAYED is folded into EXHAUSTED."""
    if d is None:
        return None
    tags = {
        "CARDTYPE": d.get("cardtype", 0),
        "ATK": d.get("attack", 0),
        "HEALTH": d.get("max_health", d.get("health", 0)),
        "DAMAGE": d.get("max_health", d.get("health", 0)) - d.get("health", 0),
        "COST": d.get("cost", 0),
        "TAUNT": 1 if d.get("taunt") else 0,
        "DIVINE_SHIELD": 1 if d.get("divine_shield") else 0,
        "STEALTH": 1 if d.get("stealth") else 0,
        "EXHAUSTED": 1 if d.get("exhausted") else 0,
    }
    return Entity(
        id=d["id"], name=d.get("name", ""), card_id=d.get("card_id", ""),
        controller=d.get("controller", 0), zone=d.get("zone", ""),
        tags=tags,
    )


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
        "game_over": state.game_over,
        "game_result": state.game_result,
        "mulligan_receipt": state.mulligan_receipt,
    }


def _player_from_dict(d: dict, my_id: int):
    from .state import PlayerState

    p = PlayerState(
        player_id=d["player_id"],
        hero=entity_from_dict(d.get("hero")),
        hero_power=entity_from_dict(d.get("hero_power")),
        weapon=entity_from_dict(d.get("weapon")),
        hand=[e for e in (entity_from_dict(x) for x in d.get("hand", [])) if e],
        board=[e for e in (entity_from_dict(x) for x in d.get("board", [])) if e],
        deck_count=d.get("deck_count", 30),
        mana=d.get("mana", 0),
        max_mana=d.get("max_mana", 0),
        fatigue=d.get("fatigue", 0),
    )
    return p


def state_from_dict(d: dict) -> GameState:
    """Inverse of state_to_dict (see entity_from_dict for round-trip limits).
    Rebuilds the entities index so entity(id) lookups work."""
    state = GameState(
        turn=d.get("turn", 0),
        step=d.get("step", ""),
        current_player=d.get("current_player", 0),
        my_id=d.get("my_id", 1),
    )
    # player_to_dict tags each side with is_me; recover both sides by role.
    me_d = d["me"] if d["me"].get("is_me") else d["opp"]
    opp_d = d["opp"] if d["me"].get("is_me") else d["me"]
    state.players[state.my_id] = _player_from_dict(me_d, state.my_id)
    state.players[3 - state.my_id] = _player_from_dict(opp_d, state.my_id)
    state.secrets = [e for e in (entity_from_dict(x) for x in d.get("secrets", [])) if e]
    state.game_over = bool(d.get("game_over", False))
    state.game_result = d.get("game_result")
    state.mulligan_receipt = d.get("mulligan_receipt")
    for player in state.players.values():
        for ent in [player.hero, player.hero_power, player.weapon, *player.hand, *player.board]:
            if ent is not None:
                state.entities[ent.id] = ent
    for ent in state.secrets:
        state.entities[ent.id] = ent
    return state


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
