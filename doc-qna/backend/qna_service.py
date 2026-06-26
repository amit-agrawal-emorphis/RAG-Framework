# import base64
# import json
# import os
# import re
# import threading
# import time
# from concurrent.futures import ThreadPoolExecutor
# from functools import lru_cache
# from typing import Any, Iterator, Optional

# from llama_cpp import Llama, LlamaRAMCache

# from logger import get_logger, log_process_end, log_process_start
# from prompts import (
#     build_rag_prompt_dynamic,
#     build_rag_prompt_static,
#     ensure_blank_line_before_key_points,
#     normalize_runon_bullet_lines,
# )
# from rag_utils import (
#     _parse_page_number,
#     configure_rag_file_logging,
#     embed_fused_query_for_retrieval,
#     load_llamacpp_embedding_model,
#     load_vector_store,
#     log_llm_generation_duration,
#     pipeline_log_preview,
#     resolve_embedding_prompt_style,
#     retrieve_rag_pipeline,
#     strip_encoded_payload_noise,
#     topk_search,
#     topk_search_faiss,
# )
# from store_runtime_config import merge_store_runtime_config

# REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# DATA_DIR = os.path.join(REPO_ROOT, "data")
# INGESTED_DIR = os.path.join(DATA_DIR, "Ingested")


# def _first_ingested_machine() -> str:
#     """Return the most recently updated folder name in data/Ingested/."""
#     if os.path.isdir(INGESTED_DIR):
#         folders = [
#             f
#             for f in os.listdir(INGESTED_DIR)
#             if os.path.isdir(os.path.join(INGESTED_DIR, f))
#         ]
#         if folders:
#             # Prefer latest upload/activity folder by filesystem mtime.
#             # Tiebreaker on name keeps selection deterministic.
#             folders.sort(
#                 key=lambda f: (os.path.getmtime(os.path.join(INGESTED_DIR, f)), f),
#                 reverse=True,
#             )
#             return folders[0]
#     return ""


# RAG_STORE_TENANT = _first_ingested_machine()
# RAG_STORE_INDEX_NAME = os.environ.get("RAG_STORE_INDEX_NAME", "document_text").strip() or "document_text"
# IMAGE_RAG_STORE_TENANT = RAG_STORE_TENANT
# IMAGE_RAG_STORE_INDEX_NAME = os.environ.get("IMAGE_RAG_STORE_INDEX_NAME", "Img").strip() or "Img"
# EMBEDDING_MODEL_PATH = os.environ.get(
#     "EMBEDDING_MODEL_PATH",
#     os.path.join(DATA_DIR, "models", "embeddinggemma-300M-Q8_0.gguf"),
# )
# LLM_MODEL_PATH = os.environ.get(
#     "LLM_MODEL_PATH",
#     os.path.join(DATA_DIR, "models", "gemma-3-4b-it-Q4_K_M.gguf"),
# )
# OUT_OF_DOMAIN_REPLY = (
#     "Hi! I'm the Equipment Intelligence assistant.\n"
#     "related to equipment/manual-related question?"
# )

# # Between parallel batch answers: avoid "---" / "***" (Markdown renders as <hr> and looks like a second reply).
# _PARALLEL_BATCH_ANSWER_SEPARATOR = "\n\n"

# # Slice streamed LLM text so the UI can refresh progressively (Streamlit fragment polling).
# _SSE_UI_DELTA_MAX_CHARS = 56

# # Whole-line removal: model hedging that contradicts retrieved steps (case-insensitive substring match).
# _RAG_DISCLAIMER_LINE_SUBSTRINGS = (
#     "does not provide information, not specified in the provided context",
#     "the document does not provide information",
#     "not specified in the provided context",
#     "not found in the provided context",
#     "is not mentioned in the provided context",
#     "cannot be determined from the provided context",
#     "this section does not provide details",
#     "does not provide details on",
#     "end of answer",
# )

# _CAPTION_STOPWORDS = {
#     "the",
#     "and",
#     "for",
#     "with",
#     "from",
#     "show",
#     "please",
#     "about",
#     "what",
#     "how",
#     "when",
#     "where",
#     "machine",
#     "manual",
#     "diagram",
#     "image",
#     "images",
#     "picture",
#     "pictures",
#     "figure",
# }

# # Light synonym groups so manual wording (e.g. "sucking") still matches image-seeking queries ("suction").
# _IMAGE_LEXICON_GROUPS: tuple[frozenset[str], ...] = (
#     frozenset({"suction", "suck", "sucking", "vacuum", "vacuo"}),
#     frozenset({"form", "forming", "formed", "unformed", "former"}),
#     frozenset({"carton", "cartoning", "cartons"}),
#     frozenset({"adjust", "adjusting", "adjustment"}),
#     frozenset({"height", "high", "lower", "raise"}),
#     frozenset({"bolt", "bolts", "hexagon", "hex"}),
# )


# def _synonym_closure(tok: str) -> frozenset[str]:
#     t = (tok or "").lower()
#     for g in _IMAGE_LEXICON_GROUPS:
#         if t in g:
#             return g
#     return frozenset({t})


# def _strip_rag_disclaimer_lines(text: str) -> str:
#     out: list[str] = []
#     for ln in (text or "").splitlines():
#         low = ln.lower()
#         if any(n in low for n in _RAG_DISCLAIMER_LINE_SUBSTRINGS):
#             continue
#         out.append(ln)
#     return "\n".join(out).strip()


# _PARALLEL_LATER_NEGATIVE_LINE_RE = re.compile(
#     r"(?i)\b("
#     r"(the\s+)?document\s+does\s+not\s+provide"
#     r"|does\s+not\s+provide\s+(steps|details|information|instructions)"
#     r"|does\s+not\s+mention"
#     r"|not\s+specified\s+in\s+the\s+provided\s+context"
#     r"|not\s+found\s+in\s+the\s+provided\s+context"
#     r"|cannot\s+be\s+determined\s+from\s+the\s+provided\s+context"
#     r")\b"
# )


# def _sanitize_parallel_later_part(text: str) -> str:
#     """
#     For batch 2..N, remove fallback/negative context lines.
#     If nothing substantive remains, return empty so we don't append noise.
#     """
#     kept: list[str] = []
#     for ln in (text or "").splitlines():
#         if _PARALLEL_LATER_NEGATIVE_LINE_RE.search(ln or ""):
#             continue
#         kept.append(ln)
#     return "\n".join(kept).strip()


# def _truncate_previous_answer_draft(text: str, *, max_chars: int = 1400) -> str:
#     t = (text or "").strip()
#     if len(t) <= max_chars:
#         return t
#     # Keep tail to preserve current section/list continuation cues.
#     return t[-max_chars:].strip()


# def _word_overlap_ratio(text_a: str, text_b: str) -> float:
#     """Word-level Jaccard similarity between two texts."""
#     words_a = set((text_a or "").lower().split())
#     words_b = set((text_b or "").lower().split())
#     if not words_a or not words_b:
#         return 0.0
#     return len(words_a & words_b) / len(words_a | words_b)


# def _detect_query_language(question: str) -> str:
#     q = (question or "").strip()
#     if not q:
#         return "English"
#     # If the query contains Devanagari script, treat it as Hindi.
#     if re.search(r"[\u0900-\u097F]", q):
#         return "Hindi"
#     tokens = re.findall(r"[A-Za-z']+", q.lower())
#     if not tokens:
#         return "English"
#     # Expanded Hinglish vocabulary with common words, verbs, and patterns
#     hinglish_tokens = {
#         "kya", "kyun", "kyunki", "kaise", "kahan", "kab", "kabhi", "kaun", "kuch", "agar",
#         "hai", "hain", "hote", "hota", "hoti", "h", "ho", "hoga", "hua",
#         "ka", "ke", "ki", "ko", "se", "mein", "main", "me",
#         "aap", "aapko", "aapka", "aapke", "aapki", "tum", "tumhe", "tumha", "tumhara",
#         "mujhe", "mera", "meri", "mere", "humko", "humara", "hamara",
#         "nahi", "nhi", "na", "mat",
#         "sab", "sabko", "sabhi", "saath",
#         "bahut", "bohot", "bahot", "zyada",
#         "achha", "acha", "theek", "bilkul",
#         "par", "lekin", "aur", "ya",
#         "karo", "kar", "kiya", "kiye", "samjhao", "batao", "batana", "kahna", "kaha",
#         "de", "do", "diya", "dete", "dene", "dena",
#         "raha", "rahi", "rahe", "rah", "reh",
#         "haan", "ha", "haa", "jee",
#         "abhi", "abe", "bhee", "hi",
#         "dekho", "dekha", "dekhen",
#         "liya", "liye", "le", "lena",
#         "chakla", "chalti", "chalta",
#         "chahiye", "chahee", "chahia",
#         "sakta", "sakti", "sakte", "sakenge",
#         "aisa", "aise", "aisi",
#         "tab", "tabhee", "tabhi",
#         "jo", "jaha",
#     }
#     hinglish_count = sum(1 for t in tokens if t in hinglish_tokens)
#     if hinglish_count >= 1:
#         return "Hinglish"
#     return "English"


# # Slightly stricter tokenization for duplicate-detection across batches.
# _PARALLEL_REDUNDANCY_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)


# def _parallel_token_set(text: str) -> set[str]:
#     return {t.lower() for t in _PARALLEL_REDUNDANCY_TOKEN_RE.findall(text or "")}


# def _is_redundant_parallel_part(part_text: str, rolling_ctx: str) -> tuple[bool, float, float]:
#     """
#     Detect whether a later-batch answer is effectively repeating prior answer content.
#     Returns:
#       - redundant: bool
#       - jaccard: token-set Jaccard similarity
#       - coverage: fraction of part tokens already present in rolling context
#     """
#     pt = _parallel_token_set(part_text)
#     rt = _parallel_token_set(rolling_ctx)
#     if not pt or not rt:
#         return False, 0.0, 0.0
#     inter = len(pt & rt)
#     union = len(pt | rt)
#     jaccard = float(inter) / float(max(1, union))
#     coverage = float(inter) / float(max(1, len(pt)))
#     # Suppress when the later part is mostly covered by prior answer tokens,
#     # even if Jaccard is moderate due to longer rolling context.
#     redundant = (coverage >= 0.72) or (jaccard >= _BATCH_REDUNDANCY_OVERLAP_THRESHOLD)
#     return redundant, jaccard, coverage


# # Batch 2+ answers with word-overlap >= this threshold vs rolling context are suppressed as duplicates.
# _BATCH_REDUNDANCY_OVERLAP_THRESHOLD = 0.75

# _NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+)", re.MULTILINE)


# def _filter_novel_list_items(new_text: str, rolling_ctx: str) -> str | None:
#     """
#     For numbered-list responses: strip items already covered by rolling_ctx and
#     renumber the remaining novel items sequentially after the last number in rolling_ctx.

#     Returns:
#       - str  : the filtered (possibly empty) text to emit — caller should emit if non-blank
#       - None : new_text is not a numbered list; caller should fall back to word-overlap check
#     """
#     lines = (new_text or "").strip().splitlines()
#     item_map: list[tuple[int, str]] = []  # (original_number, item_text)
#     header_lines: list[str] = []
#     non_item_count = 0
#     for line in lines:
#         m = re.match(r"^\s*(\d+)\.\s+(.+)", line)
#         if m:
#             item_map.append((int(m.group(1)), m.group(2).strip()))
#         else:
#             if line.strip():
#                 non_item_count += 1
#                 header_lines.append(line.rstrip())

#     # Only treat as a numbered list if there are items and they dominate the content.
#     if not item_map or non_item_count > len(item_map):
#         return None

#     ref_tokens = _parallel_token_set(rolling_ctx)
#     ref_nums = [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.", rolling_ctx)]
#     last_ref_num = max(ref_nums, default=0)

#     novel_items: list[str] = []
#     for _, item_text in item_map:
#         tok = _parallel_token_set(item_text)
#         if not tok:
#             continue
#         # Item is novel if less than 65 % of its tokens are already in the reference context.
#         coverage = len(tok & ref_tokens) / len(tok)
#         if coverage < 0.65:
#             novel_items.append(item_text)

#     if not novel_items:
#         return ""

#     # Renumber sequentially after the last reference number.
#     result_lines = [f"{last_ref_num + 1 + i}. {item}" for i, item in enumerate(novel_items)]
#     # Prepend any non-list header lines (e.g. "According to page 6…") for source attribution.
#     if header_lines:
#         return "\n".join(header_lines) + "\n" + "\n".join(result_lines)
#     return "\n".join(result_lines)


# _thread_llm_local = threading.local()

# def _llm_domain_decision(
#     question: str,
#     llm_model: Llama,
#     config: dict[str, Any],
#     log: Any,
# ) -> bool:
#     """LLM-only domain decision. Returns True if query is equipment/manual related."""
#     q = (question or "").strip()
#     if not q:
#         return False
#     prompt = (
#         "You are a strict relevance classifier for an equipment-manual assistant.\n"
#         "Task: Decide whether the user query is related to laboratory, analytical, "
#         "manufacturing, utility equipment, machine operation, troubleshooting, "
#         "maintenance, components, diagrams, specs, SOPs, or manuals.\n"
#         "User queries may be in English, Hindi (Devanagari), or Romanized Hindi (Hinglish).\n"
#         "Do not treat Romanized Hindi / Hinglish phrasing as a reason to mark a relevant query irrelevant.\n"
#         "Respond with exactly one token:\n"
#         "- RELEVANT\n"
#         "- IRRELEVANT\n\n"
#         "If the query can reasonably be answered from equipment manuals or equipment knowledge, "
#         "return RELEVANT.\n"
#         "If the query is general chit-chat or unrelated domains (weather, movies, sports, finance, etc.), "
#         "return IRRELEVANT.\n\n"
#         f"USER QUERY:\n{q}\n\n"
#         "DECISION:"
#     )
#     max_tok = int(config.get("llm_domain_gate_max_tokens") or 8)
#     try:
#         out = llm_model(
#             prompt,
#             max_tokens=max_tok,
#             temperature=0.0,
#             top_p=1.0,
#             repeat_penalty=1.0,
#             stream=False,
#         )
#         txt = str(((out.get("choices") or [{}])[0].get("text") or "")).strip().upper()
#         decision = "RELEVANT" if "RELEVANT" in txt and "IRRELEVANT" not in txt else ("IRRELEVANT" if "IRRELEVANT" in txt else "")
#         if not decision:
#             # Fail-open to avoid false denials when model output format drifts.
#             log.warning("llm_domain_gate unparseable_output=%r defaulting_to_relevant", txt[:120])
#             return True
#         return decision == "RELEVANT"
#     except Exception as e:
#         # Fail-open on classifier issues so normal RAG path can still answer.
#         log.warning("llm_domain_gate failed err=%s defaulting_to_relevant", e)
#         return True


# def _create_llama_instance(config: dict[str, Any]) -> Llama:
#     llm = Llama(
#         model_path=LLM_MODEL_PATH,
#         n_ctx=int(config["llm_n_ctx"]),
#         n_threads=int(os.environ.get("YUKTRA_LLM_N_THREADS") or os.cpu_count() or 1),
#         n_batch=int(config["llm_n_batch"]),
#         n_ubatch=int(config["llm_n_ubatch"]),
#         verbose=os.environ.get("YUKTRA_LLAMA_CPP_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on"),
#     )
#     cache_mb = int(config.get("llm_prompt_cache_mb") or 0)
#     if cache_mb > 0:
#         llm.set_cache(LlamaRAMCache(capacity_bytes=cache_mb * 1024 * 1024))
#     return llm


# def _worker_llama(config: dict[str, Any]) -> Llama:
#     llm = getattr(_thread_llm_local, "llm", None)
#     if llm is None:
#         _thread_llm_local.llm = _create_llama_instance(config)
#         llm = _thread_llm_local.llm
#     return llm


# def _chunk_batches_head_then_rest(
#     chunks: list[dict[str, Any]],
#     first_size: int,
#     rest_size: int,
# ) -> list[list[dict[str, Any]]]:
#     """
#     First batch uses ``first_size`` chunks; every following batch uses ``rest_size``.
#     If both sizes are <= 0, returns a single batch with all chunks.
#     """
#     if not chunks:
#         return []
#     rows = [dict(c) for c in chunks]
#     if first_size <= 0 and rest_size <= 0:
#         return [rows]
#     fs = int(first_size) if first_size > 0 else int(rest_size)
#     rs = int(rest_size) if rest_size > 0 else int(first_size)
#     fs = max(1, fs)
#     rs = max(1, rs)
#     out: list[list[dict[str, Any]]] = []
#     i = 0
#     take0 = min(fs, len(rows) - i)
#     out.append(rows[i : i + take0])
#     i += take0
#     while i < len(rows):
#         take = min(rs, len(rows) - i)
#         out.append(rows[i : i + take])
#         i += take
#     return out


# def _parallel_batch_settings(config: dict[str, Any]) -> tuple[int, int, int]:
#     first_sz = int(config.get("llm_parallel_first_batch_chunk_size") or 0)
#     rest_sz = int(config.get("llm_parallel_chunk_batch_size") or 0)
#     max_workers = int(config.get("llm_parallel_max_workers") or 0)
#     return first_sz, rest_sz, max_workers


# def _yield_text_delta_events(text: str, width: int = 56) -> Iterator[dict[str, Any]]:
#     t = text or ""
#     for j in range(0, len(t), max(1, width)):
#         yield {"type": "delta", "text": t[j : j + width]}


# def _parallel_worker_generate_batch(
#     question: str,
#     batch_chunks: list[dict[str, Any]],
#     config: dict[str, Any],
#     batch_index: int,
#     batch_total: int,
#     log: Any,
# ) -> str:
#     t_w0 = time.perf_counter()
#     th_name = threading.current_thread().name
#     log.info(
#         "llm_parallel_worker phase=start batch=%d/%d chunk_count=%d thread=%r",
#         batch_index,
#         batch_total,
#         len(batch_chunks),
#         th_name,
#     )
#     llm = _worker_llama(config)
#     out = _generate_one_batch_nostream(
#         question,
#         batch_chunks,
#         config,
#         batch_index,
#         batch_total,
#         log,
#         llm_model=llm,
#     )
#     if int(batch_index) > 1:
#         out = _sanitize_parallel_later_part(out)
#     log.info(
#         "llm_parallel_worker phase=done batch=%d/%d answer_chars=%d duration_sec=%.4f thread=%r",
#         batch_index,
#         batch_total,
#         len(out),
#         time.perf_counter() - t_w0,
#         th_name,
#     )
#     return out


# def _generate_one_batch_nostream(
#     question: str,
#     batch_chunks: list[dict[str, Any]],
#     config: dict[str, Any],
#     batch_index: int,
#     batch_total: int,
#     log: Any,
#     *,
#     llm_model: Llama,
# ) -> str:
#     """One non-streaming completion for a chunk batch (may run in a worker thread)."""
#     st, dyn, _ = _build_prompt_fitting_llm_ctx(
#         question,
#         batch_chunks,
#         llm_model,
#         n_ctx=int(config["llm_n_ctx"]),
#         llm_max_new_tokens=int(config["llm_max_new_tokens"]),
#         log=log,
#         batch_index=batch_index,
#         batch_count=batch_total,
#     )
#     prompt = st + dyn
#     max_tok = int(config["llm_max_new_tokens"])

#     def _complete(p: str) -> Any:
#         return llm_model(
#             p,
#             max_tokens=None if max_tok <= 0 else max_tok,
#             temperature=float(config["llm_temperature"]),
#             top_p=float(config["llm_top_p"]),
#             repeat_penalty=float(config["llm_repeat_penalty"]),
#         )

#     try:
#         result = _complete(prompt)
#     except ValueError as e:
#         err = str(e).lower()
#         if "exceed" not in err or "context" not in err:
#             raise
#         log.warning(
#             "rag_llm_ctx_retry_parallel_batch batch=%d/%d err=%s",
#             batch_index,
#             batch_total,
#             str(e).replace("\n", " ")[:200],
#         )
#         prompt = _repair_prompt_on_context_overflow(question, llm_model, config, log)
#         try:
#             result = _complete(prompt)
#         except ValueError:
#             prompt = _repair_prompt_brutal(llm_model, prompt, config, log)
#             result = _complete(prompt)
#     return (result["choices"][0]["text"] or "").strip()


# def _store_dir() -> str:
#     return os.path.join(INGESTED_DIR, RAG_STORE_TENANT, RAG_STORE_INDEX_NAME)


# def _image_store_dir() -> str:
#     return os.path.join(INGESTED_DIR, IMAGE_RAG_STORE_TENANT, IMAGE_RAG_STORE_INDEX_NAME)


# def warmup_models() -> None:
#     """Load vector store, embedding model, and LLM once (same work as the first real question)."""
#     _load_runtime()


# @lru_cache(maxsize=1)
# def _load_runtime() -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any], Any, Any]:
#     store_dir = _store_dir()
#     vectors, faiss_index, metadata, config = load_vector_store(store_dir)
#     cfg = merge_store_runtime_config(dict(config))
#     emb_model = load_llamacpp_embedding_model(
#         EMBEDDING_MODEL_PATH,
#         n_ctx=int(cfg["emb_llamacpp_n_ctx"]),
#         n_threads=int(os.environ.get("YUKTRA_LLM_N_THREADS") or os.cpu_count() or 1),
#         n_batch=int(cfg["emb_llamacpp_n_batch"]),
#         verbose=False,
#     )
#     llm = _create_llama_instance(cfg)
#     return vectors, faiss_index, metadata, cfg, emb_model, llm


# def _format_gemma_ground_truth_judge_prompt(question: str, expected: str, model_output: str) -> str:
#     return (
#         "<start_of_turn>system\n"
#         "You grade how well MODEL_OUTPUT matches EXPECTED (document ground truth) for the QUESTION. "
#         "Output ONLY a single JSON object, no markdown, no other text. "
#         'Format: {"score": <integer 1-5>, "explanation": "<one short sentence>"}\n'
#         "Score: 1=completely wrong; 2=mostly wrong; 3=partially correct; 4=mostly correct; 5=fully correct in meaning.\n"
#         "<end_of_turn>\n"
#         "<start_of_turn>user\n"
#         f"QUESTION:\n{question}\n\nEXPECTED:\n{expected}\n\nMODEL_OUTPUT:\n{model_output}\n"
#         "<end_of_turn>\n"
#         "<start_of_turn>model\n"
#     )


# def _strip_code_fences_for_judge(s: str) -> str:
#     t = (s or "").strip()
#     if t.startswith("```"):
#         t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
#         t = re.sub(r"\s*```$", "", t, flags=re.DOTALL)
#     return t.strip()


# def judge_ground_truth_gemma(question: str, expected: str, model_output: str) -> dict[str, Any]:
#     """
#     LLM-as-judge using the same Gemma GGUF as RAG (llama.cpp).
#     Returns {"score": int, "explanation": str|None, "error": str|None}.
#     """
#     ref = (expected or "").strip()[:12000]
#     out = (model_output or "").strip()[:8001]
#     q = (question or "").strip()
#     if not ref or not out:
#         return {"score": None, "explanation": None, "error": "empty expected or model output"}
#     _vs, _fi, _meta, _cfg, _emb, llm = _load_runtime()
#     prompt = _format_gemma_ground_truth_judge_prompt(q, ref, out)
#     max_tok = int(os.environ.get("YUKTRA_GEMMA_JUDGE_MAX_TOKENS", "512"))
#     try:
#         gen = llm(
#             prompt,
#             max_tokens=max_tok,
#             temperature=0.0,
#             top_p=1.0,
#             stream=False,
#         )
#         text = str(((gen.get("choices") or [{}])[0].get("text") or "")).strip()
#     except Exception as e:  # noqa: BLE001
#         return {"score": None, "explanation": None, "error": str(e)}
#     text = _strip_code_fences_for_judge(text)
#     parsed: dict[str, Any] | None = None
#     for blob in (
#         text,
#         text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else "",
#     ):
#         b = (blob or "").strip()
#         if not b.startswith("{"):
#             continue
#         try:
#             p = json.loads(b)
#         except json.JSONDecodeError:
#             continue
#         if isinstance(p, dict):
#             parsed = p
#             break
#     if not isinstance(parsed, dict):
#         return {
#             "score": None,
#             "explanation": None,
#             "error": f"unparseable judge JSON. Raw: {text[:400]!r}",
#         }
#     sc_raw = parsed.get("score", 0)
#     try:
#         sc = int(float(sc_raw)) if sc_raw is not None and sc_raw != "" else 0
#     except (TypeError, ValueError):
#         return {"score": None, "explanation": None, "error": f"invalid score: {parsed!r}"}
#     sc = max(1, min(5, sc))
#     exp = str(parsed.get("explanation", "") or "").strip()
#     return {"score": sc, "explanation": exp or None, "error": None}


