#!/usr/bin/env python3
"""Builds a persistent demo Git repository under example/demo-repo that
reproduces a realistic release/26.05 -> release/26.06 comparison, including
the FIX A / FIX B regression scenario from the brief. Used to generate the
example-report.html/.json shipped in this repo.
"""
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE / "demo-repo"


def run(*args, env=None):
    r = subprocess.run(["git", "-C", str(REPO)] + list(args), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


def write(relpath: str, content: str):
    p = REPO / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def commit(message: str, files: dict[str, str]) -> str:
    for relpath, content in files.items():
        write(relpath, content)
    run("add", "-A")
    run("commit", "-q", "-m", message, "--allow-empty")
    return run("rev-parse", "HEAD")


def main():
    if REPO.exists():
        shutil.rmtree(REPO)
    REPO.mkdir(parents=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "platform-eng@example.com")
    run("config", "user.name", "Platform Engineering")
    run("config", "commit.gpgsign", "false")
    run("remote", "add", "origin", "https://git.example.com/risk-platform/locate-service.git")

    commit("Initial import", {"README.md": "# risk-locate-platform\n"})
    base = commit("RISK-1000 Baseline risk engine release scaffold", {
        "src/core.py": "def core():\n    return 1\n",
        "pom.xml": "<version>26.04.0</version>\n",
    })

    # --- release/26.05 ---
    run("branch", "release/26.05", base)
    run("checkout", "-q", "release/26.05")
    fix_a = commit("RISK-1832 Fix locate validation", {
        "src/locate.py": "def validate(order):\n    if order.qty <= 0:\n        return False\n    return True\n",
    })
    fix_extra = commit("RISK-1871 Position cache correction", {
        "src/cache.py": "CACHE = {}\n\ndef get(key):\n    return CACHE.get(key)\n",
    })
    fix_b = commit("RISK-1942 Fix position validation", {
        "src/position.py": "def validate_position(pos):\n    if pos.qty is None:\n        return False\n    return True\n",
    })
    commit("RISK-1955 Minor logging cleanup in locate service", {
        "src/locate.py": "def validate(order):\n    if order.qty <= 0:\n        return False\n    return True\n\ndef log(msg):\n    print(msg)\n",
    })

    # --- release/26.06 branches from the same base, around the same time ---
    run("branch", "release/26.06", base)
    run("checkout", "-q", "release/26.06")
    fix_a_prime = run("cherry-pick", "-x", fix_a) or run("rev-parse", "HEAD")
    fix_a_prime = run("rev-parse", "HEAD")
    fix_extra_prime = None
    run("cherry-pick", "-x", fix_extra)
    fix_extra_prime = run("rev-parse", "HEAD")
    # NOTE: RISK-1942 (fix_b) and RISK-1955 are deliberately NOT carried
    # forward -- this is the regression-risk scenario the tool must surface.

    new_feature = commit("RISK-1903 Add new locate query handling", {
        "src/query.py": "def query(criteria):\n    return []\n",
    })
    commit("Release 26.06 version bump", {"pom.xml": "<version>26.06.0</version>\n"})
    risky_new = commit("RISK-1981 Add throttling to market data feed", {
        "src/throttle.py": "def throttle(rate):\n    pass\n",
    })
    # A genuine revert on the target release
    risky_change = commit("RISK-1990 Experimental order-routing change", {
        "src/routing.py": "def route(order):\n    return 'EXPERIMENTAL'\n",
    })
    run("revert", "--no-edit", risky_change)

    run("checkout", "-q", "main")

    print("Demo repo built at:", REPO)
    print("RISK-1832 (carried forward):", fix_a, "->", fix_a_prime)
    print("RISK-1871 (carried forward):", fix_extra, "->", fix_extra_prime)
    print("RISK-1942 (MISSING - regression risk):", fix_b)
    print("RISK-1903 (new in target):", new_feature)
    print("RISK-1981 (new in target):", risky_new)


if __name__ == "__main__":
    main()
