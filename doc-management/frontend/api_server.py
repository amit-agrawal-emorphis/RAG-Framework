#



"""API server that powers the React doc-management UI."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Allow env-var overrides so Nuitka-compiled builds and Docker containers can set
# runtime paths without depending on __file__ compile-time path resolution.
REPO_ROOT = (os.environ.get("YUKTRA_DM_REPO_ROOT") or "").strip() or os.path.abspath(os.path.join(APP_DIR, "..", ".."))
_DM_BACKEND = os.path.join(REPO_ROOT, "doc-management", "backend")
if _DM_BACKEND not in sys.path:
    sys.path.insert(0, _DM_BACKEND)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
from vector_store_cleanup import delete_machine_tenant, remove_document_vectors  # noqa: E402
from export_queue_store import (  # noqa: E402
    export_cleanup_download,
    export_get_ready_zip,
    export_mark_failed,
    export_mark_ready,
    export_owns_build_slot,
    export_prepare,
    export_protected_zip_paths,
    export_remove_machine,
    export_status,
    init_export_queue_db,
)

DATA_DIR = (os.environ.get("YUKTRA_DM_DATA_DIR") or "").strip() or os.path.join(REPO_ROOT, "data")
REACT_DIST_DIR = (os.environ.get("YUKTRA_DM_REACT_DIR") or "").strip() or os.path.join(APP_DIR, "react-app", "dist")
TEMPLATE_BUNDLE_DIR = os.path.join(DATA_DIR, "Yuktra-YEQ")
TEMPLATE_BUNDLE_ZIP = os.path.join(DATA_DIR, "Yuktra-YEQ.zip")
DEFAULT_EMBEDDING_MODEL = os.path.join(DATA_DIR, "models", "embeddinggemma-300M-Q8_0.gguf")
AUTH_DB_PATH = os.path.join(DATA_DIR, "admin.db")
AUTH_PBKDF2_ITERATIONS = 200_000

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".mp4"}
INGEST_STATES: dict[str, dict[str, str]] = {}
INGEST_LOCK = threading.Lock()
INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=2)
EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _slugify_machine_name(raw: str) -> str:
    value = (raw or "").strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    value = value.strip("._")
    return value


def _tenant_docs_dir(machine_name: str) -> str:
    return os.path.join(DATA_DIR, machine_name, "documents")


def _tenant_index_root(machine_name: str) -> str:
    return os.path.join(DATA_DIR, machine_name)


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _init_auth_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _hash_password(password: str, salt_hex: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), AUTH_PBKDF2_ITERATIONS
    )
    return derived.hex()


def _load_indexed_doc_names(machine_name: str) -> set[str]:
    meta_path = os.path.join(_tenant_index_root(machine_name), "document_text", "metadata.json")
    if not os.path.isfile(meta_path):
        return set()
    try:
        with open(meta_path, encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            return set()
        return {
            str(row.get("doc_name") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("doc_name") or "").strip()
        }
    except Exception:
        return set()


def _load_ingest_doc_status(machine_name: str) -> dict[str, dict[str, Any]]:
    status_path = os.path.join(_tenant_index_root(machine_name), "document_text", "ingest_doc_status.json")
    if not os.path.isfile(status_path):
        return {}
    try:
        with open(status_path, encoding="utf-8") as f:
            payload = json.load(f)
        docs = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(docs, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for name, row in docs.items():
            if isinstance(row, dict):
                out[str(name)] = row
        return out
    except Exception:
        return {}


def _list_documents(machine_name: str) -> list[dict[str, Any]]:
    docs_dir = _tenant_docs_dir(machine_name)
    if not os.path.isdir(docs_dir):
        return []

    rows: list[dict[str, Any]] = []
    indexed_docs = _load_indexed_doc_names(machine_name)
    ingest_report = _load_ingest_doc_status(machine_name)
    with INGEST_LOCK:
        cur = INGEST_STATES.get(machine_name, {})
        machine_state = str(cur.get("state") or "")
        machine_progress = int(cur.get("progressPct") or 0)
        processing_docs = {
            str(x).strip()
            for x in (cur.get("processingDocs") or [])
            if str(x).strip()
        }

    for name in sorted(os.listdir(docs_dir)):
        path = os.path.join(docs_dir, name)
        if not os.path.isfile(path):
            continue

        report_row = ingest_report.get(name) or {}
        if machine_state == "in_progress":
            if name in processing_docs or name not in indexed_docs:
                doc_status = "In progress"
                doc_pct = machine_progress
            else:
                doc_status = "Completed"
                doc_pct = 100
        elif name in indexed_docs:
            doc_status = "Completed"
            doc_pct = 100
        elif str(report_row.get("status") or "").strip().lower() == "failed":
            doc_status = "Failed"
            doc_pct = 0
        else:
            doc_status = "Uploaded"
            doc_pct = 0

        row: dict[str, Any] = {
            "fileName": name,
            "machineName": machine_name,
            "status": doc_status,
            "absPath": path,
            "ingestionPct": doc_pct,
            "machineIngestState": machine_state,
        }
        err = str(report_row.get("error") or "").strip()
        if err:
            row["ingestError"] = err
        rows.append(row)
    return rows


def _list_all_documents() -> list[dict[str, Any]]:
    if not os.path.isdir(DATA_DIR):
        return []
    rows: list[dict[str, Any]] = []
    for machine_name in sorted(os.listdir(DATA_DIR)):
        tenant_root = os.path.join(DATA_DIR, machine_name)
        if not os.path.isdir(tenant_root):
            continue
        if machine_name in {"models", "ui", "docs", "doc-management", "Yuktra-YEQ", "Ingested", "logs", "chat_history_db"}:
            continue
        tenant_docs = os.path.join(tenant_root, "documents")
        tenant_text = os.path.join(tenant_root, "document_text")
        tenant_img = os.path.join(tenant_root, "Img")
        if not (os.path.isdir(tenant_docs) or os.path.isdir(tenant_text) or os.path.isdir(tenant_img)):
            continue
        rows.extend(_list_documents(machine_name))
    rows.sort(key=lambda row: (row["machineName"], row["fileName"]))
    return rows


def _save_uploads(machine_name: str, files: list[UploadFile]) -> tuple[int, int]:
    if not files:
        return 0, 0

    tenant_docs_dir = _tenant_docs_dir(machine_name)
    os.makedirs(tenant_docs_dir, exist_ok=True)
    saved = 0
    skipped = 0

    for upload in files:
        base = os.path.basename(upload.filename or "").strip()
        if not base:
            skipped += 1
            continue
        ext = os.path.splitext(base)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped += 1
            continue
        # Duplicate check is machine-scoped only.
        # Same document name is allowed for different machine names.
        if os.path.isfile(os.path.join(tenant_docs_dir, base)):
            skipped += 1
            continue

        payload = upload.file.read()
        target = os.path.join(tenant_docs_dir, base)
        with open(target, "wb") as out:
            out.write(payload)
        saved += 1
    return saved, skipped


def _delete_selected(paths: list[str]) -> int:
    deleted = 0
    for path in paths:
        if os.path.isfile(path):
            os.remove(path)
            deleted += 1
    return deleted


def _delete_file(machine_name: str, file_name: str) -> dict[str, Any]:
    return remove_document_vectors(DATA_DIR, machine_name, file_name)


def _delete_machine_documents(machine_name: str) -> dict[str, Any]:
    return delete_machine_tenant(DATA_DIR, machine_name)


def _bump_ingest_progress(machine_name: str, target_pct: int) -> None:
    with INGEST_LOCK:
        cur = INGEST_STATES.setdefault(machine_name, {})
        cur["progressPct"] = max(int(cur.get("progressPct") or 0), int(target_pct))


def _track_processing_doc(machine_name: str, doc_path: str) -> None:
    base = os.path.basename((doc_path or "").strip())
    if not base:
        return
    with INGEST_LOCK:
        cur = INGEST_STATES.setdefault(machine_name, {})
        docs = cur.get("processingDocs")
        if not isinstance(docs, list):
            docs = []
        if base not in docs:
            docs.append(base)
        cur["processingDocs"] = docs


def _run_ingestion(machine_name: str) -> tuple[bool, str]:
    docs_dir = _tenant_docs_dir(machine_name)
    out_dir = _tenant_index_root(machine_name)
    if not os.path.isdir(docs_dir):
        return False, f"Docs folder not found: {docs_dir}"
    if not any(os.path.isfile(os.path.join(docs_dir, n)) for n in os.listdir(docs_dir)):
        return False, f"No documents found in: {docs_dir}"
    if not os.path.isfile(DEFAULT_EMBEDDING_MODEL):
        return False, f"Embedding model not found: {DEFAULT_EMBEDDING_MODEL}"
    os.makedirs(out_dir, exist_ok=True)

    # YUKTRA_DM_INGEST_BIN — path to the compiled ingest.bin (Nuitka standalone).
    # YUKTRA_DM_INGEST_PYTHON — fallback: Python interpreter + launcher.py script.
    _ingest_bin = (os.environ.get("YUKTRA_DM_INGEST_BIN") or "").strip()
    _ingest_args = [
        "--docs_dir", docs_dir,
        "--out_dir", out_dir,
        "--index_name", "document_text",
        "--image_index_name", "Img",
        "--incremental",
        "--docling_embed_markdown_images",
        "--embedding_model", DEFAULT_EMBEDDING_MODEL,
    ]
    if _ingest_bin and os.path.isfile(_ingest_bin):
        # Compiled binary: call directly with args (no interpreter, no script path).
        cmd = [_ingest_bin] + _ingest_args
    else:
        # Fallback: plain Python interpreter + launcher script.
        _ingest_py = (os.environ.get("YUKTRA_DM_INGEST_PYTHON") or "").strip() or sys.executable
        cmd = [
            _ingest_py, "-u",
            os.path.join("doc-management", "backend", "launcher.py"),
        ] + _ingest_args
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output_lines: list[str] = []
    total_docs = 0
    docs_done = 0
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                output_lines.append(line)
                if "ingest_document_discovery count=" in line:
                    m = re.search(r"count=(\d+)", line)
                    if m:
                        total_docs = max(1, int(m.group(1)))
                        _bump_ingest_progress(machine_name, 5)
                elif "incremental_process_doc path=" in line:
                    m = re.search(r"incremental_process_doc path=(.+)$", line.strip())
                    if m:
                        _track_processing_doc(machine_name, m.group(1).strip())
                elif "ingest_document_start path=" in line:
                    m = re.search(r"ingest_document_start path=(.+?) ext=", line)
                    if m:
                        _track_processing_doc(machine_name, m.group(1).strip())
                    docs_done += 1
                    process_total = max(1, total_docs)
                    scaled = 5 + int(min(docs_done, process_total) / process_total * 35)
                    _bump_ingest_progress(machine_name, scaled)
                elif "video_transcript phase=extract_audio" in line:
                    _bump_ingest_progress(machine_name, 40)
                elif "video_transcript phase=whisper" in line:
                    _bump_ingest_progress(machine_name, 55)
                elif "video_transcript phase=chunked" in line:
                    _bump_ingest_progress(machine_name, 72)
                elif "ingest_chunking_ok " in line:
                    _bump_ingest_progress(machine_name, 78)
                elif "ingest_phase=embed_chunks" in line:
                    _bump_ingest_progress(machine_name, 85)
                elif "ingest_export_done " in line:
                    _bump_ingest_progress(machine_name, 95)
                elif "ingest_images export_done" in line:
                    _bump_ingest_progress(machine_name, 98)
    finally:
        proc.wait()
    output = "".join(output_lines).strip()
    return proc.returncode == 0, output


def _zip_tenant(machine_name: str) -> bytes:
    tenant_root = _tenant_index_root(machine_name)
    text_dir = os.path.join(tenant_root, "document_text")
    img_dir = os.path.join(tenant_root, "Img")
    docs_dir = _tenant_docs_dir(machine_name)
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source_dir, folder_name in ((text_dir, "document_text"), (img_dir, "Img"), (docs_dir, "documents")):
            if not os.path.isdir(source_dir):
                continue
            for root, _dirs, files in os.walk(source_dir):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, source_dir)
                    arcname = os.path.join(machine_name, folder_name, rel_path)
                    zf.write(abs_path, arcname=arcname)
    mem.seek(0)
    return mem.getvalue()


def _fast_file_copy(src: str, dst: str) -> None:
    """Copy a large file with a big buffer (faster than metadata-preserving copy)."""
    with open(src, "rb") as src_f, open(dst, "wb") as dst_f:
        shutil.copyfileobj(src_f, dst_f, length=16 * 1024 * 1024)


def _resolve_template_bundle_source() -> tuple[str, str]:
    """Prefer the pre-built Yuktra-YEQ.zip; fall back to an extracted folder."""
    zip_path = (os.environ.get("YUKTRA_DM_BUNDLE_ZIP") or "").strip() or TEMPLATE_BUNDLE_ZIP
    if os.path.isfile(zip_path):
        return "zip", zip_path
    if os.path.isdir(TEMPLATE_BUNDLE_DIR):
        return "dir", TEMPLATE_BUNDLE_DIR
    raise FileNotFoundError("Yuktra-YEQ bundle not found (expected Yuktra-YEQ.zip or Yuktra-YEQ/)")


def _append_machine_to_bundle_zip(export_zip_path: str, machine_name: str, machine_root: str) -> None:
    """Append machine ingested artifacts into an export copy of the base bundle."""
    ingested_prefix = f"Yuktra-YEQ/data/Ingested/{machine_name}/"
    with zipfile.ZipFile(export_zip_path, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for root, _dirs, files in os.walk(machine_root):
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, machine_root).replace("\\", "/")
                zf.write(abs_path, arcname=f"{ingested_prefix}{rel_path}")


def _build_template_bundle_from_dir(machine_name: str, zip_path: str, machine_root: str) -> None:
    """Legacy path: rebuild the bundle from an extracted Yuktra-YEQ/ folder."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for root, _dirs, files in os.walk(TEMPLATE_BUNDLE_DIR):
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, TEMPLATE_BUNDLE_DIR)
                zf.write(abs_path, arcname=os.path.join("Yuktra-YEQ", rel_path))
        for root, _dirs, files in os.walk(machine_root):
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, machine_root)
                arcname = os.path.join("Yuktra-YEQ", "data", "Ingested", machine_name, rel_path)
                zf.write(abs_path, arcname=arcname)


