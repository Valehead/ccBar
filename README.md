# ccBar

A two-line ANSI status bar for [Claude Code](https://claude.ai/code), showing context window usage, model name, cost (optional), and live git status.

**Zero token cost.** ccBar runs entirely as a local subprocess. Claude never sees its input or output, so it adds no API cost and no context usage.

---

## What it shows

**Line 1** — Model name (bold) + color-coded context bar + percentage and token counts + optional cost estimate.

**Line 2** — Repo/folder name + git branch + dirty marker + staged and unstaged change counts.

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

The installer:

1. Detects the correct Python invocation (prefers the Windows `py` launcher so a missing `python` on PATH never silently breaks the bar).
2. Copies `ccbar.py` to `~/.claude/ccbar.py`.
3. Copies `config.example.json` to `~/.claude/ccbar.config.json` (only if one doesn't already exist).
4. Merges the `statusLine` key into `~/.claude/settings.json`, preserving all other keys and backing the file up to `settings.json.bak` first.
5. Runs a self-test to confirm the renderer works.

Restart Claude Code after installing.

### Options

| Flag | Effect |
|------|--------|
| `--dry-run` | Print what would change; write nothing |
| `--no-verify` | Skip self-test |
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
  "git_changes": true
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
  "context_text": "reset",
  "cost": "cyan",
  "branch": "cyan",
  "dirty": "yellow",
  "changes": "reset"
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
  "line_counts": false,
  "dirty_marker": "*",
  "timeout_ms": 800,
  "cache": true
}
```

- `line_counts`: switch staged/unstaged counts from file counts to line counts (runs two extra `git diff --numstat` commands when the cache is stale).
- `cache`: skip git subprocesses entirely when `.git/HEAD` and `.git/index` haven't changed since the last refresh. Strongly recommended.

### Icons / glyphs

```json
"icons": {
  "branch": "",
  "dirty": null,
  "staged": "S",
  "unstaged": "U",
  "ascii_fallback": false
}
```

Set `"ascii_fallback": true` to suppress any glyph icons and use plain labels only.

---

## Self-test

```sh
python ccbar.py --selftest
# or after install:
python ~/.claude/ccbar.py --selftest
```

Feeds two mock payloads (200k and 1M context windows) through the renderer and prints the result.

---

## Troubleshooting

**Blank status line with no error.** The most common cause is `python` not being on PATH. The installer works around this by recording the absolute interpreter path in `settings.json`. If you installed manually, check that the `statusLine` command in `~/.claude/settings.json` points to a real Python binary.

**Status line shows raw text / garbled characters.** Your terminal may not support UTF-8 block characters. Set `"filled_char": "#"`, `"empty_char": "-"`, and `"ascii_fallback": true` in the config.

**Git info missing.** Verify `git` is on PATH in the shell Claude Code inherits. On macOS the Xcode Command Line Tools may need to be installed (`xcode-select --install`).

**Status line is slow.** Set `"cache": true` in the `git` section (it is the default). The cache key is the repo root + mtime of `.git/HEAD` and `.git/index`, so git commands only run when something actually changes.

---

## License

MIT. See LICENSE.
