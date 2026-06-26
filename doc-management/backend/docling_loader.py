import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Generator, List, TextIO
 
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.base import ImageRefMode
 
logger = logging.getLogger(__name__)
 
 
class _FilterPdfFontNoiseStderr:
    """Drop PDFium/Poppler-style lines: ``Syntax Warning: Invalid Font Weight``."""
 
    _MARKER = "Invalid Font Weight"
 
    def __init__(self, real: TextIO) -> None:
        self._real = real
        self._buf = ""
 
    def write(self, s: str) -> int:
        if not s:
            return 0
        # tqdm and similar use carriage returns without newlines — pass through.
        if "\n" not in s and "\r" in s:
            self._real.write(s)
            return len(s)
        self._buf += s
        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            full = line + "\n"
            if self._MARKER in full and "Syntax Warning" in full:
                continue
            self._real.write(full)
        return len(s)
 
    def flush(self) -> None:
        if self._buf:
            if self._MARKER not in self._buf or "Syntax Warning" not in self._buf:
                self._real.write(self._buf)
            self._buf = ""
        self._real.flush()
 
    def __getattr__(self, name: str):
        return getattr(self._real, name)
 
 
@contextlib.contextmanager
def _suppress_pdf_font_noise_stderr() -> Generator[None, None, None]:
    prev = sys.stderr
    sys.stderr = _FilterPdfFontNoiseStderr(prev)
    try:
        yield
    finally:
        try:
            sys.stderr.flush()
        finally:
            sys.stderr = prev
 
 
def _emit(msg: str) -> None:
    print(msg, flush=True)
 
 
def _heartbeat_while_converting(
    stop: threading.Event,
    label: str,
    pdf_path: str,
    *,
    interval_sec: float = 30.0,
) -> None:
    """Print periodic status so long Docling runs do not look frozen."""
    n = 0
    bn = os.path.basename(pdf_path)
    while not stop.wait(interval_sec):
        n += 1
        secs = int(n * interval_sec)
        _emit(
            f"[docling] {label} — still converting {bn!r} (~{secs}s elapsed; "
            f"layout + table structure on CPU often takes many minutes on large PDFs)…"
        )
 
 
def _export_document_to_markdown(doc: Any, *, embed_images: bool) -> str:
    image_mode = ImageRefMode.EMBEDDED if embed_images else ImageRefMode.PLACEHOLDER
    attempts: list[dict[str, Any]] = [
        {
            "traverse_pictures": True,
            "image_mode": image_mode,
            "image_placeholder": "[Image]",
            "enable_chart_tables": True,
            "compact_tables": False,
            "page_break_placeholder": "<!-- page break -->",
            "include_annotations": True,
        },
        {
            "image_mode": image_mode,
            "image_placeholder": "[Image]",
            "enable_chart_tables": True,
            "compact_tables": False,
            "page_break_placeholder": "<!-- page break -->",
            "include_annotations": True,
        },
        {
            "image_mode": image_mode,
            "image_placeholder": "[Image]",
            "page_break_placeholder": "<!-- page break -->",
        },
        {},
    ]
    for kwargs in attempts:
        try:
            md = doc.export_to_markdown(**kwargs)
            logger.info(
                "docling_markdown export_signature_selected kwargs=%s",
                ",".join(sorted(kwargs.keys())) if kwargs else "none",
            )
            return md
        except TypeError as e:
            logger.warning(
                "docling_markdown export_signature_rejected kwargs=%s err=%s",
                ",".join(sorted(kwargs.keys())) if kwargs else "none",
                e,
            )
            continue
    raise TypeError("Docling export_to_markdown incompatible with all fallback signatures")
 
 
def pdf_text_layer_has_words(pdf_path: str) -> bool:
    """
    True if the PDF text layer yields at least one alphanumeric character (a "word"
    in the practical sense). Uses ``pdftotext`` when available, else pypdf.
 
    If this is False, the file is treated as scan/image-only for pipeline selection
    and Docling runs with OCR.
    """
    text = ""
    try:
        text = subprocess.check_output(
            ["pdftotext", pdf_path, "-"],
            text=True,
            errors="ignore",
            timeout=300,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.info(
            "docling_pdf_text_probe pdftotext_unavailable_or_failed err=%s trying_pypdf",
            type(e).__name__,
        )
        try:
            from pypdf import PdfReader
 
            with _suppress_pdf_font_noise_stderr():
                reader = PdfReader(pdf_path)
                parts: List[str] = []
                for page in reader.pages:
                    parts.append(page.extract_text() or "")
                text = "\n".join(parts)
        except Exception as e2:
            logger.warning("docling_pdf_text_probe pypdf_failed err=%s", e2)
            return False
 
    t = (text or "").strip()
    if not t:
        return False
    return any(ch.isalnum() for ch in t)
 
 
def build_pdf_converter(
    *,
    extract_tables: bool = True,
    force_backend_text: bool = False,
    embed_images: bool = False,
) -> DocumentConverter:
    """
    PDF pipeline tuned for structure + optional native text (skips OCR when True).
    """
    opts = PdfPipelineOptions(
        do_table_structure=extract_tables,
        do_ocr=not force_backend_text,
        force_backend_text=force_backend_text,
    )
    # Required for markdown image export to include actual image payloads/refs
    # instead of "Image not available" placeholders.
    if embed_images and hasattr(opts, "generate_picture_images"):
        setattr(opts, "generate_picture_images", True)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        }
    )
 
 
