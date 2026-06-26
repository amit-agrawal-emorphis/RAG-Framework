import errno
import html as html_module
import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse
import uuid

# Streamlit only adds this file's directory to sys.path; add ``doc-qna/backend`` and repo root.
_frontend_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.abspath(os.path.join(_frontend_dir, "..", "backend"))
_repo_root = os.path.abspath(os.path.join(_frontend_dir, "..", ".."))
for _p in (_backend_root, _repo_root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import base64
import hashlib
import re
from io import BytesIO
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import markdown as markdown_lib
except ImportError:
    markdown_lib = None

import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_pdf_viewer import pdf_viewer as _pdf_js_viewer
except ImportError:
    _pdf_js_viewer = None

from logger import get_logger
from streamlit_theme import APP_CSS


# ================================
# CONFIG
# ================================
# Frontend config only. AI/RAG runtime is delegated to the FastAPI backend (``qna_service``).

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(APP_DIR, "..", ".."))
# Follow the DATA_DIR env (set by the launcher) so paths are correct no matter where
# this file is shipped (e.g. release\frontend\app\, where REPO_ROOT would be wrong).
DATA_DIR = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(REPO_ROOT, "data")
INGESTED_DIR = os.path.join(DATA_DIR, "Ingested")
UI_HEADER_LOGO_PATH = os.path.join(DATA_DIR, "ui", "header_logo.png")
UI_HISTORY_ICON_PATH = os.path.join(DATA_DIR, "ui", "History.png")
UI_NEW_ICON_PATH = os.path.join(DATA_DIR, "ui", "New_chat.png")
UI_PRODUCT_ICON_PATH = os.path.join(DATA_DIR, "ui", "Product.png")
UI_MIC_ICON_PATH = os.path.join(DATA_DIR, "ui", "mic.png")
UI_SPEAKER_ICON_PATH = os.path.join(DATA_DIR, "ui", "speaker.png")

