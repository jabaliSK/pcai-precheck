"""Orchestrates every pre-check and exposes the aggregated state.

The orchestrator runs in a background thread so the web UI can render live
progress while checks are still running. Individual checks run in parallel
via a thread pool. Results are stored in-memory and served by the Flask app.
"""
from __future__ import annotations

import logging
import os
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from . import __version__
from .checks import connectivity, github, huggingface, pypi, speedtest
from .checks.base import CheckResult, STATUS_FAIL, STATUS_PASS, STATUS_WARN

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (loaded from env vars, matches the bash script's names).
# ---------------------------------------------------------------------------
@dataclass
class Config:
    retries: int = int(os.environ.get("RETRIES", "3"))
    timeout: int = int(os.environ.get("TIMEOUT", "10"))
    retry_delay: float = float(os.environ.get("RETRY_DELAY", "2"))
    port: int = int(os.environ.get("PORT", "443"))
    fail_on_ssl: bool = os.environ.get("FAIL_ON_SSL", "false").lower() == "true"
    domains_file: str = os.environ.get("DOMAINS_FILE", "/etc/precheck/domains.txt")
    extra_domains: str = os.environ.get("DOMAINS", "")
    hf_repo: str = os.environ.get("HF_REPO", "hf-internal-testing/tiny-random-gpt2")
    hf_file: str = os.environ.get("HF_FILE", "config.json")
    github_repo: str = os.environ.get("GITHUB_REPO", "https://github.com/octocat/Hello-World.git")
    run_on_startup: bool = os.environ.get("RUN_ON_STARTUP", "true").lower() == "true"
    # Max concurrent checks. 0 or negative means "auto" (min(32, domains+3)).
    max_workers: int = int(os.environ.get("MAX_WORKERS", "0"))
    # Speed test settings.
    speedtest_enabled: bool = os.environ.get("SPEEDTEST_ENABLED", "true").lower() == "true"
    speedtest_url: str = os.environ.get("SPEEDTEST_URL", speedtest.DEFAULT_URL)
    speedtest_download_bytes: int = int(
        os.environ.get("SPEEDTEST_DOWNLOAD_BYTES", str(speedtest.DEFAULT_DOWNLOAD_BYTES))
    )
    speedtest_upload_bytes: int = int(
        os.environ.get("SPEEDTEST_UPLOAD_BYTES", str(speedtest.DEFAULT_UPLOAD_BYTES))
    )
    speedtest_min_mbps: float = float(
        os.environ.get("SPEEDTEST_MIN_MBPS", str(speedtest.DEFAULT_MIN_MBPS))
    )


DEFAULT_DOMAINS = [
    # Hugging Face (dynamic signed-URL CDN hosts are validated by the HF
    # end-to-end download check, not by bare HTTPS probes).
    "huggingface.co", "hf.co",
    # NVIDIA NGC. Auth is authn.nvidia.com per NVIDIA's official network
    # protocols doc; auth.ngc.nvidia.com does not exist.
    "ngc.nvidia.com", "api.ngc.nvidia.com", "authn.nvidia.com",
    "catalog.ngc.nvidia.com", "files.ngc.nvidia.com",
    "xfiles.ngc.nvidia.com", "xlfiles.ngc.nvidia.com",
    "helm.ngc.nvidia.com",
    "nvcr.io", "layers.nvcr.io",
    # Docker Hub
    "hub.docker.com", "docker.io", "index.docker.io",
    "registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com",
    # GitHub
    "github.com", "api.github.com", "codeload.github.com",
    "raw.githubusercontent.com", "objects.githubusercontent.com",
    "github.githubassets.com", "ghcr.io",
    # PyPI
    "pypi.org", "files.pythonhosted.org", "pythonhosted.org",
]


def _load_domains(cfg: Config) -> list[str]:
    domains: list[str] = []
    if os.path.isfile(cfg.domains_file):
        with open(cfg.domains_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    domains.append(line)
        log.info("Loaded %d domains from %s", len(domains), cfg.domains_file)
    else:
        domains = list(DEFAULT_DOMAINS)
        log.info("Domains file %s not found; using %d built-in defaults",
                 cfg.domains_file, len(domains))
    if cfg.extra_domains.strip():
        domains.extend(cfg.extra_domains.split())
    # De-duplicate, preserve order.
    seen: set[str] = set()
    ordered: list[str] = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
@dataclass
class State:
    version: str = __version__
    hostname: str = platform.node()
    started_at: float | None = None
    finished_at: float | None = None
    running: bool = False
    current_task: str = "idle"
    progress: int = 0            # 0..100
    total_steps: int = 0
    completed_steps: int = 0
    results: list[CheckResult] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "hostname": self.hostname,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "running": self.running,
            "current_task": self.current_task,
            "progress": self.progress,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "config": self.config,
            "results": [r.to_dict() for r in self.results],
            "summary": summarize(self.results),
        }


def summarize(results: list[CheckResult]) -> dict[str, int]:
    summary = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0, "total": len(results)}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    return summary


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class Orchestrator:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config()
        self.state = State(config=self._config_dict())
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _config_dict(self) -> dict[str, Any]:
        return {
            "retries": self.cfg.retries,
            "timeout": self.cfg.timeout,
            "retry_delay": self.cfg.retry_delay,
            "port": self.cfg.port,
            "fail_on_ssl": self.cfg.fail_on_ssl,
            "domains_file": self.cfg.domains_file,
            "hf_repo": self.cfg.hf_repo,
            "hf_file": self.cfg.hf_file,
            "github_repo": self.cfg.github_repo,
            "max_workers": self.cfg.max_workers,
            "speedtest_enabled": self.cfg.speedtest_enabled,
            "speedtest_url": self.cfg.speedtest_url,
            "speedtest_download_bytes": self.cfg.speedtest_download_bytes,
            "speedtest_upload_bytes": self.cfg.speedtest_upload_bytes,
            "speedtest_min_mbps": self.cfg.speedtest_min_mbps,
        }

    # ---- lifecycle -------------------------------------------------------
    def is_running(self) -> bool:
        with self._lock:
            return self.state.running

    def start(self) -> bool:
        """Kick off a run in a background thread. Returns False if one is already running."""
        with self._lock:
            if self.state.running:
                return False
            self.state = State(config=self._config_dict())
            self.state.running = True
            self.state.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="precheck", daemon=True)
        self._thread.start()
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.state.to_dict()

    # ---- work ------------------------------------------------------------
    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self.state, k, v)

    def _bump(self, task: str) -> None:
        with self._lock:
            self.state.completed_steps += 1
            self.state.current_task = task
            if self.state.total_steps:
                self.state.progress = int(100 * self.state.completed_steps / self.state.total_steps)

    def _append(self, results: list[CheckResult]) -> None:
        with self._lock:
            self.state.results.extend(results)

    def _run(self) -> None:
        try:
            domains = _load_domains(self.cfg)

            # Build the task list: one per domain plus the three end-to-end
            # checks. Each task is (order_key, label, callable). The
            # order_key keeps the final results table stable regardless of
            # completion order, so the UI/PDF read cleanly.
            tasks: list[tuple[int, str, Callable[[], list[CheckResult]]]] = []
            for i, d in enumerate(domains):
                tasks.append((
                    i,
                    f"connectivity: {d}",
                    lambda d=d: connectivity.check_domain(
                        d,
                        port=self.cfg.port,
                        retries=self.cfg.retries,
                        timeout=self.cfg.timeout,
                        retry_delay=self.cfg.retry_delay,
                        fail_on_ssl=self.cfg.fail_on_ssl,
                    ),
                ))
            # Group the "expensive" end-to-end checks after the connectivity
            # rows in the report.
            base = len(domains)
            tasks.append((
                base + 0,
                "pypi: curl / wget / pip download",
                lambda: pypi.run(self.cfg.retries, self.cfg.timeout, self.cfg.retry_delay),
            ))
            tasks.append((
                base + 1,
                f"huggingface: download {self.cfg.hf_repo}",
                lambda: huggingface.run(
                    self.cfg.retries, self.cfg.timeout, self.cfg.retry_delay,
                    repo=self.cfg.hf_repo, filename=self.cfg.hf_file,
                ),
            ))
            tasks.append((
                base + 2,
                f"github: git clone {self.cfg.github_repo}",
                lambda: github.run(
                    self.cfg.retries, self.cfg.timeout, self.cfg.retry_delay,
                    repo_url=self.cfg.github_repo,
                ),
            ))
            if self.cfg.speedtest_enabled:
                tasks.append((
                    base + 3,
                    f"speedtest: {self.cfg.speedtest_url}",
                    lambda: speedtest.run(
                        self.cfg.retries, self.cfg.timeout, self.cfg.retry_delay,
                        base_url=self.cfg.speedtest_url,
                        download_bytes=self.cfg.speedtest_download_bytes,
                        upload_bytes=self.cfg.speedtest_upload_bytes,
                        min_mbps=self.cfg.speedtest_min_mbps,
                    ),
                ))

            total = len(tasks)
            self._set(total_steps=total, completed_steps=0, progress=0,
                      current_task=f"running {total} checks in parallel")

            # Decide pool size.
            configured = self.cfg.max_workers
            if configured and configured > 0:
                workers = configured
            else:
                # Cap at 32 to be nice to CPU + network stacks; ensure at
                # least 4 so small runs still parallelise.
                workers = max(4, min(32, total))
            log.info("Running %d checks with %d worker threads", total, workers)

            # Bucket results by order_key so they land in a deterministic
            # order once every future has completed.
            buckets: dict[int, list[CheckResult]] = {}
            active: dict[Any, tuple[int, str]] = {}
            in_flight_labels: list[str] = []

            def _refresh_current_task() -> None:
                # Reflect a couple of active labels in the UI progress line.
                if in_flight_labels:
                    preview = ", ".join(in_flight_labels[:3])
                    more = "" if len(in_flight_labels) <= 3 else f" (+{len(in_flight_labels)-3} more)"
                    self._set(current_task=f"in flight: {preview}{more}")

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="check") as pool:
                for order, label, fn in tasks:
                    fut = pool.submit(fn)
                    active[fut] = (order, label)
                    in_flight_labels.append(label)
                _refresh_current_task()

                for fut in as_completed(list(active.keys())):
                    order, label = active.pop(fut)
                    try:
                        results = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        log.exception("check %s failed with an unexpected error", label)
                        results = [CheckResult(
                            category="Errors",
                            name=label,
                            tool="orchestrator",
                            target=label,
                            status=STATUS_FAIL,
                            detail=f"{type(exc).__name__}: {exc}",
                        )]
                    buckets[order] = results
                    try:
                        in_flight_labels.remove(label)
                    except ValueError:
                        pass
                    _refresh_current_task()
                    self._bump(f"done: {label}")

            # Publish all results in deterministic order.
            ordered: list[CheckResult] = []
            for k in sorted(buckets.keys()):
                ordered.extend(buckets[k])
            with self._lock:
                self.state.results = ordered

            self._set(current_task="done", progress=100)
        except Exception:  # noqa: BLE001
            log.exception("orchestrator failed")
            self._set(current_task="error")
        finally:
            with self._lock:
                self.state.running = False
                self.state.finished_at = time.time()


# Module-level singleton.
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
