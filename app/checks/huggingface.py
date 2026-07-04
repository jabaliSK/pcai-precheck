"""Hugging Face check: download a tiny model file via the huggingface_hub client."""
from __future__ import annotations

import os
import shutil
import tempfile
import time

from .base import CheckResult, STATUS_PASS, STATUS_FAIL, trimmed

# tiny-random-gpt2 is a purpose-built micro model used by HF for CI; its
# config.json is a few hundred bytes.
DEFAULT_REPO = "hf-internal-testing/tiny-random-gpt2"
DEFAULT_FILE = "config.json"


def _do_download(repo: str, filename: str, timeout: int) -> tuple[bool, str, str, int]:
    """Returns (ok, detail, output, elapsed_ms)."""
    started = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="pcai-hf-")
    # Constrain HF cache to our temp dir so we don't touch anything else.
    env_backup = {k: os.environ.get(k) for k in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE")}
    os.environ["HF_HOME"] = tmp
    os.environ["HF_HUB_CACHE"] = tmp
    os.environ["HUGGINGFACE_HUB_CACHE"] = tmp
    try:
        # Imported lazily so import errors are reported cleanly.
        from huggingface_hub import hf_hub_download  # type: ignore

        path = hf_hub_download(
            repo_id=repo,
            filename=filename,
            cache_dir=tmp,
            etag_timeout=timeout,
        )
        size = os.path.getsize(path) if os.path.exists(path) else 0
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return True, f"downloaded {filename} ({size} bytes)", f"path={path}", elapsed_ms
    except Exception as exc:  # noqa: BLE001 - report everything to the user
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return False, f"{type(exc).__name__}: {exc}", trimmed(repr(exc)), elapsed_ms
    finally:
        # Restore env, wipe temp cache.
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def run(retries: int, timeout: int, retry_delay: float,
        repo: str = DEFAULT_REPO, filename: str = DEFAULT_FILE) -> list[CheckResult]:
    ok = False
    detail = ""
    output = ""
    elapsed = 0
    attempts = 0
    for attempts in range(1, retries + 1):
        ok, detail, output, elapsed = _do_download(repo, filename, timeout)
        if ok:
            break
        if attempts < retries:
            time.sleep(retry_delay)

    return [CheckResult(
        category="Hugging Face",
        name=f"download {repo}/{filename}",
        tool="huggingface_hub",
        target=f"https://huggingface.co/{repo}",
        status=STATUS_PASS if ok else STATUS_FAIL,
        detail=detail,
        duration_ms=elapsed,
        attempts=attempts,
        output=output,
    )]
