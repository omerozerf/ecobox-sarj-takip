"""Where the remote collector's snapshot files come from.

The GitHub Actions collector commits `snapshots/` to a branch of a public repository. The
service keeps a shallow checkout of that branch under the gitignored `data/` directory and
refreshes it with the system `git` over anonymous https. Tests use `DirectorySource` instead.
"""

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

SNAPSHOT_SUBDIR = "snapshots"
GIT_TIMEOUT_S = 300.0

GitRunner = Callable[[list[str]], None]
"""Runs `git <args>`; raises on failure."""


class SnapshotSource(Protocol):
    def refresh(self) -> Path:
        """Bring the snapshot files up to date and return the directory holding them."""


class DirectorySource:
    """Snapshot files already on disk (tests, or a manual `--dir`)."""

    def __init__(self, directory: Path):
        self._directory = directory

    def refresh(self) -> Path:
        return self._directory


class GitSnapshotSource:
    """A shallow single-branch checkout, cloned on first use and fast-forwarded after.

    Fetching by URL (not by the `origin` remote) means a changed URL in `.env` takes effect
    on the next refresh. The checkout is owned by the importer: local edits there are
    discarded by the hard reset.
    """

    def __init__(self, url: str, branch: str, checkout: Path, git: GitRunner):
        self._url = url
        self._branch = branch
        self._checkout = checkout
        self._git = git

    def refresh(self) -> Path:
        if (self._checkout / ".git").exists():
            self._git(["-C", str(self._checkout), "fetch", "--quiet", "--depth", "1",
                       self._url, self._branch])
            self._git(["-C", str(self._checkout), "reset", "--quiet", "--hard", "FETCH_HEAD"])
        else:
            self._checkout.parent.mkdir(parents=True, exist_ok=True)
            self._git(["clone", "--quiet", "--depth", "1", "--single-branch",
                       "--branch", self._branch, self._url, str(self._checkout)])
        return self._checkout / SNAPSHOT_SUBDIR


def run_git(args: list[str], timeout_s: float = GIT_TIMEOUT_S) -> None:
    """The system git, never prompting: an anonymous fetch that needs credentials fails."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="")
    completed = subprocess.run(["git", *args], env=env, capture_output=True, text=True,
                               timeout=timeout_s, stdin=subprocess.DEVNULL)
    if completed.returncode != 0:
        raise GitError(f"git {args[0] if args[0] != '-C' else args[2]} failed "
                       f"({completed.returncode}): {completed.stderr.strip()[:500]}")


class GitError(RuntimeError):
    pass
