import unittest

import gh_suggest as gs


STAGED = """diff --git a/src/client.py b/src/client.py
--- a/src/client.py
+++ b/src/client.py
@@ -42 +42 @@
-timeout=5
+timeout=10
"""

PR = """diff --git a/src/client.py b/src/client.py
--- a/src/client.py
+++ b/src/client.py
@@ -41,2 +41,2 @@
 url="https://example.com"
-timeout=1
+timeout=5
"""


class SuggestTests(unittest.TestCase):
    def test_parses_staged_zero_context_diff(self):
        hunks = gs.parse_unified_diff(STAGED)
        self.assertEqual(hunks[0].path, "src/client.py")
        self.assertEqual(hunks[0].new_start, 42)
        self.assertEqual(hunks[0].added, ["timeout=10"])

    def test_maps_simple_replacement(self):
        suggestions, skips = gs.map_suggestions(
            gs.parse_unified_diff(STAGED),
            gs.pr_added_lines(gs.parse_unified_diff(PR)),
            30,
        )
        self.assertEqual(skips, [])
        self.assertEqual(suggestions[0].path, "src/client.py")
        self.assertEqual(suggestions[0].line, 42)

    def test_maps_multiline_replacement(self):
        staged = STAGED.replace("+timeout=10", "+timeout=10\n+retry_after=2").replace("@@ -42 +42 @@", "@@ -42 +42,2 @@")
        suggestions, skips = gs.map_suggestions(gs.parse_unified_diff(staged), gs.pr_added_lines(gs.parse_unified_diff(PR)), 30)
        self.assertEqual(skips, [])
        self.assertIn("retry_after=2", suggestions[0].body)

    def test_skips_file_outside_pr(self):
        suggestions, skips = gs.map_suggestions(gs.parse_unified_diff(STAGED), {}, 30)
        self.assertEqual(suggestions, [])
        self.assertEqual(skips[0].reason, "file not in PR diff")

    def test_skips_oversized_hunk(self):
        hunk = gs.parse_unified_diff(STAGED)[0]
        hunk.added = ["x", "y"]
        suggestions, skips = gs.map_suggestions([hunk], gs.pr_added_lines(gs.parse_unified_diff(PR)), 1)
        self.assertEqual(suggestions, [])
        self.assertEqual(skips[0].reason, "hunk too large")

    def test_skips_binary_file(self):
        hunk = gs.parse_unified_diff("""diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
@@ -1 +1 @@
-old
+new
""")[0]
        suggestions, skips = gs.map_suggestions([hunk], {"logo.png": {1: 1}}, 30)
        self.assertEqual(suggestions, [])
        self.assertEqual(skips[0].reason, "binary file")

    def test_skips_new_file(self):
        hunk = gs.parse_unified_diff("""diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+print("hi")
""")[0]
        suggestions, skips = gs.map_suggestions([hunk], {"new.py": {1: 1}}, 30)
        self.assertEqual(suggestions, [])
        self.assertEqual(skips[0].reason, "new file not supported")

    def test_renders_suggestion_markdown(self):
        self.assertEqual(gs.render_suggestion(["a", "b"]), "```suggestion\na\nb\n```")

    def test_preview_does_not_need_network(self):
        text = gs.preview("123", [gs.Suggestion("a.py", 1, "body", "x")], [gs.Skip("b.py", 2, "line not in PR diff")])
        self.assertIn("Will post 1 suggestion", text)
        self.assertIn("Skipped 1 hunk", text)
        self.assertIn("gh pr diff", text)

    def test_permission_error_has_fix(self):
        text = gs.explain_error(RuntimeError("gh api graphql failed: Resource not accessible by integration"))
        self.assertIn("gh auth refresh", text)

    def test_nothing_posted_exit_code(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["gh", "auth"]:
                return ""
            if cmd[:2] == ["git", "diff"]:
                return ""
            return PR

        old_run = gs.run
        gs.run = fake_run
        try:
            self.assertEqual(gs.main(["123", "--dry-run"]), 1)
        finally:
            gs.run = old_run
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
