#!/usr/bin/env python3
"""ccBar - Claude Code status line. Reads JSON from stdin, prints two ANSI lines."""

import io
import json
import os
import re
import subprocess
import sys

# Force UTF-8 output on Windows where the default codepage may reject block chars
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS = {
    "segments": {
        "model": True,
        "context_bar": True,
        "context_pct": True,
        "cost": True,
        "git_branch": True,
        "git_dirty": True,
        "git_changes": True,
    },
    "bar": {
        "width": 20,
        "filled_char": "█",
        "empty_char": "░",
        "thresholds": {"green": 60, "yellow": 80},
    },
    "colors": {
        "model": "bold",
        "bar_green": "green",
        "bar_yellow": "yellow",
        "bar_red": "red",
        "context_text": "reset",
        "cost": "cyan",
        "branch": "cyan",
        "dirty": "yellow",
        "changes": "reset",
    },
    "no_color": False,
    "cost": {
        "show": True,
        "decimals": 4,
        # cost is an estimate, not a billed charge
    },
    "git": {
        "dirty_marker": "*",
        "timeout_ms": 800,
    },
    "icons": {
        "branch": "🌿",
        "folder": "📁",
        "dirty": None,
        "staged": "S",
        "unstaged": "U",
        "ascii_fallback": False,
    },
}

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "black": "\x1b[30m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "white": "\x1b[37m",
}


def _color(text, *codes, cfg=None):
    if cfg and cfg.get("no_color"):
        return text
    parts = []
    for c in codes:
        if c and c in _ANSI:
            parts.append(_ANSI[c])
    if not parts:
        return text
    return "".join(parts) + text + _ANSI["reset"]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config():
    cfg_path = os.path.expanduser("~/.claude/ccbar.config.json")
    cfg = _deep_merge({}, DEFAULTS)
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg = _deep_merge(cfg, user)
        except Exception:
            pass
    return cfg


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args, timeout_ms):
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _parse_shortstat(output):
    ins = dels = 0
    if output:
        m = re.search(r'(\d+) insertion', output)
        if m:
            ins = int(m.group(1))
        m = re.search(r'(\d+) deletion', output)
        if m:
            dels = int(m.group(1))
    return ins, dels


def get_git_info(cfg):
    timeout_ms = cfg.get("git", {}).get("timeout_ms", 800)
    branch = _run_git(["branch", "--show-current"], timeout_ms) or ""
    shortstat = _run_git(["diff", "--shortstat"], timeout_ms) or ""
    ins, dels = _parse_shortstat(shortstat)
    return {"branch": branch, "insertions": ins, "deletions": dels}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_num(n):
    return f"{int(n):,}"


