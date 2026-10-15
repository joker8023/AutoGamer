"""Hearthstone deckstring decoder (HearthSim format).

Format (verified against the user's 号角 deck code):
  reserved(0, varint) | version(varint) | format(varint, 1=Wild, 2=Standard)
  | heroes: count(varint) + dbfIds
  | cards: 1-copy group, 2-copy group, n-copy group (varint counts + dbfIds)
  | sideboard (optional): present(varint, 1) | count(varint)
  |   count x (cardDbfId, ownerDbfId) pairs
  |   two trailing reserved zero varints

Sideboard entries are (card, owner) pairs: e.g. Zilliax Deluxe 3000 owns
its base card + its modules; 通电机器人 is attached to 指挥官碧阿崔克丝.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field


@dataclass
class DeckString:
    version: int
    format: int  # 1 = Wild, 2 = Standard
    heroes: list[int] = field(default_factory=list)
    cards: list[tuple[int, int]] = field(default_factory=list)  # (dbfId, count)
    sideboard: list[tuple[int, int]] = field(default_factory=list)  # (cardDbfId, ownerDbfId)

    @property
    def total_cards(self) -> int:
        return sum(count for _, count in self.cards)

    @property
    def is_wild(self) -> bool:
        return self.format == 1


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if pos >= len(data):
            raise ValueError("deckstring truncated while reading varint")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def decode_deckstring(deckstring: str) -> DeckString:
    data = base64.b64decode(deckstring.strip())
    pos = 0
    _reserved, pos = _read_varint(data, pos)
    version, pos = _read_varint(data, pos)
    fmt, pos = _read_varint(data, pos)

    n_heroes, pos = _read_varint(data, pos)
    heroes = []
    for _ in range(n_heroes):
        h, pos = _read_varint(data, pos)
        heroes.append(h)

    cards: list[tuple[int, int]] = []
    n_single, pos = _read_varint(data, pos)
    for _ in range(n_single):
        c, pos = _read_varint(data, pos)
        cards.append((c, 1))
    n_double, pos = _read_varint(data, pos)
    for _ in range(n_double):
        c, pos = _read_varint(data, pos)
        cards.append((c, 2))
    n_multi, pos = _read_varint(data, pos)
    for _ in range(n_multi):
        c, pos = _read_varint(data, pos)
        cnt, pos = _read_varint(data, pos)
        cards.append((c, cnt))

    sideboard: list[tuple[int, int]] = []
    if pos < len(data):
        present, pos = _read_varint(data, pos)
        if present:
            n_sb, pos = _read_varint(data, pos)
            for _ in range(n_sb):
                card_id, pos = _read_varint(data, pos)
                owner_id, pos = _read_varint(data, pos)
                sideboard.append((card_id, owner_id))
            # Two trailing reserved zero varints (observed in current client output).
            for _ in range(2):
                if pos < len(data):
                    _zero, pos = _read_varint(data, pos)
    return DeckString(
        version=version, format=fmt, heroes=heroes, cards=cards, sideboard=sideboard
    )
