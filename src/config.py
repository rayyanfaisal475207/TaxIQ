# ============================================================
# RAG System — All Settings, Loaded from .env
#
# Provider strategy used in this project:
#   LLM_PROVIDER   = groq    → fast LLM calls (rewriter, router, evaluator, response)
#   EMBEDDING_PROVIDER = gemini → Gemini text-embedding-004 (Groq has no embedding API)
#   Vision          = gemini → Gemini 2.0 Flash for image/scanned-PDF understanding
#
# Why this split?
#   Groq runs open-source models (LLaMA 3.3) on custom inference chips at
#   extremely low latency (~200ms per call). Perfect for 3–4 calls per query.
#   Gemini provides best-in-class embeddings and multimodal vision — both
#   features Groq doesn't currently offer.
# ============================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env file ────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ── LLM Provider (text generation) ───────────────────────────────────────────
# Controls which backend handles: query rewriter, router, evaluator, response
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")  # "groq" | "gemini" | "openai" | "anthropic"

# Groq — fast open-source inference
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Google Gemini — also used as LLM fallback and for vision
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# OpenAI (optional, kept for compatibility)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

# Anthropic (optional, kept for compatibility)
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

# Optional local OpenAI-compatible endpoint (LM Studio / vLLM / ngrok tunnel).
# Leave LOCAL_LLM_URL empty to disable. Only used when a user's llm_mode
# setting is "local".
LOCAL_LLM_URL: str = os.getenv("LOCAL_LLM_URL", "")
LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "qwen3.5-2b")
LOCAL_LLM_API_KEY: str = os.getenv("LOCAL_LLM_API_KEY", "")
LOCAL_LLM_TIMEOUT: float = float(os.getenv("LOCAL_LLM_TIMEOUT", "20"))


# ── Embedding Provider ────────────────────────────────────────────────────────
# Controls which backend converts text → vectors for ChromaDB storage/search.
# Groq has no embedding API → we always use Gemini (or OpenAI as fallback).
EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "gemini")  # "gemini" | "openai"
GEMINI_EMBEDDING_MODEL: str = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


# ── ChromaDB ─────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR: Path = Path(
    os.getenv("CHROMA_PERSIST_DIR", str(_PROJECT_ROOT / "data" / "chroma_db"))
)
CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "rag_documents")


# ── Pipeline Settings ─────────────────────────────────────────────────────────
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "1"))
TOP_K_RETRIEVAL: int = int(os.getenv("TOP_K_RETRIEVAL", "10"))
TOP_K_RERANK: int = int(os.getenv("TOP_K_RERANK", "5"))


# ── Text Chunking ─────────────────────────────────────────────────────────────
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "64"))


# ── Conversation Memory ────────────────────────────────────────────────────────
MEMORY_BACKEND: str = os.getenv("MEMORY_BACKEND", "json")
MEMORY_DIR: Path = Path(
    os.getenv("MEMORY_DIR", str(_PROJECT_ROOT / "data" / "memory"))
)
MAX_HISTORY_TOKENS: int = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))


# ── FastAPI Server ────────────────────────────────────────────────────────────
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
RELOAD: bool = os.getenv("RELOAD", "true").lower() == "true"
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")


# ── Document Ingestion ────────────────────────────────────────────────────────
DOCUMENTS_DIR: Path = Path(
    os.getenv("DOCUMENTS_DIR", str(_PROJECT_ROOT / "data" / "documents"))
)
MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))


# ── Relational Database (Pipeline Logging) ────────────────────────────────────
# SQLite file that stores all pipeline events in a normalized schema.
# Lives in data/ alongside ChromaDB so everything is in one place.
DB_PATH: Path = Path(
    os.getenv("DB_PATH", str(_PROJECT_ROOT / "data" / "pipeline_logs.db"))
)


# ── Directory Setup ───────────────────────────────────────────────────────────
def ensure_directories() -> None:
    """Create all required data directories if they don't already exist."""
    for d in [CHROMA_PERSIST_DIR, MEMORY_DIR, DOCUMENTS_DIR, DB_PATH.parent]:
        d.mkdir(parents=True, exist_ok=True)



# ── Validation ────────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    """
    Check that required API keys and settings are present.
    Returns a list of warning strings. Empty list = fully configured.
    """
    errors: list[str] = []

    # LLM provider key check
    if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
        errors.append("GROQ_API_KEY is not set. LLM calls will fail.")
    elif LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY is not set. LLM calls will fail.")
    elif LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set. LLM calls will fail.")
    elif LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set. LLM calls will fail.")
    elif LLM_PROVIDER not in ("groq", "gemini", "openai", "anthropic"):
        errors.append(
            f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. "
            "Valid values: 'groq', 'gemini', 'openai', 'anthropic'."
        )

    # Embedding provider key check
    if EMBEDDING_PROVIDER == "gemini" and not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY is not set. Embedding calls will fail.")
    elif EMBEDDING_PROVIDER == "openai" and not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set. Embedding calls will fail.")

    # Chunking sanity check
    if CHUNK_OVERLAP >= CHUNK_SIZE:
        errors.append(
            f"CHUNK_OVERLAP ({CHUNK_OVERLAP}) must be less than CHUNK_SIZE ({CHUNK_SIZE})."
        )

    return errors


import os
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-for-dev")
JWT_ALGORITHM = "HS256"
CORS_ORIGINS = ["http://localhost:5173", "http://localhost:3000", "http://localhost:5174"]

# Added for MCP
DATABASE_URL = os.getenv("DATABASE_URL", "")
