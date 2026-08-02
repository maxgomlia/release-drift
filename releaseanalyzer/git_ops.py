"""Read-only Git plumbing wrappers.

Every function here is a thin, defensive wrapper around a *read-only* git
subcommand. Nothing in this module ever calls checkout / reset / merge /
rebase / push, or otherwise mutates repository state, per the
"analysis must be read-only" requirement.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("releaseanalyzer.git_ops")

# Field separators unlikely to collide with commit message content.
_US = "\x1f"   # unit separator - between fields
_RS = "\x1e"   # record separator - between commits

# Fields within one commit record are joined with the unit separator; the
# record separator is appended after every commit (via --pretty=tformat:,
# which -- unlike --pretty=format: -- emits the trailing separator after the
# final commit too) so multi-line commit bodies can never be confused with
# the boundary between two commits.
LOG_FORMAT = _US.join([
    "%H", "%an", "%ae", "%aI", "%cI", "%s", "%b", "%P",
]) + _RS


class GitError(RuntimeError):
    """Raised when a git command fails or the repo state is unusable."""


@dataclass
class RawCommit:
    sha: str
    author_name: str
    author_email: str
    author_date: str
    committer_date: str
    subject: str
    body: str
    parents: list[str]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def _run(repo_dir: str, args: list[str], check: bool = True) -> str:
    cmd = ["git", "-C", repo_dir] + args
    logger.debug("git command: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def ensure_repo(repo_dir: str) -> None:
    out = _run(repo_dir, ["rev-parse", "--is-inside-work-tree"], check=False)
    if out.strip() != "true":
        raise GitError(f"{repo_dir} is not inside a Git working tree")


def fetch_all(repo_dir: str, remote: str = "origin", prune: bool = True) -> None:
    """Best-effort fetch. Does not fail the whole run if there is no remote
    (e.g. purely local/test repositories) — logs and continues, since the
    branches may already be present locally."""
    args = ["fetch", remote]
    if prune:
        args.append("--prune")
    out = subprocess.run(
        ["git", "-C", repo_dir] + args, capture_output=True, text=True
    )
    if out.returncode != 0:
        logger.warning(
            "git fetch failed or no remote '%s' configured; continuing with "
            "local refs. stderr: %s",
            remote, out.stderr.strip(),
        )


def resolve_ref(repo_dir: str, ref: str) -> str:
    """Resolve a branch name to its commit SHA, trying local, then origin/<ref>."""
    for candidate in (ref, f"origin/{ref}", f"refs/remotes/origin/{ref}"):
        out = _run(repo_dir, ["rev-parse", "--verify", "--quiet", candidate], check=False).strip()
        if out:
            return out
    raise GitError(f"Could not resolve ref '{ref}' (tried local and origin/*)")


def merge_base_all(repo_dir: str, ref_a: str, ref_b: str) -> list[str]:
    """Return all merge-base candidates (criss-cross merges can have >1)."""
    out = _run(repo_dir, ["merge-base", "--all", ref_a, ref_b], check=False)
    candidates = [line.strip() for line in out.splitlines() if line.strip()]
    if not candidates:
        raise GitError(f"No common ancestor found between {ref_a} and {ref_b}")
    return candidates


def log_range(repo_dir: str, range_expr: str) -> list[RawCommit]:
    """git log <range_expr> --first-parent=false, parsed into RawCommit list.

    We deliberately do NOT use --first-parent so that commits contributed via
    merged feature branches are still visible for classification; merge
    commits themselves are marked via `is_merge` and handled specially by
    the analyzer.
    """
    out = _run(
        repo_dir,
        [
            "log",
            range_expr,
            f"--pretty=tformat:{LOG_FORMAT}",
            "--no-color",
        ],
    )
    commits: list[RawCommit] = []
    if not out.strip():
        return commits
    for record in out.split(_RS):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(_US)
        if len(parts) < 8:
            logger.warning("Skipping malformed git log record (fields=%d)", len(parts))
            continue
        sha, an, ae, ad, cd, subject, body, parents = parts[:8]
        commits.append(
            RawCommit(
                sha=sha.strip(),
                author_name=an,
                author_email=ae,
                author_date=ad,
                committer_date=cd,
                subject=subject.strip(),
                body=body.strip(),
                parents=[p for p in parents.strip().split(" ") if p],
            )
        )
    return commits


def diff_stat_files(repo_dir: str, sha: str) -> tuple[list[str], int, int]:
    """Return (files_changed, insertions, deletions) for a single commit,
    diffed against its first parent (or the empty tree for a root commit)."""
    parents = _run(repo_dir, ["rev-parse", f"{sha}^@"], check=False).split()
    base = parents[0] if parents else _empty_tree(repo_dir)
    numstat = _run(repo_dir, ["diff", "--numstat", base, sha], check=False)
    files: list[str] = []
    insertions = 0
    deletions = 0
    for line in numstat.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        added, removed, path = cols
        files.append(path)
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return files, insertions, deletions


def _empty_tree(repo_dir: str) -> str:
    return _run(repo_dir, ["hash-object", "-t", "tree", "/dev/null"], check=False).strip()


def patch_id_for_commit(repo_dir: str, sha: str) -> str | None:
    """Compute a stable patch-id for a single commit's diff.

    Uses `git show <sha> | git patch-id --stable`, which normalizes line
    numbers and is independent of blob SHAs, author, date, and committer —
    exactly the property needed to detect cherry-picks across branches.
    Returns None for commits with an empty diff (rare, but possible for
    empty merge/no-op commits).
    """
    show = subprocess.run(
        ["git", "-C", repo_dir, "show", sha],
        capture_output=True, text=True,
    )
    if show.returncode != 0 or not show.stdout.strip():
        return None
    pid = subprocess.run(
        ["git", "-C", repo_dir, "patch-id", "--stable"],
        input=show.stdout, capture_output=True, text=True,
    )
    if pid.returncode != 0 or not pid.stdout.strip():
        return None
    # Output format: "<patch-id> <commit-sha>"
    return pid.stdout.split()[0]


def cherry(repo_dir: str, upstream: str, head: str) -> dict[str, bool]:
    """git cherry <upstream> <head>.

    Returns {sha: has_equivalent_upstream} for every commit unique to
    <head> relative to <upstream>. '-' prefix => equivalent patch exists
    upstream (True); '+' prefix => genuinely unique to head (False).
    """
    out = _run(repo_dir, ["cherry", "-v", upstream, head], check=False)
    result: dict[str, bool] = {}
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        marker, rest = line[0], line[1:].strip()
        sha = rest.split(" ", 1)[0]
        result[sha] = marker == "-"
    return result


def repository_identifier(repo_dir: str) -> str:
    out = _run(repo_dir, ["remote", "get-url", "origin"], check=False).strip()
    if out:
        return out
    return _run(repo_dir, ["rev-parse", "--show-toplevel"], check=False).strip()
