"""Veto / prefer two-layer rules.

The decision engine's action menu passes through two filters before
beam search:

- VETO (hard): actions that are provably wrong are removed, with a
  recorded reason. A veto never removes END_TURN.
- PREFER (soft): named preferences narrow the menu, but a prefer never
  empties the menu and never removes END_TURN. If a prefer-removed
  first-step action would have led to a strictly better plan (by
  ``PREFER_RELEASE_MARGIN``), that prefer is released for the round —
  the search runs a second time on the veto-only menu.

A veto is a function ``(state, db, action, cfg) -> reason | None``.
A prefer is a function ``(state, db, actions, cfg) -> (name, removed)``.
Both layers are pure functions of (state, db, cfg): no hidden state.

This module does NOT import decision.py (decision.py imports this
module); the small board-value helper below mirrors
``decision.minion_value`` on purpose to avoid a circular import.
"""
from __future__ import annotations

from .actions import ATTACK, END_TURN, PLAY_MINION, PLAY_SPELL, Action
from .cards import CardDB
from .deck_config import DeckConfig
from .state import Entity, GameState

PREFER_RELEASE_MARGIN = 1.0  # second-search win needed to release a prefer

_COIN_NAMES = {"幸运币", "The Coin"}
_COIN_CARD_ID = "GAME_005"


# --- helpers ------------------------------------------------------------
def _minion_value(ent: Entity, weights: dict) -> float:
    value = ent.attack * weights["attack"] + ent.health * weights["health"]
    if ent.tag("TAUNT"):
        value += weights["taunt"]
    if ent.tag("DIVINE_SHIELD"):
        value += weights["divine_shield"]
    return value


def _is_behind(state: GameState, weights: dict) -> bool:
    my_board = sum(_minion_value(m, weights) for m in state.me.board)
    opp_board = sum(_minion_value(m, weights) for m in state.opp.board)
    return opp_board > my_board + 2 or state.me.hero_health <= 12


def _card_for(action: Action, db: CardDB):
    if action.kind in (PLAY_MINION, PLAY_SPELL) and action.source_id:
        return db.get_by_name(action.card_name) if action.card_name else None
    return None


# --- vetoes -------------------------------------------------------------
def _veto_own_hero_damage(state, db, action, cfg):
    """Direct damage aimed at our own hero (spell or attack)."""
    me_hero = state.me.hero
    if me_hero is None:
        return None
    if action.kind == PLAY_SPELL and action.target_id == me_hero.id:
        card = _card_for(action, db)
        if card is not None and card.effect_damage:
            return "spell would damage own hero"
    if action.kind == ATTACK and action.target_id == me_hero.id:
        return "attack would target own hero"
    return None


def _veto_fatigue_suicide(state, db, action, cfg):
    """Drawing with an empty deck while at <=1 HP: the next fatigue hit
    (minimum 1 damage) is lethal. Only the lethal case is vetoed."""
    if action.kind not in (PLAY_MINION, PLAY_SPELL):
        return None
    card = _card_for(action, db)
    if card is None or not card.effect_draw:
        return None
    if state.me.deck_count == 0 and state.me.hero_health <= 1:
        return "draw into lethal fatigue (empty deck, <=1 HP)"
    return None


def _veto_useless_buff(state, db, action, cfg):
    """A spell with no modeled effect (pure buff / other) and no
    beneficiary: board-buff on an empty board. Secrets are exempt:
    they are playable on an empty board."""
    if action.kind != PLAY_SPELL:
        return None
    card = _card_for(action, db)
    if card is None or card.effect_unknown:
        return None
    if card.effect_damage or card.effect_draw or card.effect_heal or card.effect_summon:
        return None
    if "SECRET" in card.mechanics:
        return None
    if action.card_name in _COIN_NAMES:
        return None  # the Coin is a mana spell, not a buff (see dead_coin)
    if not state.me.board:
        return "buff with no beneficiary (empty board)"
    return None


