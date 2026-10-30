#!/usr/bin/env python3
"""ccBar - Claude Code status line. Reads JSON from stdin, prints two ANSI lines."""

import copy
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback

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
        "retro": True,
        "handoff": True,
        "effort": True,
        "worktree": True,
        "rate_limits": True,
        "prompt_cache": True,
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
        "bar_empty": None,
        "cost": "cyan",
        "branch": "cyan",
        "dirty": "yellow",
        "insertions": "green",
        "deletions": "red",
        "retro": "magenta",
        "handoff": "blue",
        "worktree": "cyan",
        "cache_warm": "green",
        "cache_cold": "yellow",
    },
    "no_color": False,
    "cost": {
        "show": True,
        "decimals": 4,
        "thresholds": {"yellow": 10.0, "red": 30.0},
        # cost is an estimate, not a billed charge
    },
    "git": {
        "dirty_marker": "*",
        "timeout_ms": 800,
        "cache_ttl_ms": 3000,
    },
    "icons": {
        "branch": "🌿",
        "folder": "📁",
        "dirty": None,
        "worktree": "⎇",
        "ascii_fallback": False,
    },
    "rate_limits": {
        "show_reset": True,
    },
    "retro": {
        "enabled": True,
        "threshold_tokens": 400000,
        "threshold_pct": 70,
    },
    "handoff": {
        "enabled": True,
        "threshold_tokens": 600000,
        "threshold_pct": 85,
        "plans_dir": "~/.claude/plans",
        "prompt_template": "Read and execute this plan at: {path}",
    },
    "guards": {
        "line_endings": {"enabled": True},
        "posted_text": {"enabled": True, "banned": []},
        "build_gate": {"enabled": True, "builds": []},
    },
}

RATE_WINDOWS = (("5h", "five_hour"),)
BRANCH_MAX_LEN = 30

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
    "gray": "\x1b[90m",
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


def _sanitize_label(text, max_len=100):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:max_len]


def _truncate(text, max_len, ellipsis):
    if len(text) <= max_len:
        return text
    return text[: max_len - len(ellipsis)] + ellipsis


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

def _run_git(args, timeout_ms, cwd):
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
            cwd=cwd,
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


def get_git_info(cfg, cwd):
    timeout_ms = cfg.get("git", {}).get("timeout_ms", 800)
    branch = _run_git(["branch", "--show-current"], timeout_ms, cwd) or ""
    shortstat = _run_git(["diff", "--shortstat", "HEAD"], timeout_ms, cwd) or ""
    porcelain = _run_git(["--no-optional-locks", "status", "--porcelain"], timeout_ms, cwd) or ""
    ins, dels = _parse_shortstat(shortstat)
    return {"branch": branch, "insertions": ins, "deletions": dels, "dirty": bool(porcelain)}


def _main_repo_name(git_dir):
    # A linked worktree's git dir is <main>/.git/worktrees/<name>; anything else has no main repo to name
    if not git_dir:
        return ""
    worktrees = os.path.dirname(os.path.normpath(git_dir))
    dot_git = os.path.dirname(worktrees)
    if os.path.basename(worktrees) != "worktrees" or os.path.basename(dot_git) != ".git":
        return ""
    return os.path.basename(os.path.dirname(dot_git))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_num(n):
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    thousands = round(n / 1000)
    if thousands < 1000:
        return f"{thousands}k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def context_tokens(data):
    cw = data.get("context_window") or {}
    size = cw.get("context_window_size") or 200000
    used = cw.get("total_input_tokens") or 0
    if not used:
        used = int((cw.get("used_percentage") or 0) / 100 * size)
    return used, size


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pct_color(pct, cfg):
    colors = cfg.get("colors", {})
    thresh = cfg.get("bar", {}).get("thresholds", {})
    if pct <= thresh.get("green", 60):
        return colors.get("bar_green", "green")
    if pct <= thresh.get("yellow", 80):
        return colors.get("bar_yellow", "yellow")
    return colors.get("bar_red", "red")


