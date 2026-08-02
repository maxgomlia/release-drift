"""Edge-case tests beyond the core FIX A / FIX B scenario."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from releaseanalyzer import analyzer, github_api
from releaseanalyzer.models import Classification
from tests.repo_builder import RepoBuilder


class TestMergeCommitWithRealDiff(unittest.TestCase):
    """A merge commit that itself introduces a diff vs its first parent
    (e.g. a squash-style merge) must still be captured as a change, not
    silently dropped."""

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-5000 base", {"b.txt": "1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.branch("release/26.06", base)

        self.repo.checkout("release/26.06")
        self.repo._run(["checkout", "-q", "-b", "feature/x"])
        self.repo.commit("RISK-5010 feature work", {"feature.txt": "new\n"})
        self.repo.checkout("release/26.06")
        self.repo._run(["merge", "--no-ff", "-m", "Merge feature/x into release/26.06", "feature/x"])
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_merge_commit_change_is_visible(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        change_ids = [c.change_id for c in comparison.changes]
        self.assertIn("RISK-5010", change_ids)


class TestReworkedFixDowngradesToNeedsReview(unittest.TestCase):
    """Same Jira ID on both sides but a genuinely different diff (e.g. the
    fix was reimplemented, not cherry-picked) should NOT be silently
    reported as MISSING -- it's downgraded to NEEDS REVIEW with a note."""

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-6000 base", {"b.txt": "1\n"})

        self.repo.branch("release/26.05", base)
        self.repo.checkout("release/26.05")
        self.repo.commit("RISK-6010 Fix throttle bug", {"throttle.py": "def throttle():\n    return 1\n"})

        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        # Same change id, deliberately different implementation/diff.
        self.repo.commit("RISK-6010 Fix throttle bug", {"throttle.py": "def throttle():\n    return 2  # reworked\n"})
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_reworked_fix_is_needs_review_not_missing(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        entry = next(c for c in comparison.changes if c.change_id == "RISK-6010")
        self.assertEqual(entry.classification, Classification.NEEDS_REVIEW)
        self.assertIsNotNone(entry.source_commit)
        self.assertIsNotNone(entry.target_commit)


class TestGithubApiHelpers(unittest.TestCase):
    def test_owner_repo_from_ssh_remote(self):
        result = github_api.owner_repo_from_remote("git@ghe.example.com:risk-platform/locate-service.git")
        self.assertEqual(result, ("risk-platform", "locate-service"))

    def test_owner_repo_from_https_remote(self):
        result = github_api.owner_repo_from_remote("https://ghe.example.com/risk-platform/locate-service")
        self.assertEqual(result, ("risk-platform", "locate-service"))

    def test_owner_repo_from_local_path_returns_none_or_best_effort(self):
        # A bare local filesystem path has no owner/repo structure to extract
        # reliably; this should not raise.
        github_api.owner_repo_from_remote("/var/repos/some-local-checkout")


if __name__ == "__main__":
    unittest.main()
