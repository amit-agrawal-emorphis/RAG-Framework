"""
Entry point for the Nuitka-compiled doc-management API server.

Compiled with:
  python -m nuitka --standalone ... server_main.py

All path resolution is done via environment variables so the binary
works regardless of where it is placed at runtime:
  YUKTRA_DM_DATA_DIR   — data directory (documents, models, embeddings)
  YUKTRA_DM_REACT_DIR  — React static files directory (served at /)
  YUKTRA_DM_INGEST_BIN — path to the compiled ingest binary
  YUKTRA_DM_API_HOST   — bind host  (default 0.0.0.0)
  YUKTRA_DM_API_PORT   — bind port  (default 8001)
  YUKTRA_DM_WORKERS    — Gunicorn worker count (default 2*CPU+1)
"""
from __future__ import annotations

import multiprocessing
import os

from gunicorn.app.base import BaseApplication

from api_server import app


class _StandaloneApp(BaseApplication):
    def __init__(self, application, options: dict):
        self.options = options
        self.application = application
        super().__init__()

    def load_config(self) -> None:
        for key, value in self.options.items():
            if key in self.cfg.settings and value is not None:
                self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


def main() -> int:
    host = os.environ.get("YUKTRA_DM_API_HOST", "0.0.0.0")
    port = int(os.environ.get("YUKTRA_DM_API_PORT", "8001"))

    workers_env = (os.environ.get("YUKTRA_DM_WORKERS") or "").strip()
    workers = int(workers_env) if workers_env else (2 * multiprocessing.cpu_count() + 1)

    _StandaloneApp(app, {
        "bind":         f"{host}:{port}",
        "workers":      workers,
        "worker_class": "uvicorn.workers.UvicornWorker",
        "loglevel":     "info",
        "accesslog":    "-",
        "errorlog":     "-",
    }).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
