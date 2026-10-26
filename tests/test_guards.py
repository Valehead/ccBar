import contextlib, io, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import guards

ENABLED = {"guards": {"line_endings": {"enabled": True}}}
DISABLED = {"guards": {"line_endings": {"enabled": False}}}
EM_DASH = "—"


def posted_text_cfg(enabled=True, banned=(EM_DASH,)):
    return {"guards": {"posted_text": {"enabled": enabled, "banned": list(banned)}}}


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


class IsPostingCommandTests(unittest.TestCase):
    def test_posting_commands_match(self):
        commands = (
            "gh pr create --body x",
            "gh issue comment 3 -b x",
            "gh api repos/o/r/pulls/1/reviews -f body=x",
            "az repos pr update --id 1 -d x",
            "az boards work-item update --id 1 --discussion x",
            "az devops invoke --area git --resource pullRequestThreads --in-file t.json",
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(guards.is_posting_command(command))

    def test_non_posting_commands_do_not_match(self):
        commands = ("git status", "gh pr view 3", "gh api repos/o/r/pulls", "az repos pr list")

        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(guards.is_posting_command(command))


class FindBannedTests(unittest.TestCase):
    def test_returns_hit(self):
        self.assertEqual(guards.find_banned("a " + EM_DASH + " b", [EM_DASH]), EM_DASH)

    def test_absent_gives_none(self):
        self.assertIsNone(guards.find_banned("a - b", [EM_DASH]))

    def test_empty_entry_does_not_match(self):
        self.assertIsNone(guards.find_banned("a - b", [""]))

    def test_empty_list_gives_none(self):
        self.assertIsNone(guards.find_banned("a " + EM_DASH + " b", []))


class ReferencedFilesTests(unittest.TestCase):
    def test_body_file_next_token(self):
        self.assertEqual(guards.referenced_files("gh pr create --body-file b.md", True), ["b.md"])

    def test_body_file_equals_form(self):
        self.assertEqual(guards.referenced_files("gh pr create --body-file=b.md", True), ["b.md"])

    def test_short_f_on_gh_pr(self):
        self.assertEqual(guards.referenced_files("gh pr create -F b.md", True), ["b.md"])

    def test_gh_api_field_from_file(self):
        self.assertEqual(guards.referenced_files("gh api x -F body=@b.md", True), ["b.md"])

    def test_at_value(self):
        self.assertEqual(guards.referenced_files("gh pr create --body @b.md", True), ["b.md"])

    def test_in_file(self):
        self.assertEqual(guards.referenced_files("az devops invoke --in-file t.json", True), ["t.json"])

    def test_stdin_dash_is_skipped(self):
        self.assertEqual(guards.referenced_files("gh api x --input -", True), [])

    def test_unbalanced_quotes_give_empty_list(self):
        self.assertEqual(guards.referenced_files('gh pr create --body "x', True), [])

    def test_non_posix_keeps_backslash_path(self):
        files = guards.referenced_files('gh pr create --body-file "C:\\tmp\\b.md"', False)

        self.assertEqual(files, ["C:\\tmp\\b.md"])


class PostedTextMessageTests(unittest.TestCase):
    def test_one_char_entry_includes_codepoint(self):
        self.assertEqual(
            guards.posted_text_message(EM_DASH, "the command"),
            'Posted text contains banned text "' + EM_DASH + '" (U+2014) in the command. '
            "Rewrite it without that text and run the command again.",
        )

    def test_multi_char_entry_has_no_codepoint(self):
        self.assertEqual(
            guards.posted_text_message("--", "the command"),
            'Posted text contains banned text "--" in the command. '
            "Rewrite it without that text and run the command again.",
        )


class RunPostedTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = os.path.join(self.tmp.name, "b.md")
        with open(self.body, "w", encoding="utf-8") as f:
            f.write("a " + EM_DASH + " b\n")

    def payload(self, command, **extra):
        return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}

    def test_disabled_returns_0(self):
        code = guards.run_posted_text(self.payload("gh pr create --body x" + EM_DASH), posted_text_cfg(enabled=False))

        self.assertEqual(code, 0)

    def test_non_posting_command_returns_0_without_reading_files(self):
        with mock.patch("builtins.open") as opened:
            code = guards.run_posted_text(self.payload("cat --body-file " + self.body), posted_text_cfg())

        self.assertEqual(code, 0)
        opened.assert_not_called()

    def test_banned_text_in_command_returns_2_and_writes_message(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            code = guards.run_posted_text(self.payload('gh pr create --body "a ' + EM_DASH + ' b"'), posted_text_cfg())

        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue(), guards.posted_text_message(EM_DASH, "the command") + "\n")

    def test_banned_text_in_body_file_returns_2_naming_file(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            code = guards.run_posted_text(self.payload("gh pr create --body-file " + self.body.replace("\\", "/")), posted_text_cfg())

        self.assertEqual(code, 2)
        self.assertIn(self.body.replace("\\", "/"), stderr.getvalue())

    def test_missing_body_file_returns_0(self):
        missing = os.path.join(self.tmp.name, "missing.md").replace("\\", "/")

        code = guards.run_posted_text(self.payload("gh pr create --body-file " + missing), posted_text_cfg())

        self.assertEqual(code, 0)

    def test_relative_body_file_resolves_against_cwd(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = guards.run_posted_text(self.payload("gh pr create --body-file b.md", cwd=self.tmp.name), posted_text_cfg())

        self.assertEqual(code, 2)

    def test_empty_banned_list_returns_0(self):
        code = guards.run_posted_text(self.payload("gh pr create --body-file b.md", cwd=self.tmp.name), posted_text_cfg(banned=()))

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
