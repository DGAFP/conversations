#!/usr/bin/env python3
"""Open one pull request for the oldest upstream release missing from main."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

UPSTREAM_REPOSITORY = "suitenumerique/conversations"
BASE_BRANCH = "main"
BRANCH_PREFIX = "upstream-release/"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Release:
    tag_name: str
    published_at: str


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and include its diagnostic output in failures."""
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        message = f"Command failed: {' '.join(command)}"
        if details:
            message = f"{message}\n{details}"
        raise RuntimeError(message)
    return completed


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], check=check)


def gh(*arguments: str) -> subprocess.CompletedProcess[str]:
    return run(["gh", *arguments])


def gh_json(*arguments: str) -> object:
    return json.loads(gh(*arguments).stdout)


def gh_paginated(endpoint: str) -> list[dict]:
    """Return all items from a paginated GitHub API list endpoint."""
    pages = gh_json("api", "--paginate", "--slurp", endpoint)
    return [item for page in pages for item in page]


def find_open_sync_pull_request(repository: str) -> str | None:
    pulls = gh_paginated(f"repos/{repository}/pulls?state=open&per_page=100")
    return next(
        (
            pull["head"]["ref"]
            for pull in pulls
            if pull["head"]["ref"].startswith(BRANCH_PREFIX)
        ),
        None,
    )


def list_published_releases() -> list[Release]:
    releases = gh_paginated(f"repos/{UPSTREAM_REPOSITORY}/releases?per_page=100")
    return [
        Release(tag_name=release["tag_name"], published_at=release["published_at"])
        for release in releases
        if not release["draft"] and not release["prerelease"]
    ]


def is_release_integrated(tag_name: str) -> bool:
    """A release is integrated when its tagged commit is an ancestor of main."""
    result = git(
        "merge-base",
        "--is-ancestor",
        f"{tag_name}^{{commit}}",
        f"origin/{BASE_BRANCH}",
        check=False,
    )
    if result.returncode not in (0, 1):
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Could not inspect release {tag_name}: {details}")
    return result.returncode == 0


def remote_branch_exists(branch_name: str) -> bool:
    result = git(
        "ls-remote", "--exit-code", "--heads", "origin", branch_name, check=False
    )
    if result.returncode not in (0, 2):
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Could not inspect remote branch {branch_name}: {details}")
    return result.returncode == 0


def find_next_missing_release(
    releases: Iterable[Release], integrated: Callable[[str], bool]
) -> Release | None:
    for release in sorted(releases, key=lambda item: item.published_at):
        if not integrated(release.tag_name):
            return release
    return None


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY must identify the fork repository")

    existing_branch = find_open_sync_pull_request(repository)
    if existing_branch:
        logger.info(f"A release sync pull request is already open: {existing_branch}")
        return 0

    upstream_url = f"https://github.com/{UPSTREAM_REPOSITORY}.git"
    git("remote", "add", "upstream", upstream_url)
    git("fetch", "--tags", "upstream", BASE_BRANCH)
    git("fetch", "origin", BASE_BRANCH)

    release = find_next_missing_release(
        list_published_releases(), is_release_integrated
    )
    if release is None:
        logger.info("No missing upstream releases.")
        return 0

    branch = f"{BRANCH_PREFIX}{release.tag_name}"
    if remote_branch_exists(branch):
        logger.info("Reusing existing release branch: %s", branch)
        git(
            "fetch",
            "origin",
            f"refs/heads/{branch}:refs/remotes/origin/{branch}",
        )
        git("switch", "-c", branch, f"origin/{branch}")
    else:
        git("switch", "-c", branch, f"origin/{BASE_BRANCH}")
        git(
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "merge",
            "--no-ff",
            "--no-edit",
            f"{release.tag_name}^{{commit}}",
        )
        git("push", "--set-upstream", "origin", branch)

    release_url = (
        f"https://github.com/{UPSTREAM_REPOSITORY}/releases/tag/{release.tag_name}"
    )
    body = (
        f"Merges upstream release [{release.tag_name}]({release_url}).\n\n"
        "Merge with a merge commit, not squash or rebase, so subsequent runs "
        "can detect the integrated release."
    )
    pull_request_url = gh(
        "pr",
        "create",
        "--repo",
        repository,
        "--base",
        BASE_BRANCH,
        "--head",
        branch,
        "--title",
        f"chore: merge upstream release {release.tag_name}",
        "--body",
        body,
    ).stdout.strip()
    logger.info(f"Opened {pull_request_url} for {release.tag_name}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        sys.exit(main())
    except RuntimeError as error:
        logger.error("error: %s", error)
        sys.exit(1)
