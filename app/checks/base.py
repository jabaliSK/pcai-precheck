"""Common data model and subprocess helpers shared by all checks."""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import Any


# Result status values used across the UI, JSON API and PDF report.
STATUS_PASS = "pass"
STATUS_WARN = "warn"       # e.g. reachable only without SSL verification
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"


@dataclass
class CheckResult:
    """A single probe/test result."""
    category: str          # e.g. "Connectivity", "PyPI", "Hugging Face", "GitHub"
    name: str              # short label shown in the UI
    tool: str              # curl | wget | pip | git | huggingface_hub | ...
    target: str            # the URL / package / repo / model that was tested
    status: str            # STATUS_*
    detail: str            # short human-readable detail
    duration_ms: int = 0
    attempts: int = 1
    output: str = ""       # captured stdout/stderr (trimmed)
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trim(text: str, limit: int = 2000) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text) - limit} more bytes)"


def run_cmd(cmd: list[str], timeout: int = 30, env: dict[str, str] | None = None) -> tuple[int, str, str, int]:
    """Run a command, return (exit_code, stdout, stderr, elapsed_ms).

    Never raises on non-zero exits; timeouts return exit_code=124.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        rc = proc.returncode
        out = proc.stdout or ""
        err = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        rc = 124
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
        err = (err + f"\n[timeout after {timeout}s]").strip()
    except FileNotFoundError as exc:
        rc = 127
        out = ""
        err = f"command not found: {exc.filename}"
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out, err, elapsed_ms


def run_with_retries(
    cmd: list[str],
    retries: int,
    timeout: int,
    retry_delay: float = 2.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, int, int]:
    """Run cmd up to `retries` times. Returns (rc, stdout, stderr, elapsed_ms, attempts)."""
    attempts = 0
    rc, out, err, elapsed = 1, "", "", 0
    for attempts in range(1, retries + 1):
        rc, out, err, elapsed = run_cmd(cmd, timeout=timeout, env=env)
        if rc == 0:
            return rc, out, err, elapsed, attempts
        if attempts < retries:
            time.sleep(retry_delay)
    return rc, out, err, elapsed, attempts


def format_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def trimmed(text: str, limit: int = 2000) -> str:
    return _trim(text, limit)
