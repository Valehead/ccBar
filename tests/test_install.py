import contextlib, io, json, os, re, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import install

CLONE_DIR = "C:\\dev\\ccBar"
IN_PLACE_STATUS = {"statusLine": {"type": "command", "command": 'py -3 "C:/dev/ccBar/ccbar.py"'}}
DIRECT_STATUS = {"statusLine": {"type": "command", "command": "py -3 ~/.claude/ccbar.py"}}
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


class IsCcbarSubagentLineTests(unittest.TestCase):
    def test_matches_unquoted_copy_command(self):
        self.assertTrue(install.is_ccbar_subagent_line("py -3 ~/.claude/ccbar.py --subagents"))

    def test_matches_quoted_in_place_command(self):
        self.assertTrue(install.is_ccbar_subagent_line('py -3 "C:/dev/ccBar/ccbar.py" --subagents'))

    def test_rejects_unrelated_command(self):
        self.assertFalse(install.is_ccbar_subagent_line("~/.claude/subagent-statusline.sh"))

    def test_rejects_stop_hook(self):
        self.assertFalse(install.is_ccbar_subagent_line("py -3 ~/.claude/ccbar.py --stop-hook"))


class BuildSubagentStatusLineTests(unittest.TestCase):
    def test_appends_subagents_flag_without_refresh_interval(self):
        line = install.build_subagent_status_line("py -3 ~/.claude/ccbar.py")

        self.assertEqual(line, {"type": "command", "command": "py -3 ~/.claude/ccbar.py --subagents"})


class ApplySubagentStatusLineTests(unittest.TestCase):
    IN_PLACE_COMMAND = 'py -3 "C:/dev/ccBar/ccbar.py"'
    OTHER_LINE = {"type": "command", "command": "~/.claude/subagent-statusline.sh"}

    def test_adds_key_to_empty_settings(self):
        settings = {}

        action = install.apply_subagent_status_line(settings, self.IN_PLACE_COMMAND)

        self.assertEqual(settings["subagentStatusLine"]["command"], self.IN_PLACE_COMMAND + " --subagents")
        self.assertTrue(action.startswith("Added"))

    def test_replaces_old_ccbar_value(self):
        settings = {"subagentStatusLine": {"type": "command", "command": "py -3 ~/.claude/ccbar.py --subagents"}}

        action = install.apply_subagent_status_line(settings, self.IN_PLACE_COMMAND)

        self.assertEqual(settings["subagentStatusLine"]["command"], self.IN_PLACE_COMMAND + " --subagents")
        self.assertTrue(action.startswith("Updated"))

    def test_leaves_someone_elses_value_unchanged(self):
        settings = {"subagentStatusLine": copy_of(self.OTHER_LINE)}

        action = install.apply_subagent_status_line(settings, self.IN_PLACE_COMMAND)

        self.assertEqual(settings["subagentStatusLine"], self.OTHER_LINE)
        self.assertTrue(action.startswith("Kept"))


class RemoveSubagentStatusLineTests(unittest.TestCase):
    def test_removes_ccbar_value(self):
        settings = {"subagentStatusLine": {"type": "command", "command": "py -3 ~/.claude/ccbar.py --subagents"}}

        removed = install.remove_subagent_status_line(settings)

        self.assertTrue(removed)
        self.assertNotIn("subagentStatusLine", settings)

    def test_keeps_someone_elses_value(self):
        settings = {"subagentStatusLine": {"type": "command", "command": "~/.claude/subagent-statusline.sh"}}

        removed = install.remove_subagent_status_line(settings)

        self.assertFalse(removed)
        self.assertIn("subagentStatusLine", settings)

    def test_absent_key_returns_false(self):
        self.assertFalse(install.remove_subagent_status_line({}))


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
            "  Guard hooks (line endings, posted text, build gate): py -3 install.py --guards  (.NET projects: install.py --dotnet)",
        ])

    def test_omits_guards_hint_when_guards_registered(self):
        lines = install.next_steps("C:/Users/me/.claude/ccbar.config.json", True, "py -3")

        self.assertEqual(lines, ["Optional next steps:", "  Customize:  C:/Users/me/.claude/ccbar.config.json"])


class ResolveModeTests(unittest.TestCase):
    def test_existing_in_place_status_line_keeps_in_place(self):
        self.assertTrue(install.resolve_mode(IN_PLACE_STATUS, CLONE_DIR, []))

    def test_copy_flag_overrides_existing_in_place(self):
        self.assertFalse(install.resolve_mode(IN_PLACE_STATUS, CLONE_DIR, ["--install-direct"]))

    def test_inside_repo_flag_overrides_existing_direct(self):
        self.assertTrue(install.resolve_mode(DIRECT_STATUS, CLONE_DIR, ["--install-inside-repo"]))

    def test_no_status_line_and_no_flag_is_inside_repo(self):
        self.assertTrue(install.resolve_mode({}, CLONE_DIR, []))

    def test_existing_direct_status_line_stays_direct(self):
        self.assertFalse(install.resolve_mode(DIRECT_STATUS, CLONE_DIR, []))

    def test_non_ccbar_status_line_and_no_flag_is_inside_repo(self):
        settings = {"statusLine": {"type": "command", "command": "starship statusline"}}

        self.assertTrue(install.resolve_mode(settings, CLONE_DIR, []))

    def test_match_is_case_insensitive(self):
        self.assertTrue(install.resolve_mode(IN_PLACE_STATUS, "c:\\DEV\\ccbar", []))


