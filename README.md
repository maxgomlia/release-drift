# Release Change & QA Sign-off Report

A standalone Python tool that answers, for two release branches:

> **What exactly are we releasing in the target release branch compared with
> the previous release branch, and has QA explicitly reviewed/signed off
> each relevant change?**

Built for a Git + GitHub Enterprise + enterprise CI/CD-orchestrator environment
(not GitHub Actions — see below). No database, no web server — a read-only
Git analysis engine that emits a self-contained HTML report and a
machine-readable JSON payload.

See **`DESIGN.md`** for the full algorithm rationale, edge cases, and data
model — required reading before modifying `releaseanalyzer/analyzer.py`.

---

## Quick start

```bash
pip install pyyaml   # only non-stdlib dependency

python release_report.py \
    --source release/26.05 \
    --target release/26.06 \
    --repo /path/to/your/repo \
    --signoff release-signoff-26.06.yaml \
    --out-dir ./out
```

Produces `./out/release-report.html` and `./out/release-report.json`.

Run against the repo in your current directory:

```bash
cd /path/to/your/repo
python /path/to/release_report.py --source release/26.05 --target release/26.06
```

### Example output

`example/` contains a synthetic demo repository (built by
`example/build_demo_repo.py`) that reproduces the FIX A / FIX B regression
scenario from the brief, plus a full generated report:

- `example/example-report.html` — open this in a browser first.
- `example/example-report.json`
- `example/release-signoff-26.06.yaml` — the sign-off sidecar that produced it.

Regenerate it any time with:

```bash
python example/build_demo_repo.py
python release_report.py --repo example/demo-repo --source release/26.05 \
    --target release/26.06 --no-fetch --signoff example/release-signoff-26.06.yaml \
    --out-dir example --html-name example-report.html --json-name example-report.json
```

---

## CLI reference

```
python release_report.py --source REF --target REF [options]

--source REF        Source (previous) release branch, e.g. release/26.05  [required]
--target REF        Target (new) release branch, e.g. release/26.06      [required]
--repo PATH          Path to the Git repository                (default: .)
--remote NAME         Git remote to fetch from                  (default: origin)
--no-fetch            Skip `git fetch`; use local refs as-is
--signoff PATH        Path to a QA sign-off sidecar (YAML or JSON)
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
| 0    | READY FOR RELEASE               |
| 1    | QA SIGN-OFF INCOMPLETE          |
| 2    | RELEASE REVIEW REQUIRED (missing changes, needs-review items, or a failed test found) |
| 3    | Git or sign-off input error      |
| 4    | Unexpected error (see traceback) |

A CI/CD pipeline stage can call the CLI and gate on the exit code
directly, or parse `release-report.json` for finer-grained logic (e.g. only
block on `blockers` that are `MISSING FROM TARGET RELEASE`, but warn-only on
`NEEDS REVIEW`).

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

---

## QA Sign-off Workflow

QA/release-management sign-off is supplied via a YAML (or JSON) sidecar
file, matched to changes by `change_id` (Jira key, preferred) or by
`source_sha` / `target_sha` prefix as a fallback:

```yaml
release: release/26.06
reviewer_default: qa-team@example.com

entries:
  - change_id: RISK-1832
    status: SIGNED_OFF          # NOT_REVIEWED | TESTED_PASS | TESTED_FAIL | NOT_APPLICABLE | SIGNED_OFF
    reviewer: jane.doe@example.com
    comments: "Verified in UAT, regression pack green."
    evidence_ref: "https://testrail.example.com/runs/4821"
    reviewed_at: "2026-07-30T10:15:00Z"

  - target_sha: 9f3a1c2
    status: TESTED_FAIL
    reviewer: john.smith@example.com
    comments: "Locate validation still rejects valid GC trades."
    evidence_ref: "DEFECT-5521"
