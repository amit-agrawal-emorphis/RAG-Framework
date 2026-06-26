"""
Doc-management Streamlit + pywebview. Ingestion is CLI-only: run
``python doc-management/backend/launcher.py`` separately when you need to ingest.

Run: ``python doc-management/frontend/launcher.py`` (Streamlit child mode uses ``--streamlit-child``).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser

_frontend_dir = os.path.dirname(os.path.abspath(__file__))
if _frontend_dir not in sys.path:
    sys.path.insert(0, _frontend_dir)

from launcher_config import get_bin_name, get_launch_mode

_dm_root = os.path.abspath(os.path.join(_frontend_dir, ".."))


def _repo_root() -> str:
    return os.path.abspath(os.path.join(_dm_root, ".."))


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_streamlit_in_this_process(script_path: str, port: int) -> None:
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--theme.base",
        "light",
        "--global.developmentMode",
        "false",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.enableCORS",
        "false",
        "--server.enableXsrfProtection",
        "false",
    ]
    stcli.main()


_run_streamlit_in_this_process = run_streamlit_in_this_process


def main() -> int:
    if os.environ.get("ALLOW_KEYRING", "").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
        os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    repo_root = _repo_root()
    os.chdir(repo_root)

    script_path = os.path.join(_dm_root, "frontend", "streamlit_app.py")
    if not os.path.isfile(script_path):
        print(
            f"Missing UI script: {script_path}\n"
            "Add streamlit_app.py next to this launcher, or install frontend deps:\n"
            "  pip install -r doc-management/frontend/requirements.txt",
            file=sys.stderr,
        )
        return 1

    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "Streamlit is not installed. Install with:\n"
            "  pip install -r doc-management/frontend/requirements.txt",
            file=sys.stderr,
        )
        return 1

    if len(sys.argv) >= 3 and sys.argv[1] == "--streamlit-child":
        run_streamlit_in_this_process(script_path=script_path, port=int(sys.argv[2]))
        return 0

    port_env = (os.environ.get("YUKTRA_DM_STREAMLIT_PORT") or "").strip()
    if port_env:
        try:
            port = int(port_env)
        except ValueError:
            port = _pick_free_port()
    else:
        port = _pick_free_port()

    url = f"http://127.0.0.1:{port}"
    child_env = os.environ.copy()

    child_script = os.path.join(_dm_root, "frontend", "launcher.py")

    if get_launch_mode() == "bin":
        cmd = [os.path.join(repo_root, get_bin_name()), "--streamlit-child", str(port)]
    else:
        self_path = os.path.abspath(sys.argv[0])
        child_entry = child_script if os.path.isfile(child_script) else self_path
        if child_entry.endswith(".py"):
            cmd = [sys.executable, child_entry, "--streamlit-child", str(port)]
        else:
            cmd = [child_entry, "--streamlit-child", str(port)]

    proc = subprocess.Popen(cmd, cwd=repo_root, env=child_env)
    try:
        if not _wait_for_port("127.0.0.1", port, timeout_sec=90.0):
            raise RuntimeError("Streamlit did not start (port not ready).")

        try:
            import webview  # type: ignore

            webview.create_window("Doc management", url, width=1200, height=800)
            webview.start(gui="gtk")
            return 0
        except Exception:
            webbrowser.open(url)
            return int(proc.wait())
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
