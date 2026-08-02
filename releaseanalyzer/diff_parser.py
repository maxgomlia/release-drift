"""Parses unified diff text (as produced by `git diff`) into structured
per-file, per-hunk, per-line data -- the input a GitHub-style colorized
diff view needs, rather than a flat block of preformatted text.

Pure stdlib, no dependencies. Deliberately tolerant: unrecognized lines are
skipped rather than raising, since this only feeds a "for your convenience"
view in the report, never the classification algorithm itself.
"""
from __future__ import annotations

import re

from .models import DiffFile, DiffHunk, DiffLine

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


def parse_unified_diff(diff_text: str, max_lines_per_file: int = 200) -> list[DiffFile]:
    """Parse `git diff` output into a list of DiffFile.

    Known limitation: renames are not specially detected (a renamed file
    will show under its post-rename path with the diff Git provides, which
    for a pure rename is typically empty or minimal -- this is a display
    limitation only, not a classification issue, since classification never
    depends on this parser).
    """
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None
    old_lineno = new_lineno = 0
    lines_in_file = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = DiffFile(path="")
            files.append(current_file)
            current_hunk = None
            lines_in_file = 0
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            current_file.is_new = True
            continue
        if line.startswith("deleted file mode"):
            current_file.is_deleted = True
            continue
        if line.startswith("Binary files") or line.startswith("GIT binary patch"):
            current_file.is_binary = True
            continue
        if line.startswith("--- "):
            path = line[4:].strip()
            if path != "/dev/null" and not current_file.path:
                current_file.path = _strip_ab_prefix(path)
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                current_file.path = _strip_ab_prefix(path)
            continue

        m = _HUNK_HEADER_RE.match(line)
        if m:
            old_lineno = int(m.group(1))
            new_lineno = int(m.group(2))
            current_hunk = DiffHunk(header=line)
            current_file.hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if lines_in_file >= max_lines_per_file:
            current_file.truncated = True
            continue

        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        elif line.startswith("+"):
            current_hunk.lines.append(DiffLine("add", line[1:], None, new_lineno))
            new_lineno += 1
        elif line.startswith("-"):
            current_hunk.lines.append(DiffLine("remove", line[1:], old_lineno, None))
            old_lineno += 1
        elif line.startswith(" "):
            current_hunk.lines.append(DiffLine("context", line[1:], old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1
        else:
            continue
        lines_in_file += 1

    return files


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
