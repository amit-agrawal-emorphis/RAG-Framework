"""Cross-process FIFO export queue for doc-management zip downloads.

Gunicorn runs multiple worker processes; in-memory queues are not shared.
This module persists slot/queue/state in SQLite so all workers follow one FIFO.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

_EXPORT_DB_NAME = "export_queue.db"


def _db_path(data_dir: str) -> str:
    return os.path.join(data_dir, _EXPORT_DB_NAME)


def init_export_queue_db(data_dir: str) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with _connect(data_dir) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                slot TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_queue (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS export_states (
                slug TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                zip_path TEXT,
                zip_size INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO export_meta (id, slot) VALUES (1, NULL)")
        conn.commit()


@contextmanager
def _connect(data_dir: str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_db_path(data_dir), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
    finally:
        conn.close()


@contextmanager
def _transaction(data_dir: str) -> Generator[sqlite3.Connection, None, None]:
    with _connect(data_dir) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _get_slot(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT slot FROM export_meta WHERE id = 1").fetchone()
    if not row:
        return None
    value = row["slot"]
    return str(value) if value else None


def _set_slot(conn: sqlite3.Connection, slug: str | None) -> None:
    conn.execute("UPDATE export_meta SET slot = ? WHERE id = 1", (slug,))


def _queue_slugs(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT slug FROM export_queue ORDER BY position ASC").fetchall()
    return [str(row["slug"]) for row in rows]


def _queue_position(conn: sqlite3.Connection, slug: str) -> int | None:
    rows = _queue_slugs(conn)
    if slug not in rows:
        return None
    return rows.index(slug) + 1


def _enqueue(conn: sqlite3.Connection, slug: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO export_queue (slug) VALUES (?)
        """,
        (slug,),
    )
    conn.execute(
        """
        INSERT INTO export_states (slug, state, zip_path, zip_size, error)
        VALUES (?, 'queued', NULL, 0, '')
        ON CONFLICT(slug) DO UPDATE SET
            state = 'queued',
            zip_path = NULL,
            zip_size = 0,
            error = ''
        """,
        (slug,),
    )
    position = _queue_position(conn, slug)
    return position or len(_queue_slugs(conn))


def _set_state(
    conn: sqlite3.Connection,
    slug: str,
    state: str,
    zip_path: str | None,
    zip_size: int,
    error: str,
) -> None:
    conn.execute(
        """
        INSERT INTO export_states (slug, state, zip_path, zip_size, error)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            state = excluded.state,
            zip_path = excluded.zip_path,
            zip_size = excluded.zip_size,
            error = excluded.error
        """,
        (slug, state, zip_path, zip_size, error),
    )


def _get_state_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT slug, state, zip_path, zip_size, error FROM export_states WHERE slug = ?",
        (slug,),
    ).fetchone()


def _state_payload(row: sqlite3.Row | None, slug: str) -> dict[str, Any]:
    if row is None:
        return {"machineName": slug, "state": "idle", "zipSize": 0}
    state = str(row["state"] or "idle")
    payload: dict[str, Any] = {
        "machineName": slug,
        "state": state,
        "zipSize": int(row["zip_size"] or 0),
    }
    if state == "failed":
        payload["error"] = str(row["error"] or "Export failed")
    return payload


def _start_slot_locked(conn: sqlite3.Connection, slug: str) -> None:
    _set_slot(conn, slug)
    _set_state(conn, slug, "building", None, 0, "")


