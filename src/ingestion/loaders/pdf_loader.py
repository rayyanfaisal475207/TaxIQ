# ============================================================
# PDF Loader — Text-Based and Scanned PDFs
#
# PDFs come in two flavours:
#   1. Text-based PDFs: The text is stored as actual characters inside the
#      file. Tools like PyMuPDF can extract it directly. Fast and accurate.
#   2. Scanned PDFs: These are images of pages. There is no text layer.
#      PyMuPDF will return an empty string for each page. We detect this
#      and fall back to the vision LLM approach (same as the image loader).
#
# HOW PDF EXTRACTION WORKS (ANALOGY):
# Think of a PDF as a filing cabinet. Text-based PDFs store documents as
# Word files in the cabinet — you can open and read them directly.
# Scanned PDFs store photographs of the documents — you need to look at
# the picture and type out what you see (= OCR or vision LLM).
# ============================================================

import logging
import json
import re
from pathlib import Path

from dateutil import parser
from src.ingestion.document import Document

logger = logging.getLogger(__name__)

# Minimum characters per page before we consider extraction "failed"
# and fall back to vision. Some pages have headers/footers but no real text.
MIN_TEXT_CHARS = 50


def _extract_temporal_metadata(file_path: Path) -> tuple[int, int]:
    """Extract effective_from and effective_to dates as YYYYMMDD integers for ChromaDB filtering."""
    effective_from = 19900101
    effective_to = 99991231

    # Try checking metadata.jsonl for SROs
    metadata_file = file_path.parent / "metadata.jsonl"
    if metadata_file.exists():
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    data = json.loads(line)
                    if file_path.name in data.get("pdf_url", ""):
                        raw_date = data.get("issue_date_raw", "")
                        try:
                            dt = parser.parse(raw_date, dayfirst=True)
                            effective_from = int(dt.strftime("%Y%m%d"))
                            return effective_from, effective_to
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Failed to parse metadata.jsonl for %s: %s", file_path.name, e)

    # Fallback to parsing filename (e.g., FinanceAct2026.pdf)
    match = re.search(r'(20\d{2})', file_path.name)
    if match:
        year = match.group(1)
        effective_from = int(f"{year}0701") # Default to fiscal year start
        
        # Check if it's an auto-expiring rate schedule
        if "FinanceAct" in file_path.name or "Finance_Act" in file_path.name:
            next_year = int(year) + 1
            effective_to = int(f"{next_year}0630")

    return effective_from, effective_to


def load_pdf(file_path: Path) -> list[Document]:
    """
    Load a PDF file, returning one Document per page.

    For each page:
    - If text extraction succeeds (>= MIN_TEXT_CHARS), use the extracted text.
    - If extraction returns little or no text, the page is likely scanned.
      Falls back to vision LLM (via the image loader's vision call).

    Args:
        file_path: Path to the .pdf file.

    Returns:
        List of Documents, one per page with text content.
    """
    try:
        import fitz  # PyMuPDF — install: pip install pymupdf
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF loading. "
            "Install with: pip install pymupdf"
        )

    documents: list[Document] = []

    try:
        pdf = fitz.open(str(file_path))
        logger.info("Opened PDF %s (%d pages)", file_path.name, pdf.page_count)
        
        effective_from, effective_to = _extract_temporal_metadata(file_path)

        for page_num in range(pdf.page_count):
            page = pdf[page_num]
            text = page.get_text().strip()

            if len(text) >= MIN_TEXT_CHARS:
                # Text-based page — use extracted text directly
                doc = Document(
                    text=text,
                    metadata={
                        "source": file_path.name,
                        "source_path": str(file_path),
                        "type": "pdf",
                        "page": page_num + 1,       # 1-indexed for humans
                        "total_pages": pdf.page_count,
                        "extraction_method": "pymupdf",
                        "effective_from": effective_from,
                        "effective_to": effective_to,
                    },
                )
                documents.append(doc)
                logger.debug(
                    "  Page %d: extracted %d chars via text layer",
                    page_num + 1, len(text)
                )

            else:
                # Scanned page — fall back to vision LLM
                logger.warning(
                    "  Page %d of %s: text layer is empty/garbled (%d chars). "
                    "Falling back to vision LLM.",
                    page_num + 1, file_path.name, len(text)
                )
                vision_docs = _load_scanned_page_with_vision(
                    pdf, page_num, file_path, effective_from, effective_to
                )
                documents.extend(vision_docs)

        pdf.close()

    except Exception as exc:
        logger.error("Failed to load PDF %s: %s", file_path.name, exc)
        raise

    logger.info(
        "Loaded %d pages from %s", len(documents), file_path.name
    )
    return documents


def _load_scanned_page_with_vision(
    pdf,
    page_num: int,
    file_path: Path,
    effective_from: int,
    effective_to: int,
) -> list[Document]:
    """
    Render a PDF page to a PNG image and pass it to the vision LLM for OCR.

    This is the same technique as load_image_with_vision(), but applied to
    a rendered page rather than a standalone image file.

    Args:
        pdf:       Open PyMuPDF document object.
        page_num:  0-based page index.
        file_path: Original PDF path (for metadata).
        effective_from: Extracted effective start date.
        effective_to: Extracted effective end date.

    Returns:
        List of one Document with vision-extracted text (or empty if vision fails).
    """
    max_retries = 10
    for attempt in range(max_retries):
        try:
            import tempfile
            import os
            import time
            from src.ingestion.loaders.image_loader import _describe_image_bytes

            page = pdf[page_num]
            # Render at 2x resolution (matrix scale=2) for better OCR quality
            mat = __import__("fitz").Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            image_bytes = pix.tobytes("png")

            extracted_text = _describe_image_bytes(image_bytes, "png")

            if not extracted_text:
                logger.warning(
                    "Vision LLM returned no text for page %d of %s",
                    page_num + 1, file_path.name
                )
                return []

            return [
                Document(
                    text=extracted_text,
                    metadata={
                        "source": file_path.name,
                        "source_path": str(file_path),
                        "type": "pdf",
                        "page": page_num + 1,
                        "extraction_method": "vision_llm",
                        "effective_from": effective_from,
                        "effective_to": effective_to,
                    },
                )
            ]

        except Exception as exc:
            err_msg = str(exc).lower()
            if "limit: 20" in err_msg:
                logger.error(
                    "Daily Gemini Free Tier Quota (20 requests) exhausted. Aborting retries for page %d of %s.",
                    page_num + 1, file_path.name
                )
                raise
            
            if "quota" in err_msg or "429" in err_msg or "exhausted" in err_msg:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Rate limit hit on page %d of %s. Retrying in 120s... (%d/%d)",
                        page_num + 1, file_path.name, attempt + 1, max_retries
                    )
                    import time
                    time.sleep(120)
                    continue
                else:
                    logger.error(
                        "Vision fallback failed for page %d of %s after %d retries: %s",
                        page_num + 1, file_path.name, max_retries, exc
                    )
                    raise  # Re-raise so the outer robust_ingest catches it and fails the whole file
            else:
                logger.error(
                    "Vision fallback failed for page %d of %s: %s",
                    page_num + 1, file_path.name, exc
                )
                return []
    
    return []
