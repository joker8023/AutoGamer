"""Human labeling helper: show a position + its options, record best/ok.

Usage (from games/hearthstone/):
    python3 eval/eval_label.py [--positions ...] [--labels ...]

For each unlabelled position, prints the state summary and the legal
menu, then prompts: best indices (comma-separated), ok indices, note.
Writes eval/labels.json. Non-interactive parts (formatting) are covered
by tests; the prompt loop is manual.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hsbot.serialize import state_from_dict  # noqa: E402

HERE = os.path.dirname(__file__)


def format_position(pos: dict) -> str:
    st = state_from_dict(pos["state"])
    lines = [f"== {pos['id']}  ({pos.get('source', '')})",
             f"turn {st.turn}  mana {st.me.mana}  my HP {st.me.hero_health}  "
             f"opp HP {st.opp.hero_health}"]
    lines.append("my hand: " + ", ".join(
        f"{e.name}(id={e.id},cost={e.cost})" for e in st.me.hand) or "(empty)")
    lines.append("my board: " + ", ".join(
        f"{e.name}(id={e.id},{e.attack}/{e.health})" for e in st.me.board) or "(empty)")
    lines.append("opp board: " + ", ".join(
        f"{e.name}(id={e.id},{e.attack}/{e.health})" for e in st.opp.board) or "(empty)")
    lines.append("menu:")
    for i, action in enumerate(pos.get("menu", [])):
        lines.append(f"  [{i}] {action}")
    return "\n".join(lines)


def _parse_indices(raw: str, n: int) -> list[int]:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        idx = int(part)
        if 0 <= idx < n:
            out.append(idx)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default=os.path.join(HERE, "positions.jsonl"))
    ap.add_argument("--labels", default=os.path.join(HERE, "labels.json"))
    args = ap.parse_args()
    with open(args.positions, encoding="utf-8") as f:
        positions = [json.loads(line) for line in f if line.strip()]
    labels = {}
    if os.path.exists(args.labels):
        with open(args.labels, encoding="utf-8") as f:
            labels = json.load(f)
    for pos in positions:
        if pos["id"] in labels:
            continue
        print(format_position(pos))
        menu = pos.get("menu", [])
        best_idx = _parse_indices(input("best indices (comma-sep): "), len(menu))
        ok_idx = _parse_indices(input("ok indices (comma-sep): "), len(menu))
        note = input("note: ").strip()
        labels[pos["id"]] = {
            "best": [menu[i] for i in best_idx],
            "ok": [menu[i] for i in ok_idx],
            "note": note,
        }
    with open(args.labels, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(labels)} labels to {args.labels}")


if __name__ == "__main__":
    main()
