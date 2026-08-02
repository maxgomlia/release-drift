"""End-to-end tests against real, throwaway Git repositories.

These reproduce the exact failure scenario from the brief:

    main
      +-- release/26.05
      |      +-- FIX A
      |      +-- FIX B
      +-- release/26.06
             +-- FIX A'   (cherry-pick of FIX A; FIX B never carried over)

The tool must report FIX A as CARRIED FORWARD (different SHA, same patch)
and FIX B as MISSING FROM TARGET RELEASE (regression risk).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from releaseanalyzer import analyzer
from releaseanalyzer.models import Classification
from tests.repo_builder import RepoBuilder


class TestFixAFixBScenario(unittest.TestCase):
    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial commit", {"app.py": "print('hello')\n"})
        self.base = self.repo.commit("RISK-1000 baseline release code", {"core.py": "def core():\n    return 1\n"})

        self.repo.branch("release/26.05", self.base)
        self.repo.checkout("release/26.05")
        self.fix_a = self.repo.commit(
            "RISK-1832 Fix locate validation",
            {"locate.py": "def validate():\n    return True\n"},
        )
        self.fix_b = self.repo.commit(
            "RISK-1942 Fix position validation",
            {"position.py": "def validate_position():\n    return True\n"},
        )

        self.repo.branch("release/26.06", self.base)
        self.repo.checkout("release/26.06")
        self.fix_a_prime = self.repo.cherry_pick(self.fix_a)
        # FIX B intentionally NOT cherry-picked.

        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_fix_a_carried_forward_despite_different_sha(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)

        fix_a_entry = next(c for c in comparison.changes if c.change_id == "RISK-1832")
        self.assertEqual(fix_a_entry.classification, Classification.CARRIED_FORWARD)
        self.assertIsNotNone(fix_a_entry.source_commit)
        self.assertIsNotNone(fix_a_entry.target_commit)
        self.assertEqual(fix_a_entry.source_commit.sha, self.fix_a)
        self.assertEqual(fix_a_entry.target_commit.sha, self.fix_a_prime)
        self.assertNotEqual(fix_a_entry.source_commit.sha, fix_a_entry.target_commit.sha)

    def test_fix_b_flagged_as_missing_regression_risk(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)

        fix_b_entry = next(c for c in comparison.changes if c.change_id == "RISK-1942")
        self.assertEqual(fix_b_entry.classification, Classification.MISSING_FROM_TARGET)
        self.assertIsNotNone(fix_b_entry.source_commit)
        self.assertIsNone(fix_b_entry.target_commit)
        self.assertIn(fix_b_entry, comparison.blockers)

    def test_summary_and_release_status(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        self.assertEqual(comparison.summary.total_changes, 2)
        self.assertEqual(comparison.summary.carried_forward, 1)
        self.assertEqual(comparison.summary.potentially_missing, 1)
        # No sign-off applied and a missing change exists -> review required.
        self.assertEqual(comparison.release_status, "RELEASE REVIEW REQUIRED")


class TestNewChangeInTarget(unittest.TestCase):
    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial commit", {"app.py": "print('hello')\n"})
        self.base = self.repo.commit("RISK-1000 baseline", {"core.py": "x = 1\n"})
        self.repo.branch("release/26.05", self.base)
        self.repo.branch("release/26.06", self.base)
        self.repo.checkout("release/26.06")
        self.new_commit = self.repo.commit(
            "RISK-1903 Add new locate query handling",
            {"query.py": "def query():\n    return []\n"},
        )
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_new_change_classified_correctly(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        entry = next(c for c in comparison.changes if c.change_id == "RISK-1903")
        self.assertEqual(entry.classification, Classification.NEW_IN_TARGET)
        self.assertEqual(entry.target_commit.sha, self.new_commit)
        self.assertIsNone(entry.source_commit)


class TestFastForwardNoDivergence(unittest.TestCase):
    """Target is a strict superset of source -- no missing changes possible."""

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-2000 base", {"b.txt": "1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        self.repo.commit("RISK-2001 extra work only on target", {"c.txt": "1\n"})
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_no_missing_changes_reported(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        self.assertEqual(comparison.summary.potentially_missing, 0)


class TestRevertDetection(unittest.TestCase):
    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-3000 base", {"b.txt": "1\n"})
        self.repo.branch("release/26.05", base)

        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        risky = self.repo.commit("RISK-3010 risky change", {"risky.txt": "danger\n"})
        # Build a real revert commit via git revert so message format matches Git's own convention.
        self.repo._run(["revert", "--no-edit", risky])
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_revert_is_classified(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        classifications = [c.classification for c in comparison.changes]
        self.assertIn(Classification.REVERTED, classifications)


class TestQaSignoffApplication(unittest.TestCase):
    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-4000 base", {"b.txt": "1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        self.repo.commit("RISK-4010 signed off change", {"x.txt": "1\n"})
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_signoff_sidecar_applied(self):
        import tempfile, textwrap
        from releaseanalyzer import signoff

        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(textwrap.dedent("""
                release: release/26.06
                entries:
                  - change_id: RISK-4010
                    status: SIGNED_OFF
                    reviewer: qa.lead@example.com
                    comments: "Verified in regression pack."
                    evidence_ref: "TR-1"
            """))
            sidecar_path = f.name

        source = signoff.apply_signoff(comparison.changes, sidecar_path)
        analyzer.summarize(comparison)

        entry = next(c for c in comparison.changes if c.change_id == "RISK-4010")
        from releaseanalyzer.models import QaState
        self.assertEqual(entry.qa.status, QaState.SIGNED_OFF)
        self.assertEqual(entry.qa.reviewer, "qa.lead@example.com")
        self.assertTrue(source.startswith("sidecar:"))
        self.assertEqual(comparison.summary.qa_signed_off, 1)
        self.assertEqual(comparison.release_status, "READY FOR RELEASE")


if __name__ == "__main__":
    unittest.main()
