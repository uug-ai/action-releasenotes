#!/usr/bin/env python3

import unittest
from unittest import mock

import generate_releasenotes as notes


def pull_request(number, title="Add feature", body="", labels=(), author_type="User", paths=(), commits=()):
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/example/repo/pull/{number}",
        "author": {"login": "someone", "__typename": author_type},
        "labels": list(labels),
        "paths": list(paths),
        "commits": list(commits),
    }


class CleanPullRequestBodyTest(unittest.TestCase):
    def test_drops_internal_heading_sections(self):
        body = (
            "## Live Environment\n\nAccess it [here](https://pr1.example).\n\n"
            "## Description\n\n- Users can pick a tile shape.\n\n"
            "### Implementation details\n- Added a helper.\n\n"
            "### Tests\n- Covers the helper.\n\n"
            "### Accessibility\n- Buttons have labels.\n"
        )
        cleaned = notes.clean_pull_request_body(body)
        self.assertIn("Users can pick a tile shape", cleaned)
        self.assertIn("Buttons have labels", cleaned)
        self.assertNotIn("pr1.example", cleaned)
        self.assertNotIn("Added a helper", cleaned)
        self.assertNotIn("Covers the helper", cleaned)

    def test_drops_internal_bullet_sections(self):
        body = (
            "- Motivation\n  - Keep tiles consistent.\n"
            "- Implementation details\n  - Added a helper.\n"
            "- Tests\n  - Covers the helper.\n"
            "- Accessibility\n  - Buttons have labels.\n"
        )
        cleaned = notes.clean_pull_request_body(body)
        self.assertIn("Keep tiles consistent", cleaned)
        self.assertIn("Buttons have labels", cleaned)
        self.assertNotIn("Added a helper", cleaned)
        self.assertNotIn("Covers the helper", cleaned)

    def test_drops_bare_label_sections(self):
        body = (
            "- Remove the 60-minute sync cap.\n\n"
            "Implementation details\n- FilterComponent\n  - Removed a constant.\n\n"
            "Rollout\n- Enable after the index exists.\n"
        )
        cleaned = notes.clean_pull_request_body(body)
        self.assertIn("Remove the 60-minute sync cap", cleaned)
        self.assertIn("Enable after the index exists", cleaned)
        self.assertNotIn("FilterComponent", cleaned)

    def test_strips_comments_and_images(self):
        body = "<!-- template -->Fixes upload retries.\n![shot](https://x/y.png)\n<img src=\"z.png\">"
        self.assertEqual(notes.clean_pull_request_body(body), "Fixes upload retries.")


class SkipReasonTest(unittest.TestCase):
    labels = {"dependencies", "chore"}

    def test_keeps_feature_pull_requests(self):
        self.assertIsNone(notes.pull_request_skip_reason(pull_request(1, "Let owners edit tags"), self.labels))

    def test_skips_bots_labels_and_housekeeping_titles(self):
        self.assertIn("bot", notes.pull_request_skip_reason(pull_request(1, author_type="Bot"), self.labels))
        self.assertIn("label", notes.pull_request_skip_reason(pull_request(1, labels=["Dependencies"]), self.labels))
        for title in ("Bump amqp091-go to v1.13.0", "chore(deps): update go", "docs: add notes", "Release v1.2.3"):
            self.assertEqual(notes.pull_request_skip_reason(pull_request(1, title), self.labels), "title", title)


class BudgetTest(unittest.TestCase):
    def test_short_texts_stay_whole_and_long_ones_share_the_rest(self):
        self.assertEqual(notes.allocate_budgets([100, 5000, 5000], 3100), [100, 1500, 1500])
        self.assertEqual(notes.allocate_budgets([10, 20], 1000), [10, 20])

    def test_context_stays_within_budget(self):
        pull_requests = [pull_request(number, body="word " * 2000) for number in range(1, 15)]
        context = notes.build_pull_request_context(pull_requests, ["Direct fix"], budget=8000)
        self.assertLessEqual(len(context), 8000 + 15 * len("\n(…truncated)"))
        for number in range(1, 15):
            self.assertIn(f"### PR #{number}:", context)
        self.assertIn("- Direct fix", context)

    def test_short_bodies_fall_back_to_commit_headlines(self):
        context = notes.build_pull_request_context(
            [pull_request(7, body="", commits=["Store slim snapshots", "Throttle updates"])], []
        )
        self.assertIn("- Store slim snapshots", context)


