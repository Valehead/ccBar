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
            "az rest --method post --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads?api-version=7.1 --body x",
            "az rest --method post --uri https://dev.azure.com/o/p/_apis/wit/workItems/5/comments?api-version=7.1-preview.4 --body x",
            '$uri = "https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads"; az rest --method post --uri $uri --body x',
            "az rest --method post --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/Threads --body x",
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(guards.is_posting_command(command))

    def test_non_posting_commands_do_not_match(self):
        commands = (
            "git status",
            "gh pr view 3",
            "gh api repos/o/r/pulls",
            "az repos pr list",
            "az rest --method post --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1",
        )

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

    def test_az_rest_body_at_file(self):
        self.assertEqual(guards.referenced_files("az rest --method post --uri x/threads --body @body.json", True), ["body.json"])

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

    def test_az_rest_thread_post_with_banned_inline_body_returns_2(self):
        command = (
            "az rest --method post --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads?api-version=7.1 "
            "--body '{\"comments\":[{\"content\":\"a " + EM_DASH + " b\"}]}'"
        )

        with contextlib.redirect_stderr(io.StringIO()):
            code = guards.run_posted_text(self.payload(command), posted_text_cfg())

        self.assertEqual(code, 2)

    def test_az_rest_thread_post_with_banned_body_file_returns_2(self):
        command = "az rest --method post --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads --body @b.md"

        with contextlib.redirect_stderr(io.StringIO()):
            code = guards.run_posted_text(self.payload(command, cwd=self.tmp.name), posted_text_cfg())

        self.assertEqual(code, 2)

    def test_powershell_uri_variable_before_az_rest_with_banned_body_file_returns_2(self):
        command = (
            '$uri = "https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads?api-version=7.1"; '
            'az rest --method post --uri $uri --body "@b.md"'
        )

        with contextlib.redirect_stderr(io.StringIO()):
            code = guards.run_posted_text(self.payload(command, tool_name="PowerShell", cwd=self.tmp.name), posted_text_cfg())

        self.assertEqual(code, 2)

    def test_az_rest_thread_get_without_body_returns_0(self):
        command = "az rest --method get --uri https://dev.azure.com/o/p/_apis/git/repositories/r/pullRequests/1/threads"

        code = guards.run_posted_text(self.payload(command, cwd=self.tmp.name), posted_text_cfg())

        self.assertEqual(code, 0)

    def test_empty_banned_list_returns_0(self):
        code = guards.run_posted_text(self.payload("gh pr create --body-file b.md", cwd=self.tmp.name), posted_text_cfg(banned=()))

        self.assertEqual(code, 0)


PY_ROW = {"glob": "*.py", "root_marker": "setup.cfg", "command": ["tool", "build"]}
CS_ROW = {"glob": "*.cs", "root_marker": ["*.sln", "*.slnx"], "command": ["dotnet", "build"]}
TOP = os.path.normpath("/r")


def build_gate_cfg(enabled=True, builds=(PY_ROW,)):
    return {"guards": {"build_gate": {"enabled": enabled, "builds": list(builds)}}}


def fake_tree(tree):
    """Map repo-relative dirs to their entries; return (isdir, listdir) over it."""
    full = {os.path.normpath(os.path.join(TOP, d)): entries for d, entries in tree.items()}
    return full.__contains__, full.__getitem__


class IsPushCommandTests(unittest.TestCase):
    def test_push_commands_match(self):
        commands = ("git push", "git push -u origin x", "git -C ../r push", "gh pr create --fill", "cd r && git push")

        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(guards.is_push_command(command))

    def test_non_push_commands_do_not_match(self):
        commands = ("git status", "git pull", "gh pr view", "echo push")

        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(guards.is_push_command(command))


class PushDirTests(unittest.TestCase):
    def test_leading_cd_gives_that_dir(self):
        self.assertEqual(guards.push_dir("cd sub && git push", "/w", True), os.path.join("/w", "sub"))

    def test_git_dash_c_absolute_dir(self):
        self.assertEqual(guards.push_dir("git -C /abs push", "/w", True), "/abs")

    def test_plain_push_gives_cwd(self):
        self.assertEqual(guards.push_dir("git push", "/w", True), "/w")

    def test_unbalanced_quotes_give_cwd(self):
        self.assertEqual(guards.push_dir('git -C "r push', "/w", True), "/w")

    def test_non_posix_keeps_backslash_dir(self):
        self.assertEqual(guards.push_dir('git -C "C:\\r" push', "C:\\w", False), os.path.join("C:\\w", "C:\\r"))