def _veto_dead_coin(state, db, action, cfg):
    """The Coin buys nothing: after +1 mana there is still no playable
    card and no usable hero power."""
    if action.kind != PLAY_SPELL:
        return None
    card = _card_for(action, db)
    if card is None:
        return None
    if action.card_name not in _COIN_NAMES:
        return None
    mana_after = state.me.mana + 1
    for ent in state.me.hand:
        if ent.id == action.source_id:
            continue
        if ent.cost <= mana_after:
            return None
    hp = state.me.hero_power
    if (hp is not None and mana_after >= 2
            and hp.id not in state.sim_exhausted
            and len(state.me.board) < 7):
        return None
    return "coin buys nothing"


def _veto_full_board(state, db, action, cfg):
    """Playing a minion onto a full board (defense in depth: the
    enumerator already filters this)."""
    if action.kind == PLAY_MINION and len(state.me.board) >= 7:
        return "board is full"
    return None


VETOES: list[tuple[str, object]] = [
    ("own_hero_damage", _veto_own_hero_damage),
    ("fatigue_suicide", _veto_fatigue_suicide),
    ("useless_buff", _veto_useless_buff),
    ("dead_coin", _veto_dead_coin),
    ("full_board", _veto_full_board),
]


def veto_actions(state: GameState, db: CardDB, actions: list[Action],
                 deck_config: DeckConfig | None = None
                 ) -> tuple[list[Action], list[dict]]:
    """Split actions into (kept, removed). Each removed entry is
    ``{action, rule, reason}``. END_TURN is never vetoed."""
    cfg = deck_config
    kept: list[Action] = []
    removed: list[dict] = []
    for action in actions:
        if action.kind == END_TURN:
            kept.append(action)
            continue
        hit = None
        for name, fn in VETOES:
            reason = fn(state, db, action, cfg)  # type: ignore[operator]
            if reason is not None:
                hit = {"action": repr(action), "rule": name, "reason": reason}
                break
        if hit is None:
            kept.append(action)
        else:
            removed.append(hit)
    return kept, removed


# --- prefers ------------------------------------------------------------
def _prefer_hold_zilliax(state, db, actions, cfg):
    """Zilliax: save when behind (flex removal/heal), never tempo it."""
    if cfg is None or not cfg.zilliax_save_when_behind:
        return "hold_zilliax", []
    if not _is_behind(state, cfg.weights):
        return "hold_zilliax", []
    removed = [a for a in actions
               if a.kind == PLAY_MINION and a.card_name == "奇利亚斯豪华版3000型"]
    return "hold_zilliax", removed


def _prefer_hold_for_value(state, db, actions, cfg):
    """Hold-for-value cards: never play them into an empty board."""
    if cfg is None or not cfg.holds:
        return "hold_for_value", []
    if state.me.board:
        return "hold_for_value", []
    removed = [a for a in actions
               if a.kind == PLAY_MINION and a.card_name in cfg.holds]
    return "hold_for_value", removed


PREFERS = [_prefer_hold_zilliax, _prefer_hold_for_value]


def prefer_actions(state: GameState, db: CardDB, actions: list[Action],
                   deck_config: DeckConfig | None = None
                   ) -> tuple[list[Action], list[dict]]:
    """Narrow the menu with ordered prefers. Each prefer applies only if
    the narrowed menu stays non-empty; END_TURN is never removed.
    Returns (kept, applied) where applied = [{rule, removed: [repr]}]."""
    kept = list(actions)
    applied: list[dict] = []
    for fn in PREFERS:
        name, removed = fn(state, db, kept, deck_config)
        removed = [a for a in removed if a.kind != END_TURN]
        if not removed:
            continue
        narrowed = [a for a in kept if a not in removed]
        if not narrowed:
            continue  # a prefer never empties the menu
        kept = narrowed
        applied.append({"rule": name, "removed": [repr(a) for a in removed]})
    return kept, applied
