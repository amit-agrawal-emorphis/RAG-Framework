# """
# Q&A Streamlit + pywebview only. The FastAPI backend must be running separately
# (``python doc-qna/backend/launcher.py`` or repo root ``launcher.py``, which starts it for you).

# Repo root ``launcher.py`` loads this module; Streamlit child mode uses ``--streamlit-child``.
# """
# from __future__ import annotations

# import os
# import socket
# import subprocess
# import sys
# import time
# import urllib.error
# import urllib.request
# import webbrowser

# _frontend_dir = os.path.dirname(os.path.abspath(__file__))
# if _frontend_dir not in sys.path:
#     sys.path.insert(0, _frontend_dir)

# from launcher_config import get_bin_name, get_launch_mode

# _dq_root = os.path.abspath(os.path.join(_frontend_dir, ".."))

# def _get_real_python():
#     env_python = (os.environ.get("PYTHON_EXECUTABLE") or "").strip()
#     if env_python:
#         return env_python
#     if sys.executable:
#         return sys.executable
#     return "python"

# def _pick_free_port() -> int:
#     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#         s.bind(("127.0.0.1", 0))
#         return int(s.getsockname()[1])


# def _wait_for_port(host: str, port: int, timeout_sec: float) -> bool:
#     deadline = time.time() + timeout_sec
#     while time.time() < deadline:
#         try:
#             with socket.create_connection((host, port), timeout=0.25):
#                 return True
#         except OSError:
#             time.sleep(0.1)
#     return False


# def _append_repo_pythonpath(repo_root: str, env: dict) -> None:
#     """Match ``run_chatbot.sh``: ``doc-qna/backend`` + ``doc-management/backend``."""
#     roots = [
#         os.path.join(repo_root, "doc-qna", "backend"),
#         os.path.join(repo_root, "doc-management", "backend"),
#     ]
#     prev = (env.get("PYTHONPATH") or "").strip()
#     env["PYTHONPATH"] = os.pathsep.join([*roots, prev] if prev else roots)


# def _api_listen_host_port() -> tuple[str, int]:
#     host = (os.environ.get("YUKTRA_QNA_API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
#     raw = (os.environ.get("YUKTRA_QNA_API_PORT") or "8008").strip() or "8008"
#     try:
#         port = int(raw)
#     except ValueError:
#         port = 8008
#     return host, port


# def _wait_for_api_health(api_base: str, timeout_sec: float) -> bool:
#     url = f"{api_base.rstrip('/')}/health"
#     deadline = time.time() + timeout_sec
#     while time.time() < deadline:
#         try:
#             with urllib.request.urlopen(url, timeout=2.0) as resp:
#                 if resp.status == 200:
#                     return True
#         except (urllib.error.URLError, OSError, TimeoutError, ValueError):
#             pass
#         time.sleep(0.5)
#     return False


# def run_streamlit_in_this_process(script_path: str, port: int) -> None:
#     # Streamlit installs signal handlers; this must be the main thread of a process.
#     from streamlit.web import cli as stcli

#     sys.argv = [
#         "streamlit",
#         "run",
#         script_path,
#         "--theme.base",
#         "light",
#         "--global.developmentMode",
#         "false",
#         "--server.address",
#         "127.0.0.1",
#         "--server.port",
#         str(port),
#         "--server.headless",
#         "true",
#         "--browser.gatherUsageStats",
#         "false",
#         "--server.enableCORS",
#         "false",
#         "--server.enableXsrfProtection",
#         "false",
#     ]
#     stcli.main()


# # Root ``launcher.py`` (``--streamlit-child``) expects this symbol.
# _run_streamlit_in_this_process = run_streamlit_in_this_process


# def main() -> int:
#     if os.environ.get("ALLOW_KEYRING", "").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
#         os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

#     doc_qna_root = _dq_root
#     repo_root = os.path.abspath(os.path.join(doc_qna_root, ".."))
#     base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
#     script_path = os.path.join(base_dir, "streamlit_app.py")
#     #script_path = os.path.join(doc_qna_root, "frontend", "streamlit_app.py")
#     root_launcher = os.path.join(repo_root, "launcher.py")

#     if len(sys.argv) >= 3 and sys.argv[1] == "--streamlit-child":
#         run_streamlit_in_this_process(script_path=script_path, port=int(sys.argv[2]))
#         return 0

#     api_host, api_port = _api_listen_host_port()
#     api_base = (os.environ.get("YUKTRA_QNA_API_BASE") or "").strip().rstrip("/")
#     if not api_base:
#         api_base = f"http://{api_host}:{api_port}"