# @lru_cache(maxsize=1)
# def _load_image_runtime() -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any]] | None:
#     store_dir = _image_store_dir()
#     if not os.path.isdir(store_dir):
#         return None
#     try:
#         vectors, faiss_index, metadata, config = load_vector_store(store_dir)
#     except Exception:
#         return None
#     return vectors, faiss_index, metadata, dict(config)


# def get_image_blob_by_uuid(image_uuid: str) -> tuple[bytes, str] | None:
#     rt = _load_image_runtime()
#     if not rt:
#         return None
#     _img_vectors, _img_faiss, img_meta, _img_cfg = rt
#     uid = str(image_uuid or "").strip()
#     if not uid:
#         return None
#     for row in img_meta:
#         if str(row.get("image_uuid") or "").strip() != uid:
#             continue
#         b64 = str(row.get("image_base64") or "").strip()
#         if not b64:
#             return None
#         try:
#             raw = base64.b64decode(b64, validate=True)
#         except Exception:
#             return None
#         mime = str(row.get("image_mime") or "image/png").strip() or "image/png"
#         return raw, mime
#     return None


# def _retrieve_caption_images(
#     *,
#     question: str,
#     retrieved_chunks: list[dict[str, Any]],
#     emb_model: Any,
#     text_cfg: dict[str, Any],
#     app_logger: Any,
# ) -> list[dict[str, Any]]:
#     t_img0 = time.perf_counter()
#     image_rt = _load_image_runtime()
#     if not image_rt:
#         app_logger.info("qna_images_retrieval phase=load_runtime duration_sec=%.4f runtime=missing", time.perf_counter() - t_img0)
#         return []
#     img_vectors, img_faiss, img_meta, img_cfg = image_rt
#     if not img_meta:
#         app_logger.info("qna_images_retrieval phase=load_runtime duration_sec=%.4f runtime=empty_meta", time.perf_counter() - t_img0)
#         return []
#     app_logger.info(
#         "qna_images_retrieval phase=load_runtime duration_sec=%.4f meta_rows=%d backend=%s",
#         time.perf_counter() - t_img0,
#         len(img_meta),
#         "faiss" if img_faiss is not None else "numpy",
#     )

#     t_q0 = time.perf_counter()
#     caption_query_parts: list[str] = [question.strip()]
#     for ch in retrieved_chunks[:6]:
#         tx = strip_encoded_payload_noise(str(ch.get("text") or "")).strip()
#         if tx:
#             caption_query_parts.append(tx[:600])
#     caption_query = "\n".join([x for x in caption_query_parts if x]).strip()
#     if not caption_query:
#         app_logger.info(
#             "qna_images_retrieval phase=build_query duration_sec=%.4f query=empty",
#             time.perf_counter() - t_q0,
#         )
#         return []
#     app_logger.info(
#         "qna_images_retrieval phase=build_query duration_sec=%.4f query_chars=%d question_chars=%d retrieved_chunks=%d",
#         time.perf_counter() - t_q0,
#         len(caption_query),
#         len(question or ""),
#         len(retrieved_chunks),
#     )

#     t_emb0 = time.perf_counter()
#     emb_style = resolve_embedding_prompt_style(
#         img_cfg,
#         str(img_cfg.get("embedding_model") or text_cfg.get("embedding_model") or ""),
#     )
#     qvec = embed_fused_query_for_retrieval(
#         caption_query,
#         None,
#         emb_model,
#         device="cpu",
#         max_length=int(img_cfg.get("embedding_max_length") or text_cfg["embedding_max_length"]),
#         embedding_prompt_style=emb_style,
#     )
#     app_logger.info(
#         "qna_images_retrieval phase=query_embed duration_sec=%.4f emb_style=%s",
#         time.perf_counter() - t_emb0,
#         emb_style,
#     )
#     t_search0 = time.perf_counter()
#     k = max(1, int(os.environ.get("IMAGE_RETRIEVAL_TOP_K", "8")))
#     pool = min(len(img_meta), k * 3)
#     if img_faiss is not None:
#         idx, _scores = topk_search_faiss(img_faiss, qvec, pool)
#     else:
#         idx, _scores = topk_search(img_vectors, qvec, pool)
#     app_logger.info(
#         "qna_images_retrieval phase=vector_search duration_sec=%.4f top_k=%d pool=%d returned=%d",
#         time.perf_counter() - t_search0,
#         k,
#         pool,
#         len(idx),
#     )

#     t_filter0 = time.perf_counter()
#     out: list[dict[str, Any]] = []
#     scored: list[tuple[float, dict[str, Any]]] = []
#     fallback_scored: list[tuple[float, float, dict[str, Any]]] = []
#     seen: set[str] = set()
#     min_match = float(os.environ.get("IMAGE_CAPTION_MATCH_MIN", "0.6"))
#     # Default higher so multi-figure sections can return more than one relevant image.
#     max_out = int(os.environ.get("IMAGE_RESPONSE_MAX", "4"))
#     ql = re.sub(r"\s+", " ", (question or "").lower()).strip()
#     explicit_image_intent = any(
#         t in ql for t in ("show image", "give image", "display image", "image of", "picture of", "figure of")
#     )
#     strict_min_match = float(os.environ.get("IMAGE_CAPTION_MATCH_MIN_STRICT", "0.75"))
#     top_doc = ""
#     if retrieved_chunks:
#         top_doc = str(retrieved_chunks[0].get("doc_name") or "").strip()
#     same_doc_chunks = [
#         ch for ch in retrieved_chunks if str(ch.get("doc_name") or "").strip() == top_doc
#     ] if top_doc else list(retrieved_chunks)
#     chunk_ctx = "\n".join(str(ch.get("text") or "")[:800] for ch in same_doc_chunks[:4]).strip()
#     qk = _query_keywords(question)
#     scanned = 0
#     dropped_no_overlap = 0
#     dropped_doc_mismatch = 0
#     dropped_low_score = 0
#     dropped_strict = 0
#     for pos, raw_i in enumerate(idx):
#         scanned += 1
#         row = img_meta[int(raw_i)]
#         uid = str(row.get("image_uuid") or "").strip()
#         b64 = str(row.get("image_base64") or "").strip()
#         caption = str(row.get("caption") or "").strip()
#         img_doc = str(row.get("doc_name") or "").strip()
#         if not uid or not b64 or uid in seen:
#             continue
#         # Keep image retrieval anchored to the same document as text retrieval.
#         if top_doc and img_doc and img_doc != top_doc:
#             dropped_doc_mismatch += 1
#             continue
#         candidate_text = " ".join(
#             x for x in (caption, str(row.get("text") or "").strip()) if x
#         ).strip()
#         if not candidate_text:
#             continue
#         # Primary lexical relevance should track what the user asked, with chunk-context
#         # support as a secondary signal to ensure images are tied to retrieved text.
#         score_q = _caption_match_score(candidate_text, question)
#         score_ctx = _caption_match_score(candidate_text, caption_query)
#         score_chunk = _caption_match_score(candidate_text, chunk_ctx) if chunk_ctx else 0.0
#         score = max(score_q, score_ctx)
#         # Guardrail: if the user has concrete keywords, require a minimum synonym-aware overlap.
#         if qk:
#             ck = _query_keywords(candidate_text)
#             overlap = _synonym_overlap_count(qk, ck)
#             # Be less aggressive here; requiring 2+ overlaps often suppresses valid sibling
#             # images from the same section whose captions use slightly different phrasing.
#             need = 1
#             if overlap < need:
#                 dropped_no_overlap += 1
#                 continue
#             overlap_ratio = float(overlap) / float(max(1, len(qk)))
#         else:
#             overlap_ratio = 0.0
#         if explicit_image_intent and qk:
#             # For explicit image asks, require high lexical fidelity to avoid "nearest but wrong" photos.
#             if overlap_ratio < 0.75 or score_q < strict_min_match:
#                 # Controlled fallback: if strict mode finds nothing, allow one best candidate
#                 # that still has meaningful overlap and semantic proximity.
#                 sem = 0.0
#                 try:
#                     sem = float(_scores[pos])
#                 except Exception:
#                     sem = 0.0
#                 if overlap_ratio >= 0.45 and max(score_q, score_chunk, score_ctx) >= min_match:
#                     fallback_scored.append(
#                         (
#                             max(score_q, score_ctx, score_chunk),
#                             sem,
#                             {
#                                 "image_uuid": uid,
#                                 "caption": caption,
#                                 "image_mime": str(row.get("image_mime") or "image/png"),
#                                 "doc_name": str(row.get("doc_name") or ""),
#                             },
#                         )
#                     )
#                 else:
#                     dropped_strict += 1
#                 continue
#         if score < min_match or max(score_q, score_chunk) < 0.45:
#             dropped_low_score += 1
#             continue
#         seen.add(uid)
#         scored.append(
#             (
#                 score,
#                 {
#                     "image_uuid": uid,
#                     "caption": caption,
#                     "image_mime": str(row.get("image_mime") or "image/png"),
#                     "doc_name": str(row.get("doc_name") or ""),
#                 },
#             )
#         )
#     scored.sort(key=lambda x: x[0], reverse=True)
#     if explicit_image_intent and not scored and fallback_scored:
#         # Keep strict behavior by default, but return top fallback candidates (not only one)
#         # so multi-image answers from the same section can still surface together.
#         fallback_scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
#         take_fb = max(1, min(max_out, k))
#         for fb in fallback_scored[:take_fb]:
#             scored.append((fb[0], fb[2]))
#     app_logger.info(
#         "qna_images_retrieval phase=filter_rank duration_sec=%.4f scanned=%d kept=%d fallback_candidates=%d dropped_doc=%d dropped_overlap=%d dropped_score=%d dropped_strict=%d explicit_intent=%s",
#         time.perf_counter() - t_filter0,
#         scanned,
#         len(scored),
#         len(fallback_scored),
#         dropped_doc_mismatch,
#         dropped_no_overlap,
#         dropped_low_score,
#         dropped_strict,
#         explicit_image_intent,
#     )
#     for _, img in scored[: max(1, min(max_out, k))]:
#         out.append(img)
#     app_logger.info(
#         "qna_images_retrieval image_tenant=%s image_index=%s query_chars=%d returned=%d min_match=%.2f max_out=%d total_duration_sec=%.4f",
#         IMAGE_RAG_STORE_TENANT,
#         IMAGE_RAG_STORE_INDEX_NAME,
#         len(caption_query),
#         len(out),
#         min_match,
#         max_out,
#         time.perf_counter() - t_img0,
#     )
#     return out


# def _query_keywords(query: str) -> set[str]:
#     toks = re.findall(r"[a-z0-9]+", (query or "").lower())
#     out: set[str] = set()
#     for t in toks:
#         if len(t) < 3:
#             continue
#         if t in _CAPTION_STOPWORDS:
#             continue
#         out.add(t)
#     return out


# def _tokens_synonym_match(a: str, b: str) -> bool:
#     return bool(_synonym_closure(a) & _synonym_closure(b))


# def _synonym_overlap_count(a_tokens: set[str], b_tokens: set[str]) -> int:
#     if not a_tokens or not b_tokens:
#         return 0
#     return sum(1 for a in a_tokens if any(_tokens_synonym_match(a, b) for b in b_tokens))


# def _caption_match_score(caption: str, query_text: str) -> float:
#     """Lexical gate for image candidates: use full retrieval query (question + chunks), symmetric
#     coverage, and synonym groups so strict 0.6 thresholds still allow strong partial matches."""
#     c = (caption or "").strip().lower()
#     if not c:
#         return 0.0
#     if c in ("image", "img", "picture", "figure"):
#         return 0.0
#     qk = _query_keywords(query_text)
#     if not qk:
#         return 1.0
#     ck = _query_keywords(c)
#     if not ck:
#         ctoks = set(re.findall(r"[a-z0-9]+", c))
#         if not ctoks:
#             return 0.0
#         ck = {t for t in ctoks if len(t) >= 3 and t not in _CAPTION_STOPWORDS}

#     matched_q = sum(
#         1 for q in qk if any(_tokens_synonym_match(q, cap_t) for cap_t in ck)
#     )
#     matched_c = sum(
#         1 for cap_t in ck if any(_tokens_synonym_match(cap_t, q) for q in qk)
#     )
#     if matched_q or matched_c:
#         return max(
#             float(matched_q) / float(len(qk)),
#             float(matched_c) / float(len(ck)),
#         )
#     qnorm = re.sub(r"\s+", " ", (query_text or "").strip().lower())
#     return 1.0 if len(qnorm) >= 6 and qnorm in c else 0.0


# def _is_explicit_image_intent_query(query: str) -> bool:
#     ql = re.sub(r"\s+", " ", (query or "").lower()).strip()
#     if not ql:
#         return False
#     return any(
#         t in ql
#         for t in (
#             "show image",
#             "give image",
#             "display image",
#             "image of",
#             "picture of",
#             "figure of",
#             "show diagram",
#             "display diagram",
#             "show figure",
#             "display figure",
#         )
#     )


# def _contains_out_of_domain_boilerplate(text: str) -> bool:
#     t = re.sub(r"\s+", " ", str(text or "").strip().lower())
#     return "i'm an equipment intelligence assistant" in t or "i am an equipment intelligence assistant" in t


# def _fallback_answer_from_retrieved_chunks(
#     question: str,
#     retrieved: list[dict[str, Any]],
# ) -> str:
#     if not retrieved:
#         return (
#             "I found this topic in the manual, but I could not extract a clean answer. "
#             "Please rephrase the question or ask for a specific step/section."
#         )
#     first = retrieved[0]
#     doc = str(first.get("doc_name") or "the manual").strip()
#     page_num = _source_page_number(first)
#     page = str(page_num) if page_num is not None else ""
#     if page in ("", "?", "None"):
#         page = ""
#     sec = " ".join(str(first.get("section_path_str", "") or "").split())
#     if sec in ("", "None"):
#         sec = ""
#     tx = str(first.get("text") or "").strip()
#     tx = re.sub(r"\s+", " ", tx)
#     if not tx:
#         return f"I found relevant content in {doc}, but the extracted text is limited. Please ask a more specific question."
#     snippet = tx[:420].rstrip()
#     if len(tx) > 420:
#         snippet += "..."
#     ref_parts: list[str] = []
#     if page:
#         ref_parts.append(f"page {page}")
#     if sec:
#         ref_parts.append(f"section {sec}")
#     ref = ", ".join(ref_parts)
#     return f"From {doc} ({ref}): {snippet}" if ref else f"From {doc}: {snippet}"


# def _extract_exact_table_spec_answer(
#     question: str,
#     chunks: list[dict[str, Any]],
# ) -> str | None:
#     q_terms = {
#         t
#         for t in re.findall(r"[a-z0-9]+", (question or "").lower())
#         if len(t) >= 3 and t not in {
#             "what", "are", "the", "for", "and", "with", "from", "supported", "range", "ranges"
#         }
#     }
#     if not q_terms:
#         return None

#     best: tuple[float, dict[str, Any], str, str] | None = None
#     for ch in chunks:
#         text = strip_encoded_payload_noise(str(ch.get("text") or ""))
#         if "|" not in text:
#             continue
#         for raw_line in text.splitlines():
#             line = raw_line.strip()
#             if not (line.startswith("|") and line.endswith("|")):
#                 continue
#             if re.fullmatch(r"\|[\s:\-\|]+\|", line):
#                 continue
#             cells = [re.sub(r"\s+", " ", c).strip() for c in line.strip("|").split("|")]
#             cells = [c for c in cells if c]
#             if len(cells) < 2:
#                 continue
#             value_idx = -1
#             for i in range(len(cells) - 1, -1, -1):
#                 if re.search(r"\d", cells[i]) and re.search(r"(?i)\bmm\b|×|x|kw|v|hz|mpa|l/min|boxes/min|g/m", cells[i]):
#                     value_idx = i
#                     break
#             if value_idx <= 0:
#                 continue
#             label = " ".join(cells[:value_idx]).strip()
#             value = cells[value_idx].strip()
#             label_terms = set(re.findall(r"[a-z0-9]+", label.lower()))
#             hits = len(q_terms & label_terms)
#             if hits < 2:
#                 continue
#             score = hits / max(1, len(q_terms))
#             if best is None or score > best[0]:
#                 best = (score, ch, label, value)

#     if best is None:
#         return None

#     _score, ch, label, value = best
#     doc = str(ch.get("doc_name") or "the manual").strip()
#     page_num = _source_page_number(ch)
#     page = f", page {page_num}" if page_num is not None else ""
#     sec = " ".join(str(ch.get("section_path_str", "") or "").split())
#     sec_txt = f", section {sec}" if sec else ""
#     clean_label = label
#     # Docling tables often duplicate the item label across two columns; collapse that.
#     parts = clean_label.split()
#     half = len(parts) // 2
#     if half > 0 and len(parts) % 2 == 0 and parts[:half] == parts[half:]:
#         clean_label = " ".join(parts[:half])
#     return f"According to {doc}{page}{sec_txt}, {clean_label} is {value}."


# def _source_page_number(ch: dict[str, Any]) -> int | None:
#     """Best-effort page extraction across legacy/new metadata keys."""
#     for key in ("page_number", "page", "page_no", "page_num", "source_page"):
#         pn = _parse_page_number(ch.get(key))
#         if pn is not None:
#             return pn
#     return None


# def _chunk_log_line(ch: dict[str, Any]) -> str:
#     doc = str(ch.get("doc_name", "") or "")
#     vec_id = ch.get("vector_id", "?")
#     chunk_idx = ch.get("chunk_index", "?")
#     page = ch.get("page_number", "?")
#     sec = " ".join(str(ch.get("section_path_str", "") or "").split())
#     if len(sec) > 80:
#         sec = sec[:77] + "..."
#     txt = " ".join(str(ch.get("text", "") or "").split())
#     # Make logs more useful by showing more of each chunk's beginning.
#     head_chars = 120
#     tail_chars = 40
#     if len(txt) > head_chars + tail_chars + 10:
#         txt = txt[:head_chars] + " ... " + txt[-tail_chars:]
#     return (
#         f"doc={doc} page={page} chunk={chunk_idx} vec={vec_id}"
#         + (f" section={sec}" if sec else "")
#         + f' text="{txt}"'
#     )


# def _batch_progress_message(batch_no: int, batch_total: int, batch_chunks: list[dict[str, Any]]) -> str:
#     pages: list[str] = []
#     seen_pages: set[str] = set()
#     for ch in batch_chunks:
#         p = str(ch.get("page_number") or "").strip()
#         if not p or p in ("?", "None") or p in seen_pages:
#             continue
#         seen_pages.add(p)
#         pages.append(p)
#         if len(pages) >= 4:
#             break
#     sec = ""
#     for ch in batch_chunks:
#         raw = " ".join(str(ch.get("section_path_str", "") or "").split())
#         if raw:
#             sec = raw
#             break
#     parts = ["Analyzing relevant manual sections"]
#     if pages:
#         parts.append(f"pages {', '.join(pages)}")
#     if sec:
#         if len(sec) > 70:
#             sec = sec[:67] + "..."
#         parts.append(f"section {sec}")
#     return " | ".join(parts)


# def _log_retrieved_chunks(app_logger: Any, label: str, chunks: list[dict[str, Any]], *, max_items: int = 16) -> None:
#     app_logger.info("[CHUNK_LOG] %s count=%d", label, len(chunks))
#     cap = min(len(chunks), max(0, int(max_items)))
#     for i, ch in enumerate(chunks[:cap], start=1):
#         app_logger.info("[CHUNK_LOG] %s #%d %s", label, i, _chunk_log_line(ch))
#     if len(chunks) > cap:
#         app_logger.info("[CHUNK_LOG] %s ... %d more omitted (max_items=%d)", label, len(chunks) - cap, max_items)


# def _resolve_doc_path(doc_name: str | None, stored_path: str | None, metadata: list[dict[str, Any]]) -> str | None:
#     def _search_ingested(filename: str) -> str | None:
#         if not filename or not os.path.isdir(INGESTED_DIR):
#             return None
#         for machine in sorted(os.listdir(INGESTED_DIR)):
#             candidate = os.path.join(INGESTED_DIR, machine, "documents", filename)
#             if os.path.isfile(candidate):
#                 return candidate
#         return None

#     name = (doc_name or "").strip()
#     if name:
#         found = _search_ingested(name)
#         if found:
#             return found
#         found = _search_ingested(os.path.basename(name))
#         if found:
#             return found
#     if stored_path:
#         sp = str(stored_path).strip()
#         if sp and os.path.isfile(sp):
#             return sp
#     if not name and stored_path:
#         for row in metadata:
#             if row.get("doc_path") == stored_path and row.get("doc_name"):
#                 name = str(row.get("doc_name")).strip()
#                 break
#         if name:
#             found = _search_ingested(name)
#             if found:
#                 return found
#             found = _search_ingested(os.path.basename(name))
#             if found:
#                 return found
#     return None


# def _format_answer_for_display(answer: str) -> str:
#     text = (answer or "").strip()
#     text = _strip_rag_disclaimer_lines(text)
#     # Remove markdown horizontal-rule lines that visually split one answer bubble.
#     text = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", text)
#     text = normalize_runon_bullet_lines(text)
#     text = ensure_blank_line_before_key_points(text)
#     text = re.sub(r"(?i)the correct answer.*?:", "", text)
#     lines_out: list[str] = []
#     for line in text.splitlines():
#         m = re.match(r"^(\s*)- (\S.*)$", line)
#         if m:
#             lines_out.append(f"{m.group(1)}\u2022 {m.group(2)}")
#         else:
#             lines_out.append(line.replace("●", "\u2022"))
#     # Remove boilerplate denial line when mixed into an otherwise relevant answer.
#     # Keep it only when the whole response is the denial itself.
#     denial_lines = {ln.strip().lower() for ln in OUT_OF_DOMAIN_REPLY.splitlines() if ln.strip()}
#     non_empty = [ln for ln in lines_out if ln.strip()]
#     if len(non_empty) > 1:
#         filtered: list[str] = []
#         for ln in lines_out:
#             if ln.strip() and ln.strip().lower() in denial_lines:
#                 continue
#             filtered.append(ln)
#         lines_out = filtered
#     text = "\n".join(lines_out)
#     if "\n" not in text and "?" in text:
#         qpos = text.rfind("?")
#         if qpos != -1 and (len(text) - qpos) <= 180:
#             boundary = max(text.rfind("\n\n", 0, qpos), text.rfind("\n", 0, qpos))
#             if boundary == -1:
#                 boundary = max(text.rfind(". ", 0, qpos), text.rfind("! ", 0, qpos))
#             if boundary == -1:
#                 boundary = max(text.rfind(".\n", 0, qpos), text.rfind("!\n", 0, qpos))
#             start = boundary + 1 if boundary != -1 else 0
#             if start > 0 and start < qpos and (qpos - start) <= 260:
#                 main = text[:start].rstrip()
#                 qsent = text[start:].lstrip()
#                 text = f"{main}\n\n{qsent}".strip()
#     if "\n" not in text and len(text) >= 320:
#         text = re.sub(r"(?i)\s+(adjustment to the\s+\w+\s+manipulator\s*:)", r"\n\n\1", text)
#         text = re.sub(r"(?i)\s+(adjustment to automatic tube feeder\s*:)", r"\n\n\1", text)
#         text = re.sub(r"(?m)(^|[:;])\s*(\d+\))\s+", r"\1\n\2 ", text)
#         text = re.sub(r"(?m)(^|[:;])\s*(\d{1,2}\.)\s+(?!\d)", r"\1\n\2 ", text)
#         text = re.sub(r"(?im)(^|[:;])\s*([a-h]\.)\s+", r"\1\n  \2 ", text)
#         text = re.sub(r"\n{3,}", "\n\n", text).strip()
#     text = re.sub(r"(?<!\n)\n(?!\n)", "  \n", text)
#     return re.sub(r"\n{3,}", "\n\n", text).strip()


