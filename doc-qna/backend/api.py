import asyncio
import base64
import concurrent.futures
import io
import json
import logging
import os
import queue
import time
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from typing import Any
#
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse

from chat_history_db import (
    append_message,
    create_session,
    get_latest_session_id,
    init_db,
    list_sessions,
    load_messages_for_session,
)
from logger import get_logger
from qna_service import (
    ask_question,
    ask_question_stream_events,
    get_image_blob_by_uuid,
    judge_ground_truth_gemma,
    warmup_models,
)
from stt_service import transcribe_audio_b64, warmup_whisper_server
from tts_service import TTSCancelledError, synthesize_text_to_wav_b64

# Single thread for load + inference (llama-cpp-python; matches old in-process Streamlit).
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="yuktra_qna_llm",
)
_TTS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="yuktra_qna_tts",
)
# Dedicated STT executor so live partial transcribes don't queue behind LLM
# generation on /chat/ask. Two workers let a final transcribe overlap a
# partial that's already mid-flight at whisper-server.
_STT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="yuktra_qna_stt",
)
_TTS_REQ_LOCK = asyncio.Lock()
_TTS_LATEST_REQUEST_ID = 0
STT_CHAT_PREFIX = os.environ.get("YUKTRA_QNA_STT_CHAT_PREFIX", "\u2063yukt_stt\u2063")
LEGACY_STT_CHAT_PREFIXES = ("[[STT]] ", "[STT] ", "yukt_stt")


def _strip_stt_markers(text: str) -> str:
    out = str(text or "").strip()
    while out:
        changed = False
        if STT_CHAT_PREFIX and out.startswith(STT_CHAT_PREFIX):
            out = out[len(STT_CHAT_PREFIX) :].strip()
            changed = True
        for prefix in LEGACY_STT_CHAT_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix) :].strip()
                changed = True
        if not changed:
            break
    return out


def _enqueue_stream_events(
    question: str,
    q: "asyncio.Queue[Any]",
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Producer side of the SSE pipeline. Runs in a worker thread (LLM executor)
    and hands each event to the asyncio event loop via call_soon_threadsafe,
    waking the consumer immediately without spawning a thread per get()."""
    try:
        for ev in ask_question_stream_events(question):
            loop.call_soon_threadsafe(q.put_nowait, ev)
    except Exception as e:
        loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "message": str(e)})
    finally:
        loop.call_soon_threadsafe(q.put_nowait, None)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    skip = os.environ.get("YUKTRA_QNA_SKIP_WARMUP", "").strip().lower() in ("1", "true", "yes", "on")
    try:
        if not skip:
            # Warmup must never crash startup: on a machine without an ingested
            # default store, warmup_models raises FileNotFoundError. Swallow it so
            # the API still starts and loads stores lazily on the first request.
            try:
                loop = asyncio.get_running_loop()
                # Pre-spawn whisper-server in parallel with LLM warmup so the first
                # mic click doesn't pay the model-load cold start.
                stt_warm = loop.run_in_executor(_STT_EXECUTOR, warmup_whisper_server)
                await loop.run_in_executor(_LLM_EXECUTOR, warmup_models)
                try:
                    await stt_warm
                except Exception:
                    pass
            except Exception as _warm_err:
                logging.getLogger("yuktra_qna.app").warning(
                    "warmup_skipped err=%r (API still starting; stores load lazily)", _warm_err
                )
        yield
    finally:
        _LLM_EXECUTOR.shutdown(wait=False)
        _TTS_EXECUTOR.shutdown(wait=False)
        _STT_EXECUTOR.shutdown(wait=False)


app = FastAPI(title="Yuktra QnA API", version="1.0.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# Use DATA_DIR (same as RAG/stores) so /pdf finds files in the real data folder.
# In a frozen/relocated build, _REPO_ROOT points next to the exe, NOT at the data,
# so the old `_REPO_ROOT/data` path broke PDF serving (viewer showed "0 of 0").
_DATA_DIR = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(_REPO_ROOT, "data")
_INGESTED_DIR = os.path.join(_DATA_DIR, "Ingested")
_PDFJS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfjs")
_PDFJS_VERSION = os.environ.get("YUKTRA_PDFJS_VERSION", "3.11.174").strip()

# Security script injected into viewer.html after download to disable print/download/edit.
_PDFJS_SECURITY_SCRIPT = """
    <script>
      var _yuktHideStyle = document.createElement('style');
      _yuktHideStyle.textContent =
        '#openFile,#print,#download,' +
        '#secondaryOpenFile,#secondaryPrint,#secondaryDownload,' +
        '#editorModeButtons,#editorModeSeparator,' +
        '.verticalToolbarSeparator.hiddenMediumView,' +
        '#editorFreeTextParamsToolbar,#editorInkParamsToolbar,#editorStampParamsToolbar' +
        '{display:none!important}';
      document.head.appendChild(_yuktHideStyle);
      window.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === 'p' || e.key === 'P' || e.key === 's' || e.key === 'S')) {
          e.preventDefault(); e.stopImmediatePropagation();
        }
      }, true);
      window.print = function() {};
      window.addEventListener('load', function() {
        ['print','download','openFile','secondaryPrint','secondaryDownload','secondaryOpenFile'].forEach(function(id) {
          var el = document.getElementById(id);
          if (el) el.addEventListener('click', function(e) { e.preventDefault(); e.stopImmediatePropagation(); }, true);
        });
      });
    </script>
