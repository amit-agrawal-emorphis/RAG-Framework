import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any
from typing import Callable

from logger import get_logger

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_data_env = (os.environ.get("DATA_DIR") or "").strip()
DATA_DIR = os.path.abspath(_data_env) if _data_env else os.path.join(REPO_ROOT, "data")


def _default_piper_bin() -> str:
    path_bin = shutil.which("piper")
    if path_bin:
        return path_bin
    local_bin = os.path.join(DATA_DIR, "models", "piper", "piper")
    return local_bin


def _env_nonempty(name: str) -> str | None:
    v = (os.environ.get(name) or "").strip()
    return v if v else None


def _resolve_piper_bin() -> str:
    """Prefer explicit env, then PATH, then mounted data dir, then image-baked /opt/piper."""
    env_b = _env_nonempty("YUKTRA_PIPER_BIN")
    which_p = shutil.which("piper")
    under_data = os.path.join(DATA_DIR, "models", "piper", "piper")
    baked = "/opt/piper/piper"
    for p in (env_b, which_p, under_data, baked):
        if p and os.path.isfile(p):
            return p
    return env_b or _default_piper_bin()


def _resolve_piper_model_path() -> str:
    """Prefer explicit env, then mounted data dir, then image-baked /opt/piper-models."""
    env_m = _env_nonempty("YUKTRA_PIPER_MODEL_PATH")
    under_data = os.path.join(DATA_DIR, "models", "piper", "en_IN-medium.onnx")
    baked = "/opt/piper-models/en_IN-medium.onnx"
    for p in (env_m, under_data, baked):
        if p and os.path.isfile(p):
            return p
    return env_m or under_data


PIPER_BIN = _resolve_piper_bin()
PIPER_MODEL_PATH = _resolve_piper_model_path()
PIPER_SPEAKER_ID = int(os.environ.get("YUKTRA_PIPER_SPEAKER_ID", "0"))
PIPER_LENGTH_SCALE = float(os.environ.get("YUKTRA_PIPER_LENGTH_SCALE", "1.0"))
_tts_log = get_logger("yuktra_qna.tts", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)
_TTS_CACHE_LOCK = threading.Lock()
_TTS_CACHE_MAX_ITEMS = int(os.environ.get("YUKTRA_TTS_CACHE_MAX_ITEMS", "16"))
_TTS_CACHE: dict[str, dict[str, Any]] = {}
_TTS_CACHE_ORDER: list[str] = []


class TTSCancelledError(RuntimeError):
    pass


def _tts_cache_key(text: str, piper_bin: str, piper_model: str) -> str:
    h = hashlib.sha256()
    h.update(str(piper_bin).encode("utf-8", errors="ignore"))
    h.update(b"\0")
    h.update(str(piper_model).encode("utf-8", errors="ignore"))
    h.update(b"\0")
    h.update(str(PIPER_SPEAKER_ID).encode("ascii", errors="ignore"))
    h.update(b"\0")
    h.update(str(PIPER_LENGTH_SCALE).encode("ascii", errors="ignore"))
    h.update(b"\0")
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _get_tts_cache(key: str) -> dict[str, Any] | None:
    with _TTS_CACHE_LOCK:
        cached = _TTS_CACHE.get(key)
        if cached is None:
            return None
        try:
            _TTS_CACHE_ORDER.remove(key)
        except ValueError:
            pass
        _TTS_CACHE_ORDER.append(key)
        return dict(cached)


def _put_tts_cache(key: str, value: dict[str, Any]) -> None:
    if _TTS_CACHE_MAX_ITEMS <= 0:
        return
    with _TTS_CACHE_LOCK:
        _TTS_CACHE[key] = dict(value)
        try:
            _TTS_CACHE_ORDER.remove(key)
        except ValueError:
            pass
        _TTS_CACHE_ORDER.append(key)
        while len(_TTS_CACHE_ORDER) > _TTS_CACHE_MAX_ITEMS:
            old = _TTS_CACHE_ORDER.pop(0)
            _TTS_CACHE.pop(old, None)


def synthesize_text_to_wav_b64(
    text: str,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    txt = str(text or "").strip()
    if not txt:
        return {"audio_base64": "", "mime_type": "audio/wav", "engine": "piper"}
    piper_bin = _resolve_piper_bin()
    piper_model = _resolve_piper_model_path()
    # If YUKTRA_PIPER_PYTHON points to a Python that has piper-tts installed, run piper
    # as a module (python -m piper) -- no standalone piper.exe needed (portable build).
    piper_python = _env_nonempty("YUKTRA_PIPER_PYTHON")
    use_python_module = bool(piper_python) and os.path.isfile(piper_python)
    if not use_python_module and not os.path.isfile(piper_bin):
        raise RuntimeError(
            "Piper not found. Set YUKTRA_PIPER_BIN to piper.exe, "
            "or YUKTRA_PIPER_PYTHON to a Python that has piper-tts."
        )
    if not os.path.isfile(piper_model):
        raise RuntimeError(
            f"Piper model not found at '{piper_model}'. Set YUKTRA_PIPER_MODEL_PATH to your local model path."
        )
    cache_key = _tts_cache_key(txt, piper_bin, piper_model)
    cached = _get_tts_cache(cache_key)
    if cached is not None:
        _tts_log.info("tts_synthesize cache_hit text_chars=%d", len(txt))
        return cached

    _tts_log.info(
        "tts_synthesize begin text_chars=%d model=%s bin=%s",
        len(txt),
        piper_model,
        piper_bin,
    )
    with tempfile.TemporaryDirectory(prefix="yuktra_tts_") as td:
        out_wav = os.path.join(td, "tts.wav")
        cmd = ([piper_python, "-m", "piper"] if use_python_module else [piper_bin]) + [
            "--model",
            piper_model,
            "--output_file",
            out_wav,
            "--speaker",
            str(PIPER_SPEAKER_ID),
            "--length_scale",
            str(PIPER_LENGTH_SCALE),
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None,
            # Windows: don't pop up a console window for the piper child process.
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        try:
            if proc.stdin is not None:
                proc.stdin.write(txt)
                proc.stdin.close()
            while True:
                if should_cancel is not None and should_cancel():
                    _tts_log.info("tts_synthesize cancelled model=%s", piper_model)
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    raise TTSCancelledError("TTS request cancelled by newer request.")
                rc = proc.poll()
                if rc is not None:
                    break
                time.sleep(0.05)
            out_stdout = proc.stdout.read() if proc.stdout is not None else ""
            out_stderr = proc.stderr.read() if proc.stderr is not None else ""
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass

        if proc.returncode != 0:
            err = (out_stderr or out_stdout or "").strip()
            _tts_log.error("tts_synthesize failed rc=%d err=%r", proc.returncode, err[:500])
            raise RuntimeError(f"Piper synthesis failed: {err[:500]}")
        if not os.path.isfile(out_wav):
            raise RuntimeError("Piper synthesis did not produce output wav.")
        with open(out_wav, "rb") as f:
            audio = f.read()
    b64 = base64.b64encode(audio).decode("utf-8")
    _tts_log.info("tts_synthesize done wav_bytes=%d", len(audio))
    out = {"audio_base64": b64, "mime_type": "audio/wav", "engine": "piper"}
    _put_tts_cache(cache_key, out)
    return out
