import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import install

CLONE_DIR = "C:\\dev\\ccBar"
IN_PLACE_STATUS = {"statusLine": {"type": "command", "command": 'py -3 "C:/dev/ccBar/ccbar.py"'}}
ASYNC_GROUP = {"hooks": [{"type": "command", "command": "notify.sh", "async": True}]}


class BuildCommandsTests(unittest.TestCase):
    def test_in_place_commands_quote_clone_path_with_forward_slashes(self):
        status, stop = install.build_commands(["py", "-3"], True, CLONE_DIR)

        self.assertEqual(status, 'py -3 "C:/dev/ccBar/ccbar.py"')
        self.assertEqual(stop, 'py -3 "C:/dev/ccBar/ccbar.py" --stop-hook')

    def test_copy_commands_point_at_claude_dir(self):
        status, stop = install.build_commands(["py", "-3"], False, CLONE_DIR)

        self.assertEqual(status, "py -3 ~/.claude/ccbar.py")
        self.assertEqual(stop, "py -3 ~/.claude/ccbar.py --stop-hook")


class IsCcbarStopHookTests(unittest.TestCase):
    def test_matches_unquoted_copy_command(self):
        self.assertTrue(install.is_ccbar_stop_hook("py -3 ~/.claude/ccbar.py --stop-hook"))

    def test_matches_quoted_in_place_command(self):
        self.assertTrue(install.is_ccbar_stop_hook('py -3 "C:/dev/ccBar/ccbar.py" --stop-hook'))

    def test_rejects_unrelated_stop_hook(self):
        self.assertFalse(install.is_ccbar_stop_hook("notify.sh --on-stop"))


class UpsertStopHookTests(unittest.TestCase):
    def test_twice_leaves_one_ccbar_entry_and_unrelated_group_untouched(self):
        settings = {"hooks": {"Stop": [json.loads(json.dumps(ASYNC_GROUP))]}}
        stop_cmd = 'py -3 "C:/dev/ccBar/ccbar.py" --stop-hook'

        install.upsert_stop_hook(settings, "py -3 ~/.claude/ccbar.py --stop-hook")
        install.upsert_stop_hook(settings, stop_cmd)

        stop = settings["hooks"]["Stop"]
        ccbar_hooks = [h for g in stop for h in g["hooks"] if install.is_ccbar_stop_hook(h["command"])]
        self.assertEqual(ccbar_hooks, [{"type": "command", "command": stop_cmd, "timeout": 10}])
        self.assertEqual(json.dumps(stop[0]), json.dumps(ASYNC_GROUP))


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
