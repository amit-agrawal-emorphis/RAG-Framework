from typing import Dict, List

from .heading_detector import detect_heading
from .hierarchy_builder import update_hierarchy_stack


def build_section_path(hierarchy_stack: List[Dict]) -> str:
    parts = []
    for h in hierarchy_stack:
        label = f"Section {h['number']}"
        if h.get("title"):
            label += f" {h['title']}"
        parts.append(label)
    #return " / ".join(parts)
    return " > ".join(parts)


def safe_section_number(hierarchy_stack: List[Dict]) -> str:
    if hierarchy_stack and hierarchy_stack[-1].get("number"):
        return hierarchy_stack[-1]["number"]
    return "0"


def create_chunk(
    chunk_id: str,
    doc_name: str,
    content: str,
    chunk_type: str,
    hierarchy_stack: List[Dict],
    page_number: int,
) -> Dict:

    section_number = safe_section_number(hierarchy_stack)

    return {
        "chunk_id": chunk_id,
        "doc_name": doc_name,
        "type": chunk_type,
        "section_number": section_number,
        "section_title": hierarchy_stack[-1]["title"]
        if hierarchy_stack
        else "Document Root",
        "section_path": build_section_path(hierarchy_stack),
        "level_depth": len(hierarchy_stack),
        "page_number": page_number,
        "content": content.strip(),
    }


def create_chunks(
    blocks: List[Dict],
    *,
    max_chunk_chars: int = 1500,
    chunk_overlap_chars: int = 200,
) -> List[Dict]:
    """
    Converts docling blocks into hierarchical chunks.
    Handles both paragraph and table blocks.

    Paragraph text is merged within a section until a heading/table/section change.
    If max_chunk_chars > 0, buffered text and oversized blocks are further split using
    the same character logic as flat ingestion (chunk_text).
    If max_chunk_chars <= 0, no size limit is applied (one chunk per buffer flush).
    """

    hierarchy_stack: List[Dict] = []
    chunks: List[Dict] = []
    chunk_counter = 0

    paragraph_buffer: List[str] = []
    buffer_page_number: int = 1
    buffer_section_signature: str = ""

    overlap_eff = chunk_overlap_chars
    if max_chunk_chars > 0 and overlap_eff >= max_chunk_chars:
        overlap_eff = max(0, max_chunk_chars - 1)

    def current_section_signature() -> str:
        return " > ".join(h["number"] for h in hierarchy_stack)

    def append_paragraph_pieces(content: str, page_no: int) -> None:
        nonlocal chunk_counter
        merged = (content or "").strip()
        if not merged:
            return
        if max_chunk_chars <= 0:
            chunk_counter += 1
            chunk_id = f"chunk_{chunk_counter:05d}"
            chunks.append(
                create_chunk(
                    chunk_id=chunk_id,
                    doc_name="unknown",
                    content=merged,
                    chunk_type="paragraph",
                    hierarchy_stack=hierarchy_stack,
                    page_number=page_no,
                )
            )
            return
        from rag_utils import chunk_text as _chunk_text

        for piece in _chunk_text(merged, max_chunk_chars, overlap_eff):
            if not piece.strip():
                continue
            chunk_counter += 1
            chunk_id = f"chunk_{chunk_counter:05d}"
            chunks.append(
                create_chunk(
                    chunk_id=chunk_id,
                    doc_name="unknown",
                    content=piece,
                    chunk_type="paragraph",
                    hierarchy_stack=hierarchy_stack,
                    page_number=page_no,
                )
            )

    def flush_buffer():
        nonlocal paragraph_buffer

        if not paragraph_buffer:
            return

        merged_text = " ".join(paragraph_buffer)
        paragraph_buffer.clear()
        append_paragraph_pieces(merged_text, buffer_page_number)

    for block in blocks:
        block_type = block.get("type", "paragraph")
        page_number = int(block.get("page_number", 1))

        # -----------------------
        # TABLE
        # -----------------------
        if block_type == "table":
            flush_buffer()

            from .table_extractor import create_table_chunk

            chunk_counter += 1
            chunk_id = f"chunk_{chunk_counter:05d}"

            chunk = create_table_chunk(
                table_rows=block.get("content", []),
                hierarchy_stack=hierarchy_stack,
                page_number=page_number,
                chunk_id=chunk_id,
                doc_name="unknown",
            )
            chunks.append(chunk)
            continue

        text = (block.get("content") or "").strip()
        if not text:
            continue

        is_heading, number, title = detect_heading(text)

        # -----------------------
        # HEADING
        # -----------------------
        if is_heading:
            flush_buffer()

            hierarchy_stack = update_hierarchy_stack(
                hierarchy_stack, number, title
            )

            # create heading chunk (your earlier fix)
            chunk_counter += 1
            chunk_id = f"chunk_{chunk_counter:05d}"

            chunks.append(
                create_chunk(
                    chunk_id=chunk_id,
                    doc_name="unknown",
                    content=title,
                    chunk_type="heading",
                    hierarchy_stack=hierarchy_stack,
                    page_number=page_number,
                )
            )

            buffer_section_signature = current_section_signature()
            continue

        # -----------------------
        # PARAGRAPH
        # -----------------------
        section_sig = current_section_signature()

        # new section -> flush buffer
        if paragraph_buffer and section_sig != buffer_section_signature:
            flush_buffer()

        # start new buffer
        if not paragraph_buffer:
            buffer_page_number = page_number
            buffer_section_signature = section_sig

        if max_chunk_chars > 0 and paragraph_buffer:
            candidate = " ".join(paragraph_buffer + [text])
            if len(candidate) > max_chunk_chars:
                flush_buffer()

        if not paragraph_buffer:
            buffer_page_number = page_number
            buffer_section_signature = section_sig

        if max_chunk_chars > 0 and len(text) > max_chunk_chars:
            flush_buffer()
            append_paragraph_pieces(text, page_number)
            continue

        paragraph_buffer.append(text)

    # flush remaining text
    flush_buffer()

    return chunks