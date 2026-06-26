# doc-qna/backend/model_registry.py
import logging
import os
from typing import Any
from llama_cpp import Llama
from rag_utils import load_llamacpp_embedding_model

_embedding_model = None
_llm_model = None


def _runtime_threads() -> int:
    raw = os.environ.get("YUKTRA_LLM_N_THREADS", "").strip()
    try:
        val = int(raw) if raw else 2
    except ValueError:
        val = 2
    return max(1, val)


def _llama_gpu_available() -> bool:
    try:
        from llama_cpp import llama_supports_gpu_offload  # type: ignore

        return bool(llama_supports_gpu_offload())
    except Exception:
        return False


def _resolve_gpu_layers() -> int:
    """Return n_gpu_layers: auto GPU when available, else CPU.

    Override with YUKTRA_LLM_N_GPU_LAYERS or YUKTRA_GPU_LAYERS.
    """
    for key in ("YUKTRA_LLM_N_GPU_LAYERS", "YUKTRA_GPU_LAYERS"):
        raw = os.environ.get(key, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
    return -1 if _llama_gpu_available() else 0


def get_embedding_model(model_path: str) -> Any:
    global _embedding_model

    if _embedding_model is None:
        _embedding_model = load_llamacpp_embedding_model(
            model_path,
            n_ctx=2048,
            n_threads=_runtime_threads(),
            n_batch=256,
            verbose=False,
        )

    return _embedding_model


def get_llm(model_path: str) -> Any:
    global _llm_model

    if _llm_model is None:
        n_gpu = _resolve_gpu_layers()
        logging.getLogger("yuktra_qna.model_registry").info(
            "llm_load n_gpu_layers=%s",
            n_gpu,
        )
        _llm_model = Llama(
            model_path=model_path,
            n_ctx=8192,
            n_threads=_runtime_threads(),
            n_batch=512,
            n_gpu_layers=n_gpu,
            verbose=False,
        )

    return _llm_model
