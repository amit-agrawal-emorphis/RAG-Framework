import argparse
import base64
import binascii
import hashlib
import json
import os
from datetime import datetime, timezone
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Repo layout: ``doc-qna/backend`` and ``doc-management/backend`` on ``PYTHONPATH``.
def _ensure_repo_paths() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    dq_backend = os.path.join(repo_root, "doc-qna", "backend")
    dm_backend = os.path.join(repo_root, "doc-management", "backend")
    for p in (dm_backend, dq_backend, repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)
    return repo_root


_REPO_ROOT = _ensure_repo_paths()

import numpy as np

# Run: ``python -m ingest_and_export ...`` with both backends on ``PYTHONPATH``, or ``doc-management/backend/launcher.py``.

from logger import get_logger, log_process_end, log_process_start
from store_runtime_config import runtime_defaults_subset
from ground_id_grouping import assign_ground_ids_slice
from rag_utils import (
    EMBEDDING_PROMPT_STYLE_EMBEDDINGGEMMA,
    EMBEDDING_PROMPT_STYLE_PLAIN,
    embed_texts_llamacpp,
    is_embeddinggemma_model,
    load_llamacpp_embedding_model,
    load_vector_store,
    pipeline_log_preview,
    read_text_file,
    save_vector_store,
)

_data_dir_env = (os.environ.get("YUKTRA_DM_DATA_DIR") or "").strip()
_INGEST_LOG_DIR = os.path.join(_data_dir_env if _data_dir_env else os.path.join(_REPO_ROOT, "data"), "logs")


def _runtime_threads() -> int:
    raw = os.environ.get("YUKTRA_LLM_N_THREADS", "").strip()
    try:
        val = int(raw) if raw else 2
    except ValueError:
        val = 2
    return max(1, val)


def _dm_n_gpu_layers() -> int:
    """Return n_gpu_layers for doc-management embedding model loading.

    YUKTRA_DM_USE_GPU controls behavior:
      - "0" / "false" / "no" / "off" → force CPU (returns 0).
      - any other value or unset      → auto: offload all layers (-1) if the
        installed llama-cpp-python was built with GPU support, else CPU (0).

    Embedding GGUFs are small (~300 MB) so offloading all layers is the right
    setting when GPU is available; CPU is the safe default elsewhere.
    """
    raw = (os.environ.get("YUKTRA_DM_USE_GPU") or "auto").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return 0
    try:
        from llama_cpp import llama_supports_gpu_offload  # type: ignore
        if bool(llama_supports_gpu_offload()):
            return -1
    except Exception:
        pass
    return 0


# -------------------------------------------------------------------
# Document discovery
# -------------------------------------------------------------------

def iter_documents(docs_dir: str) -> List[str]:
    supported_exts = {".pdf", ".docx", ".txt", ".md", ".markdown", ".mp4"}
    paths: List[str] = []
    for root, _, files in os.walk(docs_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in supported_exts:
                paths.append(os.path.join(root, name))
    paths.sort()
    return paths


_MD_IMAGE_RE = re.compile(r"!\[(?P<caption>[^\]]*)\]\((?P<src>[^)]+)\)")
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)$",
    flags=re.IGNORECASE,
)


