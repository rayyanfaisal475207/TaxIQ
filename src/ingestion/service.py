# ============================================================
# Ingestion Service — Loads, Chunks, Embeds, and Stores
#
# This service is the entry point for Milestone 2.
# It takes files from data/documents/, processes them,
# pushes them to ChromaDB, and logs them to the SQLite DB.
# ============================================================

import logging
from pathlib import Path

from src import config
from src.ingestion.loader_router import route_and_load
from src.ingestion.chunker import chunk_documents
from src.retrieval.embedder import embed_texts
from src.retrieval.vector_store import upsert_documents
from src.database.pipeline_logger import log_ingested_chunk

logger = logging.getLogger(__name__)


async def ingest_directory(dir_path: Path = None, project_id: str = None) -> dict:
    """
    Ingest all supported files in a directory.
    If no dir_path provided, uses config.DOCUMENTS_DIR.

    Returns:
        dict: Summary of ingestion (files processed, chunks added).
    """
    if dir_path is None:
        dir_path = config.DOCUMENTS_DIR

    if not dir_path.exists():
        logger.error("Directory not found: %s", dir_path)
        return {"error": "Directory not found"}

    all_files = [f for f in dir_path.iterdir() if f.is_file() and f.name != "README.txt"]
    if not all_files:
        logger.info("No files to ingest in %s", dir_path)
        return {"status": "success", "files_processed": 0, "chunks_added": 0}

    logger.info("Starting ingestion of %d files from %s", len(all_files), dir_path)
    total_chunks = 0

    for file_path in all_files:
        stats = await ingest_file(file_path, project_id=project_id)
        total_chunks += stats.get("chunks_added", 0)

    return {
        "status": "success",
        "files_processed": len(all_files),
        "chunks_added": total_chunks
    }


async def ingest_file(file_path: Path, project_id: str = None, is_global: bool = False) -> dict:
    """
    Ingest a single file into the SHARED knowledge base.

    1. Load text (via loader_router)
    2. Chunk text
    3. Embed chunks
    4. Save chunks to Postgres/pgvector (the same table retrieval reads)
    5. Return stats, including the doc_id, so the caller can track the job

    `is_global=True` marks the document as part of the shared knowledge base —
    that is what admin uploads produce. Chat attachments never reach this
    function: their text is injected into a single conversation and is never
    embedded or indexed.
    """
    logger.info("Ingesting file: %s", file_path.name)
    try:
        # 1. Load
        documents = route_and_load(file_path)
        if not documents:
            logger.warning("No content extracted from %s", file_path.name)
            return {"chunks_added": 0, "error": "No text could be extracted from this file."}

        # 2. Chunk
        chunks = chunk_documents(
            documents,
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP
        )
        if not chunks:
            logger.warning("No chunks generated for %s", file_path.name)
            return {"chunks_added": 0, "error": "The file produced no text chunks."}

        # Tag every chunk so the vector store writes the right document row
        for chunk in chunks:
            if project_id:
                chunk.metadata["project_id"] = project_id
            chunk.metadata["is_global"] = is_global
            chunk.metadata["doc_type"] = file_path.suffix.lower().lstrip(".")

        # 3. Embed
        # Extract text for embedding
        texts_to_embed = [c.text for c in chunks]
        embeddings = await embed_texts(texts_to_embed, task_type="RETRIEVAL_DOCUMENT")

        if len(embeddings) != len(chunks):
            logger.error("Mismatch: %d chunks vs %d embeddings", len(chunks), len(embeddings))
            return {"chunks_added": 0}

        # Attach embeddings to metadata for vector_store to pick up
        # Note: In our current vector_store.py we might just pass text, let's see.
        # Actually ChromaDB can generate embeddings itself if we pass an embedding function,
        # but we do it manually to use Gemini. We'll pass embeddings to upsert_documents.

        # 4. Save to PostgreSQL
        ids = [c.doc_id for c in chunks]
        metadatas = [c.metadata for c in chunks]
        await upsert_documents(
            ids=ids,
            texts=texts_to_embed,
            embeddings=embeddings,
            metadatas=metadatas
        )

        # 5. Log to SQLite
        # ext = file_path.suffix.lower().lstrip(".")
        # for c, emb in zip(chunks, embeddings):
        #     log_ingested_chunk(
        #         chunk_id=c.doc_id,
        #         source_file=file_path.name,
        #         source_path=str(file_path),
        #         file_type=ext,
        #         chunk_index=c.metadata.get("chunk_index", 0),
        #         chunk_total=c.metadata.get("chunk_total", 1),
        #         chunk_text=c.text,
        #         embedding_model=config.GEMINI_EMBEDDING_MODEL,
        #         embedding_dims=len(emb)
        #     )

        # Record the chunk count on the document row so the dashboard's
        # "chunks per document" breakdown is accurate without counting 88k rows.
        doc_id = chunks[0].metadata.get("doc_id") if chunks else None
        if doc_id:
            try:
                from src.data_gateway import get_gateway
                gateway = await get_gateway()
                await gateway.log_document(
                    doc_id=str(doc_id),
                    filename=file_path.name,
                    doc_type=file_path.suffix.lower().lstrip("."),
                    chunk_count=len(chunks),
                    is_global=is_global,
                )
            except Exception as exc:
                logger.warning("Could not update document record for %s: %s", file_path.name, exc)

        logger.info("Successfully ingested %d chunks from %s", len(chunks), file_path.name)

        return {
            "doc_id": str(doc_id) if doc_id else None,
            "chunks_added": len(chunks),
            "char_count": sum(len(d.text) for d in documents),
            "ocr_pages": sum(1 for d in documents if d.metadata.get("extraction_method") == "vision_llm"),
            "total_pages": len(documents),
            "effective_from": documents[0].metadata.get("effective_from") if documents else None,
            "effective_to": documents[0].metadata.get("effective_to") if documents else None,
        }

    except Exception as exc:
        logger.error("Failed to ingest %s: %s", file_path.name, exc)
        return {"chunks_added": 0, "error": str(exc)}
