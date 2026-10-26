import contextlib, io, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import guards

ENABLED = {"guards": {"line_endings": {"enabled": True}}}
DISABLED = {"guards": {"line_endings": {"enabled": False}}}


class CountLineEndingsTests(unittest.TestCase):
    def test_mixed_bytes(self):
        self.assertEqual(guards.count_line_endings(b"a\r\nb\nc\r\nd\n"), (2, 2))

    def test_empty(self):
        self.assertEqual(guards.count_line_endings(b""), (0, 0))


class LineEndingProblemTests(unittest.TestCase):
    def test_crlf_expected_with_lf_lines_gives_message(self):
        problem = guards.line_ending_problem("f.txt", b"a\r\nb\r\n", b"a\nb\r\n")

        self.assertEqual(
            problem,
            "f.txt: expected CRLF line endings (git checkout form), found 1 LF line(s). Convert them back to CRLF.",
        )

    def test_lf_expected_with_crlf_line_gives_message(self):
        problem = guards.line_ending_problem("f.txt", b"a\nb\n", b"a\r\nb\n")

        self.assertEqual(
            problem,
            "f.txt: expected LF line endings (git checkout form), found 1 CRLF line(s). Convert them back to LF.",
        )

    def test_matching_styles_give_none(self):
        self.assertIsNone(guards.line_ending_problem("f.txt", b"a\r\nb\r\n", b"a\r\nb\r\nc\r\n"))

    def test_no_newlines_in_expected_gives_none(self):
        self.assertIsNone(guards.line_ending_problem("f.txt", b"abc", b"a\r\nb\n"))

    def test_final_line_without_newline_is_not_a_violation(self):
        self.assertIsNone(guards.line_ending_problem("f.txt", b"a\r\nb\r\n", b"a\r\nb\r\nc"))


class RunLineEndingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "f.txt")
        with open(self.path, "wb") as f:
            f.write(b"a\nb\n")
        self.payload = {"tool_input": {"file_path": self.path}}
        self.toplevel = (self.tmp.name + "\n").encode()

    def test_disabled_returns_0_without_calling_git(self):
        with mock.patch.object(guards, "_git") as git:
            code = guards.run_line_endings(self.payload, DISABLED)

        self.assertEqual(code, 0)
        git.assert_not_called()

    def test_missing_file_path_returns_0(self):
        with mock.patch.object(guards, "_git") as git:
            code = guards.run_line_endings({"tool_input": {}}, ENABLED)

        self.assertEqual(code, 0)
        git.assert_not_called()

    def test_outside_a_repo_returns_0(self):
        with mock.patch.object(guards, "_git", return_value=None):
            code = guards.run_line_endings(self.payload, ENABLED)

        self.assertEqual(code, 0)

    def test_untracked_file_returns_0(self):
        with mock.patch.object(guards, "_git", side_effect=[self.toplevel, None]):
            code = guards.run_line_endings(self.payload, ENABLED)

        self.assertEqual(code, 0)

    def test_flip_returns_2_and_writes_message_to_stderr(self):
        stderr = io.StringIO()

        with mock.patch.object(guards, "_git", side_effect=[self.toplevel, b"a\r\nb\r\n"]):
            with contextlib.redirect_stderr(stderr):
                code = guards.run_line_endings(self.payload, ENABLED)

        self.assertEqual(code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "f.txt: expected CRLF line endings (git checkout form), found 2 LF line(s). Convert them back to CRLF.\n",
        )


if __name__ == "__main__":
    unittest.main()
