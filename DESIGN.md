# Design Notes — Release Change Report

These notes are the "explain before you implement" step requested. The
implementation in this repo follows this design exactly.

## 1. Why `git cherry` is the core mechanism (not SHA comparison)

The central problem stated in the brief is:

> release/26.06 was created around the same time fixes were landing on
> release/26.05. FIX A was cherry-picked into 26.06. FIX B was missed.

Cherry-picked commits get **new SHAs** (new parent, possibly new committer/date),
so naive `git log` SHA-set comparison is useless — every single commit on
26.06 looks "new" even when its *content* is identical to a commit on 26.05.

Git already solves this with **patch-id equivalence**: a patch-id is a hash
of a commit's diff with line numbers and blob SHAs normalized out, so the
same logical change produces the same patch-id even after a cherry-pick,
even if the commit message, author date, or committer changed.

`git cherry <upstream> <head>` does exactly the comparison we need in one
call: it walks every commit reachable from `<head>` but not from `<upstream>`,
computes its patch-id, and checks whether an equivalent patch-id exists
anywhere in `<upstream>`'s unique history. It prefixes:

- `-` (minus) — an equivalent patch **was** found upstream → effectively
  "already carried forward"
- `+` (plus) — no equivalent patch found → genuinely new/unique to `<head>`

This is precisely "was FIX A carried forward" vs "was FIX B missed."

### Chosen algorithm

```
merge_base      = git merge-base --all SOURCE TARGET   (divergence point)
source_only     = git log MERGE_BASE..SOURCE            (commits unique to source)
target_only     = git log MERGE_BASE..TARGET            (commits unique to target)

# Patch-equivalence, computed BOTH directions:
cherry_src_in_tgt = git cherry TARGET SOURCE
    -> for each source_only commit: does an equivalent patch exist in target?
cherry_tgt_in_src = git cherry SOURCE TARGET
    -> for each target_only commit: does an equivalent patch exist in source?

# Cross-checked with an independent signal:
patch_id(commit)  = git patch-id --stable computed per-commit via `git show`
                     (used to directly pair source_only <-> target_only
                      commits with the SAME patch-id, independent of
                      git cherry, as a corroborating signal and to produce
                      explicit source-SHA/target-SHA pairs for the report)

# Jira/change-id extraction:
change_id(commit) = regex match on subject line, e.g. [A-Z]{2,10}-\d+
                     used only as a *secondary* correlation signal for
                     NEEDS REVIEW cases (never as a primary equivalence test)
```

We deliberately use **two independent corroborating signals** (git cherry's
internal patch-id matching, and our own direct patch-id computation +
pairing) rather than trusting one blindly, because `git cherry` only tells
you yes/no — it does not hand back *which* target commit matched, which the
report needs to show the "Source SHA → Target SHA" pairing. So:

1. Compute patch-id for every `source_only` and `target_only` commit.
2. Build a patch-id → commit map for each side.
3. A source commit whose patch-id appears in the target map is **CARRIED
   FORWARD**, and we can cite the exact target SHA.
4. A source commit whose patch-id does NOT appear on the target side is
   cross-checked against `git cherry`'s verdict (belt-and-braces — the two
   should agree; if they disagree we treat it as **NEEDS REVIEW** rather
   than silently trusting one).
5. If truly absent from target by both signals → **MISSING FROM TARGET
   RELEASE** (candidate regression risk — the FIX B scenario).
6. A target commit with no source-side patch-id match, not itself a merge
   commit, and not identified as a revert → **NEW IN TARGET RELEASE**.
7. Revert detection: a target-only commit whose subject matches Git's
   standard `Revert "..."` form, and whose body contains
   `This reverts commit <sha>`, is checked against that SHA. If the
   reverted SHA (or its patch-id) is a change we already classified as
   CARRIED FORWARD or NEW IN TARGET, the pair is flagged **REVERTED**
   rather than silently counted as an unrelated new change.
8. Merge commits are excluded from the primary change list (they carry no
   unique diff of their own relative to their first parent in the common
   case) but are logged, and if a merge commit's diff vs its first parent
   is non-empty (e.g. squash-style merges), it is included and treated
   like any other commit.
9. Anything Git cannot safely resolve — patch-id/cherry disagreement,
   ambiguous multi-parent history, empty/whitespace-only diffs, or a
   commit touching only files explicitly outside deployable scope but
   without conclusive evidence — is classified **NEEDS REVIEW**. We never
   guess.

`TARGET-ONLY CHANGE` is **not** derived purely from patch-id/cherry logic:
it's used for target commits that are new but are clearly release-
infrastructure/version-bump only (heuristic: touches only version/build
files, e.g. `pom.xml`, `build.gradle`, `VERSION`) — informational, not a
functional change.

## 2. Edge cases and known limitations

- **Rebased/reworded cherry-picks**: if a cherry-pick is followed by manual
  edits (conflict resolution changes the diff), the patch-id will differ.
  `git cherry`'s patch-id is based on the *final* diff content, so
  non-trivial conflict resolution can cause a true carry-forward to be
  reported as MISSING or NEEDS REVIEW. We mitigate this with the Jira-ID
  secondary signal: if source and target both have a commit with the same
  extracted change ID but different patch-ids, we downgrade from MISSING to
  NEEDS REVIEW with an explicit note, rather than a false regression alarm.
