#!/usr/bin/env python3
"""
guards.py - ccBar guard hooks. Registered by `install.py --guards`.

Usage:
  python guards.py --line-endings   PostToolUse (Edit|Write): exit 2 when the edited
                                    file's line endings no longer match git's
                                    checkout form of it

Reads the hook payload as JSON on stdin. Exits 0 on anything it doesn't
understand, so a guard never blocks work by accident.
"""

import json
import os
import subprocess
import sys

# The script's own directory is sys.path[0], and the clone always holds ccbar.py,
# so this resolves in both install modes.
import ccbar

GIT_TIMEOUT_S = 5


def count_line_endings(data):
    """Return (crlf, lf_only) counts for raw bytes."""
    crlf = data.count(b"\r\n")
    return crlf, data.count(b"\n") - crlf


def line_ending_problem(name, expected, current):
    """Return a one-line message when current mixes in the other style, else None."""
    exp_crlf, exp_lf = count_line_endings(expected)
    if exp_crlf == exp_lf:
        return None
    style, other = ("CRLF", "LF") if exp_crlf > exp_lf else ("LF", "CRLF")
    cur_crlf, cur_lf = count_line_endings(current)
    n = cur_lf if style == "CRLF" else cur_crlf
    if not n:
        return None
    return (
        f"{name}: expected {style} line endings (git checkout form), "
        f"found {n} {other} line(s). Convert them back to {style}."
    )


def _read_payload():
    return json.loads(sys.stdin.buffer.read().decode("utf-8"))


def _git(args, cwd):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def run_line_endings(payload, cfg):
    if not cfg["guards"]["line_endings"]["enabled"]:
        return 0
    path = payload.get("tool_input", {}).get("file_path")
    if not path or not os.path.isfile(path):
        return 0
    top = _git(["rev-parse", "--show-toplevel"], cwd=os.path.dirname(path))
    if top is None:
        return 0
    top = top.decode().strip()
    rel = os.path.relpath(path, top).replace(os.sep, "/")
    if rel.startswith(".."):
        return 0
    # --filters applies core.autocrlf and .gitattributes exactly as a checkout would
    expected = _git(["cat-file", "--filters", "HEAD:" + rel], cwd=top)
    if expected is None:
        return 0
    with open(path, "rb") as f:
        current = f.read()
    problem = line_ending_problem(rel, expected, current)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    return 0


def main():
    if "--line-endings" not in sys.argv[1:]:
        sys.exit(0)
    try:
        code = run_line_endings(_read_payload(), ccbar.load_config())
    except Exception as e:
        print(f"guards.py: {e}", file=sys.stderr)
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
