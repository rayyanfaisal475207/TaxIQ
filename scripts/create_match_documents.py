import asyncio
from sqlalchemy import text
from src.database.postgres import engine

async def create_rpc():
    sql = """
    CREATE OR REPLACE FUNCTION match_documents(
        query_embedding vector(384),
        query_text text,
        match_count int DEFAULT 10
    )
    RETURNS TABLE (
        chunk_id uuid,
        doc_id uuid,
        source_file text,
        text_content text,
        metadata jsonb,
        rrf_score float
    )
    LANGUAGE sql
    AS $$
    WITH semantic_search AS (
        SELECT 
            chunk_id, doc_id, source_file, text_content, metadata, embedding,
            RANK() OVER (ORDER BY embedding <=> query_embedding) as semantic_rank
        FROM document_chunks
        ORDER BY embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    keyword_search AS (
        SELECT 
            chunk_id, doc_id, source_file, text_content, metadata, embedding,
            RANK() OVER (ORDER BY ts_rank_cd(tsvector, websearch_to_tsquery('english', query_text)) DESC) as keyword_rank
        FROM document_chunks
        WHERE tsvector @@ websearch_to_tsquery('english', query_text)
        ORDER BY ts_rank_cd(tsvector, websearch_to_tsquery('english', query_text)) DESC
        LIMIT match_count * 2
    )
    SELECT 
        COALESCE(s.chunk_id, k.chunk_id) as chunk_id,
        COALESCE(s.doc_id, k.doc_id) as doc_id,
        COALESCE(s.source_file, k.source_file) as source_file,
        COALESCE(s.text_content, k.text_content) as text_content,
        COALESCE(s.metadata, k.metadata) as metadata,
        (COALESCE(1.0 / (60 + s.semantic_rank), 0.0) + 
         COALESCE(1.0 / (60 + k.keyword_rank), 0.0))::float as rrf_score
    FROM semantic_search s
    FULL OUTER JOIN keyword_search k ON s.chunk_id = k.chunk_id
    ORDER BY rrf_score DESC
    LIMIT match_count;
    $$;
    """
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    print("Created match_documents RPC!")

if __name__ == "__main__":
    asyncio.run(create_rpc())
