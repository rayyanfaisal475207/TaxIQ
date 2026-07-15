"""
Migration script to port data from local ChromaDB to PostgreSQL.
Extracts chunks, embeddings, and metadata, inserts into pgvector,
and generates tsvector indices for hybrid search.
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Ensure the root of the project is in PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env first
load_dotenv()

# We use sync SQLAlchemy for the migration script for simplicity
from sqlalchemy import create_engine, text
from src.database.models import Base
from src.config import DATABASE_URL

CHROMA_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")).resolve()
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "rag_documents")

def main():
    print("=== ChromaDB to PostgreSQL Migration ===")
    
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set in .env")
        sys.exit(1)
        
    print(f"PostgreSQL target: {DATABASE_URL.split('@')[-1]}")
    
    try:
        import chromadb
    except ImportError:
        print("ERROR: chromadb is not installed. Cannot read local ChromaDB.")
        sys.exit(1)
        
    if not CHROMA_PERSIST_DIR.exists():
        print(f"ERROR: Chroma persist dir {CHROMA_PERSIST_DIR} does not exist.")
        sys.exit(1)

    print("Connecting to ChromaDB...")
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
    
    try:
        collection = chroma_client.get_collection(name=CHROMA_COLLECTION_NAME)
    except Exception as e:
        print(f"ERROR: Could not get collection '{CHROMA_COLLECTION_NAME}': {e}")
        sys.exit(1)
        
    # Get count
    chroma_count = collection.count()
    print(f"Found {chroma_count} total chunks in ChromaDB.")
    
    if chroma_count == 0:
        print("Nothing to migrate.")
        sys.exit(0)

    # Convert async pg URL to sync URL for sqlalchemy create_engine
    # postgresql+asyncpg:// -> postgresql://
    sync_db_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    
    engine = create_engine(sync_db_url)
    
    print("Ensuring pgvector extension and creating tables...")
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    
    # Create all tables (including DocumentChunk)
    Base.metadata.create_all(engine)
    
    print("Fetching all IDs from ChromaDB to avoid offset pagination bugs...")
    all_data = collection.get()
    all_ids = all_data["ids"]
    
    print("Extracting data from ChromaDB and inserting to Postgres...")
    batch_size = 1000
    migrated_count = 0
    
    # We do not use a single transaction for everything, to avoid hanging and locking.
    # Instead, we will commit per batch!
    for i in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[i:i + batch_size]
        print(f"  Fetching batch {i} to {i + len(batch_ids)}...")
        
        try:
            batch = collection.get(
                ids=batch_ids,
                include=["embeddings", "documents", "metadatas"]
            )
        except Exception as e:
            print(f"  WARNING: Batch failed ({e}). Falling back to 1-by-1 for this batch...")
            ids = []
            embeddings = []
            documents = []
            metadatas = []
            for single_id in batch_ids:
                try:
                    single_batch = collection.get(ids=[single_id], include=["embeddings", "documents", "metadatas"])
                    if single_batch and single_batch["ids"]:
                        ids.extend(single_batch["ids"])
                        embeddings.extend(single_batch["embeddings"])
                        documents.extend(single_batch["documents"])
                        metadatas.extend(single_batch["metadatas"])
                except Exception as single_e:
                    print(f"  ERROR: Could not fetch chunk {single_id}: {single_e}. Skipping.")
            
            # Reconstruct batch
            batch = {
                "ids": ids,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas
            }
        
        ids = batch["ids"]
        embeddings = batch["embeddings"]
        documents = batch["documents"]
        metadatas = batch["metadatas"]
        
        # Use chunks so we don't hold the lock too long
        with engine.begin() as conn:
            for j in range(len(ids)):
                chunk_id = ids[j]
                meta = metadatas[j] or {}
                # Handle possible missing metadata
                doc_id = meta.get("doc_id", "unknown_" + chunk_id)
                chunk_index = meta.get("chunk_index", 0)
                source_file = meta.get("source", "unknown")
                
                # Check for effective_from and effective_to 
                eff_from = meta.get("effective_from")
                eff_to = meta.get("effective_to")
                
                # First ensure the document exists
                doc_sql = """
                INSERT INTO documents (doc_id, filename, doc_type, is_global)
                VALUES (:doc_id, :filename, 'migrated', false)
                ON CONFLICT (doc_id) DO NOTHING
                """
                conn.execute(text(doc_sql), {
                    "doc_id": str(doc_id),
                    "filename": str(source_file)
                })
                
                # We do an INSERT ... ON CONFLICT DO NOTHING to make script restartable
                sql = """
                INSERT INTO document_chunks 
                    (chunk_id, doc_id, chunk_index, chunk_text, embedding, fts_vector, source_file, effective_from, effective_to)
                VALUES 
                    (:chunk_id, :doc_id, :chunk_index, :chunk_text, :embedding, to_tsvector('english', :chunk_text), :source_file, NULL, NULL)
                ON CONFLICT (chunk_id) DO NOTHING
                """
                
                import json
                # Ensure embedding is a standard list, then convert to JSON string format which pgvector accepts as '[1.0, 2.0]'
                emb_list = embeddings[j].tolist() if hasattr(embeddings[j], "tolist") else embeddings[j]
                
                conn.execute(text(sql), {
                    "chunk_id": chunk_id,
                    "doc_id": str(doc_id),
                    "chunk_index": int(chunk_index),
                    "chunk_text": documents[j],
                    "embedding": json.dumps(emb_list),
                    "source_file": str(source_file)
                })
                
                migrated_count += 1
        print(f"  Committed batch. Total migrated so far: {migrated_count}")
                
    print(f"\nMigration complete. Inserted {migrated_count} chunks.")
    
    print("\n=== Verification ===")
    with engine.connect() as conn:
        pg_count = conn.execute(text("SELECT count(*) FROM document_chunks;")).scalar()
        
    print(f"ChromaDB Chunk Count: {chroma_count}")
    print(f"PostgreSQL Chunk Count: {pg_count}")
    
    if chroma_count == pg_count:
        print("SUCCESS: Row counts match perfectly.")
        print("\nYou can now safely delete ChromaDB. Recommendation:")
        print("  rm -r ./data/chroma_db")
    else:
        print("WARNING: Row counts do not match. Review the database.")

if __name__ == "__main__":
    main()