def _release_slot_and_dequeue_next_locked(conn: sqlite3.Connection) -> str | None:
    _set_slot(conn, None)
    row = conn.execute(
        "SELECT slug FROM export_queue ORDER BY position ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    next_slug = str(row["slug"])
    conn.execute("DELETE FROM export_queue WHERE slug = ?", (next_slug,))
    _start_slot_locked(conn, next_slug)
    return next_slug


def export_prepare(data_dir: str, slug: str) -> tuple[dict[str, Any], str | None]:
    """Enqueue or claim the export slot. Returns (response, slug_to_build_or_none)."""
    with _transaction(data_dir) as conn:
        queue_position = _queue_position(conn, slug)
        if queue_position is not None:
            return {
                "machineName": slug,
                "state": "queued",
                "queuePosition": queue_position,
                "zipSize": 0,
            }, None

        slot = _get_slot(conn)
        row = _get_state_row(conn, slug)
        state = str(row["state"]) if row else "idle"
        zip_path = str(row["zip_path"]) if row and row["zip_path"] else None

        if state == "building" and slug == slot:
            return {"machineName": slug, "state": "building", "zipSize": 0}, None
        if state == "ready" and zip_path and os.path.isfile(zip_path) and slug == slot:
            return {
                "machineName": slug,
                "state": "ready",
                "zipSize": int(row["zip_size"] or os.path.getsize(zip_path)),
            }, None

        if slot is not None:
            position = _enqueue(conn, slug)
            return {
                "machineName": slug,
                "state": "queued",
                "queuePosition": position,
                "zipSize": 0,
            }, None

        _start_slot_locked(conn, slug)
        return {"machineName": slug, "state": "building", "zipSize": 0}, slug


def export_status(data_dir: str, slug: str) -> dict[str, Any]:
    with _transaction(data_dir) as conn:
        queue_position = _queue_position(conn, slug)
        if queue_position is not None:
            return {
                "machineName": slug,
                "state": "queued",
                "queuePosition": queue_position,
                "zipSize": 0,
            }

        row = _get_state_row(conn, slug)
        state = str(row["state"]) if row else "idle"
        zip_path = str(row["zip_path"]) if row and row["zip_path"] else None
        if state == "ready" and zip_path and not os.path.isfile(zip_path):
            _set_state(conn, slug, "failed", None, 0, "Prepared export file is missing")
            row = _get_state_row(conn, slug)
        return _state_payload(row, slug)


def export_get_ready_zip(data_dir: str, slug: str) -> str:
    with _transaction(data_dir) as conn:
        row = _get_state_row(conn, slug)
        if row is None or str(row["state"]) != "ready":
            raise LookupError("not_ready")
        zip_path = str(row["zip_path"] or "")
        if not zip_path or not os.path.isfile(zip_path):
            _set_state(conn, slug, "failed", None, 0, "Prepared export file is missing")
            raise FileNotFoundError("missing_zip")
        return zip_path


def export_mark_ready(data_dir: str, slug: str, zip_path: str, zip_size: int) -> None:
    with _transaction(data_dir) as conn:
        if _get_slot(conn) != slug:
            return
        _set_state(conn, slug, "ready", zip_path, zip_size, "")


def export_mark_failed(data_dir: str, slug: str, error: str) -> str | None:
    with _transaction(data_dir) as conn:
        _set_state(conn, slug, "failed", None, 0, error)
        if _get_slot(conn) != slug:
            return None
        return _release_slot_and_dequeue_next_locked(conn)


def export_cleanup_download(data_dir: str, slug: str, zip_path: str) -> str | None:
    with _transaction(data_dir) as conn:
        row = _get_state_row(conn, slug)
        if row and str(row["zip_path"] or "") == zip_path:
            _set_state(conn, slug, "idle", None, 0, "")
        if _get_slot(conn) != slug:
            return None
        return _release_slot_and_dequeue_next_locked(conn)


def export_remove_machine(data_dir: str, slug: str) -> str | None:
    with _transaction(data_dir) as conn:
        conn.execute("DELETE FROM export_states WHERE slug = ?", (slug,))
        conn.execute("DELETE FROM export_queue WHERE slug = ?", (slug,))
        if _get_slot(conn) != slug:
            return None
        return _release_slot_and_dequeue_next_locked(conn)


def export_protected_zip_paths(data_dir: str) -> set[str]:
    with _connect(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT zip_path FROM export_states
            WHERE zip_path IS NOT NULL
              AND state IN ('building', 'ready')
            """
        ).fetchall()
    protected: set[str] = set()
    for row in rows:
        path = str(row["zip_path"] or "").strip()
        if path:
            protected.add(os.path.abspath(path))
    return protected


def export_owns_build_slot(data_dir: str, slug: str) -> bool:
    with _connect(data_dir) as conn:
        return _get_slot(conn) == slug
