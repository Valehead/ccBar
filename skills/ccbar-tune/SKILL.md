---
name: ccbar-tune
version: 1.0.0
description: |
  Fit ccBar's handoff and retro thresholds to the user's own sessions. Runs
  ccbar.py --context-report over the local transcripts, recommends new
  threshold_tokens values, and edits ~/.claude/ccbar.config.json only after
  the user approves. Use when asked "should my thresholds be lower?", "the
  handoff feels early", "the handoff never fires", or "tune ccbar", or when
  ccBar's Stop hook asked and the user said yes.
license: MIT
compatibility: claude-code
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash(py:*)
  - Bash(python:*)
  - Bash(python3:*)
  - Bash(cp:*)
---

# /ccbar-tune

Recommend handoff and retro thresholds from measured session lengths, then apply them only on approval.

## 1. Run the report

Read `~/.claude/settings.json` and take `statusLine.command`. It already names the right Python and the right `ccbar.py` for either install mode (the clone, or the copy at `~/.claude/ccbar.py`). If it doesn't contain `ccbar.py`, ccBar isn't installed: say so and stop.

Run that command with ` --context-report` appended. Pass `--days N` only if the user asked for a different window (default 35).

## 2. Read the current values

Read `~/.claude/ccbar.config.json`. Current values are `handoff.threshold_tokens` and `retro.threshold_tokens`; if the file or a key is missing, the defaults apply (handoff 200000, retro 300000). The report's last lines also show them.

## 3. Recommend

- **Under 10 sessions:** there isn't enough history to fit. Say so, keep the current values, and stop. Suggest running again after more use.
- **Handoff:** the p90 peak rounded to the nearest 50,000, at least 50,000.
- **Lower it when compactions say so:** if more than half of the manual compactions happened below that handoff, the user is compacting before the handoff would ever reach them. Propose instead the median manual compaction point, rounded down to 50,000, and say that's why.
- **Retro:** handoff + 100,000.
- **Percent thresholds:** keep `threshold_pct` at 70 (handoff) and 85 (retro). The report can't model them, since transcripts don't record the context window size.

## 4. Show current against proposed

One short table: current and proposed for handoff and retro, with how many sessions each value would have fired in. The report gives counts for the current values; for proposed values (multiples of 50k) read the matching "Sessions reaching" line, or 0 above the last step. Quote the session count, median and p90, and that percentiles are nearest-rank.

If proposed equals current, say the thresholds already fit and stop.

## 5. Apply only on approval

Ask whether to apply. Anything other than a clear yes leaves the config untouched.

On yes:

1. If `~/.claude/ccbar.config.json` exists, copy it to `~/.claude/ccbar.config.json.bak` first, then Edit only `handoff.threshold_tokens` and `retro.threshold_tokens`, adding either key inside its block if it's missing. Change nothing else.
2. If the file doesn't exist, Write it with only those two values: `{"handoff": {"threshold_tokens": N}, "retro": {"threshold_tokens": M}}`. ccBar merges it over its defaults.
3. Confirm in one line what changed and where the backup is. The new values apply from the next status line refresh.