class HelpTests(unittest.TestCase):
    def test_help_prints_usage_and_installs_nothing(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                out = io.StringIO()
                with mock.patch.object(sys, "argv", ["install.py", flag]), \
                        mock.patch.object(install, "install") as run_install, \
                        contextlib.redirect_stdout(out):
                    install.main()

                self.assertIn("--install-direct", out.getvalue())
                run_install.assert_not_called()

    def test_unknown_flag_stops_before_installing_and_points_at_help(self):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["install.py", "--dry-run", "--copy"]), \
                mock.patch.object(install, "install") as run_install, \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                install.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--copy", err.getvalue())
        self.assertIn("--help", err.getvalue())
        run_install.assert_not_called()

    def test_every_documented_flag_is_known(self):
        documented = set(re.findall(r"--[a-z-]+", install.__doc__))

        self.assertLessEqual(documented, install.KNOWN_FLAGS)


class AddBuildPresetTests(unittest.TestCase):
    def test_empty_config_gets_nested_builds_list(self):
        config = {}

        changed = install.add_build_preset(config, "dotnet")

        self.assertTrue(changed)
        self.assertEqual(config, {"guards": {"build_gate": {"builds": ["dotnet"]}}})

    def test_keeps_existing_rows_and_guard_keys(self):
        row = {"glob": "*.py", "root_marker": "setup.cfg", "command": ["tool", "build"]}
        config = {"guards": {"posted_text": {"banned": ["x"]}, "build_gate": {"enabled": True, "builds": [row]}}}

        install.add_build_preset(config, "dotnet")

        self.assertEqual(config, {
            "guards": {"posted_text": {"banned": ["x"]}, "build_gate": {"enabled": True, "builds": [row, "dotnet"]}},
        })

    def test_second_call_changes_nothing(self):
        config = {}
        install.add_build_preset(config, "dotnet")

        changed = install.add_build_preset(config, "dotnet")

        self.assertFalse(changed)
        self.assertEqual(config["guards"]["build_gate"]["builds"], ["dotnet"])


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        claude = os.path.join(tmp.name, ".claude")
        os.makedirs(claude)
        self.settings = os.path.join(claude, "settings.json")
        self.bak = os.path.join(claude, "settings.json.bak")
        paths = {
            "CLAUDE_DIR": claude,
            "SETTINGS_PATH": self.settings,
            "SETTINGS_BAK": self.bak,
            "DEST_SCRIPT": os.path.join(claude, "ccbar.py"),
            "DEST_CONFIG": os.path.join(claude, "ccbar.config.json"),
            "DEST_CONFIG_BAK": os.path.join(claude, "ccbar.config.json.bak"),
            "DEST_SKILLS_DIR": os.path.join(claude, "skills"),
        }
        patcher = mock.patch.multiple(install, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def assert_refuses(self, call):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                call()
        self.assertEqual(raised.exception.code, 1)

    def test_malformed_json_stops_install_and_leaves_files_unchanged(self):
        self.write(self.settings, b'{"a": 1,}')
        self.write(self.bak, b'{"good": true}')

        with mock.patch.object(install, "find_python", return_value=(["py", "-3"], "py -3")):
            self.assert_refuses(lambda: install.install(no_verify=True))

        self.assertEqual(self.read(self.settings), b'{"a": 1,}')
        self.assertEqual(self.read(self.bak), b'{"good": true}')

    def test_malformed_json_stops_uninstall_and_leaves_files_unchanged(self):
        self.write(self.settings, b'{"a": 1,}')
        self.write(self.bak, b'{"good": true}')

        self.assert_refuses(install.uninstall)

        self.assertEqual(self.read(self.settings), b'{"a": 1,}')
        self.assertEqual(self.read(self.bak), b'{"good": true}')

    def test_top_level_array_stops(self):
        self.write(self.settings, b"[]")

        self.assert_refuses(install.load_settings)

    def test_malformed_config_stops_dotnet_install_and_leaves_it_unchanged(self):
        config = install.DEST_CONFIG
        self.write(config, b'{"guards": ,}')

        with mock.patch.object(install, "find_python", return_value=(["py", "-3"], "py -3")):
            self.assert_refuses(lambda: install.install(no_verify=True, guards=True, dotnet=True))

        self.assertEqual(self.read(config), b'{"guards": ,}')
        self.assertFalse(os.path.exists(install.DEST_CONFIG_BAK))

    def test_reset_config_with_dotnet_keeps_the_user_config_backup(self):
        self.write(install.DEST_CONFIG, b'{"mine": true}')

        with mock.patch.object(install, "find_python", return_value=(["py", "-3"], "py -3")):
            with contextlib.redirect_stdout(io.StringIO()):
                install.install(no_verify=True, reset_config=True, guards=True, dotnet=True)

        self.assertEqual(self.read(install.DEST_CONFIG_BAK), b'{"mine": true}')

    def test_missing_file_gives_empty_dict(self):
        self.assertEqual(install.load_settings(), {})

    def test_dry_run_shows_subagent_status_line_without_writing(self):
        out = io.StringIO()

        with mock.patch.object(install, "find_python", return_value=(["py", "-3"], "py -3")), \
                contextlib.redirect_stdout(out):
            install.install(dry_run=True, no_verify=True)

        self.assertIn("subagentStatusLine", out.getvalue())
        self.assertIn("--subagents", out.getvalue())
        self.assertFalse(os.path.exists(self.settings))


if __name__ == "__main__":
    unittest.main()