#     wait_raw = (os.environ.get("YUKTRA_QNA_LAUNCHER_WAIT_API_SEC") or "360").strip()
#     try:
#         wait_sec = float(wait_raw)
#     except ValueError:
#         wait_sec = 360.0
#     if wait_sec > 0 and not _wait_for_api_health(api_base, wait_sec):
#         print(
#             f"The QnA API did not respond at {api_base}/health within {wait_sec:.0f}s.\n"
#             "Start the backend first, then launch the frontend again:\n"
#             "  python doc-qna/backend/launcher.py",
#             file=sys.stderr,
#         )
#         return 1

#     port = _pick_free_port()
#     url = f"http://127.0.0.1:{port}"

#     child_env = os.environ.copy()
#     child_env["YUKTRA_QNA_API_BASE"] = api_base
#     _append_repo_pythonpath(repo_root, child_env)

#     if get_launch_mode() == "bin":
#         cmd = [os.path.join(repo_root, get_bin_name()), "--streamlit-child", str(port)]
#     else:
#         self_path = os.path.abspath(sys.argv[0])
#         child_entry = root_launcher if os.path.isfile(root_launcher) else self_path
#         # if child_entry.endswith(".py"):
#         #     cmd = [sys.executable, child_entry, "--streamlit-child", str(port)]
#         # else:
#         #     cmd = [child_entry, "--streamlit-child", str(port)]
#         if child_entry.endswith(".py"):
#             cmd = [_get_real_python(), child_entry, "--streamlit-child", str(port)]
#         else:
#             cmd = [child_entry, "--streamlit-child", str(port)]

#     proc = subprocess.Popen(cmd, cwd=repo_root, env=child_env)
#     try:
#         if not _wait_for_port("127.0.0.1", port, timeout_sec=90.0):
#             raise RuntimeError("Streamlit did not start (port not ready).")

#         try:
#             import webview  # type: ignore

#             webview.create_window("Yuktra", url, width=1200, height=800)
#             webview.start(gui="gtk")
#             return 0
#         except Exception:
#             webbrowser.open(url)
#             return int(proc.wait())
#     finally:
#         if proc.poll() is None:
#             proc.terminate()
#             try:
#                 proc.wait(timeout=5)
#             except Exception:
#                 proc.kill()


