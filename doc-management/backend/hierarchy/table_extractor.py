from typing import List, Dict


def flatten_table(table_rows: List[List[str]]) -> str:
    """
    Converts table rows into markdown table for better embeddings.
    """

    lines: List[str] = []

    for row in table_rows:
        clean_row = [cell.strip() for cell in row if cell and cell.strip()]
        if clean_row:
            lines.append("| " + " | ".join(clean_row) + " |")

    if len(lines) >= 1:
        col_count = lines[0].count("|") - 1
        separator = "|" + "|".join([" --- "] * col_count) + "|"
        lines.insert(1, separator)

    return "\n".join(lines)


def create_table_chunk(
    table_rows: List[List[str]],
    hierarchy_stack: List[Dict],
    page_number: int,
    chunk_id: str,
    doc_name: str,
):
    from .chunking import create_chunk

    table_text = flatten_table(table_rows)

    return create_chunk(
        chunk_id=chunk_id,
        doc_name=doc_name,
        content=table_text,
        chunk_type="table",
        hierarchy_stack=hierarchy_stack,
        page_number=page_number,
    )