def _clean_caption_candidate(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"^\*\*|\*\*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _caption_from_nearby_markdown(md: str, start: int, end: int) -> str:
    """
    Try to infer a meaningful caption from lines near the image markdown token.
    """
    window = 800
    left = md[max(0, start - window) : start]
    right = md[end : min(len(md), end + window)]

    left_lines = [ln.strip() for ln in left.splitlines() if ln.strip()]
    right_lines = [ln.strip() for ln in right.splitlines() if ln.strip()]

    candidates: List[str] = []
    # Nearby forward lines often contain figure labels/captions.
    candidates.extend(right_lines[:6])
    # Fallback: nearest preceding lines.
    candidates.extend(list(reversed(left_lines[-6:])))

    for raw in candidates:
        if "data:image/" in raw.lower():
            continue
        if raw.startswith("![") or raw.startswith("<!--"):
            continue
        c = _clean_caption_candidate(raw)
        if not c:
            continue
        # Prefer lines that look like figure captions.
        if re.search(r"\b(fig(ure)?|diagram|layout|schematic)\b", c, flags=re.IGNORECASE):
            return c[:220]
    for raw in candidates:
        c = _clean_caption_candidate(raw)
        if not c:
            continue
        if len(c) < 4:
            continue
        return c[:220]
    return ""


def _extract_embedded_images_from_markdown(
    markdown_text: str,
    *,
    doc_name: str,
    doc_path: str,
) -> List[Dict[str, Any]]:
    """
    Extract data-URI images from markdown and emit caption-indexable rows.
    """
    out: List[Dict[str, Any]] = []
    if not (markdown_text or "").strip():
        return out

    image_idx = 0
    for m in _MD_IMAGE_RE.finditer(markdown_text):
        src = (m.group("src") or "").strip()
        dm = _DATA_URI_RE.match(src)
        if not dm:
            continue
        caption = (m.group("caption") or "").strip()
        is_generic = (caption or "").strip().lower() in ("", "image", "img", "picture", "figure")
        if is_generic:
            nearby = _caption_from_nearby_markdown(markdown_text, m.start(), m.end())
            caption = nearby or f"{os.path.splitext(doc_name)[0]} figure {image_idx + 1}"
        mime = (dm.group("mime") or "image/png").strip().lower()
        b64_raw = re.sub(r"\s+", "", dm.group("data") or "")
        if not b64_raw:
            continue
        try:
            raw_bytes = base64.b64decode(b64_raw, validate=True)
        except (ValueError, binascii.Error):
            continue
        if not raw_bytes:
            continue
        b64_clean = base64.b64encode(raw_bytes).decode("ascii")
        out.append(
            {
                "image_uuid": str(uuid.uuid4()),
                "caption": caption,
                "text": caption,  # Retrieval uses caption embeddings.
                "image_base64": b64_clean,
                "image_mime": mime,
                "doc_name": doc_name,
                "doc_path": doc_path,
                "image_index": image_idx,
                "chunk_type": "embedded_image",
            }
        )
        image_idx += 1
    return out


# -------------------------------------------------------------------
# Markdown chunk loading (Docling PDF/DOCX + plain-text Markdown)
# -------------------------------------------------------------------

def _load_markdown_chunks_via_docling(
    path: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
    force_backend_text: bool = False,
    embed_images: bool = False,
    pdf_try_native_first: bool = True,
) -> tuple[str, List[Dict[str, Any]]]:
    """
    Docling PDF/DOCX → Markdown (tables + image placeholders/captions) → RAG chunks.
    """
    from docling_loader import convert_to_markdown_via_docling
    from markdown_ingest import chunk_markdown_for_rag

    md = convert_to_markdown_via_docling(
        path,
        extract_tables=True,
        force_backend_text=force_backend_text,
        embed_images=embed_images,
        pdf_try_native_first=pdf_try_native_first,
    )
    if not (md or "").strip():
        raise RuntimeError("Docling markdown export was empty")

    chunks = chunk_markdown_for_rag(
        md,
        chunk_size_chars=chunk_size_chars,
        chunk_overlap_chars=chunk_overlap_chars,
    )
    if not chunks:
        raise RuntimeError("Markdown chunking produced zero chunks")
    return md, chunks


def _load_markdown_chunks_from_text_file(
    path: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> List[Dict[str, Any]]:
    """``.md`` / ``.txt`` as Markdown: same heading + size chunking as Docling output."""
    from markdown_ingest import chunk_markdown_for_rag

    md = read_text_file(path)
    if not (md or "").strip():
        raise RuntimeError("Text file is empty")
    chunks = chunk_markdown_for_rag(
        md,
        chunk_size_chars=chunk_size_chars,
        chunk_overlap_chars=chunk_overlap_chars,
    )
    if not chunks:
        raise RuntimeError("Markdown chunking produced zero chunks")
    return chunks


# def _default_whisper_bin() -> str:
#     return os.path.join(_REPO_ROOT, "data", "models", "whisper.cpp", "build", "bin", "whisper-cli")


# def _default_whisper_model() -> str:
#     primary = os.path.join(_REPO_ROOT, "data", "models", "ggml-base.bin")
#     if os.path.isfile(primary):
#         return primary
#     return os.path.join(_REPO_ROOT, "data", "models", "whisper.cpp", "models", "ggml-base.bin")


# -------------------------------------------------------------------
# whisper.cpp paths
# -------------------------------------------------------------------

def _default_whisper_bin() -> str:
    """
    Resolve whisper.cpp executable path.

    Priority:
    1. ENV override
    2. /opt/whisper.cpp/build/bin/whisper-cli
    3. legacy repo-local path
    """

    env_path = os.environ.get("WHISPER_CPP_PATH", "").strip()
    if env_path:
        return env_path

    docker_path = "/opt/whisper.cpp/build/bin/whisper-cli"
    if os.path.isfile(docker_path):
        return docker_path

    legacy_path = os.path.join(
        _REPO_ROOT,
        "data",
        "models",
        "whisper.cpp",
        "build",
        "bin",
        "whisper-cli",
    )

    return legacy_path


def _default_whisper_model() -> str:
    """
    Resolve Whisper model path.

    Priority:
    1. ENV override
    2. Existing ggml-base.bin in mounted models folder
    3. whisper.cpp bundled models
    """

    env_model = os.environ.get("YUKTRA_WHISPER_MODEL_PATH", "").strip()
    if env_model:
        return env_model

    primary = os.path.join(
        _REPO_ROOT,
        "data",
        "models",
        "ggml-base.bin",
    )

    if os.path.isfile(primary):
        return primary

    docker_model = "/opt/whisper.cpp/models/ggml-base.bin"
    if os.path.isfile(docker_model):
        return docker_model

    legacy_model = os.path.join(
        _REPO_ROOT,
        "data",
        "models",
        "whisper.cpp",
        "models",
        "ggml-base.bin",
    )

    return legacy_model


##########################################################################################################



def _resolve_ffmpeg_bin() -> str:
    configured = os.environ.get("YUKTRA_FFMPEG_BIN", "").strip()
    if configured:
        return configured
    return shutil.which("ffmpeg") or "ffmpeg"


def _video_ingest_temp_parent() -> str:
    """Workspace-local temp dir (some ffmpeg builds cannot write under ``/tmp`` subdirs)."""
    configured = (os.environ.get("YUKTRA_VIDEO_INGEST_TMP_DIR") or "").strip()
    parent = configured or os.path.join(
        _data_dir_env if _data_dir_env else os.path.join(_REPO_ROOT, "data"),
        ".video_ingest_tmp",
    )
    os.makedirs(parent, exist_ok=True)
    return parent


def _subprocess_error_tail(proc: subprocess.CompletedProcess[str], *, max_chars: int = 900) -> str:
    """Return the tail of stderr/stdout — ffmpeg prints its banner first, errors last."""
    err = (proc.stderr or proc.stdout or "").strip()
    if len(err) <= max_chars:
        return err
    return err[-max_chars:]


_VIDEO_CHUNK_WINDOW_SEC = 60


def _format_video_timestamp(seconds: int) -> str:
    """Format seconds as ``MM:SS`` (or ``HH:MM:SS`` once past 1 hour)."""
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _bucket_whisper_segments_into_minute_chunks(
    segments: List[Dict[str, Any]],
    *,
    window_sec: int = _VIDEO_CHUNK_WINDOW_SEC,
) -> List[Dict[str, Any]]:
    """Group whisper segments into fixed-size time windows.

    Each segment carries millisecond ``offsets.from`` / ``offsets.to`` and ``text``.
    A segment is assigned to the bucket containing its start offset, so segments that
    span a window boundary are not duplicated.
    """
    buckets: Dict[int, Dict[str, Any]] = {}
    win_ms = max(1, int(window_sec)) * 1000
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        offsets = seg.get("offsets") or {}
        try:
            t_start_ms = int(offsets.get("from") or 0)
            t_end_ms = int(offsets.get("to") or t_start_ms)
        except (TypeError, ValueError):
            continue
        if t_start_ms < 0:
            t_start_ms = 0
        if t_end_ms < t_start_ms:
            t_end_ms = t_start_ms
        bucket_idx = t_start_ms // win_ms
        b = buckets.setdefault(
            bucket_idx,
            {"texts": [], "min_start_ms": t_start_ms, "max_end_ms": t_end_ms},
        )
        b["texts"].append(text)
        if t_start_ms < b["min_start_ms"]:
            b["min_start_ms"] = t_start_ms
        if t_end_ms > b["max_end_ms"]:
            b["max_end_ms"] = t_end_ms

    out: List[Dict[str, Any]] = []
    for idx in sorted(buckets.keys()):
        bk = buckets[idx]
        joined = " ".join(t for t in bk["texts"] if t).strip()
        if not joined:
            continue
        win_start_sec = idx * (win_ms // 1000)
        win_end_sec = win_start_sec + (win_ms // 1000)
        actual_end_sec = max(win_end_sec, int(round(bk["max_end_ms"] / 1000.0)))
        timestamp = (
            f"{_format_video_timestamp(win_start_sec)}-"
            f"{_format_video_timestamp(min(win_end_sec, actual_end_sec))}"
        )
        out.append(
            {
                "content": joined,
                "page_number": idx + 1,
                "section_heading": timestamp,
                "chunk_type": "video_segment",
                "timestamp": timestamp,
                "start_sec": win_start_sec,
                "end_sec": min(win_end_sec, actual_end_sec),
            }
        )
    return out


def _load_markdown_chunks_from_video_file(
    path: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
    log: Any,
) -> List[Dict[str, Any]]:
    """
    MP4 -> mono 16k WAV -> whisper.cpp transcript with timestamps -> 1-minute chunks.
    Each returned chunk corresponds to a fixed time window and carries a ``timestamp``
    field of the form ``MM:SS-MM:SS``. Video ingestion intentionally produces only
    text chunks/embeddings.
    """
    ffmpeg_bin = _resolve_ffmpeg_bin()
    whisper_bin = os.environ.get("YUKTRA_WHISPER_CPP_BIN", _default_whisper_bin()).strip()
    whisper_model = os.environ.get("YUKTRA_WHISPER_MODEL_PATH", _default_whisper_model()).strip()
    whisper_lang = os.environ.get("YUKTRA_VIDEO_WHISPER_LANG", os.environ.get("YUKTRA_WHISPER_LANG", "auto")).strip() or "auto"
    whisper_threads_raw = os.environ.get("YUKTRA_WHISPER_THREADS", str(_runtime_threads())).strip()
    try:
        whisper_threads = max(1, int(whisper_threads_raw or _runtime_threads()))
    except ValueError:
        whisper_threads = _runtime_threads()

    if not shutil.which(ffmpeg_bin) and not os.path.isfile(ffmpeg_bin):
        raise RuntimeError(
            "ffmpeg is required for video ingestion. Install ffmpeg or set YUKTRA_FFMPEG_BIN to its path."
        )
    if not os.path.isfile(whisper_bin):
        raise RuntimeError(f"whisper.cpp binary not found: {whisper_bin}")
    if not os.path.isfile(whisper_model):
        raise RuntimeError(f"Whisper model not found: {whisper_model}")

    with tempfile.TemporaryDirectory(
        prefix="yuktra_video_ingest_",
        dir=_video_ingest_temp_parent(),
    ) as td:
        wav_path = os.path.join(td, "audio.wav")
        out_prefix = os.path.join(td, "transcript")
        log.info(
            "video_transcript phase=extract_audio path=%s ffmpeg=%s tmp_dir=%s",
            os.path.abspath(path),
            ffmpeg_bin,
            td,
        )
        ffmpeg_cmd = [
            ffmpeg_bin,
            "-nostdin",
            "-y",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            wav_path,
        ]
        ffmpeg_run = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if ffmpeg_run.returncode != 0:
            err = _subprocess_error_tail(ffmpeg_run)
            raise RuntimeError(f"ffmpeg audio extraction failed: {err}")
        if not os.path.isfile(wav_path) or os.path.getsize(wav_path) <= 44:
            raise RuntimeError("ffmpeg produced an empty WAV; video may not contain an audio track.")

        log.info(
            "video_transcript phase=whisper wav_bytes=%d model=%s lang=%s threads=%s",
            os.path.getsize(wav_path),
            whisper_model,
            whisper_lang,
            whisper_threads,
        )
        whisper_cmd = [
            whisper_bin,
            "-m",
            whisper_model,
            "-f",
            wav_path,
            "-l",
            whisper_lang,
            "-t",
            str(whisper_threads),
            "-oj",
            "-of",
            out_prefix,
            "-np",
        ]
        whisper_run = subprocess.run(whisper_cmd, capture_output=True, text=True)
        if whisper_run.returncode != 0:
            err = _subprocess_error_tail(whisper_run)
            raise RuntimeError(f"whisper.cpp video transcription failed: {err}")
        transcript_json_path = out_prefix + ".json"
        if not os.path.isfile(transcript_json_path):
            raise RuntimeError("whisper.cpp did not produce a video transcript JSON file.")
        with open(transcript_json_path, "r", encoding="utf-8", errors="replace") as f:
            transcript_doc = json.load(f)

    segments = transcript_doc.get("transcription") if isinstance(transcript_doc, dict) else None
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Whisper transcript JSON contained no segments.")

    chunks = _bucket_whisper_segments_into_minute_chunks(
        segments,
        window_sec=_VIDEO_CHUNK_WINDOW_SEC,
    )
    if not chunks:
        raise RuntimeError("Video transcript bucketing produced zero chunks")

    transcript_chars = sum(len(c.get("content") or "") for c in chunks)
    log.info(
        "video_transcript phase=chunked doc=%s transcript_chars=%d chunks=%d window_sec=%d first_ts=%s last_ts=%s",
        os.path.basename(path),
        transcript_chars,
        len(chunks),
        _VIDEO_CHUNK_WINDOW_SEC,
        chunks[0].get("timestamp"),
        chunks[-1].get("timestamp"),
    )
    return chunks


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _write_ingest_doc_status(final_out_dir: str, documents: Dict[str, Dict[str, Any]]) -> None:
    """Persist per-document ingest outcomes for the doc-management UI."""
    os.makedirs(final_out_dir, exist_ok=True)
    path = os.path.join(final_out_dir, "ingest_doc_status.json")
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _markdown_first_line_plain(text: str) -> str:
    """First line of text with leading ATX # markers removed, lowercased."""
    if not (text or "").strip():
        return ""
    first = text.lstrip().split("\n", 1)[0].strip()
    return re.sub(r"^#+\s*", "", first).strip().lower()


def _coerce_section_path_list(section_path: Any, section_path_str: str) -> List[str]:
    out: List[str] = []
    if isinstance(section_path, list):
        out = [str(x).strip() for x in section_path if str(x).strip()]
    s = (section_path_str or "").strip()
    if not out and s:
        out = [s]
    return out


def _forward_fill_section_paths_and_texts(
    chunk_texts: List[str],
    metas: List[Dict[str, Any]],
) -> None:
    """
    For one document: carry forward the last non-empty section_path_str / section_path
    into chunks that have none, then prepend that path to embedding text when needed.
    """
    last_str = ""
    last_list: List[str] = []

    for i in range(len(metas)):
        m = metas[i]
        sps0 = (m.get("section_path_str") or "").strip()
        path_list0 = _coerce_section_path_list(m.get("section_path"), sps0)

        inherited = False
        if not sps0 and not path_list0:
            if last_str:
                m["section_path_str"] = last_str
                m["section_path"] = list(last_list)
                inherited = True
        else:
            if sps0:
                m["section_path_str"] = sps0
            if path_list0:
                m["section_path"] = path_list0
            elif sps0:
                m["section_path"] = [sps0]

        eff_str = (m.get("section_path_str") or "").strip()
        eff_list = m.get("section_path")
        if isinstance(eff_list, list) and [x for x in eff_list if str(x).strip()]:
            last_list = [str(x).strip() for x in eff_list if str(x).strip()]
            last_str = eff_str or last_list[-1]
        elif eff_str:
            last_str = eff_str
            last_list = [eff_str]

        t = (m.get("section_path_str") or "").strip()
        raw = (m.get("raw_content") or "").strip()
        cur = chunk_texts[i]
        if t and inherited and _markdown_first_line_plain(raw) != t.strip().lower():
            cur = f"{t}\n\n{cur}"
        chunk_texts[i] = cur
        m["text"] = cur


def _safe_name(value: str, default: str) -> str:
    v = (value or "").strip()
    if not v:
        v = default
    out_chars: List[str] = []
    for ch in v:
        if ch.isalnum() or ch in ("-", "_", "."):
            out_chars.append(ch)
        elif ch.isspace():
            out_chars.append("_")
        else:
            out_chars.append("_")
    out = "".join(out_chars).strip("._")
    return out or default


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _doc_name_from_meta(row: Dict[str, Any]) -> str:
    return str(row.get("doc_name") or os.path.basename(str(row.get("doc_path") or ""))).strip()


def _reconstruct_faiss_vectors(index: Any, log: Any) -> Optional[np.ndarray]:
    try:
        rows: List[np.ndarray] = []
        for i in range(int(index.ntotal)):
            rows.append(np.asarray(index.reconstruct(i), dtype=np.float32))
        if not rows:
            return np.zeros((0, int(getattr(index, "d", 0) or 0)), dtype=np.float32)
        return np.vstack(rows).astype(np.float32)
    except Exception as e:
        log.warning("incremental_existing_reconstruct_failed err=%s", e, exc_info=True)
        return None


def _load_existing_vectors(
    store_dir: str,
    log: Any,
) -> Tuple[Optional[np.ndarray], List[Dict[str, Any]], Dict[str, Any]]:
    if not os.path.isdir(store_dir):
        return None, [], {}
    try:
        vectors, faiss_index, metadata, config = load_vector_store(store_dir)
        if vectors is None and faiss_index is not None:
            vectors = _reconstruct_faiss_vectors(faiss_index, log)
        if vectors is None:
            return None, [], {}
        return np.asarray(vectors, dtype=np.float32), list(metadata or []), dict(config or {})
    except Exception as e:
        log.warning("incremental_existing_load_failed store_dir=%s err=%s", store_dir, e, exc_info=True)
        return None, [], {}


def _metadata_matches_file(rows: List[Dict[str, Any]], fingerprint: str) -> bool:
    if not rows:
        return False
    for row in rows:
        text = f"{row.get('text') or ''}\n{row.get('raw_content') or ''}"
        if "data:image/" in text.lower():
            return False
    row_fingerprints = {
        str(r.get("doc_fingerprint") or "").strip()
        for r in rows
        if str(r.get("doc_fingerprint") or "").strip()
    }
    if row_fingerprints:
        return row_fingerprints == {fingerprint}
    # Backward compatibility for stores created before fingerprints existed:
    # a same-named uploaded file is treated as already indexed.
    return True


def _filter_kept_rows(
    vectors: Optional[np.ndarray],
    metadata: List[Dict[str, Any]],
    doc_names_to_keep: set[str],
) -> Tuple[Optional[np.ndarray], List[Dict[str, Any]]]:
    if vectors is None or not metadata:
        return None, []
    keep_indices = [
        i
        for i, row in enumerate(metadata)
        if _doc_name_from_meta(row) in doc_names_to_keep
    ]
    if not keep_indices:
        return None, []
    kept_vectors = np.asarray(vectors[keep_indices], dtype=np.float32)
    kept_meta = [dict(metadata[i]) for i in keep_indices]
    for i, row in enumerate(kept_meta):
        row["vector_id"] = i
    return kept_vectors, kept_meta


def _combine_vectors(
    kept_vectors: Optional[np.ndarray],
    new_vectors: Optional[np.ndarray],
) -> Optional[np.ndarray]:
    parts = [v for v in (kept_vectors, new_vectors) if v is not None and int(v.shape[0]) > 0]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0].astype(np.float32)
    return np.vstack(parts).astype(np.float32)


# -------------------------------------------------------------------
# Main ingestion
# -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest documents, compute embeddings, and export a portable vector store ZIP."
    )

    parser.add_argument("--docs_dir", required=True)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("--zip_out", default="")
    parser.add_argument("--index_name", default="document_text")
    parser.add_argument("--image_index_name", default="document_images")
    parser.add_argument("--enable_multitenancy", action="store_true")
    parser.add_argument("--tenant_name", default="")
    parser.add_argument("--image_tenant_name", default="Img")

    parser.add_argument("--embedding_model", required=True, help="Path to local embedding GGUF file.")
    parser.add_argument("--embedding_max_length", type=int, default=512)
    parser.add_argument("--embedding_batch_size", type=int, default=8)

    # Chunking defaults.
    parser.add_argument("--chunk_size_chars", type=int, default=1500)
    parser.add_argument("--chunk_overlap_chars", type=int, default=200)
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Reuse already indexed documents in the output store and ingest only new/changed documents.",
    )

    parser.add_argument(
        "--docling_force_backend_text",
        action="store_true",
        help="PDF only: use embedded text layer and disable OCR (faster; use for non-scanned PDFs).",
    )
    parser.add_argument(
        "--docling_embed_markdown_images",
        action="store_true",
        help="Embed pictures as base64 in Markdown (much larger; optional).",
    )
    parser.add_argument(
        "--docling_ocr_always",
        action="store_true",
        help="PDF: skip pdftotext probe and always run OCR (slow on CPU).",
    )

    parser.add_argument("--llm_model", default="")
    parser.add_argument("--llm_device", default="cpu")

    parser.add_argument(
        "--assign_ground_ids",
        action="store_true",
        help="After chunking, call LLM to assign informational ground_id per chunk (updates metadata before export).",
    )
    parser.add_argument(
        "--ground_id_after_each_doc",
        action="store_true",
        help="Run ground_id assignment per document; otherwise once after all documents. Requires --assign_ground_ids.",
    )
    parser.add_argument("--ground_id_window_size", type=int, default=100)
    parser.add_argument("--ground_id_overlap", type=int, default=10)
    parser.add_argument(
        "--ground_id_max_workers",
        type=int,
        default=1,
        help="Parallel LLM processes (each loads the GGUF). 1 = single in-process model.",
    )
    parser.add_argument("--ground_id_llm_n_ctx", type=int, default=8192)
    parser.add_argument("--ground_id_llm_max_new_tokens", type=int, default=8192)
    parser.add_argument("--ground_id_temperature", type=float, default=0.1)
    parser.add_argument("--ground_id_top_p", type=float, default=0.9)
    parser.add_argument("--ground_id_repeat_penalty", type=float, default=1.05)

    args = parser.parse_args()
    get_logger("yuktra_qna", log_dir=_INGEST_LOG_DIR, also_console=False)
    log = get_logger(
        "yuktra_docmgmt.ingest",
        log_dir=_INGEST_LOG_DIR,
        also_console=True,
        console_stream=sys.stdout,
    )
    log.propagate = False
    index_name = _safe_name(args.index_name, default="document_text")
    tenant_name = _safe_name(args.tenant_name, default="default_tenant")
    image_index_name = _safe_name(args.image_index_name, default="document_images")
    image_tenant_name = _safe_name(args.image_tenant_name, default="Img")

    if args.enable_multitenancy and not (args.tenant_name or "").strip():
        raise SystemExit("--tenant_name is required when --enable_multitenancy is set")

    if args.assign_ground_ids:
        lm = (args.llm_model or "").strip()
        if not lm:
            raise SystemExit("--assign_ground_ids requires --llm_model (path to chat/instruct GGUF)")
        if not os.path.isfile(lm):
            raise SystemExit(f"--llm_model not found: {lm}")
        if args.ground_id_window_size <= 0:
            raise SystemExit("--ground_id_window_size must be > 0")
        if args.ground_id_overlap < 0 or args.ground_id_overlap >= args.ground_id_window_size:
            raise SystemExit("--ground_id_overlap must satisfy 0 <= overlap < ground_id_window_size")
        if args.ground_id_max_workers < 1:
            raise SystemExit("--ground_id_max_workers must be >= 1")

    if args.enable_multitenancy:
        final_out_dir = os.path.join(args.out_dir, tenant_name, index_name)
        final_image_out_dir = os.path.join(args.out_dir, image_tenant_name, image_index_name)
    else:
        final_out_dir = os.path.join(args.out_dir, index_name)
        final_image_out_dir = os.path.join(args.out_dir, image_index_name)

    _ingest_total_chunks = 0
    log_process_start(
        log,
        "document_ingestion",
        extra=f"out={os.path.abspath(final_out_dir)} docs={os.path.abspath(args.docs_dir)}",
    )
    try:
        log.info(
            "ingest_start docs_dir=%s final_out_dir=%s index_name=%s multitenancy=%s tenant_name=%s "
            "chunking=markdown docling_force_backend_text=%s docling_embed_md_images=%s "
            "docling_ocr_always=%s embedding_model=%s llm_model=%s zip_out=%s "
            "assign_ground_ids=%s ground_id_after_each_doc=%s ground_id_window=%d ground_id_overlap=%d "
            "ground_id_max_workers=%d incremental=%s",
            os.path.abspath(args.docs_dir),
            os.path.abspath(final_out_dir),
            index_name,
            bool(args.enable_multitenancy),
            tenant_name if args.enable_multitenancy else "",
            bool(args.docling_force_backend_text),
            bool(args.docling_embed_markdown_images),
            bool(args.docling_ocr_always),
            args.embedding_model,
            (args.llm_model or "").strip(),
            (args.zip_out or "").strip(),
            bool(args.assign_ground_ids),
            bool(args.ground_id_after_each_doc),
            int(args.ground_id_window_size),
            int(args.ground_id_overlap),
            int(args.ground_id_max_workers),
            bool(args.incremental),
        )

        # -------------------------------------------------------------------
        # Load embedding model
        # -------------------------------------------------------------------

        doc_paths = iter_documents(args.docs_dir)
        if not doc_paths:
            log.error("ingest_no_documents docs_dir=%s", os.path.abspath(args.docs_dir))
            raise SystemExit(f"No supported documents found in: {args.docs_dir}")

        log.info("ingest_document_discovery count=%d", len(doc_paths))
        for i, p in enumerate(doc_paths[:30]):
            log.info("ingest_document_list #%d path=%s", i + 1, p)
        if len(doc_paths) > 30:
            log.info("ingest_document_list ... %d more paths omitted", len(doc_paths) - 30)

        if not os.path.isfile(args.embedding_model):
            raise SystemExit(f"Embedding GGUF not found: {args.embedding_model}")

        doc_fingerprints = {os.path.basename(p): _file_sha256(p) for p in doc_paths}
        doc_names_current = set(doc_fingerprints.keys())
        doc_paths_to_process = list(doc_paths)
        kept_text_vectors: Optional[np.ndarray] = None
        kept_text_metadata: List[Dict[str, Any]] = []
        kept_image_vectors: Optional[np.ndarray] = None
        kept_image_metadata: List[Dict[str, Any]] = []

        if args.incremental:
            existing_text_vectors, existing_text_metadata, _ = _load_existing_vectors(final_out_dir, log)
            existing_image_vectors, existing_image_metadata_all, _ = _load_existing_vectors(final_image_out_dir, log)

            existing_by_doc: Dict[str, List[Dict[str, Any]]] = {}
            for row in existing_text_metadata:
                doc_name0 = _doc_name_from_meta(row)
                if doc_name0:
                    existing_by_doc.setdefault(doc_name0, []).append(row)

            doc_names_to_process: set[str] = set()
            doc_names_to_keep: set[str] = set()
            for path in doc_paths:
                doc_name = os.path.basename(path)
                rows = existing_by_doc.get(doc_name, [])
                if _metadata_matches_file(rows, doc_fingerprints[doc_name]):
                    doc_names_to_keep.add(doc_name)
                else:
                    doc_names_to_process.add(doc_name)

            doc_paths_to_process = [p for p in doc_paths if os.path.basename(p) in doc_names_to_process]
            kept_text_vectors, kept_text_metadata = _filter_kept_rows(
                existing_text_vectors,
                existing_text_metadata,
                doc_names_to_keep,
            )
            kept_image_vectors, kept_image_metadata = _filter_kept_rows(
                existing_image_vectors,
                existing_image_metadata_all,
                doc_names_to_keep,
            )

            log.info(
                "incremental_plan current_docs=%d process_docs=%d keep_docs=%d kept_text_rows=%d kept_image_rows=%d",
                len(doc_paths),
                len(doc_paths_to_process),
                len(doc_names_to_keep),
                len(kept_text_metadata),
                len(kept_image_metadata),
            )
            for p in doc_paths_to_process[:30]:
                log.info("incremental_process_doc path=%s", p)
            if not doc_paths_to_process:
                log.info("incremental_no_new_or_changed_documents")

        all_chunks: List[str] = []
        metadata: List[Dict[str, Any]] = []
        image_texts: List[str] = []
        image_metadata: List[Dict[str, Any]] = []
        doc_ingest_results: Dict[str, Dict[str, Any]] = {}

        # -------------------------------------------------------------------
        # Document loop
        # -------------------------------------------------------------------

        for path in doc_paths_to_process:
            doc_name = os.path.basename(path)
            ext = os.path.splitext(path)[1].lower()
            doc_fingerprint = doc_fingerprints.get(doc_name) or _file_sha256(path)
            log.info("ingest_document_start path=%s ext=%s", path, ext)

            produced = 0
            used_mode = "markdown"
            doc_meta_start = len(metadata)

            try:
                if ext in (".pdf", ".docx"):
                    log.info("ingest_chunking_try markdown_docling doc=%s", doc_name)
                    markdown_raw, md_chunks = _load_markdown_chunks_via_docling(
                        path,
                        chunk_size_chars=args.chunk_size_chars,
                        chunk_overlap_chars=args.chunk_overlap_chars,
                        force_backend_text=args.docling_force_backend_text,
                        embed_images=args.docling_embed_markdown_images,
                        pdf_try_native_first=not args.docling_ocr_always,
                    )
                    used_mode = "markdown_docling"
                    extracted_images = _extract_embedded_images_from_markdown(
                        markdown_raw,
                        doc_name=doc_name,
                        doc_path=path,
                    )
                    for im in extracted_images:
                        im_row = dict(im)
                        im_row["vector_id"] = len(kept_image_metadata) + len(image_texts)
                        im_row["doc_fingerprint"] = doc_fingerprint
                        try:
                            st = os.stat(path)
                            im_row["doc_size"] = int(st.st_size)
                            im_row["doc_mtime_ns"] = int(st.st_mtime_ns)
                        except OSError:
                            pass
                        image_texts.append(str(im_row.get("text") or ""))
                        image_metadata.append(im_row)
                    if extracted_images:
                        log.info(
                            "ingest_embedded_images_found doc=%s images=%d",
                            doc_name,
                            len(extracted_images),
                        )
                elif ext in (".txt", ".md", ".markdown"):
                    log.info("ingest_chunking_try markdown_text_file doc=%s", doc_name)
                    md_chunks = _load_markdown_chunks_from_text_file(
                        path,
                        chunk_size_chars=args.chunk_size_chars,
                        chunk_overlap_chars=args.chunk_overlap_chars,
                    )
                    used_mode = "markdown_text"
                elif ext == ".mp4":
                    log.info("ingest_chunking_try video_transcript doc=%s", doc_name)
                    md_chunks = _load_markdown_chunks_from_video_file(
                        path,
                        chunk_size_chars=args.chunk_size_chars,
                        chunk_overlap_chars=args.chunk_overlap_chars,
                        log=log,
                    )
                    used_mode = "video_transcript"
                else:
                    log.error("ingest_unsupported_extension doc=%s ext=%s", doc_name, ext)
                    print(f"[error] Unsupported file type (skipped): {doc_name}")
                    doc_ingest_results[doc_name] = {
                        "status": "failed",
                        "chunks": 0,
                        "error": f"unsupported extension {ext}",
                        "mode": used_mode,
                    }
                    continue

                pending_texts: List[str] = []
                pending_meta: List[Dict[str, Any]] = []

                for chunk_idx, hc in enumerate(md_chunks):
                    content = (hc.get("content") or "").strip()
                    if not content:
                        continue

                    heading = (hc.get("section_heading") or "").strip()
                    if heading and _markdown_first_line_plain(content) == heading.strip().lower():
                        text_for_embedding = content
                    elif heading:
                        text_for_embedding = f"{heading}\n\n{content}"
                    else:
                        text_for_embedding = content

                    vec_id = len(kept_text_metadata) + len(all_chunks) + len(pending_texts)
                    try:
                        st = os.stat(path)
                        doc_size = int(st.st_size)
                        doc_mtime_ns = int(st.st_mtime_ns)
                    except OSError:
                        doc_size = 0
                        doc_mtime_ns = 0
                    pending_texts.append(text_for_embedding)
                    meta_row: Dict[str, Any] = {
                        "vector_id": vec_id,
                        "doc_name": doc_name,
                        "doc_path": path,
                        "doc_fingerprint": doc_fingerprint,
                        "doc_size": doc_size,
                        "doc_mtime_ns": doc_mtime_ns,
                        "chunk_index": chunk_idx,
                        "text": text_for_embedding,
                        "raw_content": content,
                        "section_path": [heading] if heading else [],
                        "section_path_str": heading,
                        "is_table": "|" in content and "---" in content,
                        "level_depth": 0,
                        "page_number": int(hc.get("page_number", 0) or 0),
                        "chunk_type": str(hc.get("chunk_type", "markdown")),
                        "ingest_mode": used_mode,
                    }
                    ts_val = hc.get("timestamp")
                    if isinstance(ts_val, str) and ts_val.strip():
                        meta_row["timestamp"] = ts_val.strip()
                        if "start_sec" in hc:
                            try:
                                meta_row["start_sec"] = int(hc.get("start_sec") or 0)
                            except (TypeError, ValueError):
                                pass
                        if "end_sec" in hc:
                            try:
                                meta_row["end_sec"] = int(hc.get("end_sec") or 0)
                            except (TypeError, ValueError):
                                pass
                    pending_meta.append(meta_row)

                _forward_fill_section_paths_and_texts(pending_texts, pending_meta)
                all_chunks.extend(pending_texts)
                metadata.extend(pending_meta)
                produced = len(pending_texts)

                if args.assign_ground_ids and args.ground_id_after_each_doc and produced > 0:
                    log.info(
                        "ingest_phase=ground_id_per_doc doc=%s slice=[%d,%d)",
                        doc_name,
                        doc_meta_start,
                        len(metadata),
                    )
                    assign_ground_ids_slice(
                        metadata,
                        doc_meta_start,
                        len(metadata),
                        llm_model_path=(args.llm_model or "").strip(),
                        log=log,
                        window_size=int(args.ground_id_window_size),
                        overlap=int(args.ground_id_overlap),
                        max_workers=int(args.ground_id_max_workers),
                        llm_n_ctx=int(args.ground_id_llm_n_ctx),
                        llm_max_new_tokens=int(args.ground_id_llm_max_new_tokens),
                        temperature=float(args.ground_id_temperature),
                        top_p=float(args.ground_id_top_p),
                        repeat_penalty=float(args.ground_id_repeat_penalty),
                        scope_doc_name=doc_name,
                    )

                if produced <= 0:
                    doc_ingest_results[doc_name] = {
                        "status": "failed",
                        "chunks": 0,
                        "error": "no chunks produced",
                        "mode": used_mode,
                    }
                    log.error("ingest_no_chunks_for_doc doc=%s mode=%s", doc_name, used_mode)
                else:
                    doc_ingest_results[doc_name] = {
                        "status": "ok",
                        "chunks": produced,
                        "mode": used_mode,
                    }

                log.info(
                    "ingest_chunking_ok mode=%s doc=%s raw_chunks=%d kept_chunks=%d",
                    used_mode,
                    doc_name,
                    len(md_chunks),
                    produced,
                )

            except Exception as e:
                log.error(
                    "ingest_chunking_fail doc=%s err=%s",
                    doc_name,
                    e,
                    exc_info=True,
                )
                print(f"[error] Markdown chunking failed for {doc_name}: {e}")
                doc_ingest_results[doc_name] = {
                    "status": "failed",
                    "chunks": 0,
                    "error": str(e),
                    "mode": used_mode,
                }

            print(f"Loaded {doc_name}: {produced} chunks ({used_mode})")
            log.info(
                "ingest_document_done doc=%s chunks=%d mode=%s cumulative_chunks=%d",
                doc_name,
                produced,
                used_mode,
                len(all_chunks),
            )

        # -------------------------------------------------------------------
        # Embedding
        # -------------------------------------------------------------------

        if not all_chunks and not kept_text_metadata:
            log.error("ingest_no_chunks_produced")
            raise SystemExit("No chunks were produced.")

        if args.assign_ground_ids and not args.ground_id_after_each_doc:
            log.info(
                "ingest_phase=ground_id_full_index slice=[0,%d) max_workers=%d",
                len(metadata),
                int(args.ground_id_max_workers),
            )
            assign_ground_ids_slice(
                metadata,
                0,
                len(metadata),
                llm_model_path=(args.llm_model or "").strip(),
                log=log,
                window_size=int(args.ground_id_window_size),
                overlap=int(args.ground_id_overlap),
                max_workers=int(args.ground_id_max_workers),
                llm_n_ctx=int(args.ground_id_llm_n_ctx),
                llm_max_new_tokens=int(args.ground_id_llm_max_new_tokens),
                temperature=float(args.ground_id_temperature),
                top_p=float(args.ground_id_top_p),
                repeat_penalty=float(args.ground_id_repeat_penalty),
                scope_doc_name="",
            )

        emb_prompt_style = (
            EMBEDDING_PROMPT_STYLE_EMBEDDINGGEMMA
            if is_embeddinggemma_model(args.embedding_model)
            else EMBEDDING_PROMPT_STYLE_PLAIN
        )

        model = None
        if all_chunks or image_texts:
            n_gpu = _dm_n_gpu_layers()
            log.info(
                "ingest_phase=load_llamacpp_embedding_model n_gpu_layers=%s",
                n_gpu,
            )
            model = load_llamacpp_embedding_model(
                args.embedding_model,
                n_ctx=2048,
                n_threads=_runtime_threads(),
                n_batch=256,
                verbose=False,
                n_gpu_layers=n_gpu,
            )

        new_vectors: Optional[np.ndarray] = None
        if all_chunks:
            log.info(
                "ingest_phase=embed_chunks num_chunks=%d emb_prompt_style=%s max_length=%d batch_size=%d",
                len(all_chunks),
                emb_prompt_style,
                args.embedding_max_length,
                args.embedding_batch_size,
            )
            new_vectors = embed_texts_llamacpp(
                texts=all_chunks,
                llm=model,
                batch_size=args.embedding_batch_size,
                embedding_prompt_style=emb_prompt_style,
                embedding_prompt_role="document",
            )
        else:
            log.info("ingest_phase=embed_chunks skipped_no_new_text_chunks")

        vectors = _combine_vectors(kept_text_vectors, new_vectors)
        if vectors is None:
            log.error("ingest_no_vectors_available")
            raise SystemExit("No vectors were available to save.")
        metadata = kept_text_metadata + metadata
        for i, row in enumerate(metadata):
            row["vector_id"] = i

        # -------------------------------------------------------------------
        # Save vector store
        # -------------------------------------------------------------------

        config: Dict[str, Any] = {
            "embedding_model": args.embedding_model,
            "embedding_max_length": args.embedding_max_length,
            "embedding_prompt_style": emb_prompt_style,
            "chunk_size_chars": args.chunk_size_chars,
            "chunk_overlap_chars": args.chunk_overlap_chars,
            "chunking_mode": "markdown",
            "docling_force_backend_text": bool(args.docling_force_backend_text),
            "docling_embed_markdown_images": bool(args.docling_embed_markdown_images),
            "docling_ocr_always": bool(args.docling_ocr_always),
            "video_ingestion_enabled": True,
            "video_transcript_engine": "whisper.cpp",
            "video_whisper_lang": os.environ.get(
                "YUKTRA_VIDEO_WHISPER_LANG",
                os.environ.get("YUKTRA_WHISPER_LANG", "auto"),
            ),
            "incremental_ingestion": bool(args.incremental),
            "docs_total": len(doc_names_current),
            "docs_processed_this_run": len(doc_paths_to_process),
            "docs_reused_this_run": len(doc_names_current) - len(doc_paths_to_process),
            "llm_model": args.llm_model,
            "llm_device": args.llm_device,
            "num_chunks": int(vectors.shape[0]),
            "embedding_dim": int(vectors.shape[1]),
            "similarity": "cosine_dot (vectors are L2-normalized)",
            "index_name": index_name,
            "multitenancy_enabled": bool(args.enable_multitenancy),
            "tenant_name": tenant_name if args.enable_multitenancy else "",
        }
        # Query/runtime tuning (single place to edit for Streamlit + CLI after export).
        config.update(runtime_defaults_subset())
        config["embedding_max_length"] = args.embedding_max_length
        if args.assign_ground_ids:
            config["ground_id_assigned"] = True
            config["ground_id_after_each_doc"] = bool(args.ground_id_after_each_doc)
            config["ground_id_window_size"] = int(args.ground_id_window_size)
            config["ground_id_overlap"] = int(args.ground_id_overlap)
            config["ground_id_max_workers"] = int(args.ground_id_max_workers)

        log.info(
            "ingest_phase=save_vector_store out_dir=%s shape=%s sample_text_preview=%r",
            os.path.abspath(final_out_dir),
            getattr(vectors, "shape", None),
            pipeline_log_preview((all_chunks[0] if all_chunks else metadata[0].get("text", "")), max_chars=400)
            if metadata
            else "",
        )
        save_vector_store(final_out_dir, vectors=vectors, metadata=metadata, config=config)
        print(f"Exported vector store to: {final_out_dir}")
        log.info("ingest_export_done path=%s", os.path.abspath(final_out_dir))

        new_image_vectors: Optional[np.ndarray] = None
        if image_texts:
            log.info(
                "ingest_images phase=embed image_rows=%d image_tenant=%s image_index=%s",
                len(image_texts),
                image_tenant_name if args.enable_multitenancy else "",
                image_index_name,
            )
            new_image_vectors = embed_texts_llamacpp(
                texts=image_texts,
                llm=model,
                batch_size=args.embedding_batch_size,
                embedding_prompt_style=emb_prompt_style,
                embedding_prompt_role="document",
            )
        image_vectors = _combine_vectors(kept_image_vectors, new_image_vectors)
        image_metadata = kept_image_metadata + image_metadata
        for i, row in enumerate(image_metadata):
            row["vector_id"] = i

        if image_vectors is not None and image_metadata:
            image_config: Dict[str, Any] = {
                "embedding_model": args.embedding_model,
                "embedding_max_length": args.embedding_max_length,
                "embedding_prompt_style": emb_prompt_style,
                "index_name": image_index_name,
                "multitenancy_enabled": bool(args.enable_multitenancy),
                "tenant_name": image_tenant_name if args.enable_multitenancy else "",
                "image_index": True,
                "image_source_mode": "docling_markdown_embedded_base64",
                "incremental_ingestion": bool(args.incremental),
                "docs_total": len(doc_names_current),
                "docs_processed_this_run": len(doc_paths_to_process),
                "docs_reused_this_run": len(doc_names_current) - len(doc_paths_to_process),
                "num_chunks": int(image_vectors.shape[0]),
                "embedding_dim": int(image_vectors.shape[1]),
                "similarity": "cosine_dot (vectors are L2-normalized)",
            }
            image_config.update(runtime_defaults_subset())
            image_config["embedding_max_length"] = args.embedding_max_length
            save_vector_store(
                final_image_out_dir,
                vectors=image_vectors,
                metadata=image_metadata,
                config=image_config,
            )
            print(f"Exported image vector store to: {final_image_out_dir}")
            log.info("ingest_images export_done path=%s", os.path.abspath(final_image_out_dir))
        else:
            os.makedirs(final_image_out_dir, exist_ok=True)
            log.info("ingest_images none_found_or_embedded_disabled")

        # -------------------------------------------------------------------
        # Optional ZIP
        # -------------------------------------------------------------------

        if args.zip_out:
            import zipfile

            zip_path = args.zip_out
            log.info("ingest_phase=zip_start zip_path=%s", os.path.abspath(zip_path))
            os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(final_out_dir):
                    for name in files:
                        file_path = os.path.join(root, name)
                        rel_path = os.path.relpath(file_path, final_out_dir)
                        zf.write(file_path, arcname=os.path.join(os.path.basename(final_out_dir), rel_path))

            print(f"Wrote ZIP: {zip_path}")
            log.info("ingest_zip_done zip_path=%s", os.path.abspath(zip_path))

        if doc_ingest_results:
            _write_ingest_doc_status(final_out_dir, doc_ingest_results)

        _ingest_total_chunks = len(metadata)
        failed_docs = [
            name
            for name, row in doc_ingest_results.items()
            if str(row.get("status") or "").strip().lower() != "ok"
        ]
        log.info(
            "ingest_complete final_out_dir=%s total_chunks=%d new_chunks=%d reused_chunks=%d failed_docs=%d",
            os.path.abspath(final_out_dir),
            len(metadata),
            len(all_chunks),
            len(kept_text_metadata),
            len(failed_docs),
        )
        if failed_docs:
            log.error("ingest_document_failures docs=%s", failed_docs)
            raise SystemExit(1)
    except Exception:
        log.exception("ingest_fatal_error")
        raise
    finally:
        log_process_end(
            log,
            "document_ingestion",
            extra=f"total_chunks={_ingest_total_chunks}",
        )



if __name__ == "__main__":
    main()