class CitationTest(unittest.TestCase):
    pull_requests = {12: pull_request(12), 15: pull_request(15)}

    def test_links_valid_citations_and_drops_unknown_ones(self):
        summary = "\n".join([
            "- Added tile shapes (#12).",
            "- Faster uploads (#15, #99)",
            "- Invented feature (#99)",
            "- Uncited bullet",
            "- Retries uploads (PR #12 and #15)",
        ])
        self.assertEqual(
            notes.link_pull_request_citations(summary, self.pull_requests).splitlines(),
            [
                "- Added tile shapes ([#12](https://github.com/example/repo/pull/12))",
                "- Faster uploads ([#15](https://github.com/example/repo/pull/15))",
                "- Uncited bullet",
                "- Retries uploads ([#12](https://github.com/example/repo/pull/12), "
                "[#15](https://github.com/example/repo/pull/15))",
            ],
        )


class RangeMappingTest(unittest.TestCase):
    def test_graphql_url(self):
        self.assertEqual(notes.graphql_url("https://api.github.com"), "https://api.github.com/graphql")
        self.assertEqual(notes.graphql_url("https://ghe.example/api/v3"), "https://ghe.example/api/graphql")

    def test_pull_requests_belong_to_the_range_through_their_merge_commit(self):
        commits = [
            {"sha": "a" * 40, "parents": [{}], "commit": {"message": "Feature work"}},
            {"sha": "b" * 40, "parents": [{}, {}], "commit": {"message": "Merge pull request #5"}},
            {"sha": "c" * 40, "parents": [{}], "commit": {"message": "Direct fix"}},
        ]
        response = {"repository": {
            "c0": {"associatedPullRequests": {"nodes": [
                {"number": 5, "mergeCommit": {"oid": "b" * 40}},
                {"number": 3, "mergeCommit": {"oid": "f" * 40}},
            ]}},
            "c1": {"associatedPullRequests": {"nodes": [{"number": 5, "mergeCommit": {"oid": "b" * 40}}]}},
            "c2": {"associatedPullRequests": {"nodes": []}},
        }}
        with mock.patch.object(notes, "run_graphql", return_value=response):
            numbers, direct = notes.find_merged_pull_requests("https://api.github.com", "example/repo", commits, {})
        self.assertEqual(numbers, [5])
        self.assertEqual([commit["sha"] for commit in direct], ["c" * 40])


def commit(sha, message, parents=1, author=None):
    return {
        "sha": sha,
        "parents": [{}] * parents,
        "author": author or {"login": "someone", "type": "User"},
        "html_url": f"https://github.com/example/repo/commit/{sha}",
        "commit": {"message": message},
    }


class CommitMessageTest(unittest.TestCase):
    def test_strips_trailers(self):
        subject, body = notes.clean_commit_message(
            "Cache heartbeat credentials\n\nOnly successes are cached.\n\n"
            "Co-Authored-By: Claude <noreply@anthropic.com>\nSigned-off-by: Dev <dev@example.com>\n"
        )
        self.assertEqual(subject, "Cache heartbeat credentials")
        self.assertEqual(body, "Only successes are cached.")

    def test_skips_merges_bots_and_housekeeping(self):
        self.assertEqual(notes.commit_skip_reason(commit("a" * 40, "Merge pull request #5", parents=2)), "merge commit")
        self.assertIn("bot", notes.commit_skip_reason(commit("a" * 40, "Update deps", author={"login": "dependabot[bot]", "type": "Bot"})))
        for subject in ("Bump models to v1.7.79", "chore: tidy", "Merge branch 'main'"):
            self.assertEqual(notes.commit_skip_reason(commit("a" * 40, subject)), "subject", subject)
        self.assertIsNone(notes.commit_skip_reason(commit("a" * 40, "fix(auth): restrict owner grants")))
        self.assertIsNone(notes.commit_skip_reason(
            commit("a" * 40, "fix(build): retry registry failures", author={"login": "Copilot", "type": "Bot"})
        ))

    def test_commits_without_description_use_their_code(self):
        commits = [
            commit("a" * 40, "Restrict owner-level role grants\n\nOnly owners may grant the owner role or edit owners."),
            commit("b" * 40, "Fix crash"),
            commit("c" * 40, "Merge pull request #5", parents=2),
        ]
        with mock.patch.object(notes, "list_range_commits", return_value=commits), \
                mock.patch.object(notes, "fetch_commit_code", return_value="File: src/player.ts\n+guard\n") as fetch:
            context, links, count = notes.get_commit_context("https://api.github.com", "example/repo", "v1", "v2", {})
        fetch.assert_called_once()
        self.assertEqual(count, 3)
        self.assertEqual(set(links), {"aaaaaaa", "bbbbbbb"})
        self.assertIn("### Commit aaaaaaa: Restrict owner-level role grants", context)
        self.assertIn("Only owners may grant", context)
        self.assertIn("No description; code changes:\nFile: src/player.ts", context)
        self.assertNotIn("Merge pull request", context)

    def test_context_stays_within_budget(self):
        commits = [
            {"sha": f"{index:040x}", "subject": f"Change {index}", "body": "word " * 2000}
            for index in range(1, 15)
        ]
        context = notes.build_commit_context(commits, budget=8000)
        self.assertLessEqual(len(context), 8000 + 15 * len("\n(…truncated)"))
        self.assertEqual(context.count("### Commit "), 14)

    def test_links_commit_citations(self):
        links = {"3f2a9c1": "https://github.com/example/repo/commit/3f2a9c1aaa"}
        summary = "\n".join([
            "- Added log levels (3F2A9C1aaaa).",
            "- Invented feature (deadbee)",
            "- Uncited bullet",
        ])
        self.assertEqual(
            notes.link_commit_citations(summary, links).splitlines(),
            [
                "- Added log levels ([3f2a9c1](https://github.com/example/repo/commit/3f2a9c1aaa))",
                "- Uncited bullet",
            ],
        )


