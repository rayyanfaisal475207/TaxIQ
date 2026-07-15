# ============================================================
# TaxIQ — FastAPI Application Entry Point
#
# This file wires together the entire system into a web API.
# At Milestone 1, this file proves the project structure is correct
# by starting without import errors.
#
# WHAT THIS FILE DOES:
# 1. Creates the FastAPI app instance
# 2. Validates configuration at startup
# 3. Ensures required directories exist
# 4. Registers API route handlers (stubs at Milestone 1, real at Milestone 7)
# 5. Provides a health check endpoint to verify the server is running
#
# TO RUN (from the rag_system/ directory):
#   uvicorn src.main:app --reload
# OR (using this file directly):
#   python src/main.py
# ============================================================

import logging
import sys
from contextlib import asynccontextmanager
import json
import uvicorn
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src import config
from src.config import ensure_directories, validate_config
from src.database import pipeline_logger
from src.pipeline.orchestrator import process_query
from src.data_gateway import get_gateway

from src.auth.routes import router as auth_router, limiter, get_current_user
from src.auth.jwt import require_admin
from src.database.models import User
from fastapi import Depends
from src.api.sessions import router as sessions_router
from src.api.profile import router as profile_router
from src.api.admin import router as admin_router
from src.api.projects import router as projects_router
from src.api.attachments import router as attachments_router
from src.observability import errors as error_capture

from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler


