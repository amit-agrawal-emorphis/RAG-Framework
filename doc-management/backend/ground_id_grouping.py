"""
Assign ``ground_id`` to metadata rows by asking a local LLM to group consecutive
chunks (vector_id + section_path_str) into coarse informational units (e.g. front
matter, whole chapters, full procedures)—not one group per minor heading.

Windows default to 100 pairs with 10 overlap; parallel workers load one GGUF
each (``max_workers`` > 1) and process batches of windows serially inside the worker.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SIZE = 100
DEFAULT_OVERLAP = 10


def _runtime_threads() -> int:
    raw = os.environ.get("YUKTRA_LLM_N_THREADS", "").strip()
    try:
        val = int(raw) if raw else 2
    except ValueError:
        val = 2
    return max(1, val)


def _ensure_repo_paths() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    dq_backend = os.path.join(repo_root, "doc-qna", "backend")
    dm_backend = os.path.join(repo_root, "doc-management", "backend")
    for p in (dm_backend, dq_backend, repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)
    return repo_root


def _safe_doc_slug(doc_name: str) -> str:
    v = (doc_name or "").strip() or "doc"
    out: List[str] = []
    for ch in v:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
        else:
            out.append("_")
    s = "".join(out).strip("._") or "doc"
    s = s[:56] if len(s) > 56 else s
    return s + "__"


def _pairs_for_metadata_slice(
    metadata: List[Dict[str, Any]], start: int, end: int
) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for i in range(start, end):
        m = metadata[i]
        vid = int(m.get("vector_id", i))
        sp = m.get("section_path_str")
        if sp is None and isinstance(m.get("section_path"), list):
            parts = [str(x).strip() for x in m["section_path"] if str(x).strip()]
            sp = " > ".join(parts) if parts else ""
        path = str(sp or "").strip()
        out.append((vid, path))
    return out


def build_windows(
    pairs: List[Tuple[int, str]],
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> List[Dict[str, Any]]:
    if window_size <= 0:
        raise ValueError("window_size must be > 0")
    if overlap < 0 or overlap >= window_size:
        raise ValueError("overlap must satisfy 0 <= overlap < window_size")

    n = len(pairs)
    if n == 0:
        return []

    step = window_size - overlap
    windows: List[Dict[str, Any]] = []
    start_i = 0
    widx = 0
    while start_i < n:
        end_i = min(start_i + window_size, n)
        block = pairs[start_i:end_i]
        vid_to_path = {str(a): b for a, b in block}
        windows.append(
            {
                "window_index": widx,
                "start_i": start_i,
                "end_i": end_i,
                "vid_to_path": vid_to_path,
            }
        )
        widx += 1
        if end_i >= n:
            break
        start_i += step
    return windows


def _build_grouping_prompt(vid_to_path: Dict[str, str]) -> str:
    rows = []
    for k in sorted(vid_to_path.keys(), key=lambda x: int(x)):
        path = vid_to_path[k].replace("\n", " ").strip()
        rows.append(f"{k}: {path}")
    listing = "\n".join(rows)
    return f"""You assign each chunk to a COARSE informational group, like parts of a book or manual—not one group per heading.

Input: lines are "vector_id: section_path" in strict reading order. section_path is often a heading or breadcrumb; it is a hint only.

GOAL (critical):
- Many consecutive vector_ids MUST share the SAME group_label when they belong to one major unit (e.g. all front matter before Chapter 1; an entire numbered chapter such as "3 Using the Module" including its subsections, tables, and notes; one full maintenance procedure from its title through steps until that procedure clearly ends).
- Do NOT give every distinct section_path its own group. Subheadings, WARNING, CAUTION, NOTE, "Parameter"/"Description", short boilerplate, and repeated titles under the same chapter stay in the PARENT chapter or procedure group.
- Start a NEW group_label only when the manual clearly moves to a different TOP-LEVEL unit, for example:
  * A new numbered major chapter/section (e.g. "1 Introduction" → "2 Site Requirements…", or "6 Error Information" → "7 Maintenance").
  * A new major part (front matter vs first chapter; appendix vs main body).
  * A clearly separate long block (e.g. a full error-catalog section vs a full maintenance chapter).
- For step-by-step procedures: keep ONE group from the procedure’s main title through substeps until an explicit wrap-up ("Next Steps:", end of that procedure) OR the next major chapter/section line appears in section_path.

