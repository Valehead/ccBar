import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ccbar

RETRO_CFG = {"enabled": True, "threshold_tokens": 400000, "threshold_pct": 70}
CFG = {"retro": RETRO_CFG}
CROSSED_STATE = {"crossed": True, "retro_fired": False, "tokens": 410000, "window_size": 1000000}
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
        state = {**CROSSED_STATE, "retro_fired": True}

        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, state, CFG))

    def test_returns_none_when_not_crossed(self):
        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, {}, CFG))

    def test_returns_none_when_disabled(self):
        cfg = {"retro": {**RETRO_CFG, "enabled": False}}

        self.assertIsNone(ccbar.stop_hook_output(PAYLOAD, CROSSED_STATE, cfg))

    def test_returns_additional_context_naming_skill_and_transcript(self):
        out = ccbar.stop_hook_output(PAYLOAD, CROSSED_STATE, CFG)

        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "Stop")
        self.assertIn("session-retro", hso["additionalContext"])
        self.assertIn(PAYLOAD["transcript_path"], hso["additionalContext"])


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

    def test_prune_removes_files_older_than_max_age(self):
        now = 1_800_000_000
        ccbar.save_state("old", CROSSED_STATE)
        ccbar.save_state("fresh", CROSSED_STATE)
        old_time = now - 8 * 86400
        os.utime(os.path.join(self._tmp.name, "old.json"), (old_time, old_time))
        os.utime(os.path.join(self._tmp.name, "fresh.json"), (now, now))

        ccbar.prune_state(now=now)

        self.assertEqual(os.listdir(self._tmp.name), ["fresh.json"])

    def test_update_retro_state_writes_once_on_crossing(self):
        below = ccbar.update_retro_state(_status_payload(100000), CFG)
        state_path = os.path.join(self._tmp.name, "sess-1.json")
        self.assertEqual(below, {})
        self.assertFalse(os.path.exists(state_path))

        first = ccbar.update_retro_state(_status_payload(450000), CFG)
        self.assertTrue(first["crossed"])

        second = ccbar.update_retro_state(_status_payload(600000), CFG)
        self.assertEqual(second["tokens"], 450000)


if __name__ == "__main__":
    unittest.main()
