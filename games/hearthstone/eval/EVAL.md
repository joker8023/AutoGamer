# Offline A/B eval

Scores an engine version against hand-labelled positions. Purely
offline — no model calls, no game client.

## Files

- `positions.jsonl` — frozen positions. One JSON object per line:
  `id`, `deck_id`, `source`, `state` (`state_to_dict` output), `menu`
  (legal-action reprs at seed time), `db_extra_cards` (extra cards the
  position's DB needs), `db_overrides` (per-card effect overrides).
  Positions are self-contained: the eval harness rebuilds the exact DB
  the position was seeded with.
- `labels.json` — `{position_id: {best: [repr], ok: [repr], note}}`.
  `best` = first actions of a correct line; `ok` = acceptable but
  suboptimal. Labels are the author's judgments, not the engine's output.
- `seed_positions.py` — regenerates `positions.jsonl` + `labels.json`
  from the sample log + hand-written edge positions. Re-run after
  changing the seed set.
- `eval_score.py` — scores one engine version (1 / 0.5 / 0).
- `eval_label.py` — interactive helper for labelling new positions.

## Run

```bash
cd games/hearthstone
python3 eval/seed_positions.py          # regenerate seed data (optional)
python3 -m eval.eval_score --engine-version v1
```

## A/B a change

1. Add the new engine version to `hsbot/engine_version.py` (never
   silently fall back: unknown versions raise).
2. Score both versions; diff the totals and the per-position rows.
3. A position listed as "blocked" means the current engine's legal menu
   contains no best action — a rule removed every best action, i.e. the
   labels disagree with the rules. Fix the rule, not the label, unless
   the label itself was wrong (record the reason in the verdict).

## Adding a position

1. Build the `GameState` in `seed_positions.py` (or capture one from a
   replay), add its `db_extra_cards` / `db_overrides` if the position
   needs modeled cards the base DB lacks.
2. Label it with `eval_label.py` or by editing `labels.json` directly.
3. Re-run `eval_score.py` for every registered engine version.

## Verdict records

When a rule change is judged against the labels, append a short entry
to the project notes (date + position id + conclusion + what changed).
Keep it short: one line per verdict.