def convert_to_markdown_via_docling(
    path: str,
    *,
    extract_tables: bool = True,
    force_backend_text: bool = False,
    embed_images: bool = False,
    pdf_try_native_first: bool = True,
) -> str:
    """
    Convert PDF or DOCX through Docling and serialize to Markdown (tables as MD;
    images as placeholders or base64 per embed_images).
    traverse_pictures=True helps OCR / scanned PDFs where text sits under PictureItem.
 
    For PDFs, when ``force_backend_text`` is False and ``pdf_try_native_first`` is True,
    runs ``pdftotext`` (or pypdf) on the file: if there is **no** extractable word-like
    text, uses OCR; otherwise uses embedded text only in Docling (fast, no RapidOCR).
    With ``--docling_ocr_always`` (``pdf_try_native_first=False``), always uses OCR.
    """
    ext = os.path.splitext(path)[1].lower()
    logger.info(
        "docling_markdown start path=%s ext=%s tables=%s force_backend_text=%s "
        "embed_images=%s pdf_try_native_first=%s",
        os.path.abspath(path),
        ext,
        extract_tables,
        force_backend_text,
        embed_images,
        pdf_try_native_first,
    )
 
    def run_convert(converter: DocumentConverter, label: str) -> str:
        _emit(
            f"[docling] {label} — starting "
            f"(layout/table models may load; CPU-bound on large PDFs)…"
        )
        t0 = time.perf_counter()
        stop_hb = threading.Event()
        hb = threading.Thread(
            target=_heartbeat_while_converting,
            args=(stop_hb, label, path),
            kwargs={"interval_sec": 30.0},
            daemon=True,
            name="docling-ingest-heartbeat",
        )
        hb.start()
        try:
            with _suppress_pdf_font_noise_stderr():
                result = converter.convert(path)
        finally:
            stop_hb.set()
        elapsed = time.perf_counter() - t0
        if not result or not result.document:
            raise RuntimeError("Docling conversion produced no document")
        md = _export_document_to_markdown(result.document, embed_images=embed_images)
        _emit(
            f"[docling] {label} — finished in {elapsed:.1f}s, markdown length {len(md or '')} chars."
        )
        logger.info(
            "docling_markdown phase_done label=%s seconds=%.3f md_chars=%d",
            label,
            elapsed,
            len(md or ""),
        )
        return md
 
    if ext == ".docx":
        md = run_convert(DocumentConverter(), "DOCX → Markdown")
        return md
 
    if ext != ".pdf":
        raise ValueError(f"Markdown Docling path supports .pdf and .docx, got: {ext}")
 
    if force_backend_text:
        converter = build_pdf_converter(
            extract_tables=extract_tables,
            force_backend_text=True,
            embed_images=embed_images,
        )
        return run_convert(converter, "PDF (embedded text only, no OCR)")
 
    if pdf_try_native_first:
        if pdf_text_layer_has_words(path):
            logger.info("docling_markdown pdftotext_has_alnum using_embedded_text_only")
            _emit(
                "[docling] PDF text layer has extractable text (pdftotext/pypdf); "
                "using Docling with embedded text only (no OCR)."
            )
            fast = build_pdf_converter(
                extract_tables=extract_tables,
                force_backend_text=True,
                embed_images=embed_images,
            )
            return run_convert(fast, "PDF embedded text (no OCR)")
        logger.info("docling_markdown pdftotext_empty_using_ocr")
        _emit(
            "[docling] No words in PDF text layer (pdftotext/pypdf empty); "
            "using OCR + layout (slow on CPU)."
        )
 
    slow = build_pdf_converter(
        extract_tables=extract_tables,
        force_backend_text=False,
        embed_images=embed_images,
    )
    return run_convert(slow, "PDF with OCR + layout")