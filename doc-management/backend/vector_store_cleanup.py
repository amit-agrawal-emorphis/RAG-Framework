"""Remove machine tenants and per-document vectors from FAISS stores."""
from __future__ import annotations

import os
import shutil
import sys
from typing import Any

import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DQNA_BACKEND = os.path.join(_REPO_ROOT, "doc-qna", "backend")
if _DQNA_BACKEND not in sys.path:
    sys.path.insert(0, _DQNA_BACKEND)

from rag_utils import load_vector_store, save_vector_store  # noqa: E402

TEXT_INDEX_NAME = "document_text"
IMAGE_INDEX_NAME = "Img"


def _doc_name_from_meta(row: dict[str, Any]) -> str:
    return str(row.get("doc_name") or os.path.basename(str(row.get("doc_path") or ""))).strip()


def _reconstruct_faiss_vectors(index: Any) -> np.ndarray | None:
    try:
        rows: list[np.ndarray] = []
        for i in range(int(index.ntotal)):
            rows.append(np.asarray(index.reconstruct(i), dtype=np.float32))
        if not rows:
            return np.zeros((0, int(getattr(index, "d", 0) or 0)), dtype=np.float32)
        return np.vstack(rows).astype(np.float32)
    except Exception:
        return None


def _load_store_vectors(store_dir: str) -> tuple[np.ndarray | None, list[dict[str, Any]], dict[str, Any]]:
    if not os.path.isdir(store_dir):
        return None, [], {}
    try:
        vectors, faiss_index, metadata, config = load_vector_store(store_dir)
        if vectors is None and faiss_index is not None:
            vectors = _reconstruct_faiss_vectors(faiss_index)
        if vectors is None:
            return None, [], {}
        return np.asarray(vectors, dtype=np.float32), list(metadata or []), dict(config or {})
    except Exception:
        return None, [], {}


def _filter_rows_not_matching_doc(
    vectors: np.ndarray | None,
    metadata: list[dict[str, Any]],
    doc_name: str,
) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    if vectors is None or not metadata:
        return None, []
    keep_indices = [i for i, row in enumerate(metadata) if _doc_name_from_meta(row) != doc_name]
    if not keep_indices:
        return None, []
    kept_vectors = np.asarray(vectors[keep_indices], dtype=np.float32)
    kept_meta = [dict(metadata[i]) for i in keep_indices]
    for i, row in enumerate(kept_meta):
        row["vector_id"] = i
    return kept_vectors, kept_meta


def _write_or_remove_store(
    store_dir: str,
    vectors: np.ndarray | None,
    metadata: list[dict[str, Any]],
    config: dict[str, Any],
) -> bool:
    """Persist filtered vectors or remove the store directory when empty."""
    if vectors is None or vectors.shape[0] == 0 or not metadata:
        if os.path.isdir(store_dir):
            shutil.rmtree(store_dir, ignore_errors=True)
        return False

    updated_config = dict(config or {})
    updated_config["num_chunks"] = int(vectors.shape[0])
    save_vector_store(store_dir, vectors, metadata, updated_config)
    return True


def _remove_document_from_store(store_dir: str, doc_name: str) -> bool:
    vectors, metadata, config = _load_store_vectors(store_dir)
    if vectors is None or not metadata:
        return False
    kept_vectors, kept_meta = _filter_rows_not_matching_doc(vectors, metadata, doc_name)
    return _write_or_remove_store(store_dir, kept_vectors, kept_meta, config)


def remove_document_vectors(data_dir: str, machine_name: str, file_name: str) -> dict[str, Any]:
    """Delete one uploaded file and remove its vectors from text/image stores."""
    safe_name = os.path.basename(file_name)
    machine_root = os.path.join(data_dir, machine_name)
    doc_path = os.path.join(machine_root, "documents", safe_name)

    removed_file = 0
    if os.path.isfile(doc_path):
        os.remove(doc_path)
        removed_file = 1

    text_store = os.path.join(machine_root, TEXT_INDEX_NAME)
    image_store = os.path.join(machine_root, IMAGE_INDEX_NAME)
    text_updated = _remove_document_from_store(text_store, safe_name)
    image_updated = _remove_document_from_store(image_store, safe_name)

    ingested_root = os.path.join(data_dir, "Ingested", machine_name)
    if os.path.isdir(ingested_root):
        _remove_document_from_store(os.path.join(ingested_root, TEXT_INDEX_NAME), safe_name)
        _remove_document_from_store(os.path.join(ingested_root, IMAGE_INDEX_NAME), safe_name)
        ingested_docs = os.path.join(ingested_root, "documents", safe_name)
        if os.path.isfile(ingested_docs):
            os.remove(ingested_docs)

    return {
        "deletedCount": removed_file,
        "vectorsUpdated": bool(text_updated or image_updated),
        "docName": safe_name,
        "machineName": machine_name,
    }


def _force_rmtree(path: str) -> None:
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


def delete_machine_tenant(data_dir: str, machine_name: str) -> dict[str, Any]:
    """Delete the full machine tenant from admin data and data/Ingested."""
    machine_root = os.path.join(data_dir, machine_name)
    ingested_root = os.path.join(data_dir, "Ingested", machine_name)

    deleted_docs = 0
    docs_dir = os.path.join(machine_root, "documents")
    if os.path.isdir(docs_dir):
        for name in os.listdir(docs_dir):
            target = os.path.join(docs_dir, name)
            if os.path.isfile(target):
                os.remove(target)
                deleted_docs += 1

    removed_machine_root = os.path.isdir(machine_root)
    removed_ingested_root = os.path.isdir(ingested_root)
    if removed_machine_root:
        _force_rmtree(machine_root)
    if removed_ingested_root:
        _force_rmtree(ingested_root)

    return {
        "deletedCount": deleted_docs,
        "machineRemoved": removed_machine_root or removed_ingested_root,
        "machineName": machine_name,
    }
