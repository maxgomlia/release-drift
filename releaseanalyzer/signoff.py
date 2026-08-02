"""QA sign-off sidecar loading and application.

Sign-off state is supplied externally via a YAML (or JSON) file, matched to
ChangeEntry rows by `change_id` (preferred) or `target_sha` / `source_sha`
(fallback, for changes without an extractable Jira ID).

This file is explicitly NOT the tool's source of truth long-term -- see
README "Auditability & Persistence" -- but is the pragmatic v1 mechanism
requested. Nothing here writes to the sidecar; it is loaded read-only, and
its content hash is recorded in report metadata so a generated report is
traceable back to the exact sign-off input that produced it.

Example sidecar (release-signoff-26.06.yaml):

    release: release/26.06
    reviewer_default: qa-team@example.com
    entries:
      - change_id: RISK-1832
        status: SIGNED_OFF
        reviewer: jane.doe@example.com
        comments: "Verified in UAT, regression pack green."
        evidence_ref: "https://testrail.example.com/runs/4821"
        reviewed_at: "2026-07-30T10:15:00Z"
      - target_sha: 9f3a1c2
        status: TESTED_FAIL
        reviewer: john.smith@example.com
        comments: "Locate validation still rejects valid GC trades."
        evidence_ref: "DEFECT-5521"
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from .models import ChangeEntry, QaState, QaStatus

logger = logging.getLogger("releaseanalyzer.signoff")

_STATUS_ALIASES = {
    "NOT_REVIEWED": QaState.NOT_REVIEWED,
    "NOT REVIEWED": QaState.NOT_REVIEWED,
    "TESTED_PASS": QaState.TESTED_PASS,
    "TESTED - PASS": QaState.TESTED_PASS,
    "PASS": QaState.TESTED_PASS,
    "TESTED_FAIL": QaState.TESTED_FAIL,
    "TESTED - FAIL": QaState.TESTED_FAIL,
    "FAIL": QaState.TESTED_FAIL,
    "NOT_APPLICABLE": QaState.NOT_APPLICABLE,
    "NOT APPLICABLE": QaState.NOT_APPLICABLE,
    "N/A": QaState.NOT_APPLICABLE,
    "SIGNED_OFF": QaState.SIGNED_OFF,
    "ACCEPTED": QaState.SIGNED_OFF,
    "ACCEPTED / SIGNED OFF": QaState.SIGNED_OFF,
    "SIGNED OFF": QaState.SIGNED_OFF,
}


class SignoffError(RuntimeError):
    pass


def load_sidecar(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise SignoffError(f"Sign-off sidecar not found: {path}")
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict) or "entries" not in data:
        raise SignoffError(
            f"Sign-off sidecar {path} must be a mapping with an 'entries' list"
        )
    return data


def sidecar_fingerprint(path: str) -> str:
    """Short content hash so a report's metadata can prove exactly which
    version of the sign-off file it was generated from."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digest[:16]


def apply_signoff(changes: list[ChangeEntry], sidecar_path: str) -> str:
    """Mutates `changes` in place with QA status from the sidecar.

    Returns a descriptive `signoff_source` string for report metadata,
    e.g. "sidecar:release-signoff-26.06.yaml@a1b2c3d4e5f6a7b8".
    """
    data = load_sidecar(sidecar_path)
    entries = data.get("entries") or []

    by_change_id = {c.change_id: c for c in changes}
    by_source_sha = {c.source_commit.sha: c for c in changes if c.source_commit}
    by_target_sha = {c.target_commit.sha: c for c in changes if c.target_commit}

    matched = 0
    unmatched: list[str] = []

    for raw_entry in entries:
        change_id = raw_entry.get("change_id")
        source_sha = raw_entry.get("source_sha")
        target_sha = raw_entry.get("target_sha")

        entry: ChangeEntry | None = None
        if change_id and change_id in by_change_id:
            entry = by_change_id[change_id]
        elif target_sha:
            entry = _match_prefix(by_target_sha, target_sha)
        elif source_sha:
            entry = _match_prefix(by_source_sha, source_sha)

        if entry is None:
            identifier = change_id or target_sha or source_sha or "<unspecified>"
            unmatched.append(str(identifier))
            continue

        status_raw = str(raw_entry.get("status", "NOT_REVIEWED")).strip()
        status = _STATUS_ALIASES.get(status_raw.upper().replace("-", "_"), None)
        if status is None:
            status = _STATUS_ALIASES.get(status_raw, QaState.NOT_REVIEWED)

        entry.qa = QaStatus(
            status=status,
            reviewer=raw_entry.get("reviewer") or data.get("reviewer_default"),
            comments=raw_entry.get("comments"),
            evidence_ref=raw_entry.get("evidence_ref"),
            reviewed_at=raw_entry.get("reviewed_at"),
            source=f"sidecar:{Path(sidecar_path).name}",
        )
        matched += 1

    if unmatched:
        logger.warning(
            "Sign-off sidecar had %d entr%s that did not match any change "
            "in this comparison (stale entries or wrong release?): %s",
            len(unmatched), "y" if len(unmatched) == 1 else "ies", unmatched,
        )
    logger.info("Applied QA sign-off for %d/%d changes from %s", matched, len(changes), sidecar_path)

    fingerprint = sidecar_fingerprint(sidecar_path)
    return f"sidecar:{Path(sidecar_path).name}@{fingerprint}"


def _match_prefix(sha_map: dict[str, ChangeEntry], prefix: str) -> ChangeEntry | None:
    prefix = prefix.strip()
    if prefix in sha_map:
        return sha_map[prefix]
    matches = [entry for sha, entry in sha_map.items() if sha.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning("Ambiguous SHA prefix '%s' matched %d changes; skipping", prefix, len(matches))
    return None
