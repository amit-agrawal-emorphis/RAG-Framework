"""
QnA API entrypoint: uvicorn from repo root with the same PYTHONPATH as ``run_chatbot.sh``.

Example:
  python doc-qna/backend/launcher.py
"""
from __future__ import annotations

import os
import sys


def _load_dotenv(repo_root: str) -> None:
    """Load .env from repo root; existing env vars take precedence (setdefault behaviour)."""
    env_path = os.path.join(repo_root, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _apply_ram_limit() -> None:
    """Apply a virtual-memory ceiling from YUKTRA_MAX_RAM_MB (Linux only)."""
    raw = os.environ.get("YUKTRA_MAX_RAM_MB", "").strip()
    if not raw:
        return
    try:
        import resource
        ram_bytes = int(raw) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (ram_bytes, ram_bytes))
    except Exception:
        pass


def _prepare() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    os.chdir(repo_root)
    dq = os.path.join(repo_root, "doc-qna", "backend")
    dm = os.path.join(repo_root, "doc-management", "backend")
    for p in (dq, dm, repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)
    return repo_root


def _append_repo_pythonpath(repo_root: str, env: dict) -> None:
    roots = [
        os.path.join(repo_root, "doc-qna", "backend"),
        os.path.join(repo_root, "doc-management", "backend"),
    ]
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = os.pathsep.join([*roots, prev] if prev else roots)


def _api_listen_host_port() -> tuple[str, int]:
    host = (os.environ.get("YUKTRA_QNA_API_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    raw = (os.environ.get("YUKTRA_QNA_API_PORT") or "8009").strip() or "8009"
    try:
        port = int(raw)
    except ValueError:
        port = 8009
    return host, port


def main() -> None:
    if os.environ.get("ALLOW_KEYRING", "").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
        os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    repo_root = _prepare()
    _load_dotenv(repo_root)
    _apply_ram_limit()
    host, port = _api_listen_host_port()
    _append_repo_pythonpath(os.getcwd(), os.environ)

    import uvicorn

    uvicorn.run("api:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