def _build_template_bundle(machine_name: str) -> str:
    """Build a Yuktra-YEQ export zip with the machine folder under data/Ingested/<machine>.

    When data/Yuktra-YEQ.zip exists, copy it and append only the machine folder
    (fast). Otherwise fall back to walking an extracted Yuktra-YEQ/ tree.
    """
    machine_root = _tenant_index_root(machine_name)
    token = secrets.token_hex(4)
    zip_path = os.path.join(DATA_DIR, f"Yuktra-YEQ_export_{machine_name}_{token}.zip")

    source_kind, source_path = _resolve_template_bundle_source()
    if source_kind == "zip":
        _fast_file_copy(source_path, zip_path)
        _append_machine_to_bundle_zip(zip_path, machine_name, machine_root)
    else:
        _build_template_bundle_from_dir(machine_name, zip_path, machine_root)

    return zip_path


def _force_rmtree(path: str) -> None:
    """Remove a directory tree even if it contains read-only files/dirs (the
    Yuktra-YEQ bundle ships some read-only Ingested artifacts, which copytree
    preserves and which would otherwise block deletion)."""
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path):
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), 0o700)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_bundle(zip_path: str) -> None:
    """Remove a temporary export zip once the download is done."""
    try:
        os.remove(zip_path)
    except OSError:
        pass


