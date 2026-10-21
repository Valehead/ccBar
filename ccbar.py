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
        "cost": "cyan",
        "branch": "cyan",
        "dirty": "yellow",
        "insertions": "green",
        "deletions": "red",
        "retro": "magenta",
        "handoff": "blue",
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
        "cache_ttl_ms": 3000,
    },
    "icons": {
        "branch": "🌿",
        "folder": "📁",
        "dirty": None,
        "ascii_fallback": False,
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


def _sanitize_label(text, max_len=100):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:max_len]


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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_num(n):
    return f"{int(n):,}"


def context_tokens(data):
    cw = data.get("context_window") or {}
    size = cw.get("context_window_size") or 200000
    used = cw.get("total_input_tokens") or 0
    if not used:
        used = int((cw.get("used_percentage") or 0) / 100 * size)
    return used, size


def _workspace_dir(data):
    return data.get("workspace", {}).get("current_dir", os.getcwd())


def render(data, cfg, git_info=None, events=None):
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
        line1_parts.append(c(f"[{model_raw}]", colors.get("model", "bold")))

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

    for name in EVENTS:
        if segs.get(name, True) and (events or {}).get(name, {}).get("fired"):
            label = f"{name} done" if icons.get("ascii_fallback", False) else f"{name} ✓"
            line1 += " | " + c(label, colors.get(name))

    # ---- build line 2 ----
    cwd = _workspace_dir(data)
    git_info = git_info or {}

    line2_parts = []

    folder = os.path.basename(cwd)
    branch = _sanitize_label(git_info.get("branch", ""))
    ins = git_info.get("insertions", 0)
    dels = git_info.get("deletions", 0)
    ascii_fallback = icons.get("ascii_fallback", False)

    if folder:
        folder_icon = icons.get("folder", "📁")
        folder_prefix = "" if (ascii_fallback or not folder_icon) else folder_icon + " "
        line2_parts.append(folder_prefix + folder)

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
        line2_parts.append(branch_part)

    if segs.get("git_changes", True) and (ins or dels):
        parts = []
        if ins:
            parts.append(c(f"+{ins}", colors.get("insertions", "green")))
        if dels:
            parts.append(c(f"-{dels}", colors.get("deletions", "red")))
        line2_parts.append("/".join(parts))

    line2 = "  ".join(line2_parts)

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
    cutoff = (now or time.time()) - STATE_MAX_AGE_DAYS * 86400
    for name in os.listdir(STATE_DIR):
        path = os.path.join(STATE_DIR, name)
        if os.path.getmtime(path) < cutoff:
            os.remove(path)


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
    prune_state()
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
}


def selftest():
    cfg = load_config()
    ok = True
    for label, mock in [("200k window", MOCK_200K), ("1M window", MOCK_1M)]:
        out = render(mock, cfg, git_info=get_git_info(cfg, _workspace_dir(mock)))
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

    try:
        data = json.loads(sys.stdin.read())
        cfg = load_config()
        state = update_event_state(data, cfg)
        git_info = cached_git_info(data, cfg, state)
        print(render(data, cfg, git_info=git_info, events=state.get("events", {})))
    except Exception:
        print("\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
