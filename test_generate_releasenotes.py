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


if __name__ == "__main__":
    unittest.main()
