import os, sys, subprocess
def _here(): return os.path.dirname(os.path.abspath(sys.argv[0]))
def _root(s):
    d = s
    for _ in range(8):
        if os.path.isdir(os.path.join(d,"doc-qna")) or os.path.isfile(os.path.join(d,"venv","Scripts","pythonw.exe")): return d
        p = os.path.dirname(d)
        if p == d: break
        d = p
    return s
def _first(*ps):
    for p in ps:
        if p and os.path.isfile(p): return p
    return None
def main():
    here = _here(); root = _root(here)
    pyw = _first(os.path.join(here,"python","pythonw.exe"),          # bundled portable python
                 os.path.join(here,"python","python.exe"),
                 os.path.join(here,"venv","Scripts","pythonw.exe"),
                 os.path.join(root,"venv","Scripts","pythonw.exe")) or "pythonw.exe"
    entry = _first(os.path.join(here,"app","launcher.py"),
                   os.path.join(root,"doc-qna","frontend","launcher.py"))
    if not entry: return 2
    env = os.environ.copy()
    if not (env.get("DATA_DIR") or "").strip():
        for c in (os.path.join(os.path.dirname(here),"data"), os.path.join(root,"data"), os.path.join(root,"dist","data")):
            if os.path.isdir(c): env["DATA_DIR"] = c; break
    env.setdefault("YUKTRA_QNA_API_HOST","127.0.0.1")
    env.setdefault("YUKTRA_QNA_API_PORT","8009")
    env.setdefault("YUKTRA_QNA_API_BASE","http://127.0.0.1:8009")
    env.setdefault("YUKTRA_QNA_SKIP_WARMUP","1")
    return int(subprocess.Popen([pyw, entry], cwd=os.path.dirname(entry), env=env).wait())
if __name__ == "__main__":
    raise SystemExit(main())
