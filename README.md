# Release Change Report

A standalone Python tool that answers, for two release branches:

> **What exactly are we releasing in the target release branch compared with
> the previous release branch — and did we miss carrying anything forward
> from the previous release?**

Built for a Git + GitHub Enterprise + enterprise CI/CD-orchestrator environment
(not GitHub Actions — see below). Zero external dependencies, no database,
no web server — a read-only Git analysis engine that emits a self-contained
HTML report and a machine-readable JSON payload.

See **`DESIGN.md`** for the full algorithm rationale, edge cases, and data
model — required reading before modifying `releaseanalyzer/analyzer.py`.

---

## Quick start

**Zero external dependencies.** Everything here is Python standard library.

```bash
cd /path/to/your/real/repo

python /path/to/release-report/release_report.py \
    --source release/26.05 \
    --target release/26.06 \
    --out-dir ~/release-reports
```

Produces `~/release-reports/release-report.html` and `release-report.json`.
Open the HTML in a browser — the "Potentially Missing" section at the top
is the one to look at first.

By default the tool **does not** run `git fetch` — it compares whatever's
already in your local clone. This is deliberate: `git fetch` can trigger a
credential prompt (e.g. Git Credential Manager popping up a window on
Windows) if your remote needs re-authentication, and a read-only analysis
tool shouldn't surprise you with that. If you want it to refresh from
`origin` first, opt in explicitly:

```bash
python /path/to/release-report/release_report.py \
    --source release/26.05 --target release/26.06 --fetch
```

### Example output

`example/` contains a synthetic demo repository (built by
`example/build_demo_repo.py`) that reproduces a cherry-pick/regression
scenario — a fix cherry-picked into the target release under a different
SHA (correctly recognised as carried forward), and a second fix that never
made it over (correctly flagged as potentially missing) — plus the full
generated report:

- `example/example-report.html` — open this in a browser first.
- `example/example-report.json`

Regenerate it any time with:

```bash
python example/build_demo_repo.py
python release_report.py --repo example/demo-repo --source release/26.05 \
    --target release/26.06 --out-dir example \
    --html-name example-report.html --json-name example-report.json
```

---

## CLI reference

```
python release_report.py --source REF --target REF [options]

--source REF        Source (previous) release branch, e.g. release/26.05  [required]
--target REF        Target (new) release branch, e.g. release/26.06      [required]
--repo PATH          Path to the Git repository                (default: .)
--remote NAME         Git remote to fetch from                  (default: origin)
--fetch                Run 'git fetch' before comparing (default: off -- avoids
                          surprise credential prompts; opt in explicitly)
--out-dir PATH        Output directory                          (default: .)
--html-name NAME       Output HTML filename                     (default: release-report.html)
--json-name NAME       Output JSON filename                     (default: release-report.json)
--no-html / --no-json  Skip one of the two outputs
--github-api-url URL   Optional: GitHub Enterprise API base (e.g. https://ghe.example.com/api/v3)
                          to enrich changes with PR title/url/state. Purely cosmetic --
                          classification never depends on this or on network access.
--github-token TOKEN    Required if --github-api-url is set
--github-owner NAME     Repo owner/org (default: parsed from git remote URL)
--github-repo NAME      Repo name (default: parsed from git remote URL)
-v / --verbose         Debug logging
--version
```

### Exit codes (for CI/CD release gates)

| Code | Meaning                        |
|------|---------------------------------|
| 0    | READY FOR RELEASE — no missing changes, no ambiguous classifications |
| 1    | RELEASE REVIEW REQUIRED — missing changes and/or ambiguous classifications found |
| 2    | Git or input error               |
| 3    | Unexpected error (see traceback) |

A CI/CD pipeline stage can call the CLI and gate on the exit code directly,
or parse `release-report.json` for finer-grained logic.

---

## The comparison algorithm, in brief

1. `git merge-base --all SOURCE TARGET` → divergence point.
2. `git log MERGE_BASE..SOURCE` / `..TARGET` → commits unique to each side.
3. `git patch-id --stable` per commit, both sides → direct patch-id pairing
   (this is what makes a cherry-picked commit with a **different SHA**
   still recognised as the **same change**).
