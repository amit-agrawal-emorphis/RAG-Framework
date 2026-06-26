import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class PdfRef:
    doc_name: str
    doc_path: str


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    created_at: str
    last_message_at: str | None
    message_count: int
    first_message: str | None


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                sources_json TEXT,
                images_json TEXT,
                FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
                ON chat_messages(session_id, created_at);

            CREATE TABLE IF NOT EXISTS chat_message_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                doc_name TEXT NOT NULL,
                doc_path TEXT NOT NULL,
                FOREIGN KEY(message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sources_doc_name ON chat_message_sources(doc_name);
            CREATE INDEX IF NOT EXISTS idx_sources_doc_path ON chat_message_sources(doc_path);
            """
        )
        cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
        if cols and "images_json" not in cols:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN images_json TEXT")


def create_session(db_path: str) -> str:
    session_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO chat_sessions(session_id, created_at) VALUES (?, ?)",
            (session_id, _utc_iso()),
        )
    return session_id


def get_latest_session_id(db_path: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id FROM chat_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row["session_id"]) if row else None


def list_sessions(db_path: str, *, limit: int = 50) -> list[SessionSummary]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              s.session_id AS session_id,
              s.created_at AS created_at,
              MAX(m.created_at) AS last_message_at,
              COUNT(m.id) AS message_count,
              (
                SELECT m2.content
                FROM chat_messages m2
                WHERE m2.session_id = s.session_id
                ORDER BY m2.id ASC
                LIMIT 1 OFFSET 1
              ) AS first_message,
              (
                SELECT m3.content
                FROM chat_messages m3
                WHERE m3.session_id = s.session_id
                ORDER BY m3.id ASC
                LIMIT 1
              ) AS fallback_first_message
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.session_id
            GROUP BY s.session_id, s.created_at
            ORDER BY COALESCE(MAX(m.created_at), s.created_at) DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        SessionSummary(
            session_id=str(r["session_id"]),
            created_at=str(r["created_at"]),
            last_message_at=str(r["last_message_at"]) if r["last_message_at"] else None,
            message_count=int(r["message_count"] or 0),
            first_message=str(r["first_message"] or r["fallback_first_message"])
            if (r["first_message"] or r["fallback_first_message"])
            else None,
        )
        for r in rows
    ]


def append_message(
    db_path: str,
    *,
    session_id: str,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
    images_json = json.dumps(images, ensure_ascii=False) if images else None
    created_at = _utc_iso()

    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chat_sessions(session_id, created_at) VALUES (?, ?)",
            (session_id, created_at),
        )
        cur = conn.execute(
            """
            INSERT INTO chat_messages(session_id, created_at, role, content, sources_json, images_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, created_at, role, content, sources_json, images_json),
        )
        message_id = int(cur.lastrowid)

        if sources:
            rows: list[tuple[int, str, str]] = []
            for s in sources:
                name = (s.get("doc_name") or "").strip()
                path = (s.get("doc_path") or "").strip()
                if not name and not path:
                    continue
                rows.append((message_id, name or "(unknown)", path))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO chat_message_sources(message_id, doc_name, doc_path)
                    VALUES (?, ?, ?)
                    """,
                    rows,
                )

    return message_id, created_at


def load_messages_for_session(db_path: str, session_id: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, sources_json, images_json, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        msg: dict[str, Any] = {
            "message_id": int(r["id"]),
            "role": str(r["role"]),
            "content": str(r["content"]),
            "ts": str(r["created_at"]) if r["created_at"] else None,
        }
        if r["sources_json"]:
            try:
                msg["sources"] = json.loads(str(r["sources_json"]))
            except Exception:
                pass
        ij = r["images_json"]
        if ij:
            try:
                parsed = json.loads(str(ij))
                if isinstance(parsed, list):
                    msg["images"] = parsed
            except Exception:
                pass
        out.append(msg)
    return out


def list_distinct_pdfs(db_path: str) -> list[PdfRef]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT doc_name, doc_path, COUNT(*) AS cnt
            FROM chat_message_sources
            GROUP BY doc_name, doc_path
            ORDER BY cnt DESC, doc_name ASC
            """
        ).fetchall()
    return [PdfRef(doc_name=str(r["doc_name"]), doc_path=str(r["doc_path"])) for r in rows]


def find_sessions_for_pdf(db_path: str, *, doc_name: str, doc_path: str) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.session_id
            FROM chat_messages m
            JOIN chat_message_sources s ON s.message_id = m.id
            WHERE s.doc_name = ? AND s.doc_path = ?
            ORDER BY m.id DESC
            """,
            (doc_name, doc_path),
        ).fetchall()
    return [str(r["session_id"]) for r in rows]

