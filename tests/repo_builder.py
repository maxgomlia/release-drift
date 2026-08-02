"""Helper for building throwaway Git repositories in tests."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class RepoBuilder:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name
        self._run(["init", "-q", "-b", "main"])
        self._run(["config", "user.email", "test@example.com"])
        self._run(["config", "user.name", "Test User"])
        self._run(["config", "commit.gpgsign", "false"])

    def _run(self, args, env=None):
        r = subprocess.run(
            ["git", "-C", self.path] + args,
            capture_output=True, text=True, env=env,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {args} failed: {r.stderr}")
        return r.stdout

    def write(self, relpath: str, content: str):
        p = Path(self.path) / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def commit(self, message: str, files: dict[str, str] | None = None, date: str | None = None) -> str:
        if files:
            for relpath, content in files.items():
                self.write(relpath, content)
        self._run(["add", "-A"])
        env = None
        args = ["commit", "-q", "-m", message, "--allow-empty"]
        if date:
            args += [f"--date={date}"]
            import os
            env = dict(**{**_os_environ(), "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
        self._run(args, env=env)
        return self.rev_parse("HEAD")

    def branch(self, name: str, start_point: str = "HEAD"):
        self._run(["branch", name, start_point])

    def checkout(self, ref: str):
        self._run(["checkout", "-q", ref])

    def cherry_pick(self, sha: str) -> str:
        # -x appends "(cherry picked from commit <sha>)" to the message and
        # is standard practice for traceable cherry-picks; it also guarantees
        # a distinct commit object even when tree/parent/author/committer
        # timestamps would otherwise collide within the same test run.
        self._run(["cherry-pick", "-x", sha])
        return self.rev_parse("HEAD")

    def rev_parse(self, ref: str) -> str:
        return self._run(["rev-parse", ref]).strip()

    def cleanup(self):
        self._tmp.cleanup()


def _os_environ():
    import os
    return dict(os.environ)