# _MIN_RAG_CHUNK_CHARS = 200


# def _llm_prompt_token_count(llm_model: Any, prompt: str) -> int:
#     return len(llm_model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True))


# def _truncate_prompt_to_max_tokens(llm_model: Any, prompt: str, max_tokens: int, log: Any) -> str:
#     max_tokens = max(64, int(max_tokens))
#     toks = llm_model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
#     if len(toks) <= max_tokens:
#         return prompt
#     log.warning(
#         "rag_prompt_token_truncated tokens=%d->%d (llama context cap)",
#         len(toks),
#         max_tokens,
#     )
#     cut = toks[:max_tokens]
#     raw = llm_model.detokenize(cut, special=True)
#     return raw.decode("utf-8", errors="replace")


# def _truncate_prompt_pair_to_max_tokens(
#     llm_model: Any,
#     static_s: str,
#     dynamic_s: str,
#     max_tokens: int,
#     log: Any,
# ) -> tuple[str, str]:
#     max_tokens = max(64, int(max_tokens))
#     full = static_s + dynamic_s
#     toks = llm_model.tokenize(full.encode("utf-8"), add_bos=False, special=True)
#     if len(toks) <= max_tokens:
#         return static_s, dynamic_s
#     static_toks = llm_model.tokenize(static_s.encode("utf-8"), add_bos=False, special=True)
#     n_static = len(static_toks)
#     if n_static >= max_tokens:
#         log.warning(
#             "rag_prompt_token_truncated static_prefix_tokens=%d->%d (falling back to whole-prompt trim)",
#             n_static,
#             max_tokens,
#         )
#         truncated = _truncate_prompt_to_max_tokens(llm_model, full, max_tokens, log)
#         return "", truncated
#     lo, hi = 0, len(dynamic_s)
#     best = ""
#     while lo <= hi:
#         mid = (lo + hi) // 2
#         cand = static_s + dynamic_s[:mid]
#         n = len(llm_model.tokenize(cand.encode("utf-8"), add_bos=False, special=True))
#         if n <= max_tokens:
#             best = dynamic_s[:mid]
#             lo = mid + 1
#         else:
#             hi = mid - 1
#     log.warning(
#         "rag_prompt_dynamic_truncated chars=%d->%d (llama context cap, static prefix preserved)",
#         len(dynamic_s),
#         len(best),
#     )
#     return static_s, best


# def _uniform_scale_chunk_texts(capped: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
#     out: list[dict[str, Any]] = []
#     for c in capped:
#         nc = dict(c)
#         t = str(nc.get("text", "") or "")
#         if not t.strip():
#             out.append(nc)
#             continue
#         nlen = max(_MIN_RAG_CHUNK_CHARS, int(len(t) * factor))
#         nc["text"] = t[:nlen]
#         out.append(nc)
#     return out


# def _build_prompt_fitting_llm_ctx(
#     question: str,
#     capped: list[dict[str, Any]],
#     llm_model: Any,
#     *,
#     n_ctx: int,
#     llm_max_new_tokens: int,
#     log: Any,
#     batch_index: Optional[int] = None,
#     batch_count: Optional[int] = None,
#     previous_answer_draft: Optional[str] = None,
# ) -> tuple[str, str, list[dict[str, Any]]]:
#     capped = [dict(c) for c in capped]
#     n_ctx_i = max(512, int(n_ctx))
#     mnt = int(llm_max_new_tokens)
#     gen_floor = 512 if mnt <= 0 else min(mnt + 128, n_ctx_i // 2)
#     slack = 256
#     hard_cap = max(128, n_ctx_i - gen_floor - slack)
#     query_language = _detect_query_language(question)
#     static = build_rag_prompt_static()

#     def _dyn(q: str, chunks: list[dict[str, Any]]) -> str:
#         return build_rag_prompt_dynamic(
#             q,
#             chunks,
#             batch_index=batch_index,
#             batch_count=batch_count,
#             previous_answer_draft=previous_answer_draft,
#             query_language=query_language,
#         )

#     def fits(full: str) -> bool:
#         return _llm_prompt_token_count(llm_model, full) <= hard_cap

#     start_n = len(capped)
#     while capped:
#         dynamic = _dyn(question, capped)
#         if fits(static + dynamic):
#             break
#         if len(capped) > 1:
#             shrunk_to_fit = False
#             for factor in (0.92, 0.85, 0.75, 0.65, 0.55):
#                 trial = _uniform_scale_chunk_texts(capped, factor)
#                 dyn2 = _dyn(question, trial)
#                 if fits(static + dyn2):
#                     capped = trial
#                     shrunk_to_fit = True
#                     log.info(
#                         "rag_prompt_uniform_shrink factor=%.2f chunks=%d (fit n_ctx)",
#                         factor,
#                         len(capped),
#                     )
#                     break
#             if shrunk_to_fit:
#                 continue
#             capped.pop()
#             continue
#         text = str(capped[0].get("text", "") or "")
#         if len(text) <= _MIN_RAG_CHUNK_CHARS:
#             break
#         lo, hi = _MIN_RAG_CHUNK_CHARS, len(text)
#         best_prefix = text[:_MIN_RAG_CHUNK_CHARS]
#         while lo <= hi:
#             mid = (lo + hi) // 2
#             ch = dict(capped[0])
#             ch["text"] = text[:mid]
#             dyn2 = _dyn(question, [ch])
#             if fits(static + dyn2):
#                 best_prefix = text[:mid]
#                 lo = mid + 1
#             else:
#                 hi = mid - 1
#         capped[0] = dict(capped[0])
#         capped[0]["text"] = best_prefix
#         log.info(
#             "rag_prompt_single_chunk_prefix_trim chars=%d->%d (fit n_ctx)",
#             len(text),
#             len(best_prefix),
#         )
#         break

#     dynamic = _dyn(question, capped)
#     static_kept, dynamic_kept = _truncate_prompt_pair_to_max_tokens(
#         llm_model, static, dynamic, hard_cap, log
#     )
#     if len(capped) < start_n:
#         log.warning(
#             "rag_prompt_shrunk context_chunks=%d->%d hard_cap_tokens=%d (n_ctx=%d)",
#             start_n,
#             len(capped),
#             hard_cap,
#             n_ctx_i,
#         )
#     return static_kept, dynamic_kept, capped


# def _repair_prompt_on_context_overflow(
#     question: str,
#     llm_model: Any,
#     config: dict[str, Any],
#     log: Any,
# ) -> str:
#     n_ctx_i = max(512, int(config["llm_n_ctx"]))
#     emergency_cap = max(256, n_ctx_i - 512)
#     _st = build_rag_prompt_static()
#     _dy = build_rag_prompt_dynamic(question, [], query_language=_detect_query_language(question))
#     static_prefix, dynamic_suffix = _truncate_prompt_pair_to_max_tokens(
#         llm_model, _st, _dy, emergency_cap, log
#     )
#     return static_prefix + dynamic_suffix


# def _repair_prompt_brutal(llm_model: Any, prompt: str, config: dict[str, Any], log: Any) -> str:
#     n_ctx_i = max(512, int(config["llm_n_ctx"]))
#     emergency_cap = max(256, n_ctx_i - 512)
#     return _truncate_prompt_to_max_tokens(llm_model, prompt, max(128, emergency_cap // 2), log)


# def ask_question_stream_events(question: str) -> Iterator[dict[str, Any]]:
#     """Yield dict events for SSE: ``delta``, ``images``, ``done`` (answer + sources + images), or ``error``."""
#     app_logger = get_logger("yuktra_qna.app", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)
#     configure_rag_file_logging(log_dir=os.path.join(DATA_DIR, "logs"))
#     t0 = time.perf_counter()
#     last = [t0]

#     def mark(step: str, **kv: Any) -> None:
#         now = time.perf_counter()
#         suffix = (" " + " ".join(f"{k}={v}" for k, v in kv.items())) if kv else ""
#         app_logger.info(
#             "qna_stream step=%s delta_sec=%.4f cum_sec=%.4f%s",
#             step,
#             now - last[0],
#             now - t0,
#             suffix,
#         )
#         last[0] = now

#     log_process_start(app_logger, "qna_stream", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
#     try:
#         mark("enter", question_chars=len(question or ""))
#         vectors, faiss_index, metadata, config, emb_model, llm_model = _load_runtime()
#         mark("load_runtime_done")
#         domain_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yuktra_qna_domain")
#         domain_future = domain_pool.submit(_llm_domain_decision, question, llm_model, config, app_logger)

#         emb_prompt_style = resolve_embedding_prompt_style(
#             config, str(config.get("embedding_model") or "local-embedding-gguf")
#         )
#         query_vec = embed_fused_query_for_retrieval(
#             question,
#             None,
#             emb_model,
#             device="cpu",
#             max_length=int(config["embedding_max_length"]),
#             embedding_prompt_style=emb_prompt_style,
#         )
#         mark("query_embedding_done")

#         retrieved, capped, _ = retrieve_rag_pipeline(
#             question,
#             query_vec,
#             vectors,
#             metadata,
#             faiss_index=faiss_index,
#             top_k=int(config["top_k"]),
#             retrieval_pool_k=int(config["retrieval_pool_k"]),
#             mmr_k=int(config["mmr_k"]),
#             mmr_lambda=float(config["mmr_lambda"]),
#             max_context_chars=int(config["max_context_chars"]),
#             restrict_top_document=bool(config["restrict_top_document"]),
#             bm25_weight=float(config["bm25_weight"]),
#             initial_pool_multiplier=int(config["initial_pool_multiplier"]),
#             max_chunks_for_prompt=int(config["max_chunks_for_prompt"]),
#             rrf_linear_blend=float(config["rrf_linear_blend"]),
#             hybrid_alpha_semantic=float(config["hybrid_alpha_semantic"]),
#             min_hybrid_rerank_score=float(config["min_hybrid_rerank_score"]),
#             top_doc_page_window=int(config["top_doc_page_window"]),
#             top_doc_chunk_neighbor_radius=int(config["top_doc_chunk_neighbor_radius"]),
#             top_doc_chunk_neighbors_before=int(config.get("top_doc_chunk_neighbors_before", 5)),
#             top_doc_chunk_neighbors_after=int(config.get("top_doc_chunk_neighbors_after", 1)),
#         )
#         mark("retrieval_done", retrieved_chunks=len(retrieved), capped_chunks=len(capped))
#         _log_retrieved_chunks(app_logger, "qna_stream retrieved_chunks_detail", retrieved)
#         _log_retrieved_chunks(app_logger, "qna_stream capped_chunks_detail", capped)
#         # Domain decision runs in parallel with retrieval work; wait only when needed.
#         if not bool(domain_future.result()):
#             mark("out_of_domain_skip_llm")
#             yield {"type": "done", "answer": OUT_OF_DOMAIN_REPLY, "sources": [], "images": []}
#             return

#         first_bs, rest_bs, para_workers = _parallel_batch_settings(config)
#         batches = _chunk_batches_head_then_rest(list(capped), first_bs, rest_bs)
#         use_parallel = para_workers > 0 and len(batches) > 1
#         if not use_parallel:
#             app_logger.info(
#                 "qna_stream llm_parallel phase=skipped logical_batches=%d first_batch_cfg=%d rest_batch_cfg=%d "
#                 "max_workers_cfg=%d reason=%s",
#                 len(batches),
#                 first_bs,
#                 rest_bs,
#                 para_workers,
#                 "max_workers_zero" if para_workers <= 0 else "single_logical_batch",
#             )

#         max_tok = int(config["llm_max_new_tokens"])
#         max_tok_label = "unlimited" if max_tok <= 0 else str(max_tok)
#         t_llm = time.perf_counter()
#         pieces: list[str] = []
#         first_llm_token_logged = False
#         prompt_for_log = ""

#         def _stream_llm(p: str):
#             return llm_model(
#                 p,
#                 max_tokens=None if max_tok <= 0 else max_tok,
#                 temperature=float(config["llm_temperature"]),
#                 top_p=float(config["llm_top_p"]),
#                 repeat_penalty=float(config["llm_repeat_penalty"]),
#                 stream=True,
#             )

#         def _consume_stream(p: str) -> Iterator[dict[str, Any]]:
#             nonlocal first_llm_token_logged
#             stream = _stream_llm(p)
#             for chunk in stream:
#                 if not isinstance(chunk, dict):
#                     continue
#                 ch0 = (chunk.get("choices") or [{}])[0]
#                 delta = (ch0.get("text") or "") if isinstance(ch0, dict) else ""
#                 if delta:
#                     step = max(8, int(_SSE_UI_DELTA_MAX_CHARS))
#                     for off in range(0, len(delta), step):
#                         piece = delta[off : off + step]
#                         if not first_llm_token_logged:
#                             prev = pipeline_log_preview(piece, max_chars=120)
#                             app_logger.info(
#                                 "qna_stream first_llm_token sec_after_llm_call_start=%.4f sec_after_stream_start=%.4f preview=%r",
#                                 time.perf_counter() - t_llm,
#                                 time.perf_counter() - t0,
#                                 prev,
#                             )
#                             first_llm_token_logged = True
#                         pieces.append(piece)
#                         yield {"type": "delta", "text": piece}

#         if not use_parallel:
#             static_prefix, dynamic_suffix, capped = _build_prompt_fitting_llm_ctx(
#                 question,
#                 capped,
#                 llm_model,
#                 n_ctx=int(config["llm_n_ctx"]),
#                 llm_max_new_tokens=int(config["llm_max_new_tokens"]),
#                 log=app_logger,
#             )
#             prompt = static_prefix + dynamic_suffix
#             prompt_for_log = prompt
#             mark(
#                 "prompt_built",
#                 prompt_chars=len(prompt),
#                 llm_max_new_tokens=max_tok_label,
#             )
#             try:
#                 for ev in _consume_stream(prompt):
#                     yield ev
#             except ValueError as e:
#                 err = str(e).lower()
#                 if "exceed" not in err or "context" not in err:
#                     raise
#                 app_logger.warning(
#                     "rag_llm_ctx_retry_stream after ValueError: %s",
#                     str(e).replace("\n", " ")[:200],
#                 )
#                 prompt = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
#                 prompt_for_log = prompt
#                 try:
#                     for ev in _consume_stream(prompt):
#                         yield ev
#                 except ValueError:
#                     prompt = _repair_prompt_brutal(llm_model, prompt, config, app_logger)
#                     prompt_for_log = prompt
#                     for ev in _consume_stream(prompt):
#                         yield ev
#         else:
#             n_b = len(batches)
#             pool_w = min(para_workers, n_b - 1)
#             mark(
#                 "llm_parallel_plan",
#                 batches=n_b,
#                 first_batch_chunk_size=first_bs,
#                 rest_batch_chunk_size=rest_bs,
#                 background_worker_threads=pool_w,
#                 total_chunk_rows=sum(len(b) for b in batches),
#             )
#             app_logger.info(
#                 "qna_stream llm_parallel phase=plan batches=%d first_batch_chunks=%d rest_batch_chunks=%d "
#                 "pool_workers=%d chunks_per_batch=%s total_chunks=%d",
#                 n_b,
#                 first_bs,
#                 rest_bs,
#                 pool_w,
#                 [len(b) for b in batches],
#                 sum(len(b) for b in batches),
#             )
#             prompt_chars_acc = 0
#             mark(
#                 "prompt_built",
#                 prompt_chars=prompt_chars_acc,
#                 llm_max_new_tokens=max_tok_label,
#                 parallel_batches=n_b,
#             )
#             batch_parts: list[str] = []
#             rolling_answer_ctx = ""
#             for bi, batch_chunks in enumerate(batches, start=1):
#                 yield {
#                     "type": "progress",
#                     "text": _batch_progress_message(bi, n_b, batch_chunks),
#                 }
#                 static_prefix_b, dynamic_suffix_b, _ = _build_prompt_fitting_llm_ctx(
#                     question,
#                     batch_chunks,
#                     llm_model,
#                     n_ctx=int(config["llm_n_ctx"]),
#                     llm_max_new_tokens=int(config["llm_max_new_tokens"]),
#                     log=app_logger,
#                     batch_index=bi,
#                     batch_count=n_b,
#                     previous_answer_draft=rolling_answer_ctx,
#                 )
#                 prompt_b = static_prefix_b + dynamic_suffix_b
#                 prompt_for_log = prompt_b
#                 prompt_chars_acc += len(prompt_b)
#                 app_logger.info(
#                     "qna_stream llm_parallel phase=batch_stream_start batch=%d/%d chunk_count=%d prompt_chars=%d role=main",
#                     bi,
#                     n_b,
#                     len(batch_chunks),
#                     len(prompt_b),
#                 )
#                 t_b = time.perf_counter()
#                 part_buf: list[str] = []
#                 raw_buf: list[str] = []  # raw LLM tokens before dedup, used for redundancy comparison
#                 sep_emitted = False
#                 # For batch 2+, buffer delta events and decide after generation whether to flush
#                 # or discard them (redundancy check prevents repeated answers from reaching the UI).
#                 pending_events: list[dict] = []
#                 prev_ctx_for_dedupe = rolling_answer_ctx if bi > 1 else ""
#                 suppress_repeat_prefix = bool(prev_ctx_for_dedupe)
#                 repeat_match_idx = 0
#                 suppressed_prefix_chars = 0
#                 try:
#                     for ev in _consume_stream(prompt_b):
#                         txt = str(ev.get("text") or "")
#                         if bi > 1 and txt:
#                             raw_buf.append(txt)
#                         emit_txt = txt
#                         if bi > 1 and txt and suppress_repeat_prefix:
#                             out_chars: list[str] = []
#                             for ch in txt:
#                                 if repeat_match_idx < len(prev_ctx_for_dedupe):
#                                     if ch == prev_ctx_for_dedupe[repeat_match_idx]:
#                                         repeat_match_idx += 1
#                                         suppressed_prefix_chars += 1
#                                         continue
#                                     # Match broke — discard matched prefix, don't re-emit it.
#                                     suppress_repeat_prefix = False
#                                     out_chars.append(ch)
#                                 else:
#                                     if ch.isspace() and not out_chars:
#                                         suppressed_prefix_chars += 1
#                                         continue
#                                     suppress_repeat_prefix = False
#                                     out_chars.append(ch)
#                             emit_txt = "".join(out_chars)
#                         if bi > 1 and emit_txt and not sep_emitted:
#                             pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
#                             sep_emitted = True
#                         if emit_txt:
#                             part_buf.append(emit_txt)
#                             if bi == 1:
#                                 yield {"type": "delta", "text": emit_txt}
#                             else:
#                                 pending_events.append({"type": "delta", "text": emit_txt})
#                 except ValueError as e:
#                     err = str(e).lower()
#                     if "exceed" not in err or "context" not in err:
#                         raise
#                     app_logger.warning(
#                         "rag_llm_ctx_retry_stream batch=%d/%d after ValueError: %s",
#                         bi,
#                         n_b,
#                         str(e).replace("\n", " ")[:200],
#                     )
#                     prompt_b = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
#                     prompt_for_log = prompt_b
#                     try:
#                         for ev in _consume_stream(prompt_b):
#                             txt = str(ev.get("text") or "")
#                             if bi > 1 and txt:
#                                 raw_buf.append(txt)
#                             emit_txt = txt
#                             if bi > 1 and txt and suppress_repeat_prefix:
#                                 out_chars: list[str] = []
#                                 for ch in txt:
#                                     if repeat_match_idx < len(prev_ctx_for_dedupe):
#                                         if ch == prev_ctx_for_dedupe[repeat_match_idx]:
#                                             repeat_match_idx += 1
#                                             suppressed_prefix_chars += 1
#                                             continue
#                                         suppress_repeat_prefix = False
#                                         out_chars.append(ch)
#                                     else:
#                                         if ch.isspace() and not out_chars:
#                                             suppressed_prefix_chars += 1
#                                             continue
#                                         suppress_repeat_prefix = False
#                                         out_chars.append(ch)
#                                 emit_txt = "".join(out_chars)
#                             if bi > 1 and emit_txt and not sep_emitted:
#                                 pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
#                                 sep_emitted = True
#                             if emit_txt:
#                                 part_buf.append(emit_txt)
#                                 if bi == 1:
#                                     yield {"type": "delta", "text": emit_txt}
#                                 else:
#                                     pending_events.append({"type": "delta", "text": emit_txt})
#                     except ValueError:
#                         prompt_b = _repair_prompt_brutal(llm_model, prompt_b, config, app_logger)
#                         prompt_for_log = prompt_b
#                         for ev in _consume_stream(prompt_b):
#                             txt = str(ev.get("text") or "")
#                             if bi > 1 and txt:
#                                 raw_buf.append(txt)
#                             emit_txt = txt
#                             if bi > 1 and txt and suppress_repeat_prefix:
#                                 out_chars: list[str] = []
#                                 for ch in txt:
#                                     if repeat_match_idx < len(prev_ctx_for_dedupe):
#                                         if ch == prev_ctx_for_dedupe[repeat_match_idx]:
#                                             repeat_match_idx += 1
#                                             suppressed_prefix_chars += 1
#                                             continue
#                                         suppress_repeat_prefix = False
#                                         out_chars.append(ch)
#                                     else:
#                                         if ch.isspace() and not out_chars:
#                                             suppressed_prefix_chars += 1
#                                             continue
#                                         suppress_repeat_prefix = False
#                                         out_chars.append(ch)
#                                 emit_txt = "".join(out_chars)
#                             if bi > 1 and emit_txt and not sep_emitted:
#                                 pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
#                                 sep_emitted = True
#                             if emit_txt:
#                                 part_buf.append(emit_txt)
#                                 if bi == 1:
#                                     yield {"type": "delta", "text": emit_txt}
#                                 else:
#                                     pending_events.append({"type": "delta", "text": emit_txt})
#                 # Flush buffered batch 2+ events only if they add non-redundant content.
#                 # Use raw_buf (full LLM output before dedup) for comparison — the deduped
#                 # part_buf may start mid-sentence, giving a misleadingly low overlap score.
#                 if bi > 1 and pending_events:
#                     raw_full = "".join(raw_buf)
#                     part_check_src = _sanitize_parallel_later_part(raw_full) or _sanitize_parallel_later_part("".join(part_buf))

#                     # --- Numbered-list path ---
#                     # Extract only the items not already in rolling_answer_ctx and renumber them
#                     # sequentially.  This handles the case where batch 2 repeats items 1-N and
#                     # adds items N+1…M: only N+1…M are emitted, renumbered after batch 1's list.
#                     novel_text = _filter_novel_list_items(part_check_src, rolling_answer_ctx)

