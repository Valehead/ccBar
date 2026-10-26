#!/usr/bin/env python3
"""
install.py - ccBar installer. Safe to re-run after any update.

Usage:
  python install.py                Install or update ccBar
  python install.py --in-place     Run ccbar.py from this clone instead of copying it
                                   into ~/.claude (for machines that block scripts there)
  python install.py --copy         Copy ccbar.py into ~/.claude (the default; switches
                                   back from --in-place)
  python install.py --dry-run      Show what would happen, don't write
  python install.py --no-verify    Skip self-test after install
  python install.py --reset-config Overwrite your config with the latest example
                                   (backs up your existing config first)
  python install.py --uninstall    Remove ccBar from settings.json

With neither --in-place nor --copy, the mode of the existing install is kept.
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
DEST_CONFIG_BAK = DEST_CONFIG + ".bak"
SRC_SCRIPT = os.path.join(SCRIPT_DIR, "ccbar.py")
SRC_CONFIG_EXAMPLE = os.path.join(SCRIPT_DIR, "config.example.json")
SRC_SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")
DEST_SKILLS_DIR = os.path.join(CLAUDE_DIR, "skills")
STATE_DIR = os.path.join(CLAUDE_DIR, "ccbar-state")
REFRESH_INTERVAL_S = 30


def find_python():
    """Return (command_list, display) for the best available Python 3.

    Prefer short, portable command names over absolute paths so the value
    written into settings.json isn't machine-specific. Only fall back to
    sys.executable (an absolute path) if no named command works.
    """
    def probe(cmd):
        try:
            r = subprocess.run(
                cmd + ["-c", "import sys; assert sys.version_info >= (3,8)"],
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except Exception:
            return False

    # Windows py launcher is system-level; use its name, not its full path
    if platform.system() == "Windows" and shutil.which("py"):
        if probe(["py", "-3"]):
            return ["py", "-3"], "py -3"

    # Standard named commands (cross-platform)
    for name in ("python3", "python"):
        if shutil.which(name) and probe([name]):
            return [name], name

    # Last resort: absolute path of the interpreter running this script
    interp = sys.executable
    if interp and probe([interp]):
        return [interp], interp

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
    if os.path.isfile(SETTINGS_PATH):
        shutil.copy2(SETTINGS_PATH, SETTINGS_BAK)
        print(f"  Backed up settings to {SETTINGS_BAK}")
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def skill_names():
    return sorted(
        name for name in os.listdir(SRC_SKILLS_DIR)
        if os.path.isdir(os.path.join(SRC_SKILLS_DIR, name))
    )


def build_commands(py_cmd, in_place, script_dir):
    """Return (status_command, stop_command) for settings.json."""
    if in_place:
        script = '"' + script_dir.replace("\\", "/") + '/ccbar.py"'
    else:
        script = "~/.claude/ccbar.py"
    status = " ".join(py_cmd + [script])
    return status, status + " --stop-hook"


def build_status_line(command):
    return {"type": "command", "command": command, "refreshInterval": REFRESH_INTERVAL_S}


def resolve_mode(settings, script_dir, args):
    """Return True for in-place mode.

    An explicit flag wins; otherwise keep the existing install's mode, so a
    plain re-run after `git pull` doesn't flip an in-place machine to copy.
    """
    if "--copy" in args:
        return False
    if "--in-place" in args:
        return True
    command = settings.get("statusLine", {}).get("command", "")
    clone_script = script_dir.replace("\\", "/") + "/ccbar.py"
    return clone_script.lower() in command.lower()


def is_ccbar_stop_hook(command):
    return "ccbar.py" in command and "--stop-hook" in command


def remove_stop_hook(settings):
    stop = settings.get("hooks", {}).get("Stop", [])
    kept = []
    for group in stop:
        hooks = [h for h in group.get("hooks", []) if not is_ccbar_stop_hook(h.get("command", ""))]
        if hooks:
            kept.append({**group, "hooks": hooks})
    changed = kept != stop
    if changed:
        if kept:
            settings["hooks"]["Stop"] = kept
        else:
            del settings["hooks"]["Stop"]
            if not settings["hooks"]:
                del settings["hooks"]
    return changed


def upsert_stop_hook(settings, command):
    remove_stop_hook(settings)
    settings.setdefault("hooks", {}).setdefault("Stop", []).append(
        {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    )


def install(dry_run=False, no_verify=False, reset_config=False, in_place=False):
    print("=== ccBar installer ===\n")

    py_cmd, py_display = find_python()
    if not py_cmd:
        print("ERROR: could not locate a Python 3 interpreter.", file=sys.stderr)
        sys.exit(1)
    print(f"  Python: {py_display}")
    if in_place:
        print(f"  Mode: in-place (runs {SRC_SCRIPT})")
    else:
        print(f"  Mode: copy (runs {DEST_SCRIPT})")

    status_command, stop_cmd = build_commands(py_cmd, in_place, SCRIPT_DIR)
    status_cmd = build_status_line(status_command)

    script_existed = os.path.isfile(DEST_SCRIPT)
    if in_place:
        if script_existed:
            if dry_run:
                print(f"[dry-run] would ask to remove stale copy {DEST_SCRIPT}")
            else:
                ans = input(f"  Remove stale copy {DEST_SCRIPT}? [y/N] ").strip().lower()
                if ans == "y":
                    os.remove(DEST_SCRIPT)
                    print(f"  Removed {DEST_SCRIPT}")
    # Always deploy the latest ccbar.py so re-runs pick up updates
    elif dry_run:
        verb = "update" if script_existed else "install"
        print(f"[dry-run] would {verb} ccbar.py -> {DEST_SCRIPT}")
    else:
        os.makedirs(CLAUDE_DIR, exist_ok=True)
        shutil.copy2(SRC_SCRIPT, DEST_SCRIPT)
        verb = "Updated" if script_existed else "Installed"
        print(f"  {verb} ccbar.py -> {DEST_SCRIPT}")

    for name in skill_names():
        dest = os.path.join(DEST_SKILLS_DIR, name)
        skill_existed = os.path.isdir(dest)
        if dry_run:
            verb = "update" if skill_existed else "install"
            print(f"[dry-run] would {verb} {name} skill -> {dest}")
        else:
            shutil.copytree(os.path.join(SRC_SKILLS_DIR, name), dest, dirs_exist_ok=True)
            verb = "Updated" if skill_existed else "Installed"
            print(f"  {verb} {name} skill -> {dest}")

    # Config: write on first install, or when --reset-config is passed.
    # Otherwise preserve it — users edit this file to customize the bar.
    config_existed = os.path.isfile(DEST_CONFIG)
    if not config_existed or reset_config:
        if dry_run:
            verb = "reset" if (config_existed and reset_config) else "install"
            print(f"[dry-run] would {verb} config -> {DEST_CONFIG}")
        else:
            if os.path.isfile(SRC_CONFIG_EXAMPLE):
                if config_existed:
                    shutil.copy2(DEST_CONFIG, DEST_CONFIG_BAK)
                    print(f"  Backed up existing config to {DEST_CONFIG_BAK}")
                shutil.copy2(SRC_CONFIG_EXAMPLE, DEST_CONFIG)
                verb = "Reset" if config_existed else "Installed"
                print(f"  {verb} config -> {DEST_CONFIG}")
    else:
        print(f"  Config preserved at {DEST_CONFIG}")
        print(f"    (pass --reset-config to overwrite; your current config will be backed up)")

    # Always update statusLine — picks up command or format changes across versions
    settings = load_settings()
    prev = settings.get("statusLine")
    settings["statusLine"] = status_cmd
    upsert_stop_hook(settings, stop_cmd)
    save_settings(settings, dry_run)
    if not dry_run:
        verb = "Updated" if prev else "Added"
        print(f"  {verb} statusLine in {SETTINGS_PATH}")
        print(f"  Registered Stop hook (session retro) in {SETTINGS_PATH}")

    # Self-test
    if not no_verify and not dry_run:
        print("\n  Running self-test...")
        script = SRC_SCRIPT if in_place else DEST_SCRIPT
        r = subprocess.run(py_cmd + [script, "--selftest"], capture_output=False)
        if r.returncode != 0:
            print("  WARNING: self-test failed — check the output above.", file=sys.stderr)
        else:
            print("  Self-test passed.")

    print("\nDone. Restart Claude Code to activate the status line.")


def uninstall(dry_run=False):
    print("=== ccBar uninstaller ===\n")

    settings = load_settings()
    removed_status = settings.pop("statusLine", None) is not None
    removed_stop = remove_stop_hook(settings)
    if removed_status or removed_stop:
        save_settings(settings, dry_run)
    if removed_status:
        if not dry_run:
            print(f"  Removed statusLine from {SETTINGS_PATH}")
    else:
        print("  statusLine not present in settings.json, nothing to remove.")
    if removed_stop:
        if not dry_run:
            print(f"  Removed Stop hook (session retro) from {SETTINGS_PATH}")
    else:
        print("  ccBar Stop hook not present in settings.json, nothing to remove.")

    if not dry_run and os.path.isfile(SETTINGS_BAK):
        ans = input("  Restore settings.json from backup? [y/N] ").strip().lower()
        if ans == "y":
            shutil.copy2(SETTINGS_BAK, SETTINGS_PATH)
            print(f"  Restored from {SETTINGS_BAK}")

    cache_path = os.path.expanduser("~/.claude/.ccbar-cache.json")
    for path in [DEST_SCRIPT, DEST_CONFIG, cache_path]:
        if os.path.isfile(path):
            if dry_run:
                print(f"[dry-run] would remove {path}")
            else:
                ans = input(f"  Remove {path}? [y/N] ").strip().lower()
                if ans == "y":
                    os.remove(path)
                    print(f"  Removed {path}")

    skill_dirs = [os.path.join(DEST_SKILLS_DIR, name) for name in skill_names()]
    for path in skill_dirs + [STATE_DIR]:
        if os.path.isdir(path):
            if dry_run:
                print(f"[dry-run] would remove {path}")
            else:
                ans = input(f"  Remove {path}? [y/N] ").strip().lower()
                if ans == "y":
                    shutil.rmtree(path)
                    print(f"  Removed {path}")

    print(f"  Kept {os.path.join(CLAUDE_DIR, 'retros')} (your retro files)")

    print("\nDone.")


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    no_verify = "--no-verify" in args
    reset_config = "--reset-config" in args

    if "--in-place" in args and "--copy" in args:
        print("ERROR: --in-place and --copy are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

    if "--uninstall" in args:
        uninstall(dry_run=dry_run)
    else:
        in_place = resolve_mode(load_settings(), SCRIPT_DIR, args)
        install(dry_run=dry_run, no_verify=no_verify, reset_config=reset_config, in_place=in_place)


if __name__ == "__main__":
    main()