def _cost_color(total, cfg):
    colors = cfg.get("colors", {})
    thresh = cfg.get("cost", {}).get("thresholds", {})
    if total >= thresh.get("red", 30.0):
        return colors.get("bar_red", "red")
    if total >= thresh.get("yellow", 10.0):
        return colors.get("bar_yellow", "yellow")
    return colors.get("cost", "cyan")


def _fmt_duration(seconds):
    if 0 < seconds < 60:
        return "<1m"
    minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def _rate_limits_text(data, cfg, now, c):
    limits = _as_dict(data.get("rate_limits"))
    show_reset = cfg.get("rate_limits", {}).get("show_reset", True)
    parts = []
    for label, key in RATE_WINDOWS:
        window = _as_dict(limits.get(key))
        pct = window.get("used_percentage")
        if not _is_number(pct):
            continue
        text = f"{label} {pct:.0f}%"
        resets_at = window.get("resets_at")
        if show_reset and now is not None and _is_number(resets_at) and resets_at - now > 0:
            text += f" ({_fmt_duration(resets_at - now)})"
        parts.append(c(text, _pct_color(pct, cfg)))
    return "  ".join(parts)


def _prompt_cache_text(data, cfg, now, c):
    cache = _as_dict(data.get("prompt_cache"))
    if not cache or cache.get("caching_observed") is False:
        return ""
    colors = cfg.get("colors", {})
    parts = ["cache"]
    ratio = cache.get("hit_ratio")
    if _is_number(ratio):
        parts.append(f"{ratio * 100:.0f}%")
    if cache.get("warm") is True:
        parts.append("warm")
        expires_at = cache.get("expires_at")
        if now is not None and _is_number(expires_at) and expires_at - now > 0:
            parts.append(_fmt_duration(expires_at - now))
        return c(" ".join(parts), colors.get("cache_warm", "green"))
    parts.append("cold")
    return c(" ".join(parts), colors.get("cache_cold", "yellow"))


def _workspace_dir(data):
    return data.get("workspace", {}).get("current_dir", os.getcwd())


def _worktree_short_name(name, repo):
    prefix = repo + "-"
    if len(name) > len(prefix) and name[: len(prefix)].lower() == prefix.lower():
        return name[len(prefix):]
    return name


