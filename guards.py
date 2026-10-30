#!/usr/bin/env python3
"""
guards.py - ccBar guard hooks. Registered by `install.py --guards`.

Usage:
  python guards.py --line-endings   PostToolUse (Edit|Write): exit 2 when the edited
                                    file's line endings no longer match git's
                                    checkout form of it
  python guards.py --posted-text    PreToolUse (Bash|PowerShell): exit 2 when a gh/az
                                    command that posts text, or a body file it
                                    references, contains a guards.posted_text.banned
                                    entry
  python guards.py --build-gate     PreToolUse (Bash|PowerShell): before git push or
                                    gh pr create, build the project roots the unpushed
                                    commits touched; exit 2 on a failed build

Reads the hook payload as JSON on stdin. Exits 0 on anything it doesn't
understand, so a guard never blocks work by accident.
"""

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

# The script's own directory is sys.path[0], and the clone always holds ccbar.py,
# so this resolves in both install modes.
import ccbar

GIT_TIMEOUT_S = 5
POSTING_RE = re.compile(
    r"\bgh\s+(?:pr|issue)\s+(?:comment|review|create|edit)\b"
    r"|\bgh\s+api\b.*?/(?:reviews|comments)\b"
    r"|\baz\s+repos\s+pr\s+(?:create|update)\b"
    r"|\baz\s+boards\s+work-item\s+(?:create|update)\b"
    r"|\baz\s+devops\s+invoke\b.*?--resource[\s=]+\S*(?i:threads|comments)"
    r"|\baz\s+rest\b.*?/(?:threads|comments)\b",
    re.S,
)
FILE_FLAGS = ("--body-file", "--input", "--in-file")
BUILD_TIMEOUT_S = 840
BUILD_OUTPUT_LINES = 40
PUSH_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+push\b|\bgh\s+pr\s+create\b")


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


def is_posting_command(command):
    return POSTING_RE.search(command) is not None


def find_banned(text, banned):
    """Return the first non-empty banned entry found in text, else None."""
    return next((b for b in banned if isinstance(b, str) and b and b in text), None)


def _unquote(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def referenced_files(command, posix):
    """Return the body file paths a posting command names. Touches no files."""
    try:
        tokens = shlex.split(command, posix=posix)
    except ValueError:
        return []
    if not posix:
        tokens = [_unquote(t) for t in tokens]
    paths = []
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        # gh pr/issue -F is --body-file; gh api -F takes key=value, caught by "=@" below
        if tok in FILE_FLAGS or (tok == "-F" and "=" not in nxt):
            paths.append(nxt)
        for flag in FILE_FLAGS:
            if tok.startswith(flag + "="):
                paths.append(tok[len(flag) + 1:])
        if "=@" in tok:
            paths.append(tok.split("=@", 1)[1])
        elif tok.startswith("@"):
            paths.append(tok[1:])
    return [p for p in paths if p and p != "-"]


def posted_text_message(hit, where):
    code = f" (U+{ord(hit):04X})" if len(hit) == 1 else ""
    return (
        f'Posted text contains banned text "{hit}"{code} in {where}. '
        "Rewrite it without that text and run the command again."
    )


def is_push_command(command):
    return PUSH_RE.search(command) is not None


def push_dir(command, cwd, posix):
    """Return the directory a push command runs in: a leading cd, else git -C, else cwd."""
    try:
        tokens = shlex.split(command, posix=posix)
    except ValueError:
        return cwd
    if not posix:
        tokens = [_unquote(t) for t in tokens]
    d = None
    if len(tokens) >= 3 and tokens[0] in ("cd", "Set-Location") and tokens[2] in ("&&", ";"):
        d = tokens[1]
    else:
        for i, tok in enumerate(tokens[:-2]):
            if tok == "git" and tokens[i + 1] == "-C":
                d = tokens[i + 2]
                break
    if d is None:
        return cwd
    return os.path.join(cwd, d)


def _valid_build_row(row):
    if not isinstance(row, dict):
        return False
    m = row.get("root_marker")
    return (
        isinstance(row.get("glob"), str)
        and (isinstance(m, str) or (isinstance(m, list) and len(m) > 0 and all(isinstance(p, str) for p in m)))
        and isinstance(row.get("command"), list)
        and len(row["command"]) > 0
    )


def _has_marker(entries, markers):
    return any(fnmatch.filter(entries, p) for p in markers)


def matching_roots(changed, builds, top, isdir, listdir):
    """Return de-duplicated (root, command) pairs, in first-seen order, for the changed files."""
    rows = [r for r in builds if _valid_build_row(r)]
    roots = []
    for path in changed:
        for row in rows:
            if not fnmatch.fnmatch(path, row["glob"]):
                continue
            m = row["root_marker"]
            markers = [m] if isinstance(m, str) else m
            d = os.path.normpath(os.path.join(top, os.path.dirname(path)))
            while True:
                # A deleted file's directory may be gone; keep walking up
                if isdir(d) and _has_marker(listdir(d), markers):
                    pair = (d, tuple(row["command"]))
                    if pair not in roots:
                        roots.append(pair)
                    break
                parent = os.path.dirname(d)
                if d == top or parent == d:
                    break
                d = parent
    return roots


def trim_output(text, n=BUILD_OUTPUT_LINES):
    return "\n".join([line for line in text.splitlines() if line.strip()][-n:])


def build_failed_message(root, command, output):
    return f"Build failed in {root} ({' '.join(command)}). Fix these errors before pushing:\n" + trim_output(output)


def _read_payload():
    return json.loads(sys.stdin.buffer.read().decode("utf-8"))


def _git(args, cwd):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def changed_files(top):
    """Return the repo-relative paths changed by unpushed commits, or None on any git failure."""
    upstream = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], top)
    ref = upstream.decode().strip() if upstream is not None else "origin/HEAD"
    base = _git(["merge-base", "HEAD", ref], top)
    if base is None:
        return None
    diff = _git(["diff", "--name-only", base.decode().strip() + "..HEAD"], top)
    if diff is None:
        return None
    return [line for line in diff.decode("utf-8").splitlines() if line]