#                     if novel_text is not None:
#                         # Content was a numbered list.
#                         if novel_text.strip():
#                             app_logger.info(
#                                 "qna_stream llm_parallel batch=%d/%d list_filter novel_chars=%d",
#                                 bi, n_b, len(novel_text),
#                             )
#                             pieces.append(_PARALLEL_BATCH_ANSWER_SEPARATOR)
#                             for ev in _yield_text_delta_events(novel_text):
#                                 yield ev
#                             part_buf.clear()
#                             part_buf.append(novel_text)
#                         else:
#                             app_logger.info(
#                                 "qna_stream llm_parallel batch=%d/%d list_filter all_items_redundant",
#                                 bi, n_b,
#                             )
#                             part_buf.clear()
#                             sep_emitted = False
#                     else:
#                         # --- Plain-text path: word-overlap redundancy check ---
#                         redundant, jaccard, coverage = _is_redundant_parallel_part(part_check_src, rolling_answer_ctx)
#                         if part_check_src.strip() and not redundant:
#                             pieces.append(_PARALLEL_BATCH_ANSWER_SEPARATOR)
#                             for dev in pending_events:
#                                 yield dev
#                         else:
#                             app_logger.info(
#                                 "qna_stream llm_parallel batch=%d/%d suppressed_redundant_answer jaccard=%.2f coverage=%.2f chars=%d",
#                                 bi,
#                                 n_b,
#                                 jaccard,
#                                 coverage,
#                                 len(raw_full),
#                             )
#                             part_buf.clear()
#                             sep_emitted = False
#                 part_raw = "".join(part_buf)
#                 part = _sanitize_parallel_later_part(part_raw) if bi > 1 else part_raw
#                 if bi > 1 and suppressed_prefix_chars > 0:
#                     app_logger.info(
#                         "qna_stream llm_parallel batch=%d/%d dedupe_suppressed_prefix_chars=%d",
#                         bi,
#                         n_b,
#                         suppressed_prefix_chars,
#                     )
#                 batch_parts.append(part)
#                 if part.strip():
#                     rolling_answer_ctx = _truncate_previous_answer_draft(
#                         (rolling_answer_ctx + ("\n\n" if rolling_answer_ctx else "") + part).strip()
#                     )
#                 app_logger.info(
#                     "qna_stream llm_parallel phase=batch_stream_done batch=%d/%d stream_duration_sec=%.4f streamed_answer_chars=%d",
#                     bi,
#                     n_b,
#                     time.perf_counter() - t_b,
#                     len(part),
#                 )
#             app_logger.info(
#                 "qna_stream llm_parallel phase=parallel_batches_stream_done reason=rolling_previous_batch_context",
#             )
#             app_logger.info(
#                 "qna_stream llm_parallel phase=sequential_stream_done batches=%d total_llm_wall_sec=%.4f",
#                 n_b,
#                 time.perf_counter() - t_llm,
#             )

#         raw = "".join(pieces).strip()
#         log_llm_generation_duration(
#             time.perf_counter() - t_llm,
#             prompt_chars=len(prompt_for_log),
#             answer_chars=len(raw),
#             used_fallback=False,
#         )
#         mark("llm_stream_done", answer_chars=len(raw), parallel=use_parallel)
#         app_logger.info("qna_stream_answer preview=%r", pipeline_log_preview(raw, max_chars=1000))

#         answer = _format_answer_for_display(raw)
#         mark("format_answer_done")
#         exact_spec_answer = _extract_exact_table_spec_answer(question, retrieved)
#         if exact_spec_answer:
#             answer = exact_spec_answer
#             mark("exact_table_spec_answer", answer_chars=len(answer))
#         if _contains_out_of_domain_boilerplate(answer):
#             answer = _fallback_answer_from_retrieved_chunks(question, retrieved)
#             mark("boilerplate_replaced_from_retrieval", answer_chars=len(answer))

#         low_ans = answer.strip().lower()
#         out_of_domain_low = OUT_OF_DOMAIN_REPLY.strip().lower()
#         if low_ans == out_of_domain_low:
#             mark("strip_sources_early_return")
#             yield {"type": "done", "answer": answer, "sources": [], "images": []}
#             return

#         sources: list[dict[str, Any]] = []
#         seen_docs: set[str] = set()
#         for ch in retrieved:
#             name = ch.get("doc_name")
#             if not name or str(name) in seen_docs:
#                 continue
#             seen_docs.add(str(name))
#             path = _resolve_doc_path(str(name), str(ch.get("doc_path")) if ch.get("doc_path") else None, metadata)
#             row: dict[str, Any] = {"doc_name": name, "doc_path": path or ""}
#             pn = _source_page_number(ch)
#             if pn is not None:
#                 row["page_number"] = pn
#             sources.append(row)
#             if len(sources) >= 3:
#                 break
#         mark("sources_built", source_docs=len(sources))
#         images = _retrieve_caption_images(
#             question=question,
#             retrieved_chunks=retrieved,
#             emb_model=emb_model,
#             text_cfg=config,
#             app_logger=app_logger,
#         )
#         mark("images_retrieved", image_count=len(images))
#         yield {"type": "done", "answer": answer, "sources": sources, "images": images}
#     except Exception as e:
#         app_logger.exception("qna_stream failed: %s", e)
#         yield {"type": "error", "message": str(e)}
#     finally:
#         try:
#             domain_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[name-defined]
#         except Exception:
#             pass
#         app_logger.info("qna_stream_timing request_total_wall_sec=%.4f", time.perf_counter() - t0)
#         log_process_end(app_logger, "qna_stream", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")


# def ask_question(question: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
#     app_logger = get_logger("yuktra_qna.app", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)
#     configure_rag_file_logging(log_dir=os.path.join(DATA_DIR, "logs"))
#     t0 = time.perf_counter()
#     last = [t0]

#     def mark(step: str, **kv: Any) -> None:
#         now = time.perf_counter()
#         suffix = (" " + " ".join(f"{k}={v}" for k, v in kv.items())) if kv else ""
#         app_logger.info(
#             "qna_step step=%s delta_sec=%.4f cum_sec=%.4f%s",
#             step,
#             now - last[0],
#             now - t0,
#             suffix,
#         )
#         last[0] = now

#     log_process_start(app_logger, "qna_request", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
#     try:
#         mark("enter", question_chars=len(question or ""))
#         vectors, faiss_index, metadata, config, emb_model, llm_model = _load_runtime()
#         mark("load_runtime_done")
#         domain_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yuktra_qna_domain")
#         domain_future = domain_pool.submit(_llm_domain_decision, question, llm_model, config, app_logger)

#         emb_prompt_style = resolve_embedding_prompt_style(
#             config, str(config.get("embedding_model") or "local-embedding-gguf")
#         )
#         query_vec = embed_fused_query_for_retrieval(
#             question,
#             None,
#             emb_model,
#             device="cpu",
#             max_length=int(config["embedding_max_length"]),
#             embedding_prompt_style=emb_prompt_style,
#         )
#         mark("query_embedding_done")

#         retrieved, capped, _ = retrieve_rag_pipeline(
#             question,
#             query_vec,
#             vectors,
#             metadata,
#             faiss_index=faiss_index,
#             top_k=int(config["top_k"]),
#             retrieval_pool_k=int(config["retrieval_pool_k"]),
#             mmr_k=int(config["mmr_k"]),
#             mmr_lambda=float(config["mmr_lambda"]),
#             max_context_chars=int(config["max_context_chars"]),
#             restrict_top_document=bool(config["restrict_top_document"]),
#             bm25_weight=float(config["bm25_weight"]),
#             initial_pool_multiplier=int(config["initial_pool_multiplier"]),
#             max_chunks_for_prompt=int(config["max_chunks_for_prompt"]),
#             rrf_linear_blend=float(config["rrf_linear_blend"]),
#             hybrid_alpha_semantic=float(config["hybrid_alpha_semantic"]),
#             min_hybrid_rerank_score=float(config["min_hybrid_rerank_score"]),
#             top_doc_page_window=int(config["top_doc_page_window"]),
#             top_doc_chunk_neighbor_radius=int(config["top_doc_chunk_neighbor_radius"]),
#             top_doc_chunk_neighbors_before=int(config.get("top_doc_chunk_neighbors_before", 5)),
#             top_doc_chunk_neighbors_after=int(config.get("top_doc_chunk_neighbors_after", 1)),
#         )
#         mark(
#             "retrieval_done",
#             retrieved_chunks=len(retrieved),
#             capped_chunks=len(capped),
#         )
#         _log_retrieved_chunks(app_logger, "qna_request retrieved_chunks_detail", retrieved)
#         _log_retrieved_chunks(app_logger, "qna_request capped_chunks_detail", capped)
#         if not bool(domain_future.result()):
#             mark("out_of_domain_skip_llm")
#             return OUT_OF_DOMAIN_REPLY, [], []

#         first_bs, rest_bs, para_workers = _parallel_batch_settings(config)
#         batches = _chunk_batches_head_then_rest(list(capped), first_bs, rest_bs)
#         use_parallel = para_workers > 0 and len(batches) > 1
#         if not use_parallel:
#             app_logger.info(
#                 "qna_request llm_parallel phase=skipped logical_batches=%d first_batch_cfg=%d rest_batch_cfg=%d "
#                 "max_workers_cfg=%d reason=%s",
#                 len(batches),
#                 first_bs,
#                 rest_bs,
#                 para_workers,
#                 "max_workers_zero" if para_workers <= 0 else "single_logical_batch",
#             )
#         max_tok = int(config["llm_max_new_tokens"])
#         max_tok_label = "unlimited" if max_tok <= 0 else str(max_tok)
#         t_llm = time.perf_counter()

#         def _complete_sync(llm_m: Llama, p: str) -> Any:
#             return llm_m(
#                 p,
#                 max_tokens=None if max_tok <= 0 else max_tok,
#                 temperature=float(config["llm_temperature"]),
#                 top_p=float(config["llm_top_p"]),
#                 repeat_penalty=float(config["llm_repeat_penalty"]),
#             )

#         if not use_parallel:
#             static_prefix, dynamic_suffix, capped = _build_prompt_fitting_llm_ctx(
#                 question,
#                 capped,
#                 llm_model,
#                 n_ctx=int(config["llm_n_ctx"]),
#                 llm_max_new_tokens=int(config["llm_max_new_tokens"]),
#                 log=app_logger,
#             )
#             prompt = static_prefix + dynamic_suffix
#             mark(
#                 "prompt_built",
#                 prompt_chars=len(prompt),
#                 llm_max_new_tokens=max_tok_label,
#             )
#             try:
#                 result = _complete_sync(llm_model, prompt)
#             except ValueError as e:
#                 err = str(e).lower()
#                 if "exceed" not in err or "context" not in err:
#                     raise
#                 app_logger.warning(
#                     "rag_llm_ctx_retry after ValueError: %s",
#                     str(e).replace("\n", " ")[:200],
#                 )
#                 prompt = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
#                 try:
#                     result = _complete_sync(llm_model, prompt)
#                 except ValueError:
#                     prompt = _repair_prompt_brutal(llm_model, prompt, config, app_logger)
#                     result = _complete_sync(llm_model, prompt)
#             answer = (result["choices"][0]["text"] or "").strip()
#             prompt_chars_for_log = len(prompt)
#         else:
#             n_b = len(batches)
#             pool_w = min(para_workers, n_b - 1)
#             mark(
#                 "llm_parallel_plan",
#                 batches=n_b,
#                 first_batch_chunk_size=first_bs,
#                 rest_batch_chunk_size=rest_bs,
#                 background_worker_threads=pool_w,
#                 total_chunk_rows=sum(len(b) for b in batches),
#             )
#             app_logger.info(
#                 "qna_request llm_parallel phase=plan batches=%d first_batch_chunks=%d rest_batch_chunks=%d "
#                 "pool_workers=%d chunks_per_batch=%s total_chunks=%d",
#                 n_b,
#                 first_bs,
#                 rest_bs,
#                 pool_w,
#                 [len(b) for b in batches],
#                 sum(len(b) for b in batches),
#             )
#             prompt_chars_acc = 0
#             rolling_answer_ctx = ""
#             batch_parts: list[str] = []

#             def _run_batch_on_main_llm(i: int, prev_answer: str) -> str:
#                 nonlocal prompt_chars_acc
#                 st, dyn, _ = _build_prompt_fitting_llm_ctx(
#                     question,
#                     batches[i],
#                     llm_model,
#                     n_ctx=int(config["llm_n_ctx"]),
#                     llm_max_new_tokens=int(config["llm_max_new_tokens"]),
#                     log=app_logger,
#                     batch_index=i + 1,
#                     batch_count=n_b,
#                     previous_answer_draft=prev_answer,
#                 )
#                 pr = st + dyn
#                 prompt_chars_acc += len(pr)
#                 try:
#                     res = _complete_sync(llm_model, pr)
#                 except ValueError as e:
#                     err = str(e).lower()
#                     if "exceed" not in err or "context" not in err:
#                         raise
#                     pr2 = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
#                     try:
#                         res = _complete_sync(llm_model, pr2)
#                     except ValueError:
#                         pr3 = _repair_prompt_brutal(llm_model, pr2, config, app_logger)
#                         res = _complete_sync(llm_model, pr3)
#                 out = (res["choices"][0]["text"] or "").strip()
#                 return _sanitize_parallel_later_part(out) if (i + 1) > 1 else out

#             for i in range(n_b):
#                 t_b = time.perf_counter()
#                 app_logger.info(
#                     "qna_request llm_parallel phase=batch_start batch=%d/%d chunk_count=%d role=main",
#                     i + 1,
#                     n_b,
#                     len(batches[i]),
#                 )
#                 ptxt = _run_batch_on_main_llm(i, rolling_answer_ctx)
#                 app_logger.info(
#                     "qna_request llm_parallel phase=batch_done batch=%d/%d answer_chars=%d duration_sec=%.4f role=main",
#                     i + 1,
#                     n_b,
#                     len(ptxt),
#                     time.perf_counter() - t_b,
#                 )
#                 if i > 0 and ptxt.strip():
#                     redundant, jaccard, coverage = _is_redundant_parallel_part(ptxt, rolling_answer_ctx)
#                     if redundant:
#                         app_logger.info(
#                             "qna_request llm_parallel batch=%d/%d suppressed_redundant_answer jaccard=%.2f coverage=%.2f chars=%d",
#                             i + 1,
#                             n_b,
#                             jaccard,
#                             coverage,
#                             len(ptxt),
#                         )
#                         continue
#                 batch_parts.append(ptxt)
#                 if ptxt.strip():
#                     rolling_answer_ctx = _truncate_previous_answer_draft(
#                         (rolling_answer_ctx + ("\n\n" if rolling_answer_ctx else "") + ptxt).strip()
#                     )
#             answer = _PARALLEL_BATCH_ANSWER_SEPARATOR.join(batch_parts)
#             app_logger.info(
#                 "qna_request llm_parallel phase=parallel_batches_done reason=rolling_previous_batch_context",
#             )
#             app_logger.info(
#                 "qna_request llm_parallel phase=all_joined batches=%d total_answer_chars=%d llm_wall_sec=%.4f",
#                 n_b,
#                 len(answer),
#                 time.perf_counter() - t_llm,
#             )
#             mark(
#                 "prompt_built",
#                 prompt_chars=prompt_chars_acc,
#                 llm_max_new_tokens=max_tok_label,
#                 parallel_batches=n_b,
#             )
#             prompt_chars_for_log = prompt_chars_acc
#         log_llm_generation_duration(
#             time.perf_counter() - t_llm,
#             prompt_chars=prompt_chars_for_log,
#             answer_chars=len(answer),
#             used_fallback=False,
#         )
#         mark("llm_decode_done", answer_chars=len(answer))
#         app_logger.info("qna_answer preview=%r", pipeline_log_preview(answer, max_chars=1000))
#         answer = _format_answer_for_display(answer)
#         mark("format_answer_done")
#         exact_spec_answer = _extract_exact_table_spec_answer(question, retrieved)
#         if exact_spec_answer:
#             answer = exact_spec_answer
#             mark("exact_table_spec_answer", answer_chars=len(answer))
#         if _contains_out_of_domain_boilerplate(answer):
#             answer = _fallback_answer_from_retrieved_chunks(question, retrieved)
#             mark("boilerplate_replaced_from_retrieval", answer_chars=len(answer))

#         low_ans = answer.strip().lower()
#         out_of_domain_low = OUT_OF_DOMAIN_REPLY.strip().lower()
#         if low_ans == out_of_domain_low:
#             mark("strip_sources_early_return")
#             return answer, [], []

#         sources: list[dict[str, Any]] = []
#         seen_docs: set[str] = set()
#         for ch in retrieved:
#             name = ch.get("doc_name")
#             if not name or str(name) in seen_docs:
#                 continue
#             seen_docs.add(str(name))
#             path = _resolve_doc_path(str(name), str(ch.get("doc_path")) if ch.get("doc_path") else None, metadata)
#             row: dict[str, Any] = {"doc_name": name, "doc_path": path or ""}
#             pn = _source_page_number(ch)
#             if pn is not None:
#                 row["page_number"] = pn
#             sources.append(row)
#             if len(sources) >= 3:
#                 break
#         mark("sources_built", source_docs=len(sources))
#         images = _retrieve_caption_images(
#             question=question,
#             retrieved_chunks=retrieved,
#             emb_model=emb_model,
#             text_cfg=config,
#             app_logger=app_logger,
#         )
#         return answer, sources, images
#     finally:
#         try:
#             domain_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[name-defined]
#         except Exception:
#             pass
#         app_logger.info("qna_timing request_total_wall_sec=%.4f", time.perf_counter() - t0)
#         log_process_end(app_logger, "qna_request", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
import base64
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO
from typing import Any, Iterator, Optional

from llama_cpp import Llama, LlamaRAMCache

from logger import get_logger, log_process_end, log_process_start
from prompts import (
    build_rag_prompt_dynamic,
    build_rag_prompt_static,
    ensure_blank_line_before_key_points,
    normalize_runon_bullet_lines,
)
from rag_utils import (
    _parse_page_number,
    attach_corpus_retrieval_vocab,
    configure_rag_file_logging,
    embed_fused_query_for_retrieval,
    load_llamacpp_embedding_model,
    load_vector_store,
    log_llm_generation_duration,
    pipeline_log_preview,
    resolve_embedding_prompt_style,
    resolve_retrieval_generic_terms,
    retrieve_rag_pipeline,
    topk_search,
    topk_search_faiss,
)
from store_runtime_config import merge_store_runtime_config

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# Honor the DATA_DIR env var (set by the installer/service) so the vector store is
# found under ProgramData; fall back to the repo's data/ for dev runs. In the
# compiled .exe, __file__/../.. wrongly resolves to the drive root (C:\data).
DATA_DIR = (os.environ.get("DATA_DIR") or "").strip() or os.path.join(REPO_ROOT, "data")
# Ingested vector stores can be provided from OUTSIDE the app's data folder.
# Set YUKTRA_INGESTED_DIR to any path (external drive, network share, etc.) and
# the backend reads the stores from there instead of <DATA_DIR>\Ingested. This
# lets you ship content separately from the installer and swap it without a
# rebuild. Falls back to <DATA_DIR>\Ingested when the override is unset.
INGESTED_DIR = (os.environ.get("YUKTRA_INGESTED_DIR") or "").strip() or os.path.join(DATA_DIR, "Ingested")


def _runtime_threads() -> int:
    raw = os.environ.get("YUKTRA_LLM_N_THREADS", "").strip()
    try:
        val = int(raw) if raw else 2
    except ValueError:
        val = 2
    return max(1, val)


@lru_cache(maxsize=1)
def _llama_gpu_available() -> bool:
    """True when the installed llama-cpp-python was built with GPU offload
    support (CUDA / Metal / ROCm / SYCL / Vulkan).

    For an integrated GPU this means a Vulkan-enabled build. A CPU-only build
    silently ignores ``n_gpu_layers``, so we treat that as "no GPU" and stay on
    CPU. Detection failures fall back to CPU as well.
    """
    try:
        from llama_cpp import llama_supports_gpu_offload  # type: ignore

        return bool(llama_supports_gpu_offload())
    except Exception:
        return False


def _resolve_n_gpu_layers() -> int:
    """Number of model layers to offload to the GPU for llama.cpp.

    Returns ``-1`` (offload all layers) when a GPU-capable llama.cpp build is
    present, else ``0`` (CPU only). Override with ``YUKTRA_LLM_N_GPU_LAYERS``
    or ``YUKTRA_GPU_LAYERS`` (Windows installer legacy name), e.g. ``0`` forces
    CPU, ``-1`` forces full offload, ``20`` offloads 20 layers — partial offload
    often performs better on an integrated GPU).
    """
    for key in ("YUKTRA_LLM_N_GPU_LAYERS", "YUKTRA_GPU_LAYERS"):
        raw = os.environ.get(key, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
    return -1 if _llama_gpu_available() else 0


def _first_ingested_machine() -> str:
    """Return the most recently updated folder name in data/Ingested/."""
    if os.path.isdir(INGESTED_DIR):
        folders = [
            f
            for f in os.listdir(INGESTED_DIR)
            if os.path.isdir(os.path.join(INGESTED_DIR, f))
        ]
        if folders:
            # Prefer latest upload/activity folder by filesystem mtime.
            # Tiebreaker on name keeps selection deterministic.
            folders.sort(
                key=lambda f: (os.path.getmtime(os.path.join(INGESTED_DIR, f)), f),
                reverse=True,
            )
            return folders[0]
    return ""


RAG_STORE_TENANT = _first_ingested_machine()
RAG_STORE_INDEX_NAME = os.environ.get("RAG_STORE_INDEX_NAME", "document_text").strip() or "document_text"
IMAGE_RAG_STORE_TENANT = RAG_STORE_TENANT
IMAGE_RAG_STORE_INDEX_NAME = os.environ.get("IMAGE_RAG_STORE_INDEX_NAME", "Img").strip() or "Img"
EMBEDDING_MODEL_PATH = os.environ.get(
    "EMBEDDING_MODEL_PATH",
    os.path.join(DATA_DIR, "models", "embeddinggemma-300M-Q8_0.gguf"),
)
LLM_MODEL_PATH = os.environ.get(
    "LLM_MODEL_PATH",
    os.path.join(DATA_DIR, "models", "gemma-3-4b-it-Q4_K_M.gguf"),
)
OUT_OF_DOMAIN_REPLY = (
    "Hi! I'm the Equipment Intelligence assistant.\n"
    "Ask a query related to equipment/manual-related question?"
)

# Between parallel batch answers: avoid "---" / "***" (Markdown renders as <hr> and looks like a second reply).
_PARALLEL_BATCH_ANSWER_SEPARATOR = "\n\n"

# Slice streamed LLM text so the UI can refresh progressively (Streamlit fragment polling).
_SSE_UI_DELTA_MAX_CHARS = 56

# Whole-line removal: model hedging that contradicts retrieved steps (case-insensitive substring match).
_RAG_DISCLAIMER_LINE_SUBSTRINGS = (
    "does not provide information, not specified in the provided context",
    "the document does not provide information",
    "not specified in the provided context",
    "not found in the provided context",
    "is not mentioned in the provided context",
    "cannot be determined from the provided context",
    "this section does not provide details",
    "does not provide details on",
    "end of answer",
)

_CAPTION_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "show",
    "please",
    "about",
    "what",
    "how",
    "when",
    "where",
    "machine",
    "manual",
    "diagram",
    "image",
    "images",
    "picture",
    "pictures",
    "figure",
}

_IMAGE_BAD_CAPTION_PATTERNS = (
    re.compile(r"<!--\s*page\s+break\s*-->", re.I),
    re.compile(r"!\[[^\]]*\]\(\s*data:image/", re.I),
    re.compile(r"\bdata:image/[a-z0-9.+-]+;base64,", re.I),
    re.compile(r"\b(?:www\.|https?://|\.com\b|\.net\b|\.org\b)", re.I),
)

