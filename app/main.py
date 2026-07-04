"""Flask entry point for pcai-precheck.

Serves the live status page on port 18080, exposes a JSON API and a
downloadable PDF report.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request

from .orchestrator import get_orchestrator
from .report import build_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pcai-precheck")


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    orch = get_orchestrator()
    if orch.cfg.run_on_startup:
        log.info("Auto-starting pre-check on startup.")
        orch.start()

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True})

    @app.get("/api/results")
    def api_results() -> Response:
        return jsonify(orch.snapshot())

    @app.post("/api/run")
    def api_run() -> tuple[Response, int]:
        if orch.start():
            return jsonify({"started": True}), 202
        return jsonify({"started": False, "reason": "already running"}), 409

    @app.get("/report.pdf")
    def report_pdf() -> Response:
        snapshot = orch.snapshot()
        pdf = build_pdf(snapshot)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"pcai-precheck-{ts}.pdf"
        resp = Response(pdf, mimetype="application/pdf")
        # 'inline' lets browsers preview it; the UI provides an explicit
        # download link that opens this endpoint as an attachment.
        disposition = request.args.get("disposition", "attachment")
        resp.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return resp

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT_HTTP", "18080"))
    # Threaded so long-running checks don't block the UI polling.
    app.run(host="0.0.0.0", port=port, threaded=True)
