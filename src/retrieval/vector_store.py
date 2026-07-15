"""
PostgreSQL pgvector Store Wrapper

Replaces ChromaDB with a native PostgreSQL + pgvector implementation.
Features native hybrid search (cosine distance <-> pgvector + ts_rank <-> full text search)
combined natively using Reciprocal Rank Fusion (RRF) within a single SQL query.
"""
import logging
from typing import Optional
from src.data_gateway import get_gateway

# `logger` was used throughout this module but never defined: the info() call at
# the end of a successful upsert raised NameError, which ingest_file caught and
# reported as "0 chunks added". Chunks were in fact being written — every
# ingestion just *looked* like it had failed.
logger = logging.getLogger(__name__)


async def upsert_documents(
    ids: list[str],
    texts: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    """
    Store or update document chunks in PostgreSQL.
    Uses Supabase REST API if configured, otherwise falls back to SQLAlchemy.
    """
    if not ids:
        logger.warning("upsert_documents called with empty list.")
        return

    doc_params = []
    chunk_params = []

    for i in range(len(ids)):
        meta = metadatas[i] or {}
        doc_id = meta.get("doc_id", f"unknown_{ids[i]}")
        chunk_index = meta.get("chunk_index", 0)
        source_file = meta.get("source", "unknown")
        project_id = meta.get("project_id")
        emb_list = embeddings[i].tolist() if hasattr(embeddings[i], "tolist") else embeddings[i]

        doc_params.append({
            "doc_id": str(doc_id),
            "filename": str(source_file),
            # Shared knowledge-base documents are global: retrieval matches them
            # for every user. Chat attachments never come through here at all.
            "doc_type": meta.get("doc_type") or "document",
            "is_global": bool(meta.get("is_global", False)),
            "project_id": project_id,
        })
        chunk_params.append({
            "chunk_id": ids[i],
            "doc_id": str(doc_id),
            "chunk_index": int(chunk_index),
            "chunk_text": texts[i],
            "embedding": emb_list,
            "source_file": str(source_file)
        })

    # Use unique doc_ids to prevent duplicates in upsert payload
    unique_docs = list({d["doc_id"]: d for d in doc_params}.values())

    gateway = await get_gateway()
    
    unique_docs = list({d["doc_id"]: d for d in doc_params}.values())
    await gateway.insert_documents(unique_docs)
    
    # Bulk upsert chunks
    await gateway.insert_chunks(chunk_params)
    logger.info("Upserted %d chunks via DataGateway", len(ids))

async def query_similar(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 10,
    where: Optional[dict] = None,
    target_date: Optional[int] = None,
) -> list[dict]:
    """
    Find the top-k most similar document chunks using Hybrid Native Postgres Search.
    Fuses pgvector cosine similarity and ts_rank keyword similarity via Reciprocal Rank Fusion (RRF).
    """
    emb_list = query_embedding.tolist() if hasattr(query_embedding, "tolist") else query_embedding
    
    gateway = await get_gateway()
    return await gateway.query_similar_chunks(query_text, emb_list, top_k, where)

def get_all_documents_metadata() -> list[dict]:
    """
    Used by pipeline tests/evaluation to fetch all documents.
    """
    logger.warning("get_all_documents_metadata is deprecated in pgvector setup.")
    return []
