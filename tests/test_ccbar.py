import io, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ccbar

RETRO_CFG = {"enabled": True, "threshold_tokens": 400000, "threshold_pct": 70}
HANDOFF_CFG = {
    "enabled": True,
    "threshold_tokens": 600000,
    "threshold_pct": 85,
    "plans_dir": "~/.claude/plans",
    "prompt_template": "Read and execute this plan at: {path}",
}
CFG = {"retro": RETRO_CFG, "handoff": HANDOFF_CFG}
CROSSED_STATE = {"window_size": 1000000, "events": {"retro": {"crossed": True, "fired": False, "tokens": 410000}}}
FIRED_STATE = {"window_size": 1000000, "events": {"retro": {"crossed": True, "fired": True, "tokens": 410000}}}
BOTH_CROSSED_STATE = {
    "window_size": 1000000,
    "events": {
        "retro": {"crossed": True, "fired": False, "tokens": 610000},
        "handoff": {"crossed": True, "fired": False, "tokens": 610000},
    },
}
PAYLOAD = {
    "session_id": "abc12345",
    "transcript_path": "C:/t/abc12345.jsonl",
    "cwd": "C:/repo",
    "stop_hook_active": False,
}


def _status_payload(total_tokens, window_size=1000000):
    return {
        "session_id": "sess-1",
        "context_window": {"total_input_tokens": total_tokens, "context_window_size": window_size},
    }


class ContextTokensTests(unittest.TestCase):
    def test_reads_total_input_tokens_from_context_window(self):
        data = {"context_window": {"total_input_tokens": 90000, "context_window_size": 200000}}

        used, size = ccbar.context_tokens(data)

        self.assertEqual((used, size), (90000, 200000))

    def test_falls_back_to_used_percentage_when_total_is_zero(self):
        data = {"context_window": {"total_input_tokens": 0, "context_window_size": 1000000, "used_percentage": 25}}

        used, _ = ccbar.context_tokens(data)

        self.assertEqual(used, 250000)

    def test_defaults_window_size_to_200k_when_missing(self):
        data = {"context_window": {"total_input_tokens": 1000}}

        _, size = ccbar.context_tokens(data)

        self.assertEqual(size, 200000)


class ThresholdCrossedTests(unittest.TestCase):
    def test_crosses_on_token_threshold_for_1m_window(self):
        self.assertTrue(ccbar.threshold_crossed(410000, 1000000, RETRO_CFG))

    def test_crosses_on_pct_threshold_for_200k_window(self):
        self.assertTrue(ccbar.threshold_crossed(140000, 200000, RETRO_CFG))

    def test_not_crossed_below_both(self):
        self.assertFalse(ccbar.threshold_crossed(100000, 1000000, RETRO_CFG))

    def test_disabled_never_crosses(self):
        cfg = {**RETRO_CFG, "enabled": False}

        self.assertFalse(ccbar.threshold_crossed(900000, 1000000, cfg))