- **Squash merges**: a squash of 3 source commits into 1 target commit will
  not patch-id-match any single source commit. All 3 will show as
  candidates for NEEDS REVIEW/MISSING unless their combined diff happens to
  equal the squashed diff (rare). This is a fundamental Git limitation, not
  something this tool can fully solve; the report notes it in the methodology
  block.
- **File-level partial cherry-picks**: cherry-picking only some hunks of a
  commit produces no patch-id match at all. Falls through to NEEDS REVIEW
  via Jira-ID correlation if a change ID is present, else MISSING.
  candidate.
- **Whitespace/formatting-only reformatting commits**: patch-id is content
  sensitive, so a reformat will never "equal" the original. These will
  appear as NEW/MISSING; a human reading the report is the right place to
  make that call, since the tool deliberately doesn't guess at intent.
- **Merge commits with real conflict-resolution diffs**: included as their
  own change entry, classification determined the same way as any commit,
  but flagged in the report metadata as `merge_commit: true` so reviewers
  know to look closer.
- **No Jira ID in commit message**: change_id field is simply empty; the
  row is still fully reportable by SHA/subject, just not correlated by ID.
- **History that never diverges (target is a superset / fast-forward)**:
  `source_only` is empty — report correctly shows 0 potentially-missing
  items and all target commits as NEW.
- **Non-linear/multiple merge-bases** (criss-cross merges): `git merge-base
  --all` can return more than one base commit. We pick the first
  deterministically (matches `git merge-base` default behavior) and record
  all candidates in the audit metadata for transparency.
- **Rebased or recreated target branch**: if the target branch was rebased
  onto (or freshly created from) a later point on its upstream (e.g.
  `main`), the computed merge-base shifts accordingly, and an equivalent
  fix can end up part of the *shared* ancestry both branches now inherit
  rather than a target-unique commit. A search limited to
  `merge_base..target` (the target-unique `related_commits` hint) cannot
  see it. `target_file_history` searches the file's full history on the
  target branch independent of the divergence point specifically to catch
  this -- it does not change the classification (a MISSING verdict can
  still be correct even when the file was touched earlier in shared
  history), it only ensures a human reviewer sees that context.
- **Binary files**: patch-id still hashes the binary diff bytes; no special
  handling needed, but line add/delete counts for binary files are reported
  as 0/0 (Git convention) and this is noted in the UI ("binary file").
- Tool performs **read-only** Git operations only: `fetch`, `merge-base`,
  `log`, `diff`, `show`, `cherry`, `patch-id`, `rev-parse`. No `checkout`,
  `reset`, `merge`, `rebase`, or history rewriting of any kind.
- Determinism: given the same repo object state, output is byte-identical
  (commit ordering is fixed by SHA, JSON keys are sorted, timestamps aside
  from the `generated_at` field are all sourced from Git object data, not
  wall-clock).

## 3. Data model (see `releaseanalyzer/models.py`)

```
Commit
  sha, short_sha, author_name, author_email, author_date,
  committer_date, subject, body, change_id (Jira ID or None),
  patch_id, is_merge, files_changed[], insertions, deletions,
  pr_reference (extracted from message if present)

ChangeEntry            # one row in the "what are we releasing" table
  change_id                  # e.g. RISK-1832, or synthetic id if absent
  description                 # derived from subject
  change_type                 # Bug Fix / Feature / Chore / Unknown (heuristic)
  classification               # one of the 6 states from DESIGN.md §1
  classification_confidence    # HIGH / MEDIUM / LOW
  classification_reason        # human-readable justification (audit trail)
  source_commit: Optional[Commit]
  target_commit: Optional[Commit]
  related_commits: [Commit]     # e.g. multiple commits under one change id

ReleaseComparison        # top-level report payload
  metadata: RunMetadata   (repo id, source/target branch+sha, merge-base,
                            merge_base_candidates, tool_version, algorithm,
                            generated_at)
  changes: [ChangeEntry]
  summary: SummaryCounts  (derived, not hand-maintained)
  attention_items: [ChangeEntry]  (computed: MISSING + NEEDS_REVIEW subset)
  release_status           # READY FOR RELEASE / RELEASE REVIEW REQUIRED
```

## 4. HTML layout (implemented in `releaseanalyzer/report_html.py`)

1. Executive header — source → target banner + metadata grid.
2. KPI card row — total / new / carried forward / potentially missing /
   needs review / reverted.
3. Overall release status banner (READY / REVIEW REQUIRED), computed from
   `attention_items`, not hand-set.
4. "Potentially missing / regression risk" section — one card per MISSING
   item, prominent amber/red styling, explicit action checklist. This is
   the primary section — the whole point of the report.
5. "What are we releasing?" table — collapsible rows, one per ChangeEntry.
6. Branch divergence SVG diagram — merge-base, source-only commits,
   target-only commits, with ↔ equivalence markers.
7. "Needs review" section — ambiguous classifications, same card layout.
8. Audit/methodology footer — full metadata, deterministic, printable.

All CSS is inlined in the single HTML file (no CDN dependency); a small
inline `<script>` handles only row expand/collapse (no external JS).