# if __name__ == "__main__":
#     raise SystemExit(main())
"""
Q&A Streamlit + pywebview only. The FastAPI backend must be running separately
(``python doc-qna/backend/launcher.py`` or repo root ``launcher.py``, which starts it for you).

Repo root ``launcher.py`` loads this module; Streamlit child mode uses ``--streamlit-child``.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

_frontend_dir = os.path.dirname(os.path.abspath(__file__))
if _frontend_dir not in sys.path:
    sys.path.insert(0, _frontend_dir)

from launcher_config import get_bin_name, get_launch_mode

_dq_root = os.path.abspath(os.path.join(_frontend_dir, ".."))

def _get_real_python():
    env_python = (os.environ.get("PYTHON_EXECUTABLE") or "").strip()
    if env_python:
        return env_python
    if sys.executable:
        return sys.executable
    return "python"

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


def _append_repo_pythonpath(repo_root: str, env: dict) -> None:
    """Match ``run_chatbot.sh``: ``doc-qna/backend`` + ``doc-management/backend``."""
    roots = [
        os.path.join(repo_root, "doc-qna", "backend"),
        os.path.join(repo_root, "doc-management", "backend"),
    ]
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = os.pathsep.join([*roots, prev] if prev else roots)


def _api_listen_host_port() -> tuple[str, int]:
    host = (os.environ.get("YUKTRA_QNA_API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw = (os.environ.get("YUKTRA_QNA_API_PORT") or "8009").strip() or "8009"
    try:
        port = int(raw)
    except ValueError:
        port = 8009
    return host, port


def _wait_for_api_health(api_base: str, timeout_sec: float) -> bool:
    url = f"{api_base.rstrip('/')}/health"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            pass
        time.sleep(0.5)
    return False


def run_streamlit_in_this_process(script_path: str, port: int) -> None:
    # In a windowed build (Nuitka --windows-console-mode=disable) there is no console,
    # so sys.stdout / sys.stderr are None. Streamlit writes to stdout during startup
    # and crashes on a None stream -> the server never binds and the UI never opens.
    # Redirect both to a log file so the streams are valid and any error is captured.
    if sys.stdout is None or sys.stderr is None:
        import tempfile
        data_dir = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(_dq_root, "..", "data")
        log_dir = os.path.join(data_dir, "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            log_dir = tempfile.gettempdir()
        _log = open(os.path.join(log_dir, "streamlit_child.log"), "a", buffering=1,
                    encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = _log
        if sys.stderr is None:
            sys.stderr = _log

    # Streamlit installs signal handlers; this must be the main thread of a process.
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
    try:
        stcli.main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        raise


# Root ``launcher.py`` (``--streamlit-child``) expects this symbol.
_run_streamlit_in_this_process = run_streamlit_in_this_process


def main() -> int:
    if os.environ.get("ALLOW_KEYRING", "").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
        os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    doc_qna_root = _dq_root
    repo_root = os.path.abspath(os.path.join(doc_qna_root, ".."))
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    script_path = os.path.join(base_dir, "streamlit_app.py")
    #script_path = os.path.join(doc_qna_root, "frontend", "streamlit_app.py")
    root_launcher = os.path.join(repo_root, "launcher.py")

    if len(sys.argv) >= 3 and sys.argv[1] == "--streamlit-child":
        run_streamlit_in_this_process(script_path=script_path, port=int(sys.argv[2]))
        return 0

    api_host, api_port = _api_listen_host_port()
    api_base = (os.environ.get("YUKTRA_QNA_API_BASE") or "").strip().rstrip("/")
    if not api_base:
        api_base = f"http://{api_host}:{api_port}"

    wait_raw = (os.environ.get("YUKTRA_QNA_LAUNCHER_WAIT_API_SEC") or "360").strip()
    try:
        wait_sec = float(wait_raw)
    except ValueError:
        wait_sec = 360.0
    if wait_sec > 0 and not _wait_for_api_health(api_base, wait_sec):
        print(
            f"The QnA API did not respond at {api_base}/health within {wait_sec:.0f}s.\n"
            "Start the backend first, then launch the frontend again:\n"
            "  python doc-qna/backend/launcher.py",
            file=sys.stderr,
        )
        return 1

    port = _pick_free_port()
    url = f"http://127.0.0.1:{port}"

    child_env = os.environ.copy()
    child_env["YUKTRA_QNA_API_BASE"] = api_base
    _append_repo_pythonpath(repo_root, child_env)

    if get_launch_mode() == "bin":
        cmd = [os.path.join(repo_root, get_bin_name()), "--streamlit-child", str(port)]
    else:
        self_path = os.path.abspath(sys.argv[0])
        child_entry = root_launcher if os.path.isfile(root_launcher) else self_path
        # if child_entry.endswith(".py"):
        #     cmd = [sys.executable, child_entry, "--streamlit-child", str(port)]
        # else:
        #     cmd = [child_entry, "--streamlit-child", str(port)]
        if child_entry.endswith(".py"):
            cmd = [_get_real_python(), child_entry, "--streamlit-child", str(port)]
        else:
            cmd = [child_entry, "--streamlit-child", str(port)]

    proc = subprocess.Popen(cmd, cwd=repo_root, env=child_env)
    try:
        if not _wait_for_port("127.0.0.1", port, timeout_sec=90.0):
            raise RuntimeError("Streamlit did not start (port not ready).")

        try:
            import webview  # type: ignore

            try:
                import tkinter as _tk
                _r = _tk.Tk()
                _sw, _sh = _r.winfo_screenwidth(), _r.winfo_screenheight()
                _r.destroy()
            except Exception:
                _sw, _sh = 1920, 1080
            # Native window backend per platform: EdgeChromium/WebView2 on Windows,
            # GTK on Linux. gui=None lets pywebview pick the best available backend
            # (forcing "gtk" on Windows is why it used to fall back to the browser).
            _gui = "gtk" if sys.platform.startswith("linux") else None
            _win = webview.create_window("Yuktra", url, width=_sw, height=_sh, x=0, y=0)
            webview.start(func=_win.maximize, gui=_gui)
            return 0
        except Exception as _e:
            # Log why the native window failed (streams may be None in a windowed
            # build, so write to a file) before falling back to a browser tab.
            try:
                import tempfile, traceback
                _dd = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(_dq_root, "..", "data")
                _ld = os.path.join(_dd, "logs")
                try:
                    os.makedirs(_ld, exist_ok=True)
                except Exception:
                    _ld = tempfile.gettempdir()
                with open(os.path.join(_ld, "frontend_launcher.log"), "a", encoding="utf-8", errors="replace") as _lf:
                    _lf.write("webview window failed; falling back to browser:\n")
                    _lf.write(traceback.format_exc())
                    _lf.write("\n")
            except Exception:
                pass
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