class StopHookOutputTests(unittest.TestCase):
    def test_returns_none_when_stop_hook_active(self):
        payload = {**PAYLOAD, "stop_hook_active": True}

        self.assertIsNone(ccbar.stop_hook_output(payload, CROSSED_STATE, CFG))

    def test_returns_none_when_already_fired(self):
        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, FIRED_STATE, CFG))

    def test_returns_none_when_not_crossed(self):
        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, {}, CFG))

    def test_returns_none_when_disabled(self):
        cfg = {"retro": {**RETRO_CFG, "enabled": False}}

        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, CROSSED_STATE, cfg))

    def test_returns_additional_context_naming_skill_and_transcript(self):
        _, out = ccbar.stop_hook_output(PAYLOAD, CROSSED_STATE, CFG)

        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "Stop")
        self.assertIn("session-retro", hso["additionalContext"])
        self.assertIn(PAYLOAD["transcript_path"], hso["additionalContext"])

    def test_returns_retro_when_both_crossed_and_unfired(self):
        name, _ = ccbar.stop_hook_output(PAYLOAD, BOTH_CROSSED_STATE, CFG)

        self.assertEqual(name, "retro")

    def test_returns_handoff_when_retro_already_fired(self):
        state = {"events": {**BOTH_CROSSED_STATE["events"], "retro": {"crossed": True, "fired": True}}}

        name, _ = ccbar.stop_hook_output(PAYLOAD, state, CFG)

        self.assertEqual(name, "handoff")

    def test_returns_none_when_handoff_disabled_even_if_crossed(self):
        state = {"events": {"handoff": {"crossed": True, "fired": False, "tokens": 610000}}}
        cfg = {**CFG, "handoff": {**HANDOFF_CFG, "enabled": False}}

        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, state, cfg))

    def test_handoff_context_has_skill_absolute_plans_dir_and_template(self):
        state = {"events": {"handoff": {"crossed": True, "fired": False, "tokens": 610000}}}

        _, out = ccbar.stop_hook_output(PAYLOAD, state, CFG)

        context = out["hookSpecificOutput"]["additionalContext"]
        plans_dir = os.path.abspath(os.path.expanduser("~/.claude/plans"))
        self.assertIn("session-handoff", context)
        self.assertIn(f"plans_dir={plans_dir} ", context)
        self.assertNotIn("~", context)
        self.assertIn('prompt_template="Read and execute this plan at: {path}"', context)


class StateIoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state_dir = ccbar.STATE_DIR
        ccbar.STATE_DIR = self._tmp.name

    def tearDown(self):
        ccbar.STATE_DIR = self._orig_state_dir
        self._tmp.cleanup()

    def test_save_then_load_round_trips(self):
        ccbar.save_state("s1", CROSSED_STATE)

        loaded = ccbar.load_state("s1")

        self.assertEqual(loaded, CROSSED_STATE)

    def test_load_missing_session_returns_empty(self):
        self.assertEqual(ccbar.load_state("missing"), {})

    def test_load_converts_legacy_flat_file_to_retro_event(self):
        legacy = {"crossed": True, "retro_fired": True, "tokens": 410000, "window_size": 1000000}
        with open(os.path.join(self._tmp.name, "legacy.json"), "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        loaded = ccbar.load_state("legacy")

        self.assertEqual(loaded, FIRED_STATE)

    def test_update_state_skips_write_when_unchanged(self):
        ccbar.save_state("s1", CROSSED_STATE)
        path = os.path.join(self._tmp.name, "s1.json")
        os.utime(path, (1_700_000_000, 1_700_000_000))

        ccbar.update_state("s1", lambda state: None)

        self.assertEqual(os.path.getmtime(path), 1_700_000_000)

    def test_update_state_preserves_fields_it_does_not_change(self):
        ccbar.save_state("s1", {**CROSSED_STATE, "git": {"branch": "main"}})

        def mark_fired(state):
            state["events"]["retro"]["fired"] = True

        ccbar.update_state("s1", mark_fired)

        self.assertEqual(ccbar.load_state("s1"), {**FIRED_STATE, "git": {"branch": "main"}})

    def test_prune_removes_files_older_than_max_age(self):
        now = 1_800_000_000
        ccbar.save_state("old", CROSSED_STATE)
        ccbar.save_state("fresh", CROSSED_STATE)
        old_time = now - 8 * 86400
        os.utime(os.path.join(self._tmp.name, "old.json"), (old_time, old_time))
        os.utime(os.path.join(self._tmp.name, "fresh.json"), (now, now))

        ccbar.prune_state(now=now)

        self.assertEqual(os.listdir(self._tmp.name), ["fresh.json"])

    def test_update_event_state_writes_once_on_crossing(self):
        below = ccbar.update_event_state(_status_payload(100000), CFG)
        state_path = os.path.join(self._tmp.name, "sess-1.json")
        self.assertEqual(below, {})
        self.assertFalse(os.path.exists(state_path))

        first = ccbar.update_event_state(_status_payload(450000), CFG)
        self.assertTrue(first["events"]["retro"]["crossed"])

        second = ccbar.update_event_state(_status_payload(600000), CFG)
        self.assertEqual(second["events"]["retro"]["tokens"], 450000)

    def test_update_event_state_records_handoff_while_retro_disabled(self):
        cfg = {**CFG, "retro": {**RETRO_CFG, "enabled": False}}

        state = ccbar.update_event_state(_status_payload(650000), cfg)

        self.assertEqual(list(state["events"]), ["handoff"])
        self.assertTrue(state["events"]["handoff"]["crossed"])

    def test_update_event_state_records_both_then_skips_unchanged_write(self):
        first = ccbar.update_event_state(_status_payload(650000), CFG)
        path = os.path.join(self._tmp.name, "sess-1.json")
        os.utime(path, (1_700_000_000, 1_700_000_000))

        ccbar.update_event_state(_status_payload(650000), CFG)

        self.assertEqual(sorted(first["events"]), ["handoff", "retro"])
        self.assertEqual(os.path.getmtime(path), 1_700_000_000)

    def test_update_event_state_ignores_unsafe_session_id(self):
        data = {**_status_payload(450000), "session_id": "../evil"}

        state = ccbar.update_event_state(data, CFG)

        self.assertEqual(state, {})
        self.assertEqual(os.listdir(self._tmp.name), [])


class RunStopHookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state_dir = ccbar.STATE_DIR
        ccbar.STATE_DIR = self._tmp.name

    def tearDown(self):
        ccbar.STATE_DIR = self._orig_state_dir
        self._tmp.cleanup()

    def _run(self, payload):
        stdin = io.StringIO(json.dumps(payload))
        with mock.patch.object(ccbar, "load_config", return_value=CFG), \
                mock.patch("sys.stdin", stdin), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            ccbar.run_stop_hook()
        return stdout.getvalue()

    def test_fires_once_then_never_again(self):
        ccbar.save_state(PAYLOAD["session_id"], CROSSED_STATE)

        first = self._run(PAYLOAD)
        second = self._run(PAYLOAD)

        self.assertIn("session-retro", first)
        self.assertEqual(second, "")
        self.assertTrue(ccbar.load_state(PAYLOAD["session_id"])["events"]["retro"]["fired"])

    def test_fires_retro_then_handoff_then_nothing(self):
        ccbar.save_state(PAYLOAD["session_id"], BOTH_CROSSED_STATE)

        first = self._run(PAYLOAD)
        second = self._run(PAYLOAD)
        third = self._run(PAYLOAD)

        self.assertIn("session-retro", first)
        self.assertIn("session-handoff", second)
        self.assertEqual(third, "")

    def test_stop_hook_active_fires_nothing(self):
        ccbar.save_state(PAYLOAD["session_id"], CROSSED_STATE)

        out = self._run({**PAYLOAD, "stop_hook_active": True})

        self.assertEqual(out, "")
        self.assertFalse(ccbar.load_state(PAYLOAD["session_id"])["events"]["retro"]["fired"])


class GitInfoTests(unittest.TestCase):
    def test_shortstat_diffs_against_head(self):
        with mock.patch.object(ccbar, "_run_git", return_value="") as run_git:
            ccbar.get_git_info({}, "C:/repo")

        self.assertIn(mock.call(["diff", "--shortstat", "HEAD"], 800, "C:/repo"), run_git.call_args_list)

    def test_every_git_call_runs_in_given_cwd(self):
        with mock.patch.object(ccbar, "_run_git", return_value="") as run_git:
            ccbar.get_git_info({}, "C:/repo")

        self.assertTrue(run_git.call_args_list)
        for call in run_git.call_args_list:
            self.assertEqual(call.args[2], "C:/repo")

    def test_parses_insertions_and_deletions_from_shortstat(self):
        shortstat = " 2 files changed, 7 insertions(+), 3 deletions(-)"
        with mock.patch.object(ccbar, "_run_git", side_effect=["main", shortstat, ""]):
            info = ccbar.get_git_info({}, "C:/repo")

        self.assertEqual((info["insertions"], info["deletions"]), (7, 3))

    def test_status_runs_porcelain_without_optional_locks(self):
        with mock.patch.object(ccbar, "_run_git", return_value="") as run_git:
            ccbar.get_git_info({}, "C:/repo")

        self.assertIn(
            mock.call(["--no-optional-locks", "status", "--porcelain"], 800, "C:/repo"), run_git.call_args_list
        )

    def test_untracked_only_change_is_dirty_with_zero_counts(self):
        with mock.patch.object(ccbar, "_run_git", side_effect=["main", "", "?? new.txt"]):
            info = ccbar.get_git_info({}, "C:/repo")

        self.assertTrue(info["dirty"])
        self.assertEqual((info["insertions"], info["deletions"]), (0, 0))

    def test_clean_porcelain_is_not_dirty(self):
        with mock.patch.object(ccbar, "_run_git", side_effect=["main", "", ""]):
            info = ccbar.get_git_info({}, "C:/repo")

        self.assertFalse(info["dirty"])


CLEAN_INFO = {"branch": "main", "insertions": 0, "deletions": 0, "dirty": False}
NOW = 1_700_000_000
DIRTY_INFO = {"branch": "main", "insertions": 5, "deletions": 1, "dirty": True}
GIT_CFG = {"git": {"cache_ttl_ms": 3000, "timeout_ms": 800}}


def _git_payload(cwd="C:/repo"):
    return {"session_id": "sess-1", "workspace": {"current_dir": cwd}}


class GitCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state_dir = ccbar.STATE_DIR
        ccbar.STATE_DIR = self._tmp.name
        self._git_tmp = tempfile.TemporaryDirectory()
        self.git_dir = self._git_tmp.name
        self.head_log = os.path.join(self.git_dir, "logs", "HEAD")
        os.makedirs(os.path.dirname(self.head_log))
        with open(self.head_log, "w", encoding="utf-8") as f:
            f.write("")
        os.utime(self.head_log, (1_700_000_000, 1_700_000_000))

    def tearDown(self):
        ccbar.STATE_DIR = self._orig_state_dir
        self._tmp.cleanup()
        self._git_tmp.cleanup()

    def _call(self, info, now, cwd="C:/repo"):
        with mock.patch.object(ccbar, "get_git_info", return_value=info) as get_git_info, \
                mock.patch.object(ccbar, "_run_git", return_value=self.git_dir):
            result = ccbar.cached_git_info(_git_payload(cwd), GIT_CFG, ccbar.load_state("sess-1"), now=now)
        return result, get_git_info.called

    def test_hit_within_ttl_skips_git_and_state_write(self):
        self._call(CLEAN_INFO, 1000.0)
        path = os.path.join(self._tmp.name, "sess-1.json")
        os.utime(path, (1_700_000_000, 1_700_000_000))

        _, refreshed = self._call(CLEAN_INFO, 1002.0)

        self.assertFalse(refreshed)
        self.assertEqual(os.path.getmtime(path), 1_700_000_000)

    def test_miss_after_ttl_refreshes(self):
        self._call(CLEAN_INFO, 1000.0)

        _, refreshed = self._call(CLEAN_INFO, 1003.5)

        self.assertTrue(refreshed)
        self.assertEqual(ccbar.load_state("sess-1")["git"]["ts"], 1003.5)

    def test_miss_on_cwd_change_refreshes(self):
        self._call(CLEAN_INFO, 1000.0)

        _, refreshed = self._call(CLEAN_INFO, 1001.0, cwd="C:/other")

        self.assertTrue(refreshed)
        self.assertEqual(ccbar.load_state("sess-1")["git"]["cwd"], "C:/other")

    def test_commit_inside_ttl_refreshes_dirty_cache(self):
        self._call(DIRTY_INFO, 1000.0)
        os.utime(self.head_log, (1_700_000_100, 1_700_000_100))

        result, refreshed = self._call(CLEAN_INFO, 1001.0)

        self.assertTrue(refreshed)
        self.assertFalse(result["dirty"])

    def test_dirty_cache_kept_when_head_log_unchanged(self):
        self._call(DIRTY_INFO, 1000.0)

        result, refreshed = self._call(CLEAN_INFO, 1001.0)

        self.assertFalse(refreshed)
        self.assertTrue(result["dirty"])

    def test_first_commit_inside_ttl_refreshes_dirty_cache(self):
        os.remove(self.head_log)
        self._call(DIRTY_INFO, 1000.0)
        with open(self.head_log, "w", encoding="utf-8") as f:
            f.write("")

        _, refreshed = self._call(CLEAN_INFO, 1001.0)

        self.assertTrue(refreshed)

    def test_cache_write_preserves_events(self):
        ccbar.save_state("sess-1", CROSSED_STATE)

        self._call(CLEAN_INFO, 1000.0)

        state = ccbar.load_state("sess-1")
        self.assertEqual(state["events"], CROSSED_STATE["events"])
        self.assertEqual(state["window_size"], CROSSED_STATE["window_size"])

    def test_unsafe_session_id_bypasses_cache(self):
        data = {**_git_payload(), "session_id": "../evil"}
        with mock.patch.object(ccbar, "get_git_info", return_value=CLEAN_INFO), \
                mock.patch.object(ccbar, "_run_git", return_value=self.git_dir):
            result = ccbar.cached_git_info(data, GIT_CFG, {}, now=1000.0)

        self.assertEqual(result, CLEAN_INFO)
        self.assertEqual(os.listdir(self._tmp.name), [])


class SafeSessionIdTests(unittest.TestCase):
    def test_valid_id_returned_unchanged(self):
        self.assertEqual(ccbar._safe_session_id("abc-123_X"), "abc-123_X")

    def test_unsafe_ids_return_none(self):
        for value in ["../x", "a/b", "a\\b", "", None, "a" * 129]:
            with self.subTest(value=value):
                self.assertIsNone(ccbar._safe_session_id(value))


class SanitizeLabelTests(unittest.TestCase):
    def test_removes_ansi_color_sequence(self):
        self.assertEqual(ccbar._sanitize_label("\x1b[31mmain\x1b[0m"), "main")

    def test_removes_other_control_characters(self):
        self.assertEqual(ccbar._sanitize_label("ma\x07in\x7f"), "main")

    def test_truncates_to_max_len(self):
        self.assertEqual(ccbar._sanitize_label("abcdef", max_len=3), "abc")

    def test_plain_branch_name_unchanged(self):
        self.assertEqual(ccbar._sanitize_label("feat/x-1"), "feat/x-1")


class RenderTests(unittest.TestCase):
    def test_branch_escape_sequence_is_stripped(self):
        git_info = {"branch": "\x1b[31mevil", "insertions": 0, "deletions": 0}
        data = {"workspace": {"current_dir": "C:/repo"}, "model": {"display_name": "X"}}
        cfg = {"no_color": True}

        out = ccbar.render(data, cfg, git_info=git_info)

        self.assertIn("evil", out)
        self.assertNotIn("\x1b", out)

    def _render_line1(self, cfg, events):
        data = {"workspace": {"current_dir": "C:/repo"}, "model": {"display_name": "X"}}
        git_info = {"branch": "", "insertions": 0, "deletions": 0}
        return ccbar.render(data, {"no_color": True, **cfg}, git_info=git_info, events=events).splitlines()[0]

    def _render_line2(self, cfg, git_info):
        data = {"workspace": {"current_dir": "C:/repo"}, "model": {"display_name": "X"}}
        return ccbar.render(data, {"no_color": True, **cfg}, git_info=git_info).splitlines()[1]

    def test_shows_handoff_marker_when_fired(self):
        line1 = self._render_line1({}, {"handoff": {"fired": True}})

        self.assertIn("handoff ✓", line1)

    def test_shows_handoff_done_under_ascii_fallback(self):
        line1 = self._render_line1({"icons": {"ascii_fallback": True}}, {"handoff": {"fired": True}})

        self.assertIn("handoff done", line1)

    def test_hides_handoff_marker_when_segment_off(self):
        line1 = self._render_line1({"segments": {"handoff": False}}, {"handoff": {"fired": True}})

        self.assertNotIn("handoff", line1)

    def test_shows_dirty_marker_after_branch(self):
        line2 = self._render_line2({"icons": {"branch": ""}}, {**CLEAN_INFO, "dirty": True})

        self.assertIn("main*", line2)

    def test_uses_icons_dirty_when_set(self):
        line2 = self._render_line2({"icons": {"branch": "", "dirty": "!"}}, {**CLEAN_INFO, "dirty": True})

        self.assertIn("main!", line2)

    def test_ascii_fallback_ignores_icons_dirty(self):
        line2 = self._render_line2({"icons": {"dirty": "!", "ascii_fallback": True}}, {**CLEAN_INFO, "dirty": True})

        self.assertIn("main*", line2)

    def test_hides_dirty_marker_when_segment_off(self):
        line2 = self._render_line2({"segments": {"git_dirty": False}}, {**CLEAN_INFO, "dirty": True})

        self.assertNotIn("*", line2)

    def test_shows_dirty_marker_without_branch(self):
        line2 = self._render_line2({}, {**CLEAN_INFO, "branch": "", "dirty": True})

        self.assertTrue(line2.endswith("*"))

    def test_render_without_git_info_shows_folder_only(self):
        data = {"workspace": {"current_dir": "C:/repo"}, "model": {"display_name": "X"}}

        line2 = ccbar.render(data, {"no_color": True}).splitlines()[1]

        self.assertEqual(line2, "📁 repo")

    def _render_with(self, extra, cfg=None, git_info=None, now=NOW):
        data = {"workspace": {"current_dir": "C:/repo"}, "model": {"display_name": "Opus 4.8"}, **extra}
        return ccbar.render(data, {"no_color": True, **(cfg or {})}, git_info=git_info, now=now).splitlines()

    def test_effort_shown_inside_model_bracket(self):
        line1 = self._render_with({"effort": {"level": "high"}})[0]

        self.assertTrue(line1.startswith("[Opus 4.8 · high]"))

    def test_absent_effort_leaves_model_label_unchanged(self):
        line1 = self._render_with({})[0]

        self.assertTrue(line1.startswith("[Opus 4.8]"))

    def test_effort_hidden_when_segment_off(self):
        line1 = self._render_with({"effort": {"level": "high"}}, {"segments": {"effort": False}})[0]

        self.assertTrue(line1.startswith("[Opus 4.8]"))

    def test_worktree_name_wins_over_git_worktree(self):
        extra = {"worktree": {"name": "wt-a"}, "workspace": {"current_dir": "C:/repo", "git_worktree": "wt-b"}}

        line2 = self._render_with(extra, git_info=CLEAN_INFO)[1]

        self.assertTrue(line2.endswith("⎇ wt-a"))

    def test_worktree_falls_back_to_git_worktree(self):
        extra = {"workspace": {"current_dir": "C:/repo", "git_worktree": "wt-b"}}

        line2 = self._render_with(extra, git_info=CLEAN_INFO)[1]

        self.assertTrue(line2.endswith("⎇ wt-b"))

    def test_worktree_ascii_fallback_prefix(self):
        line2 = self._render_with({"worktree": {"name": "wt-a"}}, {"icons": {"ascii_fallback": True}})[1]

        self.assertTrue(line2.endswith("wt wt-a"))

    def test_rate_limits_both_windows_with_countdown_on_5h(self):
        limits = {"five_hour": {"used_percentage": 23, "resets_at": NOW + 7800}, "seven_day": {"used_percentage": 41}}

        line2 = self._render_with({"rate_limits": limits})[1]

        self.assertEqual(line2, "📁 repo | 5h 23% (2h10m)  7d 41%")

    def test_rate_limits_without_seven_day_renders_5h_only(self):
        line2 = self._render_with({"rate_limits": {"five_hour": {"used_percentage": 23}}})[1]

        self.assertEqual(line2, "📁 repo | 5h 23%")

    def test_rate_limits_past_reset_drops_countdown(self):
        limits = {"five_hour": {"used_percentage": 23, "resets_at": NOW - 1}}

        line2 = self._render_with({"rate_limits": limits})[1]

        self.assertEqual(line2, "📁 repo | 5h 23%")

    def test_rate_limits_without_now_drops_countdown(self):
        limits = {"five_hour": {"used_percentage": 23, "resets_at": NOW + 7800}}

        line2 = self._render_with({"rate_limits": limits}, now=None)[1]

        self.assertEqual(line2, "📁 repo | 5h 23%")

    def test_rate_limits_non_numeric_percentage_skips_window(self):
        limits = {"five_hour": {"used_percentage": "23"}, "seven_day": {"used_percentage": 41}}

        line2 = self._render_with({"rate_limits": limits})[1]

        self.assertEqual(line2, "📁 repo | 7d 41%")

    def test_no_rate_limits_leaves_line2_unchanged(self):
        line2 = self._render_with({})[1]

        self.assertEqual(line2, "📁 repo")

    def test_session_name_rendered_last(self):
        extra = {"session_name": "fix-bar", "rate_limits": {"five_hour": {"used_percentage": 23}}}

        line2 = self._render_with(extra)[1]

        self.assertEqual(line2, "📁 repo | 5h 23% | « fix-bar »")

    def test_session_name_strips_control_chars_and_ansi(self):
        line2 = self._render_with({"session_name": "[31mfix-bar"})[1]

        self.assertEqual(line2, "📁 repo | « fix-bar »")

    def test_session_name_truncated_to_max(self):
        line2 = self._render_with({"session_name": "x" * 50})[1]

        self.assertEqual(line2, "📁 repo | « " + "x" * ccbar.SESSION_NAME_MAX + " »")

    def test_line2_without_folder_starts_with_rate_limits(self):
        extra = {"workspace": {"current_dir": ""}, "rate_limits": {"five_hour": {"used_percentage": 23}}}

        line2 = self._render_with(extra)[1]

        self.assertEqual(line2, "5h 23%")

    def test_malformed_new_fields_render_nothing(self):
        extra = {"rate_limits": "garbage", "effort": 5, "session_name": None, "worktree": "x"}

        lines = self._render_with(extra)

        self.assertEqual(lines, ["[Opus 4.8]  " + "░" * 20 + "  0.0% (0 / 200,000) | $0.0000", "📁 repo"])


class FmtDurationTests(unittest.TestCase):
    def test_under_a_minute_is_zero_minutes(self):
        self.assertEqual(ccbar._fmt_duration(59), "0m")

    def test_hours_and_minutes(self):
        self.assertEqual(ccbar._fmt_duration(7800), "2h10m")

    def test_days_and_hours(self):
        self.assertEqual(ccbar._fmt_duration(266400), "3d2h")


class PctColorTests(unittest.TestCase):
    def test_at_green_threshold_is_green(self):
        self.assertEqual(ccbar._pct_color(60, {}), "green")

    def test_just_above_green_is_yellow(self):
        self.assertEqual(ccbar._pct_color(60.1, {}), "yellow")

    def test_above_yellow_is_red(self):
        self.assertEqual(ccbar._pct_color(80.1, {}), "red")


if __name__ == "__main__":
    unittest.main()
