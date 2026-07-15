# ============================================================
# Text Chunker — Splitting Long Text into Manageable Pieces
#
# WHY DO WE CHUNK?
# Language models and embedding models have a maximum input size (context window).
# ChromaDB retrieves chunks, not whole documents — smaller, focused chunks
# produce more precise retrieval results because a chunk about "aspirin dosage"
# will score higher for that query than a chunk mixing dosage + side effects +
# contraindications all together.
#
# THE OVERLAP TRICK:
# Imagine cutting a book into pages, but each page includes the last 2 sentences
# of the previous page. This prevents information from being split mid-sentence
# across chunks. With overlap, every sentence appears in at least one chunk in
# full context.
#
# CHUNK SIZE CHOICE:
# - Too small (< 100 chars): chunks lack context, embeddings are meaningless
# - Too large (> 2000 chars): too much noise per chunk, retrieval becomes imprecise
# - Sweet spot: 400–700 characters (about 2–4 paragraphs)
# ============================================================

import logging
from typing import Generator

from src.ingestion.document import Document
from src import config

logger = logging.getLogger(__name__)


def split_text_into_chunks(
    text: str,
    chunk_size: int = config.CHUNK_SIZE,
    chunk_overlap: int = config.CHUNK_OVERLAP,
) -> list[str]:
    """
    Split a long text string into overlapping fixed-size chunks.

    Strategy:
    1. Try to split on paragraph boundaries (double newlines) first.
       This keeps semantically related sentences together.
    2. If a paragraph is longer than chunk_size, split it at the nearest
       whitespace before the limit (never cut mid-word).
    3. Each new chunk starts (chunk_overlap) characters before the previous
       chunk ended, ensuring boundary sentences appear in full context.

    Args:
        text:          The full text to split.
        chunk_size:    Maximum characters per chunk (from config).
        chunk_overlap: Characters of overlap between adjacent chunks (from config).

    Returns:
        List of text strings, each <= chunk_size characters.
    """
    if not text or not text.strip():
        return []

    # Normalise whitespace: collapse triple+ newlines to double
    text = "\n\n".join(
        block.strip()
        for block in text.split("\n\n")
        if block.strip()
    )

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size

        if end >= text_len:
            # Last chunk: take whatever is left
            chunk = text[start:]
        else:
            # Try to break at a paragraph boundary within the chunk
            para_break = text.rfind("\n\n", start, end)
            if para_break != -1 and para_break > start:
                end = para_break
            else:
                # Fall back: break at the last whitespace (don't cut a word)
                ws_break = text.rfind(" ", start, end)
                if ws_break != -1 and ws_break > start:
                    end = ws_break

            chunk = text[start:end].strip()

        chunk = chunk.strip()
        if chunk:
            chunks.append(chunk)

        # Advance start with overlap (but always advance at least 1 char to avoid loops)
        advance = max(1, (end - start) - chunk_overlap)
        start += advance

    logger.debug("Split text into %d chunks (size=%d, overlap=%d)",
                 len(chunks), chunk_size, chunk_overlap)
    return chunks


def chunk_documents(
    documents: list[Document],
    chunk_size: int = config.CHUNK_SIZE,
    chunk_overlap: int = config.CHUNK_OVERLAP,
) -> list[Document]:
    """
    Take a list of raw Documents (one per page / sheet / etc.) and produce a
    larger list of smaller chunked Documents.

    Each output Document:
    - Has a text field <= chunk_size characters
    - Inherits all metadata from its parent document, plus:
      - 'chunk_index': which chunk within the parent (0-based)
      - 'chunk_total': total chunks from that parent
    - Gets a new doc_id that includes the chunk index (making it unique)

    Args:
        documents:     Raw documents from a loader.
        chunk_size:    Max characters per chunk.
        chunk_overlap: Overlap between chunks.

    Returns:
        Flat list of chunked Document objects ready for embedding.
    """
    chunked: list[Document] = []

    for doc in documents:
        text_chunks = split_text_into_chunks(doc.text, chunk_size, chunk_overlap)

        if not text_chunks:
            logger.warning("Document %s produced no chunks (empty text?)", doc.doc_id)
            continue

        for i, chunk_text in enumerate(text_chunks):
            # Copy the parent's metadata and add chunk-level fields.
            #
            # `doc_id` is the PARENT's id, and carrying it here is load-bearing:
            # vector_store groups chunks into a document row by this key. Without
            # it, it fell back to "unknown_<chunk_id>" — a different id for every
            # chunk — so each chunk became its own one-chunk "document" and the
            # chunks-per-document breakdown was meaningless.
            chunk_metadata = {
                **doc.metadata,
                "doc_id": doc.doc_id,
                "chunk_index": i,
                "chunk_total": len(text_chunks),
            }
            # Derive a unique ID: parent_id + chunk index
            chunk_id = f"{doc.doc_id}_c{i}"

            chunked.append(
                Document(text=chunk_text, metadata=chunk_metadata, doc_id=chunk_id)
            )

    logger.info(
        "Chunked %d documents → %d chunks", len(documents), len(chunked)
    )
    return chunked
