"""
Defaults and merge logic for per-store runtime settings (config.json).

All RAG / LLM tuning for apps that load a vector store should read these keys from
the merged config returned by load_vector_store() so you only edit config.json.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

# Keys consumed at query/runtime (ingest copies this block into exported config.json).
STORE_RUNTIME_DEFAULTS: Dict[str, Any] = {
    # retrieve_rag_pipeline
    "max_context_chars": 3700,
    "top_k": 2,
    "retrieval_pool_k": 72,
    "mmr_k": 28,
    "mmr_lambda": 0.68,
    "restrict_top_document": True,
    "max_chunks_for_prompt": 0,
    "top_doc_page_window": 2,
    "top_doc_chunk_neighbor_radius": 3,
    "top_doc_chunk_neighbors_before": 0,
    "top_doc_chunk_neighbors_after": 0,
    # When true, after the top-K seed is picked, pull in every other chunk from
    # the same document that shares the seed's section_path_str. Gives the LLM
    # the full section rather than only the chunk that scored highest inside it.
    "top_doc_section_expand": True,
    "bm25_weight": 0.65,
    "rrf_linear_blend": 0.25,
    "hybrid_alpha_semantic": 0.30,
    "hybrid_rerank_enabled": True,
    "retrieval_query_clean_enabled": True,
    "retrieval_corpus_vocab_enabled": True,
    "retrieval_generic_terms": [],
    "retrieval_generic_terms_exclude": [],
    "min_hybrid_rerank_score": 0.60,
    "initial_pool_multiplier": 2,
    # Query embedding (matches ingest embedding_max_length by default)
    "embedding_max_length": 512,
    # LLM generation (llama.cpp). 0 = fill all remaining n_ctx (very slow on CPU; prefer a cap).
    "llm_max_new_tokens": 384,
    "llm_temperature": 0.08,
    "llm_top_p": 0.9,
    "llm_repeat_penalty": 1.1,
    # Smaller n_ctx reduces KV-cache RAM (~linear); raise if you have headroom and very long prompts.
    "llm_n_ctx": int(os.environ.get("YUKTRA_LLM_N_CTX") or 4096),
    "llm_n_batch": int(os.environ.get("YUKTRA_LLM_N_BATCH") or 128),
    "llm_n_ubatch": int(os.environ.get("YUKTRA_LLM_N_UBATCH") or 32),
    # llama-cpp-python LlamaRAMCache: reuse KV for longest shared prompt prefix (static system block).
    # 0 = disabled. Typical: 128–512 (MB). Disabled by default to reduce baseline RAM usage.
    "llm_prompt_cache_mb": int(os.environ.get("YUKTRA_LLM_PROMPT_CACHE_MB") or 0),
    # Parallel LLM: first streamed batch uses this many chunks; later batches use llm_parallel_chunk_batch_size each.
    # llm_parallel_max_workers: 0 = single LLM call (legacy). >0 caps concurrent *extra* Llama instances during streaming.
    # RAM ≈ (1 + min(workers, batches-1)) full model loads for the streaming path; non-stream uses min(workers, batches).
    "llm_parallel_first_batch_chunk_size": 2,
    "llm_parallel_chunk_batch_size": 5,
    "llm_parallel_max_workers": 0,
    # llama.cpp embedding GGUF (Streamlit offline path)
    "emb_llamacpp_n_ctx": 2048,
    "emb_llamacpp_n_batch": 256,
}

_INT_KEYS = frozenset(
    {
        "max_context_chars",
        "top_k",
        "retrieval_pool_k",
        "mmr_k",
        "max_chunks_for_prompt",
        "top_doc_page_window",
        "top_doc_chunk_neighbor_radius",
        "top_doc_chunk_neighbors_before",
        "top_doc_chunk_neighbors_after",
        "initial_pool_multiplier",
        "embedding_max_length",
        "llm_max_new_tokens",
        "llm_n_ctx",
        "llm_n_batch",
        "llm_n_ubatch",
        "llm_prompt_cache_mb",
        "emb_llamacpp_n_ctx",
        "emb_llamacpp_n_batch",
        "llm_parallel_first_batch_chunk_size",
        "llm_parallel_chunk_batch_size",
        "llm_parallel_max_workers",
    }
)
_FLOAT_KEYS = frozenset(
    {
        "mmr_lambda",
        "bm25_weight",
        "rrf_linear_blend",
        "hybrid_alpha_semantic",
        "min_hybrid_rerank_score",
        "llm_temperature",
        "llm_top_p",
        "llm_repeat_penalty",
    }
)
_BOOL_KEYS = frozenset(
    {
        "restrict_top_document",
        "top_doc_section_expand",
        "hybrid_rerank_enabled",
        "retrieval_query_clean_enabled",
        "retrieval_corpus_vocab_enabled",
    }
)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(v)


def _coerce_runtime_fields(cfg: Dict[str, Any]) -> None:
    for k in _INT_KEYS:
        if k not in cfg:
            continue
        try:
            cfg[k] = int(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = int(STORE_RUNTIME_DEFAULTS[k])
    for k in _FLOAT_KEYS:
        if k not in cfg:
            continue
        try:
            cfg[k] = float(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = float(STORE_RUNTIME_DEFAULTS[k])
    for k in _BOOL_KEYS:
        if k not in cfg:
            continue
        cfg[k] = _as_bool(cfg[k])


def merge_store_runtime_config(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Fill missing runtime keys with STORE_RUNTIME_DEFAULTS and coerce types.    
    Preserves all other keys from the store (embedding_model, faiss_*, etc.).
    """
    out: Dict[str, Any] = dict(raw or {})
    for k, default in STORE_RUNTIME_DEFAULTS.items():
        if k not in out or out[k] is None:
            out[k] = default
    _coerce_runtime_fields(out)
    return out


def runtime_defaults_subset() -> Dict[str, Any]:
    """Copy of defaults for writing a new config.json at ingest/export time."""
    return dict(STORE_RUNTIME_DEFAULTS)