OUT_OF_DOMAIN_REPLY = (
    "Hi! I'm the Equipment Intelligence assistant.\n"
    "Ask a query related to equipment/manual-related question?"
)
QNA_API_BASE = os.environ.get("YUKTRA_QNA_API_BASE", "http://127.0.0.1:8009").rstrip("/")
# Max seconds to wait for the server to *finish* /chat/ask (large LLM runs); does not add delay per request.
CHAT_ASK_TIMEOUT_SEC = int(os.environ.get("YUKTRA_QNA_CHAT_ASK_TIMEOUT_SEC", "300"))
STT_TIMEOUT_SEC = int(os.environ.get("YUKTRA_QNA_STT_TIMEOUT_SEC", "180"))
TTS_TIMEOUT_SEC = int(os.environ.get("YUKTRA_QNA_TTS_TIMEOUT_SEC", "180"))
IMAGE_RENDER_WIDTH = int(os.environ.get("YUKTRA_QNA_IMAGE_RENDER_WIDTH", "320"))
VIDEO_RENDER_WIDTH = int(os.environ.get("YUKTRA_QNA_VIDEO_RENDER_WIDTH", "560"))
VKB_MAX_WIDTH_PX = int(os.environ.get("YUKTRA_QNA_VKB_MAX_WIDTH", "720"))
VKB_WIDTH_RATIO = float(os.environ.get("YUKTRA_QNA_VKB_WIDTH_RATIO", "0.62"))
INLINE_IMAGE_INSERT_AFTER_CHARS = int(os.environ.get("YUKTRA_QNA_INLINE_IMAGE_INSERT_AFTER_CHARS", "220"))
TTS_STREAM_PREFETCH_ENABLED = os.environ.get("YUKTRA_QNA_TTS_STREAM_PREFETCH", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TTS_STREAM_PREFETCH_MIN_CHARS = int(os.environ.get("YUKTRA_QNA_TTS_STREAM_PREFETCH_MIN_CHARS", "180"))
TTS_STREAM_PREFETCH_DELTA_CHARS = int(os.environ.get("YUKTRA_QNA_TTS_STREAM_PREFETCH_DELTA_CHARS", "180"))
TTS_STREAM_PREFETCH_INTERVAL_SEC = float(os.environ.get("YUKTRA_QNA_TTS_STREAM_PREFETCH_INTERVAL_SEC", "2.0"))
# Use an invisible marker so users do not see STT tags in the chat input.
STT_CHAT_PREFIX = os.environ.get("YUKTRA_QNA_STT_CHAT_PREFIX", "\u2063yukt_stt\u2063")
LEGACY_STT_CHAT_PREFIX = "[[STT]] "
STREAM_TTS_PENDING_MSG_KEY = "tts_speak_pending_streaming"
# Keep stream rendering interactive by default so chat generation and UI actions
# (e.g. TTS speak/pause on older messages) run in parallel.
USE_FRAGMENT_STREAM = os.environ.get("YUKTRA_QNA_USE_FRAGMENT_STREAM", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_INLINE_IMAGE_TAG_RE = re.compile(r"\[\[YUKTRA_IMAGE_(\d+)\]\]")
# ``Manual.pdf, page 9`` / ``[Manual.pdf, page 9]`` / comma or semicolon / optional ``**`` around
# ``page N`` or after ``[``. ``(?P<bracket_close>\s*\])?`` keeps the closing ``]`` on bracket citations.
# ``dn`` excludes ``[`` / ``]`` so we do not match across unrelated brackets.
_MARKDOWN_PDF_PAGE_REF_RE = re.compile(
    r"(?i)(?P<lead>\[\s*\*{0,2})?(?P<dn>[^\[\],;\n]+\.pdf)\s*\*{0,2}\s*[,;]\s*\*{0,2}\s*page\s+(?P<pg>\d+)\b\*{0,2}(?P<bracket_close>\s*\])?"
)
# ``[Passage 2], page 4`` / ``[Passage 2] **page 4**`` (page *outside* the first bracket).
_MARKDOWN_PASSAGE_PAGE_REF_RE = re.compile(
    r"(?i)(?P<prefix>\[[^\]]+\]\s*,?\s*)\*{0,2}\s*page\s+(?P<pg>\d+)\b\*{0,2}"
)
# ``[Passage 1, page 8]`` — page *inside* the same bracket as ``Passage k`` (common in list citations).
_MARKDOWN_PASSAGE_INLINE_PAGE_RE = re.compile(
    r"(?i)\[\s*\*{0,2}(?P<tag>Passage\s+\d+)\s*\*{0,2}\s*,\s*\*{0,2}\s*page\s+(?P<pg>\d+)\s*\*{0,2}\s*\]"
)
# Sole-PDF broad fallback: catches ``page9``, ``Page 12``, ``[Page 12]``, ``pg 7``, ``p.8``.
# Applied only when exactly one on-disk PDF source is available for the message.
_SOLE_GENERIC_PAGE_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?P<open>\[?\s*)(?P<label>page|pg|p\.)\s*[:#-]?\s*(?P<pg>\d{1,4})(?P<close>\s*\]?)(?![A-Za-z0-9_])"
)
# Sole-PDF only: ``8. … mechanism page 15`` (``page N`` at end of a numbered list line, no ``.pdf`` on that line).
_SOLE_NUMBERED_LINE_TAIL_PAGE_RE = re.compile(
    r"(?im)(?P<hdr>^\s*\d+\.[^\n]*?)\bpage\s+(?P<pg>\d+)\s*\*{0,2}\s*$"
)
# Sole-PDF only: ``…reset. page 16`` (``page N`` after ``.`` / ``!`` / ``?`` / ``…`` + space, before line/bracket end).
_SOLE_DOT_LEAD_PAGE_RE = re.compile(
    r"(?i)(?P<pfx>(?:[\.!?…]|\.{3})\s+)page\s+(?P<pg>\d+)\b\*{0,2}(?=\s*(?:$|\n|\]))"
)
_LEADING_SOURCE_PHRASE_RE = re.compile(
    r"(?i)^(according\s+to|from|in|per|see|refer\s+to)\s+",
)
# ``Video.mp4, 01:00-02:00`` / ``Video.mp4, timestamp 01:00-02:00`` / ``[Video.mp4, 01:00-02:00]`` /
# optional ``**`` around the keyword or the timestamp. Matches any common video extension.
# ``ts`` accepts both ``MM:SS-MM:SS`` and ``HH:MM:SS-HH:MM:SS``.
_MARKDOWN_VIDEO_TIME_REF_RE = re.compile(
    r"(?i)(?P<lead>\[\s*\*{0,2})?"
    r"(?P<dn>[^\[\],;\n]+\.(?:mp4|mov|mkv|avi|webm))"
    r"\s*\*{0,2}\s*[,;]\s*\*{0,2}\s*"
    r"(?:(?:timestamp|time|at)\s*\*{0,2}\s+)?"
    r"\*{0,2}\s*"
    r"(?P<ts>\d{1,2}:\d{2}(?::\d{2})?\s*[–—-]\s*\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*\*{0,2}"
    r"(?P<bracket_close>\s*\])?"
)
# Sole-video fallback: bare ``MM:SS-MM:SS`` token (or ``[MM:SS-MM:SS]``) when exactly one video source is available.
_SOLE_VIDEO_TIME_TOKEN_RE = re.compile(
    r"(?P<open>\[?\s*\*{0,2}\s*)(?P<ts>\d{1,2}:\d{2}(?::\d{2})?\s*[–—-]\s*\d{1,2}:\d{2}(?::\d{2})?)(?P<close>\s*\*{0,2}\s*\]?)"
)

_TTS_PREFETCH_LOCK = threading.Lock()
_TTS_PREFETCH_CACHE: dict[str, tuple[bytes, str, str]] = {}
_TTS_PREFETCH_INFLIGHT: dict[str, str] = {}
_TTS_PREFETCH_REQUEST_IDS: dict[str, int] = {}
_TTS_PREFETCH_ALIASES: dict[str, tuple[str, str]] = {}
_TTS_REQUEST_COUNTER = int(time.time() * 1000)
# Max characters per chunk for incremental TTS playback (must mirror JS splitter).
TTS_CHUNK_MAX_CHARS = int(os.environ.get("YUKTRA_QNA_TTS_CHUNK_MAX_CHARS", "900"))
_TTS_SENTENCE_SPLIT_RE = re.compile(r"[^.!?\n]+[.!?\n]+|[^.!?\n]+$", re.DOTALL)


def _strip_leading_source_phrase(title: str) -> str:
    return _LEADING_SOURCE_PHRASE_RE.sub("", (title or "").strip()).strip()


def _next_tts_request_id() -> int:
    global _TTS_REQUEST_COUNTER
    with _TTS_PREFETCH_LOCK:
        _TTS_REQUEST_COUNTER += 1
        return _TTS_REQUEST_COUNTER


def _tts_cache_text(text: str) -> str:
    t = _strip_inline_image_tags(str(text or "")).strip()
    # Light cleanup so Piper reads words, not markdown decoration.
    t = t.replace("**", "").replace("__", "")
    return t


def _tts_text_hash(text: str) -> str:
    return hashlib.sha1(_tts_cache_text(text).encode("utf-8")).hexdigest()


def _get_tts_cached_audio(msg_key: str, text: str) -> tuple[bytes, str]:
    want_hash = _tts_text_hash(text)
    with _TTS_PREFETCH_LOCK:
        cached = _TTS_PREFETCH_CACHE.get(msg_key)
        if not cached:
            return b"", "audio/wav"
        wav_bytes, mime, cached_hash = cached
        if cached_hash != want_hash:
            return b"", "audio/wav"
        return wav_bytes, mime


def _store_tts_cached_audio(msg_key: str, wav_bytes: bytes, mime: str, text_hash: str) -> None:
    with _TTS_PREFETCH_LOCK:
        _TTS_PREFETCH_CACHE[msg_key] = (wav_bytes, mime or "audio/wav", text_hash)


def _bump_tts_request_id_for_play(msg_key: str) -> int:
    """Assign a fresh baseline id before browser chunk playback.

    Background prefetch uses the same ``msg_key`` and monotonic request ids; the API
    cancels any synthesis whose id is below the latest. Reusing the prefetch baseline
    lets later chunk fetches get 409 and stops audio after the first chunk.
    """
    key = str(msg_key or "").strip()
    if not key:
        return _next_tts_request_id()
    rid = _next_tts_request_id()
    with _TTS_PREFETCH_LOCK:
        _TTS_PREFETCH_REQUEST_IDS[key] = rid
    return int(rid)


def _move_tts_cached_audio(old_key: str, new_key: str) -> None:
    old = str(old_key or "").strip()
    new = str(new_key or "").strip()
    if not old or not new or old == new:
        return
    with _TTS_PREFETCH_LOCK:
        if old in _TTS_PREFETCH_CACHE and new not in _TTS_PREFETCH_CACHE:
            _TTS_PREFETCH_CACHE[new] = _TTS_PREFETCH_CACHE[old]
        if old in _TTS_PREFETCH_REQUEST_IDS and new not in _TTS_PREFETCH_REQUEST_IDS:
            _TTS_PREFETCH_REQUEST_IDS[new] = _TTS_PREFETCH_REQUEST_IDS[old]
        if old in _TTS_PREFETCH_INFLIGHT:
            inflight_hash = _TTS_PREFETCH_INFLIGHT[old]
            _TTS_PREFETCH_ALIASES[old] = (new, inflight_hash)
            _TTS_PREFETCH_INFLIGHT[new] = inflight_hash
        _TTS_PREFETCH_CACHE.pop(old, None)
        _TTS_PREFETCH_REQUEST_IDS.pop(old, None)


def _split_tts_chunks(text: str, max_chars: int = TTS_CHUNK_MAX_CHARS) -> list[str]:
    """Split text into TTS-sized chunks at sentence boundaries.

    Mirrors the JS splitter in ``_render_tts_audio_bridge`` so that chunks
    requested by the backend prefetch hit the same Piper LRU cache key as the
    ones the browser later requests during playback.
    """
    txt = _tts_cache_text(text)
    if not txt:
        return []
    cap = max(40, int(max_chars or TTS_CHUNK_MAX_CHARS))
    sentences = [s.strip() for s in _TTS_SENTENCE_SPLIT_RE.findall(txt) if s and s.strip()]
    if not sentences:
        sentences = [txt]
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > cap:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s) if cur else s
        while len(cur) > cap * 2:
            cut = cur.rfind(", ", 0, cap)
            if cut < max(40, cap // 4):
                cut = cap
            chunks.append(cur[:cut].strip())
            cur = cur[cut:].strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def _ensure_tts_prefetch(msg_key: str, text: str) -> None:
    global _TTS_REQUEST_COUNTER
    key = str(msg_key or "").strip()
    txt = _tts_cache_text(text)
    if not key or not txt or _is_likely_hindi_text(txt):
        return
    txt_hash = _tts_text_hash(txt)
    with _TTS_PREFETCH_LOCK:
        cached = _TTS_PREFETCH_CACHE.get(key)
        if cached and cached[2] == txt_hash:
            return
        if _TTS_PREFETCH_INFLIGHT.get(key) == txt_hash:
            return
        # Newer streamed text should supersede older partial audio. The backend
        # cancels lower request ids, so bump the id whenever this key gets a new
        # text hash. Reserve a second id for the priority first-chunk warmup so
        # the full-text job runs after (and at a higher id than) the chunk job.
        _TTS_REQUEST_COUNTER += 2
        chunk_req_id = _TTS_REQUEST_COUNTER - 1
        req_id = _TTS_REQUEST_COUNTER
        _TTS_PREFETCH_REQUEST_IDS[key] = req_id
        _TTS_PREFETCH_INFLIGHT[key] = txt_hash

    def _worker() -> None:
        try:
            # Warm the backend Piper LRU cache with the first chunk first so the
            # browser's chunked playback gets a near-instant cache hit on click.
            chunks = _split_tts_chunks(txt)
            first_chunk = chunks[0] if chunks else ""
            if first_chunk and first_chunk != txt:
                try:
                    _api_tts_synthesize(first_chunk, request_id=chunk_req_id)
                except Exception:
                    pass
            wav_bytes, mime = _api_tts_synthesize(txt, request_id=req_id)
            if wav_bytes:
                with _TTS_PREFETCH_LOCK:
                    alias = _TTS_PREFETCH_ALIASES.get(key)
                    final_key = alias[0] if alias and alias[1] == txt_hash else key
                    if _TTS_PREFETCH_INFLIGHT.get(final_key) != txt_hash:
                        return
                _store_tts_cached_audio(final_key, wav_bytes, mime, txt_hash)
        finally:
            with _TTS_PREFETCH_LOCK:
                alias = _TTS_PREFETCH_ALIASES.get(key)
                final_key = alias[0] if alias and alias[1] == txt_hash else key
                if _TTS_PREFETCH_INFLIGHT.get(key) == txt_hash:
                    _TTS_PREFETCH_INFLIGHT.pop(key, None)
                if _TTS_PREFETCH_INFLIGHT.get(final_key) == txt_hash:
                    _TTS_PREFETCH_INFLIGHT.pop(final_key, None)
                if alias and alias[1] == txt_hash:
                    _TTS_PREFETCH_ALIASES.pop(key, None)

    threading.Thread(target=_worker, daemon=True).start()


def _maybe_schedule_streaming_tts_prefetch(state: dict[str, Any], text: str) -> str:
    if not TTS_STREAM_PREFETCH_ENABLED:
        return ""
    txt = _tts_cache_text(text)
    if len(txt) < max(1, TTS_STREAM_PREFETCH_MIN_CHARS):
        return ""
    if not _is_safe_inline_image_boundary(txt):
        return ""
    now = time.time()
    last_chars = int(state.get("tts_prefetch_chars") or 0)
    last_ts = float(state.get("tts_prefetch_ts") or 0.0)
    enough_new_text = len(txt) - last_chars >= max(1, TTS_STREAM_PREFETCH_DELTA_CHARS)
    enough_time = now - last_ts >= max(0.1, TTS_STREAM_PREFETCH_INTERVAL_SEC)
    if last_chars > 0 and not (enough_new_text and enough_time):
        return ""
    state["tts_prefetch_chars"] = len(txt)
    state["tts_prefetch_ts"] = now
    return txt


def _request_tts_play(msg_key: str, text: str, *, delay_ms: int = 0) -> None:
    """Queue playback for a message; never block UI thread for synthesis."""
    if not msg_key:
        return
    st.session_state.tts_pending_play = {
        "msg_key": msg_key,
        "text": str(text or ""),
        "delay_ms": max(0, int(delay_ms)),
    }


def _is_likely_hindi_text(text: str) -> bool:
    s = str(text or "")
    if not s:
        return False
    return bool(re.search(r"[\u0900-\u097F]", s))


def _resolve_pending_tts_play() -> None:
    pending = st.session_state.get("tts_pending_play")
    if not isinstance(pending, dict):
        return
    msg_key = str(pending.get("msg_key") or "").strip()
    if not msg_key:
        st.session_state.tts_pending_play = None
        return
    txt = str(pending.get("text") or "")
    if _is_likely_hindi_text(txt):
        st.session_state.tts_browser_cmd = {
            "action": "speak_text",
            "msg_key": msg_key,
            "text": txt,
            "api_base": _browser_image_api_base(),
            "request_id": _next_tts_request_id(),
            "preferred_lang": "hi-IN",
        }
        st.session_state.tts_active_msg_key = msg_key
        st.session_state.tts_is_paused = False
        st.session_state.tts_pending_play = None
        return
    cached_wav, cached_mime = _get_tts_cached_audio(msg_key, txt)
    if cached_wav:
        st.session_state.tts_browser_cmd = {
            "action": "play",
            "msg_key": msg_key,
            "api_base": _browser_image_api_base(),
            "audio_base64": base64.b64encode(cached_wav).decode("ascii"),
            "mime_type": cached_mime or "audio/wav",
        }
        st.session_state.tts_active_msg_key = msg_key
        st.session_state.tts_is_paused = False
        st.session_state.tts_pending_play = None
        return
    st.session_state.tts_browser_cmd = {
        "action": "chunk_play",
        "msg_key": msg_key,
        "text": txt,
        "api_base": _browser_image_api_base(),
        "request_id": _bump_tts_request_id_for_play(msg_key),
        "chunk_max_chars": int(TTS_CHUNK_MAX_CHARS),
        "delay_ms": max(0, int(pending.get("delay_ms") or 0)),
    }
    st.session_state.tts_active_msg_key = msg_key
    st.session_state.tts_is_paused = False
    st.session_state.tts_pending_play = None


def _strip_inline_md_noise(s: str) -> str:
    """Remove markdown emphasis markers so ``**Foo.pdf**`` matches sources."""
    return re.sub(r"\*+", "", (s or "")).strip()


def _browser_api_base_override() -> str:
    """Explicit browser-only API root (optional). Takes precedence over auto-detection."""
    return (os.environ.get("YUKTRA_QNA_BROWSER_API_BASE") or "").strip().rstrip("/")


def _qna_api_port_from_base() -> int:
    try:
        pu = urllib.parse.urlparse(str(QNA_API_BASE or "").strip() or "http://127.0.0.1:8009")
        return int(pu.port) if pu.port else 8009
    except Exception:
        return 8009


def _hostname_from_forwarded_host(raw: str) -> str | None:
    """Extract hostname from ``Host`` / ``X-Forwarded-Host`` (first value only)."""
    s = (raw or "").strip()
    if not s:
        return None
    first = s.split(",")[0].strip()
    if first.startswith("["):
        end = first.find("]")
        if end > 1:
            return first[1:end]
        return None
    if ":" in first:
        if first.count(":") == 1:
            host, _, port = first.rpartition(":")
            if port.isdigit():
                h = host.strip()
                return h if h else None
    return first or None


def _streamlit_request_public_hostname() -> str | None:
    """Hostname the browser used to reach Streamlit (skips loopback). Requires ``st.context`` headers."""
    try:
        ctx = getattr(st, "context", None)
        hdrs = getattr(ctx, "headers", None) if ctx is not None else None
        if not hdrs:
            return None
        get = hdrs.get if hasattr(hdrs, "get") else dict(hdrs).get
        raw = (
            get("X-Forwarded-Host")
            or get("x-forwarded-host")
            or get("Host")
            or get("host")
            or ""
        )
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        host = _hostname_from_forwarded_host(str(raw))
        if not host:
            return None
        hl = host.lower()
        if hl in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
            return None
        return host
    except Exception:
        return None


def _streamlit_request_public_scheme(default: str) -> str:
    try:
        ctx = getattr(st, "context", None)
        hdrs = getattr(ctx, "headers", None) if ctx is not None else None
        if not hdrs:
            return default
        get = hdrs.get if hasattr(hdrs, "get") else dict(hdrs).get
        xf = (get("X-Forwarded-Proto") or get("x-forwarded-proto") or "").strip().lower()
        if isinstance(xf, list):
            xf = str(xf[0] or "").lower()
        if xf:
            first = xf.split(",")[0].strip()
            if first in ("https", "http"):
                return first
    except Exception:
        pass
    return default


def _browser_image_api_base() -> str:
    """
    Base URL for browser-originated API calls (TTS fetch, STT client-log, ``/images/{uuid}``).

    ``YUKTRA_QNA_API_BASE`` stays ``http://127.0.0.1:8009`` for server-side Python inside Docker.
    Remote browsers cannot reach that host; when the configured API hostname is loopback,
    we rewrite to the same hostname the user used to open Streamlit plus the API port from
    ``YUKTRA_QNA_API_BASE`` (default 8009). Override entirely with ``YUKTRA_QNA_BROWSER_API_BASE``.
    """
    ovr = _browser_api_base_override()
    if ovr:
        return ovr
    base = str(QNA_API_BASE or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8009"
    try:
        pu = urllib.parse.urlparse(base)
        host = (pu.hostname or "").strip().lower()
        scheme = (pu.scheme or "http").lower()
        port = int(pu.port) if pu.port else _qna_api_port_from_base()
        if host in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            pub = _streamlit_request_public_hostname()
            if pub:
                sch = _streamlit_request_public_scheme(scheme)
                return f"{sch}://{pub}:{port}".rstrip("/")
        if host in ("0.0.0.0", "::"):
            sch = pu.scheme or "http"
            return f"{sch}://127.0.0.1:{port}".rstrip("/")
        return base
    except Exception:
        return base


def _qp_one(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        return str(val[0]).strip() if val else ""
    return str(val).strip()


def _resolve_pdf_path_for_doc_name(
    doc_name_query: str,
    sources: list[dict[str, Any]],
) -> str | None:
    """Match ``doc_name_query`` to a source row with a readable ``doc_path``."""
    q = _strip_inline_md_noise(doc_name_query or "")
    if not q:
        return None
    q_low = q.lower()
    q_base = os.path.basename(q).lower()
    for src in sources:
        if not isinstance(src, dict):
            continue
        name = str(src.get("doc_name") or "").strip()
        p = str(src.get("doc_path") or "").strip()
        if not p or not os.path.isfile(p):
            continue
        nb = os.path.basename(name).lower() if name else ""
        if name.lower() == q_low or name.lower() == q_base or nb == q_base or nb == q_low:
            return p
    return None


_VIDEO_SOURCE_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm")


def _document_basename_index() -> dict[str, str]:
    """basename -> absolute path map across all machines in data/Ingested/*/documents/."""
    out: dict[str, str] = {}
    if not os.path.isdir(INGESTED_DIR):
        return out
    try:
        for machine in sorted(os.listdir(INGESTED_DIR)):
            docs_dir = os.path.join(INGESTED_DIR, machine, "documents")
            if not os.path.isdir(docs_dir):
                continue
            for fn in os.listdir(docs_dir):
                out.setdefault(fn.lower(), os.path.join(docs_dir, fn))
    except Exception:
        return out
    return out


def _pdf_basename_index() -> dict[str, str]:
    return {k: v for k, v in _document_basename_index().items() if k.endswith(".pdf")}


def _resolve_source_doc_path(src: dict[str, Any]) -> str:
    """Resolve source path even if stored history path became stale."""
    p = str(src.get("doc_path") or "").strip()
    if p and os.path.isfile(p):
        return p
    cands: list[str] = []
    if p:
        cands.append(os.path.basename(p))
    n = str(src.get("doc_name") or "").strip()
    if n:
        cands.append(os.path.basename(n))
        if os.path.splitext(n.lower())[1]:
            cands.append(n)
    idx = _document_basename_index()
    for c in cands:
        key = str(c or "").strip().lower()
        if key and key in idx and os.path.isfile(idx[key]):
            return idx[key]
    return ""


def _is_video_source_path(path: str) -> bool:
    return str(path or "").strip().lower().endswith(_VIDEO_SOURCE_EXTS)


def _video_sources_from_sources(sources: list[dict[str, Any]] | None) -> list[tuple[str, str]]:
    if not isinstance(sources, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        p = _resolve_source_doc_path(src)
        if not p or not os.path.isfile(p) or not _is_video_source_path(p):
            continue
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        name = str(src.get("doc_name") or "").strip() or os.path.basename(p)
        out.append((name, p))
    return out


def _sid_query_safe(s: str) -> bool:
    t = (s or "").strip()
    if len(t) < 6 or len(t) > 96:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-]+$", t))


def _maybe_switch_session_from_pdf_link_query() -> None:
    """If the URL opened from an inline page link targets another chat, load that session first."""
    try:
        if _qp_one(st.query_params.get("yukt_pdf_open")) != "1":
            return
    except Exception:
        return
    sid = _qp_one(st.query_params.get("yukt_sid")).strip()
    if not sid or not _sid_query_safe(sid):
        return
    if str(st.session_state.get("session_id") or "") == sid:
        return
    st.session_state.session_id = sid
    st.session_state.messages = []
    _sse_dismiss_active_holder()
    st.session_state.rag_pending = None


def _unique_doc_name_for_passage_links(usable: list[dict[str, Any]]) -> str | None:
    names: set[str] = set()
    for s in usable:
        if not isinstance(s, dict):
            continue
        n = str(s.get("doc_name") or "").strip()
        if n:
            names.add(n)
    if len(names) == 1:
        return next(iter(names))
    return None


def _yukt_pdf_page_href(
    *,
    msg_index: int,
    pg: str,
    dn_for_url: str,
    chat_session_id: str,
    message_id: int | None = None,
    source_index: int | None = None,
) -> str:
    q: dict[str, str] = {
        "yukt_pdf_open": "1",
        "yukt_mi": str(int(msg_index)),
        "yukt_pg": str(pg),
        "yukt_dn": dn_for_url,
    }
    mid = int(message_id) if message_id is not None else 0
    if mid > 0:
        q["yukt_mid"] = str(mid)
    si = int(source_index) if source_index is not None else -1
    if si >= 0:
        q["yukt_si"] = str(si)
    sid = (chat_session_id or "").strip()
    if sid:
        q["yukt_sid"] = sid
    # Use explicit ``./?`` so browser always treats this as query navigation on the same app route.
    return "./?" + urllib.parse.urlencode(q)


def _yukt_pdf_page_deeplink_span(*, label: str, href_q: str) -> str:
    """Compatibility wrapper; not used by modal path."""
    esc = html_module.escape(href_q, quote=True)
    lab = html_module.escape(label, quote=False)
    return f'<span class="yukt-page-ref" data-yukt-deeplink="{esc}">{lab}</span>'


def _yukt_doc_key_for_source(src: dict[str, Any], idx: int) -> str:
    base = os.path.basename(str(src.get("doc_path") or "").strip()).lower()
    if not base:
        base = os.path.basename(str(src.get("doc_name") or "").strip()).lower()
    safe = re.sub(r"[^a-z0-9_.-]+", "-", base).strip("-")
    return f"{safe or 'doc'}-{idx}"


def _assistant_message_anchor_id(msg_index: int | None, message_id: int | None) -> str | None:
    try:
        mid = int(message_id or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid > 0:
        return f"yukt-msg-mid-{mid}"
    if msg_index is None:
        return None
    try:
        mi = int(msg_index)
    except (TypeError, ValueError):
        return None
    if mi < 0:
        return None
    return f"yukt-msg-mi-{mi}"


def _user_message_anchor_id(msg_index: int | None, message_id: int | None) -> str | None:
    try:
        mid = int(message_id or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid > 0:
        return f"yukt-user-mid-{mid}"
    if msg_index is None:
        return None
    try:
        mi = int(msg_index)
    except (TypeError, ValueError):
        return None
    if mi < 0:
        return None
    return f"yukt-user-mi-{mi}"


def _mark_scroll_target_from_link(mi: int, mid_db: int) -> None:
    anchor = _assistant_message_anchor_id(mi if mi >= 0 else None, mid_db if mid_db > 0 else None)
    if anchor:
        st.session_state._yukt_scroll_target = anchor


def _find_source_index_for_doc_name(
    sources: list[dict[str, Any]] | None,
    doc_name_query: str,
) -> int | None:
    if not isinstance(sources, list) or not sources:
        return None
    q = _strip_inline_md_noise(doc_name_query or "")
    if not q:
        return None
    q_low = q.lower()
    q_base = os.path.basename(q).lower()
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            continue
        name = str(src.get("doc_name") or "").strip()
        p = str(src.get("doc_path") or "").strip()
        if not p or not os.path.isfile(p):
            continue
        nb = os.path.basename(name).lower() if name else ""
        if name.lower() == q_low or name.lower() == q_base or nb == q_base or nb == q_low:
            return i
    return None


def _build_inline_pdf_payloads(sources: list[dict[str, Any]] | None) -> tuple[dict[str, str], dict[str, Any]]:
    """Return (doc-name->key, key->payload) for frontend modal PDF opening."""
    name_to_key: dict[str, str] = {}
    payload: dict[str, Any] = {}
    if not isinstance(sources, list):
        return name_to_key, payload
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            continue
        p = _resolve_source_doc_path(src)
        n = str(src.get("doc_name") or "").strip()
        if not p or not os.path.isfile(p) or not p.lower().endswith(".pdf"):
            continue
        # The backend rejects fids whose path escapes Ingested ("Invalid path").
        # A stale/absolute doc_path can point at a different data copy outside the
        # current INGESTED_DIR -> remap to the matching file UNDER INGESTED_DIR.
        _ap = os.path.normpath(os.path.abspath(p))
        _ing = os.path.normpath(os.path.abspath(INGESTED_DIR))
        if not _ap.startswith(_ing + os.sep):
            _cand = _document_basename_index().get(os.path.basename(p).lower())
            if _cand and os.path.isfile(_cand):
                p = _cand
            else:
                continue
        try:
            rel_path = os.path.relpath(p, INGESTED_DIR)
            fid = base64.urlsafe_b64encode(rel_path.encode()).decode().rstrip("=")
        except Exception:
            continue
        key = _yukt_doc_key_for_source(src, i)
        payload[key] = {"name": n or os.path.basename(p), "fid": fid}
        if n:
            name_to_key[n.lower()] = key
            name_to_key[os.path.basename(n).lower()] = key
        name_to_key[os.path.basename(p).lower()] = key
    return name_to_key, payload


def _merge_all_assistant_inline_pdf_payloads(msgs: list[dict[str, Any]]) -> dict[str, Any]:
    """One merged fid map for all assistant messages so a single client bridge can open any cited PDF."""
    merged: dict[str, Any] = {}
    for m in msgs or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        srcs = m.get("sources")
        if not isinstance(srcs, list):
            continue
        _nk, pl = _build_inline_pdf_payloads(srcs)
        for k, v in pl.items():
            merged[k] = v
    return merged


def _build_inline_video_payloads(
    sources: list[dict[str, Any]] | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Return (doc-name->key, key->payload) for frontend modal video opening."""
    name_to_key: dict[str, str] = {}
    payload: dict[str, Any] = {}
    if not isinstance(sources, list):
        return name_to_key, payload
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            continue
        p = _resolve_source_doc_path(src)
        if not p or not os.path.isfile(p) or not _is_video_source_path(p):
            continue
        # Same as PDF: keep the path under INGESTED_DIR so the fid doesn't escape
        # (backend rejects escaping paths with "Invalid path").
        _ap = os.path.normpath(os.path.abspath(p))
        _ing = os.path.normpath(os.path.abspath(INGESTED_DIR))
        if not _ap.startswith(_ing + os.sep):
            _cand = _document_basename_index().get(os.path.basename(p).lower())
            if _cand and os.path.isfile(_cand):
                p = _cand
            else:
                continue
        try:
            rel_path = os.path.relpath(p, INGESTED_DIR)
            fid = base64.urlsafe_b64encode(rel_path.encode()).decode().rstrip("=")
        except Exception:
            continue
        n = str(src.get("doc_name") or "").strip() or os.path.basename(p)
        key = _yukt_doc_key_for_source(src, i)
        payload[key] = {"name": n, "fid": fid}
        if n:
            name_to_key[n.lower()] = key
            name_to_key[os.path.basename(n).lower()] = key
        name_to_key[os.path.basename(p).lower()] = key
    return name_to_key, payload


def _merge_all_assistant_inline_video_payloads(msgs: list[dict[str, Any]]) -> dict[str, Any]:
    """One merged fid map for all assistant messages so a single client bridge can open any cited video."""
    merged: dict[str, Any] = {}
    for m in msgs or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        srcs = m.get("sources")
        if not isinstance(srcs, list):
            continue
        _nk, pl = _build_inline_video_payloads(srcs)
        for k, v in pl.items():
            merged[k] = v
    return merged


def _parse_video_timestamp_to_seconds(ts: str) -> int | None:
    """Parse ``MM:SS`` or ``HH:MM:SS`` into total seconds. Returns ``None`` on parse failure."""
    s = (ts or "").strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 2:
            mm = int(parts[0])
            ss = int(parts[1])
            if mm < 0 or ss < 0 or ss >= 60:
                return None
            return mm * 60 + ss
        if len(parts) == 3:
            hh = int(parts[0])
            mm = int(parts[1])
            ss = int(parts[2])
            if hh < 0 or mm < 0 or mm >= 60 or ss < 0 or ss >= 60:
                return None
            return hh * 3600 + mm * 60 + ss
    except ValueError:
        return None
    return None


def _video_timestamp_range_start_sec(ts_range: str) -> int | None:
    """Given ``MM:SS-MM:SS`` (or with hours), return the start time in seconds."""
    s = (ts_range or "").strip()
    if not s:
        return None
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    return _parse_video_timestamp_to_seconds(s)


def _inline_pdf_doc_key_for_name(
    sources: list[dict[str, Any]],
    *,
    dn_raw: str,
    dn_key: str,
    key_map: dict[str, str],
) -> str | None:
    """Resolve stable payload key for a doc name (map lookup, then fuzzy source index)."""
    for cand in (_strip_inline_md_noise(dn_key), _strip_inline_md_noise(dn_raw)):
        if not cand:
            continue
        low = cand.lower()
        k = key_map.get(low) or key_map.get(os.path.basename(low).lower())
        if k:
            return k
    si = _find_source_index_for_doc_name(sources, dn_key) or _find_source_index_for_doc_name(
        sources, dn_raw
    )
    if si is not None and 0 <= si < len(sources):
        src = sources[si]
        if isinstance(src, dict):
            return _yukt_doc_key_for_source(src, si)
    return None


def _inline_page_open_span(dk: str, pg: str) -> str:
    """HTML span for ``page N`` that opens the host-side inline PDF modal."""
    ttl = html_module.escape(f"{dk}|{pg}", quote=True)
    return (
        f'<span class="yukt-page-ref yukt-inline-page-open" role="link" tabindex="0" '
        f'title="{ttl}" '
        f'data-yukt-doc-key="{html_module.escape(dk, quote=True)}" '
        f'data-yukt-page="{html_module.escape(pg, quote=True)}">page {html_module.escape(pg)}</span>'
    )


def _inline_video_time_open_span(vk: str, ts_range: str) -> str:
    """HTML span for ``MM:SS-MM:SS`` that opens the host-side inline video modal seeked to that time."""
    start_sec = _video_timestamp_range_start_sec(ts_range)
    if start_sec is None:
        return html_module.escape(ts_range)
    ts_clean = ts_range.strip()
    ttl = html_module.escape(f"{vk}|{start_sec}", quote=True)
    return (
        f'<span class="yukt-page-ref yukt-inline-video-time-open" role="link" tabindex="0" '
        f'title="{ttl}" '
        f'data-yukt-video-key="{html_module.escape(vk, quote=True)}" '
        f'data-yukt-start-sec="{html_module.escape(str(start_sec), quote=True)}">{html_module.escape(ts_clean)}</span>'
    )


def _open_source_in_viewer(src: dict[str, Any], *, page_override: int | None = None) -> bool:
    p = _resolve_source_doc_path(src)
    if not p or not os.path.isfile(p):
        return False
    sp: int | None = None
    if page_override is not None:
        try:
            sp = int(page_override)
        except (TypeError, ValueError):
            sp = None
    else:
        pg = src.get("page_number")
        if pg is not None:
            try:
                sp = int(pg)
            except (TypeError, ValueError):
                sp = None
    if sp is not None and sp < 1:
        sp = None
    st.session_state.selected_pdf = p
    st.session_state.selected_pdf_page = sp
    st.session_state._pdf_just_open = True
    return True


def _find_sources_for_doc_across_messages(
    msgs: list[dict[str, Any]],
    dn_key: str,
    dn_raw: str,
) -> list[dict[str, Any]] | None:
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        raw_sources = m.get("sources")
        if not isinstance(raw_sources, list):
            continue
        if _resolve_pdf_path_for_doc_name(dn_key, raw_sources) or _resolve_pdf_path_for_doc_name(
            dn_raw, raw_sources
        ):
            return raw_sources
    return None


def _inject_markdown_page_links(
    content: str,
    msg_index: int | None,
    sources: list[dict[str, Any]] | None,
    *,
    chat_session_id: str,
    message_id: int | None = None,
    inline_doc_name_to_key: dict[str, str] | None = None,
) -> str:
    """Inject clickable ``page N`` spans for common citation shapes.

    **Passage** (single-PDF chats only): ``[Passage k, page N]`` **or** ``[Passage k], page N`` /
    ``[Passage k] **page N**``.

    **PDF + page** (any chat with matching sources on disk): ``Manual.pdf, page N``,
    optional ``**`` around ``page N``, optional wrapping ``[Manual.pdf, page N]`` (or ``;`` instead of ``,``),
    and optional ``**`` inside the filename from the model.

    **Sole-PDF fallbacks** (exactly one on-disk manual in sources): numbered list lines ending in
    ``… page N``, and ``. page N`` after sentence punctuation — only when the line/match has no ``<``
    (avoids matching ``page`` inside HTML attributes).
    """
    if msg_index is None or not isinstance(sources, list) or not sources:
        return content
    usable: list[dict[str, Any]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        resolved = _resolve_source_doc_path(s)
        if not resolved or not os.path.isfile(resolved):
            continue
        row = dict(s)
        # Keep citations clickable for older chat rows that stored empty/stale doc_path.
        row["doc_path"] = resolved
        usable.append(row)
    if not usable:
        return content
    key_map = inline_doc_name_to_key or {}
    text = str(content or "")

    sole = _unique_doc_name_for_passage_links(usable)
    dk_sole: str | None = None
    if sole and msg_index is not None and _resolve_pdf_path_for_doc_name(sole, usable):
        dk_sole = _inline_pdf_doc_key_for_name(
            sources or [],
            dn_raw=sole,
            dn_key=sole,
            key_map=key_map,
        )
    if sole and msg_index is not None and dk_sole:
        def _repl_sole_generic_page(m: re.Match[str]) -> str:
            whole = m.group(0)
            if "<" in whole or ">" in whole:
                return whole
            pg = (m.group("pg") or "").strip()
            if not pg:
                return whole
            open_part = m.group("open") or ""
            close_part = m.group("close") or ""
            return f"{open_part}{_inline_page_open_span(dk_sole, pg)}{close_part}"

        def _repl_passage_inline(m: re.Match[str]) -> str:
            pg = (m.group("pg") or "").strip()
            tag = (m.group("tag") or "").strip()
            if not pg or not tag:
                return m.group(0)
            tag_esc = html_module.escape(tag, quote=False)
            return f"[{tag_esc}, {_inline_page_open_span(dk_sole, pg)}]"

        def _repl_passage(m: re.Match[str]) -> str:
            pg = (m.group("pg") or "").strip()
            prefix = m.group("prefix") or ""
            if not pg:
                return m.group(0)
            return f"{prefix}{_inline_page_open_span(dk_sole, pg)}"

        text = _SOLE_GENERIC_PAGE_TOKEN_RE.sub(_repl_sole_generic_page, text)
        text = _MARKDOWN_PASSAGE_INLINE_PAGE_RE.sub(_repl_passage_inline, text)
        text = _MARKDOWN_PASSAGE_PAGE_REF_RE.sub(_repl_passage, text)

    def _repl(m: re.Match[str]) -> str:
        lead = m.group("lead") or ""
        dn_raw = _strip_inline_md_noise(m.group("dn") or "")
        pg = (m.group("pg") or "").strip()
        suf = m.group("bracket_close") or ""
        if not dn_raw or not pg:
            return m.group(0)
        dn_key = _strip_leading_source_phrase(dn_raw) or dn_raw
        path = _resolve_pdf_path_for_doc_name(dn_key, usable) or _resolve_pdf_path_for_doc_name(
            dn_raw, usable
        )
        if not path:
            return m.group(0)
        dk = _inline_pdf_doc_key_for_name(
            sources or [],
            dn_raw=dn_raw,
            dn_key=dn_key,
            key_map=key_map,
        )
        if not dk:
            return m.group(0)
        dn_show = dn_key if dn_key.strip().lower() != dn_raw.strip().lower() else dn_raw
        dn_disp = html_module.escape(dn_show, quote=False)
        return f"{lead}{dn_disp}, {_inline_page_open_span(dk, pg)}{suf}"

    text = _MARKDOWN_PDF_PAGE_REF_RE.sub(_repl, text)

    if dk_sole and msg_index is not None:

        def _repl_sole_line_tail(m: re.Match[str]) -> str:
            whole = m.group(0)
            if "<" in whole:
                return whole
            hdr = m.group("hdr") or ""
            pg = (m.group("pg") or "").strip()
            if not pg:
                return whole
            return f"{hdr}{_inline_page_open_span(dk_sole, pg)}"

        def _repl_sole_dot_page(m: re.Match[str]) -> str:
            whole = m.group(0)
            if "<" in whole:
                return whole
            pfx = m.group("pfx") or ""
            pg = (m.group("pg") or "").strip()
            if not pg:
                return whole
            return f"{pfx}{_inline_page_open_span(dk_sole, pg)}"

        text = _SOLE_NUMBERED_LINE_TAIL_PAGE_RE.sub(_repl_sole_line_tail, text)
        text = _SOLE_DOT_LEAD_PAGE_RE.sub(_repl_sole_dot_page, text)
        # Model often emits ``**page 8**`` without a .pdf line match — wrap when exactly one on-disk source.
        text = re.sub(
            r"(?i)\*\*page\s+(\d{1,4})\*\*",
            lambda m2: _inline_page_open_span(dk_sole, m2.group(1).strip()),
            text,
        )

    return text


def _inject_markdown_video_time_links(
    content: str,
    sources: list[dict[str, Any]] | None,
    *,
    inline_video_name_to_key: dict[str, str] | None = None,
) -> str:
    """Wrap ``Video.mp4, MM:SS-MM:SS`` (and bare ``MM:SS-MM:SS`` when only one video source) into
    clickable spans that open the host-side video modal seeked to the matching offset."""
    if not isinstance(sources, list) or not sources:
        return content
    name_map = inline_video_name_to_key or {}
    if not name_map:
        _nk, _pl = _build_inline_video_payloads(sources)
        name_map = _nk
    if not name_map:
        return content

    text = str(content or "")

    def _resolve_key(dn_raw: str) -> str | None:
        for cand in (dn_raw, os.path.basename(dn_raw)):
            c = (cand or "").strip().lower()
            if c and c in name_map:
                return name_map[c]
        return None

    # Sole-video fallback runs FIRST: when exactly one usable video source exists, wrap every
    # ``MM:SS-MM:SS`` token. Running this before the filename-form regex prevents double-wrapping
    # because once a timestamp is inside a ``<span>`` the filename-form regex no longer matches it.
    distinct_keys = {v for v in name_map.values()}
    if len(distinct_keys) == 1:
        sole_vk = next(iter(distinct_keys))

        def _repl_sole(m: re.Match[str]) -> str:
            whole = m.group(0)
            if "<" in whole or ">" in whole:
                return whole
            ts = (m.group("ts") or "").strip()
            if not ts or _video_timestamp_range_start_sec(ts) is None:
                return m.group(0)
            open_part = m.group("open") or ""
            close_part = m.group("close") or ""
            return f"{open_part}{_inline_video_time_open_span(sole_vk, ts)}{close_part}"

        text = _SOLE_VIDEO_TIME_TOKEN_RE.sub(_repl_sole, text)

    def _repl(m: re.Match[str]) -> str:
        lead = m.group("lead") or ""
        dn_raw = _strip_inline_md_noise(m.group("dn") or "")
        ts = (m.group("ts") or "").strip()
        suf = m.group("bracket_close") or ""
        if not dn_raw or not ts:
            return m.group(0)
        vk = _resolve_key(dn_raw) or _resolve_key(_strip_leading_source_phrase(dn_raw))
        if not vk:
            return m.group(0)
        if _video_timestamp_range_start_sec(ts) is None:
            return m.group(0)
        dn_disp = html_module.escape(dn_raw, quote=False)
        return f"{lead}{dn_disp}, {_inline_video_time_open_span(vk, ts)}{suf}"

    text = _MARKDOWN_VIDEO_TIME_REF_RE.sub(_repl, text)

    return text


def _clear_yukt_pdf_query_keys() -> None:
    keys = ("yukt_pdf_open", "yukt_mi", "yukt_mid", "yukt_si", "yukt_pg", "yukt_pq", "yukt_dn", "yukt_sid")
    try:
        for k in keys:
            try:
                del st.query_params[k]
            except Exception:
                pass
    except Exception:
        pass


def _consume_yukt_pdf_deep_link() -> None:
    """Open the PDF dialog from ``?yukt_pdf_open=1&...`` (inline page links in assistant bubbles)."""
    try:
        flag = _qp_one(st.query_params.get("yukt_pdf_open"))
    except Exception:
        return
    if flag != "1":
        return
    mi_s = _qp_one(st.query_params.get("yukt_mi"))
    mid_s = _qp_one(st.query_params.get("yukt_mid"))
    si_s = _qp_one(st.query_params.get("yukt_si"))
    pg_s = _qp_one(st.query_params.get("yukt_pg")) or _qp_one(st.query_params.get("yukt_pq"))
    dn_raw = _qp_one(st.query_params.get("yukt_dn"))
    try:
        mi = int(mi_s)
    except ValueError:
        mi = -1
    try:
        mid_db = int(mid_s)
    except ValueError:
        mid_db = -1
    try:
        src_i = int(si_s)
    except ValueError:
        src_i = -1
    try:
        pg = int(pg_s)
    except ValueError:
        pg = -1
    dn = urllib.parse.unquote(dn_raw) if dn_raw else ""
    dn_key = _strip_leading_source_phrase(dn) or dn
    _mark_scroll_target_from_link(mi, mid_db)
    msgs = st.session_state.get("messages") or []
    sources: list[dict[str, Any]] = []
    if mid_db > 0:
        for cand in msgs:
            if not isinstance(cand, dict):
                continue
            try:
                cid = int(cand.get("message_id") or 0)
            except (TypeError, ValueError):
                cid = 0
            if cid == mid_db and cand.get("role") == "assistant":
                raw_sources = cand.get("sources")
                if isinstance(raw_sources, list):
                    sources = raw_sources
                break
    if not sources and 0 <= mi < len(msgs):
        cand = msgs[mi]
        if isinstance(cand, dict) and cand.get("role") == "assistant":
            raw_sources = cand.get("sources")
            if isinstance(raw_sources, list):
                sources = raw_sources
    if sources and src_i >= 0 and src_i < len(sources):
        src = sources[src_i]
        if isinstance(src, dict) and _open_source_in_viewer(src, page_override=(pg if pg >= 1 else None)):
            _clear_yukt_pdf_query_keys()
            st.rerun()
            return
    if not sources:
        found = _find_sources_for_doc_across_messages(msgs, dn_key, dn)
        sources = found if found is not None else []
    if not sources:
        _clear_yukt_pdf_query_keys()
        st.warning("That manual link does not match this chat or the manual is unavailable.")
        return
    path = _resolve_pdf_path_for_doc_name(dn_key, sources) or _resolve_pdf_path_for_doc_name(dn, sources)
    if not path:
        _clear_yukt_pdf_query_keys()
        st.warning("That manual link is no longer available for this message.")
        return
    src_match: dict[str, Any] | None = None
    for s in sources:
        if not isinstance(s, dict):
            continue
        if str(s.get("doc_path") or "").strip() == path:
            src_match = s
            break
    if src_match is None:
        src_match = {"doc_path": path, "page_number": (pg if pg >= 1 else None)}
    _open_source_in_viewer(src_match, page_override=(pg if pg >= 1 else None))
    _clear_yukt_pdf_query_keys()
    st.rerun()


def _inline_image_tag(index_1based: int) -> str:
    return f"[[YUKTRA_IMAGE_{max(1, int(index_1based))}]]"


def _inline_image_tags_block(images: list[dict[str, Any]]) -> str:
    if not images:
        return ""
    return "\n\n" + "\n\n".join(_inline_image_tag(i + 1) for i in range(len(images))) + "\n\n"


def _contains_inline_image_tag(text: str) -> bool:
    return bool(_INLINE_IMAGE_TAG_RE.search(text or ""))


def _strip_inline_image_tags(text: str) -> str:
    return _INLINE_IMAGE_TAG_RE.sub("", str(text or ""))


def _is_safe_inline_image_boundary(text: str) -> bool:
    """Insert images only after a natural text boundary, not mid-sentence."""
    tail = str(text or "").rstrip()
    if not tail:
        return False
    if tail.endswith(("\n", "\n\n")):
        return True
    if tail.endswith((".", "!", "?", ":", ";")):
        return True
    return False


def _compose_final_assistant_text(
    streamed_text: str,
    backend_final: str,
    images: list[dict[str, Any]] | None,
) -> str:
    imgs = images if isinstance(images, list) else []
    stxt = str(streamed_text or "").strip()
    btxt = str(backend_final or "").strip()
    stxt_wo_tags = _strip_inline_image_tags(stxt).strip()
    # Final backend answer is authoritative full text; streamed accumulation can
    # occasionally lose an initial fragment under rerun/network races.
    if btxt:
        out = btxt
    elif stxt and stxt_wo_tags:
        out = stxt
    else:
        out = stxt or btxt
    if imgs and not _contains_inline_image_tag(out):
        out = (out + _inline_image_tags_block(imgs)).strip()
    return out


def _bytes_for_st_image_display(raw: bytes) -> bytes:
    """
    Many OEM PDFs bake a light cyan diagonal watermark into exported figure bitmaps.
    Whiten only near-white pixels with a blue bias so line art (dark) stays intact.
    Set YUKTRA_QNA_IMAGE_WATERMARK_FILTER=0 to skip. Thresholds are env-tunable.
    """
    flag = os.environ.get("YUKTRA_QNA_IMAGE_WATERMARK_FILTER", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return raw
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return raw
    try:
        im = Image.open(BytesIO(raw))
    except Exception:
        return raw
    try:
        if im.mode == "RGBA":
            canvas = Image.new("RGB", im.size, (255, 255, 255))
            canvas.paste(im, mask=im.split()[3])
            im = canvas
        else:
            im = im.convert("RGB")
        thr = int(os.environ.get("YUKTRA_QNA_IMAGE_WATERMARK_RGB_MIN", "226"))
        br = int(os.environ.get("YUKTRA_QNA_IMAGE_WATERMARK_MIN_BR", "2"))
        bgap = int(os.environ.get("YUKTRA_QNA_IMAGE_WATERMARK_MIN_BG", "1"))
        arr = np.asarray(im, dtype=np.int16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        mask = (r > thr) & (g > thr) & (b > thr) & ((b - r) >= br) & ((b - g) >= bgap)
        if not np.any(mask):
            buf = BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
        out = np.array(im, dtype=np.uint8)
        out[mask] = (255, 255, 255)
        buf = BytesIO()
        Image.fromarray(out, mode="RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return raw


def _frontend_logger():
    return get_logger("yuktra_qna.frontend", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)


@st.cache_data(show_spinner=False)
def _icon_data_uri(path: str) -> str:
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""


def _nav_icon_label(icon_path: str, text: str, expanded: bool, fallback_icon: str) -> str:
    uri = _icon_data_uri(icon_path)
    icon_md = f"![icon]({uri})" if uri else fallback_icon
    return f"{icon_md}  {text}" if expanded else icon_md


def _markdown_to_chat_html(text: str) -> str:
    raw = (text or "").strip()
    if markdown_lib is not None:
        try:
            return markdown_lib.markdown(
                raw,
                extensions=[
                    "markdown.extensions.nl2br",
                    "markdown.extensions.fenced_code",
                ],
            )
        except Exception:
            pass
    esc = html_module.escape(raw)
    return "<p>" + esc.replace("\n", "<br/>") + "</p>"


def _user_bubble_html(text: str) -> str:
    esc = html_module.escape((text or "").strip())
    return "<p>" + esc.replace("\n", "<br/>") + "</p>"


def _format_message_timestamp(iso_ts: str | None) -> str:
    if not iso_ts:
        return ""
    try:
        s = str(iso_ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        h12 = local.hour % 12 or 12
        ampm = "AM" if local.hour < 12 else "PM"
        return f"{local.strftime('%b %d, %Y')} {h12}:{local.minute:02d} {ampm}"
    except Exception:
        return str(iso_ts)[:19]


def _render_user_turn(
    content: str,
    ts: str | None = None,
    *,
    msg_index: int | None = None,
    message_id: int | None = None,
) -> None:
    _, col = st.columns([0.12, 0.88])
    with col:
        inner = _user_bubble_html(content)
        anchor_id = _user_message_anchor_id(msg_index, message_id)
        aid_attr = f' id="{html_module.escape(anchor_id, quote=True)}"' if anchor_id else ""
        st.markdown(
            f'<div{aid_attr} class="yukt-chat-row yukt-row-user"><div class="yukt-bubble yukt-bubble-user">{inner}</div></div>',
            unsafe_allow_html=True,
        )
        label = _format_message_timestamp(ts)
        if label:
            st.markdown(
                f'<div class="yukt-msg-ts-user">{html_module.escape(label)}</div>',
                unsafe_allow_html=True,
            )


def _assistant_bubble_block_markdown(content: str) -> str:
    inner = _markdown_to_chat_html(content)
    return (
        f'<div class="yukt-chat-row yukt-row-assistant">'
        f'<div class="yukt-bubble yukt-bubble-assistant">{inner}</div></div>'
    )


def _assistant_bubble_block_markdown_with_anchor(content: str, anchor_id: str | None = None) -> str:
    inner = _markdown_to_chat_html(content)
    if anchor_id:
        aid = html_module.escape(anchor_id, quote=True)
        return (
            f'<div id="{aid}" class="yukt-chat-row yukt-row-assistant">'
            f'<div class="yukt-bubble yukt-bubble-assistant">{inner}</div></div>'
        )
    return (
        f'<div class="yukt-chat-row yukt-row-assistant">'
        f'<div class="yukt-bubble yukt-bubble-assistant">{inner}</div></div>'
    )


def _progress_text_line(progress: str, *, fading: bool = False) -> str:
    txt = html_module.escape(str(progress or "").strip())
    if not txt:
        return ""
    if fading:
        return f'<span class="yukt-progress-fadeout">⏳ {txt}</span>'
    return f"⏳ {txt}"


def _render_retrieved_image(img: dict[str, Any], ii: int) -> None:
    b64 = str(img.get("image_base64") or "").strip()
    uid = str(img.get("image_uuid") or "").strip()
    cap = str(img.get("caption") or f"Image {ii + 1}").strip()
    if not b64 and not uid:
        return
    if b64:
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return
        disp = _bytes_for_st_image_display(raw)
    else:
        disp = f"{_browser_image_api_base()}/images/{urllib.parse.quote(uid)}"
    cap_out = cap
    if cap.lower() in ("image", "img", "picture", "figure"):
        cap_out = ""
    st.image(
        disp,
        caption=cap_out or None,
        width=max(180, min(IMAGE_RENDER_WIDTH, 520)),
        use_container_width=False,
    )


def _image_data_uri_for_inline(img: dict[str, Any]) -> str:
    b64 = str(img.get("image_base64") or "").strip()
    uid = str(img.get("image_uuid") or "").strip()
    if not b64 and uid:
        return f"{_browser_image_api_base()}/images/{urllib.parse.quote(uid)}"
    mime = str(img.get("image_mime") or "image/png").strip() or "image/png"
    if not b64:
        return ""
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return ""
    disp = _bytes_for_st_image_display(raw)
    enc = base64.b64encode(disp).decode("ascii")
    return f"data:{mime};base64,{enc}"


def _inject_inline_images_into_text(content: str, images: list[dict[str, Any]] | None) -> str:
    text = str(content or "")
    imgs = images if isinstance(images, list) else []
    if not imgs:
        return text
    out_parts: list[str] = []
    cursor = 0
    for m in _INLINE_IMAGE_TAG_RE.finditer(text):
        out_parts.append(text[cursor : m.start()])
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(imgs):
            uri = _image_data_uri_for_inline(imgs[idx])
            if uri:
                cap = html_module.escape(str(imgs[idx].get("caption") or f"Image {idx + 1}").strip())
                out_parts.append(
                    "\n\n"
                    f'<img class="yukt-inline-response-image" src="{uri}" alt="{cap}" role="button" tabindex="0" '
                    f'style="display:block;max-width:min(100%, 520px);'
                    f'width:min(100%, {IMAGE_RENDER_WIDTH}px);border-radius:8px;margin:8px 0;cursor:zoom-in;" />'
                    f'<div style="font-size:0.86rem;color:#6b7280;margin:-2px 0 8px 0;">{cap}</div>'
                    "\n\n"
                )
        cursor = m.end()
    out_parts.append(text[cursor:])
    return "".join(out_parts)


def _render_assistant_content_with_inline_images(
    content: str,
    images: list[dict[str, Any]] | None,
    *,
    show_fallback_gallery: bool = True,
    msg_index: int | None = None,
    sources: list[dict[str, Any]] | None = None,
    message_id: int | None = None,
    inline_doc_name_to_key: dict[str, str] | None = None,
    inline_video_name_to_key: dict[str, str] | None = None,
) -> None:
    imgs = images if isinstance(images, list) else []
    text = str(content or "")
    sid = str(st.session_state.get("session_id") or "").strip()
    text = _inject_markdown_page_links(
        text,
        msg_index,
        sources,
        chat_session_id=sid,
        message_id=message_id,
        inline_doc_name_to_key=inline_doc_name_to_key,
    )
    text = _inject_markdown_video_time_links(
        text,
        sources,
        inline_video_name_to_key=inline_video_name_to_key,
    )
    anchor_id = _assistant_message_anchor_id(msg_index, message_id)
    if not imgs:
        st.markdown(_assistant_bubble_block_markdown_with_anchor(text, anchor_id), unsafe_allow_html=True)
        return
    matches = list(_INLINE_IMAGE_TAG_RE.finditer(text))
    if not matches:
        st.markdown(_assistant_bubble_block_markdown_with_anchor(text, anchor_id), unsafe_allow_html=True)
        if show_fallback_gallery:
            st.markdown("**Retrieved Images**")
            for ii, img in enumerate(imgs):
                _render_retrieved_image(img, ii)
        return
    merged = _inject_inline_images_into_text(text, imgs)
    st.markdown(_assistant_bubble_block_markdown_with_anchor(merged, anchor_id), unsafe_allow_html=True)


def _render_assistant_turn(
    *,
    content: str,
    msg_index: int | None = None,
    sources: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
    ts: str | None = None,
    message_id: int | None = None,
) -> None:
    inline_name_to_key, inline_pdf_payload = _build_inline_pdf_payloads(sources)
    inline_video_name_to_key, inline_video_payload = _build_inline_video_payloads(sources)
    col, _ = st.columns([0.88, 0.12])
    with col:
        _render_assistant_content_with_inline_images(
            content,
            images,
            msg_index=msg_index,
            sources=sources,
            message_id=message_id,
            inline_doc_name_to_key=inline_name_to_key,
            inline_video_name_to_key=inline_video_name_to_key,
        )
        label = _format_message_timestamp(ts)
        if label:
            st.markdown(
                f'<div class="yukt-msg-ts-assistant">{html_module.escape(label)}</div>',
                unsafe_allow_html=True,
            )
        tts_btn_key = (
            f"tts_speak_{message_id}"
            if message_id is not None
            else f"tts_speak_pending_{msg_index}"
        )
        tts_msg_key = tts_btn_key
        tts_active_msg_key = str(st.session_state.get("tts_active_msg_key") or "")
        tts_is_paused = bool(st.session_state.get("tts_is_paused", False))
        tts_pending = st.session_state.get("tts_pending_play")
        tts_pending_msg_key = ""
        if isinstance(tts_pending, dict):
            tts_pending_msg_key = str(tts_pending.get("msg_key") or "")
        tts_label = "Speak"
        tts_disabled = False
        if tts_pending_msg_key == tts_msg_key:
            tts_label = "⏳ Preparing..."
            tts_disabled = True
        if tts_active_msg_key == tts_msg_key and not tts_is_paused:
            tts_label = "⏸ Pause"
            tts_disabled = False
        if st.button(tts_label, key=tts_btn_key, disabled=tts_disabled):
            if tts_active_msg_key == tts_msg_key and not tts_is_paused:
                st.session_state.tts_browser_cmd = {"action": "pause", "msg_key": tts_msg_key}
                st.session_state.tts_is_paused = True
            elif tts_active_msg_key == tts_msg_key and tts_is_paused:
                st.session_state.tts_browser_cmd = {"action": "resume", "msg_key": tts_msg_key}
                st.session_state.tts_is_paused = False
            else:
                # Do not block UI here; fetch/play is completed asynchronously in browser bridge.
                _request_tts_play(tts_msg_key, content)
        if not sources:
            pass
        else:
            st.markdown("**Sources**")
            for si, src in enumerate(sources):
                p = _resolve_source_doc_path(src)
                name = str(src.get("doc_name") or "").strip() or "(unknown)"
                key = (
                    f"src_{msg_index}_{si}_{name}"
                    if msg_index is not None
                    else f"src_pending_{si}"
                )
                if p and os.path.isfile(p):
                    if _is_video_source_path(p):
                        video_key = _yukt_doc_key_for_source(src, si)
                        if video_key in inline_video_payload:
                            st.markdown(
                                (
                                    '<div class="yukt-inline-video-anchor" '
                                    'data-yukt-video-anchor="1" '
                                    f'data-yukt-video-key="{html_module.escape(video_key, quote=True)}">'
                                    "</div>"
                                ),
                                unsafe_allow_html=True,
                            )
                        continue
                    doc_key = _yukt_doc_key_for_source(src, si)
                    pg = src.get("page_number")
                    try:
                        pg_int = int(pg) if pg is not None else 0
                    except (TypeError, ValueError):
                        pg_int = 0
                    pg_open = pg_int if pg_int > 0 else 1
                    # Visible page reference (only when a real page is known).
                    page_lbl = f" · page {pg_int}" if pg_int > 0 else ""
                    page_attr = f' data-yukt-page="{html_module.escape(str(pg_open), quote=True)}"'
                    ttl_src = html_module.escape(f"{doc_key}|{pg_open}", quote=True)
                    if doc_key in inline_pdf_payload:
                        st.markdown(
                            (
                                '<span class="yukt-source-chip yukt-inline-source-open" role="button" tabindex="0" '
                                f'title="{ttl_src}" '
                                f'data-yukt-doc-key="{html_module.escape(doc_key, quote=True)}"{page_attr}>'
                                f"📄 {html_module.escape(name)}{html_module.escape(page_lbl)}"
                                "</span>"
                            ),
                            unsafe_allow_html=True,
                        )
                    else:
                        # Fallback path only if inline payload was unavailable.
                        if st.button(f"📄 {name}{page_lbl}", key=key):
                            if _open_source_in_viewer(src):
                                st.rerun()
                else:
                    st.caption(f"📎 {name} (path not available on disk)")


def _apply_sidebar_drawer_css(*, open_sidebar: bool, nav_expanded: bool) -> None:
    cls = "yukt-force-open" if open_sidebar else "yukt-force-closed"
    root_cls = "yukt-sidebar-open" if open_sidebar else "yukt-sidebar-closed"
    rail_display = "none" if open_sidebar else "flex"
    gutter_display = "none" if open_sidebar else "block"
    nav_cls = "yukt-sidebar-nav-expanded" if nav_expanded else "yukt-sidebar-nav-collapsed"
    st.html(
        f"""
        <script>
        (function () {{
          const doc = (window.parent && window.parent !== window) ? window.parent.document : document;
          const sb = doc.querySelector('section[data-testid="stSidebar"]');
          if (sb) {{
            sb.classList.remove('yukt-force-open', 'yukt-force-closed');
            sb.classList.add('{cls}');
          }}

          const root = doc.documentElement;
          root.classList.remove('yukt-sidebar-open', 'yukt-sidebar-closed');
          root.classList.add('{root_cls}');
          root.classList.remove('yukt-sidebar-nav-expanded', 'yukt-sidebar-nav-collapsed');
          root.classList.add('{nav_cls}');

          function applyRailVisibility() {{
            const rail = doc.getElementById('yukt-left-rail');
            const gutter = doc.querySelector('.yukt-left-rail-gutter');
            if (rail) rail.style.display = '{rail_display}';
            if (gutter) gutter.style.display = '{gutter_display}';
          }}

          function applyHeaderFullRule() {{
            const rules = doc.querySelectorAll('.yukt-header-fullrule');
            rules.forEach(function (rule) {{
              rule.style.position = 'fixed';
              rule.style.left = '0';
              rule.style.right = '0';
              rule.style.width = '100vw';
              rule.style.marginLeft = '0';
              rule.style.top = 'var(--yukt-header-height, 64px)';
              rule.style.height = '1px';
              rule.style.background = 'var(--yukt-header-rule, rgba(15, 23, 42, 0.10))';
              rule.style.boxShadow = 'none';
              rule.style.zIndex = '1000002';
              rule.style.pointerEvents = 'none';
            }});
          }}

          function applyHeaderLayout() {{
            const headerHeight = 'var(--yukt-header-height, 64px)';
            const caps = doc.querySelectorAll('.yukt-header-leftcap');
            caps.forEach(function (cap) {{
              cap.style.position = 'fixed';
              cap.style.top = '0';
              cap.style.left = '0';
              cap.style.width = 'var(--yukt-sidebar-width, 100px)';
              cap.style.height = headerHeight;
              cap.style.zIndex = '1000001';
              cap.style.pointerEvents = 'none';
            }});
            const bars = doc.querySelectorAll('.yukt-header-bar');
            bars.forEach(function (bar) {{
              bar.style.position = 'fixed';
              bar.style.top = '0';
              bar.style.left = '0';
              bar.style.width = '100vw';
              bar.style.zIndex = '1000003';
            }});
            if (sb) {{
              sb.style.top = headerHeight;
              sb.style.height = 'calc(100vh - ' + headerHeight + ')';
              sb.style.maxHeight = 'calc(100vh - ' + headerHeight + ')';
              sb.style.borderTop = '0';
              sb.style.borderLeft = '0';
              sb.style.borderRight = '1px solid var(--yukt-chrome-border, rgba(15, 23, 42, 0.14))';
              sb.style.borderBottom = '0';
            }}
          }}

          function applyCollapsedNavHost(host) {{
            if (!host) return;
            host.style.setProperty('height', '0', 'important');
            host.style.setProperty('min-height', '0', 'important');
            host.style.setProperty('margin', '0', 'important');
            host.style.setProperty('padding', '0', 'important');
            host.style.setProperty('overflow', 'visible', 'important');
          }}

          function resetCollapsedNavNode(node) {{
            if (!node) return;
            node.style.removeProperty('position');
            node.style.removeProperty('top');
            node.style.removeProperty('left');
            node.style.removeProperty('width');
            node.style.removeProperty('height');
            node.style.removeProperty('z-index');
            node.style.removeProperty('display');
            node.style.removeProperty('justify-content');
            node.style.removeProperty('margin-top');
            const host = node.closest('[data-testid="stElementContainer"]');
            if (host) {{
              host.style.removeProperty('height');
              host.style.removeProperty('min-height');
              host.style.removeProperty('margin');
              host.style.removeProperty('padding');
              host.style.removeProperty('overflow');
            }}
            const btn = node.querySelector('button');
            if (btn) {{
              btn.style.removeProperty('height');
              btn.style.removeProperty('min-height');
              btn.style.removeProperty('display');
              btn.style.removeProperty('align-items');
              btn.style.removeProperty('justify-content');
              btn.style.removeProperty('padding');
            }}
          }}

          function applyEqLogoLayout() {{
            const collapsed = root.classList.contains('yukt-sidebar-nav-collapsed');
            const expanded = root.classList.contains('yukt-sidebar-nav-expanded');
            const wrap = doc.querySelector('section[data-testid="stSidebar"] .st-key-yukt_product_btn');
            const newBtn = doc.querySelector('section[data-testid="stSidebar"] .st-key-yukt_new_btn');
            const histBtn = doc.querySelector('section[data-testid="stSidebar"] .st-key-yukt_history_btn');
            if (!wrap || (!collapsed && !expanded)) return;
            const headerH = 'var(--yukt-header-height, 64px)';
            const navItemH = 'var(--yukt-collapsed-nav-item-h, 44px)';
            const navTop = 'calc(' + headerH + ' + 2px)';
            const histTop = 'calc(' + headerH + ' + 2px + ' + navItemH + ')';
            const eqTop = 'var(--yukt-eq-top, 25px)';
            const eqBlockH = 'var(--yukt-eq-block-h, 28px)';
            const productRailW = 'var(--yukt-sidebar-width-collapsed, 100px)';
            const navRowW = expanded
              ? 'var(--yukt-sidebar-width-expanded, 300px)'
              : productRailW;
            wrap.style.setProperty('position', 'fixed', 'important');
            wrap.style.setProperty('top', eqTop, 'important');
            wrap.style.setProperty('left', '0', 'important');
            wrap.style.setProperty('width', productRailW, 'important');
            wrap.style.setProperty('height', eqBlockH, 'important');
            wrap.style.setProperty('margin', '0', 'important');
            wrap.style.setProperty('padding', '0', 'important');
            wrap.style.setProperty('z-index', '1000005', 'important');
            wrap.style.setProperty('display', 'flex', 'important');
            wrap.style.setProperty('align-items', 'center', 'important');
            wrap.style.setProperty('justify-content', 'center', 'important');
            applyCollapsedNavHost(wrap.closest('[data-testid="stElementContainer"]'));
            const btn = wrap.querySelector('button');
            if (btn) {{
              btn.style.setProperty('height', '100%', 'important');
              btn.style.setProperty('min-height', '0', 'important');
              btn.style.setProperty('display', 'flex', 'important');
              btn.style.setProperty('align-items', 'center', 'important');
              btn.style.setProperty('justify-content', 'center', 'important');
              btn.style.setProperty('padding', '0', 'important');
              btn.style.setProperty('width', '100%', 'important');
              btn.style.setProperty('max-width', productRailW, 'important');
            }}
            const img = wrap.querySelector('img');
            if (img) {{
              img.style.setProperty('width', '40px', 'important');
              img.style.setProperty('height', '14px', 'important');
              img.style.setProperty('object-fit', 'contain', 'important');
              img.style.removeProperty('position');
              img.style.removeProperty('left');
              img.style.removeProperty('top');
              img.style.removeProperty('transform');
            }}
            function pinNavIcon(node, topExpr) {{
              if (!node) return;
              node.style.setProperty('position', 'fixed', 'important');
              node.style.setProperty('top', topExpr, 'important');
              node.style.setProperty('left', '0', 'important');
              node.style.setProperty('width', navRowW, 'important');
              node.style.setProperty('margin', '0', 'important');
              node.style.setProperty('padding', '0', 'important');
              node.style.setProperty('z-index', '1000004', 'important');
              node.style.setProperty('display', 'flex', 'important');
              node.style.setProperty('justify-content', expanded ? 'flex-start' : 'center', 'important');
              applyCollapsedNavHost(node.closest('[data-testid="stElementContainer"]'));
              const navBtn = node.querySelector('button');
              if (navBtn) {{
                navBtn.style.setProperty('height', navItemH, 'important');
                navBtn.style.setProperty('min-height', navItemH, 'important');
                navBtn.style.setProperty('display', 'flex', 'important');
                navBtn.style.setProperty('align-items', 'center', 'important');
                navBtn.style.setProperty(
                  'justify-content',
                  expanded ? 'flex-start' : 'center',
                  'important'
                );
                navBtn.style.setProperty('padding', '0', 'important');
                navBtn.style.setProperty('margin', '0', 'important');
                navBtn.style.setProperty('width', '100%', 'important');
                navBtn.style.setProperty('max-width', expanded ? 'none' : productRailW, 'important');
                navBtn.style.setProperty('border', '0', 'important');
                navBtn.style.setProperty(
                  'border-bottom',
                  '1px solid var(--yukt-chrome-border, rgba(15, 23, 42, 0.14))',
                  'important'
                );
                navBtn.style.setProperty('border-radius', '0', 'important');
                navBtn.style.setProperty('box-shadow', 'none', 'important');
              }}
            }}
            pinNavIcon(newBtn, navTop);
            pinNavIcon(histBtn, histTop);
          }}

          function applyHistoryScrollBox() {{
            if (!root.classList.contains('yukt-sidebar-nav-expanded')) return;
            const keyed = doc.querySelector('section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box');
            const savedLabel = doc.querySelector('section[data-testid="stSidebar"] .yukt-saved-chats-label');
            const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
            if (!keyed || !savedLabel || !sidebar) return;
            let scrollEl = keyed;
            const testId = scrollEl.getAttribute('data-testid') || '';
            if (testId === 'stElementContainer' || testId === 'stVerticalBlockBorderWrapper') {{
              const inner = scrollEl.querySelector('[data-testid="stVerticalBlock"]');
              if (inner) scrollEl = inner;
            }} else if (testId !== 'stVerticalBlock') {{
              const inner = scrollEl.querySelector('[data-testid="stVerticalBlock"]');
              if (inner) scrollEl = inner;
            }}
            const gapEl = doc.querySelector('section[data-testid="stSidebar"] .yukt-saved-chats-gap');
            const labelHost = savedLabel.closest('[data-testid="stElementContainer"]');
            const sidebarRect = sidebar.getBoundingClientRect();
            const anchorRect = gapEl
              ? gapEl.getBoundingClientRect()
              : (labelHost ? labelHost.getBoundingClientRect() : savedLabel.getBoundingClientRect());
            const avail = Math.max(120, Math.floor(sidebarRect.bottom - anchorRect.bottom));
            scrollEl.classList.add('yukt-history-scroll-target');
            scrollEl.style.setProperty('height', avail + 'px', 'important');
            scrollEl.style.setProperty('max-height', avail + 'px', 'important');
            scrollEl.style.setProperty('min-height', '120px', 'important');
            scrollEl.style.setProperty('margin-top', '0', 'important');
            scrollEl.style.setProperty('padding-top', '0', 'important');
            scrollEl.style.setProperty('overflow-y', 'scroll', 'important');
            scrollEl.style.setProperty('overflow-x', 'hidden', 'important');
            scrollEl.style.setProperty('overscroll-behavior', 'contain', 'important');
            scrollEl.style.setProperty('display', 'block', 'important');
            scrollEl.style.setProperty('box-sizing', 'border-box', 'important');
            const keyedHost = keyed.closest('[data-testid="stElementContainer"]');
            if (keyedHost) {{
              keyedHost.style.setProperty('margin-top', '0', 'important');
              keyedHost.style.setProperty('margin-bottom', '0', 'important');
              keyedHost.style.setProperty('padding', '0', 'important');
              keyedHost.style.setProperty('min-height', '0', 'important');
            }}
            scrollEl.querySelectorAll('[data-testid="stElementContainer"]').forEach(function (row) {{
              row.style.setProperty('flex', '0 0 auto', 'important');
              row.style.setProperty('margin', '0', 'important');
              row.style.setProperty('padding', '0', 'important');
              row.style.setProperty('min-height', '0', 'important');
              row.style.setProperty('height', 'auto', 'important');
            }});
          }}

          function scheduleEqLogoLayout() {{
            applyEqLogoLayout();
            applyHistoryScrollBox();
            setTimeout(function () {{
              applyEqLogoLayout();
              applyHistoryScrollBox();
            }}, 0);
            setTimeout(function () {{
              applyEqLogoLayout();
              applyHistoryScrollBox();
            }}, 150);
            setTimeout(function () {{
              applyEqLogoLayout();
              applyHistoryScrollBox();
            }}, 400);
            setTimeout(function () {{
              applyEqLogoLayout();
              applyHistoryScrollBox();
            }}, 900);
          }}

          if (!window.__yuktSidebarNavObserver) {{
            window.__yuktSidebarNavObserver = new MutationObserver(function () {{
              scheduleEqLogoLayout();
            }});
            const observeTarget = sb || doc.body;
            if (observeTarget) {{
              window.__yuktSidebarNavObserver.observe(observeTarget, {{
                childList: true,
                subtree: true,
              }});
            }}
          }}

          applyRailVisibility();
          applyHeaderFullRule();
          applyHeaderLayout();
          scheduleEqLogoLayout();
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _apply_yukt_pdf_inline_link_same_window_delegate() -> None:
    """No JS interception: native same-tab anchor navigation drives query-param deep links."""
    return


def _render_inline_pdf_modal_bridge(payload: dict[str, Any] | None = None) -> None:
    """Merge PDF payloads into host JS and ensure modal + capture listeners exist after Streamlit rerenders."""
    pl = payload if isinstance(payload, dict) else {}
    js_payload = json.dumps(pl).replace("</", "<\\/")
    # Extract only the port from QNA_API_BASE — the hostname is derived in the browser
    # from window.location so remote clients use the correct server IP automatically.
    import urllib.parse as _urlparse
    _parsed = _urlparse.urlparse(QNA_API_BASE)
    api_port_js = json.dumps(_parsed.port or 8009)
    # Use components.html: the script runs in a hidden iframe; chat + st.markdown live in parent.
    # st.html can run in the same document as the chat, but event wiring is unreliable across versions.
    components.html(
        f"""
        <script>
        (function() {{
          try {{
            var root;
            try {{
              root = (window.parent && window.parent !== window) ? window.parent : window;
            }} catch (e0) {{ root = window; }}
            var doc = root.document;
            var API_BASE = root.location.protocol + "//" + root.location.hostname + ":" + {api_port_js};
            root.__yuktInlinePdfData = root.__yuktInlinePdfData || {{}};
            var incoming = {js_payload};
            for (var k in incoming) {{
              if (Object.prototype.hasOwnProperty.call(incoming, k)) root.__yuktInlinePdfData[k] = incoming[k];
            }}
            function ensureOverlay() {{
              var ov = doc.getElementById("yukt-inline-pdf-modal");
              if (!ov) {{
                ov = doc.createElement("div");
                ov.id = "yukt-inline-pdf-modal";
                ov.style.cssText = "display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:999999;";
                ov.innerHTML =
                  '<div style="position:absolute;inset:5% 6%;background:#fff;border-radius:10px;overflow:hidden;display:flex;flex-direction:column;">' +
                  '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid #e5e7eb;">' +
                  '<strong id="yukt-inline-pdf-title">Manual</strong>' +
                  '<button type="button" id="yukt-inline-pdf-close" style="border:0;background:transparent;font-size:20px;cursor:pointer;">&times;</button>' +
                  '</div>' +
                  '<iframe id="yukt-inline-pdf-frame" title="PDF" style="flex:1;border:0;width:100%;"></iframe>' +
                  '</div>';
                doc.body.appendChild(ov);
              }}
              function closeModal() {{
                ov.style.display = "none";
                var fr = doc.getElementById("yukt-inline-pdf-frame");
                if (fr) fr.src = "about:blank";
              }}
              ov.onclick = function(ev) {{
                if (ev.target === ov) closeModal();
              }};
              var btn = doc.getElementById("yukt-inline-pdf-close");
              if (btn) btn.onclick = closeModal;
              root.__yuktInlinePdfClose = closeModal;
              return ov;
            }}
            function openModal(docKey, pageNum) {{
              ensureOverlay();
              var item = root.__yuktInlinePdfData[docKey];
              if (!item || !item.fid) return;
              var pg = parseInt(pageNum || "1", 10);
              if (!Number.isFinite(pg) || pg < 1) pg = 1;
              var ov2 = doc.getElementById("yukt-inline-pdf-modal");
              if (!ov2) return;
              var ti = doc.getElementById("yukt-inline-pdf-title");
              if (ti) ti.textContent = item.name || "Manual";
              var fr = doc.getElementById("yukt-inline-pdf-frame");
              if (!fr) return;
              var pdfUrl = API_BASE + "/pdf/" + encodeURIComponent(item.fid);
              var viewerUrl = API_BASE + "/pdfjs/web/viewer.html?v=4&file=" + encodeURIComponent(pdfUrl) + "#page=" + pg + "&zoom=120";
              fr.src = viewerUrl;
              ov2.style.display = "block";
            }}
            root.__yuktInlinePdfOpen = openModal;
            function resolveOpenEl(t, ev) {{
              if (!t) return null;
              var el = t.closest ? t.closest(".yukt-inline-page-open") : null;
              if (!el && t.closest) el = t.closest(".yukt-inline-source-open");
              if (!el && t.closest) el = t.closest(".yukt-page-ref");
              if (!el && t.closest) {{
                var sp = t.closest("span[title]");
                if (sp) {{
                  var ti = (sp.getAttribute("title") || "").toString();
                  if (ti.indexOf("|") > 0) el = sp;
                }}
              }}
              if (!el && ev && typeof ev.composedPath === "function") {{
                var path = ev.composedPath();
                for (var j = 0; j < path.length; j++) {{
                  var n = path[j];
                  if (
                    n &&
                    n.nodeType === 1 &&
                    n.classList &&
                    (n.classList.contains("yukt-inline-page-open") || n.classList.contains("yukt-inline-source-open"))
                  ) {{
                    el = n;
                    break;
                  }}
                }}
              }}
              return el;
            }}
            function readDocKeyPage(el) {{
              var dk = (el.getAttribute("data-yukt-doc-key") || "").toString();
              var pg = (el.getAttribute("data-yukt-page") || "").toString();
              if (!dk || !pg) {{
                var ttl = (el.getAttribute("title") || "").toString();
                var bar = ttl.indexOf("|");
                if (bar > 0) {{
                  dk = ttl.slice(0, bar);
                  pg = ttl.slice(bar + 1);
                }}
              }}
              if (dk && !pg) pg = "1";
              return {{ dk: dk, pg: pg }};
            }}
            var oldDoc = root.__yuktInlinePdfBoundDoc || null;
            if (oldDoc && root.__yuktInlinePdfOnClick) {{
              try {{ oldDoc.removeEventListener("click", root.__yuktInlinePdfOnClick, true); }} catch (eR1) {{}}
            }}
            if (oldDoc && root.__yuktInlinePdfOnKeydown) {{
              try {{ oldDoc.removeEventListener("keydown", root.__yuktInlinePdfOnKeydown, true); }} catch (eR2) {{}}
            }}
            if (oldDoc && root.__yuktInlinePdfOnEscape) {{
              try {{ oldDoc.removeEventListener("keydown", root.__yuktInlinePdfOnEscape, true); }} catch (eR3) {{}}
            }}
            if (oldDoc && root.__yuktInlinePdfOnModalCloseClick) {{
              try {{ oldDoc.removeEventListener("click", root.__yuktInlinePdfOnModalCloseClick, true); }} catch (eR4) {{}}
            }}
            root.__yuktInlinePdfOnClick = function(ev) {{
                if (ev.button !== 0) return;
                var el = resolveOpenEl(ev.target, ev);
                if (!el) return;
                var rp = readDocKeyPage(el);
                if (!rp.dk) return;
                ev.preventDefault();
                ev.stopPropagation();
                openModal(rp.dk, rp.pg);
            }};
            root.__yuktInlinePdfOnKeydown = function(ev) {{
                if (ev.key !== "Enter" && ev.key !== " ") return;
                var t = ev.target;
                var hasClass =
                  t &&
                  t.classList &&
                  (t.classList.contains("yukt-inline-page-open") ||
                    t.classList.contains("yukt-inline-source-open") ||
                    t.classList.contains("yukt-page-ref"));
                var hasTitle = t && t.getAttribute && (t.getAttribute("title") || "").indexOf("|") > 0;
                if (!t || (!hasClass && !hasTitle)) return;
                var rp = readDocKeyPage(t);
                if (!rp.dk) return;
                ev.preventDefault();
                openModal(rp.dk, rp.pg);
            }};
            root.__yuktInlinePdfOnEscape = function(ev) {{
                if (ev.key !== "Escape") return;
                if (root.__yuktInlinePdfClose) root.__yuktInlinePdfClose();
            }};
            root.__yuktInlinePdfOnModalCloseClick = function(ev) {{
                var t = ev.target;
                if (!t) return;
                if (t.id === "yukt-inline-pdf-close" || t.id === "yukt-inline-pdf-modal") {{
                    if (root.__yuktInlinePdfClose) root.__yuktInlinePdfClose();
                    ev.preventDefault();
                    ev.stopPropagation();
                }}
            }};
            doc.addEventListener("click", root.__yuktInlinePdfOnClick, true);
            doc.addEventListener("click", root.__yuktInlinePdfOnModalCloseClick, true);
            doc.addEventListener("keydown", root.__yuktInlinePdfOnKeydown, true);
            doc.addEventListener("keydown", root.__yuktInlinePdfOnEscape, true);
            root.__yuktInlinePdfBoundDoc = doc;
            ensureOverlay();
          }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _render_inline_video_modal_bridge(payload: dict[str, Any] | None = None) -> None:
    """Merge video payloads into host JS, populate inline anchor placeholders with <video> players,
    and wire timestamp spans to seek the matching inline player. No modal/overlay is used."""
    pl = payload if isinstance(payload, dict) else {}
    js_payload = json.dumps(pl).replace("</", "<\\/")
    import urllib.parse as _urlparse
    _parsed = _urlparse.urlparse(QNA_API_BASE)
    api_port_js = json.dumps(_parsed.port or 8009)
    components.html(
        f"""
        <script>
        (function() {{
          try {{
            var root;
            try {{
              root = (window.parent && window.parent !== window) ? window.parent : window;
            }} catch (e0) {{ root = window; }}
            var doc = root.document;
            var API_BASE = root.location.protocol + "//" + root.location.hostname + ":" + {api_port_js};
            root.__yuktInlineVideoData = root.__yuktInlineVideoData || {{}};
            var incoming = {js_payload};
            for (var k in incoming) {{
              if (Object.prototype.hasOwnProperty.call(incoming, k)) root.__yuktInlineVideoData[k] = incoming[k];
            }}

            // Tear down any leftover modal overlay from a previous build.
            var oldOv = doc.getElementById("yukt-inline-video-modal");
            if (oldOv && oldOv.parentNode) oldOv.parentNode.removeChild(oldOv);

            function getPlayerForKey(videoKey) {{
              if (!videoKey) return null;
              var sel = '[data-yukt-video-anchor][data-yukt-video-key="' + (window.CSS && CSS.escape ? CSS.escape(videoKey) : videoKey) + '"] video';
              return doc.querySelector(sel);
            }}
            function populateAnchors() {{
              var anchors = doc.querySelectorAll('[data-yukt-video-anchor][data-yukt-video-key]');
              for (var i = 0; i < anchors.length; i++) {{
                var a = anchors[i];
                if (a.getAttribute("data-yukt-populated") === "1") continue;
                var vk = a.getAttribute("data-yukt-video-key") || "";
                var item = root.__yuktInlineVideoData[vk];
                if (!item || !item.fid) continue;
                var url = API_BASE + "/video/" + encodeURIComponent(item.fid);
                a.innerHTML = "";
                var v = doc.createElement("video");
                v.controls = true;
                v.preload = "metadata";
                v.playsInline = true;
                v.className = "yukt-inline-video-player";
                v.style.cssText = "display:block;width:100%;max-width:560px;border-radius:0.5rem;background:#000;margin:0.4rem 0;";
                v.src = url;
                a.appendChild(v);
                a.setAttribute("data-yukt-populated", "1");
              }}
            }}
            populateAnchors();
            // Re-scan periodically: chat messages stream/rerender across Streamlit fragment updates.
            if (root.__yuktInlineVideoPopulateInterval) {{
              try {{ clearInterval(root.__yuktInlineVideoPopulateInterval); }} catch (eC) {{}}
            }}
            root.__yuktInlineVideoPopulateInterval = setInterval(populateAnchors, 600);

            function seekTo(vp, sec) {{
              if (!vp || !Number.isFinite(sec) || sec < 0) return;
              function applySeek() {{
                try {{ vp.currentTime = sec; }} catch (eS) {{}}
                try {{ vp.play().catch(function() {{}}); }} catch (eP) {{}}
              }}
              if (vp.readyState >= 1 && Number.isFinite(vp.duration) && vp.duration > 0) {{
                applySeek();
              }} else {{
                var onLoaded = function() {{
                  vp.removeEventListener("loadedmetadata", onLoaded);
                  applySeek();
                }};
                vp.addEventListener("loadedmetadata", onLoaded);
              }}
            }}
            function seekInlinePlayer(videoKey, startSec) {{
              populateAnchors();
              var vp = getPlayerForKey(videoKey);
              if (!vp) return;
              try {{ vp.scrollIntoView({{behavior: "smooth", block: "nearest"}}); }} catch (eSc) {{}}
              var startNum = Number(startSec);
              if (!Number.isFinite(startNum) || startNum < 0) startNum = 0;
              seekTo(vp, startNum);
            }}
            root.__yuktInlineVideoOpen = seekInlinePlayer;
            function resolveOpenEl(t, ev) {{
              if (!t) return null;
              var el = t.closest ? t.closest(".yukt-inline-video-time-open") : null;
              if (!el && ev && typeof ev.composedPath === "function") {{
                var path = ev.composedPath();
                for (var j = 0; j < path.length; j++) {{
                  var n = path[j];
                  if (
                    n &&
                    n.nodeType === 1 &&
                    n.classList &&
                    n.classList.contains("yukt-inline-video-time-open")
                  ) {{
                    el = n;
                    break;
                  }}
                }}
              }}
              return el;
            }}
            function readVideoKeyStart(el) {{
              var vk = (el.getAttribute("data-yukt-video-key") || "").toString();
              var ss = (el.getAttribute("data-yukt-start-sec") || "").toString();
              var n = parseFloat(ss);
              if (!Number.isFinite(n) || n < 0) n = 0;
              return {{ vk: vk, start: n }};
            }}
            var oldDoc = root.__yuktInlineVideoBoundDoc || null;
            if (oldDoc && root.__yuktInlineVideoOnClick) {{
              try {{ oldDoc.removeEventListener("click", root.__yuktInlineVideoOnClick, true); }} catch (eR1) {{}}
            }}
            if (oldDoc && root.__yuktInlineVideoOnKeydown) {{
              try {{ oldDoc.removeEventListener("keydown", root.__yuktInlineVideoOnKeydown, true); }} catch (eR2) {{}}
            }}
            root.__yuktInlineVideoOnClick = function(ev) {{
                if (ev.button !== 0) return;
                var el = resolveOpenEl(ev.target, ev);
                if (!el) return;
                var rp = readVideoKeyStart(el);
                if (!rp.vk) return;
                ev.preventDefault();
                ev.stopPropagation();
                seekInlinePlayer(rp.vk, rp.start);
            }};
            root.__yuktInlineVideoOnKeydown = function(ev) {{
                if (ev.key !== "Enter" && ev.key !== " ") return;
                var t = ev.target;
                if (
                  !t ||
                  !t.classList ||
                  !t.classList.contains("yukt-inline-video-time-open")
                ) return;
                var rp = readVideoKeyStart(t);
                if (!rp.vk) return;
                ev.preventDefault();
                seekInlinePlayer(rp.vk, rp.start);
            }};
            doc.addEventListener("click", root.__yuktInlineVideoOnClick, true);
            doc.addEventListener("keydown", root.__yuktInlineVideoOnKeydown, true);
            root.__yuktInlineVideoBoundDoc = doc;
          }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _render_inline_image_zoom_bridge() -> None:
    """Host-side image lightbox with zoom + gallery slider for assistant response images."""
    components.html(
        """
        <script>
        (function() {
          try {
            var root;
            try { root = (window.parent && window.parent !== window) ? window.parent : window; } catch (e0) { root = window; }
            var doc = root.document;
            root.__yuktImgGallery = root.__yuktImgGallery || [];
            root.__yuktImgIndex = Number(root.__yuktImgIndex || 0);
            function ensureImageOverlay() {
              var ov = doc.getElementById("yukt-inline-image-modal");
              if (!ov) {
                ov = doc.createElement("div");
                ov.id = "yukt-inline-image-modal";
                ov.style.cssText = "display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:1000000;";
                ov.innerHTML =
                  '<div id="yukt-inline-image-stage" style="position:absolute;inset:5% 6%;display:flex;align-items:center;justify-content:center;">' +
                  '<button type="button" id="yukt-img-prev" title="Previous image" style="position:absolute;left:8px;top:50%;transform:translateY(-50%);width:38px;height:38px;border:0;border-radius:999px;cursor:pointer;opacity:.9;">‹</button>' +
                  '<img id="yukt-inline-image-view" alt="image" style="max-width:100%;max-height:100%;transform-origin:center center;transition:transform .08s linear;box-shadow:0 8px 30px rgba(0,0,0,.35);border-radius:8px;background:#fff;" />' +
                  '<button type="button" id="yukt-img-next" title="Next image" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);width:38px;height:38px;border:0;border-radius:999px;cursor:pointer;opacity:.9;">›</button>' +
                  '</div>' +
                  '<div id="yukt-inline-image-controls" style="position:absolute;top:10px;right:12px;display:flex;gap:8px;align-items:center;">' +
                  '<span id="yukt-img-count" style="color:#fff;background:rgba(0,0,0,.45);padding:4px 8px;border-radius:6px;font-size:12px;min-width:56px;text-align:center;">1/1</span>' +
                  '<button type="button" id="yukt-img-zoom-out" title="Zoom out" style="width:34px;height:34px;border:0;border-radius:6px;cursor:pointer;">−</button>' +
                  '<button type="button" id="yukt-img-zoom-in" title="Zoom in" style="width:34px;height:34px;border:0;border-radius:6px;cursor:pointer;">+</button>' +
                  '<button type="button" id="yukt-img-zoom-reset" title="Reset zoom" style="height:34px;border:0;border-radius:6px;padding:0 10px;cursor:pointer;">100%</button>' +
                  '<button type="button" id="yukt-inline-image-close" title="Close" style="width:34px;height:34px;border:0;border-radius:6px;cursor:pointer;">×</button>' +
                  '</div>';
                doc.body.appendChild(ov);
              }
              var img = doc.getElementById("yukt-inline-image-view");
              var prevBtn = doc.getElementById("yukt-img-prev");
              var nextBtn = doc.getElementById("yukt-img-next");
              var count = doc.getElementById("yukt-img-count");
              var zoomOut = doc.getElementById("yukt-img-zoom-out");
              var zoomIn = doc.getElementById("yukt-img-zoom-in");
              var zoomReset = doc.getElementById("yukt-img-zoom-reset");
              var closeBtn = doc.getElementById("yukt-inline-image-close");
              root.__yuktImgScale = Number(root.__yuktImgScale || 1);
              function clampScale(v) {
                if (!Number.isFinite(v)) return 1;
                return Math.max(0.25, Math.min(6, v));
              }
              function applyScale(v) {
                root.__yuktImgScale = clampScale(v);
                if (img) img.style.transform = "scale(" + root.__yuktImgScale.toFixed(3) + ")";
                if (zoomReset) zoomReset.textContent = Math.round(root.__yuktImgScale * 100) + "%";
              }
              function updateCount() {
                if (!count) return;
                var total = (root.__yuktImgGallery || []).length;
                var idx = Number(root.__yuktImgIndex || 0) + 1;
                if (total < 1) {
                  count.textContent = "0/0";
                  return;
                }
                count.textContent = String(idx) + "/" + String(total);
              }
              function setImageAt(index) {
                var arr = root.__yuktImgGallery || [];
                if (!arr.length || !img) return;
                var n = Number(index || 0);
                if (!Number.isFinite(n)) n = 0;
                if (n < 0) n = arr.length - 1;
                if (n >= arr.length) n = 0;
                root.__yuktImgIndex = n;
                img.src = arr[n];
                applyScale(3);
                updateCount();
              }
              function step(delta) {
                var arr = root.__yuktImgGallery || [];
                if (!arr.length) return;
                setImageAt((Number(root.__yuktImgIndex || 0) + Number(delta || 0)));
              }
              function closeImageModal() {
                if (!ov) return;
                ov.style.display = "none";
                if (img) img.removeAttribute("src");
                applyScale(1);
              }
              root.__yuktCloseImageModal = closeImageModal;
              root.__yuktOpenImageModal = function(src, fromEl) {
                ensureImageOverlay();
                if (!ov || !img || !src) return;
                var scope = null;
                if (fromEl && fromEl.closest) {
                  scope = fromEl.closest(".yukt-bubble-assistant");
                }
                var nodes = Array.prototype.slice.call(
                  (scope || doc).querySelectorAll(".yukt-bubble-assistant img.yukt-inline-response-image, img.yukt-inline-response-image")
                );
                if (scope) {
                  nodes = Array.prototype.slice.call(scope.querySelectorAll("img.yukt-inline-response-image"));
                }
                var arr = [];
                for (var i = 0; i < nodes.length; i++) {
                  var s = (nodes[i].getAttribute("src") || "").toString();
                  if (s) arr.push(s);
                }
                if (!arr.length) arr = [src];
                root.__yuktImgGallery = arr;
                var idx = arr.indexOf(src);
                if (idx < 0 && fromEl) {
                  for (var j = 0; j < nodes.length; j++) {
                    if (nodes[j] === fromEl) { idx = j; break; }
                  }
                }
                if (idx < 0) idx = 0;
                root.__yuktImgIndex = idx;
                ov.style.display = "block";
                setImageAt(idx);
              };
              root.__yuktStepImageModal = step;
              ov.onclick = function(ev) {
                var t = ev.target;
                if (!t) return;
                if (t.id === "yukt-inline-image-view") return;
                var controls = doc.getElementById("yukt-inline-image-controls");
                if (controls && controls.contains(t)) return;
                if (
                  t.id === "yukt-img-prev" ||
                  t.id === "yukt-img-next" ||
                  (t.closest && (t.closest("#yukt-img-prev") || t.closest("#yukt-img-next")))
                )
                  return;
                closeImageModal();
              };
              if (prevBtn) prevBtn.onclick = function() { step(-1); };
              if (nextBtn) nextBtn.onclick = function() { step(1); };
              if (closeBtn) closeBtn.onclick = closeImageModal;
              if (zoomIn) zoomIn.onclick = function() { applyScale((root.__yuktImgScale || 1) * 1.2); };
              if (zoomOut) zoomOut.onclick = function() { applyScale((root.__yuktImgScale || 1) / 1.2); };
              if (zoomReset) zoomReset.onclick = function() { applyScale(1); };
              if (img && !img.__yuktWheelBound) {
                img.__yuktWheelBound = true;
                img.addEventListener("wheel", function(ev) {
                  ev.preventDefault();
                  var s = root.__yuktImgScale || 1;
                  if (ev.deltaY < 0) s = s * 1.1;
                  else s = s / 1.1;
                  applyScale(s);
                }, { passive: false });
              }
              return ov;
            }
            ensureImageOverlay();
            var oldDoc = root.__yuktInlineImageBoundDoc || null;
            if (oldDoc && root.__yuktInlineImageOnClick) {
              try { oldDoc.removeEventListener("click", root.__yuktInlineImageOnClick, true); } catch (e1) {}
            }
            if (oldDoc && root.__yuktInlineImageOnKeydown) {
              try { oldDoc.removeEventListener("keydown", root.__yuktInlineImageOnKeydown, true); } catch (e2) {}
            }
            root.__yuktInlineImageOnClick = function(ev) {
              var t = ev.target;
              if (!t) return;
              var img = (t.tagName === "IMG") ? t : (t.closest ? t.closest("img") : null);
              if (!img) return;
              var inBubble = img.closest ? img.closest(".yukt-bubble-assistant") : null;
              if (!inBubble) return;
              if (img.classList && !img.classList.contains("yukt-inline-response-image")) return;
              var src = (img.getAttribute("src") || "").toString();
              if (!src) return;
              ev.preventDefault();
              ev.stopPropagation();
              if (root.__yuktOpenImageModal) root.__yuktOpenImageModal(src, img);
            };
            root.__yuktInlineImageOnKeydown = function(ev) {
              var kt = ev.target;
              if (
                kt &&
                kt.tagName === "IMG" &&
                kt.classList &&
                kt.classList.contains("yukt-inline-response-image") &&
                (ev.key === "Enter" || ev.key === " ")
              ) {
                var ksrc = (kt.getAttribute("src") || "").toString();
                if (ksrc && root.__yuktOpenImageModal) {
                  ev.preventDefault();
                  root.__yuktOpenImageModal(ksrc, kt);
                  return;
                }
              }
              if (ev.key === "Escape") {
                if (root.__yuktCloseImageModal) root.__yuktCloseImageModal();
                return;
              }
              var ov = doc.getElementById("yukt-inline-image-modal");
              if (!ov || ov.style.display !== "block") return;
              if (ev.key === "ArrowRight") {
                if (root.__yuktStepImageModal) root.__yuktStepImageModal(1);
              } else if (ev.key === "ArrowLeft") {
                if (root.__yuktStepImageModal) root.__yuktStepImageModal(-1);
              }
            };
            doc.addEventListener("click", root.__yuktInlineImageOnClick, true);
            doc.addEventListener("keydown", root.__yuktInlineImageOnKeydown, true);
            root.__yuktInlineImageBoundDoc = doc;
          } catch (e) {}
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _maybe_scroll_back_to_clicked_message() -> None:
    aid = str(st.session_state.pop("_yukt_scroll_target", "") or "").strip()
    if not aid:
        return
    esc = html_module.escape(aid, quote=True)
    st.html(
        f"""
        <script>
        (function() {{
          try {{
            var host = (window.parent && window.parent !== window) ? window.parent : window;
            var doc = host.document;
            function scrollParents(node) {{
              var cur = node;
              while (cur && cur !== doc.body) {{
                try {{
                  var st = host.getComputedStyle(cur);
                  var oy = st ? st.overflowY : "";
                  if ((oy === "auto" || oy === "scroll") && cur.scrollHeight > cur.clientHeight) {{
                    cur.scrollTop = Math.max(0, node.offsetTop - 120);
                  }}
                }} catch (e0) {{}}
                cur = cur.parentElement;
              }}
              var main = doc.querySelector('section[data-testid="stMain"]');
              if (main) main.scrollTop = main.scrollHeight;
              var app = doc.querySelector('[data-testid="stAppViewContainer"]');
              if (app) app.scrollTop = app.scrollHeight;
            }}
            function go() {{
              var el = doc.getElementById("{esc}");
              if (!el) return false;
              try {{
                el.scrollIntoView({{ behavior: "smooth", block: "nearest" }});
              }} catch (e1) {{
                el.scrollIntoView(true);
              }}
              scrollParents(el);
              try {{
                host.scrollTo({{ top: doc.body.scrollHeight, behavior: "smooth" }});
              }} catch (e2) {{
                host.scrollTo(0, doc.body.scrollHeight);
              }}
              return true;
            }}
            if (go()) return;
            var attempts = 0;
            var t = host.setInterval(function() {{
              attempts += 1;
              if (go() || attempts >= 18) {{
                host.clearInterval(t);
              }}
            }}, 120);
          }} catch (e) {{}}
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )

def _ensure_query_in_view_during_stream() -> None:
    """Keep latest query/response area in view while streaming is active."""
    st.html(
        """
        <script>
        (function() {
          try {
            var host = (window.parent && window.parent !== window) ? window.parent : window;
            var doc = host.document;
            var input = doc.querySelector('[data-testid="stChatInput"] textarea');
            if (!input) return;
            var box = input.closest('[data-testid="stChatInput"]');
            var target = box || input;
            try {
              target.scrollIntoView({ behavior: "smooth", block: "end" });
            } catch (e1) {
              target.scrollIntoView(false);
            }
          } catch (e) {}
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _scroll_to_latest_chat_turn_once() -> None:
    """Scroll to the latest chat input/message area after a fresh submit."""
    if not st.session_state.pop("_yukt_scroll_to_latest_on_submit", False):
        return
    st.html(
        """
        <script>
        (function() {
          try {
            var host = (window.parent && window.parent !== window) ? window.parent : window;
            var doc = host.document;
            function findTarget() {
              var input = doc.querySelector('[data-testid="stChatInput"] textarea');
              if (!input) return null;
              return input.closest('[data-testid="stChatInput"]') || input;
            }
            function go() {
              var target = findTarget();
              if (!target) return false;
              try {
                target.scrollIntoView({ behavior: "smooth", block: "end" });
              } catch (e1) {
                target.scrollIntoView(false);
              }
              try {
                host.scrollTo({ top: doc.body.scrollHeight, behavior: "smooth" });
              } catch (e2) {
                host.scrollTo(0, doc.body.scrollHeight);
              }
              var main = doc.querySelector('section[data-testid="stMain"]');
              if (main) main.scrollTop = main.scrollHeight;
              var app = doc.querySelector('[data-testid="stAppViewContainer"]');
              if (app) app.scrollTop = app.scrollHeight;
              return true;
            }
            if (go()) return;
            var attempts = 0;
            var t = host.setInterval(function() {
              attempts += 1;
              if (go() || attempts >= 18) {
                host.clearInterval(t);
              }
            }, 120);
          } catch (e) {}
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _render_pdf_iframe_fallback(file_path: str, *, height: int = 900) -> None:
    """Fallback renderer when Streamlit's PDF component assets are unavailable."""
    with open(file_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode("ascii")
    # Hide native PDF toolbar to disable actions like download/print/comment/sign/draw.
    src = f'data:application/pdf;base64,{pdf_b64}#toolbar=0&navpanes=0&view=FitH'
    st.html(
        (
            '<iframe src="'
            + src
            + '" width="100%" height="'
            + str(max(300, int(height)))
            + '" style="border:none;border-radius:8px;"></iframe>'
        )
    )


def _render_pdf_page_image_view(file_path: str, *, initial_page: int | None = None) -> None:
    """
    Render PDF as page images (no native PDF toolbar/actions).
    This removes browser/pdf.js tools like download/print/comment/sign/draw.
    """
    try:
        import pypdfium2 as pdfium
    except Exception:
        print("PDF_RENDER_MODE=image_only import_failed:pypdfium2", file=sys.stderr, flush=True)
        st.error("PDF image renderer requires pypdfium2. Install: pip install pypdfium2")
        return

    try:
        doc = pdfium.PdfDocument(file_path)
        total = len(doc)
        if total <= 0:
            st.warning("PDF has no pages.")
            return
    except Exception as e:
        print(f"PDF_RENDER_MODE=image_only open_failed:{e}", file=sys.stderr, flush=True)
        st.error(f"Unable to open PDF: {e}")
        return

    default_page = int(initial_page) if isinstance(initial_page, int) and initial_page >= 1 else 1
    if default_page > total:
        default_page = total

    fid = hashlib.sha1(os.path.abspath(file_path).encode("utf-8")).hexdigest()[:10]
    page_no = st.number_input(
        "Page",
        min_value=1,
        max_value=int(total),
        value=int(default_page),
        step=1,
        key=f"yukt_pdf_page_picker_{fid}",
    )
    idx = int(page_no) - 1
    try:
        page = doc[idx]
        # Scale tuned for readability while keeping render latency acceptable.
        pil_img = page.render(scale=2.0).to_pil()
        st.image(pil_img, use_container_width=True)
        st.caption(f"Page {int(page_no)} / {total}")
    except Exception as e:
        st.error(f"Unable to render PDF page: {e}")


def display_pdf(file_path: str, *, scroll_to_page: int | None = None) -> None:
    """Prefer pdf.js viewer (``streamlit-pdf-viewer``) for scroll-to-page; fall back to ``st.pdf``."""
    page: int | None = None
    if scroll_to_page is not None:
        try:
            pi = int(scroll_to_page)
        except (TypeError, ValueError):
            pi = -1
        page = pi if pi >= 1 else None

    # Force a toolbar-free viewer: render selected page as an image.
    # We intentionally avoid streamlit-pdf-viewer, st.pdf, and browser PDF UI (iframe),
    # because those expose annotation/download/print toolbars.
    try:
        print("PDF_RENDER_MODE=image_only active", file=sys.stderr, flush=True)
        _render_pdf_page_image_view(file_path, initial_page=page)
        st.caption("Loaded in read-only mode (toolbar disabled).")
        return
    except Exception as fallback_exc:
        print(f"PDF_RENDER_MODE=image_only failed:{fallback_exc}", file=sys.stderr, flush=True)
        st.error(f"Unable to load PDF: {fallback_exc}")


@st.dialog(" ", width="large")
def show_pdf_dialog(file_path: str, *, scroll_to_page: int | None = None) -> None:
    st.markdown(
        f'<div class="yukt-pdf-title">{html_module.escape(os.path.basename(file_path))}</div>',
        unsafe_allow_html=True,
    )
    st.caption("Viewer mode: read-only image renderer (no PDF toolbar)")
    display_pdf(file_path, scroll_to_page=scroll_to_page)


@st.dialog(" ", width="large")
def show_video_dialog(file_path: str, *, display_name: str | None = None) -> None:
    label = display_name or os.path.basename(file_path)
    st.markdown(
        f'<div class="yukt-pdf-title">{html_module.escape(label)}</div>',
        unsafe_allow_html=True,
    )
    try:
        st.video(file_path)
    except Exception as e:
        st.error(f"Unable to load video: {e}")


def _is_transient_connect_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.URLError):
        r = exc.reason
        if isinstance(r, (ConnectionRefusedError, TimeoutError, BrokenPipeError)):
            return True
        if isinstance(r, OSError) and getattr(r, "errno", None) in (
            errno.ECONNREFUSED,
            errno.ETIMEDOUT,
            errno.ECONNRESET,
        ):
            return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def _urlopen_with_connect_retry(
    req: urllib.request.Request,
    *,
    timeout: int,
    log_tcp_open: bool = True,
):
    """Retry when the API is still starting or briefly unavailable (avoids race with uvicorn)."""
    url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
    method = req.get_method()
    flog = _frontend_logger()
    for attempt in range(40):
        t_open = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            if log_tcp_open:
                flog.info(
                    "frontend_api tcp_open method=%s url=%s http_status=%s open_wall_sec=%.4f",
                    method,
                    url,
                    resp.getcode(),
                    time.perf_counter() - t_open,
                )
            return resp
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            if not _is_transient_connect_error(e) or attempt >= 39:
                raise
            flog.warning(
                "frontend_http_connect_retry attempt=%d/40 sleep_sec=0.5 read_timeout_sec=%d url=%s err=%r",
                attempt + 1,
                timeout,
                url,
                e,
            )
            time.sleep(0.5)
    raise AssertionError("_urlopen_with_connect_retry: unreachable")


def _api_health_ok(*, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(f"{QNA_API_BASE}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200) == 200
    except Exception:
        return False


def _run_rag_and_generate(question: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Frontend delegates AI/RAG orchestration to backend API."""
    flog = _frontend_logger()
    t_http = time.perf_counter()
    flog.info(
        "frontend_api_call begin method=POST path=/chat/ask read_timeout_sec=%d",
        CHAT_ASK_TIMEOUT_SEC,
    )
    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{QNA_API_BASE}/chat/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen_with_connect_retry(req, timeout=CHAT_ASK_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"Backend API HTTP {e.code}: {msg}") from e
    except Exception as e:
        raise RuntimeError(
            f"Backend API unavailable at {QNA_API_BASE}. "
            "Start backend (`uvicorn api:app --port 8009` with `doc-qna/backend` on PYTHONPATH) and retry."
        ) from e
    answer = str(data.get("answer", "")).strip()
    sources = data.get("sources") or []
    images = data.get("images") or []
    if not isinstance(sources, list):
        sources = []
    if not isinstance(images, list):
        images = []
    flog.info(
        "frontend_api_call end method=POST path=/chat/ask duration_sec=%.4f answer_chars=%d sources=%d",
        time.perf_counter() - t_http,
        len(answer),
        len(sources),
    )
    return answer, sources, images


def _sse_chat_token_generator(question: str, done_holder: dict[str, Any]):
    """Yields token strings from SSE; fills ``done_holder`` with answer + sources on ``done``."""
    flog = _frontend_logger()
    t0 = time.perf_counter()
    flog.info(
        "frontend_api_call begin method=POST path=/chat/ask/stream read_timeout_sec=%d",
        CHAT_ASK_TIMEOUT_SEC,
    )
    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{QNA_API_BASE}/chat/ask/stream",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    first_sse_token = False
    with _urlopen_with_connect_retry(req, timeout=CHAT_ASK_TIMEOUT_SEC) as resp:
        while True:
            raw_line = resp.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            obj = json.loads(line[6:])
            ot = obj.get("type")
            if ot == "delta":
                t = obj.get("text") or ""
                if t:
                    # As soon as token text starts, hide the transient "Analyzing ..." progress line
                    # for this batch so the streamed answer is the only visible content.
                    prev_progress = str(done_holder.get("progress") or "").strip()
                    if prev_progress:
                        done_holder["progress_fade_text"] = prev_progress
                        done_holder["progress_fade_until"] = time.time() + 0.45
                    done_holder["progress"] = ""
                    if not first_sse_token:
                        pv = t[:120] if len(t) > 120 else t
                        flog.info(
                            "frontend_sse first_token_sec=%.4f after_http_begin preview=%r",
                            time.perf_counter() - t0,
                            pv,
                        )
                        first_sse_token = True
                        done_holder["first_token_ts"] = time.time()
                    done_holder["stream_chars"] = int(done_holder.get("stream_chars") or 0) + len(t)
                    done_holder["stream_text"] = str(done_holder.get("stream_text") or "") + t
                    prefetch_text = _maybe_schedule_streaming_tts_prefetch(
                        done_holder,
                        str(done_holder.get("stream_text") or ""),
                    )
                    if prefetch_text:
                        _ensure_tts_prefetch(STREAM_TTS_PENDING_MSG_KEY, prefetch_text)
                    yield t
                    pending_imgs = done_holder.get("pending_inline_images") or []
                    if (
                        isinstance(pending_imgs, list)
                        and pending_imgs
                        and not done_holder.get("inline_tags_inserted")
                        and int(done_holder.get("stream_chars") or 0) >= max(1, INLINE_IMAGE_INSERT_AFTER_CHARS)
                        and _is_safe_inline_image_boundary(str(done_holder.get("stream_text") or ""))
                    ):
                        done_holder["inline_tags_inserted"] = True
                        done_holder["pending_inline_images"] = []
                        yield _inline_image_tags_block(pending_imgs)
            elif ot == "text_reset":
                done_holder["stream_reset"] = True
                done_holder["inline_tags_inserted"] = False
                done_holder["stream_chars"] = 0
                done_holder["stream_text"] = ""
                done_holder["tts_prefetch_chars"] = 0
                done_holder["tts_prefetch_ts"] = 0.0
            elif ot == "done":
                done_holder["answer"] = str(obj.get("answer", "")).strip()
                if done_holder["answer"]:
                    _ensure_tts_prefetch(STREAM_TTS_PENDING_MSG_KEY, done_holder["answer"])
                sh = obj.get("sources") or []
                ih = obj.get("images") or []
                done_holder["sources"] = sh if isinstance(sh, list) else []
                done_holder["images"] = ih if isinstance(ih, list) else []
                if done_holder["images"] and not done_holder.get("inline_tags_inserted"):
                    done_holder["inline_tags_inserted"] = True
                    yield _inline_image_tags_block(done_holder["images"])
            elif ot == "images":
                ih = obj.get("images") or []
                if isinstance(ih, list):
                    done_holder["images"] = ih
                    done_holder["pending_inline_images"] = ih
            elif ot == "progress":
                done_holder["progress"] = str(obj.get("text") or "").strip()
                done_holder["progress_fade_text"] = ""
                done_holder["progress_fade_until"] = 0.0
                # Force UI refresh in blocking fallback even when no token text arrived yet.
                yield ""
            elif ot == "error":
                raise RuntimeError(obj.get("message", "stream error"))
    flog.info(
        "frontend_api_call end method=POST path=/chat/ask/stream duration_sec=%.4f first_sse_token=%s",
        time.perf_counter() - t0,
        first_sse_token,
    )


def _sse_clear_session_keys() -> None:
    for k in (
        "_sse_holder",
        "_sse_lock",
        "_sse_thread_started",
        "_sse_committed",
        "_sse_bound_question",
        "_sse_auto_tts_triggered",
    ):
        st.session_state.pop(k, None)


def _sse_dismiss_active_holder() -> None:
    """Tell the background reader to stop and close the HTTP body (avoids duplicate queued API jobs)."""
    h = st.session_state.get("_sse_holder")
    if isinstance(h, dict):
        h["dismissed"] = True


def _sse_clear_worker_keys() -> None:
    """Reset in-flight SSE worker state when ``rag_pending`` switches to a new question."""
    for k in (
        "_sse_holder",
        "_sse_lock",
        "_sse_thread_started",
        "_sse_committed",
        "_sse_auto_tts_triggered",
    ):
        st.session_state.pop(k, None)


def _sse_worker(question: str, holder: dict[str, Any], lock: threading.Lock) -> None:
    """Single HTTP/SSE read in a background thread so Streamlit reruns (sidebar, PDF) do not open duplicate streams."""
    flog = _frontend_logger()
    t0 = time.perf_counter()
    flog.info(
        "frontend_sse_worker begin question_chars=%d path=/chat/ask/stream",
        len(question or ""),
    )
    try:
        payload = json.dumps({"question": question}).encode("utf-8")
        req = urllib.request.Request(
            f"{QNA_API_BASE}/chat/ask/stream",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        first_tok = False
        with _urlopen_with_connect_retry(req, timeout=CHAT_ASK_TIMEOUT_SEC) as resp:
            while True:
                with lock:
                    if holder.get("dismissed"):
                        break
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                obj = json.loads(line[6:])
                ot = obj.get("type")
                if ot == "delta":
                    t = obj.get("text") or ""
                    if t:
                        prefetch_text = ""
                        if not first_tok:
                            flog.info(
                                "frontend_sse_worker first_token_sec=%.4f",
                                time.perf_counter() - t0,
                            )
                            first_tok = True
                            holder["first_token_ts"] = time.time()
                        with lock:
                            # Hide batch-level "Analyzing ..." status once real answer tokens arrive.
                            prev_progress = str(holder.get("progress") or "").strip()
                            if prev_progress:
                                holder["progress_fade_text"] = prev_progress
                                holder["progress_fade_until"] = time.time() + 0.45
                            holder["progress"] = ""
                            holder.setdefault("chunks", []).append(t)
                            holder["stream_chars"] = int(holder.get("stream_chars") or 0) + len(t)
                            holder["stream_text"] = str(holder.get("stream_text") or "") + t
                            prefetch_text = _maybe_schedule_streaming_tts_prefetch(
                                holder,
                                str(holder.get("stream_text") or ""),
                            )
                            pending_imgs = holder.get("pending_inline_images") or []
                            if (
                                isinstance(pending_imgs, list)
                                and pending_imgs
                                and not holder.get("inline_tags_inserted")
                                and int(holder.get("stream_chars") or 0) >= max(1, INLINE_IMAGE_INSERT_AFTER_CHARS)
                                and _is_safe_inline_image_boundary(str(holder.get("stream_text") or ""))
                            ):
                                holder["chunks"].append(_inline_image_tags_block(pending_imgs))
                                holder["inline_tags_inserted"] = True
                                holder["pending_inline_images"] = []
                        if prefetch_text:
                            _ensure_tts_prefetch(STREAM_TTS_PENDING_MSG_KEY, prefetch_text)
                elif ot == "text_reset":
                    # Backend replaced draft stream; clear visible tokens.
                    with lock:
                        holder["chunks"] = []
                        holder["inline_tags_inserted"] = False
                        holder["stream_chars"] = 0
                        holder["stream_text"] = ""
                        holder["tts_prefetch_chars"] = 0
                        holder["tts_prefetch_ts"] = 0.0
                        known_images = holder.get("images") or []
                        if (
                            known_images
                            and int(holder.get("stream_chars") or 0) >= max(1, INLINE_IMAGE_INSERT_AFTER_CHARS)
                            and _is_safe_inline_image_boundary(str(holder.get("stream_text") or ""))
                        ):
                            holder["chunks"].append(_inline_image_tags_block(known_images))
                            holder["inline_tags_inserted"] = True
                elif ot == "images":
                    ih = obj.get("images") or []
                    if isinstance(ih, list):
                        with lock:
                            holder["images"] = ih
                            holder["pending_inline_images"] = ih
                elif ot == "progress":
                    with lock:
                        holder["progress"] = str(obj.get("text") or "").strip()
                        holder["progress_fade_text"] = ""
                        holder["progress_fade_until"] = 0.0
                elif ot == "done":
                    final_answer = str(obj.get("answer", "")).strip()
                    if final_answer:
                        _ensure_tts_prefetch(STREAM_TTS_PENDING_MSG_KEY, final_answer)
                    with lock:
                        done_images = obj.get("images") if isinstance(obj.get("images"), list) else []
                        holder["images"] = done_images
                        if done_images and not holder.get("inline_tags_inserted"):
                            holder.setdefault("chunks", []).append(_inline_image_tags_block(done_images))
                            holder["inline_tags_inserted"] = True
                        holder["done_meta"] = {
                            "answer": final_answer,
                            "sources": obj.get("sources")
                            if isinstance(obj.get("sources"), list)
                            else [],
                            "images": done_images,
                        }
                elif ot == "error":
                    raise RuntimeError(obj.get("message", "stream error"))
        with lock:
            holder["finished"] = True
            was_dismissed = bool(holder.get("dismissed"))
        flog.info(
            "frontend_sse_worker end duration_sec=%.4f dismissed=%s",
            time.perf_counter() - t0,
            was_dismissed,
        )
    except Exception as e:
        flog.exception("frontend_sse_worker failed: %s", e)
        with lock:
            holder["error"] = str(e)
            holder["finished"] = True


def _stream_assistant_reply_blocking_fallback(
    question: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Used when ``st.fragment`` is unavailable: blocks the script until the stream finishes."""
    done_holder: dict[str, Any] = {}
    placeholder = st.empty()
    acc: list[str] = []
    last_flush = 0.0
    for chunk in _sse_chat_token_generator(question, done_holder):
        if done_holder.pop("stream_reset", None):
            acc.clear()
            imgs_known = done_holder.get("images") or []
            if (
                isinstance(imgs_known, list)
                and imgs_known
                and int(done_holder.get("stream_chars") or 0) >= max(1, INLINE_IMAGE_INSERT_AFTER_CHARS)
            ):
                acc.append(_inline_image_tags_block(imgs_known))
                done_holder["inline_tags_inserted"] = True
        if chunk:
            acc.append(chunk)
        now = time.perf_counter()
        if acc and now - last_flush >= 0.05:
            placeholder.markdown(
                _assistant_bubble_block_markdown("".join(acc)),
                unsafe_allow_html=True,
            )
            last_flush = now
    streamed_text = "".join(acc).strip()
    backend_final = str(done_holder.get("answer") or "").strip()
    images = done_holder.get("images") or []
    if not isinstance(images, list):
        images = []
    final = _compose_final_assistant_text(streamed_text, backend_final, images)
    placeholder.markdown(_assistant_bubble_block_markdown(final), unsafe_allow_html=True)
    sources = done_holder.get("sources", [])
    if not isinstance(sources, list):
        sources = []
    return final, sources, images


def _render_rag_stream_fragment() -> None:
    """Live-updating assistant bubble while the SSE worker runs (does not block full-app reruns).

    ``run_every`` only reruns this fragment; when the worker sets ``finished``, we call
    ``st.rerun()`` so ``main`` can persist the assistant turn (fragments never run the full script).
    """
    if not hasattr(st, "fragment") or not USE_FRAGMENT_STREAM:
        return

    @st.fragment(run_every=timedelta(milliseconds=100))
    def _rag_sse_poll() -> None:
        if not st.session_state.get("rag_pending"):
            return
        h = st.session_state.get("_sse_holder")
        if not h:
            return
        lock = st.session_state._sse_lock
        with lock:
            parts = list(h.get("chunks") or [])
            images = list(h.get("images") or [])
            progress = str(h.get("progress") or "").strip()
            progress_fade_text = str(h.get("progress_fade_text") or "").strip()
            progress_fade_until = float(h.get("progress_fade_until") or 0.0)
            first_token_ts = float(h.get("first_token_ts") or 0.0)
            finished = bool(h.get("finished"))
        if finished and not st.session_state.get("_sse_committed"):
            st.rerun()
            return
        col_ai, _ = st.columns([0.88, 0.12])
        with col_ai:
            stream_raw = "".join(parts) if parts else ""
            stream_tts_text = _strip_inline_image_tags(stream_raw).strip()
            if parts:
                body = "".join(parts)
                _render_assistant_content_with_inline_images(
                    body,
                    images,
                    show_fallback_gallery=False,
                )
            else:
                # Pre-first-token placeholder so the user sees activity instead
                # of a blank pane during retrieval / prompt-build / LLM warmup.
                msg = progress or "Thinking…"
                st.markdown(
                    f'<div class="yukt-stream-progress" '
                    f'style="font-style:italic;opacity:.75;padding:.4rem 0;">{msg}</div>',
                    unsafe_allow_html=True,
                )
            if first_token_ts > 0:
                rag_from_stt = bool(st.session_state.get("rag_pending_from_stt"))
                auto_done = bool(st.session_state.get("_sse_auto_tts_triggered"))
                elapsed_since_first_token = time.time() - first_token_ts
                # Voice queries get hands-free auto-TTS: wait 2 s after the
                # first response token so enough text exists to be worth
                # synthesizing, then kick off playback. The browser bridge
                # handles chunked synthesis as more tokens arrive.
                if (
                    rag_from_stt
                    and not auto_done
                    and elapsed_since_first_token >= 2.0
                    and stream_tts_text
                ):
                    st.session_state._sse_auto_tts_triggered = True
                    _request_tts_play(STREAM_TTS_PENDING_MSG_KEY, stream_tts_text)
                tts_active_msg_key = str(st.session_state.get("tts_active_msg_key") or "")
                tts_is_paused = bool(st.session_state.get("tts_is_paused", False))
                tts_pending = st.session_state.get("tts_pending_play")
                tts_pending_msg_key = ""
                if isinstance(tts_pending, dict):
                    tts_pending_msg_key = str(tts_pending.get("msg_key") or "")
                tts_label = "Speak"
                tts_disabled = False
                if tts_pending_msg_key == STREAM_TTS_PENDING_MSG_KEY:
                    tts_label = "⏳ Preparing..."
                    tts_disabled = True
                if tts_active_msg_key == STREAM_TTS_PENDING_MSG_KEY and not tts_is_paused:
                    tts_label = "⏸ Pause"
                    tts_disabled = False
                if st.button(tts_label, key="tts_speak_streaming_pending", disabled=tts_disabled):
                    if tts_active_msg_key == STREAM_TTS_PENDING_MSG_KEY and not tts_is_paused:
                        st.session_state.tts_browser_cmd = {
                            "action": "pause",
                            "msg_key": STREAM_TTS_PENDING_MSG_KEY,
                        }
                        st.session_state.tts_is_paused = True
                    elif tts_active_msg_key == STREAM_TTS_PENDING_MSG_KEY and tts_is_paused:
                        st.session_state.tts_browser_cmd = {
                            "action": "resume",
                            "msg_key": STREAM_TTS_PENDING_MSG_KEY,
                        }
                        st.session_state.tts_is_paused = False
                    elif stream_tts_text:
                        _request_tts_play(STREAM_TTS_PENDING_MSG_KEY, stream_tts_text)
            # Fragment reruns do not execute full ``main`` body; resolve/play browser TTS commands
            # here so Speak/Pause/Resume work while an answer is streaming.
            _resolve_pending_tts_play()
            _render_tts_audio_bridge(st.session_state.pop("tts_browser_cmd", None))

    _rag_sse_poll()


def _api_get_json(path: str, params: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    flog = _frontend_logger()
    t0 = time.perf_counter()
    url = f"{QNA_API_BASE}{path}"
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url = f"{url}?{q}"
    flog.info("frontend_api_call begin method=GET path=%s timeout_sec=%d", path, timeout)
    req = urllib.request.Request(url, method="GET")
    with _urlopen_with_connect_retry(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    flog.info(
        "frontend_api_call end method=GET path=%s duration_sec=%.4f response_chars=%d",
        path,
        time.perf_counter() - t0,
        len(raw),
    )
    return json.loads(raw)


def _api_post_json(path: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    flog = _frontend_logger()
    t0 = time.perf_counter()
    keys = list((payload or {}).keys()) if payload else []
    flog.info(
        "frontend_api_call begin method=POST path=%s timeout_sec=%d payload_keys=%s",
        path,
        timeout,
        keys,
    )
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{QNA_API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen_with_connect_retry(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    flog.info(
        "frontend_api_call end method=POST path=%s duration_sec=%.4f response_chars=%d",
        path,
        time.perf_counter() - t0,
        len(raw),
    )
    return json.loads(raw)


def _api_create_session() -> str:
    return str(_api_post_json("/sessions").get("session_id", "")).strip()


def _api_get_latest_session_id() -> str | None:
    sid = str(_api_get_json("/sessions/latest").get("session_id") or "").strip()
    return sid or None


def _api_list_sessions(limit: int = 100) -> list[dict[str, Any]]:
    rows = _api_get_json("/sessions", params={"limit": int(limit)})
    return rows if isinstance(rows, list) else []


def _api_load_messages_for_session(session_id: str) -> list[dict[str, Any]]:
    rows = _api_get_json(f"/sessions/{session_id}/messages")
    return rows if isinstance(rows, list) else []


def _api_append_message(
    session_id: str,
    *,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    payload: dict[str, Any] = {"role": role, "content": content, "sources": sources}
    if images is not None:
        payload["images"] = images
    out = _api_post_json(
        f"/sessions/{session_id}/messages",
        payload,
    )
    return int(out.get("message_id") or 0), str(out.get("created_at") or "")


def _api_stt_transcribe(audio_bytes: bytes, *, audio_format: str = "wav") -> str:
    if not audio_bytes:
        return ""
    _frontend_logger().info(
        "frontend_stt begin audio_format=%s audio_bytes=%d",
        audio_format,
        len(audio_bytes),
    )
    payload = {
        "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
        "audio_format": audio_format,
    }
    out = _api_post_json("/stt/transcribe", payload, timeout=STT_TIMEOUT_SEC)
    text = str(out.get("text") or "").strip()
    _frontend_logger().info(
        "frontend_stt done transcript_chars=%d preview=%r",
        len(text),
        text[:200],
    )
    return text


def _api_tts_synthesize(text: str, *, request_id: int | None = None) -> tuple[bytes, str]:
    txt = str(text or "").strip()
    if not txt:
        return b"", "audio/wav"
    payload: dict[str, Any] = {"text": txt}
    if request_id is not None:
        payload["request_id"] = int(request_id)
    try:
        out = _api_post_json("/tts/synthesize", payload, timeout=TTS_TIMEOUT_SEC)
    except urllib.error.HTTPError as e:
        if int(getattr(e, "code", 0)) == 409:
            return b"", "audio/wav"
        raise
    audio_b64 = str(out.get("audio_base64") or "")
    mime = str(out.get("mime_type") or "audio/wav")
    if not audio_b64:
        return b"", mime
    try:
        raw = base64.b64decode(audio_b64.encode("utf-8"), validate=True)
    except Exception:
        return b"", mime
    return raw, mime


def _stt_widget_audio_bytes() -> bytes:
    """Best-effort read of the hidden audio widget value from session_state."""
    try:
        blob = st.session_state.get("yukt_stt_audio")
        if blob is None:
            return b""
        if hasattr(blob, "getvalue"):
            raw = blob.getvalue()
            return raw if isinstance(raw, (bytes, bytearray)) else b""
    except Exception:
        return b""
    return b""


def _render_browser_stt_bridge() -> None:
    mic_uri = _icon_data_uri(UI_MIC_ICON_PATH)
    api_base_js = json.dumps(_browser_image_api_base()).replace("</", "<\\/")
    mic_uri_js = json.dumps(mic_uri).replace("</", "<\\/")
    components.html(
        f"""
        <script>
        (function() {{
          try {{
            var root = (window.parent && window.parent !== window) ? window.parent : window;
            var doc = root.document;
            var API_BASE = {api_base_js};
            var MIC_URI = {mic_uri_js};
            async function logEvent(eventName, detail) {{
              try {{
                await fetch(API_BASE + "/debug/client-log", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{
                    source: "browser_stt",
                    event: eventName,
                    detail: detail || {{}}
                  }})
                }});
              }} catch (e) {{
                console.error("client log failed", e);
              }}
            }}
            root.__yuktSttState = root.__yuktSttState || {{
              listening: false,
              processing: false,
              processingSince: 0,
              chunks: [],
              totalSamples: 0,
              audioContext: null,
              stream: null,
              source: null,
              processor: null,
              pollTimer: null,
              pollInflight: false,
              pollSeq: 0,
              lastPartialText: "",
              committedFinal: false,
              // Character-by-character streaming animation. Each partial
              // returns the full utterance so far; we animate the diff into
              // the chat input one character at a time so the user sees the
              // transcript materialize as they speak.
              displayedText: "",
              targetText: "",
              animTimer: null
            }};
            var S = root.__yuktSttState;
            var PROCESSING_STALE_MS = 15000;
            // Partial-transcribe poll cadence (ms). Each tick re-sends the
            // accumulated audio so far, so users see the input box update
            // every ~POLL_INTERVAL_MS as they speak.
            var POLL_INTERVAL_MS = 500;
            // Minimum audio before the first poll fires. Together with the
            // first-poll setTimeout below this sets the floor on
            // first-character latency: whisper needs *some* audio to decode,
            // and anything under ~0.10s reliably returns [BLANK_AUDIO]. 0.15s
            // is the sweet spot — fast enough to feel instant, long enough
            // that the very first decode produces a real word for fast
            // talkers.
            var POLL_MIN_SAMPLES_RATIO = 0.15;
            var CHAR_ANIM_DELAY_MS = 15; // per-character typing delay while streaming
            // Sliding-window cap (seconds) for partial transcribes. Whisper
            // re-decodes the entire buffer per call, so without a cap a 30 s
            // utterance turns the last partial into a 10-15 s blocking call.
            // We send only the most recent slice to whisper for partials and
            // keep the full buffer for the final transcribe on stop. The user
            // still sees streaming text; only the visible "trailing prefix"
            // differs vs. unlimited (rarely noticeable in practice because
            // the final transcribe replaces it on stop anyway).
            var PARTIAL_MAX_SECONDS = 12;

            function ensureUi() {{
              var btn = doc.getElementById("yukt-browser-stt-btn");
              if (!btn) {{
                btn = doc.createElement("button");
                btn.type = "button";
                btn.id = "yukt-browser-stt-btn";
                btn.title = "Voice query";
                btn.style.cssText = [
                  "position:fixed",
                  "right:140px",
                  "bottom:72px",
                  "width:32px",
                  "height:32px",
                  "border:0",
                  "background:transparent",
                  "padding:0",
                  "margin:0",
                  // Must sit above Streamlit's bottom chat-input container,
                  // which is a fixed, transparent overlay with a very high
                  // z-index. At a lower z-index the mic paints *through* that
                  // transparent area (so it looks clickable) but the bar on
                  // top silently swallows the click. Max out the stacking so
                  // nothing can intercept pointer events.
                  "z-index:2147483646",
                  "pointer-events:auto",
                  "cursor:pointer"
                ].join(";");
                btn.innerHTML = MIC_URI
                  ? '<img alt="mic" src="' + MIC_URI + '" style="width:30px;height:30px;object-fit:contain;display:block;margin:auto;" />'
                  : '<span style="font-size:18px;line-height:1;">🎙️</span>';
                doc.body.appendChild(btn);
              }}
              var indicator = doc.getElementById("yukt-browser-stt-indicator");
              if (!indicator) {{
                indicator = doc.createElement("div");
                indicator.id = "yukt-browser-stt-indicator";
                indicator.style.cssText = [
                  "display:none",
                  "position:fixed",
                  "right:104px",
                  "bottom:37px",
                  "z-index:2147483645",
                  "pointer-events:none",
                  "padding:5px 10px",
                  "border-radius:999px",
                  "border:1px solid rgba(15,23,42,.14)",
                  "background:#fffbea",
                  "font-size:12px",
                  "color:#111827",
                  "box-shadow:0 1px 3px rgba(15,23,42,.08)"
                ].join(";");
                indicator.textContent = "Listening...";
                doc.body.appendChild(indicator);
              }}
              return {{ btn: btn, indicator: indicator }};
            }}

            function ensureHiddenTtsAudio() {{
              var a = root.__yuktTtsAudio || doc.getElementById("yukt-hidden-tts-audio");
              if (!a) {{
                a = doc.createElement("audio");
                a.id = "yukt-hidden-tts-audio";
                a.style.display = "none";
                a.preload = "auto";
                doc.body.appendChild(a);
              }}
              root.__yuktTtsAudio = a;
              if (typeof root.__yuktTtsMsgKey === "undefined") root.__yuktTtsMsgKey = "";
              return a;
            }}

            function primeTtsPlayback() {{
              try {{
                var audio = ensureHiddenTtsAudio();
                // Tiny silent WAV frame to unlock media playback after explicit user interaction.
                var silentWav = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA=";
                audio.muted = true;
                audio.src = silentWav;
                var p = audio.play();
                if (p && typeof p.then === "function") {{
                  p.then(function() {{
                    try {{ audio.pause(); audio.currentTime = 0; }} catch (e1) {{}}
                    audio.muted = false;
                  }}).catch(function() {{
                    try {{ audio.pause(); audio.currentTime = 0; }} catch (e2) {{}}
                    audio.muted = false;
                  }});
                }} else {{
                  try {{ audio.pause(); audio.currentTime = 0; }} catch (e3) {{}}
                  audio.muted = false;
                }}
              }} catch (e4) {{}}
            }}

            function setChatInputValue(text) {{
              var ta = doc.querySelector('[data-testid="stChatInput"] textarea');
              if (!ta) return false;
              try {{
                var proto = Object.getPrototypeOf(ta);
                var setter = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
                if (setter && setter.set) setter.set.call(ta, text);
                else ta.value = text;
                ta.defaultValue = text;
                ta.removeAttribute("readonly");
                ta.removeAttribute("disabled");
                ta.dispatchEvent(new InputEvent("input", {{
                  bubbles: true,
                  inputType: "insertText",
                  data: text
                }}));
                ta.dispatchEvent(new Event("change", {{ bubbles: true }}));
                ta.focus();
                try {{
                  var end = String(text || "").length;
                  ta.setSelectionRange(end, end);
                }} catch (e) {{}}
                return true;
              }} catch (e) {{
                console.error("setChatInputValue failed", e);
                return false;
              }}
            }}

            function setUi() {{
              if (S.processing && !S.listening && S.processingSince > 0) {{
                var age = Date.now() - Number(S.processingSince || 0);
                if (age > PROCESSING_STALE_MS) {{
                  S.processing = false;
                  S.processingSince = 0;
                }}
              }}
              var ui = ensureUi();
              ui.indicator.style.display = (S.listening || S.processing) ? "block" : "none";
              ui.indicator.textContent = S.processing ? "Transcribing..." : "Listening...";
              ui.indicator.style.background = S.processing ? "#eef6ff" : "#fffbea";
              // Keep mic button clickable at all times; only change visual state.
              ui.btn.disabled = false;
              ui.btn.removeAttribute("disabled");
              ui.btn.style.pointerEvents = "auto";
              ui.btn.style.zIndex = "2147483646";
              ui.btn.style.cursor = "pointer";
              ui.btn.style.opacity = S.processing ? "0.75" : "1";
              ui.btn.style.filter = S.listening ? "drop-shadow(0 0 3px rgba(239,68,68,.45))" : "none";
            }}

            async function hardResetSttState() {{
              try {{ stopPartialPolling(); }} catch (e_) {{}}
              try {{ stopAnim(); }} catch (e_a) {{}}
              try {{
                if (S.processor) {{
                  S.processor.disconnect();
                  S.processor.onaudioprocess = null;
                }}
              }} catch (e0) {{}}
              try {{ if (S.source) S.source.disconnect(); }} catch (e1) {{}}
              try {{
                if (S.stream) {{
                  S.stream.getTracks().forEach(function(t) {{ try {{ t.stop(); }} catch (e2) {{}} }});
                }}
              }} catch (e3) {{}}
              try {{
                if (S.audioContext) {{
                  try {{ await S.audioContext.close(); }} catch (e4) {{}}
                }}
              }} catch (e5) {{}}
              S.chunks = [];
              S.totalSamples = 0;
              S.audioContext = null;
              S.stream = null;
              S.source = null;
              S.processor = null;
              S.processing = false;
              S.processingSince = 0;
              S.listening = false;
              S.committedFinal = false;
              S.lastPartialText = "";
              S.pollInflight = false;
              S.pollSeq = 0;
              S.displayedText = "";
              S.targetText = "";
              setUi();
            }}

            function mergeBuffers(chunks, totalSamples) {{
              var out = new Float32Array(totalSamples);
              var offset = 0;
              for (var i = 0; i < chunks.length; i++) {{
                out.set(chunks[i], offset);
                offset += chunks[i].length;
              }}
              return out;
            }}

            function floatTo16BitPCM(view, offset, input) {{
              for (var i = 0; i < input.length; i++, offset += 2) {{
                var s = Math.max(-1, Math.min(1, input[i]));
                view.setInt16(offset, s < 0 ? s * 0x8001 : s * 0x7fff, true);
              }}
            }}

            function writeString(view, offset, str) {{
              for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
            }}

            function encodeWav(samples, sampleRate) {{
              var buffer = new ArrayBuffer(44 + samples.length * 2);
              var view = new DataView(buffer);
              writeString(view, 0, "RIFF");
              view.setUint32(4, 36 + samples.length * 2, true);
              writeString(view, 8, "WAVE");
              writeString(view, 12, "fmt ");
              view.setUint32(16, 16, true);
              view.setUint16(20, 1, true);
              view.setUint16(22, 1, true);
              view.setUint32(24, sampleRate, true);
              view.setUint32(28, sampleRate * 2, true);
              view.setUint16(32, 2, true);
              view.setUint16(34, 16, true);
              writeString(view, 36, "data");
              view.setUint32(40, samples.length * 2, true);
              floatTo16BitPCM(view, 44, samples);
              return new Blob([view], {{ type: "audio/wav" }});
            }}

            function blobToBase64(blob) {{
              return new Promise(function(resolve, reject) {{
                var reader = new FileReader();
                reader.onloadend = function() {{
                  var res = String(reader.result || "");
                  var idx = res.indexOf(",");
                  resolve(idx >= 0 ? res.slice(idx + 1) : res);
                }};
                reader.onerror = reject;
                reader.readAsDataURL(blob);
              }});
            }}

            function snapshotSamples(maxSamples) {{
              // Copy the current chunk list to a single Float32Array. Safe to
              // call from poll handlers while the recorder keeps appending
              // because we read S.chunks/S.totalSamples in one pass.
              // When maxSamples is a positive number, return only the most
              // recent ``maxSamples`` samples (sliding window for partials).
              var src = S.chunks || [];
              var total = S.totalSamples || 0;
              var wanted = (maxSamples && maxSamples > 0 && maxSamples < total)
                ? maxSamples : total;
              var skip = total - wanted; // samples to drop from the front
              var out = new Float32Array(wanted);
              var offset = 0;
              var seen = 0;
              for (var i = 0; i < src.length && offset < wanted; i++) {{
                var part = src[i];
                if (!part) continue;
                if (seen + part.length <= skip) {{
                  seen += part.length;
                  continue;
                }}
                var start = (seen < skip) ? (skip - seen) : 0;
                var avail = part.length - start;
                var n = Math.min(avail, wanted - offset);
                if (n <= 0) {{ seen += part.length; continue; }}
                out.set(start === 0 && n === part.length ? part : part.subarray(start, start + n), offset);
                offset += n;
                seen += part.length;
              }}
              return offset === wanted ? out : out.subarray(0, offset);
            }}

            // Whisper internally runs at 16 kHz; sending the browser's native
            // 48 kHz forces a server-side resample and triples the upload size.
            // Decimating with a small box-average is plenty for STT quality.
            function downsampleTo16k(samples, fromRate) {{
              if (!samples || !samples.length) return samples;
              var target = 16000;
              if (!fromRate || fromRate <= target) return samples;
              var ratio = fromRate / target;
              var outLen = Math.floor(samples.length / ratio);
              if (outLen <= 0) return samples;
              var out = new Float32Array(outLen);
              for (var i = 0; i < outLen; i++) {{
                var start = Math.floor(i * ratio);
                var end = Math.min(samples.length, Math.floor((i + 1) * ratio));
                if (end <= start) {{
                  out[i] = samples[Math.min(samples.length - 1, start)] || 0;
                  continue;
                }}
                var sum = 0;
                for (var j = start; j < end; j++) sum += samples[j];
                out[i] = sum / (end - start);
              }}
              return out;
            }}

            // Whisper sometimes returns sentinel placeholders for short or
            // silent input. Treat those as "no useful transcript yet" so the
            // chat input is not overwritten with junk during live polling.
            function isUsefulPartial(text) {{
              var t = String(text || "").trim();
              if (!t) return false;
              if (/^\\[[A-Z_ ]+\\]$/.test(t)) return false;
              if (/^\\(\\s*[A-Za-z _-]+\\s*\\)$/.test(t)) return false;
              return true;
            }}

            function stopAnim() {{
              if (S.animTimer != null) {{
                root.clearTimeout(S.animTimer);
                S.animTimer = null;
              }}
            }}

            function scheduleAnim() {{
              if (S.animTimer != null) return;
              var tick = function() {{
                S.animTimer = null;
                var curr = String(S.displayedText || "");
                var tgt = String(S.targetText || "");
                if (curr === tgt) return;
                // If whisper rewrote earlier characters (new target is not a
                // forward extension), snap immediately rather than backspace.
                if (tgt.indexOf(curr) !== 0) {{
                  S.displayedText = tgt;
                  setChatInputValue(tgt);
                  return;
                }}
                var next = tgt.slice(0, curr.length + 1);
                S.displayedText = next;
                setChatInputValue(next);
                if (next !== tgt) {{
                  S.animTimer = root.setTimeout(tick, CHAR_ANIM_DELAY_MS);
                }}
              }};
              S.animTimer = root.setTimeout(tick, CHAR_ANIM_DELAY_MS);
            }}

            // Snap the chat input to ``text`` immediately, cancelling any
            // in-flight character animation. Used when committing a final or
            // held partial result on mic stop.
            function snapDisplayed(text) {{
              stopAnim();
              var t = String(text || "");
              S.displayedText = t;
              S.targetText = t;
              return setChatInputValue(t);
            }}

            async function pollPartialTranscript() {{
              // Note: we deliberately do not bail on S.committedFinal here.
              // For very short utterances the user may stop before any poll
              // has returned; letting a late poll apply its result keeps the
              // input from sitting empty for the full final-transcribe wait.
              // The final pass (stopRecording) snaps to its own result on
              // arrival via snapDisplayed(), so partials can't "win" over it.
              if (S.pollInflight) return;
              var sr = (S.audioContext && S.audioContext.sampleRate) || 44100;
              var minSamples = Math.max(1600, Math.floor(sr * POLL_MIN_SAMPLES_RATIO));
              if (!(S.totalSamples >= minSamples)) return;
              // Cap partial-poll audio to the most recent PARTIAL_MAX_SECONDS
              // so whisper decode time stays bounded for long utterances.
              // mergeBuffers (used on final stop) still consumes the full
              // buffer, so the committed final transcript is unaffected.
              var maxPartialSamples = Math.floor(sr * PARTIAL_MAX_SECONDS);
              var samples = snapshotSamples(maxPartialSamples);
              if (!samples.length) return;
              var downsampled = downsampleTo16k(samples, sr);
              var encodedRate = (sr > 16000 && downsampled !== samples) ? 16000 : sr;
              var seq = ++S.pollSeq;
              S.pollInflight = true;
              var t0 = (root.performance && root.performance.now) ? root.performance.now() : Date.now();
              try {{
                var wavBlob = encodeWav(downsampled, encodedRate);
                var b64 = await blobToBase64(wavBlob);
                var resp = await fetch(API_BASE + "/stt/transcribe", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{
                    audio_base64: b64,
                    audio_format: "wav",
                    partial: true
                  }})
                }});
                var data = await resp.json();
                if (seq !== S.pollSeq) return; // a newer poll already started
                var text = String((data && data.text) || "").trim();
                var dt = (((root.performance && root.performance.now) ? root.performance.now() : Date.now()) - t0) | 0;
                if (!isUsefulPartial(text)) {{
                  logEvent("partial_transcript_skipped", {{
                    seq: seq,
                    chars: text.length,
                    preview: text.slice(0, 60),
                    elapsedMs: dt
                  }});
                  return;
                }}
                if (text !== S.lastPartialText) {{
                  S.lastPartialText = text;
                  // Drive the character-by-character animation toward the
                  // newly received transcript. If a newer partial arrives
                  // while we're still typing, the tick will pick up the new
                  // target on its next iteration.
                  S.targetText = text;
                  scheduleAnim();
                  logEvent("partial_transcript_applied", {{
                    seq: seq,
                    chars: text.length,
                    preview: text.slice(0, 80),
                    elapsedMs: dt
                  }});
                }}
              }} catch (err) {{
                logEvent("partial_transcript_error", {{
                  seq: seq,
                  message: String((err && err.message) || err || "")
                }});
              }} finally {{
                S.pollInflight = false;
              }}
            }}

            function startPartialPolling() {{
              if (S.pollTimer) return;
              // Fire one quick poll as soon as we'll have enough audio for
              // whisper to decode something useful. Matched to
              // POLL_MIN_SAMPLES_RATIO (150 ms) — firing sooner just bails
              // on the min-samples gate inside pollPartialTranscript.
              root.setTimeout(function() {{
                if (S.listening) pollPartialTranscript();
              }}, 160);
              S.pollTimer = root.setInterval(function() {{
                pollPartialTranscript();
              }}, POLL_INTERVAL_MS);
            }}

            function stopPartialPolling() {{
              if (S.pollTimer) {{
                root.clearInterval(S.pollTimer);
                S.pollTimer = null;
              }}
            }}

            function showMicError(msg) {{
              try {{
                var ui2 = ensureUi();
                ui2.indicator.style.display = "block";
                ui2.indicator.style.background = "#fef2f2";
                ui2.indicator.style.color = "#b91c1c";
                ui2.indicator.textContent = msg;
                root.setTimeout(function() {{
                  try {{ ui2.indicator.style.display = "none"; ui2.indicator.style.color = "#111827"; }} catch (e) {{}}
                }}, 6000);
              }} catch (e) {{}}
            }}

            async function startRecording() {{
              if (S.listening || S.processing) return;
              logEvent("start_click", {{ listening: S.listening, processing: S.processing }});
              // getUserMedia is only exposed in a "secure context": HTTPS, or
              // http://localhost / http://127.0.0.1. If the app is opened over
              // a plain-HTTP LAN address (e.g. http://192.168.x.x:8501) the
              // browser leaves navigator.mediaDevices undefined and the mic
              // can never start — surface that instead of failing silently.
              if (!root.navigator || !root.navigator.mediaDevices || !root.navigator.mediaDevices.getUserMedia) {{
                logEvent("recording_start_error", {{ message: "mediaDevices unavailable (insecure context)" }});
                showMicError("Mic needs HTTPS or localhost");
                return;
              }}
              try {{
                S.stream = await root.navigator.mediaDevices.getUserMedia({{ audio: true }});
                S.audioContext = new (root.AudioContext || root.webkitAudioContext)();
                S.source = S.audioContext.createMediaStreamSource(S.stream);
                S.processor = S.audioContext.createScriptProcessor(4096, 1, 1);
                S.chunks = [];
                S.totalSamples = 0;
                S.lastPartialText = "";
                S.committedFinal = false;
                S.pollSeq = 0;
                stopAnim();
                S.displayedText = "";
                S.targetText = "";
                S.processor.onaudioprocess = function(ev) {{
                  if (!S.listening) return;
                  var input = ev.inputBuffer.getChannelData(0);
                  S.chunks.push(new Float32Array(input));
                  S.totalSamples += input.length;
                }};
                S.source.connect(S.processor);
                S.processor.connect(S.audioContext.destination);
                S.listening = true;
                // Live partial transcribes were removed: whisper-tiny
                // hallucinates on incomplete buffers ("I'm sorry",
                // "Thanks for watching", etc.) and the user sees that
                // garbage in the input. We now rely solely on the
                // higher-accuracy final transcribe in stopRecording().
                logEvent("recording_started", {{
                  sampleRate: (S.audioContext && S.audioContext.sampleRate) || 0
                }});
                setUi();
              }} catch (err) {{
                var emsg = String((err && err.message) || err || "");
                var ename = String((err && err.name) || "");
                logEvent("recording_start_error", {{ message: emsg, name: ename }});
                if (ename === "NotAllowedError" || ename === "SecurityError") {{
                  showMicError("Mic permission blocked");
                }} else if (ename === "NotFoundError" || ename === "OverconstrainedError") {{
                  showMicError("No microphone found");
                }} else {{
                  showMicError("Mic could not start");
                }}
                try {{
                  if (S.processor) {{
                    S.processor.disconnect();
                    S.processor.onaudioprocess = null;
                  }}
                  if (S.source) S.source.disconnect();
                  if (S.stream) {{
                    S.stream.getTracks().forEach(function(t) {{ try {{ t.stop(); }} catch (e) {{}} }});
                  }}
                  if (S.audioContext) {{
                    try {{ await S.audioContext.close(); }} catch (e1) {{}}
                  }}
                }} catch (e2) {{}}
                S.audioContext = null;
                S.stream = null;
                S.source = null;
                S.processor = null;
                S.processing = false;
                S.listening = false;
                setUi();
              }}
            }}

            async function stopRecording() {{
              if (!S.listening || S.processing) return;
              logEvent("stop_click", {{
                chunkCount: (S.chunks || []).length,
                totalSamples: S.totalSamples || 0,
                hasPartial: !!S.lastPartialText,
                partialChars: (S.lastPartialText || "").length
              }});
              S.listening = false;
              S.committedFinal = true;
              stopPartialPolling();
              // Show whatever the live partials produced as the immediate
              // result: the user gets instant feedback, and the higher-quality
              // final transcribe runs in the background to refine it. Snap
              // (rather than animate) so the input is fully populated before
              // we kick off the final-transcribe request.
              var heldPartial = String(S.lastPartialText || "").trim();
              if (heldPartial) {{
                snapDisplayed(heldPartial);
              }}
              S.processing = !heldPartial;
              S.processingSince = heldPartial ? 0 : Date.now();
              setUi();
              try {{
                if (S.processor) {{
                  S.processor.disconnect();
                  S.processor.onaudioprocess = null;
                }}
                if (S.source) S.source.disconnect();
                if (S.stream) {{
                  S.stream.getTracks().forEach(function(t) {{ try {{ t.stop(); }} catch (e) {{}} }});
                }}
                var samples = mergeBuffers(S.chunks || [], S.totalSamples || 0);
                if (!samples.length) {{
                  logEvent("no_samples_after_stop", {{
                    chunkCount: (S.chunks || []).length,
                    totalSamples: S.totalSamples || 0
                  }});
                  S.processing = false;
                  setUi();
                  return;
                }}
                var origSr = (S.audioContext && S.audioContext.sampleRate) || 44100;
                var finalSamples = downsampleTo16k(samples, origSr);
                var finalSr = (origSr > 16000 && finalSamples !== samples) ? 16000 : origSr;
                var wavBlob = encodeWav(finalSamples, finalSr);
                logEvent("wav_encoded", {{
                  wavBytes: wavBlob.size || 0,
                  totalSamples: finalSamples.length,
                  encodedSampleRate: finalSr
                }});
                if (S.audioContext) {{
                  try {{ await S.audioContext.close(); }} catch (e1) {{}}
                }}
                var audioB64 = await blobToBase64(wavBlob);
                logEvent("transcribe_request_begin", {{
                  audioB64Chars: (audioB64 || "").length,
                  hasPartialDisplayed: !!heldPartial
                }});
                var resp = await fetch(API_BASE + "/stt/transcribe", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{ audio_base64: audioB64, audio_format: "wav" }})
                }});
                var data = await resp.json();
                var text = String((data && data.text) || "").trim();
                logEvent("transcribe_response", {{
                  ok: !!resp.ok,
                  status: resp.status,
                  transcriptChars: text.length,
                  transcriptPreview: text.slice(0, 120),
                  rawKeys: Object.keys(data || {{}})
                }});
                S.processing = false;
                S.processingSince = 0;
                setUi();
                if (text && isUsefulPartial(text)) {{
                  // Final result wins over the held partial; backend strips
                  // any old STT markers defensively. snapDisplayed cancels
                  // any still-running character animation from a late poll.
                  var applied = snapDisplayed(text);
                  logEvent("apply_transcript_to_input", {{
                    applied: applied,
                    transcriptChars: text.length,
                    transcriptPreview: text.slice(0, 120),
                    replacedPartial: heldPartial && heldPartial !== text
                  }});
                  if (!applied) {{
                    logEvent("apply_transcript_failed", {{
                      reason: "chat_input_not_found"
                    }});
                  }}
                }} else if (heldPartial) {{
                  logEvent("final_kept_partial", {{
                    finalRaw: text,
                    partialChars: heldPartial.length
                  }});
                }}
              }} catch (err) {{
                logEvent("browser_stt_error", {{
                  message: String((err && err.message) || err || "")
                }});
                console.error("STT recorder failed", err);
                S.processing = false;
                S.processingSince = 0;
                setUi();
              }} finally {{
                stopPartialPolling();
                stopAnim();
                S.chunks = [];
                S.totalSamples = 0;
                S.audioContext = null;
                S.stream = null;
                S.source = null;
                S.processor = null;
                S.processing = false;
                S.processingSince = 0;
                S.listening = false;
                S.pollInflight = false;
                S.committedFinal = false;
                S.lastPartialText = "";
                S.pollSeq = 0;
                S.displayedText = "";
                S.targetText = "";
                setUi();
              }}
            }}

            var ui = ensureUi();
            // Reassign the click handler on EVERY render rather than binding
            // once. This component runs inside a Streamlit iframe that is
            // destroyed and recreated on each app rerun (e.g. when a transcript
            // is written into the chat input). The button lives on the parent
            // document and survives, but a listener added via addEventListener
            // closes over the *old* iframe's JS realm — once that iframe is
            // torn down the listener is dead and the mic stops responding on
            // the second click. Using onclick = (which replaces, not stacks)
            // and reassigning every render guarantees the button always holds
            // a live handler from the current iframe.
            ui.btn.onclick = async function() {{
              try {{ primeTtsPlayback(); }} catch (e0) {{}}
              if (S.processing) {{
                // Never leave mic "disabled": force-reset and continue on user click.
                await hardResetSttState();
              }}
              if (S.listening) stopRecording();
              else startRecording();
            }};
            setUi();
          }} catch (e) {{
            console.error("browser STT bridge failed", e);
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _render_virtual_keyboard_bridge() -> None:
    """On-screen virtual keyboard for the chat input.

    Renders a hidden-iframe script (same pattern as the STT bridge) that
    reaches into the parent document, finds the Streamlit chat-input textarea
    and shows a touch keyboard *only* while that textarea is focused/clicked.
    Keys type into the textarea via the React-native value setter (so
    Streamlit's state stays in sync) and Enter clicks the send button.

    Like the STT bridge, the keyboard DOM is created on the persistent parent
    document while handlers are reassigned on every render — the component's
    own iframe is torn down and recreated on each Streamlit rerun, so any
    closure bound to the old iframe's JS realm would otherwise go dead.
    """
    vkb_js = (
        """
        <script>
        (function() {
          try {
            var VKB_MAX_WIDTH = __VKB_MAX_W__;
            var VKB_WIDTH_RATIO = __VKB_W_RATIO__;
            var root = (window.parent && window.parent !== window) ? window.parent : window;
            var doc = root.document;
            var S = root.__yuktVkbState = root.__yuktVkbState || {
              visible: false, shift: false, layout: "letters", kbEl: null,
              dragged: false, dragLeft: null, dragTop: null,
              dragging: false, blurHideTimer: null, kbPointerDown: false
            };

            var LETTERS = [
              ["1","2","3","4","5","6","7","8","9","0"],
              ["q","w","e","r","t","y","u","i","o","p"],
              ["a","s","d","f","g","h","j","k","l"],
              ["{shift}","z","x","c","v","b","n","m","{bksp}"],
              ["{layout}",",","{space}",".","{enter}"]
            ];
            var SYMBOLS = [
              ["1","2","3","4","5","6","7","8","9","0"],
              ["@","#","$","_","&","-","+","(",")","/"],
              ["*","\\"","'",":",";","!","?","\\\\"],
              ["=","[","]","{","}","<",">","{bksp}"],
              ["{layout}",",","{space}",".","{enter}"]
            ];

            function getTextarea() {
              return doc.querySelector('[data-testid="stChatInput"] textarea');
            }

            function setValue(ta, val) {
              try {
                var proto = Object.getPrototypeOf(ta);
                var setter = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
                if (setter && setter.set) setter.set.call(ta, val);
                else ta.value = val;
                ta.dispatchEvent(new InputEvent("input", { bubbles: true }));
              } catch (e) { ta.value = val; }
            }

            function insertText(t) {
              var ta = getTextarea(); if (!ta) return;
              var start = ta.selectionStart, end = ta.selectionEnd, v = ta.value;
              if (typeof start !== "number") { start = v.length; end = v.length; }
              setValue(ta, v.slice(0, start) + t + v.slice(end));
              var pos = start + t.length;
              try { ta.focus(); ta.setSelectionRange(pos, pos); } catch (e) {}
            }

            function backspace() {
              var ta = getTextarea(); if (!ta) return;
              var start = ta.selectionStart, end = ta.selectionEnd, v = ta.value;
              if (typeof start !== "number") { start = v.length; end = v.length; }
              if (start === end) {
                if (start === 0) { ta.focus(); return; }
                setValue(ta, v.slice(0, start - 1) + v.slice(end));
                var p = start - 1;
                try { ta.focus(); ta.setSelectionRange(p, p); } catch (e) {}
              } else {
                setValue(ta, v.slice(0, start) + v.slice(end));
                try { ta.focus(); ta.setSelectionRange(start, start); } catch (e) {}
              }
            }

            function pressEnter() {
              var box = doc.querySelector('[data-testid="stChatInput"]');
              if (!box) return;
              hideKb();
              var btn = box.querySelector('[data-testid="stChatInputSubmitButton"]')
                     || box.querySelector("button");
              if (btn) btn.click();
            }

            function ensureStyle() {
              if (doc.getElementById("yukt-vkb-style-v2")) return;
              var oldStyle = doc.getElementById("yukt-vkb-style");
              if (oldStyle) oldStyle.remove();
              var st = doc.createElement("style");
              st.id = "yukt-vkb-style-v2";
              st.textContent = [
                "#yukt-vkb{position:fixed;left:50%;transform:translateX(-50%);",
                "width:min(__VKB_MAX_W__px,calc(100vw - 24px));z-index:2147483640;",
                "background:#f4f6fb;border:1px solid rgba(15,23,42,.14);",
                "border-radius:14px;box-shadow:0 6px 24px rgba(15,23,42,.18);",
                "padding:8px;display:none;user-select:none;-webkit-user-select:none;",
                "cursor:move;touch-action:none;}",
                "#yukt-vkb .yukt-kb-hdr{display:flex;justify-content:space-between;align-items:center;",
                "margin:0 2px 6px;}",
                "#yukt-vkb .yukt-kb-drag-hint{font-size:18px;color:#94a3b8;letter-spacing:2px;",
                "padding:2px 8px;user-select:none;-webkit-user-select:none;}",
                "#yukt-vkb .yukt-kb-close{border:0;background:transparent;cursor:pointer;",
                "font-size:16px;line-height:1;color:#475569;padding:2px 6px;border-radius:8px;}",
                "#yukt-vkb .yukt-kb-close:hover{background:rgba(15,23,42,.08);}",
                "#yukt-vkb .yukt-kb-row{display:flex;gap:8px;justify-content:stretch;margin-bottom:8px;}",
                "#yukt-vkb .yukt-kb-key{flex:1 1 0;min-width:0;height:58px;border:1px solid rgba(15,23,42,.12);",
                "background:#fff;border-radius:10px;font-size:26px;color:#000;font-weight:700;cursor:pointer;",
                "display:flex;align-items:center;justify-content:center;",
                "box-shadow:0 1px 0 rgba(15,23,42,.08);touch-action:manipulation;}",
                "#yukt-vkb .yukt-kb-key:active{background:#e2e8f0;transform:translateY(1px);}",
                "#yukt-vkb .yukt-kb-key.wide{flex:1.6 1 0;font-size:16px;}",
                "#yukt-vkb .yukt-kb-key.space{flex:6 1 0;font-size:16px;}",
                "#yukt-vkb .yukt-kb-key.enter{flex:2 1 0;background:#2563eb;color:#fff;border-color:#2563eb;font-size:16px;}",
                "#yukt-vkb .yukt-kb-key.active{background:#dbeafe;border-color:#93c5fd;}"
              ].join("");
              doc.head.appendChild(st);
            }

            function ensureKb() {
              ensureStyle();
              var kb = doc.getElementById("yukt-vkb");
              if (!kb) {
                kb = doc.createElement("div");
                kb.id = "yukt-vkb";
                kb.innerHTML = '<div class="yukt-kb-hdr">'
                  + '<span class="yukt-kb-drag-hint" title="Drag to move">&#8942;&#8942;&#8942;</span>'
                  + '<button type="button" class="yukt-kb-close" title="Hide keyboard">&#9003;</button>'
                  + '</div><div class="yukt-kb-keys"></div>';
                doc.body.appendChild(kb);
              }
              S.kbEl = kb;
              var closeBtn = kb.querySelector(".yukt-kb-close");
              if (closeBtn) {
                closeBtn.onmousedown = function(e) { e.preventDefault(); e.stopPropagation(); };
                closeBtn.onpointerdown = function(e) { e.preventDefault(); e.stopPropagation(); };
                closeBtn.onclick = function(e) { e.preventDefault(); hideKb(); };
              }
              bindKbDrag(kb);
              return kb;
            }

            function bindKbDrag(kb) {
              if (!kb) return;
              // Reassign on EVERY Streamlit rerun. addEventListener handlers from a
              // prior iframe realm die when that iframe is torn down (same as STT mic).
              var drag = S._kbDrag = S._kbDrag || {
                startX: 0, startY: 0, startLeft: 0, startTop: 0, activePointer: null
              };

              function canDragFrom(el) {
                if (!el || !el.closest) return false;
                return !el.closest(".yukt-kb-key") && !el.closest(".yukt-kb-close");
              }

              function clampPos(left, top) {
                var maxL = Math.max(0, root.innerWidth - kb.offsetWidth);
                var maxT = Math.max(0, root.innerHeight - kb.offsetHeight);
                return {
                  left: Math.max(0, Math.min(maxL, left)),
                  top: Math.max(0, Math.min(maxT, top))
                };
              }

              function applyPos(left, top) {
                var pos = clampPos(left, top);
                kb.style.left = pos.left + "px";
                kb.style.top = pos.top + "px";
                S.dragged = true;
                S.dragLeft = pos.left;
                S.dragTop = pos.top;
              }

              function cancelBlurHide() {
                if (S.blurHideTimer) {
                  root.clearTimeout(S.blurHideTimer);
                  S.blurHideTimer = null;
                }
              }

              function clearDocDragListeners() {
                doc.onpointermove = null;
                doc.onpointerup = null;
                doc.onpointercancel = null;
              }

              function endDrag() {
                S.dragging = false;
                S.kbPointerDown = false;
                drag.activePointer = null;
                clearDocDragListeners();
                var ta = getTextarea();
                if (ta) {
                  try { ta.focus(); } catch (e) {}
                }
              }

              function onDocPointerMove(ev) {
                if (!S.dragging || ev.pointerId !== drag.activePointer) return;
                ev.preventDefault();
                applyPos(
                  drag.startLeft + (ev.clientX - drag.startX),
                  drag.startTop + (ev.clientY - drag.startY)
                );
              }

              function onDocPointerEnd(ev) {
                if (!S.dragging || ev.pointerId !== drag.activePointer) return;
                ev.preventDefault();
                endDrag();
              }

              function beginDrag(clientX, clientY) {
                cancelBlurHide();
                S.dragging = true;
                S.kbPointerDown = true;
                var rect = kb.getBoundingClientRect();
                drag.startX = clientX;
                drag.startY = clientY;
                kb.style.bottom = "auto";
                kb.style.transform = "none";
                drag.startLeft = rect.left;
                drag.startTop = rect.top;
                kb.style.left = drag.startLeft + "px";
                kb.style.top = drag.startTop + "px";
              }

              kb.onmousedown = function(e) {
                if (!e.target || !e.target.closest) return;
                if (e.target.closest(".yukt-kb-close")) return;
                if (!e.target.closest(".yukt-kb-key")) {
                  e.preventDefault();
                  S.kbPointerDown = true;
                }
              };

              kb.onpointerdown = function(e) {
                if (!canDragFrom(e.target)) return;
                if (e.button != null && e.button !== 0) return;
                e.preventDefault();
                e.stopPropagation();
                drag.activePointer = e.pointerId;
                beginDrag(e.clientX, e.clientY);
                doc.onpointermove = onDocPointerMove;
                doc.onpointerup = onDocPointerEnd;
                doc.onpointercancel = onDocPointerEnd;
              };

              kb.onpointerup = function(e) {
                if (S.kbPointerDown) S.kbPointerDown = false;
              };
            }

            function keyLabel(token) {
              if (token === "{shift}") return "\\u21E7";
              if (token === "{bksp}") return "\\u232B";
              if (token === "{space}") return "space";
              if (token === "{enter}") return "\\u23CE";
              if (token === "{layout}") return S.layout === "letters" ? "?123" : "ABC";
              if (S.shift && S.layout === "letters" && token.length === 1) return token.toUpperCase();
              return token;
            }

            function handleKey(token) {
              if (token === "{shift}") { S.shift = !S.shift; renderKeys(); return; }
              if (token === "{bksp}") { backspace(); return; }
              if (token === "{space}") { insertText(" "); return; }
              if (token === "{enter}") { pressEnter(); return; }
              if (token === "{layout}") {
                S.layout = (S.layout === "letters") ? "symbols" : "letters";
                renderKeys(); return;
              }
              var ch = (S.shift && S.layout === "letters") ? token.toUpperCase() : token;
              insertText(ch);
              if (S.shift) { S.shift = false; renderKeys(); }
            }

            function renderKeys() {
              var kb = ensureKb();
              var holder = kb.querySelector(".yukt-kb-keys");
              if (!holder) return;
              var rows = (S.layout === "letters") ? LETTERS : SYMBOLS;
              holder.innerHTML = "";
              rows.forEach(function(row) {
                var rowEl = doc.createElement("div");
                rowEl.className = "yukt-kb-row";
                row.forEach(function(token) {
                  var k = doc.createElement("button");
                  k.type = "button";
                  k.className = "yukt-kb-key";
                  if (token === "{space}") k.className += " space";
                  else if (token === "{enter}") k.className += " enter";
                  else if (token === "{shift}" || token === "{bksp}" || token === "{layout}") k.className += " wide";
                  if (token === "{shift}" && S.shift) k.className += " active";
                  k.textContent = keyLabel(token);
                  // mousedown + preventDefault keeps focus on the textarea so it
                  // never blurs (which would hide the keyboard) while typing.
                  k.onmousedown = function(e) { e.preventDefault(); handleKey(token); };
                  rowEl.appendChild(k);
                });
                holder.appendChild(rowEl);
              });
            }

            function kbTargetWidth(refWidth) {
              var ref = Number(refWidth) || VKB_MAX_WIDTH;
              return Math.max(320, Math.min(Math.round(ref * VKB_WIDTH_RATIO), VKB_MAX_WIDTH));
            }

            function positionKb() {
              var kb = doc.getElementById("yukt-vkb"); if (!kb) return;
              if (S.dragged && S.dragLeft != null && S.dragTop != null) {
                kb.style.bottom = "auto";
                kb.style.transform = "none";
                var maxL = Math.max(0, root.innerWidth - kb.offsetWidth);
                var maxT = Math.max(0, root.innerHeight - kb.offsetHeight);
                S.dragLeft = Math.max(0, Math.min(maxL, S.dragLeft));
                S.dragTop = Math.max(0, Math.min(maxT, S.dragTop));
                kb.style.left = S.dragLeft + "px";
                kb.style.top = S.dragTop + "px";
                return;
              }
              var box = doc.querySelector('[data-testid="stChatInput"]')
                     || doc.querySelector('[data-testid="stBottom"]');
              var bottom = 12;
              kb.style.top = "auto";
              if (box) {
                var r = box.getBoundingClientRect();
                bottom = Math.max(8, (root.innerHeight - r.top) + 8);
                // Narrower than the chat bar, centered above it (~62% of input width).
                var kbW = kbTargetWidth(r.width);
                kb.style.width = kbW + "px";
                kb.style.left = (r.left + (r.width - kbW) / 2) + "px";
                kb.style.transform = "none";
              }
              kb.style.bottom = bottom + "px";
            }

            function showKb() {
              var kb = ensureKb();
              renderKeys();
              positionKb();
              kb.style.display = "block";
              S.visible = true;
            }

            function hideKb() {
              var kb = doc.getElementById("yukt-vkb");
              if (kb) kb.style.display = "none";
              S.visible = false;
            }

            function bindInput() {
              var ta = getTextarea();
              if (!ta) return false;
              ta.onfocus = function() { showKb(); };
              ta.onclick = function() { showKb(); };
              ta.onkeydown = function(e) {
                if (e && e.key === "Enter" && !e.shiftKey) hideKb();
              };
              ta.onblur = function() {
                // Defer so a key's mousedown (which we preventDefault, keeping
                // focus) doesn't trip this. Only hide on a genuine focus loss
                // to something outside both the input and the keyboard.
                if (S.blurHideTimer) root.clearTimeout(S.blurHideTimer);
                S.blurHideTimer = root.setTimeout(function() {
                  S.blurHideTimer = null;
                  if (S.dragging || S.kbPointerDown) return;
                  var ae = doc.activeElement;
                  if (ae === ta) return;
                  if (S.kbEl && S.kbEl.contains(ae)) return;
                  hideKb();
                }, 120);
              };
              return true;
            }

            // The textarea may not exist on first paint; retry briefly.
            // On rerun the keyboard only re-appears if the input is genuinely
            // focused — otherwise it stays hidden (e.g. right after a submit).
            ensureKb();
            bindInput();
            var taNow = getTextarea();
            if (S.visible && taNow && doc.activeElement === taNow) {
              renderKeys(); positionKb(); S.kbEl.style.display = "block";
            } else if (!S.dragging && !S.kbPointerDown) {
              hideKb();
            }
            if (!taNow) {
              var tries = 0;
              var iv = root.setInterval(function() {
                tries++;
                if (bindInput() || tries > 40) root.clearInterval(iv);
              }, 150);
            }
            if (!root.__yuktVkbResizeBound) {
              root.__yuktVkbResizeBound = true;
              root.addEventListener("resize", function() { if (S.visible) positionKb(); });
            }
          } catch (e) {
            console.error("virtual keyboard bridge failed", e);
          }
        })();
        </script>
        """
        .replace("__VKB_MAX_W__", str(VKB_MAX_WIDTH_PX))
        .replace("__VKB_W_RATIO__", str(VKB_WIDTH_RATIO))
    )
    components.html(
        vkb_js,
        height=0,
        width=0,
    )


def _render_tts_audio_bridge(cmd: dict[str, Any] | None = None) -> None:
    payload = cmd if isinstance(cmd, dict) else {}
    js_payload = json.dumps(payload).replace("</", "<\\/")
    components.html(
        f"""
        <script>
        (function() {{
          try {{
            var root;
            try {{
              root = (window.parent && window.parent !== window) ? window.parent : window;
            }} catch (e0) {{ root = window; }}
            var doc = root.document;
            var cmd = {js_payload};
            if (!cmd || !cmd.action) return;
            function _yuktClientLog(eventName, detail) {{
              try {{
                var b0 = ((cmd.api_base || "") + "").replace(/\\/+$/, "");
                if (!b0) return;
                fetch(b0 + "/debug/client-log", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{
                    source: "tts_bridge",
                    event: eventName,
                    detail: detail || {{}}
                  }})
                }}).catch(function() {{}});
              }} catch (eLog) {{}}
            }}
            _yuktClientLog("tts_cmd_received", {{
              action: (cmd.action || "").toString(),
              msg_key: (cmd.msg_key || "").toString(),
              has_text: !!((cmd.text || "").toString().trim()),
              has_audio: !!((cmd.audio_base64 || "").toString())
            }});
            if (!root.__yuktTtsAudio) {{
              var a = doc.createElement("audio");
              a.id = "yukt-hidden-tts-audio";
              a.style.display = "none";
              a.preload = "auto";
              a.autoplay = false;
              a.playsInline = true;
              doc.body.appendChild(a);
              root.__yuktTtsAudio = a;
              root.__yuktTtsMsgKey = "";
            }}
            var audio = root.__yuktTtsAudio;
            function _yuktPrimeAudioUnlock() {{
              try {{
                var silentWav = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA=";
                audio.muted = true;
                audio.src = silentWav;
                var p0 = audio.play();
                if (p0 && typeof p0.then === "function") {{
                  return p0.then(function() {{
                    try {{ audio.pause(); audio.currentTime = 0; }} catch (e0) {{}}
                    audio.muted = false;
                  }}).catch(function() {{
                    try {{ audio.pause(); audio.currentTime = 0; }} catch (e1) {{}}
                    audio.muted = false;
                  }});
                }}
                try {{ audio.pause(); audio.currentTime = 0; }} catch (e2) {{}}
                audio.muted = false;
              }} catch (e3) {{}}
              return Promise.resolve();
            }}
            function _yuktPlayWithRetry(maxAttempts) {{
              var attempts = Math.max(1, Number(maxAttempts || 1));
              var idx = 0;
              var tryPlay = function() {{
                idx += 1;
                var p = null;
                try {{ p = audio.play(); }} catch (e0) {{}}
                if (!p || typeof p.then !== "function") return;
                p.catch(function() {{
                  if (idx >= attempts) return;
                  root.setTimeout(function() {{
                    if (idx === 1) {{
                      _yuktPrimeAudioUnlock().then(tryPlay);
                    }} else {{
                      tryPlay();
                    }}
                  }}, 120 * idx);
                }});
              }};
              tryPlay();
            }}
            function _yuktSplitTextForTTS(text, maxChars) {{
              var sanitized = String(text || "").replace(/\\[\\[YUKTRA_IMAGE_\\d+\\]\\]/g, "").trim();
              if (!sanitized) return [];
              var cap = Math.max(40, Number(maxChars || 900));
              var sentences = sanitized.match(/[^.!?\\n]+[.!?\\n]+|[^.!?\\n]+$/g) || [sanitized];
              var chunks = [];
              var cur = "";
              for (var si = 0; si < sentences.length; si++) {{
                var s = sentences[si].trim();
                if (!s) continue;
                if (cur && (cur.length + 1 + s.length) > cap) {{
                  chunks.push(cur);
                  cur = s;
                }} else {{
                  cur = cur ? (cur + " " + s) : s;
                }}
                while (cur.length > cap * 2) {{
                  var cut = cur.lastIndexOf(", ", cap);
                  if (cut < Math.max(40, Math.floor(cap / 4))) cut = cap;
                  chunks.push(cur.slice(0, cut).trim());
                  cur = cur.slice(cut).trim();
                }}
              }}
              if (cur) chunks.push(cur);
              return chunks.filter(function(s) {{ return s.length > 0; }});
            }}
            function _yuktCancelChunkSession() {{
              try {{
                var prev = root.__yuktTtsChunkSession;
                if (prev) {{ prev.cancelled = true; }}
                root.__yuktTtsChunkSession = null;
              }} catch (eC) {{}}
            }}
            function _yuktFetchAndPlayText(txt, apiBase, reqId, msgKey) {{
              var t = (txt || "").toString().trim();
              var b = (apiBase || "").toString().replace(/\\/+$/, "");
              if (!t || !b) return;
              _yuktCancelChunkSession();
              _yuktClientLog("tts_fetch_begin", {{
                path: "/tts/synthesize",
                msg_key: (msgKey || "").toString(),
                text_chars: t.length
              }});
              fetch(b + "/tts/synthesize", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ text: t, request_id: Number(reqId || Date.now()) }})
              }})
              .then(function(resp) {{
                if (!resp.ok) {{
                  if (resp.status === 409) return null;
                  throw new Error("TTS fetch failed status=" + resp.status);
                }}
                return resp.json();
              }})
              .then(function(data) {{
                if (!data) return;
                var ab64 = ((data.audio_base64 || "") + "");
                var mt = ((data.mime_type || "audio/wav") + "");
                if (!ab64) return;
                _yuktClientLog("tts_fetch_done", {{
                  msg_key: (msgKey || "").toString(),
                  audio_chars: ab64.length
                }});
                try {{
                  audio.pause();
                  audio.currentTime = 0;
                  audio.onended = null;
                }} catch (e1) {{}}
                root.__yuktTtsMode = "audio";
                root.__yuktTtsMsgKey = (msgKey || "").toString();
                audio.src = "data:" + mt + ";base64," + ab64;
                _yuktPlayWithRetry(3);
              }})
              .catch(function(err) {{
                _yuktClientLog("tts_fetch_error", {{
                  msg_key: (msgKey || "").toString(),
                  error: err ? String(err.message || err) : ""
                }});
              }});
            }}
            if (cmd.action === "pause") {{
              try {{
                if (root.__yuktTtsMode === "speech" && root.speechSynthesis) {{
                  root.speechSynthesis.pause();
                }} else {{
                  audio.pause();
                }}
              }} catch (e1) {{}}
              return;
            }}
            if (cmd.action === "resume") {{
              try {{
                if (root.__yuktTtsMode === "speech" && root.speechSynthesis) {{
                  root.speechSynthesis.resume();
                }} else {{
                  var p0 = audio.play();
                  if (p0 && typeof p0.catch === "function") p0.catch(function() {{}});
                }}
              }} catch (e2) {{}}
              return;
            }}
            if (cmd.action === "speak_text") {{
              var speakTxt = (cmd.text || "").toString().trim();
              var apiBase2 = (cmd.api_base || "").toString();
              var reqId2 = Number(cmd.request_id || Date.now());
              var msgKey2 = (cmd.msg_key || "").toString();
              var preferredLang = (cmd.preferred_lang || "").toString().trim();
              if (!speakTxt) return;
              if (!root.speechSynthesis) {{
                _yuktFetchAndPlayText(speakTxt, apiBase2, reqId2, msgKey2);
                return;
              }}
              try {{
                _yuktCancelChunkSession();
                try {{ audio.pause(); audio.onended = null; }} catch (eSp) {{}}
                root.speechSynthesis.cancel();
                var utt = new SpeechSynthesisUtterance(speakTxt);
                utt.rate = 1.0;
                utt.pitch = 1.0;
                if (preferredLang) {{
                  utt.lang = preferredLang;
                }}
                try {{
                  var voices = root.speechSynthesis.getVoices ? root.speechSynthesis.getVoices() : [];
                  if (voices && voices.length) {{
                    var target = (preferredLang || utt.lang || "").toLowerCase();
                    var best = null;
                    for (var i = 0; i < voices.length; i++) {{
                      var vl = String(voices[i].lang || "").toLowerCase();
                      if (target && vl === target) {{ best = voices[i]; break; }}
                    }}
                    if (!best && target) {{
                      for (var j = 0; j < voices.length; j++) {{
                        var vl2 = String(voices[j].lang || "").toLowerCase();
                        if (vl2.indexOf(target.split("-")[0]) === 0) {{ best = voices[j]; break; }}
                      }}
                    }}
                    if (best) utt.voice = best;
                  }}
                }} catch (eVoice) {{}}
                root.__yuktTtsMode = "speech";
                root.__yuktTtsMsgKey = msgKey2;
                var started = false;
                var finished = false;
                utt.onstart = function() {{ started = true; }};
                utt.onend = function() {{ finished = true; }};
                utt.onerror = function() {{
                  if (finished) return;
                  _yuktFetchAndPlayText(speakTxt, apiBase2, reqId2, msgKey2);
                }};
                root.speechSynthesis.speak(utt);
                root.setTimeout(function() {{
                  try {{
                    var isSpeaking = !!(root.speechSynthesis && root.speechSynthesis.speaking);
                    if (!started || (!isSpeaking && !finished)) {{
                      _yuktFetchAndPlayText(speakTxt, apiBase2, reqId2, msgKey2);
                    }}
                  }} catch (e2) {{
                    _yuktFetchAndPlayText(speakTxt, apiBase2, reqId2, msgKey2);
                  }}
                }}, 450);
              }} catch (e3) {{}}
              return;
            }}
            if (cmd.action === "chunk_play") {{
              var ctxt = (cmd.text || "").toString().trim();
              var capi = (cmd.api_base || "").toString().replace(/\\/+$/, "");
              if (!ctxt || !capi) return;
              var cmaxChars = Number(cmd.chunk_max_chars || 900);
              var cchunks = _yuktSplitTextForTTS(ctxt, cmaxChars);
              if (!cchunks || cchunks.length === 0) return;
              _yuktCancelChunkSession();
              var cmsgKey = (cmd.msg_key || "").toString();
              var cbaseReqId = Number(cmd.request_id || Date.now());
              var session = {{
                cancelled: false,
                msgKey: cmsgKey,
                chunks: cchunks,
                audioUrls: new Array(cchunks.length),
                currentIdx: 0,
                nextFetchIdx: 0,
                fetching: false,
                apiBase: capi,
                baseReqId: cbaseReqId
              }};
              root.__yuktTtsChunkSession = session;
              root.__yuktTtsMode = "audio";
              root.__yuktTtsMsgKey = cmsgKey;
              function _yuktChunkFetch(sess, i) {{
                if (sess.cancelled || i >= sess.chunks.length) return Promise.resolve(null);
                return fetch(sess.apiBase + "/tts/synthesize", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{
                    text: sess.chunks[i],
                    request_id: sess.baseReqId + i + 1
                  }})
                }})
                .then(function(r) {{
                  if (!r.ok) {{
                    if (r.status === 409) return null;
                    throw new Error("tts " + r.status);
                  }}
                  return r.json();
                }})
                .then(function(data) {{
                  if (!data || sess.cancelled) return null;
                  var ab64 = ((data.audio_base64 || "") + "");
                  var mt = ((data.mime_type || "audio/wav") + "");
                  if (!ab64) return null;
                  sess.audioUrls[i] = "data:" + mt + ";base64," + ab64;
                  return sess.audioUrls[i];
                }})
                .catch(function(err) {{
                  _yuktClientLog("tts_chunk_error", {{
                    msg_key: sess.msgKey,
                    idx: i,
                    error: err ? String(err.message || err) : ""
                  }});
                  return null;
                }});
              }}
              function _yuktChunkPlayIdx(sess, i) {{
                if (sess.cancelled || i >= sess.chunks.length) return;
                if (root.__yuktTtsChunkSession !== sess) return;
                var src = sess.audioUrls[i];
                if (!src) {{
                  var tries0 = 0;
                  function waitSrc0() {{
                    if (sess.cancelled || root.__yuktTtsChunkSession !== sess) return;
                    var s2 = sess.audioUrls[i];
                    if (s2) {{
                      _yuktChunkPlayIdx(sess, i);
                      return;
                    }}
                    tries0 += 1;
                    if (tries0 < 600) root.setTimeout(waitSrc0, 50);
                  }}
                  root.setTimeout(waitSrc0, 15);
                  return;
                }}
                try {{
                  audio.pause();
                  audio.currentTime = 0;
                }} catch (eP0) {{}}
                root.__yuktTtsMode = "audio";
                root.__yuktTtsMsgKey = sess.msgKey;
                audio.src = src;
                audio.onended = function() {{
                  if (sess.cancelled) return;
                  if (root.__yuktTtsChunkSession !== sess) return;
                  var nextIdx = i + 1;
                  sess.currentIdx = nextIdx;
                  _yuktChunkPump(sess);
                  var triesN = 0;
                  function playNext() {{
                    if (sess.cancelled || root.__yuktTtsChunkSession !== sess) return;
                    if (nextIdx >= sess.chunks.length) return;
                    var s2 = sess.audioUrls[nextIdx];
                    if (s2) {{
                      _yuktChunkPlayIdx(sess, nextIdx);
                      return;
                    }}
                    triesN += 1;
                    if (triesN >= 600) return;
                    root.setTimeout(playNext, 50);
                  }}
                  playNext();
                }};
                _yuktPlayWithRetry(3);
              }}
              function _yuktChunkPump(sess) {{
                if (sess.cancelled || sess.fetching) return;
                if (sess.nextFetchIdx >= sess.chunks.length) return;
                var i = sess.nextFetchIdx++;
                sess.fetching = true;
                _yuktChunkFetch(sess, i).then(function(url) {{
                  sess.fetching = false;
                  if (sess.cancelled) return;
                  if (url && i === sess.currentIdx) {{
                    var idle = !audio.src || audio.paused || audio.ended;
                    if (idle) _yuktChunkPlayIdx(sess, i);
                  }}
                  _yuktChunkPump(sess);
                }});
              }}
              _yuktClientLog("tts_chunk_begin", {{
                msg_key: cmsgKey,
                chunks: cchunks.length,
                first_chars: (cchunks[0] || "").length
              }});
              _yuktChunkPump(session);
              return;
            }}
            if (cmd.action === "fetch_play") {{
              var txt = (cmd.text || "").toString().trim();
              var apiBase = (cmd.api_base || "").toString().replace(/\\/+$/, "");
              if (!txt || !apiBase) return;
              var reqId = Number(cmd.request_id || Date.now());
              var delayMs = Math.max(0, Number(cmd.delay_ms || 0));
              _yuktCancelChunkSession();
              root.__yuktTtsMsgKey = (cmd.msg_key || "").toString();
              var runFetchPlay = function() {{
                _yuktClientLog("tts_fetch_begin", {{
                  path: "/tts/synthesize",
                  msg_key: (cmd.msg_key || "").toString(),
                  text_chars: txt.length
                }});
                fetch(apiBase + "/tts/synthesize", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{ text: txt, request_id: reqId }})
                }})
                .then(function(resp) {{
                  if (!resp.ok) {{
                    if (resp.status === 409) return null;
                    throw new Error("TTS fetch failed status=" + resp.status);
                  }}
                  return resp.json();
                }})
                .then(function(data) {{
                  if (!data) return;
                  var ab64 = ((data.audio_base64 || "") + "");
                  var mt = ((data.mime_type || "audio/wav") + "");
                  if (!ab64) return;
                  _yuktClientLog("tts_fetch_done", {{
                    msg_key: (cmd.msg_key || "").toString(),
                    audio_chars: ab64.length
                  }});
                  try {{
                    audio.pause();
                    audio.currentTime = 0;
                    audio.onended = null;
                  }} catch (e4) {{}}
                root.__yuktTtsMode = "audio";
                  audio.src = "data:" + mt + ";base64," + ab64;
                  _yuktPlayWithRetry(4);
                }})
                .catch(function(err) {{
                  _yuktClientLog("tts_fetch_error", {{
                    msg_key: (cmd.msg_key || "").toString(),
                    error: err ? String(err.message || err) : ""
                  }});
                }});
              }};
              if (delayMs > 0) {{
                root.setTimeout(runFetchPlay, delayMs);
              }} else {{
                runFetchPlay();
              }}
              return;
            }}
            if (cmd.action === "play") {{
              var b64 = (cmd.audio_base64 || "").toString();
              var mime = (cmd.mime_type || "audio/wav").toString();
              if (!b64) return;
              var msgKeyPlay = (cmd.msg_key || "").toString();
              _yuktCancelChunkSession();
              try {{
                audio.pause();
                audio.currentTime = 0;
                audio.onended = null;
              }} catch (e3) {{}}
              root.__yuktTtsMode = "audio";
              audio.src = "data:" + mime + ";base64," + b64;
              root.__yuktTtsMsgKey = msgKeyPlay;
              _yuktPlayWithRetry(4);
            }}
          }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _render_tts_button_icon_css() -> None:
    speaker_uri = _icon_data_uri(UI_SPEAKER_ICON_PATH)
    if not speaker_uri:
        return
    st.markdown(
        f"""
<style>
div[class*="st-key-tts_speak_"] .stButton > button,
div[class*="st-key-tts_speak_pending_"] .stButton > button {{
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 6px !important;
}}
div[class*="st-key-tts_speak_"] .stButton > button::before,
div[class*="st-key-tts_speak_pending_"] .stButton > button::before {{
    content: "" !important;
    width: 16px !important;
    height: 16px !important;
    min-width: 16px !important;
    background-image: url('{speaker_uri}') !important;
    background-size: contain !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    display: inline-block !important;
}}
</style>
""",
        unsafe_allow_html=True,
    )


def _render_main_header_bar() -> None:
    header_logo_uri = _icon_data_uri(UI_HEADER_LOGO_PATH)
    if header_logo_uri:
        st.markdown(
            (
                '<div class="yukt-header-leftcap" aria-hidden="true"></div>'
                '<div class="yukt-header-fullrule" aria-hidden="true"></div>'
                '<div class="yukt-header-bar">'
                '<div class="yukt-header-inner">'
                '<div class="yukt-header-logo-cell">'
                f'<img src="{header_logo_uri}" alt="Yuktra" class="yukt-header-logo" />'
                "</div>"
                '<div class="yukt-header-title-cell">'
                '<h2 class="app-title">Equipment Intelligence</h2>'
                "</div>"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            (
                '<div class="yukt-header-leftcap" aria-hidden="true"></div>'
                '<div class="yukt-header-fullrule" aria-hidden="true"></div>'
                '<div class="yukt-header-bar">'
                '<div class="yukt-header-inner yukt-header-inner--title-only">'
                '<div class="yukt-header-title-cell">'
                '<h2 class="app-title">Equipment Intelligence</h2>'
                "</div></div></div>"
            ),
            unsafe_allow_html=True,
        )


def main():
    st.set_page_config(
        page_title="Equipment Intelligence",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not st.session_state.get("backend_ready"):
        st.markdown(
            '<h2 class="app-title" style="text-align:center">Equipment Intelligence</h2>',
            unsafe_allow_html=True,
        )
        st.info(
            "**Loading models and starting the chat backend.** "
            "The first launch can take a few minutes while the API loads weights and the index."
        )
        st.caption(f"Waiting for `{QNA_API_BASE}/health` …")
        if _api_health_ok(timeout=3.0):
            st.session_state.backend_ready = True
            st.session_state.pop("_backend_wait_n", None)
            st.rerun()
        n = int(st.session_state.get("_backend_wait_n", 0)) + 1
        st.session_state._backend_wait_n = n
        max_waits = int(os.environ.get("YUKTRA_QNA_BACKEND_WAIT_MAX", "720"))
        if n >= max_waits:
            st.error(
                f"The API at {QNA_API_BASE} did not become ready in time. "
                "If you use `./run_chatbot.sh`, check whether uvicorn is still running and read "
                "`/tmp/yuktra_qna_api.log`."
            )
            st.stop()
        time.sleep(0.5)
        st.rerun()

    # Handle left-rail actions via query params (JS -> Python bridge).
    action = None
    try:
        action = (st.query_params.get("y_action") or "").strip()
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            action = (qp.get("y_action", [""]) or [""])[0].strip()
        except Exception:
            action = ""

    if "sidebar_nav_expanded" not in st.session_state:
        st.session_state.sidebar_nav_expanded = False

    if action in {"toggle_sidebar", "open_sidebar"}:
        # Deep-link / rail: open the wide history panel.
        st.session_state.sidebar_nav_expanded = True
        try:
            st.query_params.clear()
        except Exception:
            try:
                st.experimental_set_query_params()
            except Exception:
                pass
        st.rerun()

    if action == "new_chat":
        # Clear params to avoid repeating on refresh.
        try:
            st.query_params.clear()
        except Exception:
            try:
                st.experimental_set_query_params()
            except Exception:
                pass
        st.session_state.session_id = _api_create_session()
        st.session_state.messages = []
        st.session_state.selected_pdf = None
        st.session_state.selected_pdf_page = None
        _sse_dismiss_active_holder()
        st.session_state.rag_pending = None
        st.session_state.sidebar_nav_expanded = True
        st.rerun()

    if "session_id" not in st.session_state:
        st.session_state.session_id = _api_get_latest_session_id() or _api_create_session()

    _maybe_switch_session_from_pdf_link_query()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if not st.session_state.messages:
        loaded = _api_load_messages_for_session(st.session_state.session_id)
        if loaded:
            st.session_state.messages = loaded
        else:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "Hi! Ask me a question about your manuals (troubleshooting, operation, specs, safety). "
                    ),
                }
            )
            _mid, _ts = _api_append_message(
                st.session_state.session_id,
                role="assistant",
                content=st.session_state.messages[0]["content"],
                sources=None,
            )
            st.session_state.messages[0]["ts"] = _ts
            st.session_state.messages[0]["message_id"] = int(_mid)
    if "selected_pdf" not in st.session_state:
        st.session_state.selected_pdf = None
    if "selected_pdf_page" not in st.session_state:
        st.session_state.selected_pdf_page = None
    _consume_yukt_pdf_deep_link()
    # PDF dialog: only keep selected_pdf for the rerun right after a source click. Any other
    # rerun (dialog X, sidebar, history) must clear it or the modal reopens every time.
    if not st.session_state.pop("_pdf_just_open", False):
        st.session_state.selected_pdf = None
        st.session_state.selected_pdf_page = None
    if "rag_pending" not in st.session_state:
        st.session_state.rag_pending = None
    if "rag_pending_from_stt" not in st.session_state:
        st.session_state.rag_pending_from_stt = False
    if "stt_fill_query" not in st.session_state:
        st.session_state.stt_fill_query = ""
    if "tts_active_msg_key" not in st.session_state:
        st.session_state.tts_active_msg_key = ""
    if "tts_is_paused" not in st.session_state:
        st.session_state.tts_is_paused = False
    if "tts_browser_cmd" not in st.session_state:
        st.session_state.tts_browser_cmd = None
    if "tts_pending_play" not in st.session_state:
        st.session_state.tts_pending_play = None
    # Do not drop _sse_thread_started while a worker is still reading; that caused a second
    # POST /chat/ask/stream (duplicate LLM) after session switch or similar reruns.
    if not st.session_state.get("rag_pending"):
        h_idle = st.session_state.get("_sse_holder")
        if h_idle is None or bool(h_idle.get("finished")):
            _sse_clear_session_keys()
    if "rag_logging_ready" not in st.session_state:
        _log_dir = os.path.join(DATA_DIR, "logs")
        get_logger("yuktra_qna.app", log_dir=_log_dir, also_console=False).info("streamlit_app_start")
        st.session_state.rag_logging_ready = True

    # Global theme (must not live only inside sidebar — launcher/pywebview reruns need document-level CSS).
    st.markdown(APP_CSS, unsafe_allow_html=True)
    _render_tts_button_icon_css()

    _nav_cls = (
        "yukt-sidebar-nav-expanded"
        if st.session_state.get("sidebar_nav_expanded")
        else "yukt-sidebar-nav-collapsed"
    )
    st.markdown(
        f"""
<script>
(function () {{
  const doc = (window.parent && window.parent !== window) ? window.parent.document : document;
  const root = doc.documentElement;
  root.classList.remove('yukt-sidebar-nav-expanded', 'yukt-sidebar-nav-collapsed');
  root.classList.add('{_nav_cls}');
}})();
</script>
""",
        unsafe_allow_html=True,
    )

    _render_main_header_bar()

    _nav_w = 300 if st.session_state.sidebar_nav_expanded else 100
    st.markdown(
        f"""
<style>
section[data-testid="stSidebar"] {{
    min-width: {_nav_w}px !important;
    max-width: {_nav_w}px !important;
    top: var(--yukt-header-height, 64px) !important;
    height: calc(100vh - var(--yukt-header-height, 64px)) !important;
    max-height: calc(100vh - var(--yukt-header-height, 64px)) !important;
    transform: translateX(0px) !important;
    border-top: 0 !important;
    border-left: 0 !important;
    border-right: 1px solid var(--yukt-chrome-border, rgba(15, 23, 42, 0.14)) !important;
    border-bottom: 0 !important;
    box-sizing: border-box !important;
}}
</style>
""",
        unsafe_allow_html=True,
    )

    _apply_sidebar_drawer_css(
        open_sidebar=True,
        nav_expanded=bool(st.session_state.sidebar_nav_expanded),
    )
    _apply_yukt_pdf_inline_link_same_window_delegate()

    with st.sidebar:
        nav_exp = st.session_state.sidebar_nav_expanded
        product_icon = _nav_icon_label(UI_PRODUCT_ICON_PATH, "", False, "◉")
        new_label = _nav_icon_label(UI_NEW_ICON_PATH, "New Chat", nav_exp, "➕")
        history_label = _nav_icon_label(UI_HISTORY_ICON_PATH, "History", nav_exp, "🕘")

        if nav_exp:
            if st.button(product_icon, key="yukt_product_btn", use_container_width=True):
                st.session_state.sidebar_nav_expanded = not st.session_state.sidebar_nav_expanded
                st.rerun()
            if st.button(new_label, key="yukt_new_btn", use_container_width=True):
                st.session_state.session_id = _api_create_session()
                st.session_state.messages = []
                st.session_state.selected_pdf = None
                st.session_state.selected_pdf_page = None
                _sse_dismiss_active_holder()
                st.session_state.rag_pending = None
                st.rerun()
        else:
            if st.button(product_icon, key="yukt_product_btn", use_container_width=True):
                st.session_state.sidebar_nav_expanded = not st.session_state.sidebar_nav_expanded
                st.rerun()

            if st.button(new_label, key="yukt_new_btn", use_container_width=True):
                st.session_state.session_id = _api_create_session()
                st.session_state.messages = []
                st.session_state.selected_pdf = None
                st.session_state.selected_pdf_page = None
                _sse_dismiss_active_holder()
                st.session_state.rag_pending = None
                st.rerun()

        if st.button(history_label, key="yukt_history_btn", use_container_width=True):
            st.session_state.sidebar_nav_expanded = not st.session_state.sidebar_nav_expanded
            st.rerun()

        _apply_sidebar_drawer_css(
            open_sidebar=True,
            nav_expanded=bool(st.session_state.sidebar_nav_expanded),
        )

        st.divider()

        if st.session_state.sidebar_nav_expanded:
            sessions = _api_list_sessions(limit=100)
            if sessions:
                def _preview(text: str | None, n: int = 800) -> str:
                    t = (text or "").strip().replace("\n", " ")
                    if not t:
                        return "(empty)"
                    t = re.sub(
                        r"\s*\(\s*troubleshooting\s*,\s*operation\s*,\s*specs\s*,\s*safety\s*\)\s*\.?\s*$",
                        "",
                        t,
                        flags=re.IGNORECASE,
                    )
                    return (t[: n - 1] + "…") if len(t) > n else t

                query = st.text_input(
                    "Search",
                    value="",
                    placeholder="Search saved chats…",
                    help="Filters the saved chats list by the chat title (your first real prompt).",
                )
                q = query.strip().lower()
                filtered = [
                    s
                    for s in sessions
                    if not q
                    or q in str(s.get("first_message") or "").lower()
                    or q in str(s.get("session_id") or "").lower()
                ]

                st.markdown(
                    '<div class="yukt-saved-chats-label"><strong>Saved chats</strong></div>'
                    '<div class="yukt-saved-chats-gap" aria-hidden="true"></div>',
                    unsafe_allow_html=True,
                )
                try:
                    history_scroll_box = st.container(key="yukt_history_scroll_box", border=False)
                except TypeError:
                    history_scroll_box = st.container(key="yukt_history_scroll_box")
                with history_scroll_box:
                    if not filtered:
                        st.caption("No matches.")
                    else:
                        for s in filtered:
                            title = _preview(str(s.get("first_message") or ""))
                            if st.button(
                                title,
                                key=f"hist_open_{s.get('session_id')}",
                                use_container_width=True,
                            ):
                                st.session_state.session_id = str(s.get("session_id"))
                                st.session_state.messages = _api_load_messages_for_session(
                                    st.session_state.session_id
                                )
                                st.session_state.selected_pdf = None
                                st.session_state.selected_pdf_page = None
                                _sse_dismiss_active_holder()
                                st.session_state.rag_pending = None
                                st.rerun()

            else:
                st.caption("No saved chats yet.")

        # NOTE: PDF filtering is disabled for now.
        # pdfs = list_distinct_pdfs(CHAT_DB_PATH)
        # if pdfs:
        #     st.markdown("---")
        #     st.markdown("#### Filter by PDF")
        #     pdf_labels = [
        #         f"{p.doc_name}  —  {p.doc_path}" if p.doc_path else p.doc_name for p in pdfs
        #     ]
        #     chosen = st.selectbox(
        #         "Load most recent chat that referenced a PDF",
        #         options=list(range(len(pdfs))),
        #         format_func=lambda i: pdf_labels[i],
        #         index=None,
        #         placeholder="Pick a PDF…",
        #     )
        #     if chosen is not None:
        #         ref = pdfs[int(chosen)]
        #         pdf_sessions = find_sessions_for_pdf(
        #             CHAT_DB_PATH, doc_name=ref.doc_name, doc_path=ref.doc_path
        #         )
        #         if pdf_sessions and st.button(
        #             "Load most recent for this PDF", use_container_width=True
        #         ):
        #             st.session_state.session_id = pdf_sessions[0]
        #             st.session_state.messages = load_messages_for_session(
        #                 CHAT_DB_PATH, st.session_state.session_id
        #             )
        #             st.session_state.selected_pdf = None
        #             st.session_state.rag_pending = None
        #             st.rerun()

    for i, msg in enumerate(st.session_state.messages):
        if msg["role"] == "user":
            user_mid: int | None = None
            try:
                raw_user_mid = msg.get("message_id")
                if raw_user_mid is not None:
                    user_mid = int(raw_user_mid)
                    if user_mid < 1:
                        user_mid = None
            except (TypeError, ValueError):
                user_mid = None
            _render_user_turn(msg["content"], ts=msg.get("ts"), msg_index=i, message_id=user_mid)
        else:
            mid_row: int | None = None
            try:
                raw_mid = msg.get("message_id")
                if raw_mid is not None:
                    mid_row = int(raw_mid)
                    if mid_row < 1:
                        mid_row = None
            except (TypeError, ValueError):
                mid_row = None
            _render_assistant_turn(
                content=msg["content"],
                msg_index=i,
                sources=msg.get("sources"),
                images=msg.get("images"),
                ts=msg.get("ts"),
                message_id=mid_row,
            )
    _render_inline_pdf_modal_bridge(_merge_all_assistant_inline_pdf_payloads(st.session_state.messages))
    _render_inline_video_modal_bridge(_merge_all_assistant_inline_video_payloads(st.session_state.messages))
    _render_inline_image_zoom_bridge()
    _maybe_scroll_back_to_clicked_message()
    _scroll_to_latest_chat_turn_once()

    _render_browser_stt_bridge()
    _render_virtual_keyboard_bridge()
    _resolve_pending_tts_play()
    _render_tts_audio_bridge(st.session_state.pop("tts_browser_cmd", None))

    if st.session_state.get("stt_fill_query"):
        _frontend_logger().info(
            "frontend_stt_fill_query applying_to_input chars=%d preview=%r",
            len(str(st.session_state.get("stt_fill_query") or "")),
            str(st.session_state.get("stt_fill_query") or "")[:200],
        )
        st.session_state["yukt_chat_input"] = str(st.session_state.get("stt_fill_query") or "")
        st.session_state.stt_fill_query = ""

    typed_user_msg = st.chat_input(
        "Ask about your equipment manuals…",
        key="yukt_chat_input",
    )
    user_msg = typed_user_msg

    if user_msg:
        q_raw = user_msg.strip()
        from_stt = False
        q = q_raw
        # Browser chat input normalization can strip invisible marker characters.
        # Accept either marker and strip repeatedly if both are present.
        if q_raw.startswith(STT_CHAT_PREFIX) or q_raw.startswith(LEGACY_STT_CHAT_PREFIX) or q_raw.startswith("yukt_stt"):
            from_stt = True
            q = q_raw
            while True:
                if q.startswith(STT_CHAT_PREFIX):
                    q = q[len(STT_CHAT_PREFIX) :].strip()
                    continue
                if q.startswith(LEGACY_STT_CHAT_PREFIX):
                    q = q[len(LEGACY_STT_CHAT_PREFIX) :].strip()
                    continue
                if q.startswith("yukt_stt"):
                    q = q[len("yukt_stt") :].strip()
                    continue
                break
        if not q:
            st.stop()
        # Only open the document viewer on explicit clicks, not across new questions.
        st.session_state.selected_pdf = None
        st.session_state.selected_pdf_page = None
        st.session_state.messages.append({"role": "user", "content": q})
        _mid, _ts = _api_append_message(
            st.session_state.session_id,
            role="user",
            content=q,
            sources=None,
        )
        st.session_state.messages[-1]["ts"] = _ts
        st.session_state.messages[-1]["message_id"] = int(_mid)
        # Immediate UI update: render user message first, then process in next rerun.
        st.session_state.rag_pending = q
        st.session_state.rag_pending_from_stt = bool(from_stt)
        st.session_state._yukt_scroll_target = _user_message_anchor_id(
            len(st.session_state.messages) - 1,
            int(_mid),
        )
        st.session_state._yukt_scroll_to_latest_on_submit = True
        st.rerun()

    if st.session_state.rag_pending:
        question = st.session_state.rag_pending
        rag_from_stt = bool(st.session_state.get("rag_pending_from_stt"))
        if st.session_state.get("_sse_bound_question") != question:
            _sse_dismiss_active_holder()
            _sse_clear_worker_keys()
            st.session_state._sse_bound_question = question

        h = st.session_state.get("_sse_holder")

        if not hasattr(st, "fragment") or not USE_FRAGMENT_STREAM:
            col_ai, _ = st.columns([0.88, 0.12])
            with col_ai:
                try:
                    answer, sources, images = _stream_assistant_reply_blocking_fallback(question)
                except Exception as e:
                    st.error(f"Something went wrong: {e}")
                    st.session_state.rag_pending = None
                    st.session_state.rag_pending_from_stt = False
                    _sse_clear_session_keys()
                    st.stop()
                _mid, _ats = _api_append_message(
                    st.session_state.session_id,
                    role="assistant",
                    content=answer,
                    sources=sources,
                    images=images,
                )
            if rag_from_stt and str(answer or "").strip():
                active_key = str(st.session_state.get("tts_active_msg_key") or "")
                if active_key == STREAM_TTS_PENDING_MSG_KEY:
                    st.session_state.tts_active_msg_key = f"tts_speak_{int(_mid)}"
                pending = st.session_state.get("tts_pending_play")
                if isinstance(pending, dict) and str(pending.get("msg_key") or "") == STREAM_TTS_PENDING_MSG_KEY:
                    pending["msg_key"] = f"tts_speak_{int(_mid)}"
            if str(answer or "").strip():
                _move_tts_cached_audio(STREAM_TTS_PENDING_MSG_KEY, f"tts_speak_{int(_mid)}")
                _ensure_tts_prefetch(f"tts_speak_{int(_mid)}", answer)
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "sources": sources,
                    "images": images,
                    "ts": _ats,
                    "message_id": int(_mid),
                }
            )
            st.session_state.rag_pending = None
            st.session_state.rag_pending_from_stt = False
            _sse_clear_session_keys()
            st.rerun()
            st.stop()

        if h and h.get("finished") and not st.session_state.get("_sse_committed"):
            st.session_state._sse_committed = True
            lock = st.session_state._sse_lock
            with lock:
                err = h.get("error")
                dismissed = bool(h.get("dismissed"))
                meta = h.get("done_meta") or {}
                chunks = h.get("chunks") or []
            if dismissed:
                st.session_state.rag_pending = None
                st.session_state.rag_pending_from_stt = False
                _sse_clear_session_keys()
                st.rerun()
                st.stop()
            if err:
                st.error(f"Something went wrong: {err}")
            else:
                streamed_text = "".join(chunks).strip()
                backend_final = str(meta.get("answer") or "").strip()
                sources = meta.get("sources") or []
                images = meta.get("images") or []
                if not isinstance(sources, list):
                    sources = []
                if not isinstance(images, list):
                    images = []
                answer = _compose_final_assistant_text(streamed_text, backend_final, images)
                _mid, _ats = _api_append_message(
                    st.session_state.session_id,
                    role="assistant",
                    content=answer,
                    sources=sources,
                    images=images,
                )
                if rag_from_stt and str(answer or "").strip():
                    active_key = str(st.session_state.get("tts_active_msg_key") or "")
                    if active_key == STREAM_TTS_PENDING_MSG_KEY:
                        st.session_state.tts_active_msg_key = f"tts_speak_{int(_mid)}"
                    pending = st.session_state.get("tts_pending_play")
                    if isinstance(pending, dict) and str(pending.get("msg_key") or "") == STREAM_TTS_PENDING_MSG_KEY:
                        pending["msg_key"] = f"tts_speak_{int(_mid)}"
                if str(answer or "").strip():
                    _move_tts_cached_audio(STREAM_TTS_PENDING_MSG_KEY, f"tts_speak_{int(_mid)}")
                    _ensure_tts_prefetch(f"tts_speak_{int(_mid)}", answer)
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": answer,
                        "sources": sources,
                        "images": images,
                        "ts": _ats,
                        "message_id": int(_mid),
                    }
                )
            st.session_state.rag_pending = None
            st.session_state.rag_pending_from_stt = False
            _sse_clear_session_keys()
            st.rerun()
            st.stop()

        if not st.session_state.get("_sse_thread_started"):
            holder: dict[str, Any] = {
                "chunks": [],
                "images": [],
                "pending_inline_images": [],
                "stream_chars": 0,
                "tts_prefetch_chars": 0,
                "tts_prefetch_ts": 0.0,
                "progress": "",
                "progress_fade_text": "",
                "progress_fade_until": 0.0,
                "finished": False,
                "error": None,
                "done_meta": None,
                "dismissed": False,
                "inline_tags_inserted": False,
                "first_token_ts": 0.0,
            }
            st.session_state._sse_holder = holder
            st.session_state._sse_lock = threading.Lock()
            threading.Thread(
                target=_sse_worker,
                args=(question, holder, st.session_state._sse_lock),
                daemon=True,
            ).start()
            st.session_state._sse_thread_started = True

        _render_rag_stream_fragment()
        _ensure_query_in_view_during_stream()

    if st.session_state.selected_pdf:
        show_pdf_dialog(
            st.session_state.selected_pdf,
            scroll_to_page=st.session_state.get("selected_pdf_page"),
        )

if __name__ == "__main__":
    main()
