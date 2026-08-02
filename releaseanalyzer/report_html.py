"""Renders a ReleaseComparison into a single, self-contained, printable
HTML file. No external CSS/JS/font dependency (system font stack only),
so the file is safe to archive as release evidence and open offline.

Purely a comparison/diff report -- no QA sign-off workflow. The point is
to answer, at a glance: what's being released, and what may have been
missed from the previous release branch.
"""
from __future__ import annotations

import html
from .models import ReleaseComparison, ChangeEntry, Classification

STATUS_BANNER = {
    "READY FOR RELEASE": ("status-ready", "READY FOR RELEASE"),
    "RELEASE REVIEW REQUIRED": ("status-block", "RELEASE REVIEW REQUIRED"),
}

CLASS_BADGE = {
    Classification.CARRIED_FORWARD: ("badge-neutral", "Carried Forward"),
    Classification.NEW_IN_TARGET: ("badge-info", "New in Target"),
    Classification.MISSING_FROM_TARGET: ("badge-danger", "Potentially Missing"),
    Classification.TARGET_ONLY: ("badge-neutral", "Target-Only"),
    Classification.REVERTED: ("badge-warn", "Reverted"),
    Classification.NEEDS_REVIEW: ("badge-warn", "Needs Review"),
}


def _e(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def render(comparison: ReleaseComparison) -> str:
    m = comparison.metadata
    s = comparison.summary

    missing = [c for c in comparison.changes if c.classification == Classification.MISSING_FROM_TARGET]
    needs_review = [c for c in comparison.changes if c.classification == Classification.NEEDS_REVIEW]

    banner_class, banner_label = STATUS_BANNER.get(
        comparison.release_status, ("status-block", comparison.release_status)
    )

    rows_html = "\n".join(_render_row(c, i) for i, c in enumerate(comparison.changes))
    missing_html = (
        "".join(_render_missing_card(c, metadata=m) for c in missing)
        if missing else '<p class="empty-state">No changes present on the source release appear to be missing from the target release.</p>'
    )
    review_html = (
        "".join(_render_missing_card(c, tone="warn", metadata=m) for c in needs_review)
        if needs_review else '<p class="empty-state">No changes require manual classification review.</p>'
    )
    branch_svg = _render_branch_svg(comparison)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Release Change Report — {_e(m.source_branch)} → {_e(m.target_branch)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">

  <header class="exec-header">
    <div class="exec-title">
      <div class="eyebrow">Release Change Report</div>
      <div class="release-flow">
        <div class="release-box">
          <div class="release-label">Source Release</div>
          <div class="release-name">{_e(m.source_branch)}</div>
        </div>
        <div class="release-arrow" aria-hidden="true">&#8594;</div>
        <div class="release-box">
          <div class="release-label">Target Release</div>
          <div class="release-name">{_e(m.target_branch)}</div>
        </div>
      </div>
    </div>
    <div class="status-banner {banner_class}">{_e(banner_label)}</div>
  </header>

  <section class="meta-grid">
    <div><span class="meta-k">Generated</span><span class="meta-v">{_e(m.generated_at)}</span></div>
    <div><span class="meta-k">Repository</span><span class="meta-v">{_e(m.repository)}</span></div>
    <div><span class="meta-k">Source branch</span><span class="meta-v">{_e(m.source_branch)} @ {_e(m.source_sha[:10])}</span></div>
    <div><span class="meta-k">Target branch</span><span class="meta-v">{_e(m.target_branch)} @ {_e(m.target_sha[:10])}</span></div>
    <div><span class="meta-k">Common ancestor</span><span class="meta-v">{_e(m.merge_base_sha[:10])}</span></div>
    <div><span class="meta-k">Target HEAD</span><span class="meta-v">{_e(m.target_sha[:10])}</span></div>
    <div><span class="meta-k">Comparison method</span><span class="meta-v">{_e(m.comparison_algorithm)}</span></div>
    <div><span class="meta-k">Tool version</span><span class="meta-v">release-report v{_e(m.tool_version)}</span></div>
  </section>

  <section class="kpi-row">
    {_kpi(s.total_changes, "Total Changes")}
    {_kpi(s.new_in_target, "New in Target")}
    {_kpi(s.carried_forward, "Carried Forward")}
    {_kpi(s.potentially_missing, "Potentially Missing", "danger" if s.potentially_missing else "neutral")}
    {_kpi(s.needs_review, "Needs Review", "warn" if s.needs_review else "neutral")}
    {_kpi(s.reverted, "Reverted")}
  </section>

  <section class="section">
    <h2>Regression Risk — Potentially Missing from {_e(m.target_branch)}</h2>
    <p class="section-note">
      Changes present on <strong>{_e(m.source_branch)}</strong> since the common ancestor with no
      equivalent patch (by patch-id or git-cherry) detected on <strong>{_e(m.target_branch)}</strong>.
      This does not automatically mean an error — some fixes are legitimately source-release-specific —
      but every item below is worth a deliberate decision before release.
    </p>
    {missing_html}
  </section>

  <section class="section">
    <h2>What Are We Releasing? — {_e(m.target_branch)}</h2>
    <p class="section-note">Every relevant change since the common ancestor, classified against the source release. Click a row to expand full detail.</p>
    <table class="change-table">
      <thead>
        <tr>
          <th>Change</th>
          <th>Description</th>
          <th>Type</th>
          <th>Classification</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </section>

  <section class="section">
    <h2>Branch Divergence</h2>
    <p class="section-note">Relevant history only — divergence point through both release heads.</p>
    <div class="branch-viz">{branch_svg}</div>
  </section>

  <section class="section">
    <h2>Needs Review — Ambiguous Classification</h2>
    <p class="section-note">
      Cases where Git's available signals (patch-id, git-cherry, change-id correlation) disagreed or
      were insufficient to safely classify automatically. These are not assumptions — they are flagged
      precisely because an unsafe assumption was avoided.
    </p>
    {review_html}
  </section>

  <footer class="audit-footer">
    <h3>Methodology</h3>
    <dl class="audit-dl">
      <dt>Repository</dt><dd>{_e(m.repository)}</dd>
      <dt>Source</dt><dd>{_e(m.source_branch)} @ {_e(m.source_sha)}</dd>
      <dt>Target</dt><dd>{_e(m.target_branch)} @ {_e(m.target_sha)}</dd>
      <dt>Common ancestor</dt><dd>{_e(m.merge_base_sha)}</dd>
      <dt>Merge-base candidates</dt><dd>{_e(", ".join(s[:10] for s in m.merge_base_candidates))}</dd>
      <dt>Algorithm</dt><dd>{_e(m.comparison_algorithm)}</dd>
      <dt>Tool version</dt><dd>{_e(m.tool_version)}</dd>
      <dt>Generated</dt><dd>{_e(m.generated_at)}</dd>
    </dl>
    <p class="disclaimer">
      This report is generated from read-only Git analysis (merge-base, log, diff, patch-id, cherry).
      No repository state was modified during generation. Where Git could not safely determine a
      change's relationship between releases, it is explicitly marked "Needs Review" rather than assumed.
    </p>
  </footer>

</div>
<script>{_JS}</script>
</body>
</html>
"""


def _kpi(value, label, tone: str = "neutral") -> str:
    return f'<div class="kpi kpi-{tone}"><div class="kpi-value">{_e(value)}</div><div class="kpi-label">{_e(label)}</div></div>'


def _pr_cell(commit) -> str:
    if not commit.pr_reference:
        return "—"
    if commit.pr_url:
        state = f" ({_e(commit.pr_state)})" if commit.pr_state else ""
        title = f" — {_e(commit.pr_title)}" if commit.pr_title else ""
        return f'<a href="{_e(commit.pr_url)}">{_e(commit.pr_reference)}</a>{state}{title}'
    return _e(commit.pr_reference)


def _commit_block(title: str, commit) -> str:
    if commit is None:
        return f'<div class="commit-block empty"><div class="commit-block-title">{_e(title)}</div><div class="commit-empty">— none —</div></div>'
    files_html = "".join(f"<li>{_e(f)}</li>" for f in commit.files_changed[:25])
    more = f"<li class='more'>+{len(commit.files_changed) - 25} more file(s)</li>" if len(commit.files_changed) > 25 else ""
    return f"""
    <div class="commit-block">
      <div class="commit-block-title">{_e(title)}</div>
      <table class="commit-meta">
        <tr><th>SHA</th><td><code>{_e(commit.sha)}</code></td></tr>
        <tr><th>Author</th><td>{_e(commit.author_name)} &lt;{_e(commit.author_email)}&gt;</td></tr>
        <tr><th>Author date</th><td>{_e(commit.author_date)}</td></tr>
        <tr><th>Subject</th><td>{_e(commit.subject)}</td></tr>
        <tr><th>PR</th><td>{_pr_cell(commit)}</td></tr>
        <tr><th>Files changed</th><td>{len(commit.files_changed)}</td></tr>
        <tr><th>Lines</th><td><span class="ins">+{commit.insertions}</span> / <span class="del">-{commit.deletions}</span></td></tr>
        <tr><th>Patch-id</th><td><code>{_e((commit.patch_id or "n/a")[:16])}</code></td></tr>
      </table>
      <details class="file-list"><summary>Files changed ({len(commit.files_changed)})</summary><ul>{files_html}{more}</ul></details>
    </div>"""


def _render_row(c: ChangeEntry, idx: int) -> str:
    cls_class, cls_label = CLASS_BADGE.get(c.classification, ("badge-neutral", c.classification.value))
    row_id = f"row-{idx}"
    return f"""
    <tr class="change-row" data-target="{row_id}" onclick="toggleRow('{row_id}')">
      <td class="mono">{_e(c.change_id)}</td>
      <td>{_e(c.description)}</td>
      <td>{_e(c.change_type)}</td>
      <td><span class="badge {cls_class}">{_e(cls_label)}</span></td>
    </tr>
    <tr class="change-detail-row" id="{row_id}">
      <td colspan="4">
        <div class="change-detail">
          <p class="reason"><strong>Classification basis:</strong> {_e(c.classification_reason)}
             <span class="confidence">(confidence: {_e(c.classification_confidence.value)})</span></p>
          <div class="commit-blocks">
            {_commit_block(f"Source ({c.source_commit.short_sha if c.source_commit else '—'})", c.source_commit)}
            {_commit_block(f"Target ({c.target_commit.short_sha if c.target_commit else '—'})", c.target_commit)}
          </div>
        </div>
      </td>
    </tr>"""


def _files_list(commit) -> str:
    if not commit or not commit.files_changed:
        return "—"
    shown = commit.files_changed[:12]
    items = "".join(f"<li>{_e(f)}</li>" for f in shown)
    more = f"<li class='more'>+{len(commit.files_changed) - 12} more</li>" if len(commit.files_changed) > 12 else ""
    return f'<ul class="risk-files">{items}{more}</ul>'


def _diff_snippet(commit) -> str:
    if not commit or not commit.diff_text:
        return ""
    return f'<details class="risk-diff"><summary>Show diff ({commit.short_sha})</summary><pre>{_e(commit.diff_text)}</pre></details>'


def _related_commits_hint(c: ChangeEntry) -> str:
    if not c.related_commits:
        return ""
    rows = "".join(
        f'<li><code>{_e(rc.short_sha)}</code> {_e(rc.subject)} '
        f'<span class="muted">({len(rc.files_changed)} file(s), touches a file this change also touched)</span></li>'
        for rc in c.related_commits
    )
    return f"""
      <div class="risk-hint">
        <strong>Possibly related target commit(s)</strong> — same file(s) touched, but the diff didn't
        match automatically. Worth checking manually in case this was ported by hand rather than cherry-picked:
        <ul>{rows}</ul>
      </div>"""


def _manual_check_commands(c: ChangeEntry, m) -> str:
    src, tgt = c.source_commit, c.target_commit
    lines = []
    if src:
        lines.append(f"git show {src.sha}")
        if src.files_changed:
            lines.append(f"git log {m.target_branch} -- {src.files_changed[0]}   # see target-branch history for this file")
    if tgt:
        lines.append(f"git show {tgt.sha}")
    if not lines:
        return ""
    cmds = "\n".join(lines)
    return f'<details class="risk-cmds"><summary>Commands to verify manually</summary><pre>{_e(cmds)}</pre></details>'


def _render_missing_card(c: ChangeEntry, tone: str = "danger", metadata=None) -> str:
    src = c.source_commit
    tgt = c.target_commit
    heading = "POTENTIALLY MISSING FROM TARGET" if tone == "danger" else "NEEDS REVIEW"
    return f"""
    <div class="risk-card risk-{tone}">
      <div class="risk-heading">&#9888; {heading}</div>
      <div class="risk-change-id">{_e(c.change_id)}</div>
      <div class="risk-desc">{_e(c.description)}</div>
      <div class="risk-meta">
        <div><span class="meta-k">Present in</span><span class="meta-v">source release{f" — {_e(src.short_sha)}" if src else ""}</span></div>
        <div><span class="meta-k">Equivalent in target</span><span class="meta-v">{_e(tgt.short_sha) if tgt else "NOT FOUND"}</span></div>
        <div><span class="meta-k">Basis</span><span class="meta-v">{_e(c.classification_reason)}</span></div>
      </div>
      <div class="risk-files-block">
        <span class="meta-k">Files changed (source)</span>
        {_files_list(src)}
      </div>
      {_diff_snippet(src)}
      {_related_commits_hint(c)}
      {_manual_check_commands(c, metadata) if metadata else ""}
      <div class="risk-actions">
        Action required:
        <label><input type="checkbox" disabled> Carry forward</label>
        <label><input type="checkbox" disabled> Confirm not applicable</label>
        <label><input type="checkbox" disabled> Investigate</label>
      </div>
    </div>"""


def _render_branch_svg(comparison: ReleaseComparison) -> str:
    m = comparison.metadata
    carried = sum(1 for c in comparison.changes if c.classification == Classification.CARRIED_FORWARD)
    missing = sum(1 for c in comparison.changes if c.classification == Classification.MISSING_FROM_TARGET)
    new_t = sum(1 for c in comparison.changes if c.classification == Classification.NEW_IN_TARGET)
    return f"""
    <svg viewBox="0 0 900 220" xmlns="http://www.w3.org/2000/svg" class="branch-svg" role="img"
         aria-label="Branch divergence diagram">
      <line x1="40" y1="110" x2="230" y2="110" stroke="#5b6b82" stroke-width="2"/>
      <circle cx="230" cy="110" r="6" fill="#1f2937"/>
      <text x="20" y="100" font-size="12" fill="#334155">main</text>
      <text x="205" y="135" font-size="11" fill="#64748b">common ancestor</text>
      <text x="205" y="148" font-size="10" fill="#94a3b8" font-family="monospace">{_e(m.merge_base_sha[:10])}</text>

      <line x1="230" y1="110" x2="620" y2="55" stroke="#1a7f37" stroke-width="2"/>
      <circle cx="620" cy="55" r="6" fill="#1a7f37"/>
      <text x="240" y="45" font-size="12" fill="#334155">{_e(m.source_branch)}</text>
      <text x="630" y="59" font-size="11" fill="#1a7f37">{carried} carried, {missing} missing</text>

      <line x1="230" y1="110" x2="620" y2="170" stroke="#4b5fbd" stroke-width="2"/>
      <circle cx="620" cy="170" r="6" fill="#4b5fbd"/>
      <text x="240" y="195" font-size="12" fill="#334155">{_e(m.target_branch)}</text>
      <text x="630" y="174" font-size="11" fill="#4b5fbd">{new_t} new in target</text>

      <line x1="330" y1="86" x2="330" y2="134" stroke="#94a3b8" stroke-dasharray="3,3"/>
      <text x="336" y="113" font-size="10" fill="#64748b">&#8596; equivalence checked via patch-id / git-cherry</text>
    </svg>"""


_CSS = """
:root{
  --navy:#0f1b2d; --charcoal:#1f2937; --muted:#5b6b82; --border:#dbe1ea;
  --bg:#f5f6f8; --card:#ffffff;
  --green:#1a7f37; --green-bg:#e6f4ea;
  --amber:#9a6b00; --amber-bg:#fdf3d8;
  --red:#b3261e; --red-bg:#fbe7e5;
  --blue:#3d4ea8; --blue-bg:#eaecfa;
}
*{box-sizing:border-box;}
body{margin:0;font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--charcoal);}
.page{max-width:1200px;margin:0 auto;padding:32px 40px 64px;}
h2{font-size:18px;color:var(--navy);border-bottom:1px solid var(--border);padding-bottom:8px;margin:0 0 12px;}
h3{font-size:15px;color:var(--navy);margin:20px 0 8px;}
.section{margin-top:36px;}
.section-note{color:var(--muted);font-size:13px;margin:0 0 14px;max-width:900px;}

.exec-header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px;
  background:var(--navy);color:#fff;padding:28px 32px;border-radius:6px;}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#9fb0c9;margin-bottom:10px;}
.release-flow{display:flex;align-items:center;gap:20px;}
.release-box{min-width:170px;}
.release-label{font-size:11px;color:#9fb0c9;text-transform:uppercase;letter-spacing:.06em;}
.release-name{font-size:22px;font-weight:600;font-family:monospace;}
.release-arrow{font-size:26px;color:#9fb0c9;}
.status-banner{padding:10px 20px;border-radius:4px;font-weight:700;font-size:14px;letter-spacing:.03em;white-space:nowrap;}
.status-ready{background:var(--green-bg);color:var(--green);}
.status-block{background:var(--red-bg);color:var(--red);}

.meta-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px 24px;background:var(--card);
  border:1px solid var(--border);border-radius:6px;padding:18px 24px;margin-top:20px;font-size:13px;}
.meta-k{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;}
.meta-v{display:block;color:var(--navy);font-weight:600;word-break:break-all;}

.kpi-row{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-top:24px;}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:18px 10px;text-align:center;}
.kpi-value{font-size:28px;font-weight:700;color:var(--navy);}
.kpi-label{font-size:11px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.03em;}
.kpi-danger .kpi-value{color:var(--red);}
.kpi-warn .kpi-value{color:var(--amber);}

table.change-table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:6px;overflow:hidden;font-size:13px;}
table.change-table th{text-align:left;background:#eef1f6;color:var(--navy);padding:10px 14px;font-size:11px;text-transform:uppercase;letter-spacing:.03em;}
table.change-table td{padding:10px 14px;border-top:1px solid var(--border);}
.change-row{cursor:pointer;}
.change-row:hover{background:#f0f3f8;}
.change-detail-row{display:none;background:#fafbfc;}
.change-detail-row.open{display:table-row;}
.change-detail{padding:16px 14px;font-size:13px;}
.reason{margin:0 0 12px;color:var(--charcoal);}
.confidence{color:var(--muted);font-size:12px;}
.commit-blocks{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.commit-block{border:1px solid var(--border);border-radius:4px;padding:10px 12px;background:#fff;}
.commit-block.empty{color:var(--muted);}
.commit-block-title{font-weight:700;color:var(--navy);font-size:12px;margin-bottom:6px;text-transform:uppercase;}
table.commit-meta{width:100%;font-size:12px;border-collapse:collapse;}
table.commit-meta th{text-align:left;color:var(--muted);font-weight:600;padding:2px 8px 2px 0;vertical-align:top;white-space:nowrap;}
table.commit-meta td{padding:2px 0;word-break:break-word;}
.ins{color:var(--green);} .del{color:var(--red);}
.file-list{margin-top:8px;font-size:12px;}
.file-list ul{margin:6px 0 0;padding-left:18px;max-height:140px;overflow:auto;}

.badge{display:inline-block;padding:3px 9px;border-radius:12px;font-size:11px;font-weight:700;letter-spacing:.02em;}
.badge-warn{background:var(--amber-bg);color:var(--amber);}
.badge-danger{background:var(--red-bg);color:var(--red);}
.badge-info{background:var(--blue-bg);color:var(--blue);}
.badge-neutral{background:#eef1f6;color:var(--muted);}

.risk-card{border-radius:6px;padding:16px 20px;margin-bottom:14px;border:1px solid var(--border);}
.risk-danger{background:var(--red-bg);border-color:#f0c4c0;}
.risk-warn{background:var(--amber-bg);border-color:#f2dfa0;}
.risk-heading{font-weight:700;font-size:12px;letter-spacing:.03em;color:var(--red);margin-bottom:6px;}
.risk-warn .risk-heading{color:var(--amber);}
.risk-change-id{font-family:monospace;font-size:15px;font-weight:700;color:var(--navy);}
.risk-desc{font-size:14px;margin:2px 0 10px;}
.risk-meta{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px 20px;font-size:12px;margin-bottom:10px;}
.risk-files-block{margin-bottom:10px;}
.risk-files{list-style:none;margin:4px 0 0;padding:0;font-size:12px;font-family:monospace;color:var(--charcoal);}
.risk-files li{padding:1px 0;}
.risk-files li.more{color:var(--muted);font-style:italic;}
.risk-diff{margin:10px 0;font-size:12px;}
.risk-diff summary{cursor:pointer;color:var(--navy);font-weight:600;}
.risk-diff pre{background:#0f1b2d;color:#d6e0f0;padding:12px;border-radius:4px;overflow-x:auto;font-size:11px;line-height:1.5;margin-top:6px;max-height:320px;overflow-y:auto;}
.risk-hint{margin:10px 0;font-size:12px;background:rgba(255,255,255,.5);border:1px solid var(--border);border-radius:4px;padding:10px 12px;}
.risk-hint ul{margin:6px 0 0;padding-left:18px;}
.risk-hint .muted{color:var(--muted);}
.risk-cmds{margin:10px 0;font-size:12px;}
.risk-cmds summary{cursor:pointer;color:var(--navy);font-weight:600;}
.risk-cmds pre{background:#eef1f6;color:var(--navy);padding:10px 12px;border-radius:4px;overflow-x:auto;font-size:11.5px;margin-top:6px;}
.risk-actions{font-size:12px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap;align-items:center;}
.risk-actions label{display:inline-flex;gap:4px;align-items:center;}

.branch-viz{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:16px;}
.branch-svg{width:100%;height:auto;}

.empty-state{color:var(--muted);font-size:13px;font-style:italic;}
.mono{font-family:monospace;}

.audit-footer{margin-top:48px;border-top:2px solid var(--border);padding-top:20px;}
.audit-dl{display:grid;grid-template-columns:180px 1fr;gap:4px 12px;font-size:12px;}
.audit-dl dt{color:var(--muted);}
.audit-dl dd{margin:0;font-family:monospace;color:var(--navy);word-break:break-all;}
.disclaimer{font-size:11px;color:var(--muted);margin-top:14px;max-width:900px;}

@media print{
  body{background:#fff;}
  .change-detail-row{display:table-row !important;}
  .exec-header{background:#0f1b2d !important;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
}
@media (max-width:900px){
  .kpi-row{grid-template-columns:repeat(2,1fr);}
  .meta-grid{grid-template-columns:1fr;}
  .commit-blocks{grid-template-columns:1fr;}
}
"""

_JS = """
function toggleRow(id){
  var el = document.getElementById(id);
  if(!el) return;
  el.classList.toggle('open');
}
"""
