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
        self.assertIn(fix_b_entry, comparison.attention_items)

    def test_summary_and_release_status(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        self.assertEqual(comparison.summary.total_changes, 2)
        self.assertEqual(comparison.summary.carried_forward, 1)
        self.assertEqual(comparison.summary.potentially_missing, 1)
        # A missing change exists -> review required.
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


class TestAttentionItemsAggregation(unittest.TestCase):
    """attention_items should collect exactly the MISSING and NEEDS_REVIEW
    changes -- the items someone should actually look at before releasing."""

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-4000 base", {"b.txt": "1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.checkout("release/26.05")
        self.missed_fix = self.repo.commit("RISK-4010 Fix not carried over", {"x.txt": "1\n"})
        self.repo.branch("release/26.06", base)
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_missing_change_appears_in_attention_items(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        attention_ids = [c.change_id for c in comparison.attention_items]
        self.assertIn("RISK-4010", attention_ids)
        self.assertEqual(comparison.release_status, "RELEASE REVIEW REQUIRED")


class TestManualPortingDetection(unittest.TestCase):
    """Simulates a human manually re-typing a fix (not cherry-picking it)
    instead of using `git cherry-pick`. Two cases:
      1. The manual port produces an identical diff -> still CARRIED FORWARD,
         because classification is diff-content-based, not method-based.
      2. The manual port is incomplete/different -> MISSING, but with a
         related_commits hint pointing at the target commit that touched
         the same file, since it wasn't ported via cherry-pick at all.
    """

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-7000 base", {"shared.py": "def shared():\n    return 1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.checkout("release/26.05")
        self.fix = self.repo.commit(
            "RISK-7010 Fix shared validation",
            {"shared.py": "def shared():\n    if True:\n        return 1\n    return 0\n"},
        )
        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        # Manually re-type a DIFFERENT change touching the same file --
        # simulates someone porting by hand but doing it incompletely.
        self.manual_partial = self.repo.commit(
            "RISK-7010 partial manual port",
            {"shared.py": "def shared():\n    if True:\n        return 1\n"},
        )
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_incomplete_manual_port_flagged_with_related_commit_hint(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        # Since RISK-7010 appears on both sides with a different diff, this
        # should downgrade to NEEDS_REVIEW (not silently pass as carried).
        entry = next(c for c in comparison.changes if c.change_id == "RISK-7010" and c.source_commit)
        self.assertEqual(entry.classification, Classification.NEEDS_REVIEW)
        self.assertIsNotNone(entry.source_commit.diff_text)


class TestFileOverlapHintWithoutChangeIdCorrelation(unittest.TestCase):
    """When the manually-ported commit has NO matching change id (a common
    real-world case -- someone just fixes the file without referencing the
    ticket), the change-id correlation can't help. The file-overlap hint on
    `related_commits` is the safety net that still surfaces it."""

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        base = self.repo.commit("RISK-8000 base", {"shared.py": "def shared():\n    return 1\n"})
        self.repo.branch("release/26.05", base)
        self.repo.checkout("release/26.05")
        self.fix = self.repo.commit(
            "RISK-8010 Fix shared validation",
            {"shared.py": "def shared():\n    if True:\n        return 1\n    return 0\n"},
        )
        self.repo.branch("release/26.06", base)
        self.repo.checkout("release/26.06")
        # No change id in this message at all -- e.g. someone just edited
        # the file directly without referencing the ticket.
        self.manual_no_id = self.repo.commit(
            "quick fix to shared logic",
            {"shared.py": "def shared():\n    return 2\n"},
        )
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_missing_entry_gets_related_commit_hint_via_file_overlap(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)
        entry = next(c for c in comparison.changes if c.change_id == "RISK-8010")
        self.assertEqual(entry.classification, Classification.MISSING_FROM_TARGET)
        self.assertTrue(len(entry.related_commits) >= 1)
        self.assertEqual(entry.related_commits[0].sha, self.manual_no_id)


class TestSharedHistoryFixCaughtByFullFileHistorySearch(unittest.TestCase):
    """Reproduces the real-world scenario reported: an equivalent fix
    already exists in the *shared* ancestry both branches inherit (i.e. it
    predates the computed merge-base), so it's invisible to a target-unique
    search -- but a source-side branch also carries its own extra commit
    touching the same file, with no target-unique equivalent. The
    target-unique-only `related_commits` hint correctly finds nothing;
    `target_file_history` (full branch history, not divergence-limited)
    is the complementary signal that still surfaces the earlier fix.
    """

    def setUp(self):
        self.repo = RepoBuilder()
        self.repo.commit("Initial", {"a.txt": "1\n"})
        self.repo.commit("BASE naive shared.py", {"shared.py": "def f():\n    return 0\n"})
        self.main_fix = self.repo.commit(
            "Properly fix shared logic on main", {"shared.py": "def f():\n    return 1\n"},
        )
        # Both branches fork AFTER the fix -- it's shared/common ancestry.
        self.repo.branch("release/26.05", self.main_fix)
        self.repo.branch("release/26.06", self.main_fix)

        self.repo.checkout("release/26.05")
        self.hotfix = self.repo.commit(
            "RISK-9010 Extra hotfix on top", {"shared.py": "def f():\n    return 1  # extra tweak\n"},
        )
        self.repo.checkout("main")

    def tearDown(self):
        self.repo.cleanup()

    def test_target_file_history_finds_fix_that_predates_merge_base(self):
        comparison = analyzer.analyze(
            self.repo.path, "release/26.05", "release/26.06", fetch=False,
        )
        analyzer.summarize(comparison)

        entry = next(c for c in comparison.changes if c.change_id == "RISK-9010")
        self.assertEqual(entry.classification, Classification.MISSING_FROM_TARGET)
        # related_commits (target-unique only) correctly finds nothing --
        # the fix isn't a target-unique commit, it's shared history.
        self.assertEqual(entry.related_commits, [])
        # target_file_history (full branch history) DOES find it.
        history_shas = {h.sha for h in entry.target_file_history}
        self.assertIn(self.main_fix, history_shas)


if __name__ == "__main__":
    unittest.main()
