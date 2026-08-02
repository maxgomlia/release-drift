#!/usr/bin/env python3
"""Release Change Report — CLI entry point.

Usage:
    python release_report.py --source release/26.05 --target release/26.06

Runs entirely against a local Git repository (defaults to the current
working directory). Read-only: fetches (optional) then only reads history.

Zero external dependencies -- everything here is Python standard library.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from releaseanalyzer import analyzer, report_html, report_json
from releaseanalyzer.models import TOOL_VERSION


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="release_report.py",
        description="Generate a Release Change Report comparing two release branches: "
                     "what's being released, and what may have been missed from the "
                     "previous release.",
    )
    p.add_argument("--source", required=True, help="Source (previous) release branch, e.g. release/26.05")
    p.add_argument("--target", required=True, help="Target (new) release branch, e.g. release/26.06")
    p.add_argument("--repo", default=".", help="Path to the Git repository (default: current directory)")
    p.add_argument("--remote", default="origin", help="Git remote to fetch from (default: origin)")
    p.add_argument("--no-fetch", action="store_true", help="Skip 'git fetch' and use local refs as-is")
    p.add_argument(
        "--github-api-url", default=None,
        help="Optional: GitHub Enterprise API base URL (e.g. https://ghe.example.com/api/v3) "
             "to enrich changes with PR title/url/state. Purely cosmetic -- classification "
             "never depends on this.",
    )
    p.add_argument("--github-token", default=None, help="GitHub Enterprise API token (required if --github-api-url is set)")
    p.add_argument("--github-owner", default=None, help="Repo owner/org (default: parsed from git remote)")
    p.add_argument("--github-repo", default=None, help="Repo name (default: parsed from git remote)")
    p.add_argument("--out-dir", default=".", help="Directory to write release-report.html/.json into")
    p.add_argument("--html-name", default="release-report.html")
    p.add_argument("--json-name", default="release-report.json")
    p.add_argument("--no-html", action="store_true", help="Skip HTML output")
    p.add_argument("--no-json", action="store_true", help="Skip JSON output")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging")
    p.add_argument("--version", action="version", version=f"release-report {TOOL_VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    log = logging.getLogger("release_report")

    try:
        comparison = analyzer.analyze(
            repo_dir=args.repo,
            source_branch=args.source,
            target_branch=args.target,
            fetch=not args.no_fetch,
            remote=args.remote,
        )

        if args.github_api_url:
            if not args.github_token:
                log.error("--github-api-url was set but --github-token was not provided")
                return 2
            from releaseanalyzer import github_api
            owner, repo = args.github_owner, args.github_repo
            if not (owner and repo):
                parsed = github_api.owner_repo_from_remote(comparison.metadata.repository)
                if parsed:
                    owner, repo = owner or parsed[0], repo or parsed[1]
            if owner and repo:
                github_api.enrich_pull_requests(
                    comparison.changes, args.github_api_url, args.github_token, owner, repo,
                )
            else:
                log.warning("Could not determine owner/repo for PR enrichment; skipping (pass --github-owner/--github-repo)")

        analyzer.summarize(comparison)

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if not args.no_html:
            html_path = out_dir / args.html_name
            html_path.write_text(report_html.render(comparison), encoding="utf-8")
            log.info("Wrote %s", html_path)

        if not args.no_json:
            json_path = out_dir / args.json_name
            json_path.write_text(report_json.render(comparison), encoding="utf-8")
            log.info("Wrote %s", json_path)

        log.info(
            "Release status: %s | total=%d new=%d carried=%d missing=%d "
            "target_only=%d reverted=%d needs_review=%d",
            comparison.release_status,
            comparison.summary.total_changes,
            comparison.summary.new_in_target,
            comparison.summary.carried_forward,
            comparison.summary.potentially_missing,
            comparison.summary.target_only,
            comparison.summary.reverted,
            comparison.summary.needs_review,
        )

        if comparison.release_status == "RELEASE REVIEW REQUIRED":
            return 1   # distinct exit code for pipeline gating: missing/ambiguous changes found
        return 0

    except analyzer.git_ops.GitError as exc:
        log.error(str(exc))
        return 2
    except Exception:
        log.exception("Unexpected error generating release report")
        return 3


if __name__ == "__main__":
    sys.exit(main())
