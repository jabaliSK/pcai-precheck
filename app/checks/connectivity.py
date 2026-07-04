"""Basic HTTPS connectivity check for a list of domains, using curl and wget,
with and without TLS verification, with retries."""
from __future__ import annotations

from typing import Iterable

from .base import (
    CheckResult, STATUS_PASS, STATUS_WARN, STATUS_FAIL,
    run_with_retries, trimmed,
)


def _curl_cmd(url: str, timeout: int, insecure: bool) -> list[str]:
    cmd = [
        "curl", "--silent", "--show-error", "--output", "/dev/null",
        "--location",
        "--connect-timeout", str(timeout),
        "--max-time", str(timeout),
        "--write-out", "http_%{http_code}",
    ]
    if insecure:
        cmd.append("--insecure")
    cmd.append(url)
    return cmd


def _wget_cmd(url: str, timeout: int, insecure: bool) -> list[str]:
    cmd = [
        "wget", "--quiet", "--tries=1", "--spider",
        f"--timeout={timeout}",
        f"--dns-timeout={timeout}",
        f"--connect-timeout={timeout}",
        f"--read-timeout={timeout}",
    ]
    if insecure:
        cmd.append("--no-check-certificate")
    cmd.append(url)
    return cmd


def _probe(tool: str, domain: str, port: int, insecure: bool,
           retries: int, timeout: int, retry_delay: float) -> CheckResult:
    url = f"https://{domain}:{port}/"
    if tool == "curl":
        cmd = _curl_cmd(url, timeout, insecure)
    elif tool == "wget":
        cmd = _wget_cmd(url, timeout, insecure)
    else:
        raise ValueError(f"unknown tool: {tool}")

    rc, out, err, elapsed_ms, attempts = run_with_retries(
        cmd, retries=retries, timeout=timeout, retry_delay=retry_delay
    )
    mode = "insecure" if insecure else "verify"
    ok = rc == 0
    if ok:
        detail = f"{tool} {mode}: OK ({out.strip() or 'ok'})"
    else:
        detail = f"{tool} {mode}: FAIL rc={rc}"
    return CheckResult(
        category="Connectivity",
        name=f"{tool} ({mode})",
        tool=tool,
        target=url,
        status=STATUS_PASS if ok else STATUS_FAIL,
        detail=detail,
        duration_ms=elapsed_ms,
        attempts=attempts,
        output=trimmed((out + "\n" + err).strip()),
    )


def check_domain(domain: str, port: int, retries: int, timeout: int,
                 retry_delay: float, fail_on_ssl: bool) -> list[CheckResult]:
    """Return a summary CheckResult (per-domain verdict) plus the four raw probes."""
    probes = [
        _probe("curl", domain, port, insecure=False, retries=retries, timeout=timeout, retry_delay=retry_delay),
        _probe("curl", domain, port, insecure=True,  retries=retries, timeout=timeout, retry_delay=retry_delay),
        _probe("wget", domain, port, insecure=False, retries=retries, timeout=timeout, retry_delay=retry_delay),
        _probe("wget", domain, port, insecure=True,  retries=retries, timeout=timeout, retry_delay=retry_delay),
    ]

    verify_ok = any(p.status == STATUS_PASS and "verify" in p.name for p in probes)
    insecure_ok = any(p.status == STATUS_PASS and "insecure" in p.name for p in probes)

    if verify_ok:
        summary_status = STATUS_PASS
        summary_detail = "Reachable with SSL verification."
    elif insecure_ok:
        summary_status = STATUS_FAIL if fail_on_ssl else STATUS_WARN
        summary_detail = ("Reachable only WITHOUT SSL verification -> "
                          "likely TLS interception or mis-issued certificate.")
    else:
        summary_status = STATUS_FAIL
        summary_detail = "Unreachable with both curl and wget (verify and insecure)."

    total_ms = sum(p.duration_ms for p in probes)
    max_attempts = max(p.attempts for p in probes)

    summary = CheckResult(
        category="Connectivity",
        name=domain,
        tool="curl+wget",
        target=f"https://{domain}:{port}/",
        status=summary_status,
        detail=summary_detail,
        duration_ms=total_ms,
        attempts=max_attempts,
        output="\n\n".join(f"[{p.name}] {p.detail}" for p in probes),
    )
    return [summary, *probes]


def run(domains: Iterable[str], port: int, retries: int, timeout: int,
        retry_delay: float, fail_on_ssl: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    for d in domains:
        results.extend(check_domain(
            d, port=port, retries=retries, timeout=timeout,
            retry_delay=retry_delay, fail_on_ssl=fail_on_ssl,
        ))
    return results
