# 号角 — Wild Mech Paladin (mech-tempo)

Human-readable decision priorities for this deck. This file doubles as
context for any future model call: it is the deck's strategy in prose,
while `deck.json` carries the machine-readable knobs
(`hold_for_value`, `zilliax_save_when_behind`, weights).

No winrates are claimed here. Everything below is the deck's design
intent + the owner's play experience.

## Archetype

Aggro-midrange mech. Tempo > value. Once we have board control, face
damage is worth more than the default evaluation assumes
(`prefer_face_when_board_control`).

## Mulligan

- Always keep: 无人机拆解器, 频率振荡机, 安保自动机, 忙碌机器人.
- Keep when going second (coin): 紫色珍鳃鱼人, 超级电容器, 雷达探测.
- 3+ cost cards are thrown unless the hand already has a 1-2 curve.
- See `mulligan.json` for the machine-readable table + default rule.

## Hold for value

Never play these into an empty board if a curve minion is available:

- 十字军光环 (board buff — needs minions to be worth it)
- 馆长 (value engine — hold for the turn it refills / finds answers)

## Zilliax (奇利亚斯豪华版3000型)

Flex slot. Save it when behind (taunt / heal) or when lethal is one
rush minion away. Never dump it on curve for tempo.

## What the engine does NOT know yet

- Card effects are data, not code: unknown-effect spells are never
  played by the search (see `data/overrides.json` / HearthstoneJSON).
- The engine does not model random outcomes, Discover choices, or
  complex battlecries — those steps are marked inexact and end the
  planned sequence (see `decision.py`).
