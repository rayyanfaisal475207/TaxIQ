# ============================================================
# Embedder — Google Gemini Embeddings (new google-genai SDK)
#
# MODEL: gemini-embedding-001  (3072 dimensions)
#
# WHY GEMINI FOR EMBEDDINGS?
#   Groq has no embedding API. Gemini's gemini-embedding-001 produces
#   3072-dimensional vectors — high-resolution semantic space.
#   Uses the new google-genai SDK (google-generativeai is deprecated).
#
# TASK TYPES:
#   Gemini supports task_type hints that adjust the embedding space:
#   - "RETRIEVAL_DOCUMENT": chunks stored in ChromaDB at ingestion time
#   - "RETRIEVAL_QUERY":    user query embedded at search time
#   Using the right task type improves retrieval accuracy.
#
# IMPORTANT: All chunks must use the SAME embedding model + dimensions.
# If you switch models, you must delete ChromaDB and re-ingest everything.
# ============================================================

import asyncio
import logging

from src import config

logger = logging.getLogger(__name__)


async def embed_text(text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
    """
    Embed a single text (used at query time).

    Args:
        text:      The search query to embed.
        task_type: "RETRIEVAL_QUERY" for queries (default).

    Returns:
        A 3072-dimensional embedding vector.
    """
    vectors = await embed_texts([text], task_type=task_type)
    return vectors[0]


async def embed_texts(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """
    Batch embed multiple texts (used at ingestion time).

    Args:
        texts:     List of text chunks to embed.
        task_type: "RETRIEVAL_DOCUMENT" for stored chunks (default).

    Returns:
        List of 3072-dimensional vectors in the same order as input.
    """
    if not texts:
        return []

    if config.EMBEDDING_PROVIDER == "gemini":
        return await _embed_gemini(texts, task_type)
    elif config.EMBEDDING_PROVIDER == "openai":
        return await _embed_openai(texts)
    elif config.EMBEDDING_PROVIDER == "local":
        return await _embed_local(texts)
    else:
        # Auto-detect: prefer local to avoid rate limits if no keys
        if config.GEMINI_API_KEY and config.EMBEDDING_PROVIDER == "gemini":
            return await _embed_gemini(texts, task_type)
        else:
            return await _embed_local(texts)


from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ── Gemini Embeddings (google-genai SDK) ──────────────────────────────────────

async def _embed_gemini(texts: list[str], task_type: str) -> list[list[float]]:
    """
    Embed texts using Gemini gemini-embedding-001 via the new google-genai SDK.

    The new SDK's embed_content() accepts a list of texts in one call,
    returning one embedding per input. Much cleaner than the old SDK.

    Args:
        texts:     List of texts to embed.
        task_type: Gemini task hint ("RETRIEVAL_DOCUMENT" or "RETRIEVAL_QUERY").

    Returns:
        List of 3072-dim float vectors.
    """
    from google.genai import types
    from src.llm.client import _get_gemini_client

    # Reuse the shared cached client — constructing one per embed call added
    # connection setup to every retrieval (3+ times per query with expansion).
    client = _get_gemini_client()

    from google.genai.errors import ClientError

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(ClientError),
        reraise=True
    )
    def _do_embed_batch(batch_texts: list[str]) -> list[list[float]]:
        result = client.models.embed_content(
            model=config.GEMINI_EMBEDDING_MODEL,
            contents=batch_texts,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        return [emb.values for emb in result.embeddings]

    batch_size = 100
    all_embeddings = []
    
    # Process in batches sequentially. 
    # Batching to 100 uses only 1 API call instead of 100, avoiding limits.
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # to_thread prevents blocking the async event loop during the API call
        import asyncio
        batch_embs = await asyncio.to_thread(_do_embed_batch, batch)
        all_embeddings.extend(batch_embs)

    logger.debug(
        "Gemini embedded %d text(s) via %s (task=%s, dims=%d)",
        len(texts), config.GEMINI_EMBEDDING_MODEL, task_type,
        len(all_embeddings[0]) if all_embeddings else 0,
    )
    return all_embeddings


# ── OpenAI Embeddings (fallback) ───────────────────────────────────────────────

async def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = await asyncio.to_thread(
        lambda: client.embeddings.create(
            model=config.OPENAI_EMBEDDING_MODEL,
            input=texts,
        )
    )
    return [e.embedding for e in sorted(response.data, key=lambda e: e.index)]

# ── Local Embeddings (CPU fallback) ───────────────────────────────────────────

async def _embed_local(texts: list[str]) -> list[list[float]]:
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    import asyncio
    
    ef = DefaultEmbeddingFunction()

    # Chroma default embedder returns a list of vectors
    embeddings = await asyncio.to_thread(ef, texts)
    # Chroma returns numpy arrays — convert to plain lists so the vectors are
    # JSON-serializable (the Supabase REST RPC path requires it).
    embeddings = [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]

    logger.debug(
        "Local embedded %d text(s) (dims=%d)",
        len(texts),
        len(embeddings[0]) if embeddings else 0,
    )
    return embeddings
