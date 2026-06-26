"""
yuktra-eq.exe / yeq.exe -- the user-facing launcher.

Flow:
  1. Show a small loading splash immediately.
  2. Ensure the backend is up: poll http://127.0.0.1:<port>/health. If the
     Windows service (YuktraEQBackend) isn't answering yet, try to start it.
  3. Launch webview-runner.exe (the Streamlit UI in a WebView2 window).
  4. Close the splash once the UI window has been launched / backend is healthy.

Built with Nuitka: see build_launcher.ps1 (adds --windows-icon-from-ico +
--enable-plugin=tk-inter for the splash).
"""
import os
import sys
import time
import subprocess
import urllib.request

_NO_WINDOW = 0x08001000  # CREATE_NO_WINDOW

API_BASE = (os.environ.get("YUKTRA_QNA_API_BASE") or "http://127.0.0.1:8008").rstrip("/")
SERVICE_NAME = os.environ.get("YUKTRA_EQ_SERVICE", "YuktraEQBackend")


def _log(msg: str) -> None:
    """Write a timestamped entry to launcher.log in the data directory."""
    try:
        data_dir = (os.environ.get("DATA_DIR") or "").strip()
        if not data_dir:
            return
        log_dir = os.path.join(data_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(log_dir, "launcher.log"), "a", encoding="utf-8") as f:
            f.write(f"{ts} | {msg}\n")
    except Exception:
        pass


def _here() -> str:
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(API_BASE + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _start_backend(here: str) -> None:
    # Prefer the installed Windows service; fall back to launching the exe directly.
    try:
        subprocess.run(["sc", "start", SERVICE_NAME], capture_output=True, creationflags=_NO_WINDOW)
        return
    except Exception:
        pass
    exe = os.path.join(here, "yuktra-eq-backend.exe")
    if not os.path.isfile(exe):
        exe = os.path.join(here, "backend", "backend.exe")
    if os.path.isfile(exe):
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=_NO_WINDOW)
        except Exception:
            pass


def _make_splash():
    try:
        import tkinter as tk
    except Exception:
        return None, None
    try:
        root = tk.Tk()
        try:
            root.iconbitmap(os.path.join(_here(), "yuktra.ico"))
        except Exception:
            pass
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        w, h = 440, 200
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        root.configure(bg="#141821")
        tk.Label(root, text="Yuktra-EQ", fg="white", bg="#141821",
                 font=("Segoe UI", 24, "bold")).pack(pady=(46, 8))
        msg = tk.Label(root, text="Starting, please wait…", fg="#aab4c8", bg="#141821",
                       font=("Segoe UI", 11))
        msg.pack()
        root.update()
        return root, msg
    except Exception:
        return None, None


def main() -> int:
    here = _here()
    _log(f"launcher_app started — here={here!r}  API_BASE={API_BASE!r}")
    root, msg = _make_splash()

    if not _health_ok():
        if root and msg:
            try:
                msg.config(text="Starting backend service…"); root.update()
            except Exception:
                pass
        _log("backend not healthy — attempting to start service")
        _start_backend(here)

    deadline = time.time() + 240
    while time.time() < deadline:
        if _health_ok():
            break
        if root:
            try:
                root.update()
            except Exception:
                pass
        time.sleep(1)

    if not _health_ok():
        _log(f"ERROR: backend did not respond at {API_BASE}/health after 240s — frontend will not start")
        if root and msg:
            try:
                msg.config(text="Backend not responding — see data/logs/launcher.log"); root.update()
                time.sleep(5)
            except Exception:
                pass
        if root:
            try:
                root.destroy()
            except Exception:
                pass
        return 1

    if root and msg:
        try:
            msg.config(text="Opening application…"); root.update()
        except Exception:
            pass

    # Launch the UI (webview-runner.exe). Try installed name then dev layout.
    runner = os.path.join(here, "webview-runner.exe")
    if not os.path.isfile(runner):
        runner = os.path.join(here, "frontend", "frontend.exe")
    if os.path.isfile(runner):
        _log(f"launching {runner!r}")
        try:
            subprocess.Popen([runner], cwd=os.path.dirname(runner))
        except Exception as e:
            _log(f"ERROR launching runner: {e}")
    else:
        _log(f"ERROR: webview-runner.exe not found at {runner!r}")

    # Give the UI a moment to spawn its window, then drop the splash.
    time.sleep(3)
    if root:
        try:
            root.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
