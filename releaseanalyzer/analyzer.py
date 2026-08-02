"""Core release-comparison algorithm.

See DESIGN.md section 1 for the full rationale. Summary:

  1. merge-base(source, target) -> divergence point
  2. commits unique to each side (git log merge_base..ref)
  3. patch-id every unique commit on both sides (git patch-id --stable)
  4. pair source/target commits sharing a patch-id  -> CARRIED FORWARD
  5. cross-check unpaired commits with `git cherry` (independent signal);
     disagreement between the two signals -> NEEDS REVIEW
  6. still-unpaired source commits                  -> MISSING FROM TARGET
  7. still-unpaired target commits, revert check first, then
     version/build-file-only heuristic               -> REVERTED / TARGET-ONLY / NEW
  8. Jira/change-id correlation used only to *downgrade* an apparent
     MISSING to NEEDS REVIEW when the same change id exists on both sides
     with differing patch-ids (likely a reworked cherry-pick, not a true gap)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from . import git_ops
from .models import (
    ChangeEntry, Classification, Commit, Confidence, RunMetadata,
    SummaryCounts, ReleaseComparison, TOOL_VERSION,
)

logger = logging.getLogger("releaseanalyzer.analyzer")

CHANGE_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
PR_REF_RE = re.compile(r"\(#(\d+)\)|\bpull request #(\d+)\b|\bPR[- ]?#?(\d+)\b", re.IGNORECASE)
REVERT_SUBJECT_RE = re.compile(r'^Revert\s+"(.+)"\s*$')
REVERT_BODY_SHA_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
VERSION_FILE_RE = re.compile(
    r"(^|/)(pom\.xml|build\.gradle|gradle\.properties|VERSION|version\.txt|package\.json)$"
)
FEATURE_KEYWORDS = re.compile(r"\b(add|introduce|implement|new|support for)\b", re.IGNORECASE)
CHORE_KEYWORDS = re.compile(r"\b(chore|bump|version|release prep|cleanup|refactor)\b", re.IGNORECASE)
FIX_KEYWORDS = re.compile(r"\b(fix|bug|hotfix|patch|correct|resolve)\b", re.IGNORECASE)


def _to_commit(repo_dir: str, raw: git_ops.RawCommit) -> Commit:
    files, ins, dels = git_ops.diff_stat_files(repo_dir, raw.sha)
    patch_id = git_ops.patch_id_for_commit(repo_dir, raw.sha)
    change_id_match = CHANGE_ID_RE.search(raw.subject)
    pr_match = PR_REF_RE.search(raw.subject + " " + raw.body)
    pr_ref = None
    if pr_match:
        pr_ref = next((g for g in pr_match.groups() if g), None)
        if pr_ref:
            pr_ref = f"#{pr_ref}"
    return Commit(
        sha=raw.sha,
        author_name=raw.author_name,
        author_email=raw.author_email,
        author_date=raw.author_date,
        committer_date=raw.committer_date,
        subject=raw.subject,
        body=raw.body,
        change_id=change_id_match.group(1) if change_id_match else None,
        pr_reference=pr_ref,
        patch_id=patch_id,
        is_merge=raw.is_merge,
        files_changed=files,
        insertions=ins,
        deletions=dels,
    )


def _classify_change_type(commit: Commit) -> str:
    text = f"{commit.subject} {commit.body}"
    if FIX_KEYWORDS.search(text):
        return "Bug Fix"
    if FEATURE_KEYWORDS.search(text):
        return "Feature"
    if CHORE_KEYWORDS.search(text):
        return "Chore"
    return "Unknown"


def _is_version_only(commit: Commit) -> bool:
    if not commit.files_changed:
        return False
    return all(VERSION_FILE_RE.search(f) for f in commit.files_changed)


def _describe(commit: Commit) -> str:
    return commit.subject.strip() or f"(no subject) {commit.short_sha}"


def _synthetic_change_id(commit: Commit) -> str:
    return commit.change_id or f"COMMIT-{commit.short_sha}"


def analyze(
    repo_dir: str,
    source_branch: str,
    target_branch: str,
    fetch: bool = True,
    remote: str = "origin",
) -> ReleaseComparison:
    git_ops.ensure_repo(repo_dir)
    if fetch:
        git_ops.fetch_all(repo_dir, remote=remote)

    source_sha = git_ops.resolve_ref(repo_dir, source_branch)
    target_sha = git_ops.resolve_ref(repo_dir, target_branch)
    merge_bases = git_ops.merge_base_all(repo_dir, source_sha, target_sha)
    merge_base = merge_bases[0]

    logger.info("Source %s -> %s", source_branch, source_sha[:10])
    logger.info("Target %s -> %s", target_branch, target_sha[:10])
    logger.info("Merge base: %s (candidates: %s)", merge_base[:10], merge_bases)

    source_raw = git_ops.log_range(repo_dir, f"{merge_base}..{source_sha}")
    target_raw = git_ops.log_range(repo_dir, f"{merge_base}..{target_sha}")

    source_commits = [_to_commit(repo_dir, r) for r in source_raw]
    target_commits = [_to_commit(repo_dir, r) for r in target_raw]

    # Independent signal #1: our own direct patch-id pairing.
    target_by_patch_id: dict[str, Commit] = {
        c.patch_id: c for c in target_commits if c.patch_id
    }
    source_by_patch_id: dict[str, Commit] = {
        c.patch_id: c for c in source_commits if c.patch_id
    }

    # Independent signal #2: git cherry, computed both directions.
    cherry_src_has_equiv_in_tgt = git_ops.cherry(repo_dir, upstream=target_sha, head=source_sha)
    cherry_tgt_has_equiv_in_src = git_ops.cherry(repo_dir, upstream=source_sha, head=target_sha)

    # Revert detection lookups.
    target_reverts: dict[str, str] = {}  # target_sha -> reverted_sha (from message)
    for c in target_commits:
        m = REVERT_SUBJECT_RE.match(c.subject)
        body_m = REVERT_BODY_SHA_RE.search(c.body)
        if m and body_m:
            target_reverts[c.sha] = body_m.group(1)

    change_id_to_source = {c.change_id: c for c in source_commits if c.change_id}
    change_id_to_target = {c.change_id: c for c in target_commits if c.change_id}

    changes: list[ChangeEntry] = []
    paired_target_shas: set[str] = set()

    # --- Pass 1: classify every source-side commit (carried vs missing) ---
    for sc in source_commits:
        target_match = target_by_patch_id.get(sc.patch_id) if sc.patch_id else None
        cherry_says_equiv = cherry_src_has_equiv_in_tgt.get(sc.sha)

        if target_match is not None:
            paired_target_shas.add(target_match.sha)
            if cherry_says_equiv is False:
                # Disagreement between direct patch-id pairing and git cherry.
                changes.append(_build_entry(
                    sc, target_match, Classification.NEEDS_REVIEW,
                    Confidence.LOW,
                    "Direct patch-id match found in target, but 'git cherry' "
                    "did not corroborate equivalence. Manual review required.",
                ))
            else:
                changes.append(_build_entry(
                    sc, target_match, Classification.CARRIED_FORWARD,
                    Confidence.HIGH,
                    f"Patch-id {sc.patch_id[:12]} matches target commit "
                    f"{target_match.short_sha} (git cherry corroborates).",
                ))
            continue

        # No direct patch-id match. Consult git cherry as sole signal.
        if cherry_says_equiv is True:
            changes.append(_build_entry(
                sc, None, Classification.NEEDS_REVIEW, Confidence.MEDIUM,
                "'git cherry' reports an equivalent patch exists in target, "
                "but the exact target commit could not be identified via "
                "direct patch-id lookup (possible post-cherry-pick edits). "
                "Manual review required to confirm the carrying commit.",
            ))
            continue

        # Neither signal found equivalence in target -> genuinely absent.
        # Downgrade to NEEDS REVIEW if same change-id exists on target side
        # with a different patch-id (likely reworked/reimplemented fix).
        if sc.change_id and sc.change_id in change_id_to_target:
            tgt = change_id_to_target[sc.change_id]
            changes.append(_build_entry(
                sc, tgt, Classification.NEEDS_REVIEW, Confidence.LOW,
                f"No patch-id/cherry equivalence found, but change id "
                f"{sc.change_id} also appears in target as {tgt.short_sha} "
                f"with a different diff. Likely reworked rather than missing "
                f"-- confirm manually.",
            ))
            paired_target_shas.add(tgt.sha)
            continue

        changes.append(_build_entry(
            sc, None, Classification.MISSING_FROM_TARGET, Confidence.HIGH,
            "No equivalent patch-id found in target and 'git cherry' found "
            "no corresponding change. This commit is present on the source "
            "release and appears absent from the target release "
            "-- potential regression risk.",
        ))

    # --- Pass 2: classify remaining target-only commits (new/reverted/target-only) ---
    for tc in target_commits:
        if tc.sha in paired_target_shas:
            continue  # already represented as the target side of a pair above

        # Revert check first.
        if tc.sha in target_reverts:
            reverted_sha = target_reverts[tc.sha]
            reverted_short = reverted_sha[:10]
            was_carried = any(
                ch.target_commit and ch.target_commit.sha.startswith(reverted_short)
                for ch in changes
            ) or any(
                ch.source_commit and ch.source_commit.sha.startswith(reverted_short)
                for ch in changes
            )
            reason = (
                f"Commit message identifies this as a revert of {reverted_short}. "
                + ("That change was carried forward earlier in this release and "
                   "has now been explicitly reverted -- flagging for visibility."
                   if was_carried else
                   "Could not confirm the reverted commit's classification in "
                   "this comparison; flagged for manual confirmation.")
            )
            changes.append(_build_entry(
                None, tc, Classification.REVERTED,
                Confidence.HIGH if was_carried else Confidence.MEDIUM,
                reason,
            ))
            continue

        if tc.is_merge:
            # Merge commit with no first-parent diff of its own and no
            # patch-id pairing: informational only, low-confidence NEW.
            if not tc.files_changed:
                continue

        if _is_version_only(tc):
            changes.append(_build_entry(
                None, tc, Classification.TARGET_ONLY, Confidence.MEDIUM,
                "Touches only release/version/build metadata files; treated "
                "as target-only release bookkeeping rather than a functional "
                "change.",
            ))
            continue

        changes.append(_build_entry(
            None, tc, Classification.NEW_IN_TARGET, Confidence.HIGH,
            "No equivalent patch-id or cherry match found on the source "
            "release. This is a change introduced specifically for the "
            "target release.",
        ))

    changes.sort(key=_sort_key)

    metadata = RunMetadata(
        repository=git_ops.repository_identifier(repo_dir),
        source_branch=source_branch,
        target_branch=target_branch,
        source_sha=source_sha,
        target_sha=target_sha,
        merge_base_sha=merge_base,
        merge_base_candidates=merge_bases,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tool_version=TOOL_VERSION,
        comparison_algorithm=(
            "merge-base divergence + bidirectional git-patch-id pairing, "
            "cross-checked against git-cherry patch-equivalence"
        ),
    )

    comparison = ReleaseComparison(
        metadata=metadata,
        changes=changes,
        summary=SummaryCounts(),  # filled in by summarize()
        attention_items=[],
        release_status="RELEASE REVIEW REQUIRED",
    )
    return comparison


def _build_entry(
    source_commit: Commit | None,
    target_commit: Commit | None,
    classification: Classification,
    confidence: Confidence,
    reason: str,
) -> ChangeEntry:
    anchor = target_commit or source_commit
    assert anchor is not None
    change_id = _synthetic_change_id(anchor)
    return ChangeEntry(
        change_id=change_id,
        description=_describe(anchor),
        change_type=_classify_change_type(anchor),
        classification=classification,
        classification_confidence=confidence,
        classification_reason=reason,
        source_commit=source_commit,
        target_commit=target_commit,
        related_commits=[],
        merge_commit=anchor.is_merge,
    )


def _sort_key(entry: ChangeEntry):
    order = {
        Classification.MISSING_FROM_TARGET: 0,
        Classification.NEEDS_REVIEW: 1,
        Classification.REVERTED: 2,
        Classification.NEW_IN_TARGET: 3,
        Classification.CARRIED_FORWARD: 4,
        Classification.TARGET_ONLY: 5,
    }
    return (order.get(entry.classification, 9), entry.change_id)


def summarize(comparison: ReleaseComparison) -> None:
    """Populate summary counts, attention_items, and release_status in place."""
    s = SummaryCounts()
    attention_items: list[ChangeEntry] = []

    for c in comparison.changes:
        s.total_changes += 1
        if c.classification == Classification.NEW_IN_TARGET:
            s.new_in_target += 1
        elif c.classification == Classification.CARRIED_FORWARD:
            s.carried_forward += 1
        elif c.classification == Classification.MISSING_FROM_TARGET:
            s.potentially_missing += 1
            attention_items.append(c)
        elif c.classification == Classification.TARGET_ONLY:
            s.target_only += 1
        elif c.classification == Classification.REVERTED:
            s.reverted += 1
        elif c.classification == Classification.NEEDS_REVIEW:
            s.needs_review += 1
            attention_items.append(c)

    comparison.summary = s
    comparison.attention_items = attention_items

    if s.potentially_missing > 0 or s.needs_review > 0:
        comparison.release_status = "RELEASE REVIEW REQUIRED"
    else:
        comparison.release_status = "READY FOR RELEASE"
