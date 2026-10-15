"""Privacy masking for anything that leaves the local machine.

``public_view(state)`` is the ONLY representation of a game state that may
be sent to an external service (a model API, a log uploader, anything on
the network). It contains exactly what a human opponent could see:

- my hand / board / hero / mana / fatigue (my own information),
- the opponent's board / hero / mana (public),
- the opponent's hand COUNT, deck COUNT, and secrets COUNT (counts only),
- publicly played cards (board minions on both sides).

It NEVER contains: opponent hand identities, deck order, secret
identities, BattleTag / GameAccountId (the parser does not collect
those at all), or raw log lines.

Convention: the local UI may use the full ``GameState``; any network
call must use ``public_view`` output. This module must exist before
any model integration is built.
"""
from __future__ import annotations

import json

from .serialize import entity_to_dict
from .state import GameState

# Keys that must never appear in a masked view. Checked by tests.
_FORBIDDEN_KEYS = ("battletag", "battle_tag", "gameaccountid", "game_account_id",
                   "raw_log", "log_line")


def public_view(state: GameState) -> dict:
    """Masked, network-safe view of a game state."""
    me, opp = state.me, state.opp
    view = {
        "turn": state.turn,
        "step": state.step,
        "current_player": state.current_player,
        "is_my_turn": state.is_my_turn,
        "my_id": state.my_id,
        "me": {
            "hero": entity_to_dict(me.hero),
            "hero_health": me.hero_health,
            "hero_power": entity_to_dict(me.hero_power),
            "weapon": entity_to_dict(me.weapon),
            "hand": [entity_to_dict(e) for e in me.hand],
            "board": [entity_to_dict(e) for e in me.board],
            "mana": me.mana,
            "max_mana": me.max_mana,
            "deck_count": me.deck_count,
            "fatigue": me.fatigue,
        },
        "opp": {
            "hero": entity_to_dict(opp.hero),
            "hero_health": opp.hero_health,
            "hero_power": entity_to_dict(opp.hero_power),
            "weapon": entity_to_dict(opp.weapon),
            "board": [entity_to_dict(e) for e in opp.board],
            "mana": opp.mana,
            "max_mana": opp.max_mana,
            # Counts only — never identities.
            "hand_count": len(opp.hand),
            "deck_count": opp.deck_count,
            "secrets_count": len(state.secrets),
        },
    }
    _assert_clean(view)
    return view


def _assert_clean(view: dict) -> None:
    """Fail loudly if a forbidden key ever sneaks into the masked view."""
    blob = json.dumps(view, ensure_ascii=False).lower()
    for key in _FORBIDDEN_KEYS:
        assert key not in blob, f"masked view leaks {key}"