def render(data, cfg, git_info=None, events=None, now=None):
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
    used_tokens, cw_size = context_tokens(data)
    pct = (used_tokens / cw_size * 100) if cw_size else 0

    # ---- model ----
    model_raw = data.get("model", {}).get("display_name", "")
    # strip "(... context)" suffix
    if "(" in model_raw:
        model_raw = model_raw[: model_raw.rfind("(")].strip()

    ascii_fallback = icons.get("ascii_fallback", False)

    effort = _as_dict(data.get("effort")).get("level")
    if segs.get("effort", True) and model_raw and isinstance(effort, str) and effort:
        sep = " - " if ascii_fallback else " · "
        model_raw = f"{model_raw}{sep}{_sanitize_label(effort, 10)}"

    # ---- build line 1 ----
    bar_color = _pct_color(pct, cfg)

    line1_parts = []

    if segs.get("model", True) and model_raw:
        line1_parts.append(c(f"[{model_raw}]", colors.get("model", "bold")))

    if segs.get("context_bar", True):
        bar_width = bar_cfg.get("width", 20)
        filled_char = bar_cfg.get("filled_char", "█")
        empty_char = bar_cfg.get("empty_char", "░")

        filled = int(pct / 100 * bar_width)
        filled = max(0, min(filled, bar_width))
        empty_color = colors.get("bar_empty")
        if empty_color:
            line1_parts.append(c(filled_char * filled, bar_color) + c(empty_char * (bar_width - filled), empty_color))
        else:
            line1_parts.append(c(filled_char * filled + empty_char * (bar_width - filled), bar_color))

    if segs.get("context_pct", True):
        pct_text = f"{pct:.1f}% ({_fmt_num(used_tokens)} / {_fmt_num(cw_size)})"
        line1_parts.append(c(pct_text, bar_color))

    line1 = "  ".join(line1_parts)

    if segs.get("cost", True) and cost_cfg.get("show", True):
        total_cost = data.get("cost", {}).get("total_cost_usd", 0) or 0
        dec = cost_cfg.get("decimals", 4)
        cost_str = f"${total_cost:.{dec}f}"
        line1 += " | " + c(cost_str, _cost_color(total_cost, cfg))

    for name in EVENTS:
        if segs.get(name, True) and (events or {}).get(name, {}).get("fired"):
            label = f"{name} done" if icons.get("ascii_fallback", False) else f"{name} ✓"
            line1 += " | " + c(label, colors.get(name))

    # ---- build line 2 ----
    cwd = _workspace_dir(data)
    git_info = git_info or {}

    folder = os.path.basename(cwd)
    branch = _truncate(_sanitize_label(git_info.get("branch", "")), BRANCH_MAX_LEN, "..." if ascii_fallback else "…")
    ins = git_info.get("insertions", 0)
    dels = git_info.get("deletions", 0)

    folder_icon = icons.get("folder", "📁")
    folder_prefix = "" if (ascii_fallback or not folder_icon) else folder_icon + " "
    location = folder_prefix + folder if folder else ""

    worktree = _as_dict(data.get("worktree")).get("name") or data.get("workspace", {}).get("git_worktree")
    if segs.get("worktree", True) and isinstance(worktree, str) and worktree:
        repo = git_info.get("repo")
        if repo:
            location = folder_prefix + repo
            worktree = _worktree_short_name(worktree, repo)
        worktree_icon = icons.get("worktree", "⎇")
        worktree_prefix = "wt " if (ascii_fallback or not worktree_icon) else worktree_icon + " "
        marker = c(worktree_prefix + _sanitize_label(worktree, 40), colors.get("worktree", "cyan"))
        location = f"{location} {marker}" if location else marker

    git_parts = []

    branch_part = ""
    if segs.get("git_branch", True) and branch:
        branch_icon = icons.get("branch", "🌿")
        branch_prefix = "" if (ascii_fallback or not branch_icon) else branch_icon + " "
        branch_part = c(branch_prefix + branch, colors.get("branch", "cyan"))

    if segs.get("git_dirty", True) and git_info.get("dirty"):
        marker = icons.get("dirty")
        if ascii_fallback or not marker:
            marker = git_cfg.get("dirty_marker", "*")
        branch_part += c(marker, colors.get("dirty", "yellow"))

    if branch_part:
        git_parts.append(branch_part)

    if segs.get("git_changes", True) and (ins or dels):
        parts = []
        if ins:
            parts.append(c(f"+{ins}", colors.get("insertions", "green")))
        if dels:
            parts.append(c(f"-{dels}", colors.get("deletions", "red")))
        git_parts.append("/".join(parts))

    rate_text = _rate_limits_text(data, cfg, now, c) if segs.get("rate_limits", True) else ""
    cache_text = _prompt_cache_text(data, cfg, now, c) if segs.get("prompt_cache", True) else ""

    groups = (location, "  ".join(git_parts), rate_text, cache_text)
    line2 = " | ".join(g for g in groups if g)

    return line1 + "\n" + line2


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------

STATE_DIR = os.path.expanduser("~/.claude/ccbar-state")
STATE_MAX_AGE_DAYS = 7
EVENT_SKILLS = {"retro": "session-retro", "handoff": "session-handoff"}
EVENTS = ("retro", "handoff")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def threshold_crossed(tokens, window_size, event_cfg):
    if not event_cfg.get("enabled", True):
        return False
    if tokens >= event_cfg.get("threshold_tokens", 400000):
        return True
    pct = (tokens / window_size * 100) if window_size else 0
    return pct >= event_cfg.get("threshold_pct", 70)