class MatchingRootsTests(unittest.TestCase):
    def test_nearest_marker_wins(self):
        isdir, listdir = fake_tree({"": ["setup.cfg"], "pkg": ["setup.cfg"], "pkg/src": ["a.py"]})

        roots = guards.matching_roots(["pkg/src/a.py"], [PY_ROW], TOP, isdir, listdir)

        self.assertEqual(roots, [(os.path.join(TOP, "pkg"), ("tool", "build"))])

    def test_two_files_under_one_root_give_one_entry(self):
        isdir, listdir = fake_tree({"": ["setup.cfg", "a.py", "b.py"]})

        roots = guards.matching_roots(["a.py", "b.py"], [PY_ROW], TOP, isdir, listdir)

        self.assertEqual(roots, [(TOP, ("tool", "build"))])

    def test_file_matching_no_glob_gives_nothing(self):
        isdir, listdir = fake_tree({"": ["setup.cfg", "README.md"]})

        self.assertEqual(guards.matching_roots(["README.md"], [PY_ROW], TOP, isdir, listdir), [])

    def test_no_marker_up_to_top_gives_nothing(self):
        isdir, listdir = fake_tree({"": ["pkg"], "pkg": ["a.py"]})

        self.assertEqual(guards.matching_roots(["pkg/a.py"], [PY_ROW], TOP, isdir, listdir), [])

    def test_deleted_files_missing_dir_is_skipped_and_walk_continues(self):
        isdir, listdir = fake_tree({"": ["setup.cfg"]})

        roots = guards.matching_roots(["gone/a.py"], [PY_ROW], TOP, isdir, listdir)

        self.assertEqual(roots, [(TOP, ("tool", "build"))])

    def test_invalid_rows_are_skipped(self):
        isdir, listdir = fake_tree({"": ["setup.cfg", "a.py"]})
        rows = [dict(PY_ROW, command="tool build"), {"glob": "*.py", "command": ["tool"]}]

        self.assertEqual(guards.matching_roots(["a.py"], rows, TOP, isdir, listdir), [])

    def test_list_marker_finds_slnx_root(self):
        isdir, listdir = fake_tree({"": ["x.slnx", "a.cs"]})

        roots = guards.matching_roots(["a.cs"], [CS_ROW], TOP, isdir, listdir)

        self.assertEqual(roots, [(TOP, ("dotnet", "build"))])

    def test_list_marker_finds_sln_root(self):
        isdir, listdir = fake_tree({"": ["x.sln", "a.cs"]})

        roots = guards.matching_roots(["a.cs"], [CS_ROW], TOP, isdir, listdir)

        self.assertEqual(roots, [(TOP, ("dotnet", "build"))])

    def test_invalid_root_markers_skip_the_row(self):
        isdir, listdir = fake_tree({"": ["x.sln", "a.cs"]})

        for marker in ([], ["*.sln", 3], 3):
            with self.subTest(marker=marker):
                rows = [dict(CS_ROW, root_marker=marker)]

                self.assertEqual(guards.matching_roots(["a.cs"], rows, TOP, isdir, listdir), [])


class TrimOutputTests(unittest.TestCase):
    def test_keeps_last_n_non_blank_lines(self):
        self.assertEqual(guards.trim_output("a\n\nb\n  \nc\nd\n", n=2), "c\nd")


class RunBuildTests(unittest.TestCase):
    def test_tool_not_on_path_gives_none(self):
        with mock.patch("guards.shutil.which", return_value=None):
            result = guards.run_build(TOP, ["tool", "build"])

        self.assertEqual(result, (None, "tool not found on PATH"))

    def test_timeout_gives_none(self):
        with mock.patch("guards.shutil.which", return_value="tool"), \
                mock.patch("guards.subprocess.run", side_effect=guards.subprocess.TimeoutExpired("tool", 840)):
            result = guards.run_build(TOP, ["tool", "build"])

        self.assertEqual(result, (None, "timed out after 840s"))


class RunBuildGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ("setup.cfg", "a.py"):
            open(os.path.join(self.tmp.name, name), "w").close()
        self.root = os.path.normpath(self.tmp.name)
        self.git_replies = [(self.tmp.name + "\n").encode(), b"origin/main\n", b"abc\n", b"a.py\n"]

    def payload(self, command):
        return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.tmp.name}

    def run_gate(self, returncode=0, which="tool"):
        stderr = io.StringIO()
        build = subprocess_result(returncode, b"", b"a.py:1: error\n")
        with mock.patch.object(guards, "_git", side_effect=self.git_replies), \
                mock.patch("guards.shutil.which", return_value=which), \
                mock.patch("guards.subprocess.run", return_value=build), \
                contextlib.redirect_stderr(stderr):
            code = guards.run_build_gate(self.payload("git push"), build_gate_cfg())
        return code, stderr.getvalue()

    def test_disabled_returns_0_without_calling_git(self):
        with mock.patch.object(guards, "_git") as git:
            code = guards.run_build_gate(self.payload("git push"), build_gate_cfg(enabled=False))

        self.assertEqual(code, 0)
        git.assert_not_called()

    def test_empty_builds_returns_0_without_calling_git(self):
        with mock.patch.object(guards, "_git") as git:
            code = guards.run_build_gate(self.payload("git push"), build_gate_cfg(builds=()))

        self.assertEqual(code, 0)
        git.assert_not_called()

    def test_non_push_command_returns_0_without_calling_git(self):
        with mock.patch.object(guards, "_git") as git:
            code = guards.run_build_gate(self.payload("git status"), build_gate_cfg())

        self.assertEqual(code, 0)
        git.assert_not_called()

    def test_no_upstream_and_no_origin_head_returns_0(self):
        with mock.patch.object(guards, "_git", side_effect=[self.git_replies[0], None, None]):
            code = guards.run_build_gate(self.payload("git push"), build_gate_cfg())

        self.assertEqual(code, 0)

    def test_failing_build_returns_2_with_header_on_stderr(self):
        code, stderr = self.run_gate(returncode=1)

        self.assertEqual(code, 2)
        self.assertEqual(
            stderr,
            f"Build failed in {self.root} (tool build). Fix these errors before pushing:\na.py:1: error\n",
        )

    def test_passing_build_returns_0(self):
        code, stderr = self.run_gate(returncode=0)

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")

    def test_missing_tool_returns_0_with_skipped_line(self):
        code, stderr = self.run_gate(which=None)

        self.assertEqual(code, 0)
        self.assertEqual(stderr, f"guards.py: build gate skipped {self.root}: tool not found on PATH\n")


def subprocess_result(returncode, stdout, stderr):
    return guards.subprocess.CompletedProcess(["tool"], returncode, stdout, stderr)


if __name__ == "__main__":
    unittest.main()
