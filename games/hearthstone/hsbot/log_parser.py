"""Power.log parser -> GameState.

Handles the event lines Hearthstone writes to Power.log:
  TAG_CHANGE Entity=[...] tag=X value=Y
  FULL_ENTITY - Creating ID=N CardID=...   (followed by indented tag= lines)
  SHOW_ENTITY - Updating Entity=[...] CardID=...
  HIDE_ENTITY - Entity=[...] tag=ZONE value=...
  ACTION_START / ACTION_END (block markers, tracked for ordering)

The parser is incremental: feed it one line at a time (tail -f style);
it folds events into the GameState in place.
"""
from __future__ import annotations

import re

from .state import Entity, GameState

_RE_TAG_CHANGE = re.compile(
    r"TAG_CHANGE Entity=(?P<ent>\[[^\]]*\]|GameEntity)\s+tag=(?P<tag>\w+)\s+value=(?P<value>-?\w+)"
)
_RE_FULL_ENTITY = re.compile(r"FULL_ENTITY - Creating ID=(?P<id>\d+)\s+CardID=(?P<card>\S*)")
_RE_SHOW_ENTITY = re.compile(
    r"SHOW_ENTITY - Updating Entity=(?P<ent>\[[^\]]*\])\s+CardID=(?P<card>\S*)"
)
_RE_HIDE_ENTITY = re.compile(r"HIDE_ENTITY - Entity=(?P<ent>\[[^\]]*\])\s+tag=(?P<tag>\w+)\s+value=(?P<value>-?\w+)")
_RE_BLOCK_TAG = re.compile(r"tag=(?P<tag>\w+)\s+value=(?P<value>-?\w+)")
_EVENT_KEYWORDS = ("TAG_CHANGE", "FULL_ENTITY", "SHOW_ENTITY", "HIDE_ENTITY", "ACTION_START", "ACTION_END")
_RE_KV = re.compile(r"(\w+)=([^\s\]]+)")

# CARDTYPE values in the log: 2=HERO_POWER, 3=HERO, 4=MINION, 5=SPELL, 6=ENCHANTMENT, 7=WEAPON


