#!/usr/bin/env python3
"""ccBar - Claude Code status line. Reads JSON from stdin, prints two ANSI lines."""

import io
import json
import os
import subprocess
import sys
import time

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
        "show": False,
        "decimals": 4,
        # cost is an estimate, not a billed charge
    },
    "git": {
        "line_counts": False,
        "dirty_marker": "*",
        "timeout_ms": 800,
        "cache": True,
    },
    "icons": {
        "branch": "",
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
# Git cache
# ---------------------------------------------------------------------------

_CACHE_PATH = os.path.expanduser("~/.claude/.ccbar-cache.json")


def _git_mtimes(repo_root):
    head = os.path.join(repo_root, ".git", "HEAD")
    idx = os.path.join(repo_root, ".git", "index")
    try:
        mt_head = os.path.getmtime(head)
    except OSError:
        mt_head = 0
    try:
        mt_idx = os.path.getmtime(idx)
    except OSError:
        mt_idx = 0
    return mt_head, mt_idx


def _load_cache():
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data):
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _run_git(args, cwd, timeout_ms):
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _parse_porcelain_v2(output):
    staged = 0
    unstaged = 0
    is_dirty = False
    for line in output.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("? "):
            unstaged += 1
            is_dirty = True
            continue
        if line.startswith("! "):
            continue
        if line.startswith("1 ") or line.startswith("2 ") or line.startswith("u "):
            parts = line.split(" ")
            if len(parts) < 2:
                continue
            xy = parts[1]
            x = xy[0] if len(xy) > 0 else "."
            y = xy[1] if len(xy) > 1 else "."
            if x != "." and x != "?":
                staged += 1
                is_dirty = True
            if y != "." and y != "?":
                unstaged += 1
                is_dirty = True
    return is_dirty, staged, unstaged


def _parse_numstat(output):
    total = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                total += int(parts[0])
            except ValueError:
                pass
            try:
                total += int(parts[1])
            except ValueError:
                pass
    return total


def get_git_info(cwd, cfg):
    git_cfg = cfg.get("git", {})
    use_cache = git_cfg.get("cache", True)
    timeout_ms = git_cfg.get("timeout_ms", 800)
    line_counts = git_cfg.get("line_counts", False)

    empty = {"repo": None, "branch": None, "dirty": False, "staged": 0, "unstaged": 0}

    # Find repo root
    root_out = _run_git(["rev-parse", "--show-toplevel"], cwd, timeout_ms)
    if not root_out:
        return empty

    repo_root = root_out
    repo_name = os.path.basename(repo_root)

    mt_head, mt_idx = _git_mtimes(repo_root)
    cache_key = repo_root

    if use_cache:
        cache = _load_cache()
        entry = cache.get(cache_key)
        if entry:
            if entry.get("mt_head") == mt_head and entry.get("mt_idx") == mt_idx:
                return {
                    "repo": repo_name,
                    "branch": entry.get("branch"),
                    "dirty": entry.get("dirty", False),
                    "staged": entry.get("staged", 0),
                    "unstaged": entry.get("unstaged", 0),
                }
    else:
        cache = {}

    # Run git commands for fresh data
    # Get branch in same call as toplevel
    toplevel_branch = _run_git(
        ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"], cwd, timeout_ms
    )
    branch = None
    if toplevel_branch:
        lines = toplevel_branch.splitlines()
        if len(lines) >= 2:
            branch = lines[1].strip()

    # Get status
    porcelain = _run_git(
        ["status", "--porcelain=v2", "--branch"], cwd, timeout_ms
    )
    is_dirty = False
    staged = 0
    unstaged = 0
    if porcelain is not None:
        is_dirty, staged, unstaged = _parse_porcelain_v2(porcelain)

    if line_counts and is_dirty:
        unstaged_lines = _run_git(["diff", "--numstat"], cwd, timeout_ms)
        staged_lines = _run_git(["diff", "--cached", "--numstat"], cwd, timeout_ms)
        unstaged = _parse_numstat(unstaged_lines) if unstaged_lines else unstaged
        staged = _parse_numstat(staged_lines) if staged_lines else staged

    result = {
        "repo": repo_name,
        "branch": branch,
        "dirty": is_dirty,
        "staged": staged,
        "unstaged": unstaged,
    }

    if use_cache:
        cache[cache_key] = {
            "mt_head": mt_head,
            "mt_idx": mt_idx,
            "branch": branch,
            "dirty": is_dirty,
            "staged": staged,
            "unstaged": unstaged,
        }
        _save_cache(cache)

    return result


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
    line1_parts = []

    if segs.get("model", True) and model_raw:
        line1_parts.append(c(model_raw, "bold"))

    if segs.get("context_bar", True):
        bar_width = bar_cfg.get("width", 20)
        filled_char = bar_cfg.get("filled_char", "█")
        empty_char = bar_cfg.get("empty_char", "░")
        thresh = bar_cfg.get("thresholds", {})
        t_green = thresh.get("green", 60)
        t_yellow = thresh.get("yellow", 80)

        filled = int(pct / 100 * bar_width)
        filled = max(0, min(filled, bar_width))
        bar_str = filled_char * filled + empty_char * (bar_width - filled)

        if pct <= t_green:
            bar_color = colors.get("bar_green", "green")
        elif pct <= t_yellow:
            bar_color = colors.get("bar_yellow", "yellow")
        else:
            bar_color = colors.get("bar_red", "red")

        line1_parts.append("[" + c(bar_str, bar_color) + "]")

    if segs.get("context_pct", True):
        pct_text = f"{pct:.1f}% ({_fmt_num(used_tokens)} / {_fmt_num(cw_size)})"
        line1_parts.append(c(pct_text, colors.get("context_text", "reset")))

    if segs.get("cost", True) and cost_cfg.get("show", False):
        total_cost = data.get("cost", {}).get("total_cost_usd", 0) or 0
        dec = cost_cfg.get("decimals", 4)
        cost_str = f"~${total_cost:.{dec}f}"
        line1_parts.append(c(cost_str, colors.get("cost", "cyan")))

    line1 = "  ".join(line1_parts)

    # ---- build line 2 ----
    cwd = data.get("workspace", {}).get("current_dir", os.getcwd())
    git_info = get_git_info(cwd, cfg)

    line2_parts = []

    folder = git_info.get("repo") or os.path.basename(cwd)
    branch = git_info.get("branch")
    is_dirty = git_info.get("dirty", False)
    staged = git_info.get("staged", 0)
    unstaged = git_info.get("unstaged", 0)

    if folder:
        line2_parts.append(folder)

    if segs.get("git_branch", True) and branch:
        ascii_fallback = icons.get("ascii_fallback", False)
        branch_icon = icons.get("branch", "")
        if ascii_fallback or not branch_icon:
            branch_icon = ""
        else:
            branch_icon = branch_icon + " "
        line2_parts.append(c(branch_icon + branch, colors.get("branch", "cyan")))

    if segs.get("git_dirty", True) and is_dirty:
        dirty_marker = git_cfg.get("dirty_marker", "*")
        icon_dirty = icons.get("dirty")
        marker = icon_dirty if icon_dirty else dirty_marker
        line2_parts.append(c(marker, colors.get("dirty", "yellow")))

    if segs.get("git_changes", True) and (staged or unstaged):
        staged_icon = icons.get("staged", "S")
        unstaged_icon = icons.get("unstaged", "U")
        parts = []
        if staged:
            parts.append(f"{staged_icon}:{staged}")
        if unstaged:
            parts.append(f"{unstaged_icon}:{unstaged}")
        line2_parts.append(c(" ".join(parts), colors.get("changes", "reset")))

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
