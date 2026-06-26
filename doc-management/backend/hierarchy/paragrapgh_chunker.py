from typing import List, Dict
from .chunking import create_chunk


def create_paragraph_chunk(
    paragraph: str,
    hierarchy_stack: List[Dict],
    page_number: int,
    chunk_id: str,
    doc_name: str,
):

    return create_chunk(
        chunk_id=chunk_id,
        doc_name=doc_name,
        content=paragraph,
        chunk_type="paragraph",
        hierarchy_stack=hierarchy_stack,
        page_number=page_number,
    )