def parse_entity_descriptor(text: str) -> dict[str, str]:
    """Parse `Entity=[name=X id=1 zone=HAND zonePos=0 cardId=Y player=1]`."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    out: dict[str, str] = {}
    for key, value in _RE_KV.findall(text):
        out[key] = value
    return out


def _as_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LogParser:
    def __init__(self, state: GameState | None = None) -> None:
        self.state = state or GameState()
        self._pending_entity: Entity | None = None  # FULL_ENTITY block in progress

    # -- public ----------------------------------------------------------
    def feed_line(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        if self._pending_entity is not None:
            # FULL_ENTITY blocks are contiguous indented `tag=... value=...`
            # lines (with the normal log prefix). Any other event line ends
            # the block.
            if "tag=" in line and "value=" in line and not any(
                kw in line for kw in _EVENT_KEYWORDS
            ):
                m = _RE_BLOCK_TAG.search(line)
                if m:
                    self._apply_block_tag(m.group("tag"), m.group("value"))
                    return
            self._finish_pending_entity()
        if "TAG_CHANGE" in line:
            m = _RE_TAG_CHANGE.search(line)
            if m:
                self._apply_tag_change(m.group("ent"), m.group("tag"), m.group("value"))
        elif "FULL_ENTITY" in line:
            m = _RE_FULL_ENTITY.search(line)
            if m:
                self._pending_entity = Entity(
                    id=int(m.group("id")), card_id=m.group("card")
                )
        elif "SHOW_ENTITY" in line:
            m = _RE_SHOW_ENTITY.search(line)
            if m:
                desc = parse_entity_descriptor(m.group("ent"))
                ent = self._get_or_create(desc)
                ent.card_id = m.group("card")
                self._place_entity(ent)
        elif "HIDE_ENTITY" in line:
            m = _RE_HIDE_ENTITY.search(line)
            if m:
                desc = parse_entity_descriptor(m.group("ent"))
                ent = self._get_or_create(desc)
                ent.tags[m.group("tag")] = _as_int(m.group("value"))

    def feed_text(self, text: str) -> None:
        for line in text.splitlines():
            self.feed_line(line)
        if self._pending_entity is not None:
            self._finish_pending_entity()

    # -- internals -------------------------------------------------------
    def _apply_block_tag(self, tag: str, value: str) -> None:
        assert self._pending_entity is not None
        self._pending_entity.tags[tag] = _as_int(value)
        if tag == "CONTROLLER":
            self._pending_entity.controller = _as_int(value)
        elif tag == "ZONE":
            self._pending_entity.zone = value
        elif tag == "ZONE_POSITION":
            self._pending_entity.zone_pos = _as_int(value)
        elif tag == "ENTITY_ID":
            pass  # id already known from the FULL_ENTITY header

    def _finish_pending_entity(self) -> None:
        ent = self._pending_entity
        self._pending_entity = None
        if ent is None:
            return
        self.state.entities[ent.id] = ent
        self._place_entity(ent)

    def _get_or_create(self, desc: dict[str, str]) -> Entity:
        entity_id = _as_int(desc.get("id", "0"))
        ent = self.state.entities.get(entity_id)
        if ent is None:
            ent = Entity(id=entity_id)
            self.state.entities[entity_id] = ent
        ent.name = desc.get("name", ent.name)
        if "cardId" in desc:
            ent.card_id = desc["cardId"]
        if "player" in desc:
            ent.controller = _as_int(desc["player"])
        if "zone" in desc:
            ent.zone = desc["zone"]
        if "zonePos" in desc:
            ent.zone_pos = _as_int(desc["zonePos"])
        return ent

    def _place_entity(self, ent: Entity) -> None:
        """Put the entity in the right player slot based on zone + type."""
        player = self.state.players.get(ent.controller)
        if player is None or ent.controller == 0:
            return
        if ent.is_hero:
            player.hero = ent
        elif ent.is_hero_power:
            player.hero_power = ent
        elif ent.zone == "HAND":
            if ent not in player.hand:
                player.hand.append(ent)
        elif ent.zone == "PLAY":
            if ent.is_minion and ent not in player.board:
                player.board.append(ent)
        elif ent.zone == "SECRET" and ent.controller != self.state.my_id:
            if ent not in self.state.secrets:
                self.state.secrets.append(ent)

    def _apply_tag_change(self, ent_text: str, tag: str, value: str) -> None:
        state = self.state
        if ent_text == "GameEntity":
            if tag == "TURN":
                state.turn = _as_int(value)
            elif tag == "STEP":
                state.step = value
            elif tag == "CURRENT_PLAYER":
                state.current_player = _as_int(value)
            return
        desc = parse_entity_descriptor(ent_text)
        ent = self._get_or_create(desc)
        ent.tags[tag] = _as_int(value)
        if tag == "ZONE":
            old_zone = ent.zone
            ent.zone = value
            self._on_zone_change(ent, old_zone, value)
        elif tag == "ZONE_POSITION":
            ent.zone_pos = _as_int(value)
        elif tag == "CONTROLLER":
            ent.controller = _as_int(value)
        elif tag == "RESOURCES":
            player = state.players.get(ent.controller)
            if player is not None:
                player.mana = _as_int(value)
        elif tag == "TEMP_RESOURCES":
            pass  # overload mana, handled via RESOURCES on turn start
        elif tag == "MAX_RESOURCES":
            player = state.players.get(ent.controller)
            if player is not None:
                player.max_mana = _as_int(value)
        self._place_entity(ent)

    def _on_zone_change(self, ent: Entity, old_zone: str, new_zone: str) -> None:
        player = self.state.players.get(ent.controller)
        if player is None:
            return
        if old_zone == "HAND" and ent in player.hand:
            player.hand.remove(ent)
        if old_zone == "PLAY" and ent in player.board:
            player.board.remove(ent)
        if old_zone == "DECK":
            player.deck_count = max(0, player.deck_count - 1)
        if new_zone == "HAND" and ent not in player.hand:
            player.hand.append(ent)
        if new_zone == "PLAY" and ent.is_minion and ent not in player.board:
            player.board.append(ent)
        if new_zone == "SECRET" and ent.controller != self.state.my_id:
            if ent not in self.state.secrets:
                self.state.secrets.append(ent)
        if new_zone == "GRAVEYARD" and ent in player.board:
            player.board.remove(ent)
