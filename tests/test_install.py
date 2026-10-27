import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import install

CLONE_DIR = "C:\\dev\\ccBar"
IN_PLACE_STATUS = {"statusLine": {"type": "command", "command": 'py -3 "C:/dev/ccBar/ccbar.py"'}}
ASYNC_GROUP = {"hooks": [{"type": "command", "command": "notify.sh", "async": True}]}
LINT_GROUP = {"matcher": "Edit", "hooks": [{"type": "command", "command": "lint.sh"}]}
GUARD_CMD = 'py -3 "C:/dev/ccBar/guards.py" --line-endings'
GUARD_ENTRY = {"type": "command", "command": GUARD_CMD, "timeout": 10}


def copy_of(obj):
    return json.loads(json.dumps(obj))


class BuildCommandsTests(unittest.TestCase):
    def test_in_place_commands_quote_clone_path_with_forward_slashes(self):
        status, stop = install.build_commands(["py", "-3"], True, CLONE_DIR)

        self.assertEqual(status, 'py -3 "C:/dev/ccBar/ccbar.py"')
        self.assertEqual(stop, 'py -3 "C:/dev/ccBar/ccbar.py" --stop-hook')

    def test_copy_commands_point_at_claude_dir(self):
        status, stop = install.build_commands(["py", "-3"], False, CLONE_DIR)

        self.assertEqual(status, "py -3 ~/.claude/ccbar.py")
        self.assertEqual(stop, "py -3 ~/.claude/ccbar.py --stop-hook")


class BuildStatusLineTests(unittest.TestCase):
    def test_sets_refresh_interval_to_30_seconds(self):
        status_line = install.build_status_line("py -3 ~/.claude/ccbar.py")

        self.assertEqual(status_line, {"type": "command", "command": "py -3 ~/.claude/ccbar.py", "refreshInterval": 30})


class IsCcbarStopHookTests(unittest.TestCase):
    def test_matches_unquoted_copy_command(self):
        self.assertTrue(install.is_ccbar_stop_hook("py -3 ~/.claude/ccbar.py --stop-hook"))

    def test_matches_quoted_in_place_command(self):
        self.assertTrue(install.is_ccbar_stop_hook('py -3 "C:/dev/ccBar/ccbar.py" --stop-hook'))

    def test_rejects_unrelated_stop_hook(self):
        self.assertFalse(install.is_ccbar_stop_hook("notify.sh --on-stop"))


class IsGuardHookTests(unittest.TestCase):
    def test_matches_guard_command(self):
        self.assertTrue(install.is_guard_hook(GUARD_CMD))

    def test_rejects_stop_hook(self):
        self.assertFalse(install.is_guard_hook('py -3 "C:/dev/ccBar/ccbar.py" --stop-hook'))

    def test_rejects_unrelated_command(self):
        self.assertFalse(install.is_guard_hook("lint.sh"))


class BuildGuardCommandTests(unittest.TestCase):
    def test_quotes_clone_path_with_forward_slashes(self):
        command = install.build_guard_command(["py", "-3"], CLONE_DIR, "--line-endings")

        self.assertEqual(command, GUARD_CMD)


class UpsertHookTests(unittest.TestCase):
    def test_stop_hook_twice_leaves_one_ccbar_entry_and_unrelated_group_untouched(self):
        settings = {"hooks": {"Stop": [copy_of(ASYNC_GROUP)]}}
        stop_cmd = 'py -3 "C:/dev/ccBar/ccbar.py" --stop-hook'
        old = {"type": "command", "command": "py -3 ~/.claude/ccbar.py --stop-hook", "timeout": 10}
        new = {"type": "command", "command": stop_cmd, "timeout": 10}

        install.upsert_hook(settings, "Stop", old, install.is_ccbar_stop_hook)
        install.upsert_hook(settings, "Stop", new, install.is_ccbar_stop_hook)

        stop = settings["hooks"]["Stop"]
        ccbar_hooks = [h for g in stop for h in g["hooks"] if install.is_ccbar_stop_hook(h["command"])]
        self.assertEqual(ccbar_hooks, [new])
        self.assertEqual(json.dumps(stop[0]), json.dumps(ASYNC_GROUP))

    def test_matcher_adds_group_and_leaves_unrelated_group_untouched(self):
        settings = {"hooks": {"PostToolUse": [copy_of(LINT_GROUP)]}}

        install.upsert_hook(settings, "PostToolUse", GUARD_ENTRY, install.is_guard_hook, "Edit|Write")

        self.assertEqual(
            settings["hooks"]["PostToolUse"],
            [LINT_GROUP, {"hooks": [GUARD_ENTRY], "matcher": "Edit|Write"}],
        )

    def test_rerun_does_not_duplicate(self):
        settings = {}

        install.upsert_hook(settings, "PostToolUse", GUARD_ENTRY, install.is_guard_hook, "Edit|Write")
        install.upsert_hook(settings, "PostToolUse", GUARD_ENTRY, install.is_guard_hook, "Edit|Write")

        self.assertEqual(settings["hooks"]["PostToolUse"], [{"hooks": [GUARD_ENTRY], "matcher": "Edit|Write"}])