4. `git cherry` run in both directions as an independent corroborating
   signal; disagreement between the two signals → `NEEDS REVIEW`, never a
   silent guess.
5. Revert detection via Git's standard `Revert "..."` / `This reverts
   commit <sha>` message convention.
6. Jira/change-ID correlation (regex on commit subject) used only as a
   *secondary* signal to downgrade an apparent gap to `NEEDS REVIEW` when a
   same-ID commit exists on both sides with a different diff (likely
   reworked, not missing).

Full rationale, the exact edge cases this does and doesn't handle (squash
merges, partial cherry-picks, criss-cross merge-bases, etc.), and the data
model are in **`DESIGN.md`**.

**No repository state is ever mutated.** Every Git call is read-only:
`merge-base`, `log`, `diff`, `show`, `patch-id`, `cherry`, `rev-parse`,
`fetch`. Nothing calls `checkout`, `reset`, `merge`, `rebase`, or push.

### Classification states

| State | Meaning |
|---|---|
| `CARRIED FORWARD` | Change from source found (by content) in target |
| `NEW IN TARGET RELEASE` | Change only exists in target |
| `MISSING FROM TARGET RELEASE` | Change exists in source, no equivalent found in target — **the regression-risk list** |
| `TARGET-ONLY CHANGE` | New in target, but touches only version/build metadata (informational, not functional) |
| `REVERTED` | A target commit reverts an earlier change |
| `NEEDS REVIEW` | Git's signals disagreed or were insufficient — flagged rather than guessed |

### Investigating a "Potentially Missing" item

Every card in the "Potentially Missing" and "Needs Review" sections is
collapsed by default, showing just the change ID and description so the
report stays scannable. Click a card to expand it and reveal, without
leaving the report:

- **Files changed** by the source-side commit
- **A colorized, per-file diff** (GitHub PR style — green for added lines,
  red for removed, with line numbers). Each file starts collapsed too —
  click a filename to reveal its diff, so you only load the diffs you
  actually want to inspect. For `NEEDS REVIEW` items with both a source
  and target commit (e.g. a reworked fix), both diffs are available so you
  can visually compare intent.
- **"Possibly related target commit(s)"** — if any target-only commit
  touched the same file(s), it's listed as a hint, even when the diff
  didn't patch-id-match. This is specifically for changes that were
  manually re-ported (typed by hand, not `git cherry-pick`ed) with a
  slightly different diff than the original
- **Copy-pasteable `git show`/`git log` commands** for a full manual check
  against your real repo

Printing/saving-as-PDF automatically expands everything first (and
restores your view afterwards), so an archived copy always shows the full
detail regardless of what was expanded on screen.

**Does this catch manually re-ported changes (not cherry-picked)?**
Classification is based purely on **diff content** (via `git patch-id`),
never on how a commit was created. If someone hand-types the exact same
change, producing an identical diff, it's correctly recognised as `CARRIED
FORWARD`, same as a cherry-pick. If the manual port is incomplete or
differs even slightly, it won't patch-id-match, and shows as `MISSING` or
`NEEDS REVIEW` rather than silently passing — the related-commit
file-overlap hint above exists specifically to surface these without
requiring a manual search.

---

## CI/CD pipeline integration

`example/cicd-stage-example.yaml` shows the intended integration shape: the
CLI is invoked with plain arguments, gates on process exit code, and hands
downstream steps `release-report.json` for notifications/dashboards.
Nothing about the tool assumes any specific CI/CD orchestrator — it's
orchestrator-agnostic by design (see exit codes table above).

## Optional: GitHub Enterprise API enrichment

If `--github-api-url` + `--github-token` are supplied, `releaseanalyzer/github_api.py`
performs read-only lookups against `/repos/{owner}/{repo}/pulls/{number}` for
any commit with an extractable PR reference (`(#123)` in the subject/body),
and attaches the PR title, URL, and merge state to the report. This is
strictly additive and cosmetic: if the API is unreachable, unauthenticated,
or times out, the tool logs a warning and continues — classification results
never depend on it.

---

## Architecture

```
Git repository (GitHub Enterprise)
        |
        v
release_report.py  (CLI)
        |
        v
releaseanalyzer/
  git_ops.py     -- read-only git subprocess wrappers
  analyzer.py    -- comparison + classification algorithm -> ReleaseComparison
  models.py      -- typed dataclasses (Commit, ChangeEntry, ReleaseComparison, ...)
  report_html.py -- self-contained HTML renderer
  report_json.py -- deterministic JSON renderer
  github_api.py  -- optional, read-only PR metadata enrichment
        |
        +---- release-report.html   (archivable release evidence)
        +---- release-report.json   (CI/CD / dashboard integration)
```

No web framework, no database, no Kubernetes, no external packages.
`release_report.py` is designed to be invoked identically whether run by an
engineer locally or triggered as a CI/CD pipeline stage — same CLI, same
exit codes, same JSON contract.

---

## Testing

```bash
python -m unittest discover -s tests -v
```

- `tests/test_units.py` — unit tests for `git_ops` (merge-base, patch-id
  stability, log parsing, cherry), `diff_parser` (added/removed line
  numbering, new-file detection, multi-file diffs), and a smoke test of
  the HTML renderer.
- `tests/test_integration.py` — full end-to-end tests against real,
  throwaway Git repositories (built by `tests/repo_builder.py`), including:
  - **A cherry-pick/regression scenario**: a fix cherry-picked into the
    target release (different SHA) is correctly classified `CARRIED
    FORWARD`; a second fix never carried over is correctly classified
    `MISSING FROM TARGET RELEASE` and surfaced in `attention_items`.
  - New-in-target classification.
  - Fast-forward / non-divergent history (no false positives).
  - Revert detection.
- `tests/test_edge_cases.py` — merge commits with real diffs, reworked
  fixes (same Jira ID, different diff → `NEEDS REVIEW` not a false
  `MISSING` alarm), and `github_api` URL-parsing helpers.
- `tests/test_integration.py` also covers **manual (non-cherry-pick)
  porting**: an identical hand-typed change still classifies as `CARRIED
  FORWARD`, an incomplete/different hand-port downgrades to `NEEDS
  REVIEW`/`MISSING` rather than silently passing, and the file-overlap
  hint (`related_commits`) correctly surfaces a same-file target commit
  even with no matching change ID to correlate on.

All tests operate on real Git object data (no mocking of git commands),
since the whole point of this tool is correct behaviour against real Git
plumbing.

---

## Known limitations (see `DESIGN.md` §2 for full detail)

- Cherry-picks that were manually edited during conflict resolution can
  fall through to `NEEDS REVIEW` rather than `CARRIED FORWARD`, since the
  patch-id changes with the diff content. This is a fundamental property of
  content-based patch identity, not a bug — the tool flags it rather than
  guessing.
- Squash-merged commits generally won't patch-id-match any single source
  commit; the Jira-ID correlation partially mitigates this but the tool
  cannot fully reconstruct history that Git itself has discarded.
- Multiple merge-base candidates (criss-cross merges) are recorded in
  report metadata even though only the first is used for the comparison.

---

## Files in this repository

```
release_report.py              CLI entry point
releaseanalyzer/                core package
  git_ops.py
  analyzer.py
  github_api.py                  optional, read-only PR enrichment (GitHub Enterprise API)
  diff_parser.py                  parses unified diff into structured, colorizable per-line data
  models.py
  report_html.py
  report_json.py
tests/
  repo_builder.py                test helper: builds throwaway git repos
  test_units.py
  test_integration.py             cherry-pick/regression scenario, new/missing/revert
  test_edge_cases.py              merge commits, reworked fixes, github_api parsing
example/
  build_demo_repo.py             builds the demo repo used for example output
  example-report.html            generated example report
  example-report.json            generated example JSON
  cicd-stage-example.yaml        illustrative CI/CD pipeline integration
DESIGN.md                       algorithm rationale, edge cases, data model
README.md                       this file
```