def _submit_export_job(slug: str) -> None:
    EXPORT_EXECUTOR.submit(_run_export_background, slug)


def _cleanup_export_job(slug: str, zip_path: str) -> None:
    _cleanup_bundle(zip_path)
    next_slug = export_cleanup_download(DATA_DIR, slug, zip_path)
    if next_slug:
        _submit_export_job(next_slug)


def _validate_export_request(slug: str) -> None:
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")

    tenant_index_root = _tenant_index_root(slug)
    text_dir = os.path.join(tenant_index_root, "document_text")
    img_dir = os.path.join(tenant_index_root, "Img")
    if not (os.path.isdir(text_dir) and os.path.isdir(img_dir)):
        raise HTTPException(status_code=404, detail="Indexed tenant artifacts not found")

    try:
        _resolve_template_bundle_source()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Yuktra-YEQ bundle not found")


def _run_export_background(slug: str) -> None:
    if not export_owns_build_slot(DATA_DIR, slug):
        return
    next_slug: str | None = None
    try:
        _sweep_stale_bundles(export_protected_zip_paths(DATA_DIR))
        if not export_owns_build_slot(DATA_DIR, slug):
            return
        zip_path = _build_template_bundle(slug)
        zip_size = os.path.getsize(zip_path)
        export_mark_ready(DATA_DIR, slug, zip_path, zip_size)
    except Exception as exc:
        next_slug = export_mark_failed(DATA_DIR, slug, str(exc))
    if next_slug:
        _submit_export_job(next_slug)


