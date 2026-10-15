"""Calibrate Layout against the real Hearthstone window. READ-ONLY: never
moves the mouse, never clicks. Run on the machine with Hearthstone open:

    python3 calibrate_layout.py

Compare the printed coordinates against a screenshot of your game window
(e.g. move the mouse by hand to end_turn_pos and see if it lands on the
End Turn button). If it is off by a few pixels, that is the known
window-level vs pixel-level gap -- report the offset (dx, dy) and the
detected window rect, and the reference coords in Layout can be tuned.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from hsbot.actions import Layout, _detect_window_rect


def main() -> int:
    rect = _detect_window_rect(("Hearthstone", "炉石传说"))
    lay = Layout.auto_detect()
    print(f"platform: {sys.platform}")
    print(f"detected window rect (x, y, w, h): {rect}")
    if rect is None:
        print("WARNING: no game window detected -- showing 1920x1080 fallback.")
        print("Start Hearthstone (windowed/fullscreen) and re-run.")
    print(f"board: width={lay.width} height={lay.height} "
          f"offset=({lay.offset_x}, {lay.offset_y}) "
          f"scale=({lay.sx:.4f}, {lay.sy:.4f})")
    print(f"end_turn:        {lay.end_turn_pos()}")
    print(f"hero (mine):     {lay.hero_pos(True)}")
    print(f"hero (enemy):    {lay.hero_pos(False)}")
    print(f"hero_power mine: {lay.hero_power_pos(True)}")
    print(f"mulligan_confirm: {lay.mulligan_confirm_pos()}")
    print(f"hand[0/4]:      {lay.hand_pos(0, 4)}  hand[3/4]: {lay.hand_pos(3, 4)}")
    print(f"board mine[0/3]: {lay.board_pos(True, 0, 3)}  "
          f"board enemy[1/3]: {lay.board_pos(False, 1, 3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
