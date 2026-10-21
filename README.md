# ccBar

A two-line ANSI status bar for [Claude Code](https://claude.ai/code), showing context window usage, model name, cost (optional), and live git status.

**Zero token cost.** ccBar runs entirely as a local subprocess. Claude never sees its input or output, so it adds no API cost and no context usage.

---

## What it shows

**Line 1** — Model name (bold) + color-coded context bar + percentage and token counts + optional cost estimate + `retro ✓` once the session retro has fired + `handoff ✓` once the session handoff has fired.

**Line 2** — Folder name + git branch + `+N/-N` line counts covering both staged and unstaged changes.

Bar colors: green ≤ 60%, yellow ≤ 80%, red > 80% (thresholds configurable).

---

## Requirements

- Python 3.8+ (standard library only, no pip packages)
- Claude Code CLI

---

## Install

```sh
cd ccBar
python install.py
```

The installer is safe to re-run after any update — just `git pull` and run it again.

What it does each time:

1. Detects the correct Python invocation (prefers the Windows `py` launcher so a missing `python` on PATH never silently breaks the bar).
2. Always copies the latest `ccbar.py` to `~/.claude/ccbar.py` (picks up script changes), unless installed in place (see below).
3. Copies every skill under `skills/` into `~/.claude/skills/`.
4. Copies `config.example.json` to `~/.claude/ccbar.config.json` on first install only — your config is preserved on re-runs so customizations are never lost.
5. Always updates the `statusLine` key in `~/.claude/settings.json`, preserving all other keys and backing the file up to `settings.json.bak` first (picks up command or format changes).
6. Runs a self-test to confirm the renderer works.

Restart Claude Code after installing or updating.

### In-place install

If your machine blocks scripts under `~/.claude`, use `--in-place`:

```sh
python install.py --in-place
```

The status line and Stop hook then run `ccbar.py` straight from the clone, so keep the clone where it is. Config and state still live in `~/.claude`. The mode is sticky: a later plain `python install.py` stays in place. Pass `--copy` to switch back. If an old `~/.claude/ccbar.py` is left over, the installer offers to remove it.

### Updating

```sh
git pull
python install.py
```

### Options

| Flag | Effect |
|------|--------|
| `--in-place` | Run `ccbar.py` from the clone instead of copying it to `~/.claude` |
| `--copy` | Copy `ccbar.py` to `~/.claude` (the default; switches back from `--in-place`) |
| `--dry-run` | Print what would change; write nothing |
| `--no-verify` | Skip self-test |
| `--reset-config` | Overwrite your config with the latest example (backs up existing first) |
| `--uninstall` | Remove `statusLine` from settings, optionally restore backup and remove files |

---

## Uninstall

```sh
python install.py --uninstall
```

---

## Configuration

Copy `config.example.json` to `~/.claude/ccbar.config.json` (the installer does this automatically). All keys are optional; missing keys fall back to built-in defaults.

### Segments

```json
"segments": {
  "model": true,
  "context_bar": true,
  "context_pct": true,
  "cost": false,
  "git_branch": true,
  "git_dirty": true,
  "git_changes": true,
  "retro": true,
  "handoff": true
}
```

### Bar appearance

```json
"bar": {
  "width": 20,
  "filled_char": "█",
  "empty_char": "░",
  "thresholds": { "green": 60, "yellow": 80 }
}
```

Switch to ASCII: set `"filled_char": "#"` and `"empty_char": "-"`.

### Colors

Valid values: `reset`, `bold`, `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`.

```json
"colors": {
  "model": "bold",
  "bar_green": "green",
  "bar_yellow": "yellow",
  "bar_red": "red",
  "cost": "cyan",
  "branch": "cyan",
  "dirty": "yellow",
  "insertions": "green",
  "deletions": "red",
  "handoff": "blue"
}
```

Set `"no_color": true` to strip all ANSI codes.

### Cost

```json
"cost": {
  "show": false,
  "decimals": 4
}
```

The cost figure is an **estimate** taken from Claude Code's own tracking. It is not your billed charge.

### Git

```json
"git": {
  "dirty_marker": "*",
  "timeout_ms": 800
}
```

- `timeout_ms`: cap on each git call, in milliseconds. A call that runs longer is dropped and its part of line 2 (branch or line counts) is left out.
- `dirty_marker`: reserved for the upcoming dirty marker. Not rendered yet.

### Icons / glyphs

```json
"icons": {
  "branch": "🌿",
  "folder": "📁",
  "dirty": null,
  "ascii_fallback": false
}
```

Set `"ascii_fallback": true` to suppress any glyph icons and use plain labels only.

---

## Session retro

Once per session, when context reaches `threshold_tokens` or `threshold_pct` of the model window (whichever comes first), the next response end triggers the `session-retro` skill. Claude reviews the session for what went wrong, what could have been done better, and what to improve next time.

**How.** The status line writes `~/.claude/ccbar-state/<session_id>.json` at the crossing. The Stop hook (`ccbar.py --stop-hook`, registered by the installer) reads it and injects the instruction once. State is per session, so concurrent sessions don't interfere. State files older than 7 days are pruned.

**Output.** A retro file in `~/.claude/retros/*.md`, plus numbered memory/CLAUDE.md proposals that are only applied on your approval. Lessons target the nearest existing CLAUDE.md; the retro never proposes creating a new one inside a git repo.

**Manual run.** `/session-retro` at any time.

Once the retro has fired, line 1 of the bar shows a `retro ✓` marker:

![ccBar status line showing the retro marker](docs/images/retro-marker.png)

```json
"retro": {
  "enabled": true,
  "threshold_tokens": 400000,
  "threshold_pct": 70
}
```

---

## Session handoff

Once per session, when context reaches the handoff's `threshold_tokens` or `threshold_pct` (whichever comes first), the next response end triggers the `session-handoff` skill. Claude writes a self-contained plan so a fresh session can pick up the work. If the retro and the handoff cross together, the retro fires first and the handoff at the next response end.

**How.** Same mechanism as the retro: the status line records the crossing in the session's state file and the Stop hook injects the instruction once.

**Output.** A plan in `plans_dir` named after the work, plus one line in chat built from `prompt_template` with `{path}` replaced by the plan's absolute path, ready to paste into a new session.

**Manual run.** `/session-handoff` at any time.

Once the handoff has fired, line 1 of the bar shows a `handoff ✓` marker.

```json
"handoff": {
  "enabled": true,
  "threshold_tokens": 600000,
  "threshold_pct": 85,
  "plans_dir": "~/.claude/plans",
  "prompt_template": "Read and execute this plan at: {path}"
}
```

---

## Self-test

```sh
python ccbar.py --selftest
# or after install:
python ~/.claude/ccbar.py --selftest
```

Feeds two mock payloads (200k and 1M context windows) through the renderer and prints the result.

Unit tests:

```sh
py -3 -m unittest discover -s tests
```

---

## Troubleshooting

**Blank status line with no error.** The most common cause is `python` not being on PATH. The installer works around this by recording the absolute interpreter path in `settings.json`. If you installed manually, check that the `statusLine` command in `~/.claude/settings.json` points to a real Python binary.

**Status line shows raw text / garbled characters.** Your terminal may not support UTF-8 block characters. Set `"filled_char": "#"`, `"empty_char": "-"`, and `"ascii_fallback": true` in the config.

**Git info missing.** Verify `git` is on PATH in the shell Claude Code inherits. On macOS the Xcode Command Line Tools may need to be installed (`xcode-select --install`).

**Status line is slow.** Each git call is capped by `git.timeout_ms` (default 800). Lower it if git is slow in a large repo; a call that times out is left off the bar instead of holding it up.

---

## License

MIT. See LICENSE.