def _event_context(name, event, payload, cfg):
    context = (
        f"This session's context passed {event.get('tokens', 0):,} tokens. "
        f"Before doing anything else, invoke the {EVENT_SKILLS[name]} skill with the Skill tool, "
        f"passing args: session_id={payload.get('session_id', '')} "
        f"transcript_path={payload.get('transcript_path', '')} cwd={payload.get('cwd', '')}"
    )
    if name == "handoff":
        handoff_cfg = cfg.get("handoff", {})
        plans_dir = os.path.abspath(os.path.expanduser(handoff_cfg.get("plans_dir", "~/.claude/plans")))
        template = handoff_cfg.get("prompt_template", "Read and execute this plan at: {path}")
        context += f' plans_dir={plans_dir} prompt_template="{template}"'
    return context


def stop_hook_output(payload, state, cfg):
    if payload.get("stop_hook_active"):
        return None
    events = state.get("events", {})

    def due(name):
        event = events.get(name, {})
        return cfg.get(name, {}).get("enabled", True) and event.get("crossed") and not event.get("fired")

    name = next((n for n in EVENTS if due(n)), None)
    if name is None:
        return None
    context = _event_context(name, events[name], payload, cfg)
    return name, {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}


def _state_path(session_id):
    return os.path.join(STATE_DIR, f"{session_id}.json")


def _safe_session_id(value):
    if isinstance(value, str) and _SESSION_ID_RE.match(value):
        return value
    return None


def _from_legacy(state):
    retro = {
        "crossed": state["crossed"],
        "fired": state.get("retro_fired", False),
        "tokens": state.get("tokens", 0),
    }
    return {"window_size": state.get("window_size"), "events": {"retro": retro}}


def load_state(session_id):
    path = _state_path(session_id)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if "events" not in state and "crossed" in state:
        return _from_legacy(state)
    return state