"""


def _ensure_pdfjs() -> None:
    """Download and patch pdf.js viewer if not already present."""
    viewer_html = os.path.join(_PDFJS_DIR, "web", "viewer.html")
    if os.path.isfile(viewer_html):
        return

    url = (
        f"https://github.com/mozilla/pdf.js/releases/download/"
        f"v{_PDFJS_VERSION}/pdfjs-{_PDFJS_VERSION}-dist.zip"
    )
    logging.info("pdf.js not found — downloading v%s from GitHub…", _PDFJS_VERSION)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    except Exception as exc:
        logging.warning("Could not download pdf.js: %s — PDF viewer will be unavailable.", exc)
        return

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(_PDFJS_DIR)
    except Exception as exc:
        logging.warning("Could not extract pdf.js zip: %s", exc)
        return

    # Patch viewer.html to disable print/download/edit buttons.
    try:
        with open(viewer_html, "r", encoding="utf-8") as f:
            html = f.read()
        patched = html.replace("</body>", _PDFJS_SECURITY_SCRIPT + "  </body>", 1)
        with open(viewer_html, "w", encoding="utf-8") as f:
            f.write(patched)
    except Exception as exc:
        logging.warning("Could not patch pdf.js viewer.html: %s", exc)
        return

    logging.info("pdf.js v%s ready at %s", _PDFJS_VERSION, _PDFJS_DIR)


_ensure_pdfjs()
app.mount("/pdfjs", StaticFiles(directory=_PDFJS_DIR, html=True), name="pdfjs")
_CHAT_DB_PATH = os.environ.get(
    "CHAT_DB_PATH",
    os.path.join(_DATA_DIR, "chat_history_db", "chat_history.sqlite3"),
)
init_db(_CHAT_DB_PATH)

_api_log = get_logger(
    "yuktra_qna.api",
    log_dir=os.path.join(_DATA_DIR, "logs"),
    also_console=False,
)


@app.middleware("http")
async def _log_http_requests(request: Request, call_next):
    path = request.url.path
    method = request.method
    is_health = path == "/health"
    t0 = time.perf_counter()
    if not is_health:
        _api_log.info("api_http_request method=%s path=%s", method, path)
    try:
        response = await call_next(request)
    except Exception:
        if not is_health:
            _api_log.exception(
                "api_http_error method=%s path=%s duration_sec=%.4f",
                method,
                path,
                time.perf_counter() - t0,
            )
        raise
    dt = time.perf_counter() - t0
    if not is_health:
        _api_log.info(
            "api_http_response method=%s path=%s status=%s duration_sec=%.4f",
            method,
            path,
            getattr(response, "status_code", "?"),
            dt,
        )
    else:
        _api_log.debug(
            "api_http health method=%s path=%s status=%s duration_sec=%.4f",
            method,
            path,
            getattr(response, "status_code", "?"),
            dt,
        )
    return response


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]
    images: list[dict]


class SessionCreateResponse(BaseModel):
    session_id: str


class SessionSummaryResponse(BaseModel):
    session_id: str
    created_at: str
    last_message_at: str | None
    message_count: int
    first_message: str | None


class MessageAppendRequest(BaseModel):
    role: str
    content: str
    sources: list[dict[str, Any]] | None = None
    images: list[dict[str, Any]] | None = None


class MessageAppendResponse(BaseModel):
    message_id: int
    created_at: str


class GroundTruthJudgeRequest(BaseModel):
    question: str
    expected_response: str
    model_output: str


class GroundTruthJudgeResponse(BaseModel):
    score: int | None = None
    explanation: str | None = None
    error: str | None = None


class STTTranscribeRequest(BaseModel):
    audio_base64: str
    audio_format: str = "wav"
    # Partial transcribes (live polls during recording) skip the multi-pass
    # auto-language fallback so each browser tick returns in roughly one
    # whisper inference instead of three.
    partial: bool = False


class STTTranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    engine: str = "whisper.cpp"


class TTSSynthesizeRequest(BaseModel):
    text: str
    request_id: int | None = None


class TTSSynthesizeResponse(BaseModel):
    audio_base64: str
    mime_type: str = "audio/wav"
    engine: str = "piper"


class ClientDebugLogRequest(BaseModel):
    source: str = "browser"
    event: str
    detail: dict[str, Any] | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/pdf/{fid}")
def serve_pdf(fid: str) -> FileResponse:
    """Serve a PDF directly from data/Ingested/. fid is base64url-encoded relative path."""
    try:
        padding = 4 - len(fid) % 4
        fid_padded = fid + ("=" * padding if padding != 4 else "")
        rel_path = base64.urlsafe_b64decode(fid_padded.encode()).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid fid")
    ingested_root = os.path.normpath(_INGESTED_DIR)
    abs_path = os.path.normpath(os.path.join(_INGESTED_DIR, rel_path))
    if not abs_path.startswith(ingested_root + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(abs_path):
        # Backward/robust fallback: resolve by basename under data/Ingested/*/documents.
        wanted = os.path.basename(rel_path).strip()
        if wanted:
            for machine in sorted(os.listdir(_INGESTED_DIR)) if os.path.isdir(_INGESTED_DIR) else []:
                cand = os.path.join(_INGESTED_DIR, machine, "documents", wanted)
                if os.path.isfile(cand):
                    abs_path = os.path.normpath(cand)
                    break
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(abs_path, media_type="application/pdf", headers={"Cache-Control": "public, max-age=3600"})


_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


@app.get("/video/{fid}")
def serve_video(fid: str) -> FileResponse:
    """Serve a video file from data/Ingested/. fid is base64url-encoded relative path."""
    try:
        padding = 4 - len(fid) % 4
        fid_padded = fid + ("=" * padding if padding != 4 else "")
        rel_path = base64.urlsafe_b64decode(fid_padded.encode()).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid fid")
    ingested_root = os.path.normpath(_INGESTED_DIR)
    abs_path = os.path.normpath(os.path.join(_INGESTED_DIR, rel_path))
    if not abs_path.startswith(ingested_root + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(abs_path):
        wanted = os.path.basename(rel_path).strip()
        if wanted:
            for machine in sorted(os.listdir(_INGESTED_DIR)) if os.path.isdir(_INGESTED_DIR) else []:
                cand = os.path.join(_INGESTED_DIR, machine, "documents", wanted)
                if os.path.isfile(cand):
                    abs_path = os.path.normpath(cand)
                    break
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Video not found")
    ext = os.path.splitext(abs_path)[1].lower()
    media_type = _VIDEO_MEDIA_TYPES.get(ext, "application/octet-stream")
    return FileResponse(
        abs_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600", "Accept-Ranges": "bytes"},
    )


@app.post("/chat/ask", response_model=AskResponse)
async def chat_ask(req: AskRequest) -> AskResponse:
    t_api = time.perf_counter()
    question = _strip_stt_markers(req.question)
    _api_log.info(
        "api_chat_ask step=submit_executor question_chars=%d",
        len(question or ""),
    )
    loop = asyncio.get_running_loop()
    answer, sources, images = await loop.run_in_executor(_LLM_EXECUTOR, ask_question, question)
    _api_log.info(
        "api_chat_ask step=returned executor_await_wall_sec=%.4f answer_chars=%d sources=%d",
        time.perf_counter() - t_api,
        len(answer),
        len(sources),
    )
    return AskResponse(answer=answer, sources=sources, images=images)


@app.post("/eval/judge", response_model=GroundTruthJudgeResponse)
async def eval_ground_truth_judge(req: GroundTruthJudgeRequest) -> GroundTruthJudgeResponse:
    """
    LLM judge using the same on-device Gemma (llama.cpp) as /chat/ask. Used by rag_ground_truth_eval.py
    when YUKTRA_GT_JUDGE=local_gemma.
    """
    if os.environ.get("YUKTRA_DISABLE_EVAL_JUDGE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        raise HTTPException(
            status_code=404, detail="Eval judge is disabled (YUKTRA_DISABLE_EVAL_JUDGE)"
        )
    loop = asyncio.get_running_loop()
    out = await loop.run_in_executor(
        _LLM_EXECUTOR,
        judge_ground_truth_gemma,
        req.question,
        req.expected_response,
        req.model_output,
    )
    return GroundTruthJudgeResponse(
        score=out.get("score"),
        explanation=out.get("explanation"),
        error=out.get("error"),
    )


@app.post("/stt/transcribe", response_model=STTTranscribeResponse)
async def stt_transcribe(req: STTTranscribeRequest) -> STTTranscribeResponse:
    loop = asyncio.get_running_loop()
    out = await loop.run_in_executor(
        _STT_EXECUTOR,
        transcribe_audio_b64,
        req.audio_base64,
        req.audio_format,
        req.partial,
    )
    return STTTranscribeResponse(
        text=_strip_stt_markers(str(out.get("text") or "")),
        language=(str(out.get("language") or "").strip() or None),
        engine=str(out.get("engine") or "whisper.cpp"),
    )


@app.post("/debug/client-log")
async def debug_client_log(req: ClientDebugLogRequest) -> dict[str, bool]:
    _api_log.info(
        "client_debug_log source=%s event=%s detail=%s",
        req.source,
        req.event,
        json.dumps(req.detail or {}, ensure_ascii=False),
    )
    return {"ok": True}


@app.post("/tts/synthesize", response_model=TTSSynthesizeResponse)
async def tts_synthesize(req: TTSSynthesizeRequest) -> TTSSynthesizeResponse:
    global _TTS_LATEST_REQUEST_ID
    req_id = int(req.request_id or int(time.time() * 1000))
    async with _TTS_REQ_LOCK:
        if req_id > _TTS_LATEST_REQUEST_ID:
            _TTS_LATEST_REQUEST_ID = req_id

    def _should_cancel() -> bool:
        return req_id < _TTS_LATEST_REQUEST_ID

    loop = asyncio.get_running_loop()
    try:
        out = await loop.run_in_executor(_TTS_EXECUTOR, synthesize_text_to_wav_b64, req.text, _should_cancel)
    except TTSCancelledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return TTSSynthesizeResponse(
        audio_base64=str(out.get("audio_base64") or ""),
        mime_type=str(out.get("mime_type") or "audio/wav"),
        engine=str(out.get("engine") or "piper"),
    )


@app.post("/chat/ask/stream")
async def chat_ask_stream(req: AskRequest) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[Any]" = asyncio.Queue()
    question = _strip_stt_markers(req.question)
    producer = loop.run_in_executor(_LLM_EXECUTOR, _enqueue_stream_events, question, q, loop)

    async def sse_bytes():
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=10.0)
            except asyncio.TimeoutError:
                # Keep the SSE socket alive while expensive retrieval/LLM prep runs.
                yield b": keepalive\n\n"
                continue
            if ev is None:
                break
            line = json.dumps(ev, ensure_ascii=False)
            yield f"data: {line}\n\n".encode("utf-8")
        await producer

    return StreamingResponse(
        sse_bytes(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/images/{image_uuid}")
def image_by_uuid(image_uuid: str) -> Response:
    out = get_image_blob_by_uuid(image_uuid)
    if not out:
        raise HTTPException(status_code=404, detail="Image not found")
    raw, mime = out
    return Response(
        content=raw,
        media_type=mime or "image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/sessions", response_model=SessionCreateResponse)
def sessions_create() -> SessionCreateResponse:
    return SessionCreateResponse(session_id=create_session(_CHAT_DB_PATH))


@app.get("/sessions/latest")
def sessions_latest() -> dict:
    return {"session_id": get_latest_session_id(_CHAT_DB_PATH)}


@app.get("/sessions", response_model=list[SessionSummaryResponse])
def sessions_list(limit: int = 100) -> list[SessionSummaryResponse]:
    rows = list_sessions(_CHAT_DB_PATH, limit=limit)
    return [
        SessionSummaryResponse(
            session_id=r.session_id,
            created_at=r.created_at,
            last_message_at=r.last_message_at,
            message_count=r.message_count,
            first_message=r.first_message,
        )
        for r in rows
    ]


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str) -> list[dict[str, Any]]:
    return load_messages_for_session(_CHAT_DB_PATH, session_id)


@app.post("/sessions/{session_id}/messages", response_model=MessageAppendResponse)
def session_append_message(session_id: str, req: MessageAppendRequest) -> MessageAppendResponse:
    mid, ts = append_message(
        _CHAT_DB_PATH,
        session_id=session_id,
        role=req.role,
        content=req.content,
        sources=req.sources,
        images=req.images,
    )
    return MessageAppendResponse(message_id=mid, created_at=ts)
