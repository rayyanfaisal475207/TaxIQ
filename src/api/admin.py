# ============================================================
# Admin API — analytics dashboard + knowledge base management
#
# Protected by JWT-cookie auth + the require_admin dependency
# (the user's is_admin flag). Called only by the admin-frontend app.
#
# Every number served here is computed from real rows (pipeline_runs,
# pipeline_steps, documents, document_chunks, error_logs, ingestion_jobs).
# Nothing is stubbed. Where the instrumentation tables from migration 003
# do not exist yet, the gateways return empty datasets and /instrumentation
# reports which tables are missing, so the UI can say so plainly rather than
# rendering a confident chart of nothing.
# ============================================================

import os
import time
import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks

from src import config
from src.auth.jwt import require_admin
from src.database.models import User
from src.data_gateway import get_gateway
from src.observability import analytics, errors as error_capture

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Formats the ingestion loaders can actually read.
ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".xlsx", ".xls",
    ".html", ".htm", ".docx", ".png", ".jpg", ".jpeg", ".webp",
}


# ══════════════════════════════════════════════════════════════════════
# Overview
# ══════════════════════════════════════════════════════════════════════

@router.get("/metrics")
async def get_metrics(admin: User = Depends(require_admin)):
    """Aggregated system metrics for the dashboard's summary cards."""
    gateway = await get_gateway()
    return await gateway.get_system_metrics()


@router.get("/instrumentation")
async def get_instrumentation(admin: User = Depends(require_admin)):
    """
    Which observability tables exist. The dashboard uses this to show an
    honest "run migration 003" banner instead of empty charts that look
    like a healthy, silent system.
    """
    gateway = await get_gateway()

    # Probe for real existence. Asking get_errors() whether it worked is not
    # enough: it swallows a missing table and returns [], so "no rows" and "no
    # table" come back identical — and an un-run migration would look like a
    # healthy, silent system.
    status = {}
    for table in ("error_logs", "ingestion_jobs", "session_attachments", "knowledge_base_documents"):
        try:
            status[table] = await gateway.table_exists(table)
        except Exception:
            status[table] = False

    return {
        "tables": status,
        "ready": all(status.values()),
        "error_queue": error_capture.stats(),
    }


# ══════════════════════════════════════════════════════════════════════
# Usage, routing, latency
# ══════════════════════════════════════════════════════════════════════

@router.get("/usage")
async def get_usage(days: int = 30, granularity: str = "day", admin: User = Depends(require_admin)):
    """Requests over time + routing breakdown, from pipeline_runs."""
    if granularity not in ("day", "hour"):
        raise HTTPException(status_code=400, detail="granularity must be 'day' or 'hour'")

    gateway = await get_gateway()
    runs = await gateway.get_runs_since(analytics.since_iso(days))

    return {
        "days": days,
        "granularity": granularity,
        "total_requests": len(runs),
        "timeseries": analytics.usage_timeseries(runs, days, granularity),
        "routing": analytics.routing_breakdown(runs),
    }


@router.get("/latency")
async def get_latency(days: int = 30, granularity: str = "day", admin: User = Depends(require_admin)):
    """
    Latency: overall avg/p50/p95, the trend over time, per-route, and — the
    useful one — per pipeline step, so you can see *which* stage is slow.
    """
    if granularity not in ("day", "hour"):
        raise HTTPException(status_code=400, detail="granularity must be 'day' or 'hour'")

    gateway = await get_gateway()
    since = analytics.since_iso(days)
    runs = await gateway.get_runs_since(since)
    steps = await gateway.get_step_latencies_since(since)

    return {
        "days": days,
        "granularity": granularity,
        "summary": analytics.latency_summary(runs),
        "timeseries": analytics.latency_timeseries(runs, days, granularity),
        "by_route": analytics.routing_breakdown(runs),
        "by_step": analytics.latency_by_step(steps),
    }


# ══════════════════════════════════════════════════════════════════════
# Errors
# ══════════════════════════════════════════════════════════════════════

@router.get("/errors")
async def get_errors(
    limit: int = 100,
    offset: int = 0,
    days: int = 30,
    severity: str = None,
    module: str = None,
    error_type: str = None,
    admin: User = Depends(require_admin),
):
    """Filterable error history."""
    gateway = await get_gateway()
    return {
        "errors": await gateway.get_errors(
            limit=limit, offset=offset, severity=severity, module=module,
            error_type=error_type, since=analytics.since_iso(days),
        ),
        "facets": await gateway.get_error_facets(),
    }


@router.get("/errors/trend")
async def get_error_trend(days: int = 30, granularity: str = "day", admin: User = Depends(require_admin)):
    """Errors over time, split by severity."""
    gateway = await get_gateway()
    rows = await gateway.get_errors_since(analytics.since_iso(days))
    return {
        "days": days,
        "granularity": granularity,
        "total": len(rows),
        "timeseries": analytics.error_timeseries(rows, days, granularity),
    }


# ══════════════════════════════════════════════════════════════════════
# Knowledge base
# ══════════════════════════════════════════════════════════════════════