def save_state(session_id, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(session_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    # Atomic swap: the status line and the Stop hook can touch this file at the same moment
    os.replace(tmp, path)


def update_state(session_id, change):
    loaded = load_state(session_id)
    state = copy.deepcopy(loaded)
    change(state)
    if state != loaded:
        save_state(session_id, state)
    return state


def prune_state(now=None):
    if not os.path.isdir(STATE_DIR):
        return
    cutoff = (now or time.time()) - STATE_MAX_AGE_DAYS * 86400
    for name in os.listdir(STATE_DIR):
        path = os.path.join(STATE_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except FileNotFoundError:
            # Another session's Stop hook pruned it first
            pass


def update_event_state(data, cfg):
    session_id = _safe_session_id(data.get("session_id"))
    if not session_id:
        return {}
    tokens, size = context_tokens(data)

    def record_crossings(state):
        for name in EVENTS:
            if state.get("events", {}).get(name, {}).get("crossed"):
                continue
            if threshold_crossed(tokens, size, cfg.get(name, {})):
                state.setdefault("events", {})[name] = {"crossed": True, "fired": False, "tokens": tokens}
                state["window_size"] = size

    return update_state(session_id, record_crossings)


def _head_log_mtime(git_dir):
    if not git_dir:
        return None
    try:
        return os.path.getmtime(os.path.join(git_dir, "logs", "HEAD"))
    except OSError:
        # No commits yet: the reflog doesn't exist until the first one
        return None


def cached_git_info(data, cfg, state, now=None):
    now = now or time.time()
    cwd = _workspace_dir(data)
    session_id = _safe_session_id(data.get("session_id"))
    if not session_id:
        return get_git_info(cfg, cwd)

    git_cfg = cfg.get("git", {})
    cached = state.get("git")
    fresh = (
        cached is not None
        and cached.get("cwd") == cwd
        and now - cached.get("ts", 0) < git_cfg.get("cache_ttl_ms", 3000) / 1000
    )
    if fresh and cached.get("dirty"):
        # A commit clears the tree before the TTL runs out; the HEAD reflog mtime catches it without git
        fresh = _head_log_mtime(cached.get("git_dir")) == cached.get("head_log_mtime")
    if fresh:
        return cached

    info = get_git_info(cfg, cwd)
    git_dir = _run_git(["rev-parse", "--absolute-git-dir"], git_cfg.get("timeout_ms", 800), cwd)
    info = {**info, "repo": _main_repo_name(git_dir)}
    entry = {**info, "ts": now, "cwd": cwd, "git_dir": git_dir, "head_log_mtime": _head_log_mtime(git_dir)}

    def store(state):
        state["git"] = entry

    update_state(session_id, store)
    return info


def run_stop_hook():
    payload = json.loads(sys.stdin.read())
    session_id = _safe_session_id(payload.get("session_id"))
    if not session_id:
        return
    prune_state()
    cfg = load_config()
    state = load_state(session_id)
    result = stop_hook_output(payload, state, cfg)
    if result is None:
        return
    name, out = result

    def mark_fired(state):
        state["events"][name]["fired"] = True

    # Saved before printing so a crash after the print can't fire the event twice
    update_state(session_id, mark_fired)
    print(json.dumps(out))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

MOCK_200K = {
    "session_id": "selftest-200k",
    "model": {"display_name": "Claude Sonnet 4.6 (200k context)"},
    "workspace": {"current_dir": os.path.expanduser("~")},
    "cost": {"total_cost_usd": 0.0312},
    "context_window": {
        "total_input_tokens": 90000,
        "context_window_size": 200000,
        "used_percentage": 45.0,
        "current_usage": {
            "input_tokens": 45000,
            "cache_creation_input_tokens": 12000,
            "cache_read_input_tokens": 33000,
        },
    },
}

MOCK_1M = {
    "session_id": "selftest-1m",
    "model": {"display_name": "Claude Opus 4.8 (1M context)"},
    "workspace": {"current_dir": os.path.expanduser("~")},
    "cost": {"total_cost_usd": 1.2345},
    "context_window": {
        "total_input_tokens": 821000,
        "context_window_size": 1000000,
        "used_percentage": 82.1,
        "current_usage": {
            "input_tokens": 500000,
            "cache_creation_input_tokens": 200000,
            "cache_read_input_tokens": 121000,
        },
    },
    "effort": {"level": "high"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23},
        "seven_day": {"used_percentage": 41},
    },
}


def selftest():
    cfg = load_config()
    ok = True
    now = int(time.time())
    mock_1m = copy.deepcopy(MOCK_1M)
    mock_1m["rate_limits"]["five_hour"]["resets_at"] = now + 7800
    mock_1m["prompt_cache"] = {"caching_observed": True, "warm": True, "hit_ratio": 0.92, "expires_at": now + 180}
    for label, mock in [("200k window", MOCK_200K), ("1M window", mock_1m)]:
        out = render(mock, cfg, git_info=get_git_info(cfg, _workspace_dir(mock)), now=now)
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

def _log_render_error(raw):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(os.path.join(STATE_DIR, "last-error.txt"), "w", encoding="utf-8") as f:
            f.write(stamp + "\n" + traceback.format_exc())
        if raw is not None:
            with open(os.path.join(STATE_DIR, "last-error-input.json"), "w", encoding="utf-8") as f:
                f.write(raw)
    except OSError:
        # The bar must stay blank, not broken, even when the state dir is unwritable
        pass


def main():
    # Force UTF-8 output on Windows where the default codepage may reject block chars
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)

    if "--stop-hook" in sys.argv:
        try:
            run_stop_hook()
        except Exception as e:
            print(f"ccbar --stop-hook failed: {e!r}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    raw = None
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        cfg = load_config()
        state = update_event_state(data, cfg)
        git_info = cached_git_info(data, cfg, state)
        print(render(data, cfg, git_info=git_info, events=state.get("events", {}), now=time.time()))
    except Exception:
        _log_render_error(raw)
        print("\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