class RemoveHooksTests(unittest.TestCase):
    def test_drops_only_ours(self):
        settings = {"hooks": {"PostToolUse": [copy_of(LINT_GROUP), {"matcher": "Edit|Write", "hooks": [GUARD_ENTRY]}]}}

        changed = install.remove_hooks(settings, "PostToolUse", install.is_guard_hook)

        self.assertTrue(changed)
        self.assertEqual(settings["hooks"]["PostToolUse"], [LINT_GROUP])

    def test_deletes_empty_event_and_hooks_keys(self):
        settings = {"model": "opus", "hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [GUARD_ENTRY]}]}}

        install.remove_hooks(settings, "PostToolUse", install.is_guard_hook)

        self.assertEqual(settings, {"model": "opus"})

    def test_nothing_ours_reports_unchanged(self):
        settings = {"hooks": {"PostToolUse": [copy_of(LINT_GROUP)]}}

        changed = install.remove_hooks(settings, "PostToolUse", install.is_guard_hook)

        self.assertFalse(changed)
        self.assertEqual(settings, {"hooks": {"PostToolUse": [LINT_GROUP]}})


class ApplyGuardsTests(unittest.TestCase):
    POSTED_TEXT_ENTRY = {"type": "command", "command": 'py -3 "C:/dev/ccBar/guards.py" --posted-text', "timeout": 10}
    BUILD_GATE_ENTRY = {"type": "command", "command": 'py -3 "C:/dev/ccBar/guards.py" --build-gate', "timeout": 900}
    SHELL_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}

    def test_registers_all_guard_groups_on_empty_settings(self):
        settings = {}

        install.apply_guards(settings, ["py", "-3"], CLONE_DIR, True)

        self.assertEqual(settings["hooks"], {
            "PostToolUse": [{"hooks": [GUARD_ENTRY], "matcher": "Edit|Write"}],
            "PreToolUse": [
                {"hooks": [self.POSTED_TEXT_ENTRY], "matcher": "Bash|PowerShell"},
                {"hooks": [self.BUILD_GATE_ENTRY], "matcher": "Bash|PowerShell"},
            ],
        })

    def test_build_gate_lands_in_its_own_group_beside_posted_text(self):
        settings = {"hooks": {"PreToolUse": [{"hooks": [copy_of(self.POSTED_TEXT_ENTRY)], "matcher": "Bash|PowerShell"}]}}

        install.apply_guards(settings, ["py", "-3"], CLONE_DIR, True)

        self.assertEqual(settings["hooks"]["PreToolUse"], [
            {"hooks": [self.POSTED_TEXT_ENTRY], "matcher": "Bash|PowerShell"},
            {"hooks": [self.BUILD_GATE_ENTRY], "matcher": "Bash|PowerShell"},
        ])

    def test_rerun_does_not_duplicate_and_leaves_unrelated_pre_tool_use_group(self):
        settings = {"hooks": {"PreToolUse": [copy_of(self.SHELL_GROUP)]}}

        install.apply_guards(settings, ["py", "-3"], CLONE_DIR, True)
        install.apply_guards(settings, ["py", "-3"], CLONE_DIR, True)

        self.assertEqual(
            settings["hooks"]["PreToolUse"],
            [
                self.SHELL_GROUP,
                {"hooks": [self.POSTED_TEXT_ENTRY], "matcher": "Bash|PowerShell"},
                {"hooks": [self.BUILD_GATE_ENTRY], "matcher": "Bash|PowerShell"},
            ],
        )


class NextStepsTests(unittest.TestCase):
    def test_includes_guards_hint_when_no_guards_registered(self):
        lines = install.next_steps("C:/Users/me/.claude/ccbar.config.json", False, "py -3")

        self.assertEqual(lines, [
            "Optional next steps:",
            "  Customize:  C:/Users/me/.claude/ccbar.config.json",
            "  Guard hooks (line endings, posted text, build gate): py -3 install.py --guards",
        ])

    def test_omits_guards_hint_when_guards_registered(self):
        lines = install.next_steps("C:/Users/me/.claude/ccbar.config.json", True, "py -3")

        self.assertEqual(lines, ["Optional next steps:", "  Customize:  C:/Users/me/.claude/ccbar.config.json"])


class ResolveModeTests(unittest.TestCase):
    def test_existing_in_place_status_line_keeps_in_place(self):
        self.assertTrue(install.resolve_mode(IN_PLACE_STATUS, CLONE_DIR, []))

    def test_copy_flag_overrides_existing_in_place(self):
        self.assertFalse(install.resolve_mode(IN_PLACE_STATUS, CLONE_DIR, ["--copy"]))

    def test_no_status_line_and_no_flag_is_copy(self):
        self.assertFalse(install.resolve_mode({}, CLONE_DIR, []))

    def test_match_is_case_insensitive(self):
        self.assertTrue(install.resolve_mode(IN_PLACE_STATUS, "c:\\DEV\\ccbar", []))


if __name__ == "__main__":
    unittest.main()