def _sweep_stale_bundles(protected_paths: set[str] | None = None) -> None:
    """Remove orphaned export zips, keeping active builds/downloads intact."""
    protected = protected_paths or set()
    prefix = "Yuktra-YEQ_export_"
    for entry in os.listdir(DATA_DIR):
        if not entry.startswith(prefix):
            continue
        path = os.path.abspath(os.path.join(DATA_DIR, entry))
        if path in protected:
            continue
        if os.path.isdir(path):
            _force_rmtree(path)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def _run_ingestion_background(machine_name: str) -> None:
    ok, logs = _run_ingestion(machine_name)
    tenant_index_root = _tenant_index_root(machine_name)
    text_dir = os.path.join(tenant_index_root, "document_text")
    img_dir = os.path.join(tenant_index_root, "Img")
    complete = ok and os.path.isdir(text_dir) and os.path.isdir(img_dir)
    with INGEST_LOCK:
        INGEST_STATES[machine_name] = {
            "state": "completed" if complete else "failed",
            "logs": logs,
            "progressPct": 100 if complete else 0,
            "processingDocs": [],
        }


def _start_ingestion(machine_name: str) -> tuple[bool, str]:
    with INGEST_LOCK:
        current_state = INGEST_STATES.get(machine_name, {}).get("state")
        if current_state == "in_progress":
            return False, "Ingestion already in progress for this machine."
        INGEST_STATES[machine_name] = {
            "state": "in_progress",
            "logs": "",
            "progressPct": 0,
            "processingDocs": [],
        }
    INGEST_EXECUTOR.submit(_run_ingestion_background, machine_name)
    return True, "Ingestion started."


