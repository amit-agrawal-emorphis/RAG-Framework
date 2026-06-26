"""
Desktop entry at repo root: starts the QnA API when needed, then Streamlit + webview.

The API runs via ``doc-qna/backend/launcher.py``; the UI via ``doc-qna/frontend/launcher.py``.
Streamlit child mode: ``--streamlit-child`` (only Streamlit in this process).
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_qna_frontend_launcher():
    doc_qna_root = os.path.join(_REPO_ROOT, "doc-qna")
    if doc_qna_root not in sys.path:
        sys.path.insert(0, doc_qna_root)
    path = os.path.join(doc_qna_root, "frontend", "launcher.py")
    spec = importlib.util.spec_from_file_location("yuktra_qna_launcher", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load launcher spec: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tcp_listening(host: str, port: int, *, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _api_host_port() -> tuple[str, int]:
    host = (os.environ.get("YUKTRA_QNA_API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw = (os.environ.get("YUKTRA_QNA_API_PORT") or "8008").strip() or "8008"
    try:
        port = int(raw)
    except ValueError:
        port = 8008
    return host, port


def _start_backend_subprocess() -> tuple[subprocess.Popen | None, object | None]:
    """Run ``doc-qna/backend/launcher.py`` if present and API port is free."""
    backend_py = os.path.join(_REPO_ROOT, "doc-qna", "backend", "launcher.py")
    if not os.path.isfile(backend_py):
        return None, None

    host, port = _api_host_port()
    if _tcp_listening(host, port):
        return None, None

    log_path = (os.environ.get("YUKTRA_QNA_API_LOG") or "").strip() or os.path.join(
        tempfile.gettempdir(), "yuktra_qna_api.log"
    )
    log_f = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [sys.executable, backend_py],
            cwd=_REPO_ROOT,
            env=os.environ.copy(),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log_f.close()
        raise
    return proc, log_f


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def main() -> int:
    qna = _load_qna_frontend_launcher()
    if len(sys.argv) >= 3 and sys.argv[1] == "--streamlit-child":
        script = os.path.join(_REPO_ROOT, "doc-qna", "frontend", "streamlit_app.py")
        qna._run_streamlit_in_this_process(script_path=script, port=int(sys.argv[2]))
        return 0

    api_proc, api_log_f = _start_backend_subprocess()
    try:
        return int(qna.main())
    finally:
        _terminate_process(api_proc)
        if api_log_f is not None:
            try:
                api_log_f.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
