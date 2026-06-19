#!/usr/bin/env python3
"""
install.py - ccBar installer.

Usage:
  python install.py              Install ccBar
  python install.py --dry-run    Show what would happen, don't write
  python install.py --no-verify  Skip self-test after install
  python install.py --uninstall  Remove ccBar from settings.json
"""

import json
import os
import platform
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.expanduser("~/.claude")
SETTINGS_PATH = os.path.join(CLAUDE_DIR, "settings.json")
SETTINGS_BAK = os.path.join(CLAUDE_DIR, "settings.json.bak")
DEST_SCRIPT = os.path.join(CLAUDE_DIR, "ccbar.py")
DEST_CONFIG = os.path.join(CLAUDE_DIR, "ccbar.config.json")
SRC_SCRIPT = os.path.join(SCRIPT_DIR, "ccbar.py")
SRC_CONFIG_EXAMPLE = os.path.join(SCRIPT_DIR, "config.example.json")


def find_python():
    """Return (command_list, display) for the best available Python 3."""
    # Prefer `py` launcher on Windows (handles PATH issues cleanly)
    if platform.system() == "Windows":
        py = shutil.which("py")
        if py:
            try:
                r = subprocess.run(
                    [py, "-3", "-c", "import sys; print(sys.version)"],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    return [py, "-3"], f"{py} -3"
            except Exception:
                pass

    # Absolute path of current interpreter
    interp = sys.executable
    if interp:
        return [interp], interp

    # Fallback names
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return [found], found

    return None, None


def load_settings():
    if not os.path.isfile(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data, dry_run):
    content = json.dumps(data, indent=2) + "\n"
    if dry_run:
        print(f"[dry-run] would write {SETTINGS_PATH}:")
        print(content)
        return
    # Backup first
    if os.path.isfile(SETTINGS_PATH):
        shutil.copy2(SETTINGS_PATH, SETTINGS_BAK)
        print(f"  Backed up settings to {SETTINGS_BAK}")
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def install(dry_run=False, no_verify=False):
    print("=== ccBar installer ===\n")

    py_cmd, py_display = find_python()
    if not py_cmd:
        print("ERROR: could not locate a Python 3 interpreter.", file=sys.stderr)
        sys.exit(1)
    print(f"  Python: {py_display}")

    # Build the statusLine object Claude Code expects
    status_cmd = {"type": "command", "command": " ".join(py_cmd + [DEST_SCRIPT])}

    # Copy ccbar.py
    if dry_run:
        print(f"[dry-run] would copy {SRC_SCRIPT} -> {DEST_SCRIPT}")
    else:
        os.makedirs(CLAUDE_DIR, exist_ok=True)
        shutil.copy2(SRC_SCRIPT, DEST_SCRIPT)
        print(f"  Copied ccbar.py -> {DEST_SCRIPT}")

    # Copy default config only if one doesn't already exist
    if not os.path.isfile(DEST_CONFIG):
        if dry_run:
            print(f"[dry-run] would copy {SRC_CONFIG_EXAMPLE} -> {DEST_CONFIG}")
        else:
            if os.path.isfile(SRC_CONFIG_EXAMPLE):
                shutil.copy2(SRC_CONFIG_EXAMPLE, DEST_CONFIG)
                print(f"  Copied config.example.json -> {DEST_CONFIG} (ccbar.config.json)")
    else:
        print(f"  Config already exists at {DEST_CONFIG}, skipping.")

    # Merge statusLine key into settings.json
    settings = load_settings()
    settings["statusLine"] = status_cmd
    save_settings(settings, dry_run)
    if not dry_run:
        print(f"  Set statusLine in {SETTINGS_PATH}")

    # Self-test
    if not no_verify and not dry_run:
        print("\n  Running self-test...")
        r = subprocess.run(py_cmd + [DEST_SCRIPT, "--selftest"], capture_output=False)
        if r.returncode != 0:
            print("  WARNING: self-test failed — check the output above.", file=sys.stderr)
        else:
            print("  Self-test passed.")

    print("\nDone. Restart Claude Code to activate the status line.")


def uninstall(dry_run=False):
    print("=== ccBar uninstaller ===\n")

    # Remove statusLine key
    settings = load_settings()
    if "statusLine" in settings:
        del settings["statusLine"]
        save_settings(settings, dry_run)
        if not dry_run:
            print(f"  Removed statusLine from {SETTINGS_PATH}")
    else:
        print("  statusLine not present in settings.json, nothing to remove.")

    # Restore from backup if present
    if not dry_run and os.path.isfile(SETTINGS_BAK):
        ans = input("  Restore settings.json from backup? [y/N] ").strip().lower()
        if ans == "y":
            shutil.copy2(SETTINGS_BAK, SETTINGS_PATH)
            print(f"  Restored from {SETTINGS_BAK}")

    # Optionally remove files
    for path in [DEST_SCRIPT, DEST_CONFIG]:
        if os.path.isfile(path):
            if dry_run:
                print(f"[dry-run] would remove {path}")
            else:
                ans = input(f"  Remove {path}? [y/N] ").strip().lower()
                if ans == "y":
                    os.remove(path)
                    print(f"  Removed {path}")

    print("\nDone.")


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    no_verify = "--no-verify" in args

    if "--uninstall" in args:
        uninstall(dry_run=dry_run)
    else:
        install(dry_run=dry_run, no_verify=no_verify)


if __name__ == "__main__":
    main()
