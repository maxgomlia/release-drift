"""Deterministic JSON serialisation of a ReleaseComparison, for downstream
release gates, notifications, and dashboards."""
from __future__ import annotations

import json
from .models import ReleaseComparison


def render(comparison: ReleaseComparison) -> str:
    return json.dumps(comparison.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
