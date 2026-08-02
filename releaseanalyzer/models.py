"""Typed data structures for the Release Change & QA Sign-off Report.

These are intentionally plain dataclasses (no ORM / pydantic dependency)
so the tool has zero non-stdlib runtime requirements beyond PyYAML for the
sign-off sidecar. Everything here is JSON-serialisable via `asdict()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


TOOL_VERSION = "1.0.0"


class Classification(str, Enum):
    CARRIED_FORWARD = "CARRIED FORWARD"
    NEW_IN_TARGET = "NEW IN TARGET RELEASE"
    MISSING_FROM_TARGET = "MISSING FROM TARGET RELEASE"
    TARGET_ONLY = "TARGET-ONLY CHANGE"
    REVERTED = "REVERTED"
    NEEDS_REVIEW = "NEEDS REVIEW"
    NOT_APPLICABLE = "INTENTIONALLY NOT APPLICABLE"


class QaState(str, Enum):
    NOT_REVIEWED = "NOT REVIEWED"
    TESTED_PASS = "TESTED - PASS"
    TESTED_FAIL = "TESTED - FAIL"
    NOT_APPLICABLE = "NOT APPLICABLE"
    SIGNED_OFF = "ACCEPTED / SIGNED OFF"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class Commit:
    sha: str
    author_name: str
    author_email: str
    author_date: str          # ISO-8601, from Git object data
    committer_date: str
    subject: str
    body: str
    change_id: Optional[str] = None
    pr_reference: Optional[str] = None
    pr_title: Optional[str] = None       # populated only if GH Enterprise API enrichment is enabled
    pr_url: Optional[str] = None
    pr_state: Optional[str] = None       # open / closed / merged
    patch_id: Optional[str] = None
    is_merge: bool = False
    files_changed: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0

    @property
    def short_sha(self) -> str:
        return self.sha[:10]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["short_sha"] = self.short_sha
        return d


@dataclass
class QaStatus:
    status: QaState = QaState.NOT_REVIEWED
    reviewer: Optional[str] = None
    comments: Optional[str] = None
    evidence_ref: Optional[str] = None
    reviewed_at: Optional[str] = None
    source: str = "none"   # e.g. "sidecar:release-signoff-26.06.yaml"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ChangeEntry:
    change_id: str                       # Jira ID, or synthetic "COMMIT-<short_sha>"
    description: str
    change_type: str                     # Bug Fix / Feature / Chore / Unknown
    classification: Classification
    classification_confidence: Confidence
    classification_reason: str
    source_commit: Optional[Commit] = None
    target_commit: Optional[Commit] = None
    related_commits: list[Commit] = field(default_factory=list)
    merge_commit: bool = False
    qa: QaStatus = field(default_factory=QaStatus)

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "description": self.description,
            "change_type": self.change_type,
            "classification": self.classification.value,
            "classification_confidence": self.classification_confidence.value,
            "classification_reason": self.classification_reason,
            "source_commit": self.source_commit.to_dict() if self.source_commit else None,
            "target_commit": self.target_commit.to_dict() if self.target_commit else None,
            "related_commits": [c.to_dict() for c in self.related_commits],
            "merge_commit": self.merge_commit,
            "qa": self.qa.to_dict(),
        }


@dataclass
class RunMetadata:
    repository: str
    source_branch: str
    target_branch: str
    source_sha: str
    target_sha: str
    merge_base_sha: str
    merge_base_candidates: list[str]
    generated_at: str
    tool_version: str
    comparison_algorithm: str
    signoff_source: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SummaryCounts:
    total_changes: int = 0
    new_in_target: int = 0
    carried_forward: int = 0
    potentially_missing: int = 0
    target_only: int = 0
    reverted: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    qa_signed_off: int = 0
    qa_awaiting: int = 0
    qa_failed: int = 0
    qa_not_applicable: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReleaseComparison:
    metadata: RunMetadata
    changes: list[ChangeEntry]
    summary: SummaryCounts
    blockers: list[ChangeEntry]
    release_status: str   # READY FOR RELEASE / QA SIGN-OFF INCOMPLETE / RELEASE REVIEW REQUIRED

    def to_dict(self) -> dict:
        return {
            "metadata": self.metadata.to_dict(),
            "summary": self.summary.to_dict(),
            "release_status": self.release_status,
            "changes": [c.to_dict() for c in self.changes],
            "blockers": [c.to_dict() for c in self.blockers],
        }