@router.get("/kb/stats")
async def get_kb_stats(admin: User = Depends(require_admin)):
    """Total chunks indexed + chunks per document."""
    gateway = await get_gateway()
    return await gateway.get_kb_stats()


@router.get("/kb/jobs")
async def get_kb_jobs(limit: int = 50, offset: int = 0, admin: User = Depends(require_admin)):
    """Ingestion status per uploaded document: processing / success / failed."""
    gateway = await get_gateway()
    return await gateway.get_ingestion_jobs(limit=limit, offset=offset)


@router.post("/kb/upload")
async def upload_kb_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
):
    """
    Upload a document into the SHARED knowledge base.

    This is the real thing the old user-facing "ingest" page pretended to be:
    the file is written to the documents directory, then chunked, embedded and
    inserted into the same `document_chunks` table the retriever reads — not a
    separate store. Chunking runs in the background, and its progress is
    tracked in `ingestion_jobs` so the UI can show processing/success/failed.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    contents = await file.read()
    max_bytes = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(contents) // 1024 // 1024}MB; the limit is {config.MAX_UPLOAD_SIZE_MB}MB.",
        )

    # Keep the original name (retrieval cites it), but never let it escape the
    # documents directory.
    safe_name = os.path.basename(file.filename or f"upload{ext}")
    dest = config.DOCUMENTS_DIR / safe_name
    config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    gateway = await get_gateway()
    job_id = await gateway.create_ingestion_job({
        "filename": safe_name,
        "file_type": ext.lstrip("."),
        "file_size_bytes": len(contents),
        "status": "processing",
        "uploaded_by": str(admin.id),
    })

    background_tasks.add_task(_ingest_uploaded_file, dest, job_id)

    return {
        "job_id": job_id,
        "filename": safe_name,
        "status": "processing",
        "file_size_bytes": len(contents),
    }


async def _ingest_uploaded_file(path: Path, job_id: str) -> None:
    """Chunk + embed an uploaded file, recording the outcome on its job row."""
    from src.ingestion.service import ingest_file

    gateway = await get_gateway()
    started = time.monotonic()
    try:
        stats = await ingest_file(path, is_global=True)
        duration_ms = int((time.monotonic() - started) * 1000)

        if stats.get("error"):
            await gateway.update_ingestion_job(job_id, {
                "status": "failed",
                "error_message": str(stats["error"])[:1000],
                "duration_ms": duration_ms,
            })
            return

        chunks = stats.get("chunks_added", 0)
        if chunks == 0:
            await gateway.update_ingestion_job(job_id, {
                "status": "failed",
                "error_message": "No text could be extracted from this file.",
                "duration_ms": duration_ms,
            })
            return

        await gateway.update_ingestion_job(job_id, {
            "status": "success",
            "chunks_added": chunks,
            "doc_id": stats.get("doc_id"),
            "duration_ms": duration_ms,
        })
    except Exception as exc:
        logger.error("KB ingestion failed for %s: %s", path.name, exc, exc_info=True)
        await gateway.update_ingestion_job(job_id, {
            "status": "failed",
            "error_message": f"{type(exc).__name__}: {exc}"[:1000],
            "duration_ms": int((time.monotonic() - started) * 1000),
        })


@router.delete("/kb/documents/{source_file}")
async def delete_kb_document(source_file: str, admin: User = Depends(require_admin)):
    """Remove a document and all of its chunks from the knowledge base."""
    gateway = await get_gateway()
    deleted = await gateway.delete_chunks_by_source(source_file)
    await gateway.delete_document_records(source_file)
    return {"deleted_chunks": deleted, "source_file": source_file}


# ══════════════════════════════════════════════════════════════════════
# Existing operational views
# ══════════════════════════════════════════════════════════════════════

@router.get("/runs")
async def get_runs(limit: int = 50, offset: int = 0, route_filter: str = None,
                   admin: User = Depends(require_admin)):
    gateway = await get_gateway()
    return await gateway.get_runs(limit=limit, offset=offset, route_filter=route_filter)


@router.get("/runs/{run_id}/steps")
async def get_run_steps(run_id: str, admin: User = Depends(require_admin)):
    gateway = await get_gateway()
    return await gateway.get_run_steps(run_id)


@router.get("/files")
async def get_files(limit: int = 50, offset: int = 0, admin: User = Depends(require_admin)):
    gateway = await get_gateway()
    return await gateway.get_generated_files(limit=limit, offset=offset)


@router.get("/mcp-calls")
async def get_mcp_calls(limit: int = 50, offset: int = 0, admin: User = Depends(require_admin)):
    gateway = await get_gateway()
    return await gateway.get_mcp_calls(limit=limit, offset=offset)


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, admin: User = Depends(require_admin)):
    """Delete a generated file record, and its bytes from disk."""
    gateway = await get_gateway()
    file_record = await gateway.delete_generated_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = file_record.get("storage_path")
    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
        except Exception as e:
            logger.error(f"Failed to delete physical file {storage_path}: {e}")

    return {"status": "success", "deleted": file_id}


@router.get("/users")
async def get_users(limit: int = 50, offset: int = 0, admin: User = Depends(require_admin)):
    gateway = await get_gateway()
    return await gateway.get_all_users(limit=limit, offset=offset)
