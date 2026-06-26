"""
Chunk Docling-exported Markdown for RAG: page breaks, headings, then char limits.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_HEADING_SPLIT = re.compile(r"(?m)(?=^#{1,6}\s+\S)")
_EMBEDDED_IMAGE_MD_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\("
    r"\s*data:image/[a-zA-Z0-9.+-]+;base64,"
    r"(?P<data>(?:[A-Za-z0-9+/=]+[ \t\r\n]*)+)"
    r"\)",
    flags=re.IGNORECASE,
)
_BARE_DATA_IMAGE_RE = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
    flags=re.IGNORECASE,
)


def _first_heading_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or s
    return ""


def _strip_embedded_image_payloads(md: str) -> str:
    """
    Docling can export page/figure images as base64 Markdown. Those payloads are
    useful for the image index, but poison text chunks and embeddings.
    """

    def repl(match: re.Match[str]) -> str:
        alt = (match.group("alt") or "").strip()
        if not alt or alt.lower() in {"image", "img", "picture", "figure"}:
            return "\n"
        return f"\n{alt}\n"

    md = _EMBEDDED_IMAGE_MD_RE.sub(repl, md or "")
    md = _BARE_DATA_IMAGE_RE.sub("\n", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def chunk_markdown_for_rag(
    md: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> List[Dict[str, Any]]:
    """
    Split markdown into chunks: by Docling page placeholders, then ATX headings,
    then rag_utils.chunk_text for oversized sections.
    """
    from rag_utils import chunk_text

    md = _strip_embedded_image_payloads(md)
    if not md:
        return []

    pages = md.split("<!-- page break -->")
    out: List[Dict[str, Any]] = []

    for page_no, page_md in enumerate(pages, start=1):
        page_md = page_md.strip()
        if not page_md:
            continue

        sections = _HEADING_SPLIT.split(page_md)
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue

            heading = _first_heading_line(sec)
            pieces = (
                chunk_text(sec, chunk_size_chars, chunk_overlap_chars)
                if chunk_size_chars > 0
                else [sec]
            )
            for piece in pieces:
                p = piece.strip()
                if not p:
                    continue
                out.append(
                    {
                        "content": p,
                        "page_number": page_no,
                        "section_heading": heading,
                        "chunk_type": "markdown",
                    }
                )

    return out