_IMAGE_BAD_CAPTION_EXACT = {
    "image",
    "img",
    "picture",
    "figure",
    "user's manual",
    "users manual",
    "user manual",
    "manual",
    "our superior",
}

# Light synonym groups so manual wording (e.g. "sucking") still matches image-seeking queries ("suction").
_IMAGE_LEXICON_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"suction", "suck", "sucking", "vacuum", "vacuo"}),
    frozenset({"form", "forming", "formed", "unformed", "former"}),
    frozenset({"carton", "cartoning", "cartons"}),
    frozenset({"adjust", "adjusting", "adjustment"}),
    frozenset({"height", "high", "lower", "raise"}),
    frozenset({"bolt", "bolts", "hexagon", "hex"}),
)


def _synonym_closure(tok: str) -> frozenset[str]:
    t = (tok or "").lower()
    for g in _IMAGE_LEXICON_GROUPS:
        if t in g:
            return g
    return frozenset({t})


def _is_bad_image_caption(caption: str) -> bool:
    c = re.sub(r"\s+", " ", (caption or "").strip())
    if not c:
        return True
    cl = c.lower()
    if cl in _IMAGE_BAD_CAPTION_EXACT:
        return True
    return any(p.search(c) for p in _IMAGE_BAD_CAPTION_PATTERNS)


def _clean_image_caption(caption: str) -> str:
    """Strip embedded base64 image data-URIs out of a caption/text.

    During ingestion some images get a raw ``![Image](data:image/...;base64,...)``
    blob stored as their caption/text instead of a real description. Such a "caption"
    is useless for lexical matching, so we collapse it to empty here; the image is
    then matched by image-vector similarity instead of being thrown away.
    """
    s = caption or ""
    s = re.sub(r"!\[[^\]]*\]\(\s*data:image/[^)]*\)", " ", s, flags=re.I)
    s = re.sub(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=2048)
def _image_visual_filter_reason(image_uuid: str, image_base64: str) -> str:
    """
    Reject small logo/title/watermark-like extracts before they reach the UI.
    Keeps the gate conservative: large diagrams/photos remain eligible even when
    they contain faint OEM watermarks.
    """
    b64 = str(image_base64 or "").strip()
    if not b64:
        return "empty_b64"
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return "invalid_b64"
    try:
        from PIL import Image
    except ImportError:
        return "small_payload" if len(raw) < int(os.environ.get("IMAGE_MIN_BYTES", "2500")) else ""
    try:
        im = Image.open(BytesIO(raw))
        w, h = im.size
    except Exception:
        return "unreadable_image"
    min_area = int(os.environ.get("IMAGE_MIN_AREA", "9000"))
    min_side = int(os.environ.get("IMAGE_MIN_SIDE", "35"))
    if w * h < min_area:
        return f"small_area:{w}x{h}"
    if min(w, h) < min_side:
        return f"small_side:{w}x{h}"
    return ""


def _strip_rag_disclaimer_lines(text: str) -> str:
    out: list[str] = []
    for ln in (text or "").splitlines():
        low = ln.lower()
        if any(n in low for n in _RAG_DISCLAIMER_LINE_SUBSTRINGS):
            continue
        out.append(ln)
    return "\n".join(out).strip()


_PARALLEL_LATER_NEGATIVE_LINE_RE = re.compile(
    r"(?i)\b("
    r"(the\s+)?document\s+does\s+not\s+provide"
    r"|does\s+not\s+provide\s+(steps|details|information|instructions)"
    r"|does\s+not\s+mention"
    r"|not\s+specified\s+in\s+the\s+provided\s+context"
    r"|not\s+found\s+in\s+the\s+provided\s+context"
    r"|cannot\s+be\s+determined\s+from\s+the\s+provided\s+context"
    r")\b"
)


def _sanitize_parallel_later_part(text: str) -> str:
    """
    For batch 2..N, remove fallback/negative context lines.
    If nothing substantive remains, return empty so we don't append noise.
    """
    kept: list[str] = []
    for ln in (text or "").splitlines():
        if _PARALLEL_LATER_NEGATIVE_LINE_RE.search(ln or ""):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def _truncate_previous_answer_draft(text: str, *, max_chars: int = 1400) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    # Keep tail to preserve current section/list continuation cues.
    return t[-max_chars:].strip()


def _word_overlap_ratio(text_a: str, text_b: str) -> float:
    """Word-level Jaccard similarity between two texts."""
    words_a = set((text_a or "").lower().split())
    words_b = set((text_b or "").lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _detect_query_language(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "English"
    if re.search(r"[\u0900-\u097F]", q):
        return "Hindi"
    tokens = re.findall(r"[A-Za-z']+", q.lower())
    if not tokens:
        return "English"
    hinglish_tokens = {
        "kya", "kyun", "kyunki", "kaise", "kahan", "kab", "kaun", "kitna", "kitni", "kitne",
        "hai", "hain", "hota", "hoti", "hote", "ho", "hoga", "hua",
        "ka", "ke", "ki", "ko", "se", "mein", "main", "me",
        "aur", "ya", "par", "lekin",
        "batao", "batana", "samjhao", "karo", "kar", "karna", "de", "do",
        "nahi", "nhi", "na", "chahiye",
        "machine", "manual", "alarm", "reset", "dimension", "rating", "specification",
    }
    hits = sum(1 for t in tokens if t in hinglish_tokens)
    if hits >= 1 and any(t in tokens for t in ("kya", "kaise", "kitna", "kitni", "kitne", "hota", "hoti", "hai", "ka", "ki", "ko", "aur")):
        return "Hinglish"
    return "English"


# Slightly stricter tokenization for duplicate-detection across batches.
_PARALLEL_REDUNDANCY_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)


def _parallel_token_set(text: str) -> set[str]:
    return {t.lower() for t in _PARALLEL_REDUNDANCY_TOKEN_RE.findall(text or "")}


def _is_redundant_parallel_part(part_text: str, rolling_ctx: str) -> tuple[bool, float, float]:
    """
    Detect whether a later-batch answer is effectively repeating prior answer content.
    Returns:
      - redundant: bool
      - jaccard: token-set Jaccard similarity
      - coverage: fraction of part tokens already present in rolling context
    """
    pt = _parallel_token_set(part_text)
    rt = _parallel_token_set(rolling_ctx)
    if not pt or not rt:
        return False, 0.0, 0.0
    inter = len(pt & rt)
    union = len(pt | rt)
    jaccard = float(inter) / float(max(1, union))
    coverage = float(inter) / float(max(1, len(pt)))
    # Suppress when the later part is mostly covered by prior answer tokens,
    # even if Jaccard is moderate due to longer rolling context.
    redundant = (coverage >= 0.72) or (jaccard >= _BATCH_REDUNDANCY_OVERLAP_THRESHOLD)
    return redundant, jaccard, coverage


# Batch 2+ answers with word-overlap >= this threshold vs rolling context are suppressed as duplicates.
_BATCH_REDUNDANCY_OVERLAP_THRESHOLD = 0.75

_NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+)", re.MULTILINE)


def _filter_novel_list_items(new_text: str, rolling_ctx: str) -> str | None:
    """
    For numbered-list responses: strip items already covered by rolling_ctx and
    renumber the remaining novel items sequentially after the last number in rolling_ctx.

    Returns:
      - str  : the filtered (possibly empty) text to emit — caller should emit if non-blank
      - None : new_text is not a numbered list; caller should fall back to word-overlap check
    """
    lines = (new_text or "").strip().splitlines()
    item_map: list[tuple[int, str]] = []  # (original_number, item_text)
    header_lines: list[str] = []
    non_item_count = 0
    for line in lines:
        m = re.match(r"^\s*(\d+)\.\s+(.+)", line)
        if m:
            item_map.append((int(m.group(1)), m.group(2).strip()))
        else:
            if line.strip():
                non_item_count += 1
                header_lines.append(line.rstrip())

    # Only treat as a numbered list if there are items and they dominate the content.
    if not item_map or non_item_count > len(item_map):
        return None

    ref_tokens = _parallel_token_set(rolling_ctx)
    ref_nums = [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.", rolling_ctx)]
    last_ref_num = max(ref_nums, default=0)

    novel_items: list[str] = []
    for _, item_text in item_map:
        tok = _parallel_token_set(item_text)
        if not tok:
            continue
        # Item is novel if less than 65 % of its tokens are already in the reference context.
        coverage = len(tok & ref_tokens) / len(tok)
        if coverage < 0.65:
            novel_items.append(item_text)

    if not novel_items:
        return ""

    # Renumber sequentially after the last reference number.
    result_lines = [f"{last_ref_num + 1 + i}. {item}" for i, item in enumerate(novel_items)]
    # Prepend any non-list header lines (e.g. "According to page 6…") for source attribution.
    if header_lines:
        return "\n".join(header_lines) + "\n" + "\n".join(result_lines)
    return "\n".join(result_lines)


_thread_llm_local = threading.local()


def _llm_domain_decision(
    question: str,
    llm_model: Llama,
    config: dict[str, Any],
    log: Any,
) -> bool:
    """LLM-only domain decision. Returns True if query is equipment/manual related."""
    q = (question or "").strip()
    if not q:
        return False
    prompt = (
        "You are a strict relevance classifier for an equipment-manual assistant.\n"
        "Task: Decide whether the user query is related to laboratory, analytical, "
        "manufacturing, utility equipment, machine operation, troubleshooting, "
        "maintenance, components, diagrams, specs, SOPs, manuals, training videos, or video transcripts.\n"
        "The user query may be written in English, Hindi/Devanagari, or Hinglish/Romanized Hindi.\n"
        "Treat Hindi and Hinglish equipment/manual queries the same as English equipment/manual queries.\n"
        "Do not mark a query IRRELEVANT only because it uses Hindi words, Devanagari script, "
        "Romanized Hindi words, mixed Hindi-English grammar, or spelling variants such as kya, kaise, kitna, "
        "hota, hai, machine ka, alarm ko reset, dimension kitna, or manual mein.\n"
        "Respond with exactly one token:\n"
        "- RELEVANT\n"
        "- IRRELEVANT\n\n"
        "If the query can reasonably be answered from equipment manuals or equipment knowledge, "
        "return RELEVANT.\n"
        "If it is general chit-chat or unrelated domains (weather, movies, sports, finance, etc.), "
        "return IRRELEVANT.\n\n"
        f"USER QUERY:\n{q}\n\n"
        "DECISION:"
    )
    max_tok = int(config.get("llm_domain_gate_max_tokens") or 8)
    try:
        out = llm_model(
            prompt,
            max_tokens=max_tok,
            temperature=0.0,
            top_p=1.0,
            repeat_penalty=1.0,
            stream=False,
        )
        txt = str(((out.get("choices") or [{}])[0].get("text") or "")).strip().upper()
        decision = "RELEVANT" if "RELEVANT" in txt and "IRRELEVANT" not in txt else ("IRRELEVANT" if "IRRELEVANT" in txt else "")
        if not decision:
            # Fail-open to avoid false denials when model output format drifts.
            log.warning("llm_domain_gate unparseable_output=%r defaulting_to_relevant", txt[:120])
            return True
        return decision == "RELEVANT"
    except Exception as e:
        # Fail-open on classifier issues so normal RAG path can still answer.
        log.warning("llm_domain_gate failed err=%s defaulting_to_relevant", e)
        return True


def _retrieved_context_overrides_domain_gate(
    chunks: list[dict[str, Any]],
    config: dict[str, Any],
) -> bool:
    """
    Trust strong video-transcript retrieval over the lightweight relevance gate.

    The gate only sees the question, not the retrieved context. For uploaded
    training videos, natural questions like "what is this training about" can be
    falsely classified as generic, even though retrieval has already found the
    matching transcript.
    """
    enabled = str(
        config.get(
            "domain_gate_trust_video_transcript_retrieval",
            os.environ.get("YUKTRA_DOMAIN_GATE_TRUST_VIDEO_TRANSCRIPT_RETRIEVAL", "1"),
        )
    ).strip().lower() not in ("0", "false", "no", "off")
    if not enabled or not chunks:
        return False

    has_video_transcript = False
    meaningful_chars = 0
    for ch in chunks:
        doc_name = str(ch.get("doc_name") or "").lower()
        ingest_mode = str(ch.get("ingest_mode") or "").lower()
        if ingest_mode == "video_transcript" or doc_name.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
            has_video_transcript = True
        text = re.sub(r"(?m)^\s*#+\s*", "", str(ch.get("text") or "")).strip()
        if len(text) >= 80:
            meaningful_chars += len(text)
    return has_video_transcript and meaningful_chars >= 120


def _create_llama_instance(config: dict[str, Any]) -> Llama:
    app_logger = logging.getLogger("yuktra_qna.app")
    n_gpu_layers = _resolve_n_gpu_layers()
    verbose = os.environ.get("YUKTRA_LLAMA_CPP_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")
    base_n_ctx = int(config["llm_n_ctx"])
    n_batch = int(config["llm_n_batch"])
    n_ubatch = int(config["llm_n_ubatch"])

    def _try_load(n_ctx: int, gpu_layers: int) -> Llama:
        return Llama(
            model_path=LLM_MODEL_PATH,
            n_ctx=n_ctx,
            n_threads=_runtime_threads(),
            n_batch=n_batch,
            n_ubatch=n_ubatch,
            n_gpu_layers=gpu_layers,
            verbose=verbose,
        )

    # Attempt sequence: (gpu, full_ctx) → (cpu, full_ctx) → (cpu, half_ctx) → (cpu, min_ctx)
    # Each step trades capability for lower RAM/VRAM so marginal machines still work.
    # Dedup so a CPU-only host doesn't retry (0, base_n_ctx) twice.
    _seen: set = set()
    attempts = []
    for _gl, _nc in [
        (n_gpu_layers, base_n_ctx),
        (0,            base_n_ctx),
        (0,            max(512, base_n_ctx // 2)),
        (0,            512),
    ]:
        if (_gl, _nc) not in _seen:
            _seen.add((_gl, _nc))
            attempts.append((_gl, _nc))

    last_exc: Exception = RuntimeError("no attempts made")
    for gpu_layers, n_ctx in attempts:
        try:
            llm = _try_load(n_ctx, gpu_layers)
            app_logger.info(
                "llm_loaded n_gpu_layers=%s n_ctx=%s",
                gpu_layers, n_ctx,
            )
            if n_ctx < base_n_ctx:
                app_logger.warning(
                    "llm_ctx_reduced requested=%s actual=%s (OOM fallback)", base_n_ctx, n_ctx
                )
            cache_mb = int(config.get("llm_prompt_cache_mb") or 0)
            if cache_mb > 0 and n_ctx == base_n_ctx:
                llm.set_cache(LlamaRAMCache(capacity_bytes=cache_mb * 1024 * 1024))
            return llm
        except Exception as exc:
            last_exc = exc
            if gpu_layers != 0:
                app_logger.warning(
                    "llm_gpu_load_failed n_gpu_layers=%s; falling back to CPU",
                    gpu_layers, exc_info=True,
                )
            else:
                app_logger.warning(
                    "llm_load_failed n_ctx=%s; trying smaller context", n_ctx, exc_info=True,
                )
    raise last_exc


def _worker_llama(config: dict[str, Any]) -> Llama:
    llm = getattr(_thread_llm_local, "llm", None)
    if llm is None:
        _thread_llm_local.llm = _create_llama_instance(config)
        llm = _thread_llm_local.llm
    return llm


def _chunk_batches_head_then_rest(
    chunks: list[dict[str, Any]],
    first_size: int,
    rest_size: int,
) -> list[list[dict[str, Any]]]:
    """
    First batch uses ``first_size`` chunks; every following batch uses ``rest_size``.
    If both sizes are <= 0, returns a single batch with all chunks.
    """
    if not chunks:
        return []
    rows = [dict(c) for c in chunks]
    if first_size <= 0 and rest_size <= 0:
        return [rows]
    fs = int(first_size) if first_size > 0 else int(rest_size)
    rs = int(rest_size) if rest_size > 0 else int(first_size)
    fs = max(1, fs)
    rs = max(1, rs)
    out: list[list[dict[str, Any]]] = []
    i = 0
    take0 = min(fs, len(rows) - i)
    out.append(rows[i : i + take0])
    i += take0
    while i < len(rows):
        take = min(rs, len(rows) - i)
        out.append(rows[i : i + take])
        i += take
    return out


def _parallel_batch_settings(config: dict[str, Any]) -> tuple[int, int, int]:
    first_sz = int(config.get("llm_parallel_first_batch_chunk_size") or 0)
    rest_sz = int(config.get("llm_parallel_chunk_batch_size") or 0)
    max_workers = int(config.get("llm_parallel_max_workers") or 0)
    return first_sz, rest_sz, max_workers


def _yield_text_delta_events(text: str, width: int = 56) -> Iterator[dict[str, Any]]:
    t = text or ""
    for j in range(0, len(t), max(1, width)):
        yield {"type": "delta", "text": t[j : j + width]}


def _parallel_worker_generate_batch(
    question: str,
    batch_chunks: list[dict[str, Any]],
    config: dict[str, Any],
    batch_index: int,
    batch_total: int,
    log: Any,
) -> str:
    t_w0 = time.perf_counter()
    th_name = threading.current_thread().name
    log.info(
        "llm_parallel_worker phase=start batch=%d/%d chunk_count=%d thread=%r",
        batch_index,
        batch_total,
        len(batch_chunks),
        th_name,
    )
    llm = _worker_llama(config)
    out = _generate_one_batch_nostream(
        question,
        batch_chunks,
        config,
        batch_index,
        batch_total,
        log,
        llm_model=llm,
    )
    if int(batch_index) > 1:
        out = _sanitize_parallel_later_part(out)
    log.info(
        "llm_parallel_worker phase=done batch=%d/%d answer_chars=%d duration_sec=%.4f thread=%r",
        batch_index,
        batch_total,
        len(out),
        time.perf_counter() - t_w0,
        th_name,
    )
    return out


def _generate_one_batch_nostream(
    question: str,
    batch_chunks: list[dict[str, Any]],
    config: dict[str, Any],
    batch_index: int,
    batch_total: int,
    log: Any,
    *,
    llm_model: Llama,
) -> str:
    """One non-streaming completion for a chunk batch (may run in a worker thread)."""
    st, dyn, _ = _build_prompt_fitting_llm_ctx(
        question,
        batch_chunks,
        llm_model,
        n_ctx=int(config["llm_n_ctx"]),
        llm_max_new_tokens=int(config["llm_max_new_tokens"]),
        log=log,
        batch_index=batch_index,
        batch_count=batch_total,
    )
    prompt = st + dyn
    max_tok = int(config["llm_max_new_tokens"])

    def _complete(p: str) -> Any:
        return llm_model(
            p,
            max_tokens=None if max_tok <= 0 else max_tok,
            temperature=float(config["llm_temperature"]),
            top_p=float(config["llm_top_p"]),
            repeat_penalty=float(config["llm_repeat_penalty"]),
        )

    try:
        result = _complete(prompt)
    except ValueError as e:
        err = str(e).lower()
        if "exceed" not in err or "context" not in err:
            raise
        log.warning(
            "rag_llm_ctx_retry_parallel_batch batch=%d/%d err=%s",
            batch_index,
            batch_total,
            str(e).replace("\n", " ")[:200],
        )
        prompt = _repair_prompt_on_context_overflow(question, llm_model, config, log)
        try:
            result = _complete(prompt)
        except ValueError:
            prompt = _repair_prompt_brutal(llm_model, prompt, config, log)
            result = _complete(prompt)
    return (result["choices"][0]["text"] or "").strip()


def _store_dir() -> str:
    return os.path.join(INGESTED_DIR, RAG_STORE_TENANT, RAG_STORE_INDEX_NAME)


def _image_store_dir() -> str:
    return os.path.join(INGESTED_DIR, IMAGE_RAG_STORE_TENANT, IMAGE_RAG_STORE_INDEX_NAME)


def _discover_store_dirs(index_name: str) -> list[str]:
    """Return every ``data/Ingested/<tenant>/<index_name>/`` that has a usable vector store.

    Multi-tenant retrieval: previously the backend silently picked the most recently
    modified tenant only, so a chat session could only see one store at a time. Scanning
    all tenants here means PDF and video tenants coexist in the same retrieval pool.
    """
    out: list[str] = []
    if not os.path.isdir(INGESTED_DIR):
        return out
    for tenant in sorted(os.listdir(INGESTED_DIR)):
        sd = os.path.join(INGESTED_DIR, tenant, index_name)
        if not os.path.isdir(sd):
            continue
        meta_path = os.path.join(sd, "metadata.json")
        cfg_path = os.path.join(sd, "config.json")
        if not (os.path.isfile(meta_path) and os.path.isfile(cfg_path)):
            continue
        if not (
            os.path.isfile(os.path.join(sd, "index.faiss"))
            or os.path.isfile(os.path.join(sd, "vectors.npy"))
        ):
            continue
        out.append(sd)
    return out


def _load_combined_vector_store(
    index_name: str,
) -> tuple[Optional[Any], Any, list[dict[str, Any]], dict[str, Any]]:
    """Merge every tenant's vector store under ``data/Ingested/*/<index_name>/`` into one runtime.

    Concatenates vectors and metadata in a stable tenant order, reassigns ``vector_id`` so it
    matches the merged FAISS row id, and rebuilds a single ``IndexHNSWFlat`` over the union so
    retrieval treats all tenants as one corpus. Returns ``(None, faiss_index, metadata, config)``
    matching the existing ``load_vector_store`` contract (FAISS-only path).
    """
    import faiss as _faiss
    import numpy as _np

    store_dirs = _discover_store_dirs(index_name)
    if not store_dirs:
        # Fall back to legacy single-tenant path so the existing FileNotFoundError surfaces clearly.
        return load_vector_store(os.path.join(INGESTED_DIR, RAG_STORE_TENANT, index_name))

    if len(store_dirs) == 1:
        return load_vector_store(store_dirs[0])

    all_vectors: list[Any] = []
    all_metadata: list[dict[str, Any]] = []
    base_config: dict[str, Any] | None = None
    dim_seen: int | None = None
    used_dirs: list[str] = []
    for sd in store_dirs:
        try:
            vectors, faiss_index, metadata, config = load_vector_store(sd)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("yuktra_qna.app").warning("multi_tenant_load skip store_dir=%s err=%s", sd, e)
            continue
        if not metadata:
            continue
        if vectors is None and faiss_index is not None:
            n = int(faiss_index.ntotal)
            d = int(getattr(faiss_index, "d", 0) or 0)
            if n <= 0 or d <= 0:
                continue
            recon = _np.empty((n, d), dtype=_np.float32)
            for i in range(n):
                recon[i] = _np.asarray(faiss_index.reconstruct(int(i)), dtype=_np.float32)
            vectors = recon
        if vectors is None or vectors.ndim != 2 or vectors.shape[0] == 0:
            continue
        if vectors.shape[0] != len(metadata):
            logging.getLogger("yuktra_qna.app").warning(
                "multi_tenant_load skip mismatched_shape store_dir=%s vectors=%d metadata=%d",
                sd,
                int(vectors.shape[0]),
                len(metadata),
            )
            continue
        if dim_seen is None:
            dim_seen = int(vectors.shape[1])
        elif int(vectors.shape[1]) != dim_seen:
            logging.getLogger("yuktra_qna.app").warning(
                "multi_tenant_load skip dim_mismatch store_dir=%s dim=%d expected=%d",
                sd,
                int(vectors.shape[1]),
                dim_seen,
            )
            continue
        all_vectors.append(_np.asarray(vectors, dtype=_np.float32))
        all_metadata.extend(metadata)
        if base_config is None:
            base_config = dict(config)
        used_dirs.append(sd)

    if not all_vectors or dim_seen is None:
        return load_vector_store(os.path.join(INGESTED_DIR, RAG_STORE_TENANT, index_name))

    combined = _np.vstack(all_vectors).astype(_np.float32)
    # Re-key vector_id so the dedup logic in retrieve_rag_pipeline keeps tenant rows distinct.
    for i, row in enumerate(all_metadata):
        row["vector_id"] = i

    cfg = dict(base_config or {})
    hnsw_m = int(cfg.get("faiss_hnsw_m") or 32)
    ef_construction = int(cfg.get("faiss_ef_construction") or 200)
    ef_search = int(cfg.get("faiss_ef_search") or 64)
    index = _faiss.IndexHNSWFlat(dim_seen, hnsw_m, _faiss.METRIC_INNER_PRODUCT)
    index.hnsw.efConstruction = ef_construction
    index.hnsw.efSearch = ef_search
    index.add(combined)
    cfg.update(
        {
            "vector_store_format": "faiss",
            "faiss_index_type": "IndexHNSWFlat",
            "faiss_metric": "inner_product",
            "faiss_hnsw_m": hnsw_m,
            "faiss_ef_construction": ef_construction,
            "faiss_ef_search": ef_search,
            "multi_tenant_store_dirs": used_dirs,
            "multi_tenant_total_rows": int(combined.shape[0]),
        }
    )
    cfg = attach_corpus_retrieval_vocab(cfg, all_metadata)
    logging.getLogger("yuktra_qna.app").info(
        "multi_tenant_load done index=%s tenants=%d total_rows=%d dim=%d store_dirs=%s",
        index_name,
        len(used_dirs),
        int(combined.shape[0]),
        dim_seen,
        used_dirs,
    )
    return None, index, all_metadata, cfg


def warmup_models() -> None:
    """Load runtime and run one full dummy query at startup so the first real query is fast.

    Calls ask_question("warmup") — the same code path /chat/ask uses — end-to-end:
    query embedding → FAISS → BM25 → MMR rerank → neighbor/section expansion →
    domain gate (parallel) → main LLM generation → image retrieval. The result
    is discarded; nothing is persisted (no chat history, no session row, no
    HTTP traffic — warmup runs in the FastAPI lifespan before routes accept
    requests, so it is invisible on the UI).

    Set YUKTRA_QNA_FULL_WARMUP=0 to skip the dummy query and keep only the
    lightweight model-load step (faster startup; first 1-2 real queries will
    be slow again).
    """
    _load_runtime()

    full_warmup = os.environ.get("YUKTRA_QNA_FULL_WARMUP", "1").strip().lower() not in ("0", "false", "no", "off")
    if not full_warmup:
        return

    app_logger = logging.getLogger("yuktra_qna.app")
    try:
        t0 = time.perf_counter()
        app_logger.info("warmup_models dummy_query starting")
        answer, sources, images = ask_question("warmup")
        app_logger.info(
            "warmup_models dummy_query_done duration_sec=%.2f answer_chars=%d sources=%d images=%d",
            time.perf_counter() - t0,
            len(answer or ""),
            len(sources or []),
            len(images or []),
        )
    except Exception as e:
        app_logger.warning("warmup_models dummy_query skipped (continuing): %r", e)


def _ingested_signature(index_name: str) -> tuple:
    """Hashable fingerprint of the ingested stores currently on disk for an index.

    Used to KEY the runtime caches so that adding / replacing / deleting a folder
    under ``data/Ingested/`` is picked up automatically on the next query, WITHOUT
    restarting the backend. Any change to the set of stores or to a store's
    index/metadata files (mtime + size) produces a different signature -> the
    lru_cache misses and reloads (maxsize=1 evicts the stale runtime + its RAM).
    """
    sig: list = []
    for sd in _discover_store_dirs(index_name):
        entry: list = [sd]
        for fn in ("index.faiss", "vectors.npy", "metadata.json", "config.json"):
            try:
                st = os.stat(os.path.join(sd, fn))
                entry.append((fn, int(st.st_mtime), int(st.st_size)))
            except OSError:
                pass
        sig.append(tuple(entry))
    return tuple(sig)


@lru_cache(maxsize=1)
def _load_runtime_cached(_signature: tuple) -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any], Any, Any]:
    vectors, faiss_index, metadata, config = _load_combined_vector_store(RAG_STORE_INDEX_NAME)
    cfg = merge_store_runtime_config(dict(config))
    emb_n_gpu_layers = _resolve_n_gpu_layers()
    emb_kwargs = dict(
        n_ctx=int(cfg["emb_llamacpp_n_ctx"]),
        n_threads=_runtime_threads(),
        n_batch=int(cfg["emb_llamacpp_n_batch"]),
        verbose=False,
    )
    logging.getLogger("yuktra_qna.app").info(
        "embedding_load n_gpu_layers=%s",
        emb_n_gpu_layers,
    )
    try:
        emb_model = load_llamacpp_embedding_model(
            EMBEDDING_MODEL_PATH, n_gpu_layers=emb_n_gpu_layers, **emb_kwargs
        )
    except Exception:
        if emb_n_gpu_layers == 0:
            raise
        logging.getLogger("yuktra_qna.app").warning(
            "embedding_gpu_load_failed n_gpu_layers=%s; falling back to CPU",
            emb_n_gpu_layers,
            exc_info=True,
        )
        emb_model = load_llamacpp_embedding_model(
            EMBEDDING_MODEL_PATH, n_gpu_layers=0, **emb_kwargs
        )
    llm = _create_llama_instance(cfg)
    return vectors, faiss_index, metadata, cfg, emb_model, llm


def _load_runtime() -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any], Any, Any]:
    # Re-keyed on the on-disk ingested fingerprint: drop/replace a folder under
    # data/Ingested/ and the next query reloads automatically.
    return _load_runtime_cached(_ingested_signature(RAG_STORE_INDEX_NAME))


