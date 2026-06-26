import atexit
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from lingua import Language
from lingua import LanguageDetectorBuilder
from logger import get_logger


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# Honor DATA_DIR env (installer/service) so STT models resolve under ProgramData.
DATA_DIR = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(REPO_ROOT, "data")

WHISPER_CPP_BIN = os.environ.get(
    "YUKTRA_WHISPER_CPP_BIN",
    os.path.join(DATA_DIR, "models", "whisper.cpp", "build", "bin", "whisper-cli"),
).strip()
WHISPER_SERVER_BIN = os.environ.get(
    "YUKTRA_WHISPER_SERVER_BIN",
    os.path.join(DATA_DIR, "models", "whisper.cpp", "build", "bin", "whisper-server"),
).strip()
WHISPER_MODEL_PATH = os.environ.get(
    "YUKTRA_WHISPER_MODEL_PATH",
    os.path.join(DATA_DIR, "models", "ggml-base.bin"),
).strip()
WHISPER_THREADS = int(os.environ.get("YUKTRA_WHISPER_THREADS", str(os.cpu_count() or 4)))
# whisper-server is a warm long-lived process, so it can use more threads than
# the cold whisper-cli fallback. The default lifts the CLI's per-call limit
# (often forced to 1 in deployment .env files to keep STT off the LLM cores)
# up to at least 2 so live partial polls don't take ~10s per 1s of audio.
WHISPER_SERVER_THREADS = int(
    os.environ.get(
        "YUKTRA_WHISPER_SERVER_THREADS",
        str(max(2, WHISPER_THREADS)),
    )
)
WHISPER_LANG = os.environ.get("YUKTRA_WHISPER_LANG", "auto").strip() or "auto"
# Initial-prompt bias for whisper. Whisper uses this as left-context when
# decoding, which dramatically improves recognition of in-domain vocabulary
# without retraining. Override via YUKTRA_WHISPER_PROMPT in .env to add your
# specific machine names, model numbers, or terms.
_DEFAULT_WHISPER_PROMPT = (
    "Industrial equipment manual question and answer. "
    "Topics: machine, equipment, manual, operation, troubleshooting, error, "
    "fault, maintenance, calibration, sensor, valve, motor, pump, actuator, "
    "PLC, HMI, pneumatic, hydraulic, servo, encoder, voltage, pressure, "
    "temperature, frequency, sequence, settings, page, section, diagram. "
    "Hindi + English code-switching is common."
)
WHISPER_PROMPT = os.environ.get("YUKTRA_WHISPER_PROMPT", _DEFAULT_WHISPER_PROMPT).strip()
# Greedy decoding (beam_size=1) is roughly 2x faster than the whisper.cpp
# default (-1, which expands to beam search + temperature fallback). On short
# real-time clips the accuracy difference is tiny, especially with a prompt.
WHISPER_BEAM_SIZE = int(os.environ.get("YUKTRA_WHISPER_BEAM_SIZE", "1"))
WHISPER_BEST_OF = int(os.environ.get("YUKTRA_WHISPER_BEST_OF", "1"))
WHISPER_FAST_AUTO_ACCEPT = os.environ.get("YUKTRA_WHISPER_FAST_AUTO_ACCEPT", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
WHISPER_SERVER_HOST = os.environ.get("YUKTRA_WHISPER_SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
WHISPER_SERVER_PORT = int(os.environ.get("YUKTRA_WHISPER_SERVER_PORT", "8009"))
WHISPER_SERVER_ENABLED = os.environ.get("YUKTRA_WHISPER_SERVER_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
WHISPER_SERVER_STARTUP_TIMEOUT_SEC = float(
    os.environ.get("YUKTRA_WHISPER_SERVER_STARTUP_TIMEOUT_SEC", "30")
)

# ── faster-whisper (CTranslate2) — preferred backend ─────────────────────────
# Significantly faster than whisper.cpp on CPU (2-4x) for the same model size,
# and pure Python — no subprocess, no HTTP hop, no model-load-per-call. We
# load the CT2-format model once at startup and reuse it. whisper.cpp stays as
# the automatic fallback if the package isn't installed or the load fails.
WHISPER_FASTER_DIR = os.environ.get(
    "YUKTRA_FASTER_WHISPER_DIR",
    os.path.join(DATA_DIR, "models", "faster-whisper-tiny"),
).strip()
WHISPER_FASTER_ENABLED = os.environ.get(
    "YUKTRA_FASTER_WHISPER_ENABLED", "1"
).strip().lower() in ("1", "true", "yes", "on")
# int8 matches the quantization of the downloaded Systran/faster-whisper-tiny
# model (~75 MB). Bumping to "int8_float16" or "float32" trades RAM/CPU for
# marginal accuracy on CPU only — leave as int8 for the 2-core deployment.
WHISPER_FASTER_COMPUTE_TYPE = os.environ.get(
    "YUKTRA_FASTER_WHISPER_COMPUTE_TYPE", "int8"
).strip() or "int8"
# Device for faster-whisper: "auto" (default — CUDA on NVIDIA, else CPU), "cpu", or "cuda".
WHISPER_DEVICE = os.environ.get("YUKTRA_WHISPER_DEVICE", "auto").strip() or "auto"
WHISPER_FASTER_THREADS = int(
    os.environ.get("YUKTRA_FASTER_WHISPER_THREADS", str(max(2, WHISPER_THREADS)))
)
# When this process can't import faster_whisper (e.g. compiled build excludes it),
# run it via an EXTERNAL python that has it. Set YUKTRA_STT_PYTHON to that python.exe
# (the bundled portable python). Used for final transcribes only.
_STT_PYTHON = (os.environ.get("YUKTRA_STT_PYTHON") or "").strip()

_FASTER_WHISPER_MODEL: Any = None
_FASTER_WHISPER_LOCK = threading.Lock()

_stt_log = get_logger("yuktra_qna.stt", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)


def _get_faster_whisper_model():
    """Lazily load (and cache) the CTranslate2 WhisperModel.

    Returns the model on success, or None if the package isn't installed,
    the model directory is missing, or loading fails. Callers should treat
    None as "fall back to whisper.cpp".
    """
    global _FASTER_WHISPER_MODEL
    if _FASTER_WHISPER_MODEL is not None:
        return _FASTER_WHISPER_MODEL
    if not WHISPER_FASTER_ENABLED:
        return None
    if not os.path.isdir(WHISPER_FASTER_DIR):
        _stt_log.warning("faster_whisper dir_missing path=%s", WHISPER_FASTER_DIR)
        return None
    if not os.path.isfile(os.path.join(WHISPER_FASTER_DIR, "model.bin")):
        _stt_log.warning("faster_whisper model_bin_missing dir=%s", WHISPER_FASTER_DIR)
        return None
    with _FASTER_WHISPER_LOCK:
        if _FASTER_WHISPER_MODEL is not None:
            return _FASTER_WHISPER_MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            _stt_log.warning("faster_whisper import_failed err=%r", e)
            return None
        t0 = time.time()
        try:
            _FASTER_WHISPER_MODEL = WhisperModel(
                WHISPER_FASTER_DIR,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_FASTER_COMPUTE_TYPE,
                cpu_threads=(max(1, WHISPER_FASTER_THREADS) if WHISPER_DEVICE == "cpu" else 0),
                num_workers=1,
            )
            _stt_log.info(
                "faster_whisper loaded path=%s compute=%s threads=%d load_sec=%.2f",
                WHISPER_FASTER_DIR,
                WHISPER_FASTER_COMPUTE_TYPE,
                WHISPER_FASTER_THREADS,
                time.time() - t0,
            )
            return _FASTER_WHISPER_MODEL
        except Exception as e:
            _stt_log.error("faster_whisper load_failed err=%r", e)
            _FASTER_WHISPER_MODEL = None
            return None


def _transcribe_via_faster_whisper(audio_path: str, lang: str, partial: bool = False) -> str:
    model = _get_faster_whisper_model()
    if model is None:
        raise RuntimeError("faster-whisper unavailable")
    lang_norm = (lang or "").strip().lower()
    language = lang_norm if (lang_norm and lang_norm != "auto") else None
    segments, _info = model.transcribe(
        audio_path,
        language=language,
        task="transcribe",
        beam_size=max(1, WHISPER_BEAM_SIZE),
        best_of=max(1, WHISPER_BEST_OF),
        temperature=0.0,
        # No initial_prompt: whisper echoes/repeats it on silence or unclear audio
        # (the "...code-switching is common" loop). Domain bias isn't worth that risk.
        initial_prompt=None,
        condition_on_previous_text=False,
        # VAD drops non-speech so silence isn't "transcribed" into a hallucinated
        # repeat of the prompt; no_repeat_ngram_size kills any residual loop.
        vad_filter=True,
        no_repeat_ngram_size=3,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
    )
    # segments is a generator; we must iterate to actually run inference.
    return " ".join(seg.text.strip() for seg in segments).strip()


def warmup_faster_whisper() -> bool:
    """Eager-load the CT2 model so the first partial doesn't pay cold start."""
    return _get_faster_whisper_model() is not None


def _decode_audio_b64(audio_base64: str) -> bytes:
    raw = base64.b64decode((audio_base64 or "").encode("utf-8"), validate=True)
    if not raw:
        raise RuntimeError("Received empty audio payload.")
    return raw


class _WhisperServerManager:
    """Persistent whisper-server subprocess.

    Eliminates the per-request model-load cold start by keeping ggml weights
    resident in a long-lived process. STT requests POST audio over loopback
    HTTP, which is dramatically faster than spawning whisper-cli every call.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[Any] | None = None
        self._lock = threading.Lock()
        self._ready = False
        self._url = f"http://{WHISPER_SERVER_HOST}:{WHISPER_SERVER_PORT}/inference"

    @property
    def url(self) -> str:
        return self._url

    def is_alive(self) -> bool:
        p = self._proc
        return bool(p and p.poll() is None)

    def ensure_started(self) -> bool:
        """Spawn whisper-server if not already running. Returns True on ready."""
        if not WHISPER_SERVER_ENABLED:
            return False
        if not os.path.isfile(WHISPER_SERVER_BIN):
            return False
        if not os.path.isfile(WHISPER_MODEL_PATH):
            return False
        with self._lock:
            if self._ready and self.is_alive():
                return True
            if self._proc and not self.is_alive():
                self._proc = None
                self._ready = False
            if self._proc is None:
                cmd = [
                    WHISPER_SERVER_BIN,
                    "-m",
                    WHISPER_MODEL_PATH,
                    "-t",
                    str(max(1, WHISPER_SERVER_THREADS)),
                    "--host",
                    WHISPER_SERVER_HOST,
                    "--port",
                    str(WHISPER_SERVER_PORT),
                    "-l",
                    WHISPER_LANG or "auto",
                    "-nf",
                    "-bs",
                    str(max(1, WHISPER_BEAM_SIZE)),
                    "-bo",
                    str(max(1, WHISPER_BEST_OF)),
                ]
                if WHISPER_PROMPT:
                    cmd += ["--prompt", WHISPER_PROMPT]
                _stt_log.info("whisper_server starting cmd=%s", " ".join(cmd))
                try:
                    self._proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=(lambda: os.nice(5)) if hasattr(os, "nice") else None,
                        # Windows: no console window for the whisper-server child.
                        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                    )
                except FileNotFoundError as e:
                    _stt_log.error("whisper_server start_failed err=%r", e)
                    self._proc = None
                    return False
            deadline = time.time() + max(1.0, WHISPER_SERVER_STARTUP_TIMEOUT_SEC)
            health_url = f"http://{WHISPER_SERVER_HOST}:{WHISPER_SERVER_PORT}/"
            while time.time() < deadline:
                if not self.is_alive():
                    _stt_log.error("whisper_server died_during_startup")
                    self._proc = None
                    return False
                try:
                    with urllib.request.urlopen(health_url, timeout=0.5) as resp:
                        # Any HTTP response (including 404 on root) means the
                        # listener is bound and serving.
                        _ = resp.status
                        self._ready = True
                        _stt_log.info("whisper_server ready url=%s", self._url)
                        return True
                except urllib.error.HTTPError:
                    self._ready = True
                    _stt_log.info("whisper_server ready url=%s", self._url)
                    return True
                except Exception:
                    time.sleep(0.2)
            _stt_log.error("whisper_server startup_timeout sec=%.1f", WHISPER_SERVER_STARTUP_TIMEOUT_SEC)
            return False

    def stop(self) -> None:
        with self._lock:
            p = self._proc
            self._proc = None
            self._ready = False
        if p and p.poll() is None:
            try:
                p.terminate()
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=1.0)
            except Exception:
                pass


_WHISPER_SERVER = _WhisperServerManager()
atexit.register(_WHISPER_SERVER.stop)


def warmup_whisper_server() -> bool:
    """Called by FastAPI lifespan startup to pre-load STT models.

    Prefers faster-whisper (CTranslate2). If that succeeds we skip spawning
    whisper-server entirely — it'd just be ~150 MB of RAM the deployment box
    can't afford. whisper.cpp is only loaded if faster-whisper fails.
    """
    if warmup_faster_whisper():
        return True
    return _WHISPER_SERVER.ensure_started()


def _build_multipart(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "audio/wav",
) -> tuple[bytes, str]:
    boundary = "----yuktra" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("ascii"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode("ascii"))
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(
            "utf-8"
        )
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), boundary


def _transcribe_via_server(audio_bytes: bytes, lang: str, partial: bool = False) -> str:
    if not _WHISPER_SERVER.ensure_started():
        raise RuntimeError("whisper-server unavailable")
    fields = {
        "language": lang or "auto",
        "response_format": "json",
        "temperature": "0.0",
    }
    # Skip the long industrial-domain prompt on partial transcribes — whisper
    # decodes the prompt as left-context every call, which costs ~20-30% of
    # per-call latency. We only need its bias on the high-accuracy final
    # transcribe; live partials are short and the partial UX tolerates the
    # slightly weaker vocabulary recognition for the speed win.
    if WHISPER_PROMPT and not partial:
        # whisper.cpp's example/server accepts both "prompt" and
        # "initial_prompt" form fields depending on build; send "prompt"
        # which is the canonical name and ignore unknowns server-side.
        fields["prompt"] = WHISPER_PROMPT
    body, boundary = _build_multipart(fields, "file", "audio.wav", audio_bytes, "audio/wav")
    req = urllib.request.Request(
        _WHISPER_SERVER.url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    try:
        out = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(f"whisper-server returned non-JSON response: {e}")
    return str(out.get("text") or "").strip()


_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_DEVANAGARI_SCRIPT_RE = re.compile(r"[\u0900-\u097F]")
_LATIN_SCRIPT_RE = re.compile(r"[A-Za-z]")
_LID_DETECTOR = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.HINDI).build()


def _quality_penalty(text: str) -> float:
    t = str(text or "").strip()
    if not t:
        return 1000.0
    penalty = 0.0
    # Penalize long repeated character runs (common in corrupted STT outputs).
    long_repeat_runs = len(re.findall(r"(.)\1{5,}", t))
    penalty += long_repeat_runs * 4.0
    words = re.findall(r"\w+", t, flags=re.UNICODE)
    if words:
        uniq_ratio = len(set(w.lower() for w in words)) / max(1, len(words))
        if uniq_ratio < 0.45:
            penalty += (0.45 - uniq_ratio) * 8.0
        avg_len = sum(len(w) for w in words) / max(1, len(words))
        if avg_len > 18:
            penalty += min(3.0, (avg_len - 18.0) * 0.5)
    return penalty


def _run_whisper_cli(audio_path: str, out_prefix: str, lang: str, partial: bool = False) -> str:
    cmd = [
        WHISPER_CPP_BIN,
        "-m",
        WHISPER_MODEL_PATH,
        "-f",
        audio_path,
        "-l",
        lang,
        "-t",
        str(max(1, WHISPER_THREADS)),
        "-bs",
        str(max(1, WHISPER_BEAM_SIZE)),
        "-bo",
        str(max(1, WHISPER_BEST_OF)),
        "-otxt",
        "-of",
        out_prefix,
        "-np",
    ]
    if WHISPER_PROMPT and not partial:
        cmd += ["--prompt", WHISPER_PROMPT]
    run = subprocess.run(
        cmd, capture_output=True, text=True,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    if run.returncode != 0:
        stderr = (run.stderr or run.stdout or "").strip()
        _stt_log.error("stt_transcribe whisper_failed lang=%s rc=%d err=%r", lang, run.returncode, stderr[:500])
        raise RuntimeError(f"whisper.cpp failed: {stderr[:500]}")
    txt_path = out_prefix + ".txt"
    if not os.path.isfile(txt_path):
        raise RuntimeError("whisper.cpp did not produce a transcript file.")
    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


_FW_SUBPROC_SCRIPT = (
    "import sys\n"
    # Transcripts may contain non-ASCII (Hindi/accents); force UTF-8 stdout so the
    # child never hits the Windows cp1252 charmap encode error (rc=1).
    "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
    "from faster_whisper import WhisperModel\n"
    "mdir, audio, lang, ct, th = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])\n"
    "m = WhisperModel(mdir, device='cpu', compute_type=ct, cpu_threads=th)\n"
    "kw = {} if (not lang or lang == 'auto') else {'language': lang}\n"
    # vad_filter + no_repeat_ngram_size stop the silence/prompt repeat hallucination.
    "segs, info = m.transcribe(audio, beam_size=1, temperature=0.0, "
    "condition_on_previous_text=False, vad_filter=True, no_repeat_ngram_size=3, "
    "no_speech_threshold=0.6, **kw)\n"
    "sys.stdout.write(''.join(s.text for s in segs))\n"
)


def _transcribe_via_python_faster_whisper(audio_path: str, lang: str) -> str:
    """Run faster-whisper through an external python (YUKTRA_STT_PYTHON)."""
    # Force UTF-8 in the child + decode the pipe as UTF-8 so non-ASCII transcripts
    # don't crash on Windows' default cp1252 codepage.
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    run = subprocess.run(
        [_STT_PYTHON, "-c", _FW_SUBPROC_SCRIPT, WHISPER_FASTER_DIR, audio_path,
         lang or "auto", WHISPER_FASTER_COMPUTE_TYPE, str(WHISPER_FASTER_THREADS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    if run.returncode != 0:
        raise RuntimeError(f"python faster_whisper rc={run.returncode}: {(run.stderr or '')[:300]}")
    return (run.stdout or "").strip()


def _run_whisper(audio_path: str, out_prefix: str, lang: str, partial: bool = False) -> str:
    """Transcribe with the fastest available backend.

    Preference order:
      0. external-python faster-whisper (YUKTRA_STT_PYTHON) — for compiled builds.
      1. faster-whisper (CTranslate2 in-process) — fastest, no subprocess.
      2. whisper-server (warm whisper.cpp HTTP) — fallback.
      3. whisper-cli (cold whisper.cpp subprocess) — last resort.
    """
    # Final transcribes only (subprocess loads the model each call -> too slow for
    # live partials; partials just skip and the final pass fills in).
    if _STT_PYTHON and not partial and os.path.isfile(_STT_PYTHON) and os.path.isdir(WHISPER_FASTER_DIR):
        try:
            return _transcribe_via_python_faster_whisper(audio_path, lang)
        except Exception as e:
            _stt_log.warning("python_faster_whisper failed err=%r; falling back", e)
    if WHISPER_FASTER_ENABLED:
        try:
            return _transcribe_via_faster_whisper(audio_path, lang, partial=partial)
        except Exception as e:
            _stt_log.warning(
                "faster_whisper transcribe_failed lang=%s err=%r; falling back to whisper.cpp",
                lang,
                e,
            )
    if WHISPER_SERVER_ENABLED and os.path.isfile(WHISPER_SERVER_BIN):
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            return _transcribe_via_server(audio_bytes, lang, partial=partial)
        except Exception as e:
            _stt_log.warning("whisper_server transcribe_failed lang=%s err=%r; falling back to CLI", lang, e)
    return _run_whisper_cli(audio_path, out_prefix, lang, partial=partial)


def _candidate_score(text: str, lang: str) -> float:
    t = str(text or "")
    if not t:
        return -1e6
    dev = len(_DEVANAGARI_SCRIPT_RE.findall(t))
    ara = len(_ARABIC_SCRIPT_RE.findall(t))
    lat = len(_LATIN_SCRIPT_RE.findall(t))
    detected_lang = _LID_DETECTOR.detect_language_of(t)
    score = float(len(t)) * 0.02
    score += float(dev) * 0.35
    score -= float(ara) * 0.60
    if detected_lang == Language.HINDI:
        score += 1.2
    elif detected_lang == Language.ENGLISH:
        score += 1.0
    if lang == "hi" and detected_lang == Language.HINDI:
        score += 0.4
    if lang == "en":
        score += float(lat) * 0.1
    score -= _quality_penalty(t)
    return score


def _auto_transcript_is_good_enough(text: str) -> bool:
    """
    Avoid extra whisper.cpp language passes when auto mode already produced a
    clean transcript. This keeps the common English STT path responsive while
    still falling back for noisy/Arabic-script/corrupted auto output.
    """
    t = str(text or "").strip()
    if not t:
        return False
    ara = len(_ARABIC_SCRIPT_RE.findall(t))
    if ara:
        return False
    penalty = _quality_penalty(t)
    if penalty >= 2.0:
        return False
    dev = len(_DEVANAGARI_SCRIPT_RE.findall(t))
    lat = len(_LATIN_SCRIPT_RE.findall(t))
    if not dev and not lat:
        return False
    detected_lang = _LID_DETECTOR.detect_language_of(t)
    if detected_lang in {Language.ENGLISH, Language.HINDI}:
        return True
    # Very short commands may not get a confident language label, but are still
    # fine if they contain normal Latin/Devanagari text and no corruption signal.
    return len(t) <= 80 and (lat + dev) >= max(2, int(len(t) * 0.35))


def transcribe_audio_b64(
    audio_base64: str,
    audio_format: str = "wav",
    partial: bool = False,
) -> dict[str, Any]:
    _stt_log.info(
        "stt_transcribe begin format=%s audio_b64_chars=%d partial=%s model=%s bin=%s",
        audio_format,
        len(audio_base64 or ""),
        partial,
        WHISPER_MODEL_PATH,
        WHISPER_CPP_BIN,
    )
    # Backend availability. faster-whisper is the preferred in-process backend;
    # whisper.cpp (server or cli) is only the fallback. Previously this function
    # hard-required the whisper.cpp binary/model up front, which made STT 500 on
    # any machine without a whisper.cpp build (e.g. Windows) even though
    # faster-whisper was installed and loaded. Only require whisper.cpp when no
    # other backend can serve the request.
    faster_ok = _get_faster_whisper_model() is not None
    whisper_cpp_ok = os.path.isfile(WHISPER_CPP_BIN) and os.path.isfile(WHISPER_MODEL_PATH)
    whisper_server_ok = (
        WHISPER_SERVER_ENABLED
        and os.path.isfile(WHISPER_SERVER_BIN)
        and os.path.isfile(WHISPER_MODEL_PATH)
    )
    # External-python faster-whisper (compiled build): bundled python + model dir.
    python_fw_ok = bool(_STT_PYTHON) and os.path.isfile(_STT_PYTHON) and os.path.isdir(WHISPER_FASTER_DIR)
    if not (faster_ok or whisper_cpp_ok or whisper_server_ok or python_fw_ok):
        _stt_log.error(
            "stt_transcribe no_backend faster_dir=%s cpp_bin=%s model=%s",
            WHISPER_FASTER_DIR,
            WHISPER_CPP_BIN,
            WHISPER_MODEL_PATH,
        )
        raise RuntimeError(
            "No STT backend available. Install faster-whisper (preferred) or set "
            "YUKTRA_WHISPER_CPP_BIN / YUKTRA_WHISPER_MODEL_PATH to a local whisper.cpp build."
        )
    requested_lang = WHISPER_LANG.strip().lower()
    # The English-only-model guard only applies to the whisper.cpp ggml model;
    # faster-whisper uses its own model directory and is unaffected.
    if (whisper_cpp_ok or whisper_server_ok) and not faster_ok:
        model_name = os.path.basename(WHISPER_MODEL_PATH).lower()
        if model_name.endswith(".en.bin") and requested_lang in {"auto", "hi"}:
            raise RuntimeError(
                "Current Whisper model is English-only (.en). "
                "Use a multilingual model (for example ggml-base.bin) for Hindi + English STT."
            )

    fmt = (audio_format or "wav").strip().lower()
    if not fmt:
        fmt = "wav"
    if fmt != "wav":
        raise RuntimeError("Only WAV audio is supported for offline STT right now.")

    raw = _decode_audio_b64(audio_base64)
    _stt_log.info("stt_transcribe decoded_audio_bytes=%d", len(raw))
    chosen_lang = WHISPER_LANG
    with tempfile.TemporaryDirectory(prefix="yuktra_stt_") as td:
        audio_path = os.path.join(td, f"input.{fmt}")
        with open(audio_path, "wb") as f:
            f.write(raw)

        text = _run_whisper(audio_path, os.path.join(td, "transcript_auto"), WHISPER_LANG, partial=partial)
        if requested_lang == "auto" and not partial:
            if WHISPER_FAST_AUTO_ACCEPT and _auto_transcript_is_good_enough(text):
                chosen_lang = "auto"
                _stt_log.info(
                    "stt_transcribe fast_auto_accept transcript_chars=%d preview=%r",
                    len(text),
                    text[:120],
                )
            else:
                candidates: dict[str, str] = {
                    "auto": text,
                    "hi": _run_whisper(audio_path, os.path.join(td, "transcript_hi"), "hi"),
                    "en": _run_whisper(audio_path, os.path.join(td, "transcript_en"), "en"),
                }
                best_lang = "auto"
                best_score = _candidate_score(candidates["auto"], "auto")
                auto_len = len((candidates.get("auto") or "").strip())
                hi_len = len((candidates.get("hi") or "").strip())
                en_len = len((candidates.get("en") or "").strip())
                max_non_en_len = max(auto_len, hi_len)
                for lang_key in ("hi", "en"):
                    s = _candidate_score(candidates[lang_key], lang_key)
                    if lang_key == "en" and max_non_en_len >= 20 and en_len < int(0.75 * max_non_en_len):
                        # Avoid selecting partial English translation when auto/hi contain
                        # substantially more of the spoken utterance.
                        s -= 5.0
                    if s > best_score:
                        best_score = s
                        best_lang = lang_key
                _stt_log.info(
                    "stt_transcribe fallback_applied reason=multi_pass_select best=%s score=%.3f auto_score=%.3f hi_score=%.3f en_score=%.3f auto_preview=%r hi_preview=%r en_preview=%r",
                    best_lang,
                    best_score,
                    _candidate_score(candidates["auto"], "auto"),
                    _candidate_score(candidates["hi"], "hi"),
                    _candidate_score(candidates["en"], "en"),
                    candidates["auto"][:120],
                    candidates["hi"][:120],
                    candidates["en"][:120],
                )
                text = candidates[best_lang]
                chosen_lang = best_lang
    _stt_log.info("stt_transcribe done transcript_chars=%d preview=%r", len(text), text[:200])

    return {
        "text": text,
        "language": chosen_lang,
        "engine": "faster-whisper" if faster_ok else "whisper.cpp",
    }
