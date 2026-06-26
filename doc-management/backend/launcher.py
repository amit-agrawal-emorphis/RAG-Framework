"""
Ingestion entrypoint: run from repo root so paths resolve.

Example:
  python doc-management/backend/launcher.py --docs_dir data/docs --out_dir data ...
Or:
  python -m ingest_and_export ...  (with PYTHONPATH including both backends)
"""
from __future__ import annotations

import os
import sys


def _prepare() -> str:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.chdir(root)
    dq = os.path.join(root, "doc-qna", "backend")
    dm = os.path.join(root, "doc-management", "backend")
    for p in (dm, dq, root):
        if p not in sys.path:
            sys.path.insert(0, p)
    return root


def main() -> None:
    _prepare()
    from ingest_and_export import main as ingest_main

    ingest_main()


if __name__ == "__main__":
    main()