def _format_gemma_ground_truth_judge_prompt(question: str, expected: str, model_output: str) -> str:
    return (
        "<start_of_turn>system\n"
        "You grade how well MODEL_OUTPUT matches EXPECTED (document ground truth) for the QUESTION. "
        "Output ONLY a single JSON object, no markdown, no other text. "
        'Format: {"score": <integer 1-5>, "explanation": "<one short sentence>"}\n'
        "Score: 1=completely wrong; 2=mostly wrong; 3=partially correct; 4=mostly correct; 5=fully correct in meaning.\n"
        "<end_of_turn>\n"
        "<start_of_turn>user\n"
        f"QUESTION:\n{question}\n\nEXPECTED:\n{expected}\n\nMODEL_OUTPUT:\n{model_output}\n"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
    )


def _strip_code_fences_for_judge(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t, flags=re.DOTALL)
    return t.strip()


def judge_ground_truth_gemma(question: str, expected: str, model_output: str) -> dict[str, Any]:
    """
    LLM-as-judge using the same Gemma GGUF as RAG (llama.cpp).
    Returns {"score": int, "explanation": str|None, "error": str|None}.
    """
    ref = (expected or "").strip()[:12000]
    out = (model_output or "").strip()[:8001]
    q = (question or "").strip()
    if not ref or not out:
        return {"score": None, "explanation": None, "error": "empty expected or model output"}
    _vs, _fi, _meta, _cfg, _emb, llm = _load_runtime()
    prompt = _format_gemma_ground_truth_judge_prompt(q, ref, out)
    max_tok = int(os.environ.get("YUKTRA_GEMMA_JUDGE_MAX_TOKENS", "512"))
    try:
        gen = llm(
            prompt,
            max_tokens=max_tok,
            temperature=0.0,
            top_p=1.0,
            stream=False,
        )
        text = str(((gen.get("choices") or [{}])[0].get("text") or "")).strip()
    except Exception as e:  # noqa: BLE001
        return {"score": None, "explanation": None, "error": str(e)}
    text = _strip_code_fences_for_judge(text)
    parsed: dict[str, Any] | None = None
    for blob in (
        text,
        text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else "",
    ):
        b = (blob or "").strip()
        if not b.startswith("{"):
            continue
        try:
            p = json.loads(b)
        except json.JSONDecodeError:
            continue
        if isinstance(p, dict):
            parsed = p
            break
    if not isinstance(parsed, dict):
        return {
            "score": None,
            "explanation": None,
            "error": f"unparseable judge JSON. Raw: {text[:400]!r}",
        }
    sc_raw = parsed.get("score", 0)
    try:
        sc = int(float(sc_raw)) if sc_raw is not None and sc_raw != "" else 0
    except (TypeError, ValueError):
        return {"score": None, "explanation": None, "error": f"invalid score: {parsed!r}"}
    sc = max(1, min(5, sc))
    exp = str(parsed.get("explanation", "") or "").strip()
    return {"score": sc, "explanation": exp or None, "error": None}


@lru_cache(maxsize=1)
def _load_image_runtime_cached(_signature: tuple) -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any]] | None:
    if not _discover_store_dirs(IMAGE_RAG_STORE_INDEX_NAME):
        return None
    try:
        vectors, faiss_index, metadata, config = _load_combined_vector_store(IMAGE_RAG_STORE_INDEX_NAME)
    except Exception:
        return None
    return vectors, faiss_index, metadata, dict(config)


def _load_image_runtime() -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any]] | None:
    # Same auto-reload behaviour as _load_runtime, for the image store.
    return _load_image_runtime_cached(_ingested_signature(IMAGE_RAG_STORE_INDEX_NAME))


def get_image_blob_by_uuid(image_uuid: str) -> tuple[bytes, str] | None:
    rt = _load_image_runtime()
    if not rt:
        return None
    _img_vectors, _img_faiss, img_meta, _img_cfg = rt
    uid = str(image_uuid or "").strip()
    if not uid:
        return None
    for row in img_meta:
        if str(row.get("image_uuid") or "").strip() != uid:
            continue
        b64 = str(row.get("image_base64") or "").strip()
        if not b64:
            return None
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return None
        mime = str(row.get("image_mime") or "image/png").strip() or "image/png"
        return raw, mime
    return None