app = FastAPI(title="Doc Management API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    _ensure_dirs()
    _init_auth_db()
    init_export_queue_db(DATA_DIR)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class AuthRequest(BaseModel):
    user_id: str
    password: str


@app.post("/api/auth/signup")
def auth_signup(payload: AuthRequest) -> dict[str, Any]:
    user_id = (payload.user_id or "").strip()
    password = payload.password or ""
    if not user_id or not password:
        raise HTTPException(status_code=400, detail="User ID and password are required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        raise HTTPException(status_code=400, detail="Password must include a letter")
    if not re.search(r"[0-9]", password):
        raise HTTPException(status_code=400, detail="Password must include a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise HTTPException(status_code=400, detail="Password must include a special character")

    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    conn = sqlite3.connect(AUTH_DB_PATH)
    try:
        try:
            conn.execute(
                "INSERT INTO users (user_id, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (user_id, pwd_hash, salt, datetime.utcnow().isoformat(timespec="seconds") + "Z"),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="This User ID is already registered")
    finally:
        conn.close()
    return {"ok": True, "userId": user_id}


@app.post("/api/auth/login")
def auth_login(payload: AuthRequest) -> dict[str, Any]:
    user_id = (payload.user_id or "").strip()
    password = payload.password or ""
    if not user_id or not password:
        raise HTTPException(status_code=400, detail="User ID and password are required")
    conn = sqlite3.connect(AUTH_DB_PATH)
    try:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="User ID not found")
    stored_hash, salt = row
    if not hmac.compare_digest(stored_hash, _hash_password(password, salt)):
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"ok": True, "userId": user_id}


@app.get("/api/documents")
def list_documents(machine_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")
    return {"machineName": slug, "rows": _list_documents(slug)}


@app.get("/api/documents/all")
def list_all_documents() -> dict[str, Any]:
    return {"rows": _list_all_documents()}


@app.post("/api/documents/upload")
def upload_documents(machine_name: str = Form(...), files: list[UploadFile] = File(default=[])) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")

    saved_count, skipped_count = _save_uploads(slug, files)
    return {
        "machineName": slug,
        "savedCount": saved_count,
        "skippedCount": skipped_count,
    }


@app.delete("/api/documents")
def delete_documents(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise HTTPException(status_code=400, detail="paths is required")
    deleted_count = _delete_selected(paths)
    return {"deletedCount": deleted_count}


@app.delete("/api/documents/file")
def delete_document_file(machine_name: str, file_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")
    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    return _delete_file(slug, file_name)


@app.delete("/api/documents/machine")
def delete_machine(machine_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")
    next_slug = export_remove_machine(DATA_DIR, slug)
    if next_slug:
        _submit_export_job(next_slug)
    with INGEST_LOCK:
        INGEST_STATES.pop(slug, None)
    return _delete_machine_documents(slug)


@app.post("/api/ingest")
def ingest(machine_name: str) -> JSONResponse:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")

    ok, logs = _run_ingestion(slug)
    tenant_index_root = _tenant_index_root(slug)
    text_dir = os.path.join(tenant_index_root, "document_text")
    img_dir = os.path.join(tenant_index_root, "Img")

    if not ok:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "logs": logs, "message": "Ingestion failed. Export ZIP not generated."},
        )
    if not (os.path.isdir(text_dir) and os.path.isdir(img_dir)):
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "logs": logs,
                "message": "Ingestion completed but expected tenant folders are missing.",
            },
        )

    return JSONResponse(content={"ok": True, "logs": logs, "message": "Ingestion Completed."})


@app.post("/api/ingest/start")
def ingest_start(machine_name: str) -> JSONResponse:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")
    started, message = _start_ingestion(slug)
    return JSONResponse(
        status_code=202 if started else 409,
        content={"ok": started, "machineName": slug, "message": message},
    )


@app.get("/api/ingest/status")
def ingest_status(machine_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")
    with INGEST_LOCK:
        payload = INGEST_STATES.get(slug, {"state": "idle", "logs": ""})
    return {
        "machineName": slug,
        "state": payload.get("state", "idle"),
        "logs": payload.get("logs", ""),
        "progressPct": int(payload.get("progressPct") or 0),
        "processingDocs": list(payload.get("processingDocs") or []),
    }


@app.post("/api/export-zip/prepare")
def prepare_export_zip(machine_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    _validate_export_request(slug)

    payload, next_slug = export_prepare(DATA_DIR, slug)
    if next_slug:
        _submit_export_job(next_slug)
    return payload


@app.get("/api/export-zip/status")
def export_zip_status(machine_name: str) -> dict[str, Any]:
    slug = _slugify_machine_name(machine_name)
    if not slug:
        raise HTTPException(status_code=400, detail="machine_name is required")

    return export_status(DATA_DIR, slug)


@app.get("/api/export-zip")
def export_zip(machine_name: str) -> FileResponse:
    slug = _slugify_machine_name(machine_name)
    _validate_export_request(slug)

    try:
        zip_path = export_get_ready_zip(DATA_DIR, slug)
    except LookupError:
        raise HTTPException(status_code=409, detail="Export is not ready yet")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Prepared export file is missing")

    headers = {"Content-Disposition": 'attachment; filename="Yuktra-YEQ.zip"'}
    return FileResponse(
        zip_path,
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(_cleanup_export_job, slug, zip_path),
    )


if os.path.isdir(REACT_DIST_DIR):
    app.mount("/", StaticFiles(directory=REACT_DIST_DIR, html=True), name="doc_management_ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="127.0.0.1", port=8001, reload=True)
 