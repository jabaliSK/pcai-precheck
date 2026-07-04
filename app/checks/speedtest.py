"""Network speed test: measures download and upload throughput plus latency.

Uses Cloudflare's public speed test endpoint (``speed.cloudflare.com``) by
default because it is HTTPS-only, unauthenticated, and stable. Every
parameter can be overridden via environment variables so restricted
environments can point at an internal mirror.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import CheckResult, STATUS_PASS, STATUS_FAIL, STATUS_WARN, trimmed


# Cloudflare exposes a well-known speed test worker:
#   GET  /__down?bytes=N   -> returns N bytes of zeros
#   POST /__up             -> discards the request body
DEFAULT_URL = "https://speed.cloudflare.com"
DEFAULT_DOWNLOAD_BYTES = 25 * 1024 * 1024   # 25 MiB
DEFAULT_UPLOAD_BYTES = 10 * 1024 * 1024     # 10 MiB
# Warn (but don't fail) if throughput drops below this (Mbps).
DEFAULT_MIN_MBPS = 5.0


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n} B"


def _fmt_mbps(mbps: float) -> str:
    return f"{mbps:.2f} Mbps"


def _measure_download(url: str, size_bytes: int, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    received = 0
    err: str | None = None
    status_code: int | None = None
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            status_code = resp.status_code
            resp.raise_for_status()
            # Read in reasonably large chunks; count bytes actually delivered
            # instead of trusting Content-Length in case a proxy re-encodes.
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    received += len(chunk)
    except requests.RequestException as exc:
        err = f"{type(exc).__name__}: {exc}"
    elapsed = max(time.monotonic() - started, 1e-6)
    mbps = (received * 8) / (elapsed * 1_000_000) if received else 0.0
    return {
        "elapsed_s": elapsed,
        "bytes": received,
        "expected_bytes": size_bytes,
        "mbps": mbps,
        "status_code": status_code,
        "error": err,
    }


def _measure_upload(url: str, size_bytes: int, timeout: int) -> dict[str, Any]:
    # Random-ish but incompressible-enough payload: use os.urandom so any
    # transparent compression in a proxy can't shrink it.
    payload = os.urandom(size_bytes)
    started = time.monotonic()
    err: str | None = None
    status_code: int | None = None
    try:
        resp = requests.post(
            url,
            data=payload,
            timeout=timeout,
            headers={"Content-Type": "application/octet-stream"},
        )
        status_code = resp.status_code
        resp.raise_for_status()
    except requests.RequestException as exc:
        err = f"{type(exc).__name__}: {exc}"
    elapsed = max(time.monotonic() - started, 1e-6)
    mbps = (size_bytes * 8) / (elapsed * 1_000_000) if err is None else 0.0
    return {
        "elapsed_s": elapsed,
        "bytes": size_bytes,
        "mbps": mbps,
        "status_code": status_code,
        "error": err,
    }


def _measure_latency(url: str, timeout: int, samples: int = 4) -> dict[str, Any]:
    # Use a 0-byte download as a cheap RTT probe. Skip the first sample to
    # avoid TLS/DNS warmup noise.
    latencies: list[float] = []
    err: str | None = None
    status_code: int | None = None
    try:
        for i in range(samples):
            start = time.monotonic()
            resp = requests.get(url, timeout=timeout)
            status_code = resp.status_code
            resp.raise_for_status()
            latencies.append((time.monotonic() - start) * 1000)
    except requests.RequestException as exc:
        err = f"{type(exc).__name__}: {exc}"
    effective = latencies[1:] if len(latencies) > 1 else latencies
    avg = sum(effective) / len(effective) if effective else 0.0
    return {
        "samples": latencies,
        "avg_ms": avg,
        "status_code": status_code,
        "error": err,
    }


def _download_result(res: dict[str, Any], url: str,
                     min_mbps: float, requested_bytes: int) -> CheckResult:
    elapsed_ms = int(res["elapsed_s"] * 1000)
    if res["error"]:
        return CheckResult(
            category="Network Speed",
            name="download throughput",
            tool="requests",
            target=url,
            status=STATUS_FAIL,
            detail=res["error"],
            duration_ms=elapsed_ms,
            attempts=1,
            output=trimmed(f"status={res['status_code']} bytes={res['bytes']}"),
        )
    mbps = res["mbps"]
    if mbps < min_mbps:
        status = STATUS_WARN
        detail = f"{_fmt_mbps(mbps)} (below {_fmt_mbps(min_mbps)} threshold)"
    else:
        status = STATUS_PASS
        detail = _fmt_mbps(mbps)
    return CheckResult(
        category="Network Speed",
        name="download throughput",
        tool="requests",
        target=url,
        status=status,
        detail=f"{detail}, {_fmt_bytes(res['bytes'])} in {res['elapsed_s']:.2f}s",
        duration_ms=elapsed_ms,
        attempts=1,
        output=trimmed(
            f"requested={_fmt_bytes(requested_bytes)} "
            f"received={_fmt_bytes(res['bytes'])} "
            f"elapsed={res['elapsed_s']:.3f}s "
            f"mbps={mbps:.3f} "
            f"http={res['status_code']}"
        ),
    )


def _upload_result(res: dict[str, Any], url: str, min_mbps: float) -> CheckResult:
    elapsed_ms = int(res["elapsed_s"] * 1000)
    if res["error"]:
        return CheckResult(
            category="Network Speed",
            name="upload throughput",
            tool="requests",
            target=url,
            status=STATUS_FAIL,
            detail=res["error"],
            duration_ms=elapsed_ms,
            attempts=1,
            output=trimmed(f"status={res['status_code']} bytes={res['bytes']}"),
        )
    mbps = res["mbps"]
    if mbps < min_mbps:
        status = STATUS_WARN
        detail = f"{_fmt_mbps(mbps)} (below {_fmt_mbps(min_mbps)} threshold)"
    else:
        status = STATUS_PASS
        detail = _fmt_mbps(mbps)
    return CheckResult(
        category="Network Speed",
        name="upload throughput",
        tool="requests",
        target=url,
        status=status,
        detail=f"{detail}, {_fmt_bytes(res['bytes'])} in {res['elapsed_s']:.2f}s",
        duration_ms=elapsed_ms,
        attempts=1,
        output=trimmed(
            f"sent={_fmt_bytes(res['bytes'])} "
            f"elapsed={res['elapsed_s']:.3f}s "
            f"mbps={mbps:.3f} "
            f"http={res['status_code']}"
        ),
    )


def _latency_result(res: dict[str, Any], url: str) -> CheckResult:
    if res["error"]:
        return CheckResult(
            category="Network Speed",
            name="latency (RTT)",
            tool="requests",
            target=url,
            status=STATUS_FAIL,
            detail=res["error"],
            duration_ms=0,
            attempts=len(res["samples"]) or 1,
            output=trimmed(f"status={res['status_code']}"),
        )
    samples = res["samples"]
    avg = res["avg_ms"]
    detail = f"avg {avg:.1f} ms over {len(samples)} probes"
    return CheckResult(
        category="Network Speed",
        name="latency (RTT)",
        tool="requests",
        target=url,
        status=STATUS_PASS,
        detail=detail,
        duration_ms=int(avg),
        attempts=len(samples),
        output=trimmed(
            "samples_ms=[" + ", ".join(f"{s:.1f}" for s in samples) + "]"
        ),
    )


def run(
    retries: int,
    timeout: int,
    retry_delay: float,
    base_url: str = DEFAULT_URL,
    download_bytes: int = DEFAULT_DOWNLOAD_BYTES,
    upload_bytes: int = DEFAULT_UPLOAD_BYTES,
    min_mbps: float = DEFAULT_MIN_MBPS,
) -> list[CheckResult]:
    # Cloudflare's endpoints (or an internal mirror that exposes the same
    # paths). Users can override via env vars in the orchestrator.
    base = base_url.rstrip("/")
    down_url = f"{base}/__down?bytes={download_bytes}"
    up_url = f"{base}/__up"
    # The 0-byte download is the cheapest well-defined endpoint for RTT.
    latency_url = f"{base}/__down?bytes=0"

    # Bound each phase's timeout to something proportional to the payload so
    # slow-but-working links still complete on best-effort. Minimum stays at
    # the configured `timeout` for the connect phase.
    dl_timeout = max(timeout, 30)
    ul_timeout = max(timeout, 30)

    # Latency first so warmup happens before we measure throughput.
    latency = _measure_latency(latency_url, timeout=timeout)
    download = _measure_download(down_url, download_bytes, timeout=dl_timeout)
    upload = _measure_upload(up_url, upload_bytes, timeout=ul_timeout)

    return [
        _latency_result(latency, latency_url),
        _download_result(download, down_url, min_mbps, download_bytes),
        _upload_result(upload, up_url, min_mbps),
    ]