def _retrieve_caption_images(
    *,
    question: str,
    retrieved_chunks: list[dict[str, Any]],
    emb_model: Any,
    text_cfg: dict[str, Any],
    app_logger: Any,
) -> list[dict[str, Any]]:
    t_img0 = time.perf_counter()
    image_rt = _load_image_runtime()
    if not image_rt:
        app_logger.info("qna_images_retrieval phase=load_runtime duration_sec=%.4f runtime=missing", time.perf_counter() - t_img0)
        return []
    img_vectors, img_faiss, img_meta, img_cfg = image_rt
    if not img_meta:
        app_logger.info("qna_images_retrieval phase=load_runtime duration_sec=%.4f runtime=empty_meta", time.perf_counter() - t_img0)
        return []
    app_logger.info(
        "qna_images_retrieval phase=load_runtime duration_sec=%.4f meta_rows=%d backend=%s",
        time.perf_counter() - t_img0,
        len(img_meta),
        "faiss" if img_faiss is not None else "numpy",
    )

    t_q0 = time.perf_counter()
    caption_query_parts: list[str] = [question.strip()]
    for ch in retrieved_chunks[:6]:
        tx = str(ch.get("text") or "").strip()
        if tx:
            caption_query_parts.append(tx[:600])
    caption_query = "\n".join([x for x in caption_query_parts if x]).strip()
    if not caption_query:
        app_logger.info(
            "qna_images_retrieval phase=build_query duration_sec=%.4f query=empty",
            time.perf_counter() - t_q0,
        )
        return []
    app_logger.info(
        "qna_images_retrieval phase=build_query duration_sec=%.4f query_chars=%d question_chars=%d retrieved_chunks=%d",
        time.perf_counter() - t_q0,
        len(caption_query),
        len(question or ""),
        len(retrieved_chunks),
    )

    t_emb0 = time.perf_counter()
    emb_style = resolve_embedding_prompt_style(
        img_cfg,
        str(img_cfg.get("embedding_model") or text_cfg.get("embedding_model") or ""),
    )
    qvec = embed_fused_query_for_retrieval(
        caption_query,
        None,
        emb_model,
        device="cpu",
        max_length=int(img_cfg.get("embedding_max_length") or text_cfg["embedding_max_length"]),
        embedding_prompt_style=emb_style,
        retrieval_query_clean_enabled=bool(text_cfg.get("retrieval_query_clean_enabled", True)),
        retrieval_generic_terms=resolve_retrieval_generic_terms(text_cfg),
    )
    app_logger.info(
        "qna_images_retrieval phase=query_embed duration_sec=%.4f emb_style=%s",
        time.perf_counter() - t_emb0,
        emb_style,
    )
    t_search0 = time.perf_counter()
    k = max(1, int(os.environ.get("IMAGE_RETRIEVAL_TOP_K", "8")))
    pool = min(len(img_meta), k * 3)
    if img_faiss is not None:
        idx, _scores = topk_search_faiss(img_faiss, qvec, pool)
    else:
        idx, _scores = topk_search(img_vectors, qvec, pool)
    app_logger.info(
        "qna_images_retrieval phase=vector_search duration_sec=%.4f top_k=%d pool=%d returned=%d",
        time.perf_counter() - t_search0,
        k,
        pool,
        len(idx),
    )

    t_filter0 = time.perf_counter()
    out: list[dict[str, Any]] = []
    scored: list[tuple[float, dict[str, Any]]] = []
    fallback_scored: list[tuple[float, float, dict[str, Any]]] = []
    seen: set[str] = set()
    min_match = float(os.environ.get("IMAGE_CAPTION_MATCH_MIN", "0.6"))
    # Default higher so multi-figure sections can return more than one relevant image.
    max_out = int(os.environ.get("IMAGE_RESPONSE_MAX", "4"))
    ql = re.sub(r"\s+", " ", (question or "").lower()).strip()
    explicit_image_intent = any(
        t in ql for t in ("show image", "give image", "display image", "image of", "picture of", "figure of")
    )
    strict_min_match = float(os.environ.get("IMAGE_CAPTION_MATCH_MIN_STRICT", "0.75"))
    top_doc = ""
    if retrieved_chunks:
        top_doc = str(retrieved_chunks[0].get("doc_name") or "").strip()
    same_doc_chunks = [
        ch for ch in retrieved_chunks if str(ch.get("doc_name") or "").strip() == top_doc
    ] if top_doc else list(retrieved_chunks)
    chunk_ctx = "\n".join(str(ch.get("text") or "")[:800] for ch in same_doc_chunks[:4]).strip()
    qk = _query_keywords(question)
    scanned = 0
    dropped_no_overlap = 0
    dropped_doc_mismatch = 0
    dropped_low_score = 0
    dropped_strict = 0
    dropped_bad_image = 0
    # Caption-less images (base64-blob / empty caption) have no text to match, so we
    # collect them here and rank by image-vector similarity to fill any free slots.
    semantic_pool: list[tuple[float, dict]] = []
    for pos, raw_i in enumerate(idx):
        scanned += 1
        row = img_meta[int(raw_i)]
        uid = str(row.get("image_uuid") or "").strip()
        b64 = str(row.get("image_base64") or "").strip()
        raw_caption = str(row.get("caption") or "").strip()
        caption = _clean_image_caption(raw_caption)              # base64-blob caption -> ""
        row_text = _clean_image_caption(str(row.get("text") or "").strip())
        img_doc = str(row.get("doc_name") or "").strip()
        if not uid or not b64 or uid in seen:
            continue
        # Visual size gate always applies (drops tiny logo / watermark / title crops).
        bad_visual_reason = _image_visual_filter_reason(uid, b64)
        if bad_visual_reason:
            dropped_bad_image += 1
            app_logger.info(
                "qna_images_retrieval dropped_bad_image uid=%s reason=%s caption=%r doc=%s",
                uid, bad_visual_reason, raw_caption[:160], img_doc,
            )
            continue
        # Keep image retrieval anchored to the same document as text retrieval.
        if top_doc and img_doc and img_doc != top_doc:
            dropped_doc_mismatch += 1
            continue
        # Caption-less image (its caption/text was a raw base64 blob from ingestion, or
        # genuinely empty): no text to lexically match -> rank it by image-vector
        # similarity via the semantic pool instead of dropping it as a "bad caption".
        if not caption and not row_text:
            try:
                sem = float(_scores[pos])
            except Exception:
                sem = 0.0
            semantic_pool.append((sem, {
                "image_uuid": uid,
                "caption": "",
                "image_mime": str(row.get("image_mime") or "image/png"),
                "doc_name": str(row.get("doc_name") or ""),
                "image_base64": b64,
            }))
            continue
        # A real-but-useless TEXT caption (URL / "User's Manual" / page-break) is still junk.
        if _is_bad_image_caption(caption):
            dropped_bad_image += 1
            app_logger.info(
                "qna_images_retrieval dropped_bad_image uid=%s reason=%s caption=%r doc=%s",
                uid, "bad_caption", raw_caption[:160], img_doc,
            )
            continue
        candidate_text = " ".join(x for x in (caption, row_text) if x).strip()
        if not candidate_text:
            continue
        # Primary lexical relevance should track what the user asked, with chunk-context
        # support as a secondary signal to ensure images are tied to retrieved text.
        score_q = _caption_match_score(candidate_text, question)
        score_ctx = _caption_match_score(candidate_text, caption_query)
        score_chunk = _caption_match_score(candidate_text, chunk_ctx) if chunk_ctx else 0.0
        score = max(score_q, score_ctx)
        # Guardrail: if the user has concrete keywords, require a minimum synonym-aware overlap.
        if qk:
            ck = _query_keywords(candidate_text)
            overlap = _synonym_overlap_count(qk, ck)
            # Be less aggressive here; requiring 2+ overlaps often suppresses valid sibling
            # images from the same section whose captions use slightly different phrasing.
            need = 1
            if overlap < need:
                dropped_no_overlap += 1
                continue
            overlap_ratio = float(overlap) / float(max(1, len(qk)))
        else:
            overlap_ratio = 0.0
        if explicit_image_intent and qk:
            # For explicit image asks, require high lexical fidelity to avoid "nearest but wrong" photos.
            if overlap_ratio < 0.75 or score_q < strict_min_match:
                # Controlled fallback: if strict mode finds nothing, allow one best candidate
                # that still has meaningful overlap and semantic proximity.
                sem = 0.0
                try:
                    sem = float(_scores[pos])
                except Exception:
                    sem = 0.0
                if overlap_ratio >= 0.45 and max(score_q, score_chunk, score_ctx) >= min_match:
                    fallback_scored.append(
                        (
                            max(score_q, score_ctx, score_chunk),
                            sem,
                            {
                                "image_uuid": uid,
                                "caption": caption,
                                "image_mime": str(row.get("image_mime") or "image/png"),
                                "doc_name": str(row.get("doc_name") or ""),
                                # Include pixels so the Streamlit UI can use data: URIs. UUID-only
                                # + /images/{id} breaks when the browser is not on the API host
                                # (e.g. remote :8501 with YUKTRA_QNA_API_BASE still 127.0.0.1:8008).
                                "image_base64": b64,
                            },
                        )
                    )
                else:
                    dropped_strict += 1
                continue
        if score < min_match or max(score_q, score_chunk) < 0.45:
            dropped_low_score += 1
            continue
        seen.add(uid)
        scored.append(
            (
                score,
                {
                    "image_uuid": uid,
                    "caption": caption,
                    "image_mime": str(row.get("image_mime") or "image/png"),
                    "doc_name": str(row.get("doc_name") or ""),
                    "image_base64": b64,
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    if explicit_image_intent and not scored and fallback_scored:
        # Keep strict behavior by default, but return top fallback candidates (not only one)
        # so multi-image answers from the same section can still surface together.
        fallback_scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        take_fb = max(1, min(max_out, k))
        for fb in fallback_scored[:take_fb]:
            scored.append((fb[0], fb[2]))
    app_logger.info(
        "qna_images_retrieval phase=filter_rank duration_sec=%.4f scanned=%d kept=%d fallback_candidates=%d dropped_doc=%d dropped_overlap=%d dropped_score=%d dropped_strict=%d dropped_bad_image=%d explicit_intent=%s",
        time.perf_counter() - t_filter0,
        scanned,
        len(scored),
        len(fallback_scored),
        dropped_doc_mismatch,
        dropped_no_overlap,
        dropped_low_score,
        dropped_strict,
        dropped_bad_image,
        explicit_image_intent,
    )
    # Fill any remaining image slots with caption-less (base64) figures from the
    # answer's document, ranked by image-vector similarity. Lexically-matched images
    # keep priority (they are already at the front of `scored`); these only top up the
    # rest so valid diagrams/photos that lost their caption during ingestion still show.
    cap = max(1, min(max_out, k))
    if len(scored) < cap and semantic_pool:
        semantic_pool.sort(key=lambda x: x[0], reverse=True)
        for sem, img in semantic_pool:
            if len(scored) >= cap:
                break
            if img["image_uuid"] in seen:
                continue
            seen.add(img["image_uuid"])
            scored.append((sem, img))
    for _, img in scored[: max(1, min(max_out, k))]:
        out.append(img)
    app_logger.info(
        "qna_images_retrieval image_tenant=%s image_index=%s query_chars=%d returned=%d min_match=%.2f max_out=%d total_duration_sec=%.4f",
        IMAGE_RAG_STORE_TENANT,
        IMAGE_RAG_STORE_INDEX_NAME,
        len(caption_query),
        len(out),
        min_match,
        max_out,
        time.perf_counter() - t_img0,
    )
    return out


def _query_keywords(query: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", (query or "").lower())
    out: set[str] = set()
    for t in toks:
        if len(t) < 3:
            continue
        if t in _CAPTION_STOPWORDS:
            continue
        out.add(t)
    return out


def _tokens_synonym_match(a: str, b: str) -> bool:
    return bool(_synonym_closure(a) & _synonym_closure(b))


def _synonym_overlap_count(a_tokens: set[str], b_tokens: set[str]) -> int:
    if not a_tokens or not b_tokens:
        return 0
    return sum(1 for a in a_tokens if any(_tokens_synonym_match(a, b) for b in b_tokens))


def _caption_match_score(caption: str, query_text: str) -> float:
    """Lexical gate for image candidates: use full retrieval query (question + chunks), symmetric
    coverage, and synonym groups so strict 0.6 thresholds still allow strong partial matches."""
    c = (caption or "").strip().lower()
    if not c:
        return 0.0
    if c in ("image", "img", "picture", "figure"):
        return 0.0
    qk = _query_keywords(query_text)
    if not qk:
        return 1.0
    ck = _query_keywords(c)
    if not ck:
        ctoks = set(re.findall(r"[a-z0-9]+", c))
        if not ctoks:
            return 0.0
        ck = {t for t in ctoks if len(t) >= 3 and t not in _CAPTION_STOPWORDS}

    matched_q = sum(
        1 for q in qk if any(_tokens_synonym_match(q, cap_t) for cap_t in ck)
    )
    matched_c = sum(
        1 for cap_t in ck if any(_tokens_synonym_match(cap_t, q) for q in qk)
    )
    if matched_q or matched_c:
        return max(
            float(matched_q) / float(len(qk)),
            float(matched_c) / float(len(ck)),
        )
    qnorm = re.sub(r"\s+", " ", (query_text or "").strip().lower())
    return 1.0 if len(qnorm) >= 6 and qnorm in c else 0.0


def _is_explicit_image_intent_query(query: str) -> bool:
    ql = re.sub(r"\s+", " ", (query or "").lower()).strip()
    if not ql:
        return False
    return any(
        t in ql
        for t in (
            "show image",
            "give image",
            "display image",
            "image of",
            "picture of",
            "figure of",
            "show diagram",
            "display diagram",
            "show figure",
            "display figure",
        )
    )


def _contains_out_of_domain_boilerplate(text: str) -> bool:
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return "i'm an equipment intelligence assistant" in t or "i am an equipment intelligence assistant" in t


def _fallback_answer_from_retrieved_chunks(
    question: str,
    retrieved: list[dict[str, Any]],
) -> str:
    if not retrieved:
        return (
            "I found this topic in the manual, but I could not extract a clean answer. "
            "Please rephrase the question or ask for a specific step/section."
        )
    first = retrieved[0]
    doc = str(first.get("doc_name") or "the manual").strip()
    page_num = _source_page_number(first)
    page = str(page_num) if page_num is not None else ""
    if page in ("", "?", "None"):
        page = ""
    sec = " ".join(str(first.get("section_path_str", "") or "").split())
    if sec in ("", "None"):
        sec = ""
    tx = str(first.get("text") or "").strip()
    tx = re.sub(r"\s+", " ", tx)
    if not tx:
        return f"I found relevant content in {doc}, but the extracted text is limited. Please ask a more specific question."
    snippet = tx[:420].rstrip()
    if len(tx) > 420:
        snippet += "..."
    ref_parts: list[str] = []
    if page:
        ref_parts.append(f"page {page}")
    if sec:
        ref_parts.append(f"section {sec}")
    ref = ", ".join(ref_parts)
    return f"From {doc} ({ref}): {snippet}" if ref else f"From {doc}: {snippet}"


def _source_page_number(ch: dict[str, Any]) -> int | None:
    """Best-effort page extraction across legacy/new metadata keys."""
    for key in ("page_number", "page", "page_no", "page_num", "source_page"):
        pn = _parse_page_number(ch.get(key))
        if pn is not None:
            return pn
    return None


def _chunk_log_line(ch: dict[str, Any]) -> str:
    doc = str(ch.get("doc_name", "") or "")
    vec_id = ch.get("vector_id", "?")
    chunk_idx = ch.get("chunk_index", "?")
    page = ch.get("page_number", "?")
    sec = " ".join(str(ch.get("section_path_str", "") or "").split())
    if len(sec) > 80:
        sec = sec[:77] + "..."
    txt = " ".join(str(ch.get("text", "") or "").split())
    # Make logs more useful by showing more of each chunk's beginning.
    head_chars = 120
    tail_chars = 40
    if len(txt) > head_chars + tail_chars + 10:
        txt = txt[:head_chars] + " ... " + txt[-tail_chars:]
    return (
        f"doc={doc} page={page} chunk={chunk_idx} vec={vec_id}"
        + (f" section={sec}" if sec else "")
        + f' text="{txt}"'
    )


def _batch_progress_message(batch_no: int, batch_total: int, batch_chunks: list[dict[str, Any]]) -> str:
    pages: list[str] = []
    seen_pages: set[str] = set()
    for ch in batch_chunks:
        p = str(ch.get("page_number") or "").strip()
        if not p or p in ("?", "None") or p in seen_pages:
            continue
        seen_pages.add(p)
        pages.append(p)
        if len(pages) >= 4:
            break
    sec = ""
    for ch in batch_chunks:
        raw = " ".join(str(ch.get("section_path_str", "") or "").split())
        if raw:
            sec = raw
            break
    parts = ["Analyzing relevant manual sections"]
    if pages:
        parts.append(f"pages {', '.join(pages)}")
    if sec:
        if len(sec) > 70:
            sec = sec[:67] + "..."
        parts.append(f"section {sec}")
    return " | ".join(parts)


def _log_retrieved_chunks(app_logger: Any, label: str, chunks: list[dict[str, Any]], *, max_items: int = 16) -> None:
    app_logger.info("[CHUNK_LOG] %s count=%d", label, len(chunks))
    cap = min(len(chunks), max(0, int(max_items)))
    for i, ch in enumerate(chunks[:cap], start=1):
        app_logger.info("[CHUNK_LOG] %s #%d %s", label, i, _chunk_log_line(ch))
    if len(chunks) > cap:
        app_logger.info("[CHUNK_LOG] %s ... %d more omitted (max_items=%d)", label, len(chunks) - cap, max_items)


def _resolve_doc_path(doc_name: str | None, stored_path: str | None, metadata: list[dict[str, Any]]) -> str | None:
    def _search_ingested(filename: str) -> str | None:
        if not filename or not os.path.isdir(INGESTED_DIR):
            return None
        for machine in sorted(os.listdir(INGESTED_DIR)):
            candidate = os.path.join(INGESTED_DIR, machine, "documents", filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    name = (doc_name or "").strip()
    if name:
        found = _search_ingested(name)
        if found:
            return found
        found = _search_ingested(os.path.basename(name))
        if found:
            return found
    if stored_path:
        sp = str(stored_path).strip()
        if sp and os.path.isfile(sp):
            return sp
    if not name and stored_path:
        for row in metadata:
            if row.get("doc_path") == stored_path and row.get("doc_name"):
                name = str(row.get("doc_name")).strip()
                break
        if name:
            found = _search_ingested(name)
            if found:
                return found
            found = _search_ingested(os.path.basename(name))
            if found:
                return found
    return None


def _format_answer_for_display(answer: str) -> str:
    text = (answer or "").strip()
    text = _strip_rag_disclaimer_lines(text)
    # Remove markdown horizontal-rule lines that visually split one answer bubble.
    text = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", text)
    text = normalize_runon_bullet_lines(text)
    text = ensure_blank_line_before_key_points(text)
    text = re.sub(r"(?i)the correct answer.*?:", "", text)
    lines_out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(\s*)- (\S.*)$", line)
        if m:
            lines_out.append(f"{m.group(1)}\u2022 {m.group(2)}")
        else:
            lines_out.append(line.replace("●", "\u2022"))
    # Remove boilerplate denial line when mixed into an otherwise relevant answer.
    # Keep it only when the whole response is the denial itself.
    denial_lines = {ln.strip().lower() for ln in OUT_OF_DOMAIN_REPLY.splitlines() if ln.strip()}
    non_empty = [ln for ln in lines_out if ln.strip()]
    if len(non_empty) > 1:
        filtered: list[str] = []
        for ln in lines_out:
            if ln.strip() and ln.strip().lower() in denial_lines:
                continue
            filtered.append(ln)
        lines_out = filtered
    text = "\n".join(lines_out)
    if "\n" not in text and "?" in text:
        qpos = text.rfind("?")
        if qpos != -1 and (len(text) - qpos) <= 180:
            boundary = max(text.rfind("\n\n", 0, qpos), text.rfind("\n", 0, qpos))
            if boundary == -1:
                boundary = max(text.rfind(". ", 0, qpos), text.rfind("! ", 0, qpos))
            if boundary == -1:
                boundary = max(text.rfind(".\n", 0, qpos), text.rfind("!\n", 0, qpos))
            start = boundary + 1 if boundary != -1 else 0
            if start > 0 and start < qpos and (qpos - start) <= 260:
                main = text[:start].rstrip()
                qsent = text[start:].lstrip()
                text = f"{main}\n\n{qsent}".strip()
    if "\n" not in text and len(text) >= 320:
        text = re.sub(r"(?i)\s+(adjustment to the\s+\w+\s+manipulator\s*:)", r"\n\n\1", text)
        text = re.sub(r"(?i)\s+(adjustment to automatic tube feeder\s*:)", r"\n\n\1", text)
        text = re.sub(r"(?m)(^|[:;])\s*(\d+\))\s+", r"\1\n\2 ", text)
        text = re.sub(r"(?m)(^|[:;])\s*(\d{1,2}\.)\s+(?!\d)", r"\1\n\2 ", text)
        text = re.sub(r"(?im)(^|[:;])\s*([a-h]\.)\s+", r"\1\n  \2 ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"(?<!\n)\n(?!\n)", "  \n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_MIN_RAG_CHUNK_CHARS = 200


def _llm_prompt_token_count(llm_model: Any, prompt: str) -> int:
    return len(llm_model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True))


def _truncate_prompt_to_max_tokens(llm_model: Any, prompt: str, max_tokens: int, log: Any) -> str:
    max_tokens = max(64, int(max_tokens))
    toks = llm_model.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
    if len(toks) <= max_tokens:
        return prompt
    log.warning(
        "rag_prompt_token_truncated tokens=%d->%d (llama context cap)",
        len(toks),
        max_tokens,
    )
    cut = toks[:max_tokens]
    raw = llm_model.detokenize(cut, special=True)
    return raw.decode("utf-8", errors="replace")


def _truncate_prompt_pair_to_max_tokens(
    llm_model: Any,
    static_s: str,
    dynamic_s: str,
    max_tokens: int,
    log: Any,
) -> tuple[str, str]:
    max_tokens = max(64, int(max_tokens))
    full = static_s + dynamic_s
    toks = llm_model.tokenize(full.encode("utf-8"), add_bos=False, special=True)
    if len(toks) <= max_tokens:
        return static_s, dynamic_s
    static_toks = llm_model.tokenize(static_s.encode("utf-8"), add_bos=False, special=True)
    n_static = len(static_toks)
    if n_static >= max_tokens:
        log.warning(
            "rag_prompt_token_truncated static_prefix_tokens=%d->%d (falling back to whole-prompt trim)",
            n_static,
            max_tokens,
        )
        truncated = _truncate_prompt_to_max_tokens(llm_model, full, max_tokens, log)
        return "", truncated
    lo, hi = 0, len(dynamic_s)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = static_s + dynamic_s[:mid]
        n = len(llm_model.tokenize(cand.encode("utf-8"), add_bos=False, special=True))
        if n <= max_tokens:
            best = dynamic_s[:mid]
            lo = mid + 1
        else:
            hi = mid - 1
    log.warning(
        "rag_prompt_dynamic_truncated chars=%d->%d (llama context cap, static prefix preserved)",
        len(dynamic_s),
        len(best),
    )
    return static_s, best


def _uniform_scale_chunk_texts(capped: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in capped:
        nc = dict(c)
        t = str(nc.get("text", "") or "")
        if not t.strip():
            out.append(nc)
            continue
        nlen = max(_MIN_RAG_CHUNK_CHARS, int(len(t) * factor))
        nc["text"] = t[:nlen]
        out.append(nc)
    return out


def _build_prompt_fitting_llm_ctx(
    question: str,
    capped: list[dict[str, Any]],
    llm_model: Any,
    *,
    n_ctx: int,
    llm_max_new_tokens: int,
    log: Any,
    batch_index: Optional[int] = None,
    batch_count: Optional[int] = None,
    previous_answer_draft: Optional[str] = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    capped = [dict(c) for c in capped]
    n_ctx_i = max(512, int(n_ctx))
    mnt = int(llm_max_new_tokens)
    gen_floor = 512 if mnt <= 0 else min(mnt + 128, n_ctx_i // 2)
    slack = 256
    hard_cap = max(128, n_ctx_i - gen_floor - slack)
    query_language = _detect_query_language(question)
    static = build_rag_prompt_static()

    def _dyn(q: str, chunks: list[dict[str, Any]]) -> str:
        return build_rag_prompt_dynamic(
            q,
            chunks,
            batch_index=batch_index,
            batch_count=batch_count,
            previous_answer_draft=previous_answer_draft,
            query_language=query_language,
        )

    def fits(full: str) -> bool:
        return _llm_prompt_token_count(llm_model, full) <= hard_cap

    start_n = len(capped)
    while capped:
        dynamic = _dyn(question, capped)
        if fits(static + dynamic):
            break
        if len(capped) > 1:
            shrunk_to_fit = False
            for factor in (0.92, 0.85, 0.75, 0.65, 0.55):
                trial = _uniform_scale_chunk_texts(capped, factor)
                dyn2 = _dyn(question, trial)
                if fits(static + dyn2):
                    capped = trial
                    shrunk_to_fit = True
                    log.info(
                        "rag_prompt_uniform_shrink factor=%.2f chunks=%d (fit n_ctx)",
                        factor,
                        len(capped),
                    )
                    break
            if shrunk_to_fit:
                continue
            capped.pop()
            continue
        text = str(capped[0].get("text", "") or "")
        if len(text) <= _MIN_RAG_CHUNK_CHARS:
            break
        lo, hi = _MIN_RAG_CHUNK_CHARS, len(text)
        best_prefix = text[:_MIN_RAG_CHUNK_CHARS]
        while lo <= hi:
            mid = (lo + hi) // 2
            ch = dict(capped[0])
            ch["text"] = text[:mid]
            dyn2 = _dyn(question, [ch])
            if fits(static + dyn2):
                best_prefix = text[:mid]
                lo = mid + 1
            else:
                hi = mid - 1
        capped[0] = dict(capped[0])
        capped[0]["text"] = best_prefix
        log.info(
            "rag_prompt_single_chunk_prefix_trim chars=%d->%d (fit n_ctx)",
            len(text),
            len(best_prefix),
        )
        break

    dynamic = _dyn(question, capped)
    static_kept, dynamic_kept = _truncate_prompt_pair_to_max_tokens(
        llm_model, static, dynamic, hard_cap, log
    )
    if len(capped) < start_n:
        log.warning(
            "rag_prompt_shrunk context_chunks=%d->%d hard_cap_tokens=%d (n_ctx=%d)",
            start_n,
            len(capped),
            hard_cap,
            n_ctx_i,
        )
    return static_kept, dynamic_kept, capped


def _repair_prompt_on_context_overflow(
    question: str,
    llm_model: Any,
    config: dict[str, Any],
    log: Any,
) -> str:
    n_ctx_i = max(512, int(config["llm_n_ctx"]))
    emergency_cap = max(256, n_ctx_i - 512)
    _st = build_rag_prompt_static()
    _dy = build_rag_prompt_dynamic(question, [], query_language=_detect_query_language(question))
    static_prefix, dynamic_suffix = _truncate_prompt_pair_to_max_tokens(
        llm_model, _st, _dy, emergency_cap, log
    )
    return static_prefix + dynamic_suffix


def _repair_prompt_brutal(llm_model: Any, prompt: str, config: dict[str, Any], log: Any) -> str:
    n_ctx_i = max(512, int(config["llm_n_ctx"]))
    emergency_cap = max(256, n_ctx_i - 512)
    return _truncate_prompt_to_max_tokens(llm_model, prompt, max(128, emergency_cap // 2), log)


def ask_question_stream_events(question: str) -> Iterator[dict[str, Any]]:
    """Yield dict events for SSE: ``delta``, ``images``, ``done`` (answer + sources + images), or ``error``."""
    app_logger = get_logger("yuktra_qna.app", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)
    configure_rag_file_logging(log_dir=os.path.join(DATA_DIR, "logs"))
    t0 = time.perf_counter()
    last = [t0]

    def mark(step: str, **kv: Any) -> None:
        now = time.perf_counter()
        suffix = (" " + " ".join(f"{k}={v}" for k, v in kv.items())) if kv else ""
        app_logger.info(
            "qna_stream step=%s delta_sec=%.4f cum_sec=%.4f%s",
            step,
            now - last[0],
            now - t0,
            suffix,
        )
        last[0] = now

    log_process_start(app_logger, "qna_stream", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
    try:
        mark("enter", question_chars=len(question or ""))
        vectors, faiss_index, metadata, config, emb_model, llm_model = _load_runtime()
        mark("load_runtime_done")
        domain_gate_enabled = os.environ.get("YUKTRA_QNA_DOMAIN_GATE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
        if domain_gate_enabled:
            domain_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yuktra_qna_domain")
            domain_future = domain_pool.submit(_llm_domain_decision, question, llm_model, config, app_logger)
        else:
            domain_pool = None
            domain_future = None

        emb_prompt_style = resolve_embedding_prompt_style(
            config, str(config.get("embedding_model") or "local-embedding-gguf")
        )
        retrieval_generic_terms = resolve_retrieval_generic_terms(config)
        query_vec = embed_fused_query_for_retrieval(
            question,
            None,
            emb_model,
            device="cpu",
            max_length=int(config["embedding_max_length"]),
            embedding_prompt_style=emb_prompt_style,
            retrieval_query_clean_enabled=bool(config.get("retrieval_query_clean_enabled", True)),
            retrieval_generic_terms=retrieval_generic_terms,
        )
        mark("query_embedding_done")

        retrieved, capped, _ = retrieve_rag_pipeline(
            question,
            query_vec,
            vectors,
            metadata,
            faiss_index=faiss_index,
            top_k=int(config["top_k"]),
            retrieval_pool_k=int(config["retrieval_pool_k"]),
            mmr_k=int(config["mmr_k"]),
            mmr_lambda=float(config["mmr_lambda"]),
            max_context_chars=int(config["max_context_chars"]),
            restrict_top_document=bool(config["restrict_top_document"]),
            bm25_weight=float(config["bm25_weight"]),
            initial_pool_multiplier=int(config["initial_pool_multiplier"]),
            max_chunks_for_prompt=int(config["max_chunks_for_prompt"]),
            rrf_linear_blend=float(config["rrf_linear_blend"]),
            hybrid_alpha_semantic=float(config["hybrid_alpha_semantic"]),
            hybrid_rerank_enabled=bool(config.get("hybrid_rerank_enabled", False)),
            min_hybrid_rerank_score=float(config["min_hybrid_rerank_score"]),
            top_doc_page_window=int(config["top_doc_page_window"]),
            top_doc_chunk_neighbor_radius=int(config["top_doc_chunk_neighbor_radius"]),
            top_doc_chunk_neighbors_before=int(config.get("top_doc_chunk_neighbors_before", 0)),
            top_doc_chunk_neighbors_after=int(config.get("top_doc_chunk_neighbors_after", 0)),
            top_doc_section_expand=bool(config.get("top_doc_section_expand", True)),
            retrieval_query_clean_enabled=bool(config.get("retrieval_query_clean_enabled", True)),
            retrieval_generic_terms=retrieval_generic_terms,
        )
        mark("retrieval_done", retrieved_chunks=len(retrieved), capped_chunks=len(capped))
        _log_retrieved_chunks(app_logger, "qna_stream retrieved_chunks_detail", retrieved)
        _log_retrieved_chunks(app_logger, "qna_stream capped_chunks_detail", capped)
        # Domain decision runs in parallel with retrieval work; wait only when needed.
        if domain_future is None:
            domain_relevant = True
        else:
            domain_relevant = bool(domain_future.result())
            if not domain_relevant and _retrieved_context_overrides_domain_gate(capped, config):
                app_logger.info(
                    "qna_stream domain_gate overridden_by_video_transcript_context capped_chunks=%d",
                    len(capped),
                )
                domain_relevant = True
        if not domain_relevant:
            mark("out_of_domain_skip_llm")
            yield {"type": "done", "answer": OUT_OF_DOMAIN_REPLY, "sources": [], "images": []}
            return

        first_bs, rest_bs, para_workers = _parallel_batch_settings(config)
        batches = _chunk_batches_head_then_rest(list(capped), first_bs, rest_bs)
        use_parallel = para_workers > 0 and len(batches) > 1
        if not use_parallel:
            app_logger.info(
                "qna_stream llm_parallel phase=skipped logical_batches=%d first_batch_cfg=%d rest_batch_cfg=%d "
                "max_workers_cfg=%d reason=%s",
                len(batches),
                first_bs,
                rest_bs,
                para_workers,
                "max_workers_zero" if para_workers <= 0 else "single_logical_batch",
            )

        max_tok = int(config["llm_max_new_tokens"])
        max_tok_label = "unlimited" if max_tok <= 0 else str(max_tok)
        t_llm = time.perf_counter()
        pieces: list[str] = []
        first_llm_token_logged = False
        prompt_for_log = ""

        def _stream_llm(p: str):
            return llm_model(
                p,
                max_tokens=None if max_tok <= 0 else max_tok,
                temperature=float(config["llm_temperature"]),
                top_p=float(config["llm_top_p"]),
                repeat_penalty=float(config["llm_repeat_penalty"]),
                stream=True,
            )

        def _consume_stream(p: str) -> Iterator[dict[str, Any]]:
            nonlocal first_llm_token_logged
            stream = _stream_llm(p)
            for chunk in stream:
                if not isinstance(chunk, dict):
                    continue
                ch0 = (chunk.get("choices") or [{}])[0]
                delta = (ch0.get("text") or "") if isinstance(ch0, dict) else ""
                if delta:
                    step = max(8, int(_SSE_UI_DELTA_MAX_CHARS))
                    for off in range(0, len(delta), step):
                        piece = delta[off : off + step]
                        if not first_llm_token_logged:
                            prev = pipeline_log_preview(piece, max_chars=120)
                            app_logger.info(
                                "qna_stream first_llm_token sec_after_llm_call_start=%.4f sec_after_stream_start=%.4f preview=%r",
                                time.perf_counter() - t_llm,
                                time.perf_counter() - t0,
                                prev,
                            )
                            first_llm_token_logged = True
                        pieces.append(piece)
                        yield {"type": "delta", "text": piece}

        if not use_parallel:
            static_prefix, dynamic_suffix, capped = _build_prompt_fitting_llm_ctx(
                question,
                capped,
                llm_model,
                n_ctx=int(config["llm_n_ctx"]),
                llm_max_new_tokens=int(config["llm_max_new_tokens"]),
                log=app_logger,
            )
            prompt = static_prefix + dynamic_suffix
            prompt_for_log = prompt
            mark(
                "prompt_built",
                prompt_chars=len(prompt),
                llm_max_new_tokens=max_tok_label,
            )
            try:
                for ev in _consume_stream(prompt):
                    yield ev
            except ValueError as e:
                err = str(e).lower()
                if "exceed" not in err or "context" not in err:
                    raise
                app_logger.warning(
                    "rag_llm_ctx_retry_stream after ValueError: %s",
                    str(e).replace("\n", " ")[:200],
                )
                prompt = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
                prompt_for_log = prompt
                try:
                    for ev in _consume_stream(prompt):
                        yield ev
                except ValueError:
                    prompt = _repair_prompt_brutal(llm_model, prompt, config, app_logger)
                    prompt_for_log = prompt
                    for ev in _consume_stream(prompt):
                        yield ev
        else:
            n_b = len(batches)
            pool_w = min(para_workers, n_b - 1)
            mark(
                "llm_parallel_plan",
                batches=n_b,
                first_batch_chunk_size=first_bs,
                rest_batch_chunk_size=rest_bs,
                background_worker_threads=pool_w,
                total_chunk_rows=sum(len(b) for b in batches),
            )
            app_logger.info(
                "qna_stream llm_parallel phase=plan batches=%d first_batch_chunks=%d rest_batch_chunks=%d "
                "pool_workers=%d chunks_per_batch=%s total_chunks=%d",
                n_b,
                first_bs,
                rest_bs,
                pool_w,
                [len(b) for b in batches],
                sum(len(b) for b in batches),
            )
            prompt_chars_acc = 0
            mark(
                "prompt_built",
                prompt_chars=prompt_chars_acc,
                llm_max_new_tokens=max_tok_label,
                parallel_batches=n_b,
            )
            batch_parts: list[str] = []
            rolling_answer_ctx = ""
            for bi, batch_chunks in enumerate(batches, start=1):
                yield {
                    "type": "progress",
                    "text": _batch_progress_message(bi, n_b, batch_chunks),
                }
                static_prefix_b, dynamic_suffix_b, _ = _build_prompt_fitting_llm_ctx(
                    question,
                    batch_chunks,
                    llm_model,
                    n_ctx=int(config["llm_n_ctx"]),
                    llm_max_new_tokens=int(config["llm_max_new_tokens"]),
                    log=app_logger,
                    batch_index=bi,
                    batch_count=n_b,
                    previous_answer_draft=rolling_answer_ctx,
                )
                prompt_b = static_prefix_b + dynamic_suffix_b
                prompt_for_log = prompt_b
                prompt_chars_acc += len(prompt_b)
                app_logger.info(
                    "qna_stream llm_parallel phase=batch_stream_start batch=%d/%d chunk_count=%d prompt_chars=%d role=main",
                    bi,
                    n_b,
                    len(batch_chunks),
                    len(prompt_b),
                )
                t_b = time.perf_counter()
                part_buf: list[str] = []
                raw_buf: list[str] = []  # raw LLM tokens before dedup, used for redundancy comparison
                sep_emitted = False
                # For batch 2+, buffer delta events and decide after generation whether to flush
                # or discard them (redundancy check prevents repeated answers from reaching the UI).
                pending_events: list[dict] = []
                prev_ctx_for_dedupe = rolling_answer_ctx if bi > 1 else ""
                suppress_repeat_prefix = bool(prev_ctx_for_dedupe)
                repeat_match_idx = 0
                suppressed_prefix_chars = 0
                try:
                    for ev in _consume_stream(prompt_b):
                        txt = str(ev.get("text") or "")
                        if bi > 1 and txt:
                            raw_buf.append(txt)
                        emit_txt = txt
                        if bi > 1 and txt and suppress_repeat_prefix:
                            out_chars: list[str] = []
                            for ch in txt:
                                if repeat_match_idx < len(prev_ctx_for_dedupe):
                                    if ch == prev_ctx_for_dedupe[repeat_match_idx]:
                                        repeat_match_idx += 1
                                        suppressed_prefix_chars += 1
                                        continue
                                    # Match broke — discard matched prefix, don't re-emit it.
                                    suppress_repeat_prefix = False
                                    out_chars.append(ch)
                                else:
                                    if ch.isspace() and not out_chars:
                                        suppressed_prefix_chars += 1
                                        continue
                                    suppress_repeat_prefix = False
                                    out_chars.append(ch)
                            emit_txt = "".join(out_chars)
                        if bi > 1 and emit_txt and not sep_emitted:
                            pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
                            sep_emitted = True
                        if emit_txt:
                            part_buf.append(emit_txt)
                            if bi == 1:
                                yield {"type": "delta", "text": emit_txt}
                            else:
                                pending_events.append({"type": "delta", "text": emit_txt})
                except ValueError as e:
                    err = str(e).lower()
                    if "exceed" not in err or "context" not in err:
                        raise
                    app_logger.warning(
                        "rag_llm_ctx_retry_stream batch=%d/%d after ValueError: %s",
                        bi,
                        n_b,
                        str(e).replace("\n", " ")[:200],
                    )
                    prompt_b = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
                    prompt_for_log = prompt_b
                    try:
                        for ev in _consume_stream(prompt_b):
                            txt = str(ev.get("text") or "")
                            if bi > 1 and txt:
                                raw_buf.append(txt)
                            emit_txt = txt
                            if bi > 1 and txt and suppress_repeat_prefix:
                                out_chars: list[str] = []
                                for ch in txt:
                                    if repeat_match_idx < len(prev_ctx_for_dedupe):
                                        if ch == prev_ctx_for_dedupe[repeat_match_idx]:
                                            repeat_match_idx += 1
                                            suppressed_prefix_chars += 1
                                            continue
                                        suppress_repeat_prefix = False
                                        out_chars.append(ch)
                                    else:
                                        if ch.isspace() and not out_chars:
                                            suppressed_prefix_chars += 1
                                            continue
                                        suppress_repeat_prefix = False
                                        out_chars.append(ch)
                                emit_txt = "".join(out_chars)
                            if bi > 1 and emit_txt and not sep_emitted:
                                pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
                                sep_emitted = True
                            if emit_txt:
                                part_buf.append(emit_txt)
                                if bi == 1:
                                    yield {"type": "delta", "text": emit_txt}
                                else:
                                    pending_events.append({"type": "delta", "text": emit_txt})
                    except ValueError:
                        prompt_b = _repair_prompt_brutal(llm_model, prompt_b, config, app_logger)
                        prompt_for_log = prompt_b
                        for ev in _consume_stream(prompt_b):
                            txt = str(ev.get("text") or "")
                            if bi > 1 and txt:
                                raw_buf.append(txt)
                            emit_txt = txt
                            if bi > 1 and txt and suppress_repeat_prefix:
                                out_chars: list[str] = []
                                for ch in txt:
                                    if repeat_match_idx < len(prev_ctx_for_dedupe):
                                        if ch == prev_ctx_for_dedupe[repeat_match_idx]:
                                            repeat_match_idx += 1
                                            suppressed_prefix_chars += 1
                                            continue
                                        suppress_repeat_prefix = False
                                        out_chars.append(ch)
                                    else:
                                        if ch.isspace() and not out_chars:
                                            suppressed_prefix_chars += 1
                                            continue
                                        suppress_repeat_prefix = False
                                        out_chars.append(ch)
                                emit_txt = "".join(out_chars)
                            if bi > 1 and emit_txt and not sep_emitted:
                                pending_events.append({"type": "delta", "text": _PARALLEL_BATCH_ANSWER_SEPARATOR})
                                sep_emitted = True
                            if emit_txt:
                                part_buf.append(emit_txt)
                                if bi == 1:
                                    yield {"type": "delta", "text": emit_txt}
                                else:
                                    pending_events.append({"type": "delta", "text": emit_txt})
                # Flush buffered batch 2+ events only if they add non-redundant content.
                # Use raw_buf (full LLM output before dedup) for comparison — the deduped
                # part_buf may start mid-sentence, giving a misleadingly low overlap score.
                if bi > 1 and pending_events:
                    raw_full = "".join(raw_buf)
                    part_check_src = _sanitize_parallel_later_part(raw_full) or _sanitize_parallel_later_part("".join(part_buf))

                    # --- Numbered-list path ---
                    # Extract only the items not already in rolling_answer_ctx and renumber them
                    # sequentially.  This handles the case where batch 2 repeats items 1-N and
                    # adds items N+1…M: only N+1…M are emitted, renumbered after batch 1's list.
                    novel_text = _filter_novel_list_items(part_check_src, rolling_answer_ctx)

                    if novel_text is not None:
                        # Content was a numbered list.
                        if novel_text.strip():
                            app_logger.info(
                                "qna_stream llm_parallel batch=%d/%d list_filter novel_chars=%d",
                                bi, n_b, len(novel_text),
                            )
                            pieces.append(_PARALLEL_BATCH_ANSWER_SEPARATOR)
                            for ev in _yield_text_delta_events(novel_text):
                                yield ev
                            part_buf.clear()
                            part_buf.append(novel_text)
                        else:
                            app_logger.info(
                                "qna_stream llm_parallel batch=%d/%d list_filter all_items_redundant",
                                bi, n_b,
                            )
                            part_buf.clear()
                            sep_emitted = False
                    else:
                        # --- Plain-text path: word-overlap redundancy check ---
                        redundant, jaccard, coverage = _is_redundant_parallel_part(part_check_src, rolling_answer_ctx)
                        if part_check_src.strip() and not redundant:
                            pieces.append(_PARALLEL_BATCH_ANSWER_SEPARATOR)
                            for dev in pending_events:
                                yield dev
                        else:
                            app_logger.info(
                                "qna_stream llm_parallel batch=%d/%d suppressed_redundant_answer jaccard=%.2f coverage=%.2f chars=%d",
                                bi,
                                n_b,
                                jaccard,
                                coverage,
                                len(raw_full),
                            )
                            part_buf.clear()
                            sep_emitted = False
                part_raw = "".join(part_buf)
                part = _sanitize_parallel_later_part(part_raw) if bi > 1 else part_raw
                if bi > 1 and suppressed_prefix_chars > 0:
                    app_logger.info(
                        "qna_stream llm_parallel batch=%d/%d dedupe_suppressed_prefix_chars=%d",
                        bi,
                        n_b,
                        suppressed_prefix_chars,
                    )
                batch_parts.append(part)
                if part.strip():
                    rolling_answer_ctx = _truncate_previous_answer_draft(
                        (rolling_answer_ctx + ("\n\n" if rolling_answer_ctx else "") + part).strip()
                    )
                app_logger.info(
                    "qna_stream llm_parallel phase=batch_stream_done batch=%d/%d stream_duration_sec=%.4f streamed_answer_chars=%d",
                    bi,
                    n_b,
                    time.perf_counter() - t_b,
                    len(part),
                )
            app_logger.info(
                "qna_stream llm_parallel phase=parallel_batches_stream_done reason=rolling_previous_batch_context",
            )
            app_logger.info(
                "qna_stream llm_parallel phase=sequential_stream_done batches=%d total_llm_wall_sec=%.4f",
                n_b,
                time.perf_counter() - t_llm,
            )

        raw = "".join(pieces).strip()
        log_llm_generation_duration(
            time.perf_counter() - t_llm,
            prompt_chars=len(prompt_for_log),
            answer_chars=len(raw),
            used_fallback=False,
        )
        mark("llm_stream_done", answer_chars=len(raw), parallel=use_parallel)
        app_logger.info("qna_stream_answer preview=%r", pipeline_log_preview(raw, max_chars=1000))

        answer = _format_answer_for_display(raw)
        mark("format_answer_done")
        if _contains_out_of_domain_boilerplate(answer):
            answer = _fallback_answer_from_retrieved_chunks(question, retrieved)
            mark("boilerplate_replaced_from_retrieval", answer_chars=len(answer))

        low_ans = answer.strip().lower()
        out_of_domain_low = OUT_OF_DOMAIN_REPLY.strip().lower()
        if low_ans == out_of_domain_low:
            mark("strip_sources_early_return")
            yield {"type": "done", "answer": answer, "sources": [], "images": []}
            return

        sources: list[dict[str, Any]] = []
        seen_docs: set[str] = set()
        for ch in retrieved:
            name = ch.get("doc_name")
            if not name or str(name) in seen_docs:
                continue
            seen_docs.add(str(name))
            path = _resolve_doc_path(str(name), str(ch.get("doc_path")) if ch.get("doc_path") else None, metadata)
            row: dict[str, Any] = {"doc_name": name, "doc_path": path or ""}
            pn = _source_page_number(ch)
            if pn is not None:
                row["page_number"] = pn
            ts_val = ch.get("timestamp")
            if isinstance(ts_val, str) and ts_val.strip():
                row["timestamp"] = ts_val.strip()
                ss = ch.get("start_sec")
                if ss is not None:
                    try:
                        row["start_sec"] = int(ss)
                    except (TypeError, ValueError):
                        pass
                es = ch.get("end_sec")
                if es is not None:
                    try:
                        row["end_sec"] = int(es)
                    except (TypeError, ValueError):
                        pass
            sources.append(row)
            if len(sources) >= 3:
                break
        mark("sources_built", source_docs=len(sources))
        images = _retrieve_caption_images(
            question=question,
            retrieved_chunks=retrieved,
            emb_model=emb_model,
            text_cfg=config,
            app_logger=app_logger,
        )
        mark("images_retrieved", image_count=len(images))
        yield {"type": "done", "answer": answer, "sources": sources, "images": images}
    except Exception as e:
        app_logger.exception("qna_stream failed: %s", e)
        yield {"type": "error", "message": str(e)}
    finally:
        try:
            if domain_pool is not None:  # type: ignore[name-defined]
                domain_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[name-defined]
        except Exception:
            pass
        app_logger.info("qna_stream_timing request_total_wall_sec=%.4f", time.perf_counter() - t0)
        log_process_end(app_logger, "qna_stream", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")


def ask_question(question: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    app_logger = get_logger("yuktra_qna.app", log_dir=os.path.join(DATA_DIR, "logs"), also_console=False)
    configure_rag_file_logging(log_dir=os.path.join(DATA_DIR, "logs"))
    t0 = time.perf_counter()
    last = [t0]

    def mark(step: str, **kv: Any) -> None:
        now = time.perf_counter()
        suffix = (" " + " ".join(f"{k}={v}" for k, v in kv.items())) if kv else ""
        app_logger.info(
            "qna_step step=%s delta_sec=%.4f cum_sec=%.4f%s",
            step,
            now - last[0],
            now - t0,
            suffix,
        )
        last[0] = now

    log_process_start(app_logger, "qna_request", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
    try:
        mark("enter", question_chars=len(question or ""))
        vectors, faiss_index, metadata, config, emb_model, llm_model = _load_runtime()
        mark("load_runtime_done")
        domain_gate_enabled = os.environ.get("YUKTRA_QNA_DOMAIN_GATE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
        if domain_gate_enabled:
            domain_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yuktra_qna_domain")
            domain_future = domain_pool.submit(_llm_domain_decision, question, llm_model, config, app_logger)
        else:
            domain_pool = None
            domain_future = None

        emb_prompt_style = resolve_embedding_prompt_style(
            config, str(config.get("embedding_model") or "local-embedding-gguf")
        )
        retrieval_generic_terms = resolve_retrieval_generic_terms(config)
        query_vec = embed_fused_query_for_retrieval(
            question,
            None,
            emb_model,
            device="cpu",
            max_length=int(config["embedding_max_length"]),
            embedding_prompt_style=emb_prompt_style,
            retrieval_query_clean_enabled=bool(config.get("retrieval_query_clean_enabled", True)),
            retrieval_generic_terms=retrieval_generic_terms,
        )
        mark("query_embedding_done")

        retrieved, capped, _ = retrieve_rag_pipeline(
            question,
            query_vec,
            vectors,
            metadata,
            faiss_index=faiss_index,
            top_k=int(config["top_k"]),
            retrieval_pool_k=int(config["retrieval_pool_k"]),
            mmr_k=int(config["mmr_k"]),
            mmr_lambda=float(config["mmr_lambda"]),
            max_context_chars=int(config["max_context_chars"]),
            restrict_top_document=bool(config["restrict_top_document"]),
            bm25_weight=float(config["bm25_weight"]),
            initial_pool_multiplier=int(config["initial_pool_multiplier"]),
            max_chunks_for_prompt=int(config["max_chunks_for_prompt"]),
            rrf_linear_blend=float(config["rrf_linear_blend"]),
            hybrid_alpha_semantic=float(config["hybrid_alpha_semantic"]),
            hybrid_rerank_enabled=bool(config.get("hybrid_rerank_enabled", False)),
            min_hybrid_rerank_score=float(config["min_hybrid_rerank_score"]),
            top_doc_page_window=int(config["top_doc_page_window"]),
            top_doc_chunk_neighbor_radius=int(config["top_doc_chunk_neighbor_radius"]),
            top_doc_chunk_neighbors_before=int(config.get("top_doc_chunk_neighbors_before", 0)),
            top_doc_chunk_neighbors_after=int(config.get("top_doc_chunk_neighbors_after", 0)),
            top_doc_section_expand=bool(config.get("top_doc_section_expand", True)),
            retrieval_query_clean_enabled=bool(config.get("retrieval_query_clean_enabled", True)),
            retrieval_generic_terms=retrieval_generic_terms,
        )
        mark(
            "retrieval_done",
            retrieved_chunks=len(retrieved),
            capped_chunks=len(capped),
        )
        _log_retrieved_chunks(app_logger, "qna_request retrieved_chunks_detail", retrieved)
        _log_retrieved_chunks(app_logger, "qna_request capped_chunks_detail", capped)
        if domain_future is None:
            domain_relevant = True
        else:
            domain_relevant = bool(domain_future.result())
            if not domain_relevant and _retrieved_context_overrides_domain_gate(capped, config):
                app_logger.info(
                    "qna_request domain_gate overridden_by_video_transcript_context capped_chunks=%d",
                    len(capped),
                )
                domain_relevant = True
        if not domain_relevant:
            mark("out_of_domain_skip_llm")
            return OUT_OF_DOMAIN_REPLY, [], []

        first_bs, rest_bs, para_workers = _parallel_batch_settings(config)
        batches = _chunk_batches_head_then_rest(list(capped), first_bs, rest_bs)
        use_parallel = para_workers > 0 and len(batches) > 1
        if not use_parallel:
            app_logger.info(
                "qna_request llm_parallel phase=skipped logical_batches=%d first_batch_cfg=%d rest_batch_cfg=%d "
                "max_workers_cfg=%d reason=%s",
                len(batches),
                first_bs,
                rest_bs,
                para_workers,
                "max_workers_zero" if para_workers <= 0 else "single_logical_batch",
            )
        max_tok = int(config["llm_max_new_tokens"])
        max_tok_label = "unlimited" if max_tok <= 0 else str(max_tok)
        t_llm = time.perf_counter()

        def _complete_sync(llm_m: Llama, p: str) -> Any:
            return llm_m(
                p,
                max_tokens=None if max_tok <= 0 else max_tok,
                temperature=float(config["llm_temperature"]),
                top_p=float(config["llm_top_p"]),
                repeat_penalty=float(config["llm_repeat_penalty"]),
            )

        if not use_parallel:
            static_prefix, dynamic_suffix, capped = _build_prompt_fitting_llm_ctx(
                question,
                capped,
                llm_model,
                n_ctx=int(config["llm_n_ctx"]),
                llm_max_new_tokens=int(config["llm_max_new_tokens"]),
                log=app_logger,
            )
            prompt = static_prefix + dynamic_suffix
            mark(
                "prompt_built",
                prompt_chars=len(prompt),
                llm_max_new_tokens=max_tok_label,
            )
            try:
                result = _complete_sync(llm_model, prompt)
            except ValueError as e:
                err = str(e).lower()
                if "exceed" not in err or "context" not in err:
                    raise
                app_logger.warning(
                    "rag_llm_ctx_retry after ValueError: %s",
                    str(e).replace("\n", " ")[:200],
                )
                prompt = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
                try:
                    result = _complete_sync(llm_model, prompt)
                except ValueError:
                    prompt = _repair_prompt_brutal(llm_model, prompt, config, app_logger)
                    result = _complete_sync(llm_model, prompt)
            answer = (result["choices"][0]["text"] or "").strip()
            prompt_chars_for_log = len(prompt)
        else:
            n_b = len(batches)
            pool_w = min(para_workers, n_b - 1)
            mark(
                "llm_parallel_plan",
                batches=n_b,
                first_batch_chunk_size=first_bs,
                rest_batch_chunk_size=rest_bs,
                background_worker_threads=pool_w,
                total_chunk_rows=sum(len(b) for b in batches),
            )
            app_logger.info(
                "qna_request llm_parallel phase=plan batches=%d first_batch_chunks=%d rest_batch_chunks=%d "
                "pool_workers=%d chunks_per_batch=%s total_chunks=%d",
                n_b,
                first_bs,
                rest_bs,
                pool_w,
                [len(b) for b in batches],
                sum(len(b) for b in batches),
            )
            prompt_chars_acc = 0
            rolling_answer_ctx = ""
            batch_parts: list[str] = []

            def _run_batch_on_main_llm(i: int, prev_answer: str) -> str:
                nonlocal prompt_chars_acc
                st, dyn, _ = _build_prompt_fitting_llm_ctx(
                    question,
                    batches[i],
                    llm_model,
                    n_ctx=int(config["llm_n_ctx"]),
                    llm_max_new_tokens=int(config["llm_max_new_tokens"]),
                    log=app_logger,
                    batch_index=i + 1,
                    batch_count=n_b,
                    previous_answer_draft=prev_answer,
                )
                pr = st + dyn
                prompt_chars_acc += len(pr)
                try:
                    res = _complete_sync(llm_model, pr)
                except ValueError as e:
                    err = str(e).lower()
                    if "exceed" not in err or "context" not in err:
                        raise
                    pr2 = _repair_prompt_on_context_overflow(question, llm_model, config, app_logger)
                    try:
                        res = _complete_sync(llm_model, pr2)
                    except ValueError:
                        pr3 = _repair_prompt_brutal(llm_model, pr2, config, app_logger)
                        res = _complete_sync(llm_model, pr3)
                out = (res["choices"][0]["text"] or "").strip()
                return _sanitize_parallel_later_part(out) if (i + 1) > 1 else out

            for i in range(n_b):
                t_b = time.perf_counter()
                app_logger.info(
                    "qna_request llm_parallel phase=batch_start batch=%d/%d chunk_count=%d role=main",
                    i + 1,
                    n_b,
                    len(batches[i]),
                )
                ptxt = _run_batch_on_main_llm(i, rolling_answer_ctx)
                app_logger.info(
                    "qna_request llm_parallel phase=batch_done batch=%d/%d answer_chars=%d duration_sec=%.4f role=main",
                    i + 1,
                    n_b,
                    len(ptxt),
                    time.perf_counter() - t_b,
                )
                if i > 0 and ptxt.strip():
                    redundant, jaccard, coverage = _is_redundant_parallel_part(ptxt, rolling_answer_ctx)
                    if redundant:
                        app_logger.info(
                            "qna_request llm_parallel batch=%d/%d suppressed_redundant_answer jaccard=%.2f coverage=%.2f chars=%d",
                            i + 1,
                            n_b,
                            jaccard,
                            coverage,
                            len(ptxt),
                        )
                        continue
                batch_parts.append(ptxt)
                if ptxt.strip():
                    rolling_answer_ctx = _truncate_previous_answer_draft(
                        (rolling_answer_ctx + ("\n\n" if rolling_answer_ctx else "") + ptxt).strip()
                    )
            answer = _PARALLEL_BATCH_ANSWER_SEPARATOR.join(batch_parts)
            app_logger.info(
                "qna_request llm_parallel phase=parallel_batches_done reason=rolling_previous_batch_context",
            )
            app_logger.info(
                "qna_request llm_parallel phase=all_joined batches=%d total_answer_chars=%d llm_wall_sec=%.4f",
                n_b,
                len(answer),
                time.perf_counter() - t_llm,
            )
            mark(
                "prompt_built",
                prompt_chars=prompt_chars_acc,
                llm_max_new_tokens=max_tok_label,
                parallel_batches=n_b,
            )
            prompt_chars_for_log = prompt_chars_acc
        log_llm_generation_duration(
            time.perf_counter() - t_llm,
            prompt_chars=prompt_chars_for_log,
            answer_chars=len(answer),
            used_fallback=False,
        )
        mark("llm_decode_done", answer_chars=len(answer))
        app_logger.info("qna_answer preview=%r", pipeline_log_preview(answer, max_chars=1000))
        answer = _format_answer_for_display(answer)
        mark("format_answer_done")
        if _contains_out_of_domain_boilerplate(answer):
            answer = _fallback_answer_from_retrieved_chunks(question, retrieved)
            mark("boilerplate_replaced_from_retrieval", answer_chars=len(answer))

        low_ans = answer.strip().lower()
        out_of_domain_low = OUT_OF_DOMAIN_REPLY.strip().lower()
        if low_ans == out_of_domain_low:
            mark("strip_sources_early_return")
            return answer, [], []

        sources: list[dict[str, Any]] = []
        seen_docs: set[str] = set()
        for ch in retrieved:
            name = ch.get("doc_name")
            if not name or str(name) in seen_docs:
                continue
            seen_docs.add(str(name))
            path = _resolve_doc_path(str(name), str(ch.get("doc_path")) if ch.get("doc_path") else None, metadata)
            row: dict[str, Any] = {"doc_name": name, "doc_path": path or ""}
            pn = _source_page_number(ch)
            if pn is not None:
                row["page_number"] = pn
            ts_val = ch.get("timestamp")
            if isinstance(ts_val, str) and ts_val.strip():
                row["timestamp"] = ts_val.strip()
                ss = ch.get("start_sec")
                if ss is not None:
                    try:
                        row["start_sec"] = int(ss)
                    except (TypeError, ValueError):
                        pass
                es = ch.get("end_sec")
                if es is not None:
                    try:
                        row["end_sec"] = int(es)
                    except (TypeError, ValueError):
                        pass
            sources.append(row)
            if len(sources) >= 3:
                break
        mark("sources_built", source_docs=len(sources))
        images = _retrieve_caption_images(
            question=question,
            retrieved_chunks=retrieved,
            emb_model=emb_model,
            text_cfg=config,
            app_logger=app_logger,
        )
        return answer, sources, images
    finally:
        try:
            if domain_pool is not None:  # type: ignore[name-defined]
                domain_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[name-defined]
        except Exception:
            pass
        app_logger.info("qna_timing request_total_wall_sec=%.4f", time.perf_counter() - t0)
        log_process_end(app_logger, "qna_request", extra=f"tenant={RAG_STORE_TENANT} index={RAG_STORE_INDEX_NAME}")
