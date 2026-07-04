"""PyPI availability check: curl, wget and pip download of a tiny package."""
from __future__ import annotations

import os
import shutil
import tempfile

from .base import (
    CheckResult, STATUS_PASS, STATUS_FAIL,
    run_with_retries, trimmed,
)

INDEX_URL = "https://pypi.org/simple/"
TEST_PACKAGE = "six"     # tiny, pure-python, universal wheel, ~10KB


def _http_probe(tool: str, url: str, retries: int, timeout: int, retry_delay: float) -> CheckResult:
    if tool == "curl":
        cmd = [
            "curl", "--silent", "--show-error", "--output", "/dev/null",
            "--location",
            "--connect-timeout", str(timeout), "--max-time", str(timeout),
            "--fail",
            "--write-out", "http_%{http_code}",
            url,
        ]
    else:
        cmd = [
            "wget", "--quiet", "--tries=1", "--spider",
            f"--timeout={timeout}", url,
        ]
    rc, out, err, elapsed, attempts = run_with_retries(
        cmd, retries=retries, timeout=timeout, retry_delay=retry_delay,
    )
    ok = rc == 0
    return CheckResult(
        category="PyPI",
        name=f"{tool} index",
        tool=tool,
        target=url,
        status=STATUS_PASS if ok else STATUS_FAIL,
        detail=("index reachable" if ok else f"{tool} rc={rc}"),
        duration_ms=elapsed,
        attempts=attempts,
        output=trimmed((out + "\n" + err).strip()),
    )


def _pip_download(retries: int, timeout: int, retry_delay: float) -> CheckResult:
    tmp = tempfile.mkdtemp(prefix="pcai-pip-")
    try:
        cmd = [
            "pip", "download",
            "--no-deps",
            "--no-cache-dir",
            "--index-url", INDEX_URL,
            "--dest", tmp,
            "--timeout", str(timeout),
            TEST_PACKAGE,
        ]
        rc, out, err, elapsed, attempts = run_with_retries(
            cmd, retries=retries, timeout=max(timeout * 3, 60), retry_delay=retry_delay,
        )
        downloaded = os.listdir(tmp) if os.path.isdir(tmp) else []
        ok = rc == 0 and bool(downloaded)
        detail = (f"pip downloaded {TEST_PACKAGE} -> {downloaded[0]}"
                  if ok else f"pip rc={rc}, downloaded={downloaded}")
        return CheckResult(
            category="PyPI",
            name=f"pip download ({TEST_PACKAGE})",
            tool="pip",
            target=f"{INDEX_URL} :: {TEST_PACKAGE}",
            status=STATUS_PASS if ok else STATUS_FAIL,
            detail=detail,
            duration_ms=elapsed,
            attempts=attempts,
            output=trimmed((out + "\n" + err).strip()),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(retries: int, timeout: int, retry_delay: float) -> list[CheckResult]:
    return [
        _http_probe("curl", INDEX_URL, retries, timeout, retry_delay),
        _http_probe("wget", INDEX_URL, retries, timeout, retry_delay),
        _pip_download(retries, timeout, retry_delay),
    ]