def render(data, cfg):
    segs = cfg.get("segments", {})
    colors = cfg.get("colors", {})
    bar_cfg = cfg.get("bar", {})
    cost_cfg = cfg.get("cost", {})
    git_cfg = cfg.get("git", {})
    icons = cfg.get("icons", {})

    no_color = cfg.get("no_color", False)

    def c(text, *codes):
        return _color(text, *codes, cfg=cfg)

    # ---- context window data ----
    cw = data.get("context_window", {})
    usage = data.get("current_usage", {})
    cw_size = data.get("context_window_size") or cw.get("context_window_size") or 200000

    inp = usage.get("input_tokens", 0) or 0
    cache_create = usage.get("cache_creation_input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    used_tokens = inp + cache_create + cache_read

    if used_tokens == 0:
        pct_raw = cw.get("used_percentage", 0) or 0
        used_tokens = int(pct_raw / 100 * cw_size)
        pct = pct_raw
    else:
        pct = (used_tokens / cw_size * 100) if cw_size else 0

    # ---- model ----
    model_raw = data.get("model", {}).get("display_name", "")
    # strip "(... context)" suffix
    if "(" in model_raw:
        model_raw = model_raw[: model_raw.rfind("(")].strip()

    # ---- build line 1 ----
    thresh = bar_cfg.get("thresholds", {})
    t_green = thresh.get("green", 60)
    t_yellow = thresh.get("yellow", 80)
    if pct <= t_green:
        bar_color = colors.get("bar_green", "green")
    elif pct <= t_yellow:
        bar_color = colors.get("bar_yellow", "yellow")
    else:
        bar_color = colors.get("bar_red", "red")

    line1_parts = []

    if segs.get("model", True) and model_raw:
        line1_parts.append(c(f"[{model_raw}]", "bold"))

    if segs.get("context_bar", True):
        bar_width = bar_cfg.get("width", 20)
        filled_char = bar_cfg.get("filled_char", "█")
        empty_char = bar_cfg.get("empty_char", "░")

        filled = int(pct / 100 * bar_width)
        filled = max(0, min(filled, bar_width))
        bar_str = filled_char * filled + empty_char * (bar_width - filled)

        line1_parts.append(c(bar_str, bar_color))

    if segs.get("context_pct", True):
        pct_text = f"{pct:.1f}% ({_fmt_num(used_tokens)} / {_fmt_num(cw_size)})"
        line1_parts.append(c(pct_text, bar_color))

    line1 = "  ".join(line1_parts)

    if segs.get("cost", True) and cost_cfg.get("show", True):
        total_cost = data.get("cost", {}).get("total_cost_usd", 0) or 0
        dec = cost_cfg.get("decimals", 4)
        cost_str = f"${total_cost:.{dec}f}"
        line1 += " | " + c(cost_str, colors.get("cost", "cyan"))

    # ---- build line 2 ----
    cwd = data.get("workspace", {}).get("current_dir", os.getcwd())
    git_info = get_git_info(cfg)

    line2_parts = []

    folder = os.path.basename(cwd)
    branch = git_info.get("branch", "")
    ins = git_info.get("insertions", 0)
    dels = git_info.get("deletions", 0)
    is_dirty = bool(ins or dels)

    if folder:
        ascii_fallback = icons.get("ascii_fallback", False)
        folder_icon = icons.get("folder", "📁")
        folder_prefix = "" if (ascii_fallback or not folder_icon) else folder_icon + " "
        line2_parts.append(folder_prefix + folder)

    if segs.get("git_branch", True) and branch:
        ascii_fallback = icons.get("ascii_fallback", False)
        branch_icon = icons.get("branch", "🌿")
        if ascii_fallback or not branch_icon:
            branch_icon = ""
        else:
            branch_icon = branch_icon + " "
        line2_parts.append(c(branch_icon + branch, colors.get("branch", "cyan")))

    if segs.get("git_changes", True) and (ins or dels):
        parts = []
        if ins:
            parts.append(c(f"+{ins}", "green"))
        if dels:
            parts.append(c(f"-{dels}", "red"))
        line2_parts.append("/".join(parts))

    line2 = "  ".join(line2_parts)

    return line1 + "\n" + line2


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

MOCK_200K = {
    "model": {"display_name": "Claude Sonnet 4.6 (200k context)"},
    "workspace": {"current_dir": os.path.expanduser("~")},
    "cost": {"total_cost_usd": 0.0312},
    "context_window": {"used_percentage": 45.2},
    "current_usage": {
        "input_tokens": 45000,
        "cache_creation_input_tokens": 12000,
        "cache_read_input_tokens": 33000,
    },
    "context_window_size": 200000,
}

MOCK_1M = {
    "model": {"display_name": "Claude Opus 4.8 (1M context)"},
    "workspace": {"current_dir": os.path.expanduser("~")},
    "cost": {"total_cost_usd": 1.2345},
    "context_window": {"used_percentage": 82.1},
    "current_usage": {
        "input_tokens": 500000,
        "cache_creation_input_tokens": 200000,
        "cache_read_input_tokens": 121000,
    },
    "context_window_size": 1000000,
}


def selftest():
    cfg = load_config()
    ok = True
    for label, mock in [("200k window", MOCK_200K), ("1M window", MOCK_1M)]:
        out = render(mock, cfg)
        if not out.strip():
            print(f"FAIL: empty render for {label}", file=sys.stderr)
            ok = False
        else:
            print(f"--- {label} ---")
            print(out)
            print()
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)

    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        cfg = load_config()
        print(render(data, cfg))
    except Exception:
        print("\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
