"""GitHub check: shallow-clone a tiny public repo to prove end-to-end access."""
from __future__ import annotations

import os
import shutil
import tempfile

from .base import CheckResult, STATUS_PASS, STATUS_FAIL, run_with_retries, trimmed

# The canonical tiniest public GitHub repo. Two files, ~1KB total.
DEFAULT_REPO = "https://github.com/octocat/Hello-World.git"


def run(retries: int, timeout: int, retry_delay: float,
        repo_url: str = DEFAULT_REPO) -> list[CheckResult]:
    tmp = tempfile.mkdtemp(prefix="pcai-git-")
    dest = os.path.join(tmp, "repo")
    try:
        cmd = ["git", "clone", "--depth", "1", "--quiet", repo_url, dest]
        rc, out, err, elapsed, attempts = run_with_retries(
            cmd,
            retries=retries,
            timeout=max(timeout * 3, 60),
            retry_delay=retry_delay,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        cloned_files: list[str] = []
        if os.path.isdir(dest):
            cloned_files = sorted(os.listdir(dest))
        ok = rc == 0 and bool(cloned_files)
        detail = (f"cloned {len(cloned_files)} entries from {repo_url}"
                  if ok else f"git rc={rc}")
        return [CheckResult(
            category="GitHub",
            name=f"git clone {repo_url}",
            tool="git",
            target=repo_url,
            status=STATUS_PASS if ok else STATUS_FAIL,
            detail=detail,
            duration_ms=elapsed,
            attempts=attempts,
            output=trimmed((out + "\n" + err).strip()),
        )]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
