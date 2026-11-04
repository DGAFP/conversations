"""Tests for the scheduled upstream release synchronization script."""

import os
import subprocess
import unittest
from unittest.mock import patch

import sync_upstream_release as sync


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def release(tag_name, published_at):
    return sync.Release(tag_name=tag_name, published_at=published_at)


class ReleaseSelectionTests(unittest.TestCase):
    def test_next_release_is_oldest_missing_by_publication_date(self):
        releases = [
            release("v0.0.3", "2026-03-01T00:00:00Z"),
            release("v0.0.1", "2026-01-01T00:00:00Z"),
            release("v0.0.2", "2026-02-01T00:00:00Z"),
        ]

        selected = sync.find_next_missing_release(releases, lambda tag: tag == "v0.0.1")

        self.assertEqual(selected.tag_name, "v0.0.2")

    def test_next_release_is_none_when_all_releases_are_integrated(self):
        releases = [release("v0.0.1", "2026-01-01T00:00:00Z")]

        self.assertIsNone(sync.find_next_missing_release(releases, lambda tag: True))

    @patch("sync_upstream_release.gh_paginated")
    def test_release_list_excludes_drafts_and_prereleases(self, paginated):
        paginated.return_value = [
            {
                "tag_name": "v0.0.2",
                "published_at": "2026-02-01T00:00:00Z",
                "draft": False,
                "prerelease": False,
            },
            {
                "tag_name": "v0.0.3-rc1",
                "published_at": "2026-03-01T00:00:00Z",
                "draft": False,
                "prerelease": True,
            },
            {
                "tag_name": "v0.0.3",
                "published_at": "2026-03-02T00:00:00Z",
                "draft": True,
                "prerelease": False,
            },
        ]

        releases = sync.list_published_releases()

        self.assertEqual([item.tag_name for item in releases], ["v0.0.2"])

    @patch("sync_upstream_release.git")
    def test_integrated_release_accepts_only_git_ancestry_results(self, git):
        git.side_effect = [completed(0), completed(1), completed(128, stderr="bad tag")]

        self.assertTrue(sync.is_release_integrated("v0.0.1"))
        self.assertFalse(sync.is_release_integrated("v0.0.2"))
        with self.assertRaisesRegex(RuntimeError, "bad tag"):
            sync.is_release_integrated("v0.0.3")


class MainTests(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "DGAFP/conversations"})
    @patch("sync_upstream_release.list_published_releases")
    @patch("sync_upstream_release.git")
    @patch(
        "sync_upstream_release.find_open_sync_pull_request",
        return_value="upstream-release/v0.0.25",
    )
    def test_open_sync_pr_prevents_all_git_and_release_work(
        self, find_open, git, list_releases
    ):
        self.assertEqual(sync.main(), 0)

        git.assert_not_called()
        list_releases.assert_not_called()

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "DGAFP/conversations"})
    @patch("sync_upstream_release.gh")
    @patch("sync_upstream_release.git")
    @patch("sync_upstream_release.find_next_missing_release", return_value=None)
    @patch("sync_upstream_release.list_published_releases")
    @patch("sync_upstream_release.find_open_sync_pull_request", return_value=None)
    def test_no_missing_release_does_not_push_or_create_a_pr(
        self, find_open, list_releases, find_next, git, gh
    ):
        self.assertEqual(sync.main(), 0)

        self.assertFalse(
            any(
                call.args and call.args[0] in {"push", "switch"}
                for call in git.call_args_list
            )
        )
        gh.assert_not_called()

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "DGAFP/conversations"})
    @patch("sync_upstream_release.gh")
    @patch("sync_upstream_release.git")
    @patch("sync_upstream_release.find_next_missing_release")
    @patch("sync_upstream_release.list_published_releases")
    @patch("sync_upstream_release.find_open_sync_pull_request", return_value=None)
    def test_next_release_is_merged_pushed_and_opened_as_a_pr(
        self, find_open, list_releases, find_next, git, gh
    ):
        selected = release("v0.0.25", "2026-10-01T16:17:03Z")
        find_next.return_value = selected
        gh.return_value = completed(stdout="https://example.test/pull/1\n")

        self.assertEqual(sync.main(), 0)

        git.assert_any_call("switch", "-c", "upstream-release/v0.0.25", "origin/main")
        git.assert_any_call(
            "push", "--set-upstream", "origin", "upstream-release/v0.0.25"
        )
        arguments = gh.call_args.args
        self.assertEqual(arguments[:2], ("pr", "create"))
        self.assertIn("chore: merge upstream release v0.0.25", arguments)
        self.assertIn("upstream-release/v0.0.25", arguments)


if __name__ == "__main__":
    unittest.main()