```

The report's audit footer records the sidecar filename **and a content
hash** (`sidecar:release-signoff-26.06.yaml@a1b2c3d4e5f6a7b8`), so any
generated report is traceable to the exact sign-off input that produced it,
and two people can independently verify a report matches a given sidecar
without re-running the tool.

### Auditability & persistence (v1 → production path)

This v1 deliberately uses a file-based sidecar, per the brief, so it can run
standalone with zero infrastructure. It is **not** the end-state for a
regulated environment:

- The sidecar is loaded **read-only** — this tool never writes sign-off
  state back into it or anywhere else. It's the record of what a human
  decided, not something the tool infers or mutates.
- For production use, the sidecar should be checked into the same release
  branch (or a controlled release-management repo) and go through your
  normal PR/approval process — that alone gives you Git history, author
  identity, and immutability for sign-off decisions.
- The natural next step for a bank-grade audit trail is to have your CI/CD orchestrator
  write the sidecar to an internal system of record (e.g. a release-
  management DB or ticketing system) as sign-off happens, and have this
  tool read a generated/exported sidecar at report-generation time — the
  `signoff.py` module's `apply_signoff()` interface doesn't care where the
  YAML/JSON came from, only that it's well-formed.
- **Never** rely on browser-side storage (localStorage, etc.) for sign-off
  state — there is none in this design; the HTML report is a read-only,
  archivable artifact, not an editable form.

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
  signoff.py     -- loads QA sign-off sidecar, matches to changes
  models.py      -- typed dataclasses (Commit, ChangeEntry, ReleaseComparison, ...)
  report_html.py -- self-contained HTML renderer
  report_json.py -- deterministic JSON renderer
        |
        +---- release-report.html   (archivable release evidence)
        +---- release-report.json   (CI/CD / Jira / dashboard integration)
```

No web framework, no database, no Kubernetes. `release_report.py` is
designed to be invoked identically whether run by an engineer locally or
triggered as a CI/CD pipeline stage — same CLI, same exit codes, same
JSON contract.

---

## Testing

```bash
python -m unittest discover -s tests -v
```

- `tests/test_units.py` — unit tests for `git_ops` (merge-base, patch-id
  stability, log parsing, cherry) and a smoke test of the HTML renderer.
- `tests/test_integration.py` — full end-to-end tests against real,
  throwaway Git repositories (built by `tests/repo_builder.py`), including:
  - **The exact FIX A / FIX B scenario from the brief**: FIX A is
    cherry-picked into the target release (different SHA) and correctly
    classified `CARRIED FORWARD`; FIX B is never carried over and is
    correctly classified `MISSING FROM TARGET RELEASE` and surfaced as a
    release blocker.
  - New-in-target classification.
  - Fast-forward / non-divergent history (no false positives).
  - Revert detection.
  - QA sign-off sidecar application and its effect on `release_status`.

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

## CI/CD pipeline integration

`example/cicd-stage-example.yaml` shows the intended integration
shape: the CLI is invoked with plain arguments, gates on process exit code,
and hands downstream steps `release-report.json` for notifications/Jira/
dashboards. Nothing about the tool assumes any specific CI/CD orchestrator — it's
CI/CD-orchestrator-agnostic by design (see exit codes table above).

## Optional: GitHub Enterprise API enrichment

If `--github-api-url` + `--github-token` are supplied, `releaseanalyzer/github_api.py`
performs read-only lookups against `/repos/{owner}/{repo}/pulls/{number}` for
any commit with an extractable PR reference (`(#123)` in the subject/body),
and attaches the PR title, URL, and merge state to the report. This is
strictly additive and cosmetic: if the API is unreachable, unauthenticated,
or times out, the tool logs a warning and continues — classification results
never depend on it, per the "no GitHub Actions / minimal external dependency"
requirement in the brief.

## Files in this repository

```
release_report.py              CLI entry point
releaseanalyzer/                core package
  git_ops.py
  analyzer.py
  signoff.py
  github_api.py                  optional, read-only PR enrichment (GitHub Enterprise API)
  models.py
  report_html.py
  report_json.py
tests/
  repo_builder.py                test helper: builds throwaway git repos
  test_units.py
  test_integration.py             FIX A / FIX B scenario, new/missing/revert/sign-off
  test_edge_cases.py              merge commits, reworked fixes, github_api parsing
example/
  build_demo_repo.py             builds the demo repo used for example output
  release-signoff-26.06.yaml     example sign-off sidecar
  example-report.html            generated example report
  example-report.json            generated example JSON
  cicd-stage-example.yaml         illustrative CI/CD pipeline integration
DESIGN.md                       algorithm rationale, edge cases, data model
README.md                       this file
```