group_label rules:
- Short snake_case (letters, digits, underscore only). Name the MAJOR unit (e.g. ch03_using_the_module, front_matter, ch07_maintenance, procedure_replace_inlet_tubing).
- Reuse the same label across long runs of vector_ids whenever they still belong to that unit.

JSON rules (critical):
- Include EVERY vector_id from the input exactly once as a string key in "assignments", including "0" if present.
- Output ONLY valid JSON, no markdown fences, no commentary.

Chunks:
{listing}

Output shape (example structure only):
{{"assignments": {{"0": "front_matter", "1": "front_matter", "2": "front_matter"}}}}
"""


def _extract_json_object(text: str) -> Optional[dict]:
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    frag = t[start : end + 1]
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        return None


def _llm_complete_text(
    llm: Any,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repeat_penalty: float,
) -> str:
    out = llm(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        repeat_penalty=repeat_penalty,
        stream=False,
    )
    ch0 = (out.get("choices") or [{}])[0]
    return (ch0.get("text") or "").strip()


def _parse_assignments(
    raw_text: str, expected_ids: List[str], log: Any
) -> Dict[int, str]:
    data = _extract_json_object(raw_text)
    if not isinstance(data, dict):
        log.warning("ground_id_llm_parse_failed no_json preview=%r", raw_text[:400])
        return {int(x): f"singleton_{x}" for x in expected_ids}

    assign = data.get("assignments")
    if not isinstance(assign, dict):
        log.warning("ground_id_llm_parse_failed missing_assignments preview=%r", raw_text[:400])
        return {int(x): f"singleton_{x}" for x in expected_ids}

    out: Dict[int, str] = {}
    for k, v in assign.items():
        ks = str(k).strip()
        if ks not in expected_ids:
            continue
        lab = str(v).strip() if v is not None else ""
        if not lab:
            lab = f"singleton_{ks}"
        out[int(ks)] = re.sub(r"[^a-zA-Z0-9_]+", "_", lab).strip("_") or f"singleton_{ks}"

    for eid in expected_ids:
        ei = int(eid)
        if ei not in out:
            out[ei] = f"missing_{ei}"
            log.warning("ground_id_llm_missing_vector_id id=%s", eid)

    # Common model slip: skips "0" (empty section_path); treat as same group as chunk 1.
    if (
        0 in out
        and 1 in out
        and str(out[0]).startswith("missing_")
        and not str(out[1]).startswith("missing_")
    ):
        out[0] = out[1]
        log.info("ground_id_heuristic vector_id=0 label copied from vector_id=1")

    return out


def _run_one_window(
    llm: Any,
    win: Dict[str, Any],
    *,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repeat_penalty: float,
    log: Any,
) -> Tuple[int, int, Dict[int, str], Dict[str, Any]]:
    vid_to_path = win["vid_to_path"]
    expected_ids = sorted(vid_to_path.keys(), key=lambda x: int(x))
    prompt = _build_grouping_prompt(vid_to_path)
    t0 = time.perf_counter()
    raw = _llm_complete_text(
        llm,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        repeat_penalty=repeat_penalty,
    )
    dt = time.perf_counter() - t0
    parsed = _parse_assignments(raw, expected_ids, log)
    preview = raw[:500].replace("\n", " ") if raw else ""
    log_meta = {
        "window_index": win.get("window_index"),
        "start_i": win.get("start_i"),
        "end_i": win.get("end_i"),
        "duration_sec": dt,
        "response_preview": preview,
    }
    log.info(
        "ground_id_window_done window_index=%s start_i=%s end_i=%s duration_sec=%.3f "
        "response_preview=%r",
        log_meta["window_index"],
        log_meta["start_i"],
        log_meta["end_i"],
        log_meta["duration_sec"],
        preview,
    )
    return int(win["start_i"]), int(win["end_i"]), parsed, log_meta


def _split_into_batches(windows: List[Dict[str, Any]], n_workers: int) -> List[List[Dict[str, Any]]]:
    if n_workers <= 1 or len(windows) <= 1:
        return [windows]
    n_workers = min(n_workers, len(windows))
    k, m = divmod(len(windows), n_workers)
    batches: List[List[Dict[str, Any]]] = []
    i = 0
    for j in range(n_workers):
        take = k + (1 if j < m else 0)
        batches.append(windows[i : i + take])
        i += take
    return [b for b in batches if b]


def _merge_window_maps(
    window_results: List[Tuple[int, int, Dict[int, str]]],
) -> Dict[int, str]:
    """Later windows overwrite vector_ids in the overlap zone (higher start_i wins last for same id)."""
    ordered = sorted(window_results, key=lambda x: (x[0], x[1]))
    merged: Dict[int, str] = {}
    for _s, _e, amap in ordered:
        for vid, lab in amap.items():
            merged[int(vid)] = lab
    return merged


def _labels_to_final_ground_ids(
    merged: Dict[int, str], scope_prefix: str
) -> Dict[int, str]:
    label_first_vid: Dict[str, int] = {}
    for vid in sorted(merged.keys()):
        lab = merged[vid]
        if lab not in label_first_vid or vid < label_first_vid[lab]:
            label_first_vid[lab] = vid
    ordered_labels = sorted(label_first_vid.keys(), key=lambda L: label_first_vid[L])
    label_to_gid = {
        lab: f"{scope_prefix}ground-{i}" for i, lab in enumerate(ordered_labels)
    }
    return {vid: label_to_gid[merged[vid]] for vid in merged}


def assign_ground_ids_slice(
    metadata: List[Dict[str, Any]],
    start: int,
    end: int,
    *,
    llm_model_path: str,
    log: Any,
    window_size: int = DEFAULT_WINDOW_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    max_workers: int = 1,
    llm_n_ctx: int = 8192,
    llm_max_new_tokens: int = 8192,
    temperature: float = 0.1,
    top_p: float = 0.9,
    repeat_penalty: float = 1.05,
    scope_doc_name: str = "",
) -> None:
    """
    Mutates ``metadata[i]`` for ``start <= i < end`` adding ``ground_id``.

    ``scope_doc_name``: non-empty => prefix final ids as ``{slug}__ground-N`` for uniqueness across docs.
    """
    if start < 0 or end > len(metadata) or start >= end:
        raise ValueError(f"invalid slice start={start} end={end} len={len(metadata)}")
    if not llm_model_path or not os.path.isfile(llm_model_path):
        raise FileNotFoundError(f"LLM GGUF not found: {llm_model_path}")

    pairs = _pairs_for_metadata_slice(metadata, start, end)
    windows = build_windows(pairs, window_size=window_size, overlap=overlap)
    expected_vids = [v for v, _ in pairs]

    log.info(
        "ground_id_phase=start slice=[%d,%d) chunks=%d windows=%d window_size=%d overlap=%d max_workers=%d "
        "llm=%s scope=%r",
        start,
        end,
        end - start,
        len(windows),
        window_size,
        overlap,
        max_workers,
        llm_model_path,
        (scope_doc_name or "").strip(),
    )

    scope_prefix = _safe_doc_slug(scope_doc_name) if (scope_doc_name or "").strip() else ""

    window_results: List[Tuple[int, int, Dict[int, str]]] = []

    if max_workers <= 1:
        from llama_cpp import Llama  # type: ignore

        t_load = time.perf_counter()
        llm = Llama(
            model_path=llm_model_path,
            n_ctx=int(llm_n_ctx),
            n_threads=_runtime_threads(),
            n_batch=256,
            verbose=False,
        )
        log.info("ground_id_llm_loaded duration_sec=%.3f", time.perf_counter() - t_load)
        for win in windows:
            s_i, e_i, parsed, _meta = _run_one_window(
                llm,
                win,
                max_tokens=llm_max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                repeat_penalty=repeat_penalty,
                log=log,
            )
            window_results.append((s_i, e_i, parsed))
    else:
        batches = _split_into_batches(windows, max_workers)
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=len(batches), mp_context=ctx) as ex:
            futs = [
                ex.submit(
                    _worker_process_batch,
                    {
                        "model_path": llm_model_path,
                        "n_ctx": llm_n_ctx,
                        "llm_max_new_tokens": llm_max_new_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                        "repeat_penalty": repeat_penalty,
                        "windows": batch,
                    },
                )
                for batch in batches
            ]
            for fut in as_completed(futs):
                batch_out = fut.result()
                for s_i, e_i, parsed, wmeta in batch_out:
                    window_results.append((s_i, e_i, parsed))
                    log.info(
                        "ground_id_window_done window_index=%s start_i=%s end_i=%s duration_sec=%.3f "
                        "response_preview=%r (worker)",
                        wmeta.get("window_index"),
                        wmeta.get("start_i"),
                        wmeta.get("end_i"),
                        wmeta.get("duration_sec"),
                        wmeta.get("response_preview"),
                    )

    merged = _merge_window_maps(window_results)
    for vid in expected_vids:
        if vid not in merged:
            merged[vid] = f"singleton_{vid}"
            log.warning("ground_id_merge_missing_vector_id id=%s", vid)

    final_map = _labels_to_final_ground_ids(merged, scope_prefix)
    for i in range(start, end):
        m = metadata[i]
        vid = int(m.get("vector_id", i))
        m["ground_id"] = final_map.get(vid, f"{scope_prefix}ground-orphan-{vid}")

    uniq = len({metadata[i].get("ground_id") for i in range(start, end)})
    log.info(
        "ground_id_phase=done slice=[%d,%d) distinct_ground_ids=%d sample_map=%s",
        start,
        end,
        uniq,
        dict(list((vid, final_map[vid]) for vid in expected_vids[:5] if vid in final_map)),
    )


def _worker_process_batch(
    payload: Dict[str, Any],
) -> List[Tuple[int, int, Dict[int, str], Dict[str, Any]]]:
    """Child process: load one Llama and run all windows in the batch serially."""
    from llama_cpp import Llama  # type: ignore

    model_path = payload["model_path"]
    llm = Llama(
        model_path=model_path,
        n_ctx=int(payload["n_ctx"]),
        n_threads=_runtime_threads(),
        n_batch=256,
        verbose=False,
    )
    out: List[Tuple[int, int, Dict[int, str], Dict[str, Any]]] = []
    wlog = logging.getLogger("yuktra_docmgmt.ground_id_worker")
    for win in payload["windows"]:
        out.append(
            _run_one_window(
                llm,
                win,
                max_tokens=int(payload["llm_max_new_tokens"]),
                temperature=float(payload["temperature"]),
                top_p=float(payload["top_p"]),
                repeat_penalty=float(payload["repeat_penalty"]),
                log=wlog,
            )
        )
    return out


def patch_vector_store_ground_ids(
    store_dir: str,
    *,
    llm_model_path: str,
    window_size: int = DEFAULT_WINDOW_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    max_workers: int = 1,
    log: Optional[Any] = None,
) -> None:
    """Load ``metadata.json`` from a store, assign ground_id for all rows, write back."""
    _ensure_repo_paths()
    from rag_utils import load_vector_store

    lg = log or logger
    meta_path = os.path.join(store_dir, "metadata.json")
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(meta_path)

    _vectors, _index, metadata, _cfg = load_vector_store(store_dir)
    if not metadata:
        raise ValueError("empty metadata")
    assign_ground_ids_slice(
        metadata,
        0,
        len(metadata),
        llm_model_path=llm_model_path,
        log=lg,
        window_size=window_size,
        overlap=overlap,
        max_workers=max_workers,
        scope_doc_name="",
    )
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False)
    lg.info("ground_id_patch_saved path=%s rows=%d", os.path.abspath(meta_path), len(metadata))


def _cli_main() -> None:
    _ensure_repo_paths()
    from logger import get_logger

    p = argparse.ArgumentParser(description="Assign ground_id to an existing vector store metadata.json")
    p.add_argument("--store_dir", required=True)
    p.add_argument("--llm_model", required=True)
    p.add_argument("--window_size", type=int, default=DEFAULT_WINDOW_SIZE)
    p.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    p.add_argument("--max_workers", type=int, default=1)
    args = p.parse_args()

    log_dir = os.path.join(_ensure_repo_paths(), "data", "logs")
    lg = get_logger(
        "yuktra_docmgmt.ground_id_cli",
        log_dir=log_dir,
        also_console=True,
        console_stream=sys.stdout,
    )
    patch_vector_store_ground_ids(
        args.store_dir,
        llm_model_path=args.llm_model,
        window_size=args.window_size,
        overlap=args.overlap,
        max_workers=args.max_workers,
        log=lg,
    )


if __name__ == "__main__":
    _cli_main()