def run_build(root, command):
    """Return (returncode, output); returncode is None when the build couldn't run."""
    # which() applies PATHEXT on Windows, so "dotnet" or "npm" finds the .exe/.cmd
    exe = shutil.which(command[0])
    if exe is None:
        return None, f"{command[0]} not found on PATH"
    try:
        r = subprocess.run([exe] + list(command[1:]), cwd=root, capture_output=True, timeout=BUILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {BUILD_TIMEOUT_S}s"
    except OSError as e:
        return None, str(e)
    return r.returncode, r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")


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


def run_posted_text(payload, cfg):
    guard = cfg["guards"]["posted_text"]
    command = payload.get("tool_input", {}).get("command")
    if not guard["enabled"] or not isinstance(command, str) or not command:
        return 0
    if not is_posting_command(command):
        return 0
    banned = guard["banned"]
    hit = find_banned(command, banned)
    if hit:
        print(posted_text_message(hit, "the command"), file=sys.stderr)
        return 2
    cwd = payload.get("cwd")
    # PowerShell paths use backslashes, which POSIX shlex would treat as escapes
    for path in referenced_files(command, posix=payload.get("tool_name") != "PowerShell"):
        if cwd and not os.path.isabs(path):
            path = os.path.join(cwd, path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        hit = find_banned(text, banned)
        if hit:
            print(posted_text_message(hit, path), file=sys.stderr)
            return 2
    return 0


def run_build_gate(payload, cfg):
    guard = cfg["guards"]["build_gate"]
    command = payload.get("tool_input", {}).get("command")
    if not guard["enabled"] or not guard["builds"] or not isinstance(command, str):
        return 0
    if not is_push_command(command):
        return 0
    d = push_dir(command, payload.get("cwd"), posix=payload.get("tool_name") != "PowerShell")
    top = _git(["rev-parse", "--show-toplevel"], d)
    if top is None:
        return 0
    top = os.path.normpath(top.decode().strip())
    changed = changed_files(top)
    if not changed:
        return 0
    for root, build in matching_roots(changed, guard["builds"], top, os.path.isdir, os.listdir):
        code, output = run_build(root, build)
        if code is None:
            print(f"guards.py: build gate skipped {root}: {output}", file=sys.stderr)
        elif code != 0:
            print(build_failed_message(root, build, output), file=sys.stderr)
            return 2
    return 0


RUNNERS = {
    "--line-endings": run_line_endings,
    "--posted-text": run_posted_text,
    "--build-gate": run_build_gate,
}


def main():
    flag = next((a for a in sys.argv[1:] if a in RUNNERS), None)
    if flag is None:
        sys.exit(0)
    # A cp1252 console can't encode a banned entry like the em dash; the resulting
    # UnicodeEncodeError would hit the except below and let the post through.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        code = RUNNERS[flag](_read_payload(), ccbar.load_config())
    except Exception as e:
        print(f"guards.py: {e}", file=sys.stderr)
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