# ── Logging Setup ──────────────────────────────────────────────────────────────
# Configure logging before anything else so all startup messages are captured.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Lifespan Handler (startup + shutdown) ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager: runs startup code before the server
    accepts requests, and shutdown code when the server is stopping.

    asynccontextmanager: the `yield` separates startup (before) from
    shutdown (after). Think of it as: everything before yield = __init__,
    everything after yield = __del__.
    """
    # ── STARTUP ──────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TaxIQ starting up...")
    logger.info("=" * 60)

    # Ensure all required directories exist before any component tries to use them
    ensure_directories()
    logger.info("Data directories ensured.")

    # Mirror every ERROR/CRITICAL into the error_logs table so the admin
    # dashboard has an error history instead of a terminal that scrolled away.
    error_capture.install()
    logger.info("Error capture installed.")

    # ── Database Initialization ──────────────────────────────────────────
    from src.database.postgres import is_postgres_configured, init_postgres

    if is_postgres_configured():
        # PostgreSQL is the primary database (Phase 1+).
        # A failed init must NOT kill the server: on IPv4-only networks the
        # direct DB is unreachable and the data gateway falls back to the
        # Supabase REST API — the app is fully functional without this step.
        try:
            await init_postgres()
            logger.info("[OK] PostgreSQL initialized (primary database)")
        except Exception as exc:
            logger.warning(
                "[WARN] Direct PostgreSQL unreachable at startup (%s). "
                "Continuing — the data gateway will use the Supabase REST fallback.",
                exc.__class__.__name__,
            )

        # Archive the old SQLite file if it still exists (one-time migration)
        sqlite_path = config.DB_PATH
        if sqlite_path.exists():
            archived = sqlite_path.with_suffix(".db.archived")
            if not archived.exists():
                sqlite_path.rename(archived)
                logger.info(
                    "Archived legacy SQLite: %s → %s",
                    sqlite_path.name, archived.name
                )
            else:
                logger.info("Legacy SQLite already archived: %s", archived.name)
    else:
        # Fallback: keep using SQLite (no DATABASE_URL configured)
        from src.database.db import init_db
        init_db()
        logger.info("[OK] SQLite initialized (legacy mode — set DATABASE_URL to use PostgreSQL)")

    # Validate configuration — warn about missing API keys but don't crash
    config_errors = validate_config()
    if config_errors:
        for error in config_errors:
            logger.warning("[WARN] Config warning: %s", error)
        logger.warning(
            "Server starting with configuration warnings. "
            "API calls will fail until API keys are set in .env"
        )
    else:
        logger.info("[OK] Configuration valid. LLM provider: %s", config.LLM_PROVIDER)

    logger.info("ChromaDB persist dir: %s", config.CHROMA_PERSIST_DIR)
    logger.info("Memory backend: %s", config.MEMORY_BACKEND)
    logger.info("Server ready at http://%s:%d", config.HOST, config.PORT)

    yield  # Server is running — handle requests

    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    logger.info("TaxIQ shutting down. Goodbye!")


# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TaxIQ API",
    description=(
        "TaxIQ — Pakistan's Tax Code, Answered Instantly. "
        "Self-correcting AI assistant with hybrid retrieval (semantic + BM25), "
        "RRF re-ranking, relevance evaluation, and automatic retry loop."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: allow the React frontend (running on a different port) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limiting (slowapi) ───────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(profile_router, prefix="/api/profile", tags=["profile"])
app.include_router(admin_router)   # prefix already set inside admin.py
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(attachments_router, prefix="/api/attachments", tags=["attachments"])

# ── Models ─────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str
    message: str
    project_id: Optional[str] = None


# ── Health Check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    """
    Simple health check endpoint.
    Returns a 200 OK with system status.
    Used by deployment platforms and monitoring to verify the service is alive.
    """
    doc_count = 0
    store_status = "ok"
    try:
        gateway = await get_gateway()
        doc_count = await gateway.get_collection_count()
    except Exception as exc:
        store_status = f"error: {exc}"

    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_provider": config.LLM_PROVIDER,
        "vector_store_status": store_status,
        "documents_in_store": doc_count,
    }


# ── API Routes ────────────────────────────────────────────────────────────────
@app.post("/api/chat", tags=["Chat"])
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):

    """
    Main chat endpoint — accepts a user message and streams pipeline trace events
    + the final response as Server-Sent Events.
    """
    async def event_generator():
        try:
            import asyncio
            gateway = await get_gateway()
            user_id = str(current_user.id)

            # Fetch profile and session concurrently — both are independent reads.
            user_profile, session = await asyncio.gather(
                gateway.get_user_context_profile(user_id),
                gateway.get_session(request.session_id),
            )
            project_id = request.project_id or (session.get("project_id") if session else None)

            # Create the session up-front WITH its owner. Sessions must never be
            # created as a side effect of logging (that produced ownerless rows
            # that the sidebar can't list and /api/sessions/{id} 403s on).
            if session is None:
                provisional_title = " ".join(request.message.split()[:6])[:80] or "New Conversation"
                await gateway.create_session(request.session_id, user_id, provisional_title, project_id)

            async for event in process_query(
                request.session_id, request.message,
                project_id=project_id, user_profile=user_profile, user_id=user_id,
            ):
                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error("Chat pipeline error: %s", e, exc_info=True)
            error_event = {"step": "system", "status": "error", "detail": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# NOTE: Document ingestion and knowledge-base management moved to the ADMIN API
# (/api/admin/kb/*). Normal users no longer ingest into the shared knowledge base:
# they attach files to a single conversation via /api/attachments, which never
# touches the vector store. See docs/INGESTION.md.


@app.get("/api/files/{file_id}/download", tags=["Files"])
async def download_file(file_id: str, current_user: User = Depends(get_current_user)):
    """
    Download a generated file. Ensures the user owns the file.
    """
    from src.data_gateway import get_gateway
    import os

    try:
        import uuid
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    gateway = await get_gateway()
    file_record = await gateway.get_generated_file(file_id)

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    # Admins may download any user's file (the admin panel lists all files)
    if str(file_record["user_id"]) != str(current_user.id) and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Unauthorized to access this file")

    if not os.path.exists(file_record["storage_path"]):
        raise HTTPException(status_code=404, detail="File content no longer exists on server")

    MIME_TYPES = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    file_type = (file_record.get("file_type") or "").lower()
    file_name = file_record["file_name"]
    # Legacy rows stored the bare title without an extension — repair on the way out.
    if file_type and not file_name.lower().endswith(f".{file_type}"):
        file_name = f"{file_name}.{file_type}"

    return FileResponse(
        path=file_record["storage_path"],
        filename=file_name,
        media_type=MIME_TYPES.get(file_type, "application/octet-stream")
    )



# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.RELOAD,
    )
