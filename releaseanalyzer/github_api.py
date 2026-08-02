"""Optional GitHub Enterprise API enrichment.

This is entirely optional and additive: the core comparison algorithm in
`analyzer.py` never depends on network access or the GitHub API — it works
purely from local Git history, which is the only thing that can be trusted
for correctness (patch-id / cherry equivalence). This module only *enriches*
already-classified changes with human-facing PR metadata (title, URL, merge
state) when a PR number was extracted from a commit message and the caller
has supplied GitHub Enterprise API credentials.

Failure mode: if the API is unreachable, unauthenticated, or rate-limited,
this degrades gracefully -- it logs a warning and leaves `pr_title`/`pr_url`
unset. It never fails the report generation.

Read-only: only ever issues GET requests.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .models import ChangeEntry, Commit

logger = logging.getLogger("releaseanalyzer.github_api")

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def owner_repo_from_remote(remote_url: str) -> Optional[tuple[str, str]]:
    """Best-effort parse of 'owner/repo' out of a git remote URL, supporting
    both SSH (git@ghe.example.com:owner/repo.git) and HTTPS forms."""
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", remote_url.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def enrich_pull_requests(
    changes: list[ChangeEntry],
    api_base_url: str,
    token: str,
    owner: str,
    repo: str,
    timeout: float = 5.0,
) -> int:
    """Mutates Commit objects referenced by `changes` in place, adding PR
    title/url/state where a PR number could be extracted from the commit
    message. Returns the number of PRs successfully enriched.

    `api_base_url` example for GitHub Enterprise: https://ghe.example.com/api/v3
    """
    if requests is None:
        logger.warning("`requests` package not installed; skipping PR enrichment")
        return 0

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    })

    seen_commits: dict[str, Commit] = {}
    for change in changes:
        for commit in (change.source_commit, change.target_commit):
            if commit and commit.pr_reference and commit.sha not in seen_commits:
                seen_commits[commit.sha] = commit

    enriched = 0
    for commit in seen_commits.values():
        pr_number = commit.pr_reference.lstrip("#") if commit.pr_reference else None
        if not pr_number or not pr_number.isdigit():
            continue
        url = f"{api_base_url.rstrip('/')}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code != 200:
                logger.warning(
                    "GitHub Enterprise API returned %s for PR #%s (%s); skipping enrichment",
                    resp.status_code, pr_number, url,
                )
                continue
            data = resp.json()
            commit.pr_title = data.get("title")
            commit.pr_url = data.get("html_url")
            if data.get("merged_at"):
                commit.pr_state = "merged"
            else:
                commit.pr_state = data.get("state")
            enriched += 1
        except Exception as exc:  # network errors, timeouts, bad JSON, etc.
            logger.warning("PR enrichment failed for #%s: %s", pr_number, exc)
            continue

    logger.info("Enriched %d/%d referenced pull request(s) via GitHub Enterprise API", enriched, len(seen_commits))
    return enriched