class CodeContextTest(unittest.TestCase):
    def test_reviewable_code(self):
        for path in ("api/internal/heartbeat/cache.go", "kerberos.ng/src/assets/i18n/en.json",
                     "charts/hub/templates/api.yaml", "src/app/videowall.component.ts"):
            self.assertTrue(notes.is_reviewable_code(path), path)
        for path in ("api/internal/heartbeat/cache_test.go", "go.sum", "api/docs/swagger.json", "api/docs/docs.go",
                     "kerberos.ng/src/assets/i18n/nl.json", "src/app/tile.component.spec.ts",
                     ".github/workflows/ci.yml", "web/package-lock.json", "tests/test_upload.py"):
            self.assertFalse(notes.is_reviewable_code(path), path)

    def test_pack_sections_respects_budget_and_order(self):
        chunks = notes.pack_sections(["a" * 40, "b" * 40, "c" * 40, "d" * 500], 100)
        self.assertEqual(chunks[0], "a" * 40 + "\n" + "b" * 40)
        self.assertTrue(chunks[1].startswith("c" * 40))
        self.assertTrue(all(len(chunk) <= 100 + len("\n(…truncated)") for chunk in chunks))

    def test_reads_every_part_and_drops_tests(self):
        files = [
            {"filename": "api/a.go", "status": "modified", "additions": 1, "deletions": 0, "patch": "+a" * 9000},
            {"filename": "api/b.go", "status": "modified", "additions": 1, "deletions": 0, "patch": "+b" * 9000},
            {"filename": "api/a_test.go", "status": "added", "additions": 1, "deletions": 0, "patch": "+t" * 9000},
            {"filename": "api/big.go", "status": "modified", "additions": 900, "deletions": 3},
        ]
        response = mock.Mock(status_code=200)
        response.json.return_value = {"total_commits": 4, "files": files}
        replies = iter(["- api/a.go: new endpoint", "No behavior changes."])
        with mock.patch.object(notes.requests, "get", return_value=response), \
                mock.patch.object(notes, "complete_chat", side_effect=lambda *_: next(replies)) as chat:
            context, stats = notes.get_code_context("https://api.github.com", "example/repo", "v1", "v2", {}, {})
        self.assertEqual(chat.call_count, 2)
        prompts = [call.args[0][1]["content"] for call in chat.call_args_list]
        self.assertTrue(all("a_test.go" not in prompt for prompt in prompts))
        self.assertIn("- api/a.go: new endpoint", context)
        self.assertNotIn("No behavior changes", context)
        self.assertIn("api/big.go (+900/-3)", context)
        self.assertEqual(stats["files_changed"], 4)

    def test_condense_merges_notes_until_they_fit(self):
        parts = [f"Part {index}:\n" + "- change\n" * 50 for index in range(6)]
        with mock.patch.object(notes, "complete_chat", return_value="- merged change") as chat:
            condensed = notes.condense_notes(parts, "example/repo", {}, budget=1000)
        self.assertGreater(chat.call_count, 0)
        self.assertLessEqual(len(condensed), 1000)
        self.assertIn("- merged change", condensed)


if __name__ == "__main__":
    unittest.main()